"""Hivegrid remote: control the agents of a running Hivegrid from a phone, over Tailscale.

A small HTTP server (standard library only) bound to loopback or a tailnet address. It lists the agents
of the WezTerm mux, shows the tail of their screens and a chat built from their omp session files (long
polls keep both live), sends them prompts, keys, plan-review answers and acknowledgements, switches their
model and thinking level, starts a new omp session in a pane and reports accounts, quota and machine
metrics. It never closes or focuses panes; it types only into agents, except that an account switch types
one `cd … && omp …` line into the plain shell left after omp quit (appended to any text already typed at
that prompt). The data behind the richer endpoints (session files, chat parser, accounts, metrics) is in
hivegrid_remote_data.py.
Only when the user switched it on (settings.json `remote.terminal` or `--terminal`, never with
`--read-only`) it also gives the phone a terminal of its own: a login shell on a pty, which is not one of
the window's panes (hivegrid_remote_term.py runs the shells, the page's emulator is hivegrid_remote_termjs.py).
Every request to /api needs `Authorization: Bearer <token>` (the token file next to settings.json); the
page itself (static, no data), the logos and the terminal's font are served without it. The UI lives in
hivegrid_remote_ui.py as string constants.

Everything that touches the world is injectable: the mux (`Mux`, or any object with the same methods),
the token store, the clocks and the log, so the tests run without a window, a network or a real home.
Docs: docs/remote.md.
"""
import argparse
import base64
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import secrets
import select
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import hivegrid_agents as A  # noqa: E402
import hivegrid_hive as HV  # noqa: E402
import hivegrid_outbox as O  # noqa: E402
import hivegrid_remote_data as D  # noqa: E402
import hivegrid_remote_history as HI  # noqa: E402
import hivegrid_remote_pending as PQ  # noqa: E402
import hivegrid_remote_term as T  # noqa: E402
import hivegrid_remote_termjs as TJ  # noqa: E402
import hivegrid_remote_ui as UI  # noqa: E402
import hivegrid_ui as U  # noqa: E402
from hivegrid_settings_core import DEFAULT_PRESET, HEX_RE, PRESETS, Settings, ThemeWatch, settings_path  # noqa: E402

API_VERSION = 1
DEFAULT_PORT = 8787
DEFAULT_BIND = "127.0.0.1"

# ---------------------------------------------------------------- limits and tables

MAX_BODY = 16384
MAX_PROMPT = 8000
MAX_KEYS = 8
MAX_KEY_TEXT = 256
MAX_FEEDBACK = 4000
TAIL_DEFAULT, TAIL_MIN, TAIL_MAX = 60, 5, 400
TAIL_LINE = 500
TAIL_BYTES = 65536
MAX_TOKEN_HEADER = 256
CONN_CAP = 32
SOCKET_TIMEOUT = 10
CLI_TIMEOUT = 4
SUBPROCESS_SLOTS = 4
LIST_TTL = 1.5
HEADER_DEADLINE = 15.0     # seconds a client has for the request line and headers (absolute)
BODY_DEADLINE = 15.0       # and for the body, counted from the headers
DRAIN_DEADLINE = 5.0       # and what a refused request's leftover body may take
PEER_CAP = 8               # concurrent connections per client key (peer address plus forwarded identity)
WAIT_MAX = 20              # longest `wait=` a long poll may ask for, seconds
CHAT_DEFAULT, CHAT_MAX = 60, 200
LIST_POLL = 1.0            # how often a held list/tail request looks again
CHAT_POLL = 0.5            # and a held chat request (a file stat)
SWITCH_WAIT = 3.0          # how long a model/thinking change waits for the state file to show it
MAX_FONT = 16 * 1024 * 1024
TAIL_TTL = 1.0             # a pane's screen text is reused this long by the held tail requests that watch it
SESSION_BURST, SESSION_EVERY = 3, 5.0     # starting a session: 3 at once, then one per 5 seconds
SESSION_REPEAT = 10.0      # the same directory and account is not started twice within this many seconds
ACCOUNT_BURST, ACCOUNT_EVERY = 2, 10.0   # moving a chat to another account: 2 at once, then one per 10 seconds
QUIT_BURST, QUIT_EVERY = HI.QUIT_BURST, HI.QUIT_EVERY   # quitting sessions from the list: 4 at once, then one per 2 seconds
TERM_IO_RATE, TERM_IO_BURST = 40.0, 80    # terminal requests per device: 40 a second, bursts of 80
TERM_OPEN_EVERY, TERM_OPEN_BURST = 5.0, 3 # opening a terminal: 3 at once, then one every 5 seconds, per device
TERM_KEYS = 64                            # devices whose terminal allowance is remembered

# Quick keys: name -> bytes. Each is sent in its own `send-text --no-paste` call, never concatenated
# (an ESC followed by a byte in one write reads as Alt+key).
KEYS = {
    "enter": "\r", "esc": "\x1b", "tab": "\t", "shift-tab": "\x1b[Z", "backspace": "\x7f", "space": " ",
    "up": "\x1b[A", "down": "\x1b[B", "right": "\x1b[C", "left": "\x1b[D", "ctrl-c": "\x03", "ctrl-d": "\x04",
}
DANGEROUS_KEYS = ("ctrl-c", "ctrl-d")
SINGLE_KEY = re.compile(r"[A-Za-z0-9]")
AGENT_ID = re.compile(r"(0|[1-9][0-9]{0,8})")
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{32,256}")

VOCAB = ("attention", "limit", "working", "busy", "done", "idle", "exited", "shell", "web")

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                                "img-src 'self' data: blob:; font-src 'self'; manifest-src 'self'; base-uri 'none'; "
                                "form-action 'none'; frame-ancestors 'none'"),
    "X-Frame-Options": "DENY",
    "Cross-Origin-Resource-Policy": "same-origin",
}

STATUS_TEXT = {
    "bad_request": 400, "unauthorized": 401, "forbidden_host": 403, "forbidden_origin": 403, "readonly": 403,
    "terminal_disabled": 403, "not_found": 404, "method_not_allowed": 405, "confirm_required": 409,
    "agent_not_ready": 409, "conflict": 409, "length_required": 411, "too_large": 413, "unsupported_media_type": 415,
    "rate_limited": 429, "internal": 500, "mux_failed": 502, "no_mux": 503, "terminal_busy": 503,
}

THEME_VARS = ("deep", "base", "surface", "pill", "raised", "track", "border", "edge_hi", "text", "bright", "muted",
              "dim", "accent", "cyan", "green", "amber", "red", "violet", "grey")


# ---------------------------------------------------------------- sanitizers

_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\|$)")
_STRINGS = re.compile(r"\x1b[PX^_][^\x1b]*(?:\x1b\\)?")
_ESC2 = re.compile(r"\x1b[ -/]*[0-~]?")
_BAD = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200b-\u200f\u2028\u2029\u202a-\u202e\u2066-\u2069\ufeff"
                  "\U000e0000-\U000e007f\ud800-\udfff]")
_BAD_PROMPT = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029]")


def strip_escapes(text):
    """`text` without ANSI/OSC/DCS sequences and bare ESCs."""
    text = _OSC.sub("", text)
    text = _STRINGS.sub("", text)
    text = _CSI.sub("", text)
    return _ESC2.sub("", text)


def _cut(text, limit):
    return text if len(text) <= limit else text[: max(limit - 1, 0)] + "…"


def sanitize_text(text, limit):
    """Display text: no escape sequences, no control characters (newline and tab stay), no bidi
    controls, at most `limit` characters. Anything that is not a string reads as ''."""
    if not isinstance(text, str):
        return ""
    text = strip_escapes(text.replace("\r\n", "\n"))
    return _cut(_BAD.sub("", text), limit)


def sanitize_line(text, limit, collapse=True):
    """One display line. `collapse` squeezes whitespace (titles, paths); without it tabs become four
    spaces and the indentation stays (screen rows)."""
    text = sanitize_text(text, 10 * limit if limit else 0)
    text = " ".join(text.split()) if collapse else text.replace("\n", " ").replace("\t", "    ").rstrip()
    return _cut(text, limit)


def validate_text(value, limit, what="text", allow_empty=False):
    """None when `value` may be pasted into a pane, else the reason. Control characters other than
    newline and tab are refused (an ESC could end a bracketed paste early and run the rest)."""
    if not isinstance(value, str):
        return f"{what} must be a string"
    if not allow_empty and not value.strip():
        return f"{what} must not be empty"
    if len(value) > limit:
        return f"{what} is longer than {limit} characters"
    if _BAD_PROMPT.search(value):
        return f"{what} contains control characters"
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return f"{what} is not valid text"
    return None


def parse_bearer(header):
    """The token of an `Authorization: Bearer <token>` header, None for anything else."""
    if not isinstance(header, str):
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token if token and len(token) <= MAX_TOKEN_HEADER else None


# ---------------------------------------------------------------- token

class TokenError(Exception):
    pass


def default_token_path(environ=None):
    environ = os.environ if environ is None else environ
    override = environ.get("HIVEGRID_REMOTE_TOKEN_FILE")
    if override:
        return override
    return str(settings_path().parent / "remote-token")


def generate_token():
    return secrets.token_urlsafe(32)


def write_token(path, token):
    """Write the token file atomically with mode 0600; the directory is created with 0700 when missing."""
    path = os.fspath(path)
    parent = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(parent):
        os.makedirs(parent, mode=0o700, exist_ok=True)
        os.chmod(parent, 0o700)
    tmp = os.path.join(parent, f".{os.path.basename(path)}.{os.getpid()}.{time.time_ns()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(token + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_token(path):
    """The token in `path`; TokenError when the file is missing, not a private regular file of ours,
    or holds something that is not a token."""
    path = os.fspath(path)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise TokenError(f"no token file at {path}; run: hivegrid-remote token") from None
    except OSError as exc:
        raise TokenError(f"cannot read {path}: {exc.strerror}") from None
    if stat.S_ISLNK(st.st_mode):
        raise TokenError(f"{path} is a symlink; refusing it")
    if not stat.S_ISREG(st.st_mode):
        raise TokenError(f"{path} is not a regular file")
    if st.st_uid != os.geteuid():
        raise TokenError(f"{path} is not owned by you")
    if st.st_mode & 0o077:
        raise TokenError(f"{path} is readable by others; run: chmod 600 {path}")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise TokenError(f"cannot read {path}: {exc.strerror}") from None
    try:
        fst = os.fstat(fd)
        if (fst.st_dev, fst.st_ino) != (st.st_dev, st.st_ino) or not stat.S_ISREG(fst.st_mode):
            raise TokenError(f"{path} changed while it was read")
        if fst.st_uid != os.geteuid() or fst.st_mode & 0o077:
            raise TokenError(f"{path} is not private to you; run: chmod 600 {path}")
        with os.fdopen(fd, "rb", closefd=False) as f:
            raw = f.read(1024)
    except OSError as exc:
        raise TokenError(f"cannot read {path}: {exc.strerror}") from None
    finally:
        os.close(fd)
    token = raw.decode("ascii", "replace").strip()
    if not TOKEN_RE.fullmatch(token):
        raise TokenError(f"{path} does not hold a token (32+ characters of A-Z a-z 0-9 _ -); "
                         "run: hivegrid-remote token --rotate")
    return token


def _token_digest(token):
    return hashlib.sha256(token.encode("utf-8", "replace")).digest()


def _fingerprint(digest):
    """The short stable name of a token (a digest prefix, never the token) that ties a terminal to it."""
    return digest.hex()[:16]


class TokenStore:
    """The token file, re-read whenever it changes (so `token --rotate` takes effect without a
    restart and an invalid file locks everybody out). Only a SHA-256 digest is kept in memory."""

    def __init__(self, path):
        self.path = os.fspath(path)
        self._lock = threading.Lock()
        self._stamp = False
        self._digest = None
        self.error = None

    def _stat_stamp(self):
        try:
            st = os.lstat(self.path)
        except OSError:
            return None
        return (st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_mode, st.st_uid)

    def _refresh(self):
        stamp = self._stat_stamp()
        if stamp == self._stamp:
            return
        self._stamp = stamp
        try:
            self._digest = _token_digest(read_token(self.path))
            self.error = None
        except TokenError as exc:
            self._digest = None
            self.error = str(exc)

    def load(self):
        """Read now; raises TokenError when the file is unusable (the server's start-up check)."""
        with self._lock:
            self._stamp = False
            self._refresh()
            if self._digest is None:
                raise TokenError(self.error)

    def check(self, presented):
        if not isinstance(presented, str) or not presented or len(presented) > MAX_TOKEN_HEADER:
            return False
        with self._lock:
            self._refresh()
            digest = self._digest
        if digest is None:
            return False
        return hmac.compare_digest(_token_digest(presented), digest)

    def fingerprint(self):
        """A short stable name of the valid token (a digest prefix, never the token), None when no token
        file is valid. Terminals are tied to the token that opened them: a rotation ends them."""
        with self._lock:
            self._refresh()
            return _fingerprint(self._digest) if self._digest is not None else None


# ---------------------------------------------------------------- bind, host, origin

TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")


def is_loopback(addr):
    """True for 127.0.0.0/8, ::1 and their IPv4-mapped forms; False for anything else or non-IPs."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    ip = getattr(ip, "ipv4_mapped", None) or ip
    return ip.is_loopback


def bound_host(addr):
    """The Host-header name of a bound IP address (`[fd7a::1]` for IPv6), None for non-IPs."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return None
    return f"[{ip}]" if ip.version == 6 else str(ip)


def check_bind(addr, allow_any=False):
    """Raises ValueError unless `addr` is loopback or a Tailscale address (or `allow_any`)."""
    if allow_any:
        return
    if addr == "localhost":
        return
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        raise ValueError(f"{addr!r} is not an IP address; use 127.0.0.1 or the machine's Tailscale address "
                         "(--allow-any-bind overrides)") from None
    if ip.is_loopback or ip in TAILNET_V4 or ip in TAILNET_V6:
        return
    raise ValueError(f"refusing to listen on {addr}: only loopback and Tailscale addresses are allowed "
                     "(--allow-any-bind overrides)")


def split_host(header):
    """(hostname lower-cased, port or None) of a Host header; None when it is malformed."""
    if not isinstance(header, str) or not header or len(header) > 255 or re.search(r"[\s\x00-\x1f\x7f]", header):
        return None
    if header.startswith("["):
        end = header.find("]")
        if end < 0:
            return None
        host, rest = header[: end + 1], header[end + 1:]
        if rest and not (rest.startswith(":") and rest[1:].isdigit()):
            return None
        return host.lower(), (rest[1:] or None)
    if header.count(":") > 1:
        return None
    host, _, port = header.partition(":")
    if not host or (port and not port.isdigit()):
        return None
    return host.lower(), (port or None)


def host_allowed(header, extra=()):
    """DNS-rebinding defence: only loopback, tailnet names and configured names may be asked for."""
    parts = split_host(header)
    if parts is None:
        return False
    host = parts[0]
    return host in ("127.0.0.1", "localhost", "[::1]") or host.endswith(".ts.net") or host in extra


def origin_ok(origin, host, sec_fetch_site=None):
    """Cross-site check of a POST: an Origin must name this very Host; without one, a browser's
    Sec-Fetch-Site must say same-origin (or none). No header at all is a non-browser client."""
    if origin is not None:
        if not isinstance(host, str) or origin.lower() == "null":
            return False
        netloc = urllib.parse.urlsplit(origin).netloc
        return bool(netloc) and netloc.lower() == host.lower()
    if sec_fetch_site is not None:
        return sec_fetch_site.lower() in ("same-origin", "none")
    return True


# ---------------------------------------------------------------- rate limits

class AuthLimiter:
    """Failed-auth lockout per client key (5 failures in `window` seconds lock the key for `base`
    seconds, doubling on each repeat up to `cap`; a success clears it) plus a global backstop."""

    def __init__(self, clock=time.monotonic, window=60.0, max_fail=5, base=60.0, cap=900.0, global_max=50):
        self.clock, self.window, self.max_fail, self.base, self.cap, self.global_max = clock, window, max_fail, base, cap, global_max
        self.keys = {}      # key -> {"fails": [t], "until": t, "level": n}
        self.glob = []
        self.lock = threading.Lock()

    def blocked(self, key):
        """Seconds to wait (> 0) when `key` is locked or the backstop is hit, else 0. Only requests
        with an invalid token are held back by it (the caller checks the token first)."""
        with self.lock:
            now = self.clock()
            self.glob = [t for t in self.glob if now - t < self.window]
            wait = 0.0
            if len(self.glob) >= self.global_max:
                wait = self.glob[0] + self.window - now
            rec = self.keys.get(key)
            if rec and rec["until"] > now:
                wait = max(wait, rec["until"] - now)
            return wait

    def fail(self, key):
        """Counts a failure; returns the lock length in seconds when this one locked the key, else 0."""
        with self.lock:
            now = self.clock()
            self.glob.append(now)
            if len(self.keys) > 1024:
                self.keys = {k: r for k, r in self.keys.items()
                             if r["until"] > now or any(now - t < self.window for t in r["fails"])}
            rec = self.keys.setdefault(key, {"fails": [], "until": 0.0, "level": 0})
            rec["fails"] = [t for t in rec["fails"] if now - t < self.window] + [now]
            if len(rec["fails"]) >= self.max_fail:
                length = min(self.base * 2 ** rec["level"], self.cap)
                rec["until"] = now + length
                rec["level"] += 1
                rec["fails"] = []
                return length
            return 0

    def ok(self, key):
        """A valid token: forget the key's failures, but an active lock runs out on its own, so a
        legitimate client that shares the key cannot lift it for whoever is guessing."""
        with self.lock:
            rec = self.keys.get(key)
            if rec is None:
                return
            if rec["until"] <= self.clock():
                del self.keys[key]
            else:
                rec["fails"] = []


class Bucket:
    """Token bucket: `burst` tokens, refilled at `rate` per second."""

    def __init__(self, rate, burst, clock=time.monotonic):
        self.rate, self.burst, self.clock = rate, burst, clock
        self.tokens, self.stamp = float(burst), clock()
        self.lock = threading.Lock()

    def take(self):
        """0 when a token was taken, else the seconds until one is available."""
        with self.lock:
            now = self.clock()
            self.tokens = min(self.burst, self.tokens + (now - self.stamp) * self.rate)
            self.stamp = now
            if self.tokens >= 1:
                self.tokens -= 1
                return 0.0
            return (1 - self.tokens) / self.rate


class KeyedBuckets:
    """A Bucket per client key (the device behind `tailscale serve`); beyond `cap` keys the one unused longest is forgotten."""

    def __init__(self, rate, burst, clock=time.monotonic, cap=TERM_KEYS):
        self.rate, self.burst, self.clock, self.cap = rate, burst, clock, cap
        self._buckets = {}
        self._lock = threading.Lock()

    def take(self, key):
        """0 when `key` may go on, else the seconds until it may."""
        with self._lock:
            bucket = self._buckets.pop(key, None) or Bucket(self.rate, self.burst, self.clock)
            self._buckets[key] = bucket
            while len(self._buckets) > self.cap:
                del self._buckets[next(iter(self._buckets))]
        return bucket.take()


# ---------------------------------------------------------------- the mux

class NoMux(Exception):
    """No Hivegrid window to talk to."""


class MuxFailed(Exception):
    """A `wezterm cli` call failed."""


class ClientGone(Exception):
    """The client closed its connection while its long poll was held: nobody is left to answer."""


_SLOTS = threading.BoundedSemaphore(SUBPROCESS_SLOTS)


def cli_runner(exe, sock, timeout=CLI_TIMEOUT, runner=subprocess.run, slots=_SLOTS):
    """`run(*args) -> stdout | None` for `wezterm cli` against the GUI at `sock`; None on failure."""
    env = dict(os.environ)
    env["WEZTERM_UNIX_SOCKET"] = sock
    lib = os.path.join(os.path.dirname(exe), "lib")
    if os.path.isdir(lib):
        env["LD_LIBRARY_PATH"] = lib + (os.pathsep + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")

    def run(*args):
        with slots:
            try:
                r = runner([exe, "cli", *args], capture_output=True, text=True, timeout=timeout, env=env)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                return None
        return r.stdout if r.returncode == 0 else None
    return run


def _proc_exe(pid):
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None


def runtime_dir(environ=None):
    environ = os.environ if environ is None else environ
    base = environ.get("XDG_RUNTIME_DIR")
    return os.path.join(base, "wezterm") if base else os.path.join(os.path.expanduser("~"), ".local", "share", "wezterm")


def discover_gui_socket(rdir=None, proc_exe=_proc_exe, alive=A._pid_alive):
    """(socket path, `wezterm` executable) of the running GUI, or None. The sockets are
    `gui-sock-<pid>` in the runtime dir; the newest live one wins, a socket a published
    `x11-*`/`wayland-*` link points at wins over that."""
    rdir = rdir or runtime_dir()
    try:
        names = os.listdir(rdir)
    except OSError:
        return None
    live = {}
    for name in names:
        m = re.fullmatch(r"gui-sock-(\d+)", name)
        if not m:
            continue
        pid = int(m.group(1))
        exe = proc_exe(pid) if alive(pid) else None
        if not exe:
            continue
        exe = exe.removesuffix(" (deleted)")
        if not os.path.basename(exe).startswith("wezterm-gui"):
            continue
        path = os.path.join(rdir, name)
        try:
            mtime = os.lstat(path).st_mtime_ns
        except OSError:
            continue
        live[path] = (mtime, exe)
    if not live:
        return None
    best = None
    links = []
    for name in names:
        if name.startswith(("x11-", "wayland-")):
            link = os.path.join(rdir, name)
            try:
                if os.path.islink(link):
                    links.append((os.lstat(link).st_mtime_ns, os.path.join(rdir, os.path.basename(os.path.realpath(link)))))
            except OSError:
                continue
    for _mt, target in sorted(links, reverse=True):
        if target in live:
            best = target
            break
    if best is None:
        best = max(live, key=lambda p: live[p][0])
    exe = live[best][1]
    sibling = os.path.join(os.path.dirname(exe), "wezterm")
    return best, (sibling if os.path.exists(sibling) else exe)


class Mux:
    """The agents of the running GUI. Finds the socket itself, again after a failed call (a GUI
    restart), and keeps the last agent list for `ttl` seconds."""

    def __init__(self, locate=discover_gui_socket, make_run=cli_runner, now=time.time, fg=None, ttl=LIST_TTL,
                 clock=time.monotonic, exists=os.path.exists):
        self.locate, self.make_run, self.now, self.fg, self.ttl, self.clock, self.exists = locate, make_run, now, fg, ttl, clock, exists
        self._lock = threading.Lock()       # one collect at a time (it writes seen.json and acks.json)
        self._loc_lock = threading.Lock()
        self._run = None
        self._sock = None
        self._failed = False
        self.list_ok = False
        self._cache = None
        self._cache_at = 0.0

    def _runner(self):
        with self._loc_lock:
            if self._run is None or self._failed or (self._sock and not self.exists(self._sock)):
                found = self.locate()
                if found is None:
                    self._run, self._sock = None, None
                else:
                    self._sock = found[0]
                    self._run = self.make_run(found[1], found[0])
                self._failed = False
            return self._run

    def run(self, *args):
        runner = self._runner()
        out = runner(*args) if runner else None
        if args and args[0] == "list":
            ok = out is not None
            if ok:
                try:
                    ok = isinstance(json.loads(out), list)
                except ValueError:
                    ok = False
            self.list_ok = ok
            if not ok:
                self._failed = True
        if out is None:
            self._failed = True
        return out

    def agents(self, fresh=False):
        """The agents, cached for `ttl` seconds unless `fresh`; NoMux when the list call failed."""
        with self._lock:
            if not fresh and self._cache is not None and self.clock() - self._cache_at < self.ttl:
                return self._cache
            self.list_ok = False
            fg = self.fg() if self.fg else None
            agents = A.collect(self.run, self.now, fg=fg)
            if not self.list_ok and self._sock and self.exists(self._sock):
                # the socket is there but the call failed: a GUI that restarted on the same path,
                # or a hiccup. Look again (the failed call made `run` re-discover) and retry once.
                self.list_ok = False
                agents = A.collect(self.run, self.now, fg=fg)
            if not self.list_ok:
                self._cache = None
                raise NoMux("no Hivegrid window found")
            self._cache, self._cache_at = agents, self.clock()
            return agents

    def act(self, fn):
        """`fn()` under the collect lock: the actions that write acks.json, seen.json or command files
        never overlap a collect or each other."""
        with self._lock:
            return fn()

    def invalidate(self):
        self._cache = None

    def tail(self, pane, lines):
        out = self.run("get-text", "--pane-id", str(pane), "--start-line", f"-{int(lines)}")
        if out is None:
            raise MuxFailed("could not read the pane")
        return out

    def send_text(self, pane, text):
        if self.run("send-text", "--pane-id", str(pane), "--", text) is None:
            raise MuxFailed("send-text failed")

    def send_keys(self, pane, keys, text=None):
        """`text` pasted first (no Enter), then each key in its own call; returns how many sends."""
        sent = 0
        if text:
            self.send_text(pane, text)
            sent += 1
        for key in keys:
            if self.run("send-text", "--pane-id", str(pane), "--no-paste", key) is None:
                raise MuxFailed("send-text failed")
            sent += 1
        self.invalidate()
        return sent


# ---------------------------------------------------------------- serializers

def tail_lines(text, n):
    """The last `n` rows of a `get-text` dump: escapes and control characters gone, trailing blank
    rows dropped, rows capped, the whole capped at TAIL_BYTES."""
    rows = [sanitize_line(r, TAIL_LINE, collapse=False) for r in (text or "").split("\n")]
    while rows and not rows[-1]:
        rows.pop()
    rows = rows[-n:] if n > 0 else []
    total = 0
    keep = []
    for row in reversed(rows):
        total += len(row.encode("utf-8")) + 1
        if total > TAIL_BYTES:
            break
        keep.append(row)
    return keep[::-1]


def rev_of(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]


def state_vocabulary():
    return {k: {"glyph": U.STATES[k].glyph, "label": U.STATES[k].label, "color": U.STATES[k].color,
                "rank": U.STATES[k].rank} for k in VOCAB}


CAPABILITIES = ("chat", "accounts", "system", "sessions", "model", "longpoll", "history", "quit")


def meta_json(readonly, terminal=False):
    """The server's limits and vocabulary. `terminal`: the page may open a terminal (never on a read-only server)."""
    terminal = bool(terminal) and not readonly
    capabilities = CAPABILITIES + (("terminal",) if terminal else ())
    states = state_vocabulary()
    limits = {"prompt": MAX_PROMPT, "keys": MAX_KEYS, "key_text": MAX_KEY_TEXT, "feedback": MAX_FEEDBACK,
              "tail_max": TAIL_MAX, "chat_max": CHAT_MAX, "wait_max": WAIT_MAX}
    return {"v": API_VERSION, "readonly": bool(readonly), "terminal": terminal, "limits": limits,
            "states": states, "keys": list(KEYS), "dangerous": list(DANGEROUS_KEYS),
            "capabilities": list(capabilities),
            "meta_rev": rev_of([states, bool(readonly), limits, capabilities])}


def pending_json(pend):
    """The part of a pending request a phone shows: whitelisted fields, sanitized and capped."""
    if not isinstance(pend, dict):
        return None
    kind = pend.get("kind")
    if kind == "approval":
        return {"kind": kind, "tool": sanitize_line(pend.get("tool"), 80), "summary": sanitize_line(pend.get("summary"), 200)}
    if kind == "ask":
        options = pend.get("options") if isinstance(pend.get("options"), list) else []
        return {"kind": kind, "question": sanitize_line(pend.get("question"), 300),
                "options": [sanitize_line(o, 80) for o in options[:8] if isinstance(o, str)]}
    if kind == "plan_review":
        raw = pend.get("choices") if isinstance(pend.get("choices"), list) else []
        choices = [{"id": c["id"], "label": sanitize_line(c.get("label") or c["id"], 80)}
                   for c in raw if isinstance(c, dict) and c.get("id") in A.PLAN_CHOICES]
        if not choices:
            choices = [{"id": c, "label": c.capitalize()} for c in A.PLAN_CHOICES]
        return {"kind": kind, "title": sanitize_line(pend.get("title"), 200), "choices": choices}
    return None


def can_flags(agent, pending, readonly, switching=False, now=None):
    if readonly:
        return {"prompt": False, "keys": False, "plan": False, "ack": False, "model": False, "account": False, "quit": False}
    return {"prompt": agent.state not in ("attention", "exited"), "keys": agent.state != "exited",
            "plan": bool(pending and pending["kind"] == "plan_review"),
            "ack": agent.state in ("done", "idle") or (now is not None and A.limit_expired(agent, now)),
            "model": bool(agent.model) and agent.state != "exited",
            "account": not switching and D.account_block(agent) is None,
            "quit": not switching and HI.quit_block(agent) is None}


def model_json(model):
    """The agent's model as the phone shows it: id, name, provider (sanitized), or None."""
    if not isinstance(model, dict):
        return None
    return {"id": sanitize_line(model.get("id"), 240), "name": sanitize_line(model.get("name"), 120),
            "provider": sanitize_line(model.get("provider"), 80)}


def context_json(ctx):
    if not isinstance(ctx, dict):
        return None
    return {"tokens": ctx["tokens"], "window": ctx["window"], "pct": ctx["pct"]}


def agent_json(agent, agents, now, readonly=False, session=False, limit=None, switch=None):
    state = agent.state if agent.state in U.STATES else "idle"
    pending = pending_json(agent.pending)
    usage = None
    if agent.usage is not None:
        usage = {"text": A.usage_text(agent.usage), "tokens": agent.usage.tokens, "cost": agent.usage.cost,
                 "cost_kind": agent.usage.cost_kind}
    return {
        "id": agent.pane, "label": A.agent_label(agent, agents), "num": agent.num, "tab": agent.tab_index,
        "rank": U.STATES[state].rank, "state": state, "state_label": U.STATES[state].label,
        "title": sanitize_line(HV.calm_title(agent.title), 200), "cwd": sanitize_line(U.home_path(agent.cwd), 200),
        "profile": sanitize_line(agent.profile, 80) if agent.profile else None,
        "since": agent.since, "age": HV.human_age(now - agent.since) if agent.since else None,
        "reason": sanitize_line(agent.reason, 200), "error": sanitize_line(agent.error, 200) if agent.error else None,
        "active": bool(agent.active), "pending": pending, "usage": usage,
        "can": can_flags(agent, pending, readonly, bool(switch and switch["state"] == "running"), now),
        "model": model_json(agent.model), "thinking": agent.thinking, "context": context_json(agent.context),
        "session": bool(session), "limit": limit, "switch": switch,
    }


def agents_json(agents, now, readonly=False, sessions=None, terminal=False, limits=None, switches=None):
    """The agent list. `sessions(agent) -> bool` says whether a chat is available (none: never);
    `limits(agent)` and `switches(agent)` give an agent's limit and account-switch objects (None: none).
    The revision covers everything but the ages, which the page computes from `since`."""
    ordered = sorted(agents, key=lambda a: (a.tab_index, a.num, a.pane))
    rows = [agent_json(a, agents, now, readonly, bool(sessions and sessions(a)), limits(a) if limits else None,
                       switches(a) if switches else None) for a in ordered]
    by_state = {}
    for r in rows:
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1
    totals = {"agents": len(rows), "by_state": by_state,
              "usage": A.totals_text(A.usage_totals(a.usage for a in agents))}
    rev = rev_of([{k: v for k, v in r.items() if k != "age"} for r in rows] + [totals])
    return {"v": API_VERSION, "now": now, "meta_rev": meta_json(readonly, terminal)["meta_rev"], "rev": rev,
            "totals": totals, "agents": rows}


def ansi_colors(terminal):
    """The 16 terminal colours of a saved theme (`terminal` = its ansi and brights lists) as #rrggbb, any
    missing or malformed one replaced by the default theme's."""
    default = PRESETS[DEFAULT_PRESET]["terminal"]
    terminal = terminal if isinstance(terminal, dict) else {}
    out = []
    for key in ("ansi", "brights"):
        stored = terminal.get(key) if isinstance(terminal.get(key), list) else []
        for i in range(8):
            value = stored[i] if i < len(stored) else None
            out.append(value if isinstance(value, str) and HEX_RE.match(value) else default[key][i])
    return out


def theme_css(palette, ansi=None):
    """:root custom properties of the saved palette; a missing or malformed colour falls back to the default.
    With `ansi` (16 colours, see ansi_colors) also --ansi0 .. --ansi15, the colours of the page's terminal."""
    palette = palette if isinstance(palette, dict) else {}
    lines = []
    for name in THEME_VARS:
        value = palette.get(name)
        if not (isinstance(value, str) and HEX_RE.match(value)):
            value = U.DEFAULT_PAL[name]
        lines.append(f"  --{name.replace('_', '-')}: {value};")
    for i, value in enumerate(ansi or ()):
        lines.append(f"  --ansi{i}: {value};")
    return ":root {\n" + "\n".join(lines) + "\n  color-scheme: dark;\n}\n"


# ---------------------------------------------------------------- the application

class HttpError(Exception):
    def __init__(self, code, message, headers=None, **extra):
        super().__init__(message)
        self.code, self.message, self.headers, self.extra = code, message, headers or {}, extra


class Request:
    """What App.dispatch needs of an HTTP request. `headers` has lower-case names; `read(n)` returns
    the body bytes; `duplicate` is true when a header that must be unique came twice; `alive()` says
    whether the client is still connected (a held request stops waiting for one that left)."""

    def __init__(self, method, target, headers, peer="", read=None, duplicate=False, alive=None):
        self.method, self.target, self.headers, self.peer = method, target, headers, peer
        self.read, self.duplicate = read or (lambda n: b""), duplicate
        self.alive = alive or (lambda: True)
        self.body_read = False


class Response:
    def __init__(self, status, body=b"", content_type="application/json", headers=None, close=False):
        self.status, self.body, self.content_type = status, body, content_type
        self.headers = headers or {}
        self.close = close


def _json_body(obj):
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


def _etag_match(header, rev):
    if not header:
        return False
    for part in header.split(","):
        part = part.strip()
        if part.startswith("W/"):
            part = part[2:]
        if part == "*" or part.strip('"') == rev:
            return True
    return False


class App:
    """The request handling, free of sockets: `dispatch(Request) -> Response`."""

    STATIC = {
        "/": ("text/html; charset=utf-8", "INDEX_HTML"), "/app.js": ("text/javascript; charset=utf-8", "APP_JS"),
        "/app.css": ("text/css; charset=utf-8", "APP_CSS"),
        "/manifest.webmanifest": ("application/manifest+json", "MANIFEST"),
    }
    TERM_STATIC = {"/term.js": ("text/javascript; charset=utf-8", "TERM_JS"), "/term.css": ("text/css; charset=utf-8", "TERM_CSS"),
                   "/term-view.js": ("text/javascript; charset=utf-8", "TERM_VIEW_JS")}
    GET_ROUTES = ("/api/meta", "/api/agents", "/api/accounts", "/api/system", "/api/sessions/options", "/api/sessions/dirs",
                  "/api/history")
    POST_ROUTES = ("/api/sessions",)
    GET_ACTIONS = ("tail", "chat", "models", "accounts", "files")

    FONTS = {
        "/fonts/mono.ttf": ("~/.local/share/fonts/DejaVuSansMNerdFont/DejaVuSansMNerdFontMono-Regular.ttf",
                            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
        "/fonts/mono-bold.ttf": ("~/.local/share/fonts/DejaVuSansMNerdFont/DejaVuSansMNerdFontMono-Bold.ttf",
                                 "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"),
    }

    def __init__(self, mux, tokens, readonly=False, allow_hosts=(), clock=time.monotonic, wall=time.time,
                 log=None, theme_watch=None, sleep=None, fonts=None, home=None, sysstats=None, chat=None,
                 terminal=False, terminals=None, switcher=None, outbox=None, quitter=None, history=None):
        self.mux, self.tokens, self.readonly = mux, tokens, bool(readonly)
        self.allow_hosts = tuple(h.lower() for h in allow_hosts)
        self.clock, self.wall = clock, wall
        self._stop = threading.Event()
        self.sleep = sleep or self._stop.wait       # returns True when the server is closing
        self.home = home                            # None: $HOME (tests pass a temp directory)
        self.auth_limit = AuthLimiter(clock)
        self.read_bucket = Bucket(15.0, 30, clock)
        self.write_bucket = Bucket(2.0, 10, clock)
        self.session_bucket = Bucket(1.0 / SESSION_EVERY, SESSION_BURST, clock)
        self._session_lock = threading.Lock()
        self._session_recent = {}                   # (directory, profile) -> when it was last started
        self._tails = {}                            # pane -> (when, lines, text)
        self._tails_lock = threading.Lock()
        self.log = log or (lambda msg: print(f"hivegrid-remote: {time.strftime('%H:%M:%S')} {msg}", file=sys.stderr, flush=True))
        self._theme_watch = theme_watch
        self._theme_lock = threading.Lock()
        self._theme = None
        self.chat = chat or D.ChatCache(sanitize_text, sanitize_line)
        self.queued = PQ.Queued(sanitize_text, MAX_PROMPT)   # prompts sent from the phone that omp may still hold in its queue
        self.outbox = outbox or O.Outbox()
        self.sysstats = sysstats or D.SysStats(slots=_SLOTS)
        self._fonts = {route: tuple(os.path.expanduser(p) for p in paths) for route, paths in (fonts or self.FONTS).items()}
        self._font_cache = {}
        self._icon = None
        self._sessions_seen = {}
        self.terminal_flag = bool(terminal)         # --terminal: the terminal is on whatever settings.json says
        self.term_io = KeyedBuckets(TERM_IO_RATE, TERM_IO_BURST, clock)
        self.term_open = KeyedBuckets(1.0 / TERM_OPEN_EVERY, TERM_OPEN_BURST, clock)
        self.terminals = terminals or T.Manager(clock=clock, log=self.log)
        self._remote = None                         # the `remote` settings and the file stamp they were read at
        self._remote_stamp = None
        self._remote_lock = threading.Lock()
        self.probe = D.LimitProbe()
        self.switcher = switcher or D.AccountSwitcher(self.mux, sanitize_line, home=self.home, clock=clock, wall=wall,
                                                       sleep=self.sleep, foreground=_foreground)
        self.account_bucket = Bucket(1.0 / ACCOUNT_EVERY, ACCOUNT_BURST, clock)
        self.quit_bucket = Bucket(1.0 / QUIT_EVERY, QUIT_BURST, clock)
        self.quitter = quitter or HI.SessionQuitter(self.mux, clock=clock, sleep=self.sleep, foreground=_foreground)
        self.history = history or HI.History(sanitize_line, home=self.home, clock=clock)

    # -- helpers
    def _error(self, err):
        body = {"ok": False, "error": err.code, "message": err.message}
        body.update(err.extra)
        return Response(STATUS_TEXT[err.code], _json_body(body), headers=dict(err.headers))

    def error_response(self, status, message):
        code = {400: "bad_request", 404: "not_found", 405: "method_not_allowed", 411: "length_required",
                413: "too_large", 414: "bad_request", 431: "bad_request", 501: "method_not_allowed"}.get(status, "bad_request")
        return Response(STATUS_TEXT[code], _json_body({"ok": False, "error": code, "message": message}), close=True)

    def _watch(self):
        with self._theme_lock:
            if self._theme_watch is None:
                self._theme_watch = ThemeWatch()
            return self._theme_watch

    def settings(self):
        """The settings file as it is now (the accounts come from it)."""
        return Settings(self._watch().path)

    def theme_text(self):
        watch = self._watch()
        with self._theme_lock:
            palette = watch.poll()
            if palette is not None or self._theme is None:
                theme = Settings(watch.path).theme()
                self._theme = theme_css(theme["palette"] if palette is None else palette, ansi_colors(theme["terminal"]))
            return self._theme

    def close(self):
        """Wakes the held requests (they answer at once) and ends every terminal when the server shuts down."""
        self._stop.set()
        self.terminals.shutdown()

    # -- pipeline
    def dispatch(self, req):
        try:
            resp = self._dispatch(req)
        except HttpError as err:
            resp = self._error(err)
        if not req.body_read and (req.method == "POST" or req.headers.get("content-length", "0") not in ("", "0")
                                  or "transfer-encoding" in req.headers):
            resp.close = True
        return resp

    def _dispatch(self, req):
        if not host_allowed(req.headers.get("host"), self.allow_hosts):
            raise HttpError("forbidden_host", "this host name is not allowed")
        if req.duplicate:
            raise HttpError("bad_request", "duplicate header")
        if req.method not in ("GET", "POST"):
            raise HttpError("method_not_allowed", "method not allowed")
        if not req.target.startswith("/"):
            raise HttpError("bad_request", "bad request target")
        url = urllib.parse.urlsplit(req.target)
        path = url.path
        if path == "/healthz":
            return self._static(req, "text/plain; charset=utf-8", b"ok")
        if path == "/theme.css":
            return self._static(req, "text/css; charset=utf-8", self.theme_text().encode())
        if path in self.STATIC:
            ctype, name = self.STATIC[path]
            return self._static(req, ctype, getattr(UI, name).encode("utf-8"))
        if path in self.TERM_STATIC:
            ctype, name = self.TERM_STATIC[path]
            return self._static(req, ctype, getattr(TJ, name).encode("utf-8"))
        if path == "/logo.svg":
            return self._logo(req)
        if path == "/icon.png":
            return self._icon_png(req)
        if path in self._fonts:
            return self._font(req, path)
        if path.startswith("/api/"):
            return self._api(req, path, url.query)
        raise HttpError("not_found", "not found")

    def _static(self, req, ctype, body):
        if req.method != "GET":
            raise HttpError("method_not_allowed", "method not allowed")
        rev = hashlib.sha1(body).hexdigest()[:16]
        headers = {"Cache-Control": "no-cache", "ETag": f'"{rev}"'}
        if _etag_match(req.headers.get("if-none-match"), rev):
            return Response(304, b"", ctype, headers)
        return Response(200, body, ctype, headers)

    def _logo(self, req):
        svg = getattr(UI, "LOGO_SVG", None)
        if not isinstance(svg, str) or not svg:
            raise HttpError("not_found", "not found")
        return self._static(req, "image/svg+xml", svg.encode("utf-8"))

    def _icon_png(self, req):
        if self._icon is None:
            try:
                self._icon = base64.b64decode(getattr(UI, "ICON_PNG_B64", ""), validate=True)
            except (ValueError, TypeError):
                self._icon = b""
        if not self._icon.startswith(b"\x89PNG"):
            raise HttpError("not_found", "not found")
        return self._static(req, "image/png", self._icon)

    def _font(self, req, path):
        """The terminal's font from this machine (never from the repo): the first candidate that is a
        regular file of reasonable size. Public like the other statics; the Host check already passed."""
        if req.method != "GET":
            raise HttpError("method_not_allowed", "method not allowed")
        for candidate in self._fonts.get(path, ()):
            try:
                st = os.stat(candidate)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size <= MAX_FONT:
                continue
            sig = (st.st_mtime_ns, st.st_size)
            cached = self._font_cache.get(candidate)
            if cached is None or cached[0] != sig:
                try:
                    with open(candidate, "rb") as f:
                        data = f.read(MAX_FONT + 1)
                except OSError:
                    continue
                if len(data) > MAX_FONT:
                    continue
                cached = self._font_cache[candidate] = (sig, data)
            return Response(200, cached[1], "font/ttf", {"Cache-Control": "public, max-age=86400"})
        raise HttpError("not_found", "not found")

    def _client_key(self, req):
        """Who a request counts as for the limits. Forwarding headers are believed only from a
        loopback peer (`tailscale serve` is one); from anywhere else they are the client's own claim."""
        if not is_loopback(req.peer):
            return req.peer
        xff = sanitize_line((req.headers.get("x-forwarded-for") or "").split(",")[0], 100)
        who = sanitize_line(req.headers.get("tailscale-user-login") or "", 100)
        return "|".join((req.peer, xff, who))

    def _api(self, req, path, query):
        key = self._client_key(req)
        header = req.headers.get("authorization")
        token = parse_bearer(header)
        if token is not None and self.tokens.check(token):
            self.auth_limit.ok(key)   # a valid token is never held back by lockouts
        else:
            wait = self.auth_limit.blocked(key)
            if wait > 0:
                raise HttpError("rate_limited", "too many failed attempts", {"Retry-After": str(math.ceil(wait))})
            if header is not None:
                locked = self.auth_limit.fail(key)
                self.log(f"auth failed key={key}" + (f" locked for {locked:.0f}s" if locked else ""))
            raise HttpError("unauthorized", "missing or wrong token", {"WWW-Authenticate": "Bearer"})
        terminal = path == "/api/terminals" or path.startswith("/api/terminals/")
        # Terminal traffic (a request per batch of keystrokes, a held poll per output) has its own allowance per
        # device: it neither starves the agent calls nor is starved by them.
        wait = self.term_io.take(key) if terminal else (self.write_bucket if req.method == "POST" else self.read_bucket).take()
        if wait > 0:
            raise HttpError("rate_limited", "slow down", {"Retry-After": str(max(math.ceil(wait), 1))})
        if terminal:
            return self._terminal(req, key, token, path, query)

        fm = re.fullmatch(r"/api/agents/([^/]+)/files(?:/([^/]+))?", path)
        hm = re.fullmatch(r"/api/history/([0-9a-f]{24})/revive", path)
        m = fm or re.fullmatch(r"/api/agents/([^/]+)/(tail|chat|models|accounts|prompt|keys|plan|ack|model|thinking|account|quit)", path)
        if path in self.GET_ROUTES or path in self.POST_ROUTES:
            action, ident = path, None
        elif hm:
            action, ident = "revive", hm.group(1)
        elif fm:
            action, ident = "files", fm.group(1) + ("/" + fm.group(2) if fm.group(2) else "")
        elif m:
            action, ident = m.group(2), m.group(1)
        else:
            raise HttpError("not_found", "not found")
        want = "GET" if action in self.GET_ROUTES or action in self.GET_ACTIONS else "POST"
        if req.method != want:
            raise HttpError("method_not_allowed", "method not allowed")
        try:
            if req.method == "GET":
                return self._get(req, action, ident, query)
            body = self._post_body(req)
            if self.readonly:
                raise HttpError("readonly", "this server is read-only")
            return self._post(action, ident, body)
        except NoMux:
            raise HttpError("no_mux", "no Hivegrid window found") from None
        except MuxFailed as exc:
            raise HttpError("mux_failed", str(exc)) from None

    def _post_body(self, req):
        h = req.headers
        if not origin_ok(h.get("origin"), h.get("host"), h.get("sec-fetch-site")):
            raise HttpError("forbidden_origin", "cross-site request refused")
        if (h.get("content-type") or "").split(";")[0].strip().lower() != "application/json":
            raise HttpError("unsupported_media_type", "Content-Type must be application/json")
        if h.get("x-hivegrid-remote") != "1":
            raise HttpError("forbidden_origin", "missing X-Hivegrid-Remote header")
        if "transfer-encoding" in h or h.get("content-length") is None:
            raise HttpError("length_required", "Content-Length is required")
        length = h["content-length"].strip()
        if not re.fullmatch(r"\d{1,9}", length):
            raise HttpError("bad_request", "bad Content-Length")
        if int(length) > MAX_BODY:
            raise HttpError("too_large", f"body is larger than {MAX_BODY} bytes")
        raw = req.read(int(length))
        req.body_read = True
        if len(raw) != int(length):
            raise HttpError("bad_request", "short body")
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise HttpError("bad_request", "body is not valid JSON") from None
        if not isinstance(body, dict):
            raise HttpError("bad_request", "body must be a JSON object")
        return body

    # -- responses
    def _json(self, req, obj, rev=None):
        headers = {}
        if rev is not None:
            headers["ETag"] = f'"{rev}"'
            if _etag_match(req.headers.get("if-none-match"), rev):
                return Response(304, b"", headers=headers)
        return Response(200, _json_body(obj), headers=headers)

    def _find(self, ident, fresh):
        if not AGENT_ID.fullmatch(ident):
            raise HttpError("not_found", "no such agent")
        pane = int(ident)
        agents = self._agents(fresh)
        for a in agents:
            if a.pane == pane:
                return a, agents
        raise HttpError("not_found", "no such agent")

    def _agents(self, fresh=False):
        """The agents of the mux; a `done` agent whose session file ends on a usage-limit refusal reads as
        `limit` (an omp whose extension predates that state does not say so itself)."""
        return D.with_limits(self.mux.agents(fresh=fresh), self.probe, lambda a: D.find_session_file(a, self.home))

    def _limits(self, agents):
        """{pane: the page's limit object} for the agents stopped on a limit; the settings are read only when there is one."""
        limited = [a for a in agents if a.state == "limit"]
        if not limited:
            return {}
        accounts, now = self.settings().accounts(), self.wall()
        return {a.pane: D.limit_json(a, accounts, now, sanitize_line) for a in limited}

    def _listed(self, agents):
        """The agents the phone's list shows: not the shells that omp left (quit from the phone, or on its own); one
        whose chat is being moved to another account stays, it reads `exited` for a moment on purpose."""
        return [a for a in agents if a.state != "exited" or self.switcher.status(a.pane)]

    def _agents_doc(self):
        agents = self._listed(self._agents())
        limits = self._limits(agents)
        return agents_json(agents, self.wall(), self.readonly, self._has_session, self.terminal_enabled(),
                           lambda a: limits.get(a.pane), lambda a: self.switcher.status(a.pane))

    # -- long polls
    @staticmethod
    def _wait_param(q):
        raw = q.get("wait", ["0"])[0]
        if not re.fullmatch(r"\d{1,4}(\.\d{1,3})?", raw):
            raise HttpError("bad_request", f"wait must be a number of seconds, 0 to {WAIT_MAX}")
        return min(float(raw), float(WAIT_MAX))

    @staticmethod
    def _int_param(q, name, default, low, high):
        if name not in q:
            return default
        if not re.fullmatch(r"\d{1,18}", q[name][0]):
            raise HttpError("bad_request", f"{name} must be a number")
        return min(max(int(q[name][0]), low), high)

    def _hold(self, wait, interval, current, unchanged, alive=None):
        """`current()` now; while `unchanged(it)` and for at most `wait` seconds, again every `interval`.
        Only the first look costs a rate-limit token (taken in `_api`); the loop takes no lock but
        what `current` takes for itself, and ends at once when the server closes. When `alive()` says
        the client is gone it raises ClientGone, so its connection slot is free again within one
        interval instead of after `wait`."""
        data = current()
        if wait > 0 and unchanged(data):
            end = self.clock() + wait
            while True:
                left = end - self.clock()
                if left <= 0 or self.sleep(min(interval, left)):
                    break
                if alive is not None and not alive():
                    raise ClientGone()
                data = current()
                if not unchanged(data):
                    break
        return data

    def _has_session(self, agent):
        """Whether a chat is available for `agent` (its session file is found), remembered for 5 s."""
        key, now = (agent.pid, agent.session_file), self.clock()
        seen = self._sessions_seen.get(agent.pane)
        if seen and seen[0] == key and now - seen[1] < 5.0:
            return seen[2]
        ok = D.find_session_file(agent, self.home) is not None
        if len(self._sessions_seen) > 256:
            self._sessions_seen.clear()
        self._sessions_seen[agent.pane] = (key, now, ok)
        return ok

    def _files(self, agent):
        """The files `hivegrid-send` put in the outbox for `agent`, newest first, as the page lists them."""
        win = O.window_key(getattr(self.mux, "_sock", None))
        return [{"id": m["id"], "name": m["name"], "size": m["size"], "ts": m["ts"], "image": m["mime"] != "application/octet-stream",
                 "note": m["note"]} for m in self.outbox.for_pane(agent.pane, win)]

    def _chat_look(self, ident, limit, before):
        agent, agents = self._find(ident, False)
        path = D.find_session_file(agent, self.home)
        stamp = self.chat.stamp(path) if path else None
        pending = pending_json(agent.pending)
        files = [f["id"] for f in self._files(agent)]
        self.queued.retain({a.pane for a in agents})
        queued = self._queued(agent, path if stamp else None, before)
        rev = rev_of([stamp if stamp else "screen", agent.state, agent.reason, pending, limit, before, self.switcher.status(agent.pane), files, queued])
        return agent, agents, path if stamp else None, rev

    def _session_mark(self, agent):
        """(session file, `ChatCache.mark`) of `agent` now; (None, None) when it has no readable one."""
        path = D.find_session_file(agent, self.home)
        mark = self.chat.mark(path) if path else None
        return (path, mark) if mark is not None else (None, None)

    def _queued(self, agent, path, before):
        """The prompts sent from the phone that the session file does not have yet (the older pages have none)."""
        if before is not None:
            return []
        return self.queued.view(agent, path, lambda p, n, ts: self.chat.user_items(p, min_n=n, min_ts=ts), self.wall())

    def _file(self, ident):
        """`GET /api/agents/<id>/files/<file>`: the bytes of one file sent to that agent's page. Images are served
        as their sniffed type, everything else as a download."""
        pane_id, _, fid = ident.partition("/")
        agent, _agents = self._find(pane_id, False)
        win = O.window_key(getattr(self.mux, "_sock", None))
        got = self.outbox.read(fid) if any(m["id"] == fid for m in self.outbox.for_pane(agent.pane, win)) else None
        if got is None:
            raise HttpError("not_found", "no such file")
        meta, data = got
        image = meta["mime"] != "application/octet-stream"
        quoted = urllib.parse.quote(meta["name"], safe="")
        ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", meta["name"])[:100] or "file"
        disp = f'{"inline" if image else "attachment"}; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted}'
        return Response(200, data, content_type=meta["mime"], headers={"Content-Disposition": disp})

    def _chat(self, req, ident, q):
        limit = self._int_param(q, "limit", CHAT_DEFAULT, 1, CHAT_MAX)
        before = self._int_param(q, "before", None, 0, 1 << 62)
        if before is not None and before < 1:
            raise HttpError("bad_request", "before must be the number of a chat item")
        client_rev = q.get("rev", [None])[0]
        if client_rev is not None and not re.fullmatch(r"[0-9a-f]{1,32}", client_rev):
            raise HttpError("bad_request", "bad rev")
        wait = self._wait_param(q) if before is None else 0
        look = self._hold(wait, CHAT_POLL, lambda: self._chat_look(ident, limit, before),
                          lambda s: client_rev is not None and before is None and s[3] == client_rev, req.alive)
        agent, agents, path, rev = look
        if client_rev is not None and before is None and rev == client_rev:
            return Response(200, _json_body({"unchanged": True, "rev": rev}))
        queued = self._queued(agent, path, before)      # before the window: nothing that left the queue is missing from the items
        got = self.chat.window(path, limit, before) if path else None
        if got is None:
            items, has_older, source, gen = [], False, "screen", 0
            rev = rev_of(["screen", agent.state, agent.reason, pending_json(agent.pending), limit, before, self.switcher.status(agent.pane), [f["id"] for f in self._files(agent)], queued])
        else:
            items, has_older, stamp = got
            gen = stamp[0]
            source = "session"
            rev = rev_of([stamp, agent.state, agent.reason, pending_json(agent.pending), limit, before, self.switcher.status(agent.pane), [f["id"] for f in self._files(agent)], queued])
        state = agent.state if agent.state in U.STATES else "idle"
        return Response(200, _json_body({
            "id": agent.pane, "label": A.agent_label(agent, agents), "source": source, "state": state,
            "state_label": U.STATES[state].label, "reason": sanitize_line(agent.reason, 200), "since": agent.since,
            "limit": self._limits([agent]).get(agent.pane), "switch": self.switcher.status(agent.pane), "files": self._files(agent),
            "rev": rev, "gen": gen, "has_older": has_older, "older": before is not None, "items": items, "pending_prompts": queued}))

    def _models(self, req, ident):
        agent, _agents = self._find(ident, False)
        listed = A.read_models(agent.pane)
        models = []
        for m in listed["models"]:
            row = {"id": sanitize_line(m["id"], 240), "name": sanitize_line(m["name"], 120),
                   "provider": sanitize_line(m["provider"], 80), "reasoning": m["reasoning"]}
            if row["id"] == m["id"]:        # an id that needs cleaning could not be switched to by that name anyway
                models.append(row)
        return Response(200, _json_body({
            "current": sanitize_line(agent.model["id"], 240) if agent.model else None, "thinking": agent.thinking,
            "levels": listed["levels"], "models": models}))

    def _agents_or_none(self):
        """The agents, or none when there is no Hivegrid window (accounts and options need no window)."""
        try:
            return self._agents()
        except NoMux:
            return []

    def _tail_text(self, pane, n, ttl):
        """The screen text of `pane`, reused when one of at least `n` lines is younger than `ttl`
        seconds: held tail requests on one pane share a `wezterm cli` call per second."""
        now = self.clock()
        with self._tails_lock:
            hit = self._tails.get(pane)
            if hit is not None and hit[1] >= n and now - hit[0] < ttl:
                return hit[2]
        text = self.mux.tail(pane, n)
        with self._tails_lock:
            if len(self._tails) > 64:
                self._tails.clear()
            self._tails[pane] = (self.clock(), n, text)
        return text

    def _get(self, req, action, ident, query):
        q = urllib.parse.parse_qs(query)
        if action == "/api/meta":
            meta = meta_json(self.readonly, self.terminal_enabled())
            return self._json(req, meta, meta["meta_rev"])
        if action == "/api/agents":
            etag = req.headers.get("if-none-match")
            data = self._hold(self._wait_param(q), LIST_POLL,
                              self._agents_doc,
                              lambda d: _etag_match(etag, d["rev"]), req.alive)
            return self._json(req, data, data["rev"])
        if action == "/api/accounts":
            return self._json(req, D.accounts_json(self.settings().accounts(), self._agents_or_none(), self.wall(), sanitize_line))
        if action == "/api/system":
            return self._json(req, self.sysstats.snapshot())
        if action == "/api/sessions/options":
            profiles, dirs, _real = D.session_options(self.settings().accounts(), self._agents_or_none(), sanitize_line, self.home)
            return self._json(req, {"profiles": profiles, "dirs": dirs})
        if action == "/api/history":
            offset = self._int_param(q, "offset", 0, 0, HI.SCAN_MAX)
            limit = self._int_param(q, "limit", HI.PAGE_DEFAULT, 1, HI.PAGE_MAX)
            labels = HI.account_labels(self.settings().accounts())
            return self._json(req, self.history.listing(offset, limit, self._live_sessions(self._agents_or_none()), labels, self.readonly))
        if action == "/api/sessions/dirs":
            query = q.get("q", [""])[0]
            if len(query) > D.SEARCH_QUERY_MAX:
                raise HttpError("bad_request", f"q is at most {D.SEARCH_QUERY_MAX} characters")
            return self._json(req, {"dirs": D.search_dirs(query, sanitize_line, self.home)})
        if action == "files":
            if "/" in ident:
                return self._file(ident)
            agent, _agents = self._find(ident, False)
            return self._json(req, {"files": self._files(agent)})
        if action == "chat":
            return self._chat(req, ident, q)
        if action == "accounts":
            return self._agent_accounts(ident)
        if action == "models":
            return self._models(req, ident)
        n = min(max(self._int_param(q, "lines", TAIL_DEFAULT, 0, 1 << 30), TAIL_MIN), TAIL_MAX)
        etag, wait = req.headers.get("if-none-match"), self._wait_param(q)
        looks = []

        def current():
            agent, agents = self._find(ident, False)
            rows = tail_lines(self._tail_text(agent.pane, n, TAIL_TTL if looks else 0.0), n)
            looks.append(1)
            label = A.agent_label(agent, agents)
            rev = rev_of([rows, agent.state, label])
            return {"id": agent.pane, "label": label, "state": agent.state, "lines": rows, "rev": rev}

        data = self._hold(wait, LIST_POLL, current, lambda d: _etag_match(etag, d["rev"]), req.alive)
        return self._json(req, data, data["rev"])

    @staticmethod
    def _fields(body, allowed):
        extra = sorted(set(body) - set(allowed))
        if extra:
            raise HttpError("bad_request", f"unknown field: {sanitize_line(extra[0], 40)}")

    def _written(self, action, label, nbytes):
        self.log(f"write {action} label={label} bytes={nbytes}")

    def _await_state(self, pane, done):
        """True once the state file of `pane` satisfies `done(doc)`, looking for up to SWITCH_WAIT seconds."""
        end = self.clock() + SWITCH_WAIT
        while True:
            doc = A.read_state(pane)
            if doc is not None and done(doc):
                return True
            left = end - self.clock()
            if left <= 0 or self.sleep(min(0.1, left)):
                return False

    def _switchable(self, agent):
        if agent.state == "exited":
            raise HttpError("agent_not_ready", "agent has exited")
        if agent.state in ("working", "busy", "attention"):
            raise HttpError("agent_not_ready", "wait until the agent is idle")
        if not agent.model:
            raise HttpError("agent_not_ready", "this agent's omp does not publish its model: restart it to switch")

    def _switch(self, ident, body, field, kind):
        """Model or thinking-level change: validate against what the extension published, hand it a
        command file, wait up to SWITCH_WAIT s for the state file to show the change."""
        self._fields(body, (field,))
        value = body.get(field)
        if not isinstance(value, str) or not value or len(value) > 240:
            raise HttpError("bad_request", f"{field} must be a string")
        agent, agents = self._find(ident, True)
        self._switchable(agent)
        listed = A.read_models(agent.pane)
        if kind == "model":
            if value not in {m["id"] for m in listed["models"]}:
                raise HttpError("bad_request", "unknown model")
            current, shown = (agent.model or {}).get("id"), (lambda d: isinstance(d.get("model"), dict) and d["model"].get("id") == value)
            cmd = {"type": "set_model", "model": value}
        else:
            if value not in (listed["levels"] or A.THINKING_LEVELS):
                raise HttpError("bad_request", "unknown thinking level")
            current, shown = agent.thinking, (lambda d: d.get("thinking") == value)
            cmd = {"type": "set_thinking", "level": value}
        if current == value:
            return Response(200, _json_body({"ok": True, "confirmed": True}))
        if not self.mux.act(lambda: A.send_command(agent, cmd)):
            raise HttpError("mux_failed", "could not write the command file")
        self._written(kind, A.agent_label(agent, agents), len(value.encode("utf-8")))
        confirmed = self._await_state(agent.pane, shown)
        self.mux.invalidate()
        return Response(200, _json_body({"ok": True, "confirmed": confirmed}))

    def _agent_accounts(self, ident):
        """`GET /api/agents/<id>/accounts`: the account the agent runs on, the accounts its chat can be moved to
        (with their quota) and whether it can be moved now (`can`, else `why`)."""
        agent, agents = self._find(ident, False)
        current, offered = D.account_options(self.settings().accounts(), agent, self.wall(), sanitize_line)
        switch = self.switcher.status(agent.pane)
        why = D.account_block(agent)
        if self.readonly:
            why = "this server is read-only"
        elif switch and switch["state"] == "running":
            why = "a switch is already running for this agent"
        elif why is None and not offered:
            why = "no other account is paired with an omp profile"
        return Response(200, _json_body({"id": agent.pane, "label": A.agent_label(agent, agents), "current": current,
                                         "accounts": offered, "can": why is None, "why": why, "switch": switch}))

    def _account(self, ident, body):
        """`POST /api/agents/<id>/account {"profile", "confirm": true}`: moves the agent's chat to the omp
        profile of one of the accounts `_agent_accounts` offers (docs/remote.md, "Switching account"). It
        answers 202 once omp was asked to quit; the page follows the move in the agent's `switch` object."""
        self._fields(body, ("profile", "confirm"))
        profile, confirm = body.get("profile"), body.get("confirm", False)
        if not isinstance(profile, str) or not profile or len(profile) > 64:
            raise HttpError("bad_request", "profile must be the profile of an offered account")
        if not isinstance(confirm, bool):
            raise HttpError("bad_request", "confirm must be true or false")
        agent, agents = self._find(ident, True)
        current, offered = D.account_options(self.settings().accounts(), agent, self.wall(), sanitize_line)
        target = next((o for o in offered if o["profile"] == profile), None)
        if target is None:
            raise HttpError("bad_request", "unknown account: choose one of the offered accounts")
        why = D.account_block(agent)
        if why:
            raise HttpError("agent_not_ready", why)
        if not confirm:
            raise HttpError("confirm_required", f"confirm to move the chat to {target['label']}: omp quits and resumes there")
        wait = self.account_bucket.take()
        if wait > 0:
            raise HttpError("rate_limited", "too many account switches: wait a few seconds",
                            {"Retry-After": str(max(math.ceil(wait), 1))})
        try:
            self.switcher.begin(agent, profile, target["label"], current["label"] or agent.profile)
        except D.SwitchRefused as exc:
            raise HttpError(exc.code, exc.message) from None
        self.mux.invalidate()
        self._written("account", A.agent_label(agent, agents), 0)
        return Response(202, _json_body({"ok": True, "switching": True, "to": target["label"]}))

    def _live_sessions(self, agents):
        """{real session file: the agent's label} of the agents that have a session open now (an exited agent has none)."""
        live = {}
        for a in agents:
            path = D.find_session_file(a, self.home) if a.state != "exited" else None
            if path:
                live[path] = A.agent_label(a, agents)
        return live

    def _quit(self, ident, body):
        """`POST /api/agents/<id>/quit {"confirm": true}`: omp is asked to quit with Ctrl+D (see HI.SessionQuitter); the
        shell pane that is left reads `exited` and is no longer listed. docs/remote.md, "Quitting a session"."""
        self._fields(body, ("confirm",))
        confirm = body.get("confirm", False)
        if not isinstance(confirm, bool):
            raise HttpError("bad_request", "confirm must be true or false")
        agent, agents = self._find(ident, True)
        why = HI.quit_block(agent)
        if why:
            raise HttpError("agent_not_ready", why)
        if self.switcher.running(agent.pane):
            raise HttpError("conflict", "an account switch is running for this agent")
        if not confirm:
            raise HttpError("confirm_required", "confirm to quit the session: omp gets Ctrl+D")
        wait = self.quit_bucket.take()
        if wait > 0:
            raise HttpError("rate_limited", "too many quits: wait a few seconds", {"Retry-After": str(max(math.ceil(wait), 1))})
        try:
            self.quitter.quit(agent)
        except HI.Refused as exc:
            raise HttpError(exc.code, exc.message) from None
        self.mux.invalidate()
        self._written("quit", A.agent_label(agent, agents), 0)
        return Response(200, _json_body({"ok": True, "quit": True}))

    def _revive(self, sid, body):
        """`POST /api/history/<id>/revive {"confirm": true}`: `omp [--profile=<p>] --resume <file>` in a new pane in the
        session's directory. The id comes from `GET /api/history`; the file is found and checked again here
        (docs/remote.md, "History")."""
        self._fields(body, ("confirm",))
        confirm = body.get("confirm", False)
        if not isinstance(confirm, bool):
            raise HttpError("bad_request", "confirm must be true or false")
        live = self._live_sessions(self._agents())
        try:
            real, profile, cwd = self.history.revive_plan(sid, live)
        except HI.Refused as exc:
            raise HttpError(exc.code, exc.message) from None
        if not confirm:
            raise HttpError("confirm_required", "confirm to start this session again in a new pane")
        key = ("revive", real)
        with self._session_lock:
            now = self.clock()
            for k in [k for k, t in self._session_recent.items() if now - t >= SESSION_REPEAT]:
                del self._session_recent[k]
            if key in self._session_recent:
                retry = max(math.ceil(SESSION_REPEAT - (now - self._session_recent[key])), 1)
                raise HttpError("conflict", "this session was just started again: it is on its way", {"Retry-After": str(retry)})
            wait = self.session_bucket.take()
            if wait > 0:
                raise HttpError("rate_limited", "too many sessions started: wait a few seconds",
                                {"Retry-After": str(max(math.ceil(wait), 1))})
            self._session_recent[key] = now
        pane = None
        try:
            pane = self.mux.act(lambda: A.spawn_agent(cwd=cwd, run=self.mux.run, profile=profile, arrange=True, resume=real))
        except ValueError as exc:
            raise HttpError("bad_request", sanitize_line(str(exc), 120)) from None
        finally:
            if pane is None:
                with self._session_lock:
                    self._session_recent.pop(key, None)
        if pane is None:
            raise HttpError("mux_failed", "could not start an agent")
        self.mux.invalidate()
        labels = HI.account_labels(self.settings().accounts())
        self.log(f"write revive account={sanitize_line(labels.get(profile) or 'default', 40)}")
        return Response(200, _json_body({"ok": True, "id": pane}))

    def _new_session(self, body):
        self._fields(body, ("profile", "dir"))
        profile, shown = body.get("profile"), body.get("dir")
        if profile is not None and not isinstance(profile, str):
            raise HttpError("bad_request", "profile must be a name or null")
        if not isinstance(shown, str) or not shown or len(shown) > 300:
            raise HttpError("bad_request", "dir must be one of the offered directories")
        profiles, _dirs, real = D.session_options(self.settings().accounts(), self._agents(), sanitize_line, self.home)
        known = {p["profile"]: p["label"] for p in profiles if p["profile"]}
        if profile is not None and profile not in known:
            raise HttpError("bad_request", "unknown profile")
        real_dir = real.get(shown) or D.resolve_custom_dir(shown, self.home)
        if real_dir is None:
            raise HttpError("bad_request", "dir must be an offered directory or a folder below your home")
        key = (real_dir, profile)
        with self._session_lock:
            now = self.clock()
            for k in [k for k, t in self._session_recent.items() if now - t >= SESSION_REPEAT]:
                del self._session_recent[k]
            if key in self._session_recent:
                retry = max(math.ceil(SESSION_REPEAT - (now - self._session_recent[key])), 1)
                raise HttpError("conflict", "a session was just started there on this account: it is on its way",
                                {"Retry-After": str(retry)})
            wait = self.session_bucket.take()
            if wait > 0:
                raise HttpError("rate_limited", "too many sessions started: wait a few seconds",
                                {"Retry-After": str(max(math.ceil(wait), 1))})
            self._session_recent[key] = now
        pane = None
        try:
            pane = self.mux.act(lambda: A.spawn_agent(cwd=real_dir, run=self.mux.run, profile=profile, arrange=True))
        except ValueError as exc:
            raise HttpError("bad_request", sanitize_line(str(exc), 120)) from None
        finally:
            if pane is None:
                with self._session_lock:
                    self._session_recent.pop(key, None)
        if pane is None:
            raise HttpError("mux_failed", "could not start an agent")
        self.mux.invalidate()
        self.log(f"write session account={known.get(profile, 'default')}")
        return Response(200, _json_body({"ok": True, "id": pane}))

    def _post(self, action, ident, body):
        if action == "/api/sessions":
            return self._new_session(body)
        if action == "quit":
            return self._quit(ident, body)
        if action == "revive":
            return self._revive(ident, body)
        if action == "account":
            return self._account(ident, body)
        if action in ("model", "thinking"):
            return self._switch(ident, body, "model" if action == "model" else "level", action)
        if action == "prompt":
            self._fields(body, ("text",))
            text = body.get("text")
            problem = validate_text(text, MAX_PROMPT)
            if problem:
                raise HttpError("bad_request", problem)
            agent, agents = self._find(ident, True)
            sent, path, mark = self.wall(), *self._session_mark(agent)
            ok, msg = self.mux.act(lambda: A.send_prompt(agent, text, self.mux.run))
            if not ok:
                code = "agent_not_ready" if ("waiting for an answer" in msg or "exited" in msg) else "mux_failed"
                raise HttpError(code, msg)
            self.queued.add(agent, text, sent, path, mark, self.wall())
            self.mux.invalidate()
            self._written("prompt", A.agent_label(agent, agents), len(text.encode("utf-8")))
            return Response(200, _json_body({"ok": True, "message": "sent"}))
        if action == "keys":
            return self._keys(ident, body)
        if action == "plan":
            self._fields(body, ("choice", "feedback"))
            choice, feedback = body.get("choice"), body.get("feedback")
            if choice not in A.PLAN_CHOICES:
                raise HttpError("bad_request", "unknown choice")
            if feedback is not None:
                problem = validate_text(feedback, MAX_FEEDBACK, "feedback", allow_empty=True)
                if problem:
                    raise HttpError("bad_request", problem)
            if choice == "refine" and not (feedback and feedback.strip()):
                raise HttpError("bad_request", "refine needs feedback")
            agent, agents = self._find(ident, True)
            ok, msg = self.mux.act(lambda: A.answer_plan_review(agent, choice, feedback or None))
            if not ok:
                raise HttpError("agent_not_ready", msg)
            self.mux.invalidate()
            self._written("plan", A.agent_label(agent, agents), len((feedback or "").encode("utf-8")))
            return Response(200, _json_body({"ok": True, "message": "sent"}))
        self._fields(body, ())
        agent, agents = self._find(ident, True)
        if agent.state not in ("done", "idle") and not A.limit_expired(agent, self.wall()):
            raise HttpError("agent_not_ready", "nothing to acknowledge: the agent is not done or idle, and its limit has not reset")
        self.mux.act(lambda: A.ack(agent, self.wall()))
        self.mux.invalidate()
        self._written("ack", A.agent_label(agent, agents), 0)
        return Response(200, _json_body({"ok": True}))

    def _keys(self, ident, body):
        self._fields(body, ("keys", "text", "confirm"))
        names = body.get("keys", [])
        text = body.get("text")
        confirm = body.get("confirm", False)
        if not isinstance(names, list) or len(names) > MAX_KEYS:
            raise HttpError("bad_request", f"keys must be a list of at most {MAX_KEYS}")
        for k in names:
            if not (isinstance(k, str) and (k in KEYS or SINGLE_KEY.fullmatch(k))):
                raise HttpError("bad_request", "unknown key")
        if not isinstance(confirm, bool):
            raise HttpError("bad_request", "confirm must be true or false")
        if text is not None:
            problem = validate_text(text, MAX_KEY_TEXT)
            if problem:
                raise HttpError("bad_request", problem)
        if not names and not text:
            raise HttpError("bad_request", "nothing to send")
        agent, agents = self._find(ident, True)
        if agent.state == "exited":
            raise HttpError("agent_not_ready", "agent has exited")
        danger = [k for k in names if k in DANGEROUS_KEYS]
        if danger and not confirm:
            raise HttpError("confirm_required", "confirm to send " + ", ".join(danger), dangerous=danger)
        sent = self.mux.act(lambda: self.mux.send_keys(agent.pane, [KEYS.get(k, k) for k in names], text))
        self._written("keys", A.agent_label(agent, agents), sum(len(k) for k in names) + len(text or ""))
        return Response(200, _json_body({"ok": True, "sent": sent}))

    # -- terminal
    def terminal_settings(self):
        """The `remote` settings as the file says now: read again when the file changed, a missing or unreadable
        file means the defaults (the terminal off)."""
        path = self._watch().path
        try:
            st = os.stat(path)
            stamp = (st.st_ino, st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = None
        with self._remote_lock:
            if self._remote is None or stamp != self._remote_stamp:
                self._remote, self._remote_stamp = Settings(path).remote(), stamp
            return self._remote

    def terminal_enabled(self):
        """True when the user asked for the terminal (--terminal, or remote.terminal in settings.json, read at
        every call so deleting the key ends it at once) and the server is not read-only."""
        return not self.readonly and (self.terminal_flag or self.terminal_settings()["terminal"])

    def terminal_tick(self):
        """Expiry and cleanup, on every terminal request and every few seconds: the shells go when the terminal
        is off, and the ones nobody looks at, that got no keystroke for too long or whose token is gone."""
        if self.terminal_enabled():
            cfg = self.terminal_settings()
            self.terminals.set_ttls(cfg["terminal_detached_minutes"] * 60.0, cfg["terminal_idle_minutes"] * 60.0)
        else:
            self.terminals.close_all("disabled")
        self.terminals.sweep(self.tokens.fingerprint())

    def start_janitor(self, every=5.0):
        def run():
            while not self._stop.wait(every):
                try:
                    self.terminal_tick()
                except Exception as exc:  # noqa: BLE001 - the janitor never dies of one bad round
                    self.log(f"terminal sweep failed: {type(exc).__name__}")
        threading.Thread(target=run, name="terminal-janitor", daemon=True).start()

    def _terminal_gate(self):
        if self.readonly:
            raise HttpError("readonly", "the terminal is off: this server is read-only")
        if not self.terminal_enabled():
            raise HttpError("terminal_disabled", "the terminal is off on this machine: it needs remote.terminal in settings.json or --terminal")

    @staticmethod
    def _term_error(exc):
        code = {"limit": "conflict", "gone": "conflict", "busy": "terminal_busy", "spawn": "mux_failed"}.get(exc.code, "internal")
        return HttpError(code, sanitize_line(exc.message, 120))

    def _term_limits(self):
        return {"input": T.MAX_INPUT, "cols": list(T.COLS_RANGE), "rows": list(T.ROWS_RANGE), "max": self.terminals.max_sessions,
                "detached": int(self.terminals.detached_ttl), "idle": int(self.terminals.idle_ttl)}

    def _terminal(self, req, key, token, path, query):
        """/api/terminals: list (GET), open (POST), and per session out (GET), in, resize, close (POST). The token,
        the Host and (for POST) the origin checks have passed; the terminal must be on and the server not read-only."""
        m = re.fullmatch(r"/api/terminals(?:/([^/]+)/(out|in|resize|close))?", path)
        if not m:
            raise HttpError("not_found", "not found")
        ident, action = m.groups()
        if ident is None:
            action = "list" if req.method == "GET" else "open"
        elif (req.method == "GET") != (action == "out"):
            raise HttpError("method_not_allowed", "method not allowed")
        body = self._post_body(req) if req.method == "POST" else None
        self.terminal_tick()
        self._terminal_gate()
        owner = _fingerprint(_token_digest(token))
        if action == "list":
            return Response(200, _json_body({"ok": True, "sessions": self.terminals.listing(owner), "limits": self._term_limits()}))
        if action == "open":
            return self._term_open(key, owner, body)
        session = self.terminals.get(ident, owner)
        if session is None:
            raise HttpError("not_found", "no such terminal")
        try:
            if action == "out":
                return self._term_out(req, session, urllib.parse.parse_qs(query))
            if action == "in":
                return self._term_in(session, body)
            if action == "resize":
                return self._term_resize(session, body)
            self._fields(body, ())
            self.terminals.close(session.id, owner, "client")
            return Response(200, _json_body({"ok": True}))
        except T.TermError as exc:
            raise self._term_error(exc) from None

    def _term_open(self, key, owner, body):
        self._fields(body, ("cols", "rows"))
        try:
            cols, rows = T.valid_size(body.get("cols", T.DEFAULT_SIZE[0]), body.get("rows", T.DEFAULT_SIZE[1]))
        except ValueError as exc:
            raise HttpError("bad_request", str(exc)) from None
        wait = self.term_open.take(key)
        if wait > 0:
            raise HttpError("rate_limited", "too many terminals opened: wait a few seconds",
                            {"Retry-After": str(max(math.ceil(wait), 1))})
        try:
            session = self.terminals.open(cols, rows, owner)
        except T.TermError as exc:
            raise self._term_error(exc) from None
        return Response(200, _json_body({"ok": True, **session.info(), "limits": self._term_limits()}))

    def _term_out(self, req, session, q):
        pos = self._int_param(q, "from", 0, 0, 1 << 62)
        read = session.read(pos, self._wait_param(q), stop=self._stop, alive=req.alive)
        if read is None:
            raise ClientGone()
        return Response(200, _json_body({**T.chunk_json(read), "in": session.in_seq, "cols": session.cols, "rows": session.rows}))

    def _term_in(self, session, body):
        self._fields(body, ("seq", "data"))
        try:
            seq, raw = T.parse_input(body)
        except ValueError as exc:
            raise HttpError("bad_request", str(exc)) from None
        status, expected = session.send(seq, raw)
        if status == "gap":
            raise HttpError("conflict", "input out of sequence: carry the number given", None, **{"in": expected})
        return Response(200, _json_body({"ok": True, "in": expected, "dup": status == "dup"}))

    def _term_resize(self, session, body):
        self._fields(body, ("cols", "rows", "redraw"))
        redraw = body.get("redraw", False)
        if not isinstance(redraw, bool):
            raise HttpError("bad_request", "redraw must be true or false")
        try:
            cols, rows = T.valid_size(body.get("cols"), body.get("rows"))
        except ValueError as exc:
            raise HttpError("bad_request", str(exc)) from None
        session.resize(cols, rows, redraw)
        return Response(200, _json_body({"ok": True}))


# ---------------------------------------------------------------- HTTP server

UNIQUE_HEADERS = ("host", "content-length", "content-type", "authorization", "origin")


class DeadlineReader:
    """A buffered reader over the socket whose reads stop at an absolute `deadline` (a clock reading),
    so a client that dribbles bytes cannot hold a thread past it: each recv waits at most the time
    that is left, never longer than `per_recv`."""

    def __init__(self, sock, clock=time.monotonic, per_recv=SOCKET_TIMEOUT):
        self.sock, self.clock, self.per_recv = sock, clock, per_recv
        self.buf = bytearray()
        self.deadline = None
        self.closed = False

    def _fill(self):
        left = self.per_recv if self.deadline is None else self.deadline - self.clock()
        if left <= 0:
            raise TimeoutError("request deadline passed")
        self.sock.settimeout(min(self.per_recv, left))
        chunk = self.sock.recv(65536)
        self.buf += chunk
        return bool(chunk)

    def _take(self, n):
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def readline(self, limit=-1):
        while True:
            i = self.buf.find(b"\n")
            if i >= 0 and (limit < 0 or i + 1 <= limit):
                return self._take(i + 1)
            if limit >= 0 and len(self.buf) >= limit:
                return self._take(limit)
            if not self._fill():
                return self._take(len(self.buf))

    def read(self, n=-1):
        while n < 0 or len(self.buf) < n:
            if not self._fill():
                break
        return self._take(len(self.buf) if n < 0 else n)

    def close(self):
        self.closed = True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = SOCKET_TIMEOUT
    app = None  # set by make_server on a subclass
    clock = staticmethod(time.monotonic)

    def setup(self):
        super().setup()
        self.rfile.close()
        self.rfile = DeadlineReader(self.connection, self.clock, self.timeout)
        self._claims = set()
        peer = self.client_address[0]
        # A loopback peer is usually the proxy in front of many clients: it is counted per client
        # key once the headers say who that is. Any other peer is counted at once.
        self._refused = not is_loopback(peer) and not self._claim(peer)

    def finish(self):
        for key in self._claims:
            self.server.release(key)
        super().finish()

    def _claim(self, key):
        if key in self._claims:
            return True
        if not self.server.claim(key):
            return False
        self._claims.add(key)
        return True

    def handle(self):
        if self._refused:
            self.close_connection = True
            return
        super().handle()

    def handle_one_request(self):
        self.rfile.deadline = self.clock() + HEADER_DEADLINE
        super().handle_one_request()

    def version_string(self):
        return "hivegrid-remote"

    def log_message(self, fmt, *args):
        try:
            self.app.log(sanitize_line(fmt % args, 200))
        except Exception:  # noqa: BLE001 - logging never breaks a request
            pass

    def log_request(self, code="-", size="-"):
        method = sanitize_line(getattr(self, "command", "") or "", 16)
        path = sanitize_line(getattr(self, "path", "").split("?", 1)[0], 120)
        if path.startswith("/api/terminals/"):
            # A terminal's poll and keystroke requests would drown the log, and their paths hold session ids:
            # only the ones that failed are logged, without the id (opening and closing log themselves).
            if str(code) == "200":
                return
            path = "/api/terminals/*"
        self.app.log(f"{method} {path} {code}")

    def send_error(self, code, message=None, explain=None):
        self._send(self.app.error_response(code, message or "bad request"))

    def _peer_open(self):
        """False once the client has closed its end of the connection."""
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            return not readable or bool(self.connection.recv(1, socket.MSG_PEEK))
        except (OSError, ValueError):
            return False

    def _send(self, resp):
        try:
            self._write(resp)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True               # the client left: nothing to tell it

    def _write(self, resp):
        self.connection.settimeout(self.timeout)   # the reader leaves it at whatever time was left
        close = resp.close or self.close_connection
        self.send_response(resp.status)
        headers = dict(SECURITY_HEADERS)
        headers.update(resp.headers)
        for k, v in headers.items():
            self.send_header(k, v)
        if resp.status != 304:
            self.send_header("Content-Type", resp.content_type)
            self.send_header("Content-Length", str(len(resp.body)))
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if self.command != "HEAD" and resp.status != 304:
            self.wfile.write(resp.body)

    def _serve(self):
        self.rfile.deadline = self.clock() + BODY_DEADLINE
        names = [k.lower() for k in self.headers.keys()]
        req = Request(self.command, self.path, {k.lower(): v for k, v in self.headers.items()},
                      self.client_address[0], self.rfile.read,
                      duplicate=any(names.count(h) > 1 for h in UNIQUE_HEADERS), alive=self._peer_open)
        timed_out = False
        if not self._claim(self.app._client_key(req)):
            resp = Response(429, _json_body({"ok": False, "error": "rate_limited", "message": "too many connections"}),
                            headers={"Retry-After": "1"}, close=True)
        else:
            try:
                resp = self.app.dispatch(req)
            except ClientGone:                  # the client left during a long poll: nothing to answer
                self.close_connection = True
                return
            except TimeoutError:
                timed_out = True
                resp = Response(400, _json_body({"ok": False, "error": "bad_request", "message": "request timed out"}), close=True)
            except Exception as exc:  # noqa: BLE001 - never leak internals to the client
                self.app.log(f"internal error: {type(exc).__name__}")
                resp = Response(500, _json_body({"ok": False, "error": "internal", "message": "internal error"}), close=True)
        self._send(resp)
        if resp.close and not timed_out and not req.body_read and self.headers.get("Content-Length") not in (None, "0"):
            self._drain()

    def _drain(self):
        """Read what the client is still sending (bounded in bytes and time), so closing does not
        reset the connection before it has read our error."""
        end = self.clock() + DRAIN_DEADLINE
        try:
            self.connection.shutdown(socket.SHUT_WR)
            left = 2 * MAX_BODY
            while left > 0:
                remaining = end - self.clock()
                if remaining <= 0:
                    break
                self.connection.settimeout(min(0.5, remaining))
                chunk = self.connection.recv(min(left, 8192))
                if not chunk:
                    break
                left -= len(chunk)
        except OSError:
            pass

    do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = _serve


class RemoteServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16
    allow_reuse_address = True

    def __init__(self, addr, handler, cap=CONN_CAP, peer_cap=PEER_CAP):
        if ":" in addr[0]:
            self.address_family = socket.AF_INET6
        self._slots = threading.BoundedSemaphore(cap)
        self.peer_cap = peer_cap
        self._conns = {}
        self._conns_lock = threading.Lock()
        super().__init__(addr, handler)

    def claim(self, key):
        """Count one more connection of client `key`; False when it already has `peer_cap`."""
        with self._conns_lock:
            n = self._conns.get(key, 0)
            if n >= self.peer_cap:
                return False
            self._conns[key] = n + 1
            return True

    def release(self, key):
        with self._conns_lock:
            n = self._conns.get(key, 0) - 1
            if n > 0:
                self._conns[key] = n
            else:
                self._conns.pop(key, None)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def make_server(addr, app, **kw):
    """A server for `app` on `addr` = (host, port); port 0 picks one (see server.server_address)."""
    handler = type("BoundHandler", (Handler,), {"app": app})
    return RemoteServer(addr, handler, **kw)


# ---------------------------------------------------------------- CLI

def _foreground():
    with _SLOTS:
        return A.foreground_by_tty()


def _env_flag(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def parse_port(text):
    """A TCP port 1..65535 from text; ValueError with a readable message otherwise."""
    try:
        port = int(str(text).strip(), 10)
    except ValueError:
        raise ValueError(f"{text!r} is not a port number (1-65535)") from None
    if not 1 <= port <= 65535:
        raise ValueError(f"port {port} is outside 1-65535")
    return port


def _port_arg(text):
    try:
        return parse_port(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def resolve_port(args, environ=None):
    """--port, else HIVEGRID_REMOTE_PORT, else the default; ValueError when the environment value is bad."""
    if args.port is not None:
        return args.port
    environ = os.environ if environ is None else environ
    value = environ.get("HIVEGRID_REMOTE_PORT")
    if not value or not value.strip():
        return DEFAULT_PORT
    try:
        return parse_port(value)
    except ValueError as exc:
        raise ValueError(f"HIVEGRID_REMOTE_PORT: {exc}") from None


def allowed_hosts(args, environ=None):
    """Host names the server answers to besides loopback and *.ts.net: --allow-host, the comma
    separated HIVEGRID_REMOTE_ALLOW_HOSTS, and the address it is bound to when that is a tailnet IP."""
    environ = os.environ if environ is None else environ
    names = list(args.allow_host) + environ.get("HIVEGRID_REMOTE_ALLOW_HOSTS", "").split(",")
    bound = bound_host(args.bind)
    if bound and not is_loopback(args.bind):
        names.append(bound)
    out = []
    for n in names:
        n = n.strip().lower()
        if n and n not in out:
            out.append(n)
    return out


def build_parser():
    p = argparse.ArgumentParser(prog="hivegrid-remote", description="Control Hivegrid agents from a phone over Tailscale.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the server")
    s.add_argument("--bind", default=os.environ.get("HIVEGRID_REMOTE_BIND", DEFAULT_BIND))
    s.add_argument("--port", type=_port_arg, default=None, help=f"default {DEFAULT_PORT}, or HIVEGRID_REMOTE_PORT")
    s.add_argument("--token-file")
    s.add_argument("--read-only", action="store_true", default=_env_flag("HIVEGRID_REMOTE_READ_ONLY"))
    s.add_argument("--terminal", action="store_true", default=_env_flag("HIVEGRID_REMOTE_TERMINAL"),
                   help="allow a login shell on the phone page (or set remote.terminal in settings.json); never with --read-only")
    s.add_argument("--allow-host", action="append", default=[], metavar="HOST")
    s.add_argument("--socket", default=os.environ.get("HIVEGRID_REMOTE_SOCKET"))
    s.add_argument("--wezterm", default=os.environ.get("HIVEGRID_REMOTE_WEZTERM"))
    s.add_argument("--allow-any-bind", action="store_true")
    t = sub.add_parser("token", help="create, show or rotate the access token")
    t.add_argument("--token-file")
    g = t.add_mutually_exclusive_group()
    g.add_argument("--rotate", action="store_true", help="replace the token (the old one stops working at once)")
    g.add_argument("--show", action="store_true", help="print the existing token")
    return p


def cmd_token(args):
    path = args.token_file or default_token_path()
    if args.show:
        try:
            print(read_token(path))
        except TokenError as exc:
            print(f"hivegrid-remote: {exc}", file=sys.stderr)
            return 1
        return 0
    if not args.rotate and os.path.lexists(path):
        print(f"hivegrid-remote: token file exists: {path}\n"
              "  --show prints it, --rotate replaces it", file=sys.stderr)
        return 0
    token = generate_token()
    try:
        write_token(path, token)
    except OSError as exc:
        print(f"hivegrid-remote: cannot write {path}: {exc.strerror}", file=sys.stderr)
        return 1
    print(token)
    return 0


def exe_for_socket(sock, proc_exe=_proc_exe, which=shutil.which):
    """The `wezterm` that matches the GUI behind `gui-sock-<pid>` (its sibling of wezterm-gui:
    another build's `cli` refuses to talk), else the one on PATH."""
    m = re.fullmatch(r"gui-sock-(\d+)", os.path.basename(sock))
    gui = proc_exe(int(m.group(1))) if m else None
    if gui:
        gui = gui.removesuffix(" (deleted)")
        if os.path.basename(gui).startswith("wezterm-gui"):
            sibling = os.path.join(os.path.dirname(gui), "wezterm")
            if os.path.exists(sibling):
                return sibling
    return which("wezterm")


def cli_exe(path):
    """`path` as the binary that has the `cli` subcommand: a wezterm-gui is swapped for its sibling
    `wezterm` (a Hivegrid pane exports WEZTERM_EXECUTABLE=.../wezterm-gui, which has no `cli`)."""
    if path and os.path.basename(path) == "wezterm-gui":
        sibling = os.path.join(os.path.dirname(path), "wezterm")
        if os.path.exists(sibling):
            return sibling
    return path


def _locator(args, discover=discover_gui_socket, exe_for=exe_for_socket):
    if not (args.socket or args.wezterm):
        return discover

    def locate():
        if args.socket:
            exe = cli_exe(args.wezterm) or exe_for(args.socket)
            return (args.socket, exe) if exe else None
        found = discover()
        return (found[0], cli_exe(args.wezterm)) if found else None
    return locate


def cmd_serve(args):
    try:
        args.port = resolve_port(args)
    except ValueError as exc:
        print(f"hivegrid-remote: {exc}", file=sys.stderr)
        return 2
    try:
        check_bind(args.bind, args.allow_any_bind)
    except ValueError as exc:
        print(f"hivegrid-remote: {exc}", file=sys.stderr)
        return 2
    store = TokenStore(args.token_file or default_token_path())
    try:
        store.load()
    except TokenError as exc:
        print(f"hivegrid-remote: {exc}", file=sys.stderr)
        return 1
    problem = A.state_dir_problem()
    if problem:
        print(f"hivegrid-remote: the agent state directory is not trusted: {problem}", file=sys.stderr)
        return 1
    hosts = allowed_hosts(args)
    mux = Mux(locate=_locator(args), fg=_foreground)
    app = App(mux, store, readonly=args.read_only, allow_hosts=hosts, terminal=args.terminal)
    try:
        server = make_server((args.bind, args.port), app)
    except OSError as exc:
        print(f"hivegrid-remote: cannot listen on {args.bind}:{args.port}: {exc.strerror}", file=sys.stderr)
        return 1
    host, port = server.server_address[:2]
    note = " (read-only)" if args.read_only else (" (terminal on)" if app.terminal_enabled() else "")
    print(f"hivegrid-remote: listening on http://{f'[{host}]' if ':' in host else host}:{port}/" + note,
          file=sys.stderr, flush=True)
    if args.read_only and args.terminal:
        print("hivegrid-remote: --terminal is ignored: the server is read-only", file=sys.stderr, flush=True)
    app.start_janitor()
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        server.server_close()
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    return cmd_token(args) if args.cmd == "token" else cmd_serve(args)


if __name__ == "__main__":
    sys.exit(main())
