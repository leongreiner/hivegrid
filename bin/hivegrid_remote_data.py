"""Data behind hivegrid-remote's richer endpoints, free of HTTP: the omp session file (where it may
live, an incremental JSONL parser that turns it into chat items), the accounts and their quota and
usage, the machine's metrics and the choices for a new session.

Everything read here is untrusted or private: session files are opened only below `~/.omp/**/sessions`
and only when they are regular files of the current user; quota caches are read for numbers only;
nothing returned carries a credential, a token or a directory beyond what the page needs. The
sanitizers of `hivegrid_remote` are passed in (`san_text`, `san_line`) because that module imports
this one."""
import dataclasses
import datetime
import itertools
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import threading
import time

import hivegrid_agents as A
from hivegrid_settings_core import cache_dir, current_entry

# ---------------------------------------------------------------- session file

SESSION_HINT_MAX = 4096
TERMINAL_RECORD_MAX = 4096


def _home(home=None):
    return home or os.environ.get("HOME") or os.path.expanduser("~")


def session_path_ok(path, home=None, uid=None):
    """The real path of `path` when it may be read as a session file, else None: an absolute `.jsonl`
    whose real path is a regular file owned by `uid` (default: us) below `~/.omp/profiles/<p>/agent/sessions/`
    or `~/.omp/agent/sessions/`. A symlink that leads elsewhere fails the confinement; the path the
    caller keeps is the real one, so nothing is re-resolved later."""
    if not isinstance(path, str) or not path.endswith(".jsonl") or "\0" in path or len(path) > SESSION_HINT_MAX:
        return None
    if not os.path.isabs(path):
        return None
    try:
        real = os.path.realpath(path)
        st = os.stat(real)
        root = os.path.realpath(os.path.join(_home(home), ".omp"))
    except OSError:
        return None
    if not real.endswith(".jsonl") or not stat.S_ISREG(st.st_mode) or st.st_uid != (os.getuid() if uid is None else uid):
        return None
    rel = os.path.relpath(real, root)
    if rel.startswith("..") or os.path.isabs(rel):
        return None
    parts = rel.split(os.sep)
    profile_sessions = len(parts) >= 5 and parts[0] == "profiles" and parts[2:4] == ["agent", "sessions"]
    default_sessions = len(parts) >= 3 and parts[:2] == ["agent", "sessions"]
    return real if profile_sessions or default_sessions else None


def _open_jsonl(pid, proc, home, uid):
    found = []
    try:
        entries = os.listdir(f"{proc}/{pid}/fd")
    except OSError:
        return found
    for name in entries[:256]:
        try:
            target = os.readlink(f"{proc}/{pid}/fd/{name}")
        except OSError:
            continue
        if target.endswith(".jsonl"):
            real = session_path_ok(target, home, uid)
            if real:
                found.append(real)
    return found


def _terminal_record(agent, proc, home, uid):
    try:
        tty = os.readlink(f"{proc}/{agent.pid}/fd/0")
    except OSError:
        return None
    m = re.fullmatch(r"/dev/pts/(\d+)", tty)
    if not m:
        return None
    if agent.profile:
        if not A.PROFILE_NAME.fullmatch(agent.profile):
            return None
        base = os.path.join(_home(home), ".omp", "profiles", agent.profile, "agent")
    else:
        base = os.path.join(_home(home), ".omp", "agent")
    try:
        with open(os.path.join(base, "terminal-sessions", f"pts-{m.group(1)}"), "rb") as f:
            lines = f.read(TERMINAL_RECORD_MAX).decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    return session_path_ok(lines[1], home, uid) if len(lines) >= 2 else None


def _is_nested(path):
    """True for a file below a session's artifact directory (a subagent's transcript), not in
    `sessions/<project>/` itself."""
    parts = path.split(os.sep)
    for i in range(1, len(parts)):
        if parts[i] == "sessions" and parts[i - 1] == "agent":
            return len(parts) - i - 1 > 2
    return False


def _pick_held(held, agent, record):
    """The session among several `.jsonl` files an omp process holds open: the one named after the
    state file's session id, then the terminal-sessions record's, then a top-level session file (not a
    subagent's under the session's artifact directory), then the most recently modified."""
    sid = agent.session_id if isinstance(agent.session_id, str) and len(agent.session_id) >= 8 else None

    def rank(path):
        try:
            mtime = os.stat(path).st_mtime_ns
        except OSError:
            mtime = 0
        return (bool(sid and sid in os.path.basename(path)), path == record, not _is_nested(path), mtime)
    return max(held, key=rank)


def find_session_file(agent, home=None, proc="/proc", uid=None):
    """The real path of the session JSONL of `agent`, or None: the state file's `sessionFile`, else the
    file its process holds open (see `_pick_held` when it holds several), else the terminal-sessions
    record of its tty. Every candidate passes `session_path_ok`."""
    real = session_path_ok(agent.session_file, home, uid) if agent.session_file else None
    if real:
        return real
    pid = agent.pid
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    held = _open_jsonl(pid, proc, home, uid)
    record = _terminal_record(agent, proc, home, uid)
    if held:
        return held[0] if len(held) == 1 else _pick_held(held, agent, record)
    return record


# ---------------------------------------------------------------- chat parser

ITEM_TEXT = 12000
TOOL_OUTPUT = 1500
SUMMARY_MAX = 160
BLOCKS_PER_LINE = 16
INITIAL_BYTES = 4 * 1024 * 1024       # what the first parse of a file reads, from its end
WINDOW_MAX = 32 * 1024 * 1024         # how far back a file is ever read
APPEND_MAX = 8 * 1024 * 1024          # a file that grew more than this since the last look is parsed afresh
CACHE_FILES = 8
ITEMS_MAX = 5000                      # parsed items kept per file; the oldest are dropped first
LINE_MAX = 16 * 1024 * 1024           # a session line longer than this is not parsed
FINGERPRINT = 256                     # bytes compared to notice a file that was rewritten under the cache
SUMMARY_KEYS = ("command", "cmd", "path", "file_path", "pattern", "query", "url", "description", "prompt", "intent")
_gens = itertools.count(1)


def _entry_ts(entry, message):
    raw = entry.get("timestamp")
    if isinstance(raw, str):
        try:
            return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    ms = message.get("timestamp") if isinstance(message, dict) else None
    if isinstance(ms, (int, float)) and not isinstance(ms, bool) and ms > 0:
        return float(ms) / 1000.0 if ms > 1e11 else float(ms)
    return None


class _File:
    """Parsed state of one session file: the items of bytes [start, offset), the tool items by call id."""

    def __init__(self, path, st, start, offset):
        self.path, self.ino, self.dev = path, st.st_ino, st.st_dev
        self.start, self.offset = start, offset
        self.items = []
        self.calls = {}
        self.orphans = {}        # toolResults whose call lies before `start`: id -> (status, output)
        self.gen = next(_gens)
        self.version = 0
        self.mtime = st.st_mtime_ns
        self.anchor = None       # (position, bytes): the first bytes of the first parse, still there while the file is
        self.tail = b""          # the last bytes before `offset`
        self.lines = {}          # line offset -> entry id, for the entries that have one
        self.parents = {}        # entry id -> parent id (omp's session file is a tree; the chat follows one branch)
        self.last, self.last_off = None, -1
        self.skip_floor = -1     # lines below this offset may have ancestors in a line that was too large to parse
        self.view = None         # ((offset, start, items), the items on the active branch)


class ChatCache:
    """Session JSONL -> chat items, parsed incrementally. The first look at a file reads its last
    INITIAL_BYTES; later looks read only what was appended; older history is read on demand
    (`window(before=...)`) up to WINDOW_MAX. Item numbers are `byte offset of the line * 16 + block`
    (stable, increasing, needing no scan of the skipped part)."""

    def __init__(self, san_text, san_line, initial=INITIAL_BYTES, window_max=WINDOW_MAX, append_max=APPEND_MAX,
                 max_files=CACHE_FILES, items_max=ITEMS_MAX, line_max=LINE_MAX):
        self.san_text, self.san_line = san_text, san_line
        self.initial, self.window_max, self.append_max, self.max_files = initial, window_max, append_max, max_files
        self.items_max, self.line_max = items_max, max(line_max, initial)
        self._lock = threading.Lock()
        self._files = {}

    # -- parsing
    def _text(self, v, limit=ITEM_TEXT):
        return self.san_text(v[: limit * 2], limit) if isinstance(v, str) else ""

    def _summary(self, args):
        a = args if isinstance(args, dict) else {}
        for key in SUMMARY_KEYS:
            v = a.get(key)
            if isinstance(v, str) and v.strip():
                return self.san_line(v, SUMMARY_MAX)
        return ""

    @staticmethod
    def _item(n, ts, role, kind, text, tool=None):
        return {"n": n, "ts": ts, "role": role, "kind": kind, "text": text, "tool": tool}

    def _blocks(self, base, ts, role, content):
        """Items of a user/assistant message's content."""
        out = []
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        for i, b in enumerate(content[:BLOCKS_PER_LINE] if isinstance(content, list) else []):
            if not isinstance(b, dict):
                continue
            kind, n = b.get("type"), base + i
            if kind == "text":
                text = self._text(b.get("text"))
                if text.strip():
                    out.append(self._item(n, ts, role, "text", text))
            elif kind == "thinking" and role == "assistant":
                text = self._text(b.get("thinking"))
                if text.strip():
                    out.append(self._item(n, ts, role, "thinking", text))
            elif kind == "image":
                out.append(self._item(n, ts, role, "image", "[image]"))
        return out

    def _result(self, f, message, base, ts, out):
        """Merge a toolResult into its call's item (or park it until the call is parsed)."""
        content = message.get("content")
        texts, images = [], []
        for i, b in enumerate(content[:BLOCKS_PER_LINE] if isinstance(content, list) else []):
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
                texts.append(b["text"])
            elif isinstance(b, dict) and b.get("type") == "image":
                images.append(base + i)
        output = self._text("\n".join(texts)[: TOOL_OUTPUT * 2], TOOL_OUTPUT) or None
        status = "error" if message.get("isError") is True else "ok"
        cid = message.get("toolCallId")
        if isinstance(cid, str):
            item = f.calls.get(cid)
            if item is not None:
                item["tool"]["status"], item["tool"]["output"] = status, output
                f.version += 1
            else:
                f.orphans[cid] = (status, output)
                if len(f.orphans) > 512:
                    f.orphans.pop(next(iter(f.orphans)))
        for n in images:
            out.append(self._item(n, ts, "tool", "image", "[image]"))

    def _parse_line(self, f, line, offset, out):
        try:
            entry = json.loads(line)
        except (ValueError, RecursionError):
            return
        if not isinstance(entry, dict):
            return
        eid = entry.get("id")
        if isinstance(eid, str) and eid:
            parent = entry.get("parentId")
            f.lines[offset] = eid
            f.parents[eid] = parent if isinstance(parent, str) and parent else None
            if offset > f.last_off and entry.get("type") == "message":
                f.last_off, f.last = offset, eid
        kind, base = entry.get("type"), offset * BLOCKS_PER_LINE
        if kind == "message" and isinstance(entry.get("message"), dict):
            m = entry["message"]
            ts, role = _entry_ts(entry, m), m.get("role")
            if role == "user":
                out.extend(self._blocks(base, ts, "user", m.get("content")))
            elif role == "assistant":
                limit = A.limit_of_entry(entry)
                content = m.get("content")
                cap = BLOCKS_PER_LINE - 1 if limit else BLOCKS_PER_LINE   # one index of the line stays free for the note
                for i, b in enumerate(content[:cap] if isinstance(content, list) else []):
                    if isinstance(b, dict) and b.get("type") == "toolCall":
                        name = self.san_line(b.get("name"), 60) if isinstance(b.get("name"), str) else "tool"
                        summary = self._summary(b.get("arguments")) or self._summary({"intent": b.get("intent")})
                        tool = {"name": name or "tool", "summary": summary, "status": "running", "output": None}
                        item = self._item(base + i, ts, "tool", "tool", summary, tool)
                        cid = b.get("id")
                        if isinstance(cid, str):
                            if cid in f.orphans:
                                tool["status"], tool["output"] = f.orphans.pop(cid)
                            f.calls[cid] = item
                        out.append(item)
                    else:
                        out.extend(self._blocks(base + i, ts, "assistant", [b]))
                if limit:  # after the content, at the next free index of the line: n stays unique and ascending
                    item = self._item(base + min(len(content) if isinstance(content, list) else 0, cap), ts, "note", "limit", "usage limit reached")
                    item["limit"] = limit
                    out.append(item)
            elif role == "toolResult":
                self._result(f, m, base, ts, out)
        elif kind in ("model_change", "thinking_level_change"):
            value = entry.get("model") if kind == "model_change" else entry.get("thinkingLevel")
            if isinstance(value, str) and value:
                label = "model" if kind == "model_change" else "thinking"
                out.append(self._item(base, _entry_ts(entry, None), "note", "note", self.san_line(f"{label}: {value}", 200)))

    def _parse(self, f, data, start):
        """Items of the complete lines of `data` (which begins at byte `start` of the file)."""
        out, pos = [], 0
        while True:
            nl = data.find(b"\n", pos)
            if nl < 0:
                return out, pos
            if nl > pos:
                self._parse_line(f, data[pos:nl], start + pos, out)
            pos = nl + 1

    # -- file sync
    @staticmethod
    def _read(path, start, end):
        with open(path, "rb") as fh:
            fh.seek(start)
            return fh.read(end - start)

    def _first(self, path, st):
        size = st.st_size
        start = max(0, size - self.initial)
        data = self._read(path, start, size)
        if start > 0:                          # begin at a line start
            nl = data.find(b"\n")
            if nl < 0:                         # inside one long line: `_extend` reaches it
                data, start = b"", size
            else:
                data, start = data[nl + 1:], start + nl + 1
        f = _File(path, st, start, start)
        f.items, used = self._parse(f, data, start)
        f.offset = start + used
        if data:
            f.anchor = (start, data[:FINGERPRINT])
            f.tail = data[:used][-FINGERPRINT:]
        self._trim(f)
        return f

    def _advance(self, f, st):
        """Reads what was appended to `f`; None when the file is not the one that was parsed (it was
        rewritten, even in place at the same size)."""
        if st.st_size == f.offset and st.st_mtime_ns == f.mtime:
            return f
        keep = len(f.tail)
        with open(f.path, "rb") as fh:
            if f.anchor:
                fh.seek(f.anchor[0])
                if fh.read(len(f.anchor[1])) != f.anchor[1]:
                    return None
            fh.seek(f.offset - keep)
            chunk = fh.read(st.st_size - f.offset + keep)
        if chunk[:keep] != f.tail:
            return None
        data = chunk[keep:]
        items, used = self._parse(f, data, f.offset)
        if used:
            f.items.extend(items)
            f.tail = (f.tail + data[:used])[-FINGERPRINT:]
            f.offset += used
            f.version += 1
            self._trim(f)
        f.mtime = st.st_mtime_ns
        return f

    def _sync(self, path):
        try:
            st = os.stat(path)
        except OSError:
            self._files.pop(path, None)
            return None
        f = self._files.get(path)
        if f is not None and (f.ino != st.st_ino or f.dev != st.st_dev or st.st_size < f.offset
                              or st.st_size - f.offset > self.append_max):
            f = None
        try:
            if f is not None:
                f = self._advance(f, st)
            if f is None:
                f = self._first(path, st)
        except OSError:
            self._files.pop(path, None)
            return None
        self._files.pop(path, None)
        self._files[path] = f
        while len(self._files) > self.max_files:
            self._files.pop(next(iter(self._files)))
        return f

    @staticmethod
    def _forget_before(f, start):
        """Drops what only the bytes before `start` needed: tool calls and the tree links."""
        floor = start * BLOCKS_PER_LINE
        f.calls = {k: v for k, v in f.calls.items() if v["n"] >= floor}
        for off in [o for o in f.lines if o < start]:
            del f.lines[off]
        live = set(f.lines.values())
        f.parents = {k: v for k, v in f.parents.items() if k in live}

    def _trim(self, f):
        """Keeps at most `items_max` items: the oldest go, whole lines at a time, and `start` moves up."""
        cut = len(f.items) - self.items_max
        if cut <= 0:
            return
        while cut < len(f.items) and f.items[cut]["n"] // BLOCKS_PER_LINE == f.items[cut - 1]["n"] // BLOCKS_PER_LINE:
            cut += 1
        if cut >= len(f.items):
            return
        f.items = f.items[cut:]
        f.start = f.items[0]["n"] // BLOCKS_PER_LINE
        self._forget_before(f, f.start)

    def _can_extend(self, f):
        # a line holds up to BLOCKS_PER_LINE items: with less room than that, an older line may not fit
        return f.start > 0 and f.offset - f.start < self.window_max and len(f.items) <= self.items_max - BLOCKS_PER_LINE

    def _skip_line(self, f, end):
        """The line that ends at `end` is longer than `line_max`: step over it and leave a note in its
        place. Looks for its start in blocks, at most `window_max` bytes back; past that, older history
        is given up. Always lowers `f.start`."""
        pos = max(0, end - self.line_max)       # [pos, end) holds no line start
        scanned = end - pos
        line_start = None
        while pos > 0 and scanned < self.window_max:
            lo = max(0, pos - self.initial)
            try:
                block = self._read(f.path, lo, pos)
            except OSError:
                return False
            i = block.rfind(b"\n")
            if i >= 0:
                line_start = lo + i + 1
                break
            pos, scanned = lo, scanned + len(block)
        if line_start is None and pos == 0:
            line_start = 0
        at = line_start if line_start is not None else pos
        note = self._item(at * BLOCKS_PER_LINE + 1, None, "note", "note", "[line too large]")   # never n = 0: that is not a valid `before`
        f.items.insert(0, note)
        f.skip_floor = max(f.skip_floor, end)
        f.start = line_start if line_start is not None else 0
        return True

    def _extend(self, f):
        """Reads one chunk of older history; False when there is none (or a cap is reached). A true
        answer always means `f.start` is lower. A chunk without a line start inside doubles up to
        `line_max`; a longer line is skipped (`_skip_line`)."""
        if not self._can_extend(f):
            return False
        end, size = f.start, self.initial
        while True:
            start = max(0, end - size)
            try:
                data = self._read(f.path, start, end)
            except OSError:
                return False
            cut = 0
            if start > 0:
                nl = data.find(b"\n")
                if nl < 0 or start + nl + 1 >= end:      # no line start below `end` in this chunk
                    if size < self.line_max:
                        size = min(size * 2, self.line_max)
                        continue
                    return self._skip_line(f, end)
                cut = nl + 1
            break
        older, _used = self._parse(f, data[cut:], start + cut)
        new_start = start + cut
        room = self.items_max - len(f.items)
        if len(older) > room:                  # keep the newest lines that fit
            k = len(older) - room
            while k < len(older) and older[k]["n"] // BLOCKS_PER_LINE == older[k - 1]["n"] // BLOCKS_PER_LINE:
                k += 1
            older = older[k:]
            if not older:
                return False
            new_start = older[0]["n"] // BLOCKS_PER_LINE
        f.items = older + f.items
        f.start = new_start
        self._forget_before(f, new_start)
        return True

    @staticmethod
    def _below(items, n):
        """How many of `items` (sorted by `n`) have `item["n"] < n`."""
        lo, hi = 0, len(items)
        while lo < hi:
            mid = (lo + hi) // 2
            if items[mid]["n"] < n:
                lo = mid + 1
            else:
                hi = mid
        return lo

    @staticmethod
    def _active(f):
        """The items on the branch that ends at the last message entry: omp's session file is a tree
        (`id`/`parentId`; going back to an earlier message and continuing leaves the abandoned path in
        the file). Items of lines without an id stay, and so does everything before a skipped line
        (its place in the tree is unknown)."""
        if f.last is None:
            return f.items
        key = (f.offset, f.start, len(f.items))
        if f.view is not None and f.view[0] == key:
            return f.view[1]
        chain, cur = set(), f.last
        while cur is not None and cur not in chain and cur in f.parents:
            chain.add(cur)
            cur = f.parents[cur]
        items = []
        for i in f.items:
            off = i["n"] // BLOCKS_PER_LINE
            eid = f.lines.get(off)
            if eid is None or eid in chain or off < f.skip_floor:
                items.append(i)
        f.view = (key, items)
        return items

    # -- queries
    def stamp(self, path):
        """What changes when the chat of `path` does (None when it cannot be read): cheap after the
        first call, it reads only what was appended."""
        with self._lock:
            f = self._sync(path)
            return None if f is None else (f.gen, f.offset, f.version)

    def window(self, path, limit, before=None):
        """(items, has_older, stamp) of `path`: the last `limit` items, or the `limit` items with `n < before`.
        Items are copies. None when the file cannot be read. `has_older` is false when more history
        exists but the cache is full (`items_max`) or the read-back cap (`window_max`) is reached."""
        with self._lock:
            f = self._sync(path)
            if f is None:
                return None
            stamp = (f.gen, f.offset, f.version)
            if before is not None and before <= 0:
                return [], False, stamp
            while True:
                view = self._active(f)
                hi = len(view) if before is None else self._below(view, before)
                prev = f.start
                if hi >= limit or not self._extend(f) or f.start >= prev:
                    break
            view = self._active(f)
            hi = len(view) if before is None else self._below(view, before)
            lo = max(hi - limit, 0)
            rows = [dict(i, tool=dict(i["tool"]) if i["tool"] else None) for i in view[lo:hi]]
            return rows, lo > 0 or self._can_extend(f), stamp

    def mark(self, path):
        """The lowest item number a line appended to `path` from now on can have (None when it cannot be
        read): every item parsed so far is below it."""
        with self._lock:
            f = self._sync(path)
            return None if f is None else f.offset * BLOCKS_PER_LINE

    def user_items(self, path, min_n=None, min_ts=None, limit=200):
        """The newest user text items of `path` on the active branch, oldest first, as {"n", "ts", "text"}
        (None when the file cannot be read): at most `limit`, going back only while `n >= min_n` (when
        given) and while an item's `ts` is not before `min_ts` (when given and the item has one). Reads
        only what was appended since the last look; nothing older than the cache is fetched."""
        with self._lock:
            f = self._sync(path)
            if f is None:
                return None
            out = []
            for i in reversed(self._active(f)):
                if len(out) >= limit or (min_n is not None and i["n"] < min_n):
                    break
                if min_ts is not None and i["ts"] is not None and i["ts"] < min_ts:
                    break
                if i["role"] == "user" and i["kind"] == "text":
                    out.append({"n": i["n"], "ts": i["ts"], "text": i["text"]})
            out.reverse()
            return out


# ---------------------------------------------------------------- accounts

QUOTA_MAX = 65536
LABEL_OK = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,15}")
QUOTA_STATES = ("ok", "expired", "login", "error")


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf") else None


def _window(w):
    if not isinstance(w, dict) or _num(w.get("pct")) is None:
        return None
    return {"pct": _num(w["pct"]), "reset_at": _num(w.get("reset_at"))}


def read_quota(label, now, directory=None):
    """The cached quota entry of an account as {"state", "five_hour", "seven_day", "updated_at"}; state
    "unknown" and no numbers when there is no usable cache. Numbers only: nothing else of the file leaves."""
    out = {"state": "unknown", "five_hour": None, "seven_day": None, "updated_at": None}
    if not LABEL_OK.fullmatch(label or ""):
        return out
    try:
        with open(os.path.join(directory or cache_dir(), f"{label}.json"), "rb") as f:
            entry = json.loads(f.read(QUOTA_MAX))
    except (OSError, ValueError):
        return out
    if not isinstance(entry, dict):
        return out
    entry = current_entry(entry, now)
    state = entry.get("state")
    out["state"] = state if state in QUOTA_STATES else "unknown"
    out["five_hour"], out["seven_day"] = _window(entry.get("five_hour")), _window(entry.get("seven_day"))
    out["updated_at"] = _num(entry.get("updated_at"))
    return out


def visible_accounts(accounts):
    return [a for a in accounts or [] if isinstance(a, dict) and not a.get("hidden") and isinstance(a.get("label"), str)]


def accounts_json(accounts, agents, now, san_line, directory=None):
    """`GET /api/accounts`: the header's accounts with their quota windows and the usage of the live
    agents that run on each."""
    rows = []
    for a in visible_accounts(accounts):
        profile = a.get("profile") if isinstance(a.get("profile"), str) and a.get("profile") else None
        mine = [x for x in agents if profile and x.profile == profile]
        tot = A.usage_totals(x.usage for x in mine)
        cost = None if tot["reported"] is None and tot["estimated"] is None else (tot["reported"] or 0) + (tot["estimated"] or 0)
        kind = None
        if cost is not None:
            kind = "mixed" if tot["reported"] is not None and tot["estimated"] is not None else (
                "reported" if tot["reported"] is not None else "estimated")
        rows.append({"label": san_line(a["label"], 40), "profile": san_line(profile, 80) if profile else None,
                     **read_quota(a["label"], now, directory), "agents": len(mine), "tokens": tot["tokens"],
                     "cost": cost, "cost_kind": kind})
    allt = A.usage_totals(x.usage for x in agents)
    return {"now": now, "accounts": rows,
            "totals": {"agents": allt["agents"], "tokens": allt["tokens"], "reported": allt["reported"],
                       "estimated": allt["estimated"], "text": A.totals_text(allt)}}


# ---------------------------------------------------------------- usage limit

LIMIT_TAIL = 262144
LIMIT_LABELS = {"five_hour": "5-hour limit", "seven_day": "weekly limit", None: "usage limit"}


class LimitProbe:
    """Whether the session file of an agent ends on a usage-limit refusal (`A.limit_of_entry` on the last
    message entry), for agents whose omp does not report the limit itself (an extension from before the
    `limit` state). Only the last LIMIT_TAIL bytes are read; the answer is remembered per (mtime, size)."""

    def __init__(self, cap=64):
        self.cap = cap
        self._seen = {}
        self._lock = threading.Lock()

    def check(self, path):
        try:
            st = os.stat(path)
        except OSError:
            return None
        key = (st.st_mtime_ns, st.st_size)
        with self._lock:
            hit = self._seen.get(path)
            if hit is not None and hit[0] == key:
                return hit[1]
        limit = self._tail(path, st.st_size)
        with self._lock:
            if len(self._seen) >= self.cap:
                self._seen.pop(next(iter(self._seen)))
            self._seen[path] = (key, limit)
        return limit

    @staticmethod
    def _tail(path, size):
        try:
            with open(path, "rb") as f:
                start = max(0, size - LIMIT_TAIL)
                f.seek(start)
                data = f.read(LIMIT_TAIL)
        except OSError:
            return None
        lines = data.split(b"\n")
        if start > 0:
            lines = lines[1:]            # begins inside a line
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except (ValueError, RecursionError):
                continue                 # a line still being written
            if isinstance(entry, dict) and entry.get("type") == "message":
                return A.limit_of_entry(entry)
        return None


def with_limits(agents, probe, find_file):
    """`agents` with each `done` agent of an extension that does not know the `limit` state promoted to
    it when its session file (`find_file(agent)`) ends on a usage-limit refusal. Only `done` is looked at:
    a resumed chat whose last message is an old refusal is `idle` until it is prompted."""
    out = []
    for a in agents:
        if a.state == "done" and a.source == "ext":
            path = find_file(a)
            limit = probe.check(path) if path else None
            if limit:
                a = dataclasses.replace(a, state="limit", reason="usage limit", limit=limit)
        out.append(a)
    return out


def limit_json(agent, accounts, now, san_line, directory=None):
    """What the phone shows of a limit stop: which window (`five_hour`, `seven_day` or None when it cannot
    be told), its words, when it was hit and when it reopens (epoch seconds or None), and the account the
    agent runs on. The window comes from `A.limit_window` and the account's quota cache."""
    limit = agent.limit if isinstance(agent.limit, dict) else {}
    label, windows = None, None
    for a in visible_accounts(accounts):
        if a.get("profile") and a.get("profile") == agent.profile:
            label, windows = a["label"], read_quota(a["label"], now, directory)
            break
    window = A.limit_window(limit, windows)
    return {"window": window, "label": LIMIT_LABELS[window], "hit_at": limit.get("hit_at"), "reset_at": limit.get("reset_at"),
            "account": san_line(label, 40) if label else None}


# ---------------------------------------------------------------- switching an agent's account

SWITCH_STATES = ("limit", "done", "idle")
SWITCH_RESULT_TTL = 120.0           # how long the page can still read how a switch ended
EXIT_WAIT, EXIT_POLL, SHELL_WAIT = 5.0, 0.25, 3.0
CTRL_D, ENTER = "\x04", "\r"
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


class SwitchRefused(Exception):
    """The switch cannot start; nothing was touched. `code` is an HttpError code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def account_block(agent):
    """Why `agent` cannot be moved to another account (a sentence), None when it can."""
    if agent.state == "exited":
        return "agent has exited"
    if agent.state not in SWITCH_STATES:
        return "wait until the agent is idle or has stopped on its limit"
    if not agent.profile or not A.PROFILE_NAME.fullmatch(agent.profile):
        return "this chat runs omp's default configuration: a chat moves only between omp profiles paired with an account"
    if agent.source != "ext" or not isinstance(agent.pid, int) or isinstance(agent.pid, bool) or agent.pid <= 0:
        return "this agent's omp does not publish its process: restart it to switch"
    if (os.environ.get("HIVEGRID_AGENT_CMD") or "omp").strip() != "omp":
        return "HIVEGRID_AGENT_CMD is set: a chat is moved only with plain omp"
    return None


def account_options(accounts, agent, now, san_line, directory=None):
    """(current, offered) for `agent`: the account it runs on ({"label", "profile"}, label None when no
    account is paired with its profile) and the accounts it can be moved to, each with its quota windows.
    An account without a paired omp profile is never offered (a chat moves only between profiles), nor is
    the current one."""
    current = {"label": None, "profile": agent.profile}
    offered, seen = [], set()
    for a in visible_accounts(accounts):
        profile = a.get("profile")
        if not (isinstance(profile, str) and A.PROFILE_NAME.fullmatch(profile)):
            continue
        if profile == agent.profile:
            current["label"] = san_line(a["label"], 40)
            continue
        if profile in seen:
            continue
        seen.add(profile)
        q = read_quota(a["label"], now, directory)
        offered.append({"label": san_line(a["label"], 40), "profile": profile, "state": q["state"],
                        "five_hour": q["five_hour"], "seven_day": q["seven_day"]})
    return current, offered


def _plain_path(value):
    return (isinstance(value, str) and os.path.isabs(value) and len(value) <= SESSION_HINT_MAX and not _CONTROL.search(value)
            and "\\" not in value)  # fish reads a backslash inside single quotes as an escape: never typed


def resume_command(cwd, profile, session):
    """The line typed into the shell after omp quit: `cd <dir> && omp --profile=<p> --resume <file>`
    (the resume is left out when the chat had no message yet; the cd when the directory is unknown)."""
    omp = f"omp --profile={profile}" + (f" --resume {shlex.quote(session)}" if session else "")
    return f"cd {shlex.quote(cwd)} && {omp}" if cwd else omp


class AccountSwitcher:
    """Moves the chat of an agent to another omp profile (account), with `bin/omp-switch`, exactly as the
    header's account pills do (docs/accounts-and-quota.md):

    1. `begin` checks the agent, asks `omp-switch locate` for its session (read-only; nothing touched
       yet) and presses Ctrl+D in its pane once omp is still alive in the pane's foreground (omp quits on an empty prompt and stays when it holds a draft);
       it then returns while a worker thread finishes the move;
    2. the worker waits up to EXIT_WAIT s for omp to exit (never kills it: it stays, the switch is
       abandoned) and for the pane to be at a shell prompt, runs `omp-switch transfer`, and types
       `cd <dir> && omp --profile=<new> --resume <copy>` into the shell. When the transfer fails, the
       chat is resumed on the old profile instead.

    Nothing page-controlled is typed: the profile is one of the offered names and the paths come from
    omp-switch, checked to lie under the profiles' session directories and free of control characters.
    `status(pane)` is what the page reads while and after (state running | done | failed, for
    SWITCH_RESULT_TTL s). `mux` offers `run`, `act`, `send_keys` and `invalidate`; `spawn(fn)` starts the worker."""

    def __init__(self, mux, san_line, home=None, run=subprocess.run, clock=time.monotonic, wall=time.time,
                 sleep=time.sleep, alive=A._pid_alive, foreground=A.foreground_by_tty, switch_bin=None, spawn=None):
        self.mux, self.san_line, self.home = mux, san_line, home
        self.runner, self.clock, self.wall, self.sleep = run, clock, wall, sleep
        self.alive, self.foreground = alive, foreground
        self.switch_bin = switch_bin
        self.spawn = spawn or self._thread
        self._lock = threading.Lock()
        self._jobs = {}                 # pane -> {"state", "to", "message", "since", "ended"}

    @staticmethod
    def _thread(fn):
        threading.Thread(target=fn, daemon=True, name="account-switch").start()

    # -- state the page reads
    def status(self, pane):
        """{"state", "to", "message", "since"} of the latest switch of `pane`, None when there is none (or it ended long ago)."""
        with self._lock:
            job = self._jobs.get(pane)
            if job is not None and job["ended"] is not None and self.clock() - job["ended"] > SWITCH_RESULT_TTL:
                del self._jobs[pane]
                job = None
            return None if job is None else {k: job[k] for k in ("state", "to", "message", "since")}

    def running(self, pane):
        job = self.status(pane)
        return job is not None and job["state"] == "running"

    # -- omp-switch
    def _bin(self):
        if self.switch_bin:
            return self.switch_bin
        env = os.environ.get("OMP_SWITCH_BIN")
        if env:
            return env
        here = os.path.join(os.path.dirname(os.path.realpath(__file__)), "omp-switch")
        return here if os.path.isfile(here) else (shutil.which("omp-switch") or here)

    def _omp_switch(self, *args, timeout=30):
        bad = {"state": "error", "reason": "omp-switch gave no answer"}
        try:
            r = self.runner([self._bin(), *map(str, args)], capture_output=True, text=True, timeout=timeout)
            out = json.loads(r.stdout or "")
        except (OSError, ValueError, subprocess.SubprocessError):
            return bad
        return out if isinstance(out, dict) and out.get("state") in ("ok", "none", "error") else bad

    def _reason(self, res):
        return self.san_line(res.get("reason") if isinstance(res.get("reason"), str) else "unknown reason", 160)

    def _session_in(self, path, profile):
        """The real path of `path` when it is a session file of `profile` that may be typed, else None."""
        real = session_path_ok(path, self.home)
        root = os.path.realpath(os.path.join(_home(self.home), ".omp", "profiles", profile, "agent", "sessions")) + os.sep
        return real if real and _plain_path(real) and real.startswith(root) else None

    # -- the move
    def begin(self, agent, profile, to_label, from_label):
        """Starts the move of `agent` to omp profile `profile` (labelled `to_label`). Raises SwitchRefused
        before anything is touched; returns when Ctrl+D has been sent and the worker runs."""
        why = account_block(agent)
        if why:
            raise SwitchRefused("agent_not_ready", why)
        if not A.PROFILE_NAME.fullmatch(profile or "") or profile == agent.profile:
            raise SwitchRefused("conflict", "the agent already runs on that account")
        with self._lock:
            job = self._jobs.get(agent.pane)
            if job is not None and job["state"] == "running":
                raise SwitchRefused("conflict", "a switch is already running for this agent")
            self._jobs[agent.pane] = {"state": "running", "to": to_label, "message": "asking omp to quit", "since": self.wall(), "ended": None}
        try:
            loc = self._omp_switch("locate", "--pid", agent.pid, "--from", agent.profile, timeout=15)
            if loc["state"] == "error":
                raise SwitchRefused("agent_not_ready", "cannot find the chat, omp keeps running: " + self._reason(loc))
            session = cwd = None
            if loc["state"] == "ok":
                session = self._session_in(loc.get("session"), agent.profile)
                cwd = loc.get("cwd")
                if session is None or not _plain_path(cwd):
                    raise SwitchRefused("agent_not_ready", "cannot find the chat, omp keeps running: the session is not where omp keeps it")
            elif _plain_path(agent.cwd):
                cwd = agent.cwd
            try:
                if not self._omp_in_front(agent):
                    raise SwitchRefused("agent_not_ready", "omp is no longer running in that pane: nothing was sent")
                self.mux.act(lambda: self.mux.send_keys(agent.pane, [CTRL_D]))
            except SwitchRefused:
                raise
            except Exception:  # noqa: BLE001 - the mux says why in its own words; nothing was sent
                raise SwitchRefused("agent_not_ready", "could not reach the agent's pane") from None
        except BaseException:
            with self._lock:
                self._jobs.pop(agent.pane, None)
            raise
        job = {"pane": agent.pane, "pid": agent.pid, "from": agent.profile, "from_label": from_label, "to": profile,
               "to_label": to_label, "session": session, "cwd": cwd}
        try:
            self.spawn(lambda: self._work(job))
        except Exception:  # noqa: BLE001 - Ctrl+D is out already: the job must not stay `running` and block the next switch
            self._end(agent.pane, False, "the switch could not continue: omp was asked to quit, check the pane")

    def _omp_in_front(self, agent):
        """True while the agent's process is alive and omp is what runs in the foreground of its pane:
        Ctrl+D must never reach a shell (it would close it) or another program."""
        if not self.alive(agent.pid):
            return False
        row = next((p for p in (A._list_panes(self.mux.run) or []) if p["pane_id"] == agent.pane), None)
        return row is not None and self.foreground().get(A._tty(row), (None, "", 0, ""))[1] == "omp"

    def _end(self, pane, ok, message):
        with self._lock:
            job = self._jobs.get(pane)
            if job is not None:
                job.update(state="done" if ok else "failed", message=message, ended=self.clock())
        self.mux.invalidate()

    def _wait_exit(self, pid):
        end = self.clock() + EXIT_WAIT
        while self.alive(pid):
            if self.clock() >= end or self.sleep(EXIT_POLL):
                return False
        return True

    def _at_shell(self, pane):
        end = self.clock() + SHELL_WAIT
        while True:
            panes = A._list_panes(self.mux.run) or []
            row = next((p for p in panes if p["pane_id"] == pane), None)
            fg = self.foreground() if row is not None else {}
            if row is not None and fg.get(A._tty(row), (None, "", 0, ""))[1] in A.SHELLS:
                return True
            if self.clock() >= end or self.sleep(EXIT_POLL):
                return False

    def _type(self, pane, line):
        self.mux.act(lambda: self.mux.send_keys(pane, [ENTER], line))

    def _work(self, job):
        pane = job["pane"]
        try:
            if not self._wait_exit(job["pid"]):
                return self._end(pane, False, "omp did not quit (unsent text in its prompt?): account not switched, omp keeps running")
            if not self._at_shell(pane):
                return self._end(pane, False, "omp quit but the pane is not at a shell prompt: the chat was not moved")
            session = None
            if job["session"] is not None:
                moved = self._omp_switch("transfer", "--session", job["session"], "--from", job["from"], "--to", job["to"], timeout=120)
                session = self._session_in(moved.get("session"), job["to"]) if moved["state"] == "ok" else None
                if session is None:
                    reason = self._reason(moved) if moved["state"] == "error" else "the copy is not where omp keeps sessions"
                    self._type(pane, resume_command(job["cwd"], job["from"], job["session"]))
                    return self._end(pane, False, f"the chat could not be moved ({reason}); it continues on {job['from_label']}")
            self._type(pane, resume_command(job["cwd"], job["to"], session))
            self._end(pane, True, f"the chat continues on {job['to_label']}")
        except Exception:  # noqa: BLE001 - a failed worker must still end the job
            self._end(pane, False, "the switch failed: check the pane")



# ---------------------------------------------------------------- system metrics

HISTORY_WINDOW = 6 * 3600
HISTORY_POINTS = 120
HISTORY_TAIL = 262144
GPU_QUERY = ("nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits")


def parse_cpu(text):
    """(total, idle) jiffies of the aggregate `cpu` line of /proc/stat, else None."""
    m = re.match(r"cpu\s+([0-9 ]+)", text or "")
    if not m:
        return None
    f = [int(x) for x in m.group(1).split()]
    if len(f) < 5:
        return None
    return sum(f[:8]), f[3] + f[4]


def parse_meminfo(text):
    """(total, available) in bytes from /proc/meminfo, else None."""
    t = re.search(r"MemTotal:\s+(\d+)", text or "")
    a = re.search(r"MemAvailable:\s+(\d+)", text or "")
    if not t or not a or int(t.group(1)) == 0:
        return None
    return int(t.group(1)) * 1024, int(a.group(1)) * 1024


def parse_gpu(text):
    """{"name", "util", "mem_used", "mem_total"} of the first line of the nvidia-smi query, else None."""
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 4:
        return None
    try:
        util, used, total = float(parts[-3]), float(parts[-2]), float(parts[-1])
    except ValueError:
        return None
    name = ", ".join(parts[:-3]).strip()
    return {"name": name[:80], "util": util, "mem_used": used * 1048576, "mem_total": total * 1048576}


def history_points(text, now, window=HISTORY_WINDOW, points=HISTORY_POINTS):
    """The last `window` seconds of sysstats.jsonl lines as at most `points` entries, evenly picked."""
    rows = []
    for line in (text or "").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        ts = _num(e.get("ts")) if isinstance(e, dict) else None
        if ts is None or ts < now - window or ts > now + 60:
            continue
        rows.append({"ts": ts, **{k: _num(e.get(k)) for k in ("cpu", "ram", "dsk", "gpu")}})
    if len(rows) > points:
        step = len(rows) / points
        rows = [rows[int(i * step)] for i in range(points - 1)] + [rows[-1]]
    return rows


def history_path(environ=None):
    environ = os.environ if environ is None else environ
    base = environ.get("XDG_CACHE_HOME") or os.path.join(_home(), ".cache")
    return os.path.join(base, "hivegrid", "sysstats.jsonl")


class SysStats:
    """CPU, memory, disk, GPU and the monitor log. CPU% is the delta between two /proc/stat samples:
    the previous call's when it is 0.5..30 s old, else a fresh pair 0.15 s apart, so a call never
    waits longer than that. `slots` is the semaphore that bounds subprocesses (nvidia-smi)."""

    def __init__(self, proc="/proc", clock=time.monotonic, sleep=time.sleep, wall=time.time, run=subprocess.run,
                 slots=None, disk_path=None, log_path=None, which=shutil.which):
        self.proc, self.clock, self.sleep, self.wall, self.run, self.slots = proc, clock, sleep, wall, run, slots
        self.disk_path, self.log_path, self.which = disk_path, log_path, which
        self._lock = threading.Lock()
        self._cpu_prev = None            # (total, idle, t)
        self._cpu = None
        self._gpu = None
        self._gpu_at = None
        self._hist = None                # (mtime_ns, size, checked_at, rows)

    def _read(self, name):
        try:
            with open(f"{self.proc}/{name}", encoding="ascii", errors="replace") as f:
                return f.read(65536)
        except OSError:
            return None

    def _sample(self):
        s = parse_cpu(self._read("stat"))
        return None if s is None else (s[0], s[1], self.clock())

    def cpu(self):
        with self._lock:
            now = self._sample()
            if now is None:
                return None
            prev = self._cpu_prev
            if prev is not None and 0.5 <= now[2] - prev[2] <= 30:
                self._cpu_prev = now
            elif prev is not None and now[2] - prev[2] < 0.5 and self._cpu is not None:
                return self._cpu
            else:
                self.sleep(0.15)
                prev, now = now, self._sample() or now
                self._cpu_prev = now
            dt, di = now[0] - prev[0], now[1] - prev[1]
            self._cpu = None if dt <= 0 else round(min(max(100.0 * (dt - di) / dt, 0.0), 100.0), 1)
            return self._cpu

    def gpu(self):
        with self._lock:
            t = self.clock()
            if self._gpu_at is not None and t - self._gpu_at < (3 if self._gpu else 60):
                return self._gpu
            if self.which("nvidia-smi") is None:
                self._gpu_at = t
                return self._gpu
            if self.slots is not None and not self.slots.acquire(blocking=False):
                return self._gpu                    # all busy right now: look again on the next call, not in a minute
            try:
                out = self.run(GPU_QUERY, capture_output=True, text=True, timeout=2)
                self._gpu = parse_gpu(out.stdout) if out.returncode == 0 else None
            except (OSError, subprocess.SubprocessError):
                self._gpu = None
            finally:
                if self.slots is not None:
                    self.slots.release()
            self._gpu_at = t
            return self._gpu

    def history(self, now):
        path = self.log_path or history_path()
        try:
            st = os.stat(path)
        except OSError:
            return []
        with self._lock:
            h = self._hist
            if h and h[0] == st.st_mtime_ns and h[1] == st.st_size and self.clock() - h[2] < 30:
                return h[3]
        try:
            with open(path, "rb") as f:
                f.seek(max(0, st.st_size - HISTORY_TAIL))
                text = f.read(HISTORY_TAIL).decode("utf-8", "replace")
        except OSError:
            return []
        rows = history_points(text, now)
        with self._lock:
            self._hist = (st.st_mtime_ns, st.st_size, self.clock(), rows)
        return rows

    def snapshot(self):
        now = self.wall()
        mem = parse_meminfo(self._read("meminfo"))
        mem_json = {"used": mem[0] - mem[1], "total": mem[0], "pct": round(100.0 * (mem[0] - mem[1]) / mem[0], 1)} if mem else None
        try:
            du = shutil.disk_usage(self.disk_path or _home())
            used_free = du.used + du.free
            disk = {"pct": round(100.0 * du.used / used_free, 1), "free": du.free} if used_free else None
        except OSError:
            disk = None
        try:
            load = [round(x, 2) for x in os.getloadavg()]
        except OSError:
            load = None
        up = re.match(r"\s*([0-9.]+)", self._read("uptime") or "")
        return {"ts": now, "cpu": self.cpu(), "cores": os.cpu_count(), "load": load, "mem": mem_json, "disk": disk,
                "gpu": self.gpu(), "uptime": float(up.group(1)) if up else None, "history": self.history(now)}


# ---------------------------------------------------------------- new session choices

PROJECT_DIRS_MAX = 60


def session_options(accounts, agents, san_line, home=None):
    """(profiles, dirs, real): the choices of `GET /api/sessions/options`, and {display path: real
    absolute directory} for validating `POST /api/sessions`. Directories: `~`, the working directories
    of live agents that exist, and the immediate non-hidden subdirectories of `~/Projects` (<= 60).

    An account without a paired omp profile can only start omp's default configuration
    (docs/accounts-and-quota.md), whatever its label says, so those accounts collapse into one entry
    with `profile: null`, `default: true` and a `note` saying so: the `main` account when it is one of
    them, else the first."""
    home = _home(home)
    rows, seen_labels = [], set()
    for a in visible_accounts(accounts):
        if a["label"] in seen_labels:
            continue
        seen_labels.add(a["label"])
        profile = a.get("profile") if isinstance(a.get("profile"), str) and A.PROFILE_NAME.fullmatch(a.get("profile") or "") else None
        rows.append((a["label"], profile))
    bare = [label for label, profile in rows if profile is None]
    default = "main" if "main" in bare else (bare[0] if bare else None)
    profiles = []
    for label, profile in rows:
        if profile is not None:
            profiles.append({"label": san_line(label, 40), "profile": profile})
        elif label == default:
            note = ("omp's default configuration" if label == "main"
                    else "omp's default configuration: this account has no paired omp profile")
            profiles.append({"label": san_line(label, 40), "profile": None, "default": True, "note": note})
    dirs, real = [], {}

    def add(path, label):
        shown = san_line("~" + path[len(home):] if path == home or path.startswith(home.rstrip("/") + "/") else path, 200)
        if shown and shown not in real:
            real[shown] = path
            dirs.append({"path": shown, "label": san_line(label, 60)})

    add(home, "~")
    for a in agents:
        cwd = a.cwd
        if isinstance(cwd, str) and os.path.isabs(cwd) and os.path.isdir(cwd):
            add(cwd.rstrip("/") or "/", os.path.basename(cwd.rstrip("/")) or "/")
    projects = os.path.join(home, "Projects")
    try:
        names = sorted((e.name for e in os.scandir(projects) if not e.name.startswith(".") and e.is_dir()), key=str.lower)
    except OSError:
        names = []
    count = 0
    for name in names:
        if count >= PROJECT_DIRS_MAX:
            break
        if san_line(name, 200) != name:
            continue
        before = len(dirs)
        add(os.path.join(projects, name), name)
        count += len(dirs) - before
    return profiles, dirs, real


# ---------------------------------------------------------------- custom directory for a new session

SEARCH_MAX = 30            # results of one search
SEARCH_QUERY_MAX = 100
SEARCH_VISIT = 5000        # directories looked at per search
SEARCH_DEPTH = 6           # levels below a root
SEARCH_SECONDS = 1.0
SEARCH_SKIP = frozenset(("node_modules", "__pycache__", "site-packages", "target", "venv", "dist", "build"))


def dir_roots(home=None):
    """The real paths a new session's directory may lie below: the home directory and, when it is a
    link elsewhere, `~/Projects` (the one folder the options already list)."""
    home = _home(home)
    roots = [os.path.realpath(home)]
    projects = os.path.realpath(os.path.join(home, "Projects"))
    if os.path.isdir(projects) and not any(projects == r or projects.startswith(r.rstrip("/") + "/") for r in roots):
        roots.append(projects)
    return roots


def _below(path, roots):
    """True when `path` is a root or lies below one with no hidden component (`.ssh`, `.git`, ...)."""
    for r in roots:
        rel = os.path.relpath(path, r)
        if rel == ".":
            return True
        if not (rel == ".." or rel.startswith(".." + os.sep) or os.path.isabs(rel)):
            return not any(part.startswith(".") for part in rel.split(os.sep))
    return False


def resolve_custom_dir(shown, home=None):
    """The real directory for a path the phone typed or picked (`~/x`, an absolute path), or None. It must
    exist as a directory and, once links are resolved, lie in the home directory (or `~/Projects`) with no
    hidden component: the phone cannot reach `/etc`, `~/.ssh` or a link that leads out of the home."""
    if not isinstance(shown, str) or not shown or len(shown) > 300 or "\0" in shown:
        return None
    home = _home(home)
    if shown == "~" or shown.startswith("~/"):
        path = home.rstrip("/") + shown[1:]
    elif os.path.isabs(shown):
        path = shown
    else:
        return None
    try:
        real = os.path.realpath(path)
        ok = os.path.isdir(real) and _below(real, dir_roots(home))
    except (OSError, ValueError):
        return None
    return real if ok else None


def _shown(path, home):
    home = _home(home).rstrip("/")
    return "~" + path[len(home):] if path == home or path.startswith(home + "/") else path


def search_dirs(query, san_line, home=None, clock=time.monotonic):
    """Directories for the phone's search box: [{path, label}], at most SEARCH_MAX, best first. A query that
    starts with `~` or `/` is a path to complete (the folders of the typed folder whose name continues what
    follows the last slash); anything else is words that must all appear in a folder's name or path, looked
    for below the roots breadth-first (hidden folders, links and build folders are not entered; bounded in
    folders looked at, depth and time). Only folders `resolve_custom_dir` would accept come back."""
    query = (query or "").strip()[:SEARCH_QUERY_MAX]
    if not query or "\0" in query:
        return []
    home, roots = _home(home), dir_roots(home)
    out = []

    def add(path):
        shown = san_line(_shown(path, home), 200)
        if shown and san_line(os.path.basename(path), 200) == os.path.basename(path) and shown == _shown(path, home):
            out.append({"path": shown, "label": san_line(os.path.basename(path) or "/", 60)})

    def subdirs(path):
        try:
            return sorted((e.name for e in os.scandir(path) if e.is_dir(follow_symlinks=False) and not e.name.startswith(".")), key=str.lower)
        except OSError:
            return []

    if query[0] in "~/":
        base, _slash, prefix = query.rpartition("/")
        folder = resolve_custom_dir((base or "/") if query[0] == "/" else (base or "~"), home)
        if folder is None:
            return []
        prefix = prefix.lower()
        names = subdirs(folder)
        for name in [n for n in names if n.lower().startswith(prefix)] + [n for n in names if prefix and prefix in n.lower() and not n.lower().startswith(prefix)]:
            add(os.path.join(folder, name))
            if len(out) >= SEARCH_MAX:
                break
        return out

    words = query.lower().split()
    end, visited = clock() + SEARCH_SECONDS, 0
    found = []
    level = [(r, 0) for r in roots]
    seen = set()
    while level and visited < SEARCH_VISIT and clock() < end:
        nxt = []
        for path, depth in level:
            if path in seen:
                continue
            seen.add(path)
            for name in subdirs(path):
                visited += 1
                if name in SEARCH_SKIP:
                    continue
                child = os.path.join(path, name)
                text = _shown(child, home).lower()
                low = name.lower()
                if all(w in text for w in words):
                    rank = 0 if low == query.lower() else (1 if low.startswith(words[0]) else (2 if all(w in low for w in words) else 3))
                    found.append((rank, depth, text, child))
                if depth + 1 < SEARCH_DEPTH:
                    nxt.append((child, depth + 1))
                if visited >= SEARCH_VISIT or clock() >= end:
                    break
        level = nxt
    for _rank, _depth, _text, path in sorted(found)[:SEARCH_MAX]:
        add(path)
    return out
