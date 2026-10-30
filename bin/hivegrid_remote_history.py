"""hivegrid-remote: quitting a session and the history of past sessions, free of HTTP.

Quitting. `SessionQuitter` ends the omp of one agent the way the user would: Ctrl+D on an empty prompt, sent
only while omp is the program in front in that pane, then it waits for omp to exit and the pane to show a
shell. It never kills a process and never closes a pane: the shell that is left behind reads `exited`, which
the phone's list leaves out (App._listed); the window and its dock are the desktop's and stay as they are.
See docs/remote.md, "Quitting a session".

History. `History` lists the omp session files of the machine (`~/.omp/agent/sessions` and
`~/.omp/profiles/<p>/agent/sessions`, top-level files of each project directory only: a subagent's transcript
below a session's artifact directory is no session of its own), newest first, for `GET /api/history`, and
turns an opaque id from that listing back into a file for `POST /api/history/<id>/revive`. The phone never
names a path or a command: the id is a digest of the real path, the server walks the same directories again
to resolve it, re-validates the file with `hivegrid_remote_data.session_path_ok` and builds the command line
from the file's own profile directory. Everything read out of a session file is untrusted: titles, messages
and directories are sanitized with the server's `san_line` before they leave.

Everything that touches the world is injectable (clock, sleep, process table, mux), so the tests need no
window, no real home and no omp."""
import hashlib
import json
import os
import re
import threading
import time

import hivegrid_agents as A
import hivegrid_remote_data as D
import hivegrid_ui as U

# ---------------------------------------------------------------- quitting a session

QUIT_STATES = ("limit", "done", "idle")      # like an account switch: the phone never interrupts a turn to quit
QUIT_BURST, QUIT_EVERY = 4, 2.0               # quitting: 4 at once, then one every 2 seconds


class Refused(Exception):
    """The request cannot be done; nothing was touched (or, for a quit, omp stays). `code` is an HttpError code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def quit_block(agent):
    """Why `agent` cannot be quit from the phone (a sentence), None when it can."""
    if agent.state == "exited":
        return "agent has already exited"
    if agent.state not in QUIT_STATES:
        return "wait until the agent is idle or has stopped on its limit (interrupt it first)"
    if agent.source == "scrape":
        return "only omp sessions can be quit from the phone"
    if not isinstance(agent.pid, int) or isinstance(agent.pid, bool) or agent.pid <= 0:
        return "this agent's process is unknown: quit it in its pane"
    return None


class SessionQuitter:
    """Ctrl+D into the pane of an agent, then wait for omp to exit and the pane to show a shell.

    `mux` offers `run`, `act`, `send_keys` and `invalidate` (the App's Mux). `alive(pid)` and `foreground()`
    ({tty: (pid, comm, etimes, args)}) are the process table. One quit per pane at a time: a second Ctrl+D
    sent after omp has gone would reach the shell and close its pane."""

    def __init__(self, mux, clock=time.monotonic, sleep=time.sleep, alive=A._pid_alive, foreground=A.foreground_by_tty):
        self.mux, self.clock, self.sleep, self.alive, self.foreground = mux, clock, sleep, alive, foreground
        self._lock = threading.Lock()
        self._busy = set()

    def _row(self, pane):
        return next((p for p in (A._list_panes(self.mux.run) or []) if p["pane_id"] == pane), None)

    def _front(self, row):
        return self.foreground().get(A._tty(row), (None, "", 0, ""))[1] if row is not None else ""

    def _wait_exit(self, pid):
        end = self.clock() + D.EXIT_WAIT
        while self.alive(pid):
            if self.clock() >= end or self.sleep(D.EXIT_POLL):
                return False
        return True

    def _settled(self, pane):
        """True once the pane is gone or shows a shell, looking for up to D.SHELL_WAIT seconds (False: it shows something else)."""
        end = self.clock() + D.SHELL_WAIT
        while True:
            row = self._row(pane)
            if row is None or self._front(row) in A.SHELLS:
                return True
            if self.clock() >= end or self.sleep(D.EXIT_POLL):
                return False

    def quit(self, agent):
        """Quits the omp of `agent` (a listed agent that `quit_block` allows). Returns once omp has exited and the
        pane shows a shell (or is gone), also when that took longer than D.SHELL_WAIT. Raises Refused (omp keeps
        running) when it was not asked to, or stayed (unsent text in its prompt)."""
        why = quit_block(agent)
        if why:
            raise Refused("agent_not_ready", why)
        with self._lock:
            if agent.pane in self._busy:
                raise Refused("conflict", "this agent is already being quit")
            self._busy.add(agent.pane)
        try:
            if not self.alive(agent.pid) or self._front(self._row(agent.pane)) != "omp":
                raise Refused("agent_not_ready", "omp is no longer running in that pane: nothing was sent")
            try:
                self.mux.act(lambda: self.mux.send_keys(agent.pane, [D.CTRL_D]))
            except Exception:  # noqa: BLE001 - the mux says why in its own words; nothing was sent
                raise Refused("agent_not_ready", "could not reach the agent's pane") from None
            if not self._wait_exit(agent.pid):
                raise Refused("agent_not_ready", "omp did not quit (unsent text in its prompt?): the session keeps running")
            self._settled(agent.pane)
        finally:
            with self._lock:
                self._busy.discard(agent.pane)
            self.mux.invalidate()


# ---------------------------------------------------------------- the history of sessions

PAGE_DEFAULT, PAGE_MAX = 30, 50
SCAN_MAX = 20000                  # session files considered at all (the newest of the first SCAN_MAX found)
SCAN_TTL = 3.0                    # a walk is reused this long (a page-by-page reader walks once)
HEAD_BYTES = 65536                # how much of a file's start is read for its title, directory and first message
HEAD_LINE = 32768
TITLE_MAX, PREVIEW_MAX, DIR_MAX = 120, 160, 200
SID = re.compile(r"[0-9a-f]{24}")
_PRIVATE = re.compile("[\ue000-\uf8ff\U000f0000-\U000ffffd\U00100000-\U0010fffd]")   # icon-font glyphs omp prefixes titles with


def session_id(path):
    """The opaque handle of a session file in `GET /api/history`: a digest of its real path."""
    return hashlib.sha256(path.encode("utf-8", "surrogateescape")).hexdigest()[:24]


def account_labels(accounts):
    """{omp profile or None: account label} from the settings' accounts: the label of the account paired with
    each profile, and for the default configuration (None) the `main` account when it has no profile, else the
    first account without one."""
    out, bare = {}, []
    for a in D.visible_accounts(accounts):
        profile = a.get("profile")
        if isinstance(profile, str) and A.PROFILE_NAME.fullmatch(profile):
            out.setdefault(profile, a["label"])
        else:
            bare.append(a["label"])
    if bare:
        out[None] = "main" if "main" in bare else bare[0]
    return out


def profile_of(real, home=None):
    """The omp profile a session file (a real path that passed `session_path_ok`) belongs to: its name, or None
    for omp's default configuration. ValueError for a profile name that is not a plain name."""
    rel = os.path.relpath(real, os.path.realpath(os.path.join(D._home(home), ".omp")))
    parts = rel.split(os.sep)
    if parts[0] != "profiles":
        return None
    if not A.PROFILE_NAME.fullmatch(parts[1]):
        raise ValueError("bad profile name")
    return parts[1]


def _clean(san_line, text, limit):
    return san_line(_PRIVATE.sub("", text), limit).strip()


def _first_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"].strip():
                return block["text"]
    return ""


def read_head(path):
    """(title, first user message, directory, complete) read from the first HEAD_BYTES of a session file, raw: any of
    the first three None when the file does not say; `complete` is true when the whole file was read. None when the
    file cannot be read."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(HEAD_BYTES)
    except OSError:
        return None
    lines = data.split(b"\n")
    complete = len(data) < HEAD_BYTES
    if not complete:
        lines = lines[:-1]                      # the last line is cut
    title = first = cwd = None
    for raw in lines[:400]:
        if len(raw) > HEAD_LINE:
            continue
        try:
            entry = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        if not isinstance(entry, dict):
            continue
        kind = entry.get("type")
        if kind == "title" and title is None and isinstance(entry.get("title"), str):
            title = entry["title"]
        elif kind == "session" and cwd is None and isinstance(entry.get("cwd"), str):
            cwd = entry["cwd"]
        elif kind == "message" and first is None:
            m = entry.get("message")
            if isinstance(m, dict) and m.get("role") == "user":
                first = _first_text(m.get("content")) or None
        if title is not None and cwd is not None and first is not None:
            break
    return title, first, cwd, complete


class History:
    """The session files of the machine, newest first. `san_line(text, limit)` is the server's display sanitizer."""

    def __init__(self, san_line, home=None, clock=time.monotonic, uid=None):
        self.san_line, self.home, self.clock = san_line, home, clock
        self.uid = os.getuid() if uid is None else uid
        self._lock = threading.Lock()
        self._scan = None
        self._scan_at = 0.0

    def _roots(self, omp):
        roots = [(os.path.join(omp, "agent", "sessions"), None)]
        try:
            names = sorted(e.name for e in os.scandir(os.path.join(omp, "profiles"))
                           if e.is_dir(follow_symlinks=False) and A.PROFILE_NAME.fullmatch(e.name))
        except OSError:
            names = []
        roots += [(os.path.join(omp, "profiles", n, "agent", "sessions"), n) for n in names]
        return roots

    def _walk(self):
        omp = os.path.realpath(os.path.join(D._home(self.home), ".omp"))
        found = []
        for root, profile in self._roots(omp):
            if len(found) >= SCAN_MAX:
                break
            if os.path.realpath(root) != root:
                continue                         # a sessions directory that is a link to somewhere else
            try:
                projects = [e.path for e in os.scandir(root) if e.is_dir(follow_symlinks=False)]
            except OSError:
                continue
            for project in projects:
                try:
                    with os.scandir(project) as it:
                        for e in it:
                            if not e.name.endswith(".jsonl") or not e.is_file(follow_symlinks=False):
                                continue
                            st = e.stat(follow_symlinks=False)
                            if st.st_uid == self.uid:
                                found.append((st.st_mtime, e.path, profile))
                except OSError:
                    continue
                if len(found) >= SCAN_MAX:
                    break
        found.sort(key=lambda f: (f[0], f[1]), reverse=True)
        return found[:SCAN_MAX]

    def files(self, fresh=False):
        """[(mtime, real path, profile)] newest first, cached for SCAN_TTL seconds unless `fresh`."""
        with self._lock:
            if fresh or self._scan is None or self.clock() - self._scan_at >= SCAN_TTL:
                self._scan, self._scan_at = self._walk(), self.clock()
            return self._scan

    def find(self, sid):
        """(mtime, real path, profile) of the session whose id is `sid`, None when there is none now."""
        if not isinstance(sid, str) or not SID.fullmatch(sid):
            return None
        return next((f for f in self.files(True) if session_id(f[1]) == sid), None)

    def listing(self, offset, limit, live, labels, readonly=False):
        """The page of `GET /api/history`. `live` is {real path: agent label} of the sessions an agent has open now,
        `labels` is `account_labels`. Each item: id, title, preview, dir, account, ts, live, agent, can_revive, why."""
        files = self.files()
        items = []
        for mtime, path, profile in files[offset:offset + limit]:
            real = D.session_path_ok(path, self.home, self.uid)
            if real is None:
                continue
            head = read_head(real)
            if head is None:
                continue
            title, first, cwd, complete = head
            if complete and not first:
                continue                         # nothing was ever said: there is nothing to resume
            agent = live.get(real)
            if readonly:
                why = "this server is read-only"
            elif agent is not None:
                why = f"open in agent {agent}"
            elif not D._plain_path(real):
                why = "its path cannot be typed safely"
            elif not D._plain_path(cwd) or not os.path.isdir(cwd):
                why = "its directory no longer exists"
            else:
                why = None
            shown_title = _clean(self.san_line, title, TITLE_MAX) if title else ""
            preview = _clean(self.san_line, first, PREVIEW_MAX) if first else ""
            items.append({
                "id": session_id(real), "title": shown_title or preview or "(no messages yet)",
                "preview": preview if shown_title and preview != shown_title else "",
                "dir": self.san_line(U.home_path(cwd), DIR_MAX) if isinstance(cwd, str) else "",
                "account": self.san_line(labels.get(profile) or profile or "default", 40), "ts": mtime,
                "live": agent is not None, "agent": self.san_line(agent, 20) if agent is not None else None,
                "can_revive": why is None, "why": why})
        more = offset + limit < len(files)
        return {"items": items, "offset": offset, "limit": limit, "total": len(files), "more": more,
                "capped": len(files) >= SCAN_MAX}

    def revive_plan(self, sid, live):
        """(real path, profile, directory) to start `omp [--profile=<p>] --resume <path>` for the session `sid`,
        re-validated now. Refused (Refused) when the id is unknown, the file is no longer a session file of ours, the
        path cannot be typed safely, the directory is gone or an agent has the session open."""
        found = self.find(sid)
        real = D.session_path_ok(found[1], self.home, self.uid) if found else None
        if real is None:
            raise Refused("not_found", "no such session")
        try:
            profile = profile_of(real, self.home)
        except ValueError:
            raise Refused("bad_request", "this session belongs to a profile with an unusable name") from None
        if not D._plain_path(real):
            raise Refused("bad_request", "this session's path cannot be typed safely")
        if live.get(real) is not None:
            raise Refused("conflict", f"this session is open in agent {live[real]}")
        head = read_head(real)
        cwd = head[2] if head else None
        if not D._plain_path(cwd) or not os.path.isdir(cwd):
            raise Refused("agent_not_ready", "the session's directory no longer exists")
        return real, profile, cwd
