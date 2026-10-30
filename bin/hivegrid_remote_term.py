"""Terminal sessions of hivegrid-remote: a login shell on a pty per session, as the user that runs the server.

A session keeps the last RING_BYTES of the shell's output in a ring with absolute byte positions, so a
client that lost its connection (or reloaded the page) asks for the output from where it stopped, or
from the start of the ring, and the same shell is still there. Input arrives as numbered requests: a
request that was applied but whose answer got lost is recognised when the client sends it again and is
not typed twice. The number of sessions is bounded, and a session that nobody looks at (detached) or
that gets no keystroke (idle) for a bounded time is closed, like one whose token was rotated.

Nothing here knows HTTP, tokens or settings: hivegrid_remote.py decides who may open a terminal and
when; this module only runs shells. It never logs what a shell prints or what is typed into it, and
never a path. Everything that touches the world is injectable (clock, spawn function, environment,
shell), so the tests run without the user's shell or home.
"""
import base64
import collections
import fcntl
import os
import pwd
import re
import secrets
import select
import shutil
import signal
import struct
import subprocess
import sys
import termios
import threading
import time

MAX_SESSIONS = 4              # shells open at once, over all devices
RING_BYTES = 256 * 1024       # output kept per session for a client that reconnects
CHUNK = 32 * 1024             # most bytes one output answer carries
MAX_INPUT = 4096              # characters of one input request
DETACHED_TTL = 15 * 60.0      # seconds without any client looking: the session is closed
IDLE_TTL = 60 * 60.0          # seconds without a keystroke, even while a client looks: closed
EXITED_TTL = 120.0            # a shell that ended stays listed this long, so a reload can show how it ended
WRITE_DEADLINE = 3.0          # a shell that does not read its input for this long makes the write fail
GRACE = 0.5                   # SIGHUP, then SIGKILL after this many seconds when the shell is still there
COLS_RANGE = (10, 400)
ROWS_RANGE = (3, 200)
DEFAULT_SIZE = (80, 24)
TERM_NAME = "xterm-256color"
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{16}")

# What the shell inherits from the server: its place in the user's session (display, bus, agent) and
# its locale, nothing else. The server's own settings (HIVEGRID_REMOTE_*), the pane variables of the
# window it was started from (WEZTERM_*) and anything a service manager put in its environment stay out.
ENV_KEEP = ("PATH", "LANG", "LANGUAGE", "TZ", "XDG_RUNTIME_DIR", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP",
            "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "SSH_AUTH_SOCK")
_LC = re.compile(r"LC_[A-Z_]+")
DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin"
_IUTF8 = getattr(termios, "IUTF8", 0o40000)
_ALT = re.compile(rb"\x1b\[\?(?:47|1047|1049)([hl])")

# Run in the child between fork and exec by a fresh interpreter (no fork of this threaded server): make
# the pty the controlling terminal (the child is a session leader, started by Popen), then become the
# login shell. A leading dash in argv[0] is how every shell is told it is a login shell.
_LAUNCH = ("import fcntl, os, sys, termios\n"
           "try:\n"
           "    fcntl.ioctl(0, termios.TIOCSCTTY, 0)\n"
           "except OSError:\n"
           "    pass\n"
           "os.execv(sys.argv[1], [sys.argv[2]])\n")

Read = collections.namedtuple("Read", "start next end data reset alive exit")


class TermError(Exception):
    """`code`: limit (too many sessions), gone (the session or its shell has ended), busy (the shell
    does not read its input), spawn (no shell could be started)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


# ---------------------------------------------------------------- request validation (the framing)

def valid_size(cols, rows):
    """(cols, rows) when both are ints within the limits, else ValueError."""
    for name, v, (low, high) in (("cols", cols, COLS_RANGE), ("rows", rows, ROWS_RANGE)):
        if type(v) is not int or not low <= v <= high:
            raise ValueError(f"{name} must be a whole number from {low} to {high}")
    return cols, rows


def parse_input(body):
    """(seq, bytes) of an input request `{"seq": n, "data": "..."}`; ValueError with a reason otherwise.
    Every character is allowed (a terminal needs ESC and Ctrl-C), the length is not unbounded."""
    seq, data = body.get("seq"), body.get("data")
    if type(seq) is not int or not 0 <= seq < 1 << 53:
        raise ValueError("seq must be a non-negative whole number")
    if not isinstance(data, str) or not data:
        raise ValueError("data must be a non-empty string")
    if len(data) > MAX_INPUT:
        raise ValueError(f"data is longer than {MAX_INPUT} characters")
    try:
        raw = data.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("data is not valid text") from None
    return seq, raw


def chunk_json(read):
    """The answer to an output request: the bytes as base64 (a UTF-8 character or an escape sequence may
    end in the next answer), where they start and end, and how the shell is doing."""
    return {"ok": True, "from": read.start, "next": read.next, "end": read.end,
            "data": base64.b64encode(read.data).decode("ascii"), "reset": read.reset,
            "alive": read.alive, "exit": read.exit}


# ---------------------------------------------------------------- the shell and its environment

def _executable(path):
    return isinstance(path, str) and os.path.isabs(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def _passwd():
    try:
        return pwd.getpwuid(os.getuid())
    except KeyError:
        return None


def pick_shell(environ=None, pw_shell=None):
    """The user's $SHELL when it is an executable file, else the shell of the account, else /bin/sh."""
    environ = os.environ if environ is None else environ
    if pw_shell is None:
        entry = _passwd()
        pw_shell = entry.pw_shell if entry else None
    for cand in (environ.get("SHELL"), pw_shell, "/bin/sh"):
        if _executable(cand):
            return cand
    raise TermError("spawn", "no usable shell")


def home_dir(environ=None):
    """$HOME when it is a directory, else the account's home, else /."""
    environ = os.environ if environ is None else environ
    entry = _passwd()
    for cand in (environ.get("HOME"), entry.pw_dir if entry else None):
        if isinstance(cand, str) and os.path.isabs(cand) and os.path.isdir(cand):
            return cand
    return "/"


def session_env(environ, shell, home, user=None):
    """The environment of a shell: ENV_KEEP and LC_* from `environ`, HOME, USER, LOGNAME, SHELL and TERM
    set here; a UTF-8 locale when the server has none (a service often has no LANG)."""
    env = {k: environ[k] for k in ENV_KEEP if isinstance(environ.get(k), str)}
    env.update({k: v for k, v in environ.items() if _LC.fullmatch(k) and isinstance(v, str)})
    env.setdefault("PATH", DEFAULT_PATH)
    if "LANG" not in env and "LC_ALL" not in env:
        env["LANG"] = "C.UTF-8"
    name = user or (environ.get("USER") if isinstance(environ.get("USER"), str) else None)
    if name is None:
        entry = _passwd()
        name = entry.pw_name if entry else None
    env.update(HOME=home, SHELL=shell, TERM=TERM_NAME, COLORTERM="truecolor", TERM_PROGRAM="hivegrid-remote")
    if name:
        env.update(USER=name, LOGNAME=name)
    return env


def _set_winsize(fd, cols, rows):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def spawn_shell(shell, cols, rows, home, env):
    """(Popen, master fd) of `shell` as a login shell on a new pty of cols x rows, in `home`, with `env`
    as its whole environment. The master end is non-blocking."""
    python = sys.executable or shutil.which("python3")
    if not python:
        raise TermError("spawn", "no Python to start the shell with")
    master, slave = os.openpty()
    try:
        _set_winsize(slave, cols, rows)
        attrs = termios.tcgetattr(slave)
        attrs[0] |= _IUTF8        # the line editor erases whole UTF-8 characters
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        proc = subprocess.Popen([python, "-I", "-c", _LAUNCH, shell, "-" + os.path.basename(shell)],
                                stdin=slave, stdout=slave, stderr=slave, cwd=home, env=env, close_fds=True,
                                start_new_session=True)
    except BaseException:
        os.close(master)
        raise
    finally:
        os.close(slave)
    os.set_blocking(master, False)
    return proc, master


# ---------------------------------------------------------------- one session

class Session:
    """A shell on a pty with its output ring. Times are readings of `clock`. `owner` is a fingerprint of
    the token that opened it: only that token reaches it, and a rotation ends it."""

    def __init__(self, sid, n, owner, proc, fd, cols, rows, clock=time.monotonic, ring_bytes=RING_BYTES,
                 on_exit=None):
        self.id, self.n, self.owner = sid, n, owner
        self.proc, self.fd, self.cols, self.rows = proc, fd, cols, rows
        self.clock, self.ring_cap, self.on_exit = clock, ring_bytes, on_exit
        self.started = self.last_seen = self.last_input = clock()
        self.cond = threading.Condition()
        self._io = threading.Lock()            # the master fd: writes and its final close
        self._send = threading.Lock()          # one input request at a time
        self.ring = bytearray()
        self.base = 0                          # absolute position of ring[0]
        self.in_seq = 0                        # the next input request number
        self.alt = False                       # the shell's program shows the alternate screen
        self._scan = b""
        self.exit = None                       # the shell's exit status once it ended
        self.exit_at = None
        self.closed = None                     # why close() was called
        self.thread = threading.Thread(target=self._pump, name=f"term-{n}", daemon=True)
        self.thread.start()

    @property
    def end(self):
        return self.base + len(self.ring)

    # -- output
    def _feed(self, data):
        with self.cond:
            self.ring += data
            over = len(self.ring) - self.ring_cap
            if over > 0:
                del self.ring[:over]
                self.base += over
            hits = _ALT.findall(self._scan + data)
            if hits:
                self.alt = hits[-1] == b"h"
            self._scan = (self._scan + data)[-7:]
            self.cond.notify_all()

    def _drain(self, fd):
        while True:
            try:
                data = os.read(fd, 65536)
            except OSError:
                return
            if not data:
                return
            self._feed(data)

    def _pump(self):
        fd = self.fd
        try:
            while True:
                try:
                    ready, _, _ = select.select([fd], [], [], 0.25)
                except (OSError, ValueError):
                    break
                if ready:
                    try:
                        data = os.read(fd, 65536)
                    except BlockingIOError:
                        continue
                    except OSError:      # EIO: no process holds the other end any more
                        break
                    if not data:
                        break
                    self._feed(data)
                elif self.proc.poll() is not None:
                    self._drain(fd)
                    break
        finally:
            self._finish()

    def _finish(self):
        with self._io:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1
        try:
            rc = self.proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            self._signal(signal.SIGKILL)
            rc = self.proc.wait()
        status = 128 - rc if rc < 0 else rc
        with self.cond:
            self.exit, self.exit_at = status, self.clock()
            self.cond.notify_all()
        if self.on_exit:
            self.on_exit(self)

    def _signal(self, sig):
        """`sig` to the shell's process group, unless the shell is gone (its pid may be someone else's by now)."""
        if self.proc.poll() is not None:
            return
        try:
            os.killpg(self.proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def read(self, pos, wait=0.0, max_bytes=CHUNK, stop=None, alive=None):
        """The output from absolute position `pos`. Nothing there yet: up to `wait` seconds for some (a
        shell that ended answers at once). A position the ring no longer holds (or one beyond its end) is
        answered from the start of the ring with reset=True: the client starts its screen afresh.
        None when `alive()` says the client left while it waited. Real time, not the injected clock."""
        end_at = time.monotonic() + wait
        with self.cond:
            self.last_seen = self.clock()
            while True:
                end = self.end
                reset = not self.base <= pos <= end
                start = self.base if reset else pos
                if start < end or self.exit is not None or self.closed is not None or wait <= 0:
                    break
                left = end_at - time.monotonic()
                if left <= 0 or (stop is not None and stop.is_set()):
                    break
                self.cond.wait(min(left, 0.25))
                if alive is not None and not alive():
                    return None
            data = bytes(self.ring[start - self.base:start - self.base + max_bytes])
            self.last_seen = self.clock()
            done = self.exit is not None or self.closed is not None
            return Read(start, start + len(data), end, data, reset, not done, self.exit)

    # -- input
    def _write(self, data):
        view = memoryview(data)
        done = 0
        deadline = time.monotonic() + WRITE_DEADLINE
        with self._io:
            if self.fd < 0:
                raise TermError("gone", "the shell has ended")
            while done < len(data):
                try:
                    done += os.write(self.fd, view[done:])
                    continue
                except BlockingIOError:
                    pass
                except OSError:
                    raise TermError("gone", "the shell has ended") from None
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TermError("busy", "the shell does not read its input")
                select.select([], [self.fd], [], min(left, 0.25))

    def send(self, seq, data):
        """Types `data` unless request number `seq` was applied already: ("ok"|"dup"|"gap", the number the
        next request must carry). A "dup" is a retry of a request whose answer got lost, a "gap" a client
        that is behind or ahead and must start from the number it is told."""
        with self._send:
            with self.cond:
                if self.exit is not None or self.closed is not None:
                    raise TermError("gone", "the shell has ended")
                if seq != self.in_seq:
                    return ("dup" if seq < self.in_seq else "gap"), self.in_seq
            try:
                self._write(data)
            finally:
                with self.cond:
                    self.in_seq += 1        # even a failed write counts: a retry must not type twice
                    self.last_input = self.last_seen = self.clock()
            return "ok", self.in_seq

    def resize(self, cols, rows, redraw=False):
        """The size of the pty (the shell and its program get SIGWINCH). With `redraw`, a full-screen
        program (alternate screen) is made to repaint even when the size did not change: it is
        resized one column wider for a moment first."""
        with self._io:
            if self.fd < 0:
                raise TermError("gone", "the shell has ended")
            if redraw and self.alt and (cols, rows) == (self.cols, self.rows):
                _set_winsize(self.fd, cols + 1, rows)
                time.sleep(0.05)
            _set_winsize(self.fd, cols, rows)
        with self.cond:
            self.cols, self.rows = cols, rows
            self.last_seen = self.clock()

    # -- end
    def close(self, reason):
        """Hang up the shell (SIGHUP to its group and the pty), kill it when it ignores that. The call
        returns at once; True the first time."""
        with self.cond:
            first = self.closed is None
            if first:
                self.closed = reason
            self.cond.notify_all()
        if first:
            self._signal(signal.SIGHUP)
            threading.Thread(target=self._escalate, daemon=True).start()
        return first

    def _escalate(self):
        self.thread.join(GRACE)
        if self.thread.is_alive():
            self._signal(signal.SIGKILL)

    def join(self, timeout):
        self.thread.join(timeout)

    def info(self):
        now = self.clock()
        with self.cond:
            return {"id": self.id, "n": self.n, "cols": self.cols, "rows": self.rows,
                    "alive": self.exit is None and self.closed is None, "exit": self.exit,
                    "age": int(now - self.started), "idle": int(now - self.last_input),
                    "in": self.in_seq, "end": self.end}


# ---------------------------------------------------------------- all sessions

class Manager:
    """The open sessions: opening within the limit, finding a session for its owner, expiring."""

    def __init__(self, clock=time.monotonic, environ=None, shell=None, home=None, max_sessions=MAX_SESSIONS,
                 detached_ttl=DETACHED_TTL, idle_ttl=IDLE_TTL, ring_bytes=RING_BYTES, log=None,
                 spawn=spawn_shell):
        self.clock, self.environ, self.shell, self.home = clock, environ, shell, home
        self.max_sessions, self.detached_ttl, self.idle_ttl = max_sessions, detached_ttl, idle_ttl
        self.ring_bytes, self.log, self.spawn = ring_bytes, log or (lambda msg: None), spawn
        self._lock = threading.Lock()
        self._sessions = {}
        self._count = 0

    def set_ttls(self, detached_ttl, idle_ttl):
        self.detached_ttl, self.idle_ttl = detached_ttl, idle_ttl

    def open(self, cols, rows, owner):
        """A new session for `owner`; TermError "limit" when MAX_SESSIONS shells are open, "spawn" when
        the shell cannot be started."""
        cols, rows = valid_size(cols, rows)
        environ = os.environ if self.environ is None else self.environ
        with self._lock:
            if sum(1 for s in self._sessions.values() if s.closed is None and s.exit is None) >= self.max_sessions:
                raise TermError("limit", f"{self.max_sessions} terminals are open: end one first")
            shell = self.shell or pick_shell(environ)
            home = self.home or home_dir(environ)
            try:
                proc, fd = self.spawn(shell, cols, rows, home, session_env(environ, shell, home))
            except TermError:
                raise
            except OSError as exc:
                raise TermError("spawn", f"could not start a shell: {exc.strerror or 'error'}") from None
            self._count += 1
            sid = secrets.token_urlsafe(12)
            session = Session(sid, self._count, owner, proc, fd, cols, rows, self.clock, self.ring_bytes, self._exited)
            self._sessions[sid] = session
        self.log(f"write terminal open session={session.n}")
        return session

    def _exited(self, session):
        if session.closed is None:
            self.log(f"terminal ended session={session.n} status={session.exit}")

    def get(self, sid, owner):
        """The live session `sid` of `owner`, or None (unknown, someone else's, or being closed)."""
        if not isinstance(sid, str) or not SESSION_ID.fullmatch(sid):
            return None
        with self._lock:
            s = self._sessions.get(sid)
        return s if s is not None and s.owner == owner and s.closed is None else None

    def listing(self, owner):
        with self._lock:
            mine = [s for s in self._sessions.values() if s.owner == owner and s.closed is None]
        return [s.info() for s in sorted(mine, key=lambda s: s.n)]

    def close(self, sid, owner, reason="client"):
        s = self.get(sid, owner)
        if s is not None and s.close(reason):
            self.log(f"write terminal close session={s.n} reason={reason}")
            return True
        return False

    def close_all(self, reason):
        with self._lock:
            sessions = list(self._sessions.values())
        for s in sessions:
            if s.close(reason):
                self.log(f"write terminal close session={s.n} reason={reason}")

    def sweep(self, valid_owner=None):
        """Closes the sessions nobody looks at, that got no keystroke for too long, or whose token is no
        longer the valid one (`valid_owner` is the current fingerprint; None = no token is valid), and
        forgets the ones that ended."""
        now = self.clock()
        with self._lock:
            sessions = list(self._sessions.values())
        for s in sessions:
            if s.closed is None and s.exit is None:
                if s.owner != valid_owner:
                    reason = "token"
                elif now - s.last_seen > self.detached_ttl:
                    reason = "detached"
                elif now - s.last_input > self.idle_ttl:
                    reason = "idle"
                else:
                    continue
                if s.close(reason):
                    self.log(f"write terminal close session={s.n} reason={reason}")
            elif s.exit is not None and (s.closed is not None or now - s.exit_at > EXITED_TTL):
                with self._lock:
                    self._sessions.pop(s.id, None)

    def shutdown(self, reason="shutdown"):
        """Ends every shell and waits briefly for them, so none outlives the server."""
        self.close_all(reason)
        with self._lock:
            sessions = list(self._sessions.values())
        for s in sessions:
            s.join(GRACE + 1.5)
