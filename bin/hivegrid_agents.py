"""Hivegrid agent core: discover omp (and other agent) panes of the WezTerm mux, merge them with
the state files written by the omp extension, and act on them (peek, prompt, spawn, close, jump).

Pure stdlib, no terminal I/O and no UI imports. Every mux call goes through an injectable
`run(*args) -> stdout | None` so tests never touch a real terminal.

State files: <state_dir()>/<pane>.json (written by the omp extension, see collect()).
Command files: <state_dir()>/<pane>.cmd.<unique>.json (written here, consumed by the extension).
Own bookkeeping (survives the short-lived overlay): acks.json {pane: done-since} (also written by
config/agentstatus.lua when you look at a done pane), seen.json {pane: {state, since, agent}} for
title-derived states and for remembering that an agent ran in a pane (a shell there is `exited`).
"""
import datetime
import json
import math
import mmap
import os
import re
import shlex
import shutil
import stat
import subprocess
import threading
import time
import urllib.parse
from dataclasses import dataclass

STATES = ("working", "busy", "done", "idle", "attention", "limit", "exited")
STALE_SECS = 20.0          # ext heartbeat older than this (pid alive) => trust the title instead
CELL_ASPECT = 2.1         # cell height / cell width on screen
PLAN_CHOICES = ("execute", "compact", "keep", "refine", "save")
SHELLS = {"bash", "zsh", "fish", "sh", "dash"}
AGENT_PROCS = {"omp", "claude", "codex", "gemini", "aider", "opencode"}
# Chrome = panes that are never part of the agent grid: the sidebar bar (hivegrid-bar), its
# views (yazi, hivegrid-git, lazygit; config/sidebar.lua), the overview dock (hivegrid-overview;
# config/overview.lua) and the panels.
CHROME_MARKERS = ("hivegrid-overview", "hivegrid-bar", "hivegrid-git", "hivegrid-panel")
CHROME_PROGS = {"yazi", "lazygit"}

# Mirrors config/agentstatus.lua TITLE_WORKING / SPIN.
_TITLE_WORKING = set(":-\\|/") | set("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏") | set("○◔◑◕●⠁⠂⠄⠠⠐⠈")
_TITLE_SEP = re.compile(r"^π (\S+)\s*(.*)$", re.S)
_TITLE_COLON = re.compile(r"^π:\s*(.*)$", re.S)


# ---------------------------------------------------------------- mux plumbing

def find_wezterm():
    """Nearest wezterm-gui ancestor's sibling `wezterm`, else WEZTERM_EXECUTABLE, else PATH
    (copy of hivegrid_settings_core.find_wezterm: the `cli` of another build refuses to talk)."""
    pid = os.getpid()
    for _ in range(40):
        try:
            with open(f"/proc/{pid}/stat") as f:
                stat = f.read()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
            exe = os.readlink(f"/proc/{ppid}/exe")
        except (OSError, ValueError, IndexError):
            break
        name = os.path.basename(exe)
        if name.startswith("wezterm-gui") or name == "wezterm":
            sibling = os.path.join(os.path.dirname(exe), "wezterm")
            return sibling if os.path.exists(sibling) else exe
        if ppid <= 1:
            break
        pid = ppid
    return os.environ.get("WEZTERM_EXECUTABLE") or shutil.which("wezterm")


def wezterm_cli(*args):
    """stdout of `wezterm cli <args>`, None on failure or when there is no mux."""
    exe = find_wezterm()
    if not exe or "WEZTERM_UNIX_SOCKET" not in os.environ:
        return None
    try:
        r = subprocess.run([exe, "cli", *args], capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def foreground_by_tty():
    """{ 'pts/3': (pid, comm, etimes, args) } for the foreground process of each terminal."""
    try:
        r = subprocess.run(["ps", "-eo", "pid=,tty=,stat=,etimes=,comm=,args="],
                           capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    out = {}
    for line in r.stdout.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 5 or "+" not in parts[2]:
            continue
        try:
            pid, tty, etimes = int(parts[0]), parts[1], int(parts[3])
        except ValueError:
            continue
        comm = parts[4]
        args = parts[5] if len(parts) > 5 else ""
        if tty not in out or pid < out[tty][0]:
            out[tty] = (pid, comm, etimes, args)
    return out


# ---------------------------------------------------------------- files

def state_dir():
    d = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "hivegrid-agents")
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
    except OSError:
        pass
    return d


def state_dir_problem(d=None):
    """Why the state directory cannot be trusted (it holds the files that drive prompts and model
    switches), else None: it must be a real directory of the current user that others cannot write to."""
    d = d or state_dir()
    try:
        st = os.lstat(d)
    except OSError as exc:
        return f"cannot read {d}: {exc.strerror}"
    if not stat.S_ISDIR(st.st_mode):
        return f"{d} is not a directory"
    if st.st_uid != os.getuid():
        return f"{d} belongs to another user"
    if st.st_mode & 0o022:
        return f"{d} can be written by others (chmod 700 it)"
    return None


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path, obj):
    """Atomic write (tmp + rename). The tmp name never matches <pane>.json or <pane>.cmd.*.json."""
    d, base = os.path.split(path)
    tmp = os.path.join(d, f".{base}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def _read_map(name):
    data = _read_json(os.path.join(state_dir(), name))
    return data if isinstance(data, dict) else {}


def _num(v, default=0.0):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _pid_alive(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as f:
            if f.read().rsplit(")", 1)[1].split()[0] == "Z":
                return False
    except (OSError, IndexError):
        pass
    return True


# ---------------------------------------------------------------- model

@dataclass
class Agent:
    pane: int
    tab: int
    window: int
    cwd: str
    title: str
    state: str                    # working|busy|done|idle|attention|limit|exited
    reason: str = ""
    since: float = 0.0
    source: str = "title"         # "ext"|"title"|"scrape"|"shell" (an agent ran here and quit)
    pid: int = None
    session_id: str = None
    pending: dict = None
    active: bool = False          # focused pane of its tab
    profile: str = None
    error: str = None
    num: int = 0                  # reading-order number in its tab (the dock's and picker's digits)
    tab_index: int = 1            # position of its tab in the window, from 1 (the picker's "tab N")
    usage: "Usage" = None         # tokens and cost omp reported (the state file's `usage`), None when it reported none
    session_file: str = None      # the state file's `sessionFile` (unchecked: the reader confines it)
    model: dict = None            # {"id": "provider/id", "name", "provider"} the extension published, None from an older one
    thinking: str = None
    context: dict = None          # {"tokens", "window", "pct"} when omp reported them
    limit: dict = None            # {"hit_at", "reset_at"} (epoch s, reset_at may be None) while state is "limit"


def agent_label(agent, agents):
    """What the dock and picker show for an agent: its number, prefixed with its tab ("2·3") while
    the agents sit in several tabs. HivePage.number (hivegrid_hive.py) applies the same rule; it
    stays a copy because the page's core is injectable and test fakes do not provide this."""
    n = str(agent.num or agent.pane)
    return f"{agent.tab_index}·{n}" if len({a.tab for a in agents}) > 1 else n


# ---------------------------------------------------------------- usage (tokens and cost)

COST_KINDS = ("reported", "estimated", "mixed")


@dataclass
class Usage:
    """What omp reports about a session's spend (the extension copies it into the state file's
    `usage`). A field omp did not report is None, never 0. `cost_kind` says what `cost` is:
    `reported` (a gateway's own charge), `estimated` (omp priced the tokens from its catalog, not a
    bill on a subscription) or `mixed`. Local compute (CPU, RAM) is not part of this."""
    tokens: float = None          # omp's total token count (cached prompts count on every request)
    cost: float = None            # USD
    cost_kind: str = None


def _amount(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0 else None


def parse_usage(doc):
    """The `usage` object of a state file -> Usage, or None when it holds no reported number.
    Tokens are omp's total, else the sum of the input and output it reported."""
    if not isinstance(doc, dict):
        return None
    tokens = _amount(doc.get("total"))
    if tokens is None:
        parts = [n for n in (_amount(doc.get("input")), _amount(doc.get("output"))) if n is not None]
        tokens = sum(parts) if parts else None
    cost = _amount(doc.get("cost"))
    kind = doc.get("costKind") if doc.get("costKind") in COST_KINDS else None
    if cost is not None and kind is None:
        kind = "estimated" if cost else None  # a cost of unknown origin is not presented as a charge
    if tokens is None and cost is None:
        return None
    return Usage(tokens, cost, kind if cost else None)


def format_tokens(n):
    """1234 -> '1.2k', 1_300_000 -> '1.3M'; '—' when not reported."""
    if n is None:
        return "—"
    for unit, size in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= size:
            v = n / size
            return f"{v:.1f}{unit}" if v < 10 else f"{v:.0f}{unit}"
    return str(int(n))


def format_cost(cost, kind=None):
    """USD with two decimals (a cent less than that shows as <$0.01); an estimate or a mix leads
    with `~`; '—' when not reported."""
    if cost is None:
        return "—"
    text = "$0.00" if cost == 0 else "<$0.01" if cost < 0.005 else f"${cost:,.2f}"
    return "~" + text if kind in ("estimated", "mixed") and cost else text


def usage_text(u):
    """`1.3M tok · $4.82` ('~$4.82' when estimated); None when there is nothing to show."""
    if u is None:
        return None
    return " · ".join((f"{format_tokens(u.tokens)} tok", format_cost(u.cost, u.cost_kind)))


def usage_totals(usages):
    """Sum over agents: {'agents': n reporting, 'tokens': sum | None, 'reported': USD of gateway
    charges | None, 'estimated': USD of catalog-priced (or mixed) cost | None}. The two cost
    kinds stay apart; nothing here adds compute."""
    out = {"agents": 0, "tokens": None, "reported": None, "estimated": None}
    for u in usages:
        if u is None:
            continue
        out["agents"] += 1
        if u.tokens is not None:
            out["tokens"] = (out["tokens"] or 0) + u.tokens
        if u.cost is not None:
            key = "reported" if u.cost_kind == "reported" else "estimated"
            out[key] = (out[key] or 0) + u.cost
    return out


def totals_text(t):
    """`1.3M tok · $1.20 + ~$3.62 est`; None when no agent reported anything."""
    if not t["agents"]:
        return None
    costs = []
    if t["reported"] is not None:
        costs.append(format_cost(t["reported"]))
    if t["estimated"] is not None:
        costs.append(format_cost(t["estimated"], "estimated") + (" est" if costs else ""))
    return " · ".join([f"{format_tokens(t['tokens'])} tok"] + ([" + ".join(costs)] if costs else ["—"]))


THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
MODEL_ID = re.compile(r"[^\s/]{1,80}/\S{1,160}")


def _short(v, limit):
    return v[:limit] if isinstance(v, str) and v else None


def parse_model(doc):
    """The state file's `model` -> {"id", "name", "provider"} with bounded strings, else None."""
    if not isinstance(doc, dict):
        return None
    mid, provider = _short(doc.get("id"), 240), _short(doc.get("provider"), 80)
    if not mid or not provider or not MODEL_ID.fullmatch(mid):
        return None
    return {"id": mid, "name": _short(doc.get("name"), 120) or mid.split("/", 1)[1], "provider": provider}


def parse_context(doc):
    """The state file's `context` -> {"tokens", "window", "pct"} of finite non-negative numbers, else None."""
    if not isinstance(doc, dict):
        return None
    tokens, window, pct = _amount(doc.get("tokens")), _amount(doc.get("window")), _amount(doc.get("pct"))
    if tokens is None or window is None or window <= 0:
        return None
    return {"tokens": tokens, "window": window, "pct": pct if pct is not None else round(100 * tokens / window, 1)}


# ---------------------------------------------------------------- usage limit
# The omp extension (ext/hivegrid-agent-state.ts `limitOfEntry`) and this file decide alike, so a
# session file read here and the state file the extension wrote agree. The signal, as seen in real
# omp session files: the last message of the session is an assistant message with
# `stopReason: "error"`, `errorStatus: 429` and an `errorMessage` like
# `429 {"type":"error","error":{"type":"rate_limit_error",...}} retry-after-ms=14523000`; the
# retry-after is the time until the account's 5-hour or weekly window reopens.

LIMIT_MIN_RETRY_S = 60            # a refusal that reopens sooner is a burst limit omp retries itself
LIMIT_MAX_RETRY_S = 400 * 86400
FIVE_HOURS = 5 * 3600
_LIMIT_TEXT = re.compile(r"rate_limit_error|usage[_ ]limit|insufficient_quota", re.I)
_RETRY_MS = re.compile(r"retry-after-ms=(\d{1,12})(?!\d)")  # a longer number is no retry-after: int() of thousands of digits raises


def parse_limit(doc):
    """The state file's `limit` -> {"hit_at", "reset_at"} (epoch seconds, each None when not a finite
    non-negative number), None when it is not an object."""
    if not isinstance(doc, dict):
        return None
    return {"hit_at": _amount(doc.get("hit_at")), "reset_at": _amount(doc.get("reset_at"))}


def limit_of_entry(entry):
    """{"hit_at", "reset_at"} when the session entry `entry` (a parsed JSONL line) is an assistant message
    that ended its turn on the account's usage limit, else None. `reset_at` is None when the error named
    no retry-after. A refusal that reopens within LIMIT_MIN_RETRY_S is not a limit."""
    msg = entry.get("message") if isinstance(entry, dict) and entry.get("type") == "message" else None
    if not isinstance(msg, dict) or msg.get("role") != "assistant" or msg.get("stopReason") != "error":
        return None
    text = msg.get("errorMessage") if isinstance(msg.get("errorMessage"), str) else ""
    if not (msg.get("errorStatus") == 429 or re.match(r"\s*429\b", text)) or not _LIMIT_TEXT.search(text):
        return None
    m = _RETRY_MS.search(text)
    retry = int(m.group(1)) / 1000.0 if m else None
    if retry is not None and retry < LIMIT_MIN_RETRY_S:
        return None
    ms = _amount(msg.get("completedAt"))
    if ms is None:
        ms = _amount(msg.get("timestamp"))
    hit = ms / 1000.0 if ms else None
    if hit is None and isinstance(entry.get("timestamp"), str):
        try:
            hit = datetime.datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).timestamp()
        except ValueError:
            hit = None
    if hit is None:
        return None
    return {"hit_at": round(hit, 3), "reset_at": round(hit + retry, 3) if retry is not None and retry <= LIMIT_MAX_RETRY_S else None}


def limit_window(limit, windows, slack=300.0):
    """Which window of the account a limit hit: "five_hour", "seven_day" or None when it cannot be told.
    `windows` is the account's quota cache ({"five_hour": {"reset_at"}, "seven_day": {...}}): the window
    whose own reset time is the refusal's reset time (within `slack` seconds) is the one; when both or
    neither match (or there is no cache), a reset more than five hours after the refusal can only be the
    weekly window, and anything else stays undecided: a 5-hour limit and a weekly limit that ends soon look alike."""
    reset, hit = (limit or {}).get("reset_at"), (limit or {}).get("hit_at")
    if not isinstance(reset, (int, float)):
        return None
    near = []
    for name in ("five_hour", "seven_day"):
        w = (windows or {}).get(name)
        r = w.get("reset_at") if isinstance(w, dict) else None
        if isinstance(r, (int, float)) and not isinstance(r, bool) and abs(r - reset) <= slack:
            near.append(name)
    if len(near) == 1:
        return near[0]
    if isinstance(hit, (int, float)) and reset - hit > FIVE_HOURS + slack:
        return "seven_day"
    return None


def omp_title(title):
    """omp's title protocol -> (state|None, heading). Mirrors agentstatus.lua `omp_title`."""
    m = _TITLE_SEP.match(title)
    if not m:
        m = _TITLE_COLON.match(title)
        return None, (m.group(1) if m else title)
    sep, rest = m.group(1), m.group(2)
    if sep == ">":
        return "idle", rest
    if sep == "!":
        return "attention", rest
    if sep in _TITLE_WORKING:
        return "working", rest
    return None, rest


def _cwd_path(url):
    return re.sub(r"^file://[^/]*", "", str(url or ""))


def _profile(args):
    m = re.search(r"--profile[= ](\S+)", args or "")
    return m.group(1) if m else None


def is_chrome(comm, args):
    """True for the sidebar bar/views and the overview dock: panes the hive never touches."""
    args = args or ""
    if any(m in args for m in CHROME_MARKERS):
        return True
    return comm in CHROME_PROGS or any(os.path.basename(t) in CHROME_PROGS for t in args.split()[:4])


def is_web(pane):
    """True for a web pane of the Hivegrid fork in a `wezterm cli list` entry. The fork lists a
    boolean `is_web` (the Lua side's user var hivegrid_web); that marker decides when present.
    Without it (a build that predates the marker) only a pane with no terminal (tty_name null)
    whose directory is a non-file URL counts: SSH-domain and mux-client panes have no tty_name
    either but report `file://host/...` from OSC 7, so a `file:` directory never makes a web
    pane here."""
    marker = pane.get("is_web")
    if isinstance(marker, bool):
        return marker
    cwd = str(pane.get("cwd") or "")
    scheme = re.match(r"([a-z][a-z0-9+.-]*):", cwd)
    return not pane.get("tty_name") and scheme is not None and scheme.group(1) != "file"


_web_builds = {}
_web_scans = {}
_web_lock = threading.Lock()


def _scan_web_build(exe):
    found = False
    for path in (os.path.join(os.path.dirname(exe), "wezterm-gui"), exe):
        try:
            with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                found = found or mm.find(b"hivegrid_web") >= 0
        except (OSError, ValueError):
            continue
    return found


def web_build(exe=None):
    """True when the wezterm of this terminal has the web pane: its binaries carry the user var
    name `hivegrid_web`, which only the Hivegrid build with the web feature contains (the way the
    settings panel's About recognises the fork). False when no wezterm is found. Scans the
    binaries (tens of MB) on the first call, so a UI thread uses web_build_cached instead."""
    exe = exe or find_wezterm()
    if not exe:
        return False
    with _web_lock:
        known = _web_builds.get(exe)
    if known is None:
        known = _scan_web_build(exe)
        with _web_lock:
            _web_builds[exe] = known
    return known


def web_build_cached(exe=None):
    """Like web_build, never blocking: the cached answer, or None while a background thread is
    still scanning the binaries (started by the first call). False when no wezterm is found."""
    exe = exe or find_wezterm()
    if not exe:
        return False
    with _web_lock:
        if exe in _web_builds:
            return _web_builds[exe]
        if exe not in _web_scans:
            t = threading.Thread(target=web_build, args=(exe,), daemon=True)
            _web_scans[exe] = t
            t.start()
    return None


def web_label(url):
    """What a list shows for a page: host and path (the path only when it is more than "/"); a
    URL without a host (data:, about:) as itself, cut to 40 characters."""
    url = str(url or "")
    m = re.match(r"[a-z][a-z0-9+.-]*://([^/?#]*)([^?#]*)", url)
    if m and m.group(1):
        return m.group(1) + (m.group(2) if m.group(2) != "/" else "")
    return url if len(url) <= 40 else url[:39] + "…"


_WEB_SCHEMES = {"http", "https", "file", "data"}


def _local_host(host):
    h = host.lower()
    return (h == "localhost" or h.endswith(".localhost") or re.fullmatch(r"\d+\.\d+\.\d+\.\d+", h) is not None
            or (h.startswith("[") and h.endswith("]")))


def normalize_url(text, search=""):
    """(url, None) for what a typed location stands for, else (None, reason). Twin of
    normalize_url in config/web.lua: http, https, file and data URLs and exactly about:blank are kept; local hosts
    ("localhost:3000", "127.0.0.1") get http://, other host names https://; anything else is a
    search when `search` is a template holding %s. Other schemes (javascript:, chrome:, devtools:, about:flags, ...) are refused."""
    text = (text or "").strip()
    if not text:
        return None, "nothing to open"
    spaced = re.search(r"\s", text) is not None
    hostport = (re.match(r"[^/?#:\s]+:\d+(?:$|[/?#])", text) or re.match(r"\[[0-9a-fA-F:.]+\]:\d+(?:$|[/?#])", text))
    scheme = re.match(r"([A-Za-z][A-Za-z0-9+.-]*):", text)
    if scheme and not hostport and not spaced:
        if scheme.group(1).lower() in _WEB_SCHEMES or text.lower() == "about:blank":
            return text, None
        return None, f"cannot open a {scheme.group(1)}: address in a web pane"
    if not spaced:
        authority = re.match(r"[^/?#]+", text)  # none for "/x", "?q", "#a", "//host"
        host = re.sub(r":\d+$", "", authority.group(0) if authority else "")
        if _local_host(host) or (re.fullmatch(r"[A-Za-z0-9.-]+", host) and re.search(r"\.[A-Za-z]{2,}$", host)):
            return ("http://" if _local_host(host) else "https://") + text, None
    if search and "%s" in search:
        return search.replace("%s", urllib.parse.quote(text, safe="")), None
    return None, "not a web address, and no search engine is set (Settings, Web)"


def _list_panes(run):
    text = run("list", "--format", "json")
    if not text:
        return None
    try:
        panes = json.loads(text)
    except ValueError:
        return None
    return [p for p in panes if isinstance(p, dict) and isinstance(p.get("pane_id"), int)] if isinstance(panes, list) else None


def _tty(pane):
    return str(pane.get("tty_name") or "").replace("/dev/", "")


def _numbering(panes, fg):
    """({pane id: reading-order number among the non-chrome panes of its tab},
    {tab id: position of the tab in its window, from 1}). The same digits as the overview dock
    and the pane picker (config/agentstatus.lua, panemap.numbering); `wezterm cli list` lists
    tabs in window order."""
    by_tab, tab_index, per_window = {}, {}, {}
    for p in panes:
        tab, win = int(p.get("tab_id", 0)), int(p.get("window_id", 0))
        if tab not in tab_index:
            per_window[win] = per_window.get(win, 0) + 1
            tab_index[tab] = per_window[win]
        _, comm, _, args = fg.get(_tty(p), (None, "", 0, ""))
        if not is_chrome(comm, args):
            by_tab.setdefault(tab, []).append(p)
    nums = {}
    for tab_panes in by_tab.values():
        for n, p in enumerate(_reading_order(tab_panes), 1):
            nums[p["pane_id"]] = n
    return nums, tab_index


def _same_time(a, b):
    """Two epoch seconds that went through JSON (Python's and WezTerm's encoders) are equal."""
    return isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) < 1e-3


# ---------------------------------------------------------------- collect

def collect(run=wezterm_cli, now=time.time, fg=None):
    """All agent panes of every tab/window of the mux, sorted by pane id; [] when no mux.
    Never raises."""
    try:
        return _collect(run, now, fg)
    except Exception:  # defensive: the hive must never crash on odd mux/state data
        return []


def _collect(run, now, fg):
    panes = _list_panes(run)
    if panes is None:
        return []
    t = now()
    if fg is None:
        fg = foreground_by_tty()
    d = state_dir()
    acks = _read_map("acks.json")
    seen = _read_map("seen.json")
    live_ids = {p["pane_id"] for p in panes}
    seen_dirty = False
    agents = []
    nums, tab_index = _numbering(panes, fg)

    for p in sorted(panes, key=lambda p: p["pane_id"]):
        pid_ = p["pane_id"]
        if is_web(p):
            continue  # a page is no agent (and has no terminal to look up)
        fpid, comm, _etimes, args = fg.get(_tty(p), (None, "", 0, ""))
        if is_chrome(comm, args):
            continue
        prev = seen.get(str(pid_))
        prev = prev if isinstance(prev, dict) else {}
        raw = _read_json(os.path.join(d, f"{pid_}.json"))
        sf = raw if isinstance(raw, dict) and raw.get("state") in STATES + ("exited",) else None
        if sf is not None and comm and comm not in SHELLS and not _pid_alive(sf.get("pid")):
            sf = None  # the agent that wrote it is gone and something else runs here now
        raw_title = str(p.get("title", ""))
        title_state, heading = omp_title(raw_title)
        agent = Agent(
            pane=pid_, tab=int(p.get("tab_id", 0)), window=int(p.get("window_id", 0)),
            cwd=_cwd_path(p.get("cwd")), title=heading or raw_title, state="idle",
            since=t, source="title", active=bool(p.get("is_active")), profile=_profile(args) if comm == "omp" else None,
            num=nums.get(pid_, 0), tab_index=tab_index.get(int(p.get("tab_id", 0)), 1),
        )
        if sf is not None:
            alive = _pid_alive(sf.get("pid"))
            agent.pid = sf["pid"] if isinstance(sf.get("pid"), int) else None
            agent.session_id = sf.get("sessionId") if isinstance(sf.get("sessionId"), str) else None
            agent.profile = sf.get("profile") if isinstance(sf.get("profile"), str) else agent.profile
            if isinstance(sf.get("cwd"), str) and sf["cwd"]:
                agent.cwd = sf["cwd"]
            if isinstance(sf.get("title"), str) and sf["title"]:
                agent.title = sf["title"]
            agent.source = "ext"
            agent.since = _num(sf.get("since"), t)
            agent.reason = sf["reason"] if isinstance(sf.get("reason"), str) else ""
            agent.pending = sf["pending"] if isinstance(sf.get("pending"), dict) else None
            agent.error = sf["error"] if isinstance(sf.get("error"), str) else None
            agent.usage = parse_usage(sf.get("usage"))
            agent.session_file = sf["sessionFile"] if isinstance(sf.get("sessionFile"), str) and sf["sessionFile"] else None
            agent.model = parse_model(sf.get("model"))
            agent.thinking = sf["thinking"] if sf.get("thinking") in THINKING_LEVELS else None
            agent.context = parse_context(sf.get("context"))
            if not alive:
                agent.state = "exited"
                agent.pending = None
            elif t - _num(sf.get("updated")) > STALE_SECS and title_state:
                # heartbeat stopped but the process lives: the title is the fresher truth
                agent.state, agent.source, agent.pending, agent.reason = title_state, "title", None, ""
                agent.since = None
            else:
                agent.state = sf["state"] if sf["state"] != "exited" else "idle"
                if agent.state in ("done", "limit") and _same_time(acks.get(str(pid_)), agent.since):
                    agent.state = "idle"
                if agent.state == "limit":
                    agent.limit = parse_limit(sf.get("limit"))
        elif (title_state or comm == "omp" or raw_title.startswith("π")) and comm not in SHELLS:
            agent.state = title_state or "idle"
            agent.source = "title"
            agent.since = None
            agent.pid = fpid if comm == "omp" else None
        elif comm in AGENT_PROCS:
            agent.state, agent.source, agent.since, agent.pid = "idle", "scrape", None, fpid
        elif comm in SHELLS and prev.get("agent"):
            # the agent quit and left its shell (omp removes its state file on a clean exit)
            agent.state, agent.source, agent.since = "exited", "shell", None
        else:
            continue  # plain shell (no agent ran here) / unrelated program

        if agent.since is None:  # title/scrape-derived: first time this state was seen
            if prev.get("state") == agent.state and isinstance(prev.get("since"), (int, float)):
                agent.since = float(prev["since"])
            else:
                agent.since = t
                seen[str(pid_)] = {"state": agent.state, "since": t, "agent": True}
                seen_dirty = True
        elif not prev.get("agent"):
            seen[str(pid_)] = {"agent": True}
            seen_dirty = True
        agents.append(agent)

    # bookkeeping: drop what belongs to vanished panes (only trust a non-empty listing)
    if live_ids:
        for k in [k for k in seen if k.isdigit() and int(k) not in live_ids]:
            del seen[k]
            seen_dirty = True
        stale_acks = [k for k in acks if k.isdigit() and int(k) not in live_ids]
        for k in stale_acks:
            del acks[k]
        if stale_acks:
            _write_json(os.path.join(d, "acks.json"), acks)
        _drop_stale_files(d, live_ids)
    if seen_dirty:
        _write_json(os.path.join(d, "seen.json"), seen)
    return agents


def _drop_stale_files(d, live_ids):
    try:
        names = os.listdir(d)
    except OSError:
        return
    for name in names:
        m = re.match(r"^(\d+)\.(?:json|models\.json|cmd\..+\.json)$", name)
        if m and int(m.group(1)) not in live_ids:
            try:
                os.unlink(os.path.join(d, name))
            except OSError:
                pass


# ---------------------------------------------------------------- actions

def peek(pane, lines=12, run=wezterm_cli):
    """Last `lines` non-empty screen lines of the pane ([] when unavailable)."""
    text = run("get-text", "--pane-id", str(pane))
    if not text:
        return []
    rows = [l.rstrip() for l in text.splitlines() if l.strip()]
    return rows[-lines:] if lines > 0 else []


def limit_expired(agent, now):
    """True for a limit agent whose reset time is known and no longer ahead of `now`: its account answers again."""
    info = agent.limit if agent.state == "limit" else None
    reset = info.get("reset_at") if isinstance(info, dict) else None
    return isinstance(reset, (int, float)) and not isinstance(reset, bool) and abs(reset) < 1e12 and reset <= now


def ack(agent, now=None):
    """done -> idle (and a limit whose reset has passed -> idle), remembered by (pane, the state's
    `since`) so a later done or limit is un-acked again."""
    if agent.state not in ("done", "idle") and not limit_expired(agent, time.time() if now is None else now):
        return
    acks = _read_map("acks.json")
    acks[str(agent.pane)] = agent.since
    _write_json(os.path.join(state_dir(), "acks.json"), acks)


def send_prompt(agent, text, run=wezterm_cli):
    if not text or not text.strip():
        return False, "empty prompt"
    if agent.state == "attention":
        return False, "agent is waiting for an answer in its pane"
    if agent.state == "exited":
        return False, "agent has exited"
    pane = str(agent.pane)
    # `--` so a prompt starting with '-' is not parsed as an option
    if run("send-text", "--pane-id", pane, "--", text) is None:
        return False, "send-text failed"
    if run("send-text", "--pane-id", pane, "--no-paste", "\r") is None:
        return False, "text pasted but Enter failed"
    ack(agent)
    return True, "sent"


def jump(agent, run=wezterm_cli):
    if run("activate-pane", "--pane-id", str(agent.pane)) is None:
        return False
    ack(agent)
    return True


# ---------------------------------------------------------------- layout (origin tab grid)

def _geo(p):
    size = p.get("size") if isinstance(p.get("size"), dict) else {}
    return (int(p.get("left_col", 0)), int(p.get("top_row", 0)), int(size.get("cols", 0)), int(size.get("rows", 0)))


def _cluster(values, tol=2):
    """value -> cluster index; neighbours within `tol` share one (mirrors config/panemap.lua)."""
    idx, group, prev, k = {}, [], None, -1
    for v in sorted(values):
        if prev is not None and v - prev > tol:
            group = []
        if not group:
            k += 1
            group = [v]
        idx[v] = k
        prev = v
    return idx


def _reading_order(panes):
    """Panes top to bottom, then left to right (rows are clustered, divider/rounding tolerant)."""
    xi = _cluster([_geo(p)[0] for p in panes])
    yi = _cluster([_geo(p)[1] for p in panes])
    return sorted(panes, key=lambda p: (yi[_geo(p)[1]], xi[_geo(p)[0]], _geo(p)[1], _geo(p)[0], p["pane_id"]))


def _grid_panes(tab_panes, fg):
    out = []
    for p in tab_panes:
        _fpid, comm, _e, args = fg.get(_tty(p), (None, "", 0, ""))
        if not is_chrome(comm, args):
            out.append(p)
    return out


def _unzoom(run, tab_panes):
    z = next((p for p in tab_panes if p.get("is_zoomed")), None)
    if z is None:
        return False
    run("zoom-pane", "--pane-id", str(z["pane_id"]), "--unzoom")
    return True


def origin(run=wezterm_cli):
    """(pane, tab, window) the hive was opened over, None when there is no mux or no pane.
    HIVEGRID_ORIGIN_PANE (set from `hivegrid-panel --origin`), WEZTERM_PANE and the focused pane of
    the mux clients count when they name a listed pane (an overlay's own id is not listed);
    else the active pane of the lowest-numbered tab (`wezterm cli list` has no active-tab flag)."""
    panes = _list_panes(run)
    if not panes:
        return None
    byid = {p["pane_id"]: p for p in panes}

    def hit(pane_id):
        p = byid.get(pane_id)
        return (p["pane_id"], int(p.get("tab_id", 0)), int(p.get("window_id", 0))) if p else None

    for name in ("HIVEGRID_ORIGIN_PANE", "WEZTERM_PANE"):
        v = os.environ.get(name, "")
        if v.isdigit() and hit(int(v)):
            return hit(int(v))
    try:
        clients = json.loads(run("list-clients", "--format", "json") or "[]")
    except ValueError:
        clients = []
    for c in clients if isinstance(clients, list) else []:
        fid = c.get("focused_pane_id") if isinstance(c, dict) else None
        if isinstance(fid, int) and hit(fid):
            return hit(fid)
    active = [p for p in panes if p.get("is_active")] or panes
    first = min(active, key=lambda p: (int(p.get("window_id", 0)), int(p.get("tab_id", 0)), p["pane_id"]))
    return hit(first["pane_id"])


PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


ARRANGE_VAR = "hivegrid_arrange_new"


def _agent_command(profile=None, arrange=False, resume=None):
    """The `bash -ic` command line that starts the agent. With a `profile` the command is
    `omp --profile=<p>`; ValueError for a profile that is not a plain name, or when HIVEGRID_AGENT_CMD
    names something else than plain omp (the profile flag is omp's, not any other program's). With
    `arrange` the pane first sets the user var `hivegrid_arrange_new` on itself (OSC 1337, value "1"
    base64-encoded), which asks config/layouts.lua to arrange the grid once this pane exists: nothing
    outside the pane can set a user var. `resume` (an absolute session file path, quoted here) starts
    `omp [--profile=<p>] --resume <file>`; like a profile it needs the plain omp command."""
    cmd = os.environ.get("HIVEGRID_AGENT_CMD") or "omp"
    if profile and not PROFILE_NAME.fullmatch(profile):
        raise ValueError("bad profile name")
    if profile or resume:
        if cmd.strip() != "omp":
            raise ValueError("a profile or a resumed session needs the plain omp command (HIVEGRID_AGENT_CMD is set)")
        cmd = "omp" + (f" --profile={shlex.quote(profile)}" if profile else "") + (f" --resume {shlex.quote(resume)}" if resume else "")
    if arrange:
        cmd = f"printf '\\033]1337;SetUserVar={ARRANGE_VAR}=MQ==\\007'; {cmd}"
    return ["bash", "-ic", f"{cmd}; exec bash"]


def _split_new(run, target_pane, extra):
    """Split target_pane (to the right when wide, else below) with split-pane's `extra` arguments
    (the program to run, or the URL to show); the new pane follows target in reading order.
    Returns the new pane id or None."""
    _x, _y, w, h = _geo(target_pane)
    direction = "--right" if w >= h * CELL_ASPECT else "--bottom"
    out = run("split-pane", "--pane-id", str(target_pane["pane_id"]), direction, *extra)
    m = re.match(r"\s*(\d+)\s*$", out or "")
    return int(m.group(1)) if m else None


def _spawn_in_origin(o, extra, run):
    """Split a new pane off the last grid pane of the origin tab o = (pane, tab, window) with
    split-pane's `extra` arguments; the grid is not re-tiled. Returns the new pane id or None."""
    focus, tab, _window = o
    panes = _list_panes(run)
    in_tab = [p for p in panes or [] if p.get("tab_id") == tab]
    if not in_tab:
        return None
    if _unzoom(run, in_tab):
        in_tab = [p for p in _list_panes(run) or [] if p.get("tab_id") == tab] or in_tab
    grid = _grid_panes(in_tab, foreground_by_tty()) or [p for p in in_tab if p["pane_id"] == focus] or in_tab
    return _split_new(run, _reading_order(grid)[-1], extra)


def spawn_agent(cwd=None, near=None, run=wezterm_cli, profile=None, arrange=False, resume=None):
    """Start the agent command (HIVEGRID_AGENT_CMD, default omp; run as `bash -ic '<cmd>; exec
    bash'`) in a new pane of the origin tab, always: the pane is split off the last grid pane and
    stays where the split puts it (nothing re-tiles the grid), unless `arrange` asks the window to
    arrange the grid once the pane is up (the phone does: nobody is there to press the key). The
    directory is `cwd`, else `near`'s, else the one of the pane the hive was opened over, else $HOME.
    Returns the new pane id or None. `profile` starts `omp --profile=<profile>` (ValueError, before
    any pane exists, when that cannot be done: see `_agent_command`); `resume` adds `--resume <file>`."""
    command = _agent_command(profile, arrange, resume)
    o = origin(run)
    if o is None:
        return None
    focus, _tab, _window = o
    if not cwd and near is not None and near.cwd:
        cwd = near.cwd
    if not cwd:
        here = next((p for p in _list_panes(run) or [] if p["pane_id"] == focus), None)
        cwd = (_cwd_path(here.get("cwd")) if here and not is_web(here) else "") or os.environ.get("HOME", "/")
    return _spawn_in_origin(o, ("--cwd", cwd, "--", *command), run)


def spawn_web(url, profile=None, run=wezterm_cli):
    """Open `url` in a new web pane of the origin tab (`split-pane --url`, the Hivegrid fork's web
    pane), placed like spawn_agent. `profile` names the browser profile (the fork's default when
    None). Returns the new pane id or None."""
    o = origin(run)
    if o is None:
        return None
    extra = ("--url", url) + (("--profile", profile) if profile else ())
    return _spawn_in_origin(o, extra, run)


def gather(origin_tab, run=wezterm_cli):
    """Move every agent of the origin tab's window that sits in another tab into the origin tab,
    each split off the last grid pane (nothing re-tiles the grid). Returns (ok, message)."""
    panes = _list_panes(run)
    in_tab = [p for p in panes or [] if p.get("tab_id") == origin_tab]
    if not in_tab:
        return False, "origin tab not found"
    window = in_tab[0].get("window_id")
    movers = [a for a in collect(run) if a.window == window and a.tab != origin_tab]
    if not movers:
        return True, "no agents in other tabs"
    moved = 0
    for a in sorted(movers, key=lambda a: (a.tab, a.pane)):
        panes = _list_panes(run) or []
        here = [p for p in panes if p.get("tab_id") == origin_tab]
        if _unzoom(run, here):
            here = [p for p in _list_panes(run) or [] if p.get("tab_id") == origin_tab] or here
        grid = _grid_panes(here, foreground_by_tty()) or here
        if not grid:
            break
        target = _reading_order(grid)[-1]
        _x, _y, w, h = _geo(target)
        res = run("split-pane", "--pane-id", str(target["pane_id"]), "--right" if w >= h * CELL_ASPECT else "--bottom",
                  "--move-pane-id", str(a.pane))
        if res is not None:
            moved += 1
    if moved < len(movers):
        return False, f"moved {moved} of {len(movers)} agents"
    return True, f"gathered {moved} agent{'s' if moved != 1 else ''}"


def _close(agent, run, sleep, grace):
    """Graceful close: Ctrl+D, wait until omp is gone or `grace` ran out, then kill-pane."""
    pane = str(agent.pane)
    step = 0.25

    def present():
        panes = _list_panes(run)
        if panes is None:
            return None
        return next((p for p in panes if p["pane_id"] == agent.pane), None)

    if agent.state != "exited":
        run("send-text", "--pane-id", pane, "--no-paste", "\x04")
        waited = 0.0
        while waited < grace:
            sleep(step)
            waited += step
            p = present()
            if p is None:
                return True, "closed"
            _fpid, comm, _e, _a = foreground_by_tty().get(_tty(p), (None, "", 0, ""))
            if comm in SHELLS:
                break  # omp quit, only the keep-alive shell is left
    if run("kill-pane", "--pane-id", pane) is None and present() is not None:
        return False, "kill-pane failed"
    return (True, "killed") if present() is None else (False, "pane still present")


def delete_agent(agent, run=wezterm_cli, sleep=time.sleep, grace=3.0):
    """Close the agent's pane (Ctrl+D, then kill-pane). The other panes keep their places: nothing
    re-tiles the grid. Returns (ok, message)."""
    return _close(agent, run, sleep, grace)


def send_command(agent, msg):
    """Drop a command file for the extension of `agent`'s pane; True when it was written."""
    path = os.path.join(state_dir(), f"{agent.pane}.cmd.{time.time_ns()}-{os.getpid()}.json")
    return _write_json(path, msg)


def read_state(pane):
    """The raw state file of `pane` (a dict) or None."""
    doc = _read_json(os.path.join(state_dir(), f"{int(pane)}.json"))
    return doc if isinstance(doc, dict) else None


def read_models(pane):
    """The models the extension of `pane` can switch to: {"levels": [...], "models": [{"id", "name",
    "provider", "reasoning"}]} from `<pane>.models.json`, validated and bounded; empty lists when
    the file is missing or garbled."""
    doc = _read_json(os.path.join(state_dir(), f"{int(pane)}.models.json"))
    out = {"levels": [], "models": []}
    if not isinstance(doc, dict):
        return out
    levels = doc.get("thinking_levels")
    out["levels"] = [l for l in levels if l in THINKING_LEVELS] if isinstance(levels, list) else []
    seen = set()
    for m in doc.get("models") if isinstance(doc.get("models"), list) else []:
        ref = parse_model(m)
        if ref is None or ref["id"] in seen:
            continue
        seen.add(ref["id"])
        out["models"].append({**ref, "reasoning": bool(m.get("reasoning"))})
        if len(out["models"]) >= 400:
            break
    return out


def answer_plan_review(agent, choice, feedback=None, role=None):
    """Hand a plan-review decision to the extension via a command file."""
    pend = agent.pending
    if not isinstance(pend, dict) or pend.get("kind") != "plan_review":
        return False, "no plan review pending"
    if choice not in PLAN_CHOICES:
        return False, f"unknown choice: {choice}"
    if choice == "refine" and not (feedback and feedback.strip()):
        return False, "refine needs feedback"
    msg = {"type": "plan_review", "reviewId": pend.get("reviewId"), "choice": choice}
    if feedback:
        msg["feedback"] = feedback
    if role:
        msg["role"] = role
    if not send_command(agent, msg):
        return False, "could not write command file"
    return True, "sent"
