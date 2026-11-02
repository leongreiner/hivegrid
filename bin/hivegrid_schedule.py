"""hivegrid-schedule: scheduled `omp` agents ("Bots"). See docs/schedules.md.

This module is the logic without any UI or daemon, so a daemon, a panel and the phone remote can import
it. Standard library only; nothing happens at import time. Every function that touches the disk takes
explicit paths or reads `HOME` / `XDG_*` from `env` (default `os.environ`) when it is called.

Public API (everything else is internal, names start with `_`):

  paths        config_dir, schedules_path, state_root, state_dir, command_dir, summary_path
  files        atomic_write, ensure_private_dir, locked, ScheduleError
  definitions  Store (read, get, add, change, remove, mutate), new_schedule, validate, validate_when
  time         parse_cron, next_fire, next_fires, describe, parse_when, parse_duration
  state        default_state, load_state, save_state, update_state, init_state_dir, advance, skip_next,
               record_outcome, next_run
  policy       due, plan_catchup, overlap_action, retry_plan, breaker, is_active
  runs         normalize_run, append_run, read_runs, unseen, mark_seen, report_path, parse_report, auto_headline
  prompts      compose_prompt, identity_text, trim_memory, memory_path, enforce_memory_cap
  self         self_next, self_done, self_note
  briefing     briefing, briefing_counts, mark_briefing_seen
  chat         say, conversation (the daemon runs the turns, see hivegrid_scheduler)
  daemon I/O   send_command, read_commands, build_summary, write_summary, read_summary, daemon_alive
  cli          main
"""
import argparse
import calendar
import contextlib
import copy
import datetime as dt
import fcntl
import json
import os
import random
import re
import shutil
import stat
import sys
import time
import unicodedata

SCHEMA = 1
ID_RE = re.compile(r"^[a-z0-9-]{3,32}$")
KINDS = ("task", "agent")
ORIGINS = ("cli", "panel", "phone")
MODES = ("headless", "visible", "hybrid")
PERMISSIONS = ("readonly", "edit", "full")
THINKING = ("off", "minimal", "low", "medium", "high", "xhigh", "max", "auto")
CATCHUP = ("latest", "skip")
OVERLAP = ("skip", "queue")
CONTINUITY = ("fresh", "resume")
NOTIFY_ON = ("failed", "limit", "missed", "done")
TRIGGERS = ("cron", "manual", "retry", "catchup", "self", "chat")
RUN_STATUS = ("ok", "failed", "limit", "timeout", "cost", "skipped", "missed", "cancelled", "deferred")
ATTENTION_STATUS = ("failed", "limit", "missed", "timeout", "cost")
NO_ATTENTION_STATUS = ("skipped", "deferred", "cancelled")  # recorded already seen
COMMAND_OPS = ("run-now", "skip", "unskip", "cancel", "reload", "chat")
LEVELS = ("none", "info", "important")
OVERRIDE_STATUS = ("failed", "limit", "timeout", "cost", "missed", "deferred")  # always `important`
CANCELLED_BY_USER = "cancelled by the user"  # error text of a run the user stopped (level `none`)
HEADLINE_MAX = 140
CHAT_MAX = 4000
CHAT_QUEUE_MAX = 5

MIN_INTERVAL_S = 300
MAX_INTERVAL_S = 365 * 86400
MAX_RUNS = 200
ONTIME_S = 120  # a slot noticed later than this after its time is a catch-up, not a regular fire
ONCE_MISSED_S = 86400
DUE_WINDOW_S = 7 * 86400
DUE_LIMIT = 4000
SUMMARY_MAX = 1000
HEARTBEAT_MAX_AGE_S = 30
MAX_PROMPT = 20000
MAX_NAME = 80

PREAMBLE = ("You are an unattended scheduled agent run by Hivegrid. Nobody can answer questions while you "
            "work: never ask, decide sensibly yourself. Finish with a short report.")
# A visible run (and the takeover of a hybrid one) is an interactive omp in a pane: the user may be watching.
PREAMBLE_VISIBLE = ("You are a scheduled agent run by Hivegrid in a visible pane: the user may be watching and may "
                    "answer if you ask. Ask only when you cannot decide sensibly yourself, otherwise decide and go on. "
                    "Whatever happens, finish with the short report file.")


class ScheduleError(OSError):
    """A file or directory that cannot be trusted or used (symlink, other owner, unreadable store), or a
    request that cannot be served. `code` is a short machine word when there is one (`no_daemon`,
    `queue_full`, `unknown_bot`)."""

    def __init__(self, message="", code=None):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- paths

def _env(env):
    return os.environ if env is None else env


def _uid():
    return os.getuid()


def _home(env):
    return env.get("HOME") or os.path.expanduser("~")


def _xdg(env, var, rel):
    v = env.get(var)
    return v if v and os.path.isabs(v) else os.path.join(_home(env), rel)


def config_dir(env=None):
    """`$XDG_CONFIG_HOME/hivegrid` (default `~/.config/hivegrid`)."""
    env = _env(env)
    return os.path.join(_xdg(env, "XDG_CONFIG_HOME", ".config"), "hivegrid")


def schedules_path(env=None):
    return os.path.join(config_dir(env), "schedules.json")


def state_root(env=None):
    """`$XDG_STATE_HOME/hivegrid/schedules` (default `~/.local/state/hivegrid/schedules`)."""
    env = _env(env)
    return os.path.join(_xdg(env, "XDG_STATE_HOME", ".local/state"), "hivegrid", "schedules")


def state_dir(schedule_id, env=None):
    """The runtime directory of one schedule (not created here)."""
    if not isinstance(schedule_id, str) or not ID_RE.match(schedule_id):
        raise ValueError(f"bad schedule id {schedule_id!r}")
    return os.path.join(state_root(env), schedule_id)


def command_dir(env=None):
    """The daemon control inbox: `$XDG_RUNTIME_DIR/hivegrid-schedule`, else `/tmp/hivegrid-schedule-<uid>`."""
    env = _env(env)
    rt = env.get("XDG_RUNTIME_DIR")
    if rt and os.path.isabs(rt):
        return os.path.join(rt, "hivegrid-schedule")
    return f"/tmp/hivegrid-schedule-{_uid()}"


def summary_path(env=None):
    """`$XDG_RUNTIME_DIR/hivegrid-schedule.json`; without XDG_RUNTIME_DIR `summary.json` in the command dir."""
    env = _env(env)
    rt = env.get("XDG_RUNTIME_DIR")
    if rt and os.path.isabs(rt):
        return os.path.join(rt, "hivegrid-schedule.json")
    return os.path.join(command_dir(env), "summary.json")


# ---------------------------------------------------------------- files

def ensure_private_dir(path, create=True):
    """Create `path` (mode 0700) when missing and refuse it unless it is a real directory of this user
    that others cannot write to. Raises ScheduleError."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        if not create:
            raise ScheduleError(f"{path} does not exist") from None
        try:
            os.makedirs(path, mode=0o700, exist_ok=True)
            st = os.lstat(path)
        except OSError as exc:
            raise ScheduleError(f"cannot create {path}: {exc.strerror}") from None
    except OSError as exc:
        raise ScheduleError(f"cannot read {path}: {exc.strerror}") from None
    if stat.S_ISLNK(st.st_mode):
        raise ScheduleError(f"{path} is a symbolic link")
    if not stat.S_ISDIR(st.st_mode):
        raise ScheduleError(f"{path} is not a directory")
    if st.st_uid != _uid():
        raise ScheduleError(f"{path} belongs to another user")
    if st.st_mode & 0o022:
        raise ScheduleError(f"{path} can be written by others (chmod 700 it)")


def atomic_write(path, data, mode=0o600):
    """Write `data` (str or bytes) to `path` through a temp file in the same directory and os.replace."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    d, base = os.path.split(path)
    tmp = os.path.join(d, f".{base}.{os.getpid()}.{time.time_ns()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@contextlib.contextmanager
def locked(lock_path):
    """Exclusive `flock` on a sibling lock file (created 0600, refused when not ours or a symlink)."""
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError as exc:
        raise ScheduleError(f"cannot open lock {lock_path}: {exc.strerror}") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != _uid():
            raise ScheduleError(f"{lock_path} is not a lock file of this user")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # closing releases the flock


def _read_text_trusted(path, limit=8 * 1024 * 1024):
    """File content when it is a regular file of this user (no symlink), None when missing."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ScheduleError(f"cannot read {path}: {exc.strerror}") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != _uid():
            raise ScheduleError(f"{path} is not a regular file of this user")
        if st.st_size > limit:
            raise ScheduleError(f"{path} is too large")
        chunks = []
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            chunks.append(b)
    finally:
        os.close(fd)
    return b"".join(chunks).decode("utf-8", "replace")


# ---------------------------------------------------------------- time zones

_ZONES = {}


class _Zone:
    """naive local datetime <-> epoch for "local" or an IANA name. Ambiguous (repeated) times resolve to
    the first occurrence, nonexistent (skipped) times to the shifted instant."""

    def __init__(self, tz):
        self.name = tz
        self.tzinfo = None
        if tz == "local":
            return
        if tz.upper() in ("UTC", "GMT", "Z"):
            self.tzinfo = dt.timezone.utc
            return
        try:
            import zoneinfo
            self.tzinfo = zoneinfo.ZoneInfo(tz)
        except (KeyError, ValueError, OSError, ImportError):
            raise ValueError(f"unknown time zone {tz!r} (use 'local' or an IANA name such as Europe/Berlin)") from None

    def naive(self, epoch):
        try:
            if self.tzinfo is None:
                return dt.datetime(*time.localtime(epoch)[:6])
            return dt.datetime.fromtimestamp(epoch, self.tzinfo).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            raise ValueError(f"time {epoch} is out of range") from None

    def epoch(self, naive):
        try:
            if self.tzinfo is not None:
                return int(naive.replace(tzinfo=self.tzinfo, fold=0).timestamp())
            tup = (naive.year, naive.month, naive.day, naive.hour, naive.minute, naive.second, 0, 0, -1)
            e = time.mktime(tup)
            cands = [e]
            base = calendar.timegm(naive.timetuple())
            for probe in (e - 43200, e + 43200):
                c = base - time.localtime(probe).tm_gmtoff
                if time.localtime(c)[:6] == tup[:6]:
                    cands.append(c)
            return int(min(cands))
        except (OverflowError, OSError, ValueError):
            raise ValueError(f"time {naive} is out of range") from None


def _zone(tz):
    tz = tz or "local"
    if tz == "local":
        return _Zone("local")
    z = _ZONES.get(tz)
    if z is None:
        z = _ZONES[tz] = _Zone(tz)
    return z


_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{1,2}):(\d{2})(?::(\d{2}))?$")


def _parse_naive(text):
    m = _ISO_RE.match(text.strip()) if isinstance(text, str) else None
    if not m:
        raise ValueError(f"bad time {text!r}: use YYYY-MM-DDTHH:MM (local time of the schedule)")
    y, mo, d, h, mi, s = m.groups()
    try:
        return dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0))
    except ValueError as exc:
        raise ValueError(f"bad time {text!r}: {exc}") from None


def _naive_str(n, seconds=False):
    return n.strftime("%Y-%m-%dT%H:%M:%S" if seconds else "%Y-%m-%dT%H:%M")


# ---------------------------------------------------------------- cron

_MONTHS = {n: i + 1 for i, n in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
_MONTHS.update({"january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7, "august": 8,
                "september": 9, "october": 10, "november": 11, "december": 12})
_DOWS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
         "sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6}
_MACROS = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
           "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *", "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *"}
_MAXDAY = {1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}


class Cron:
    """A parsed 5-field cron expression: sorted tuples minutes, hours, doms, months, dows (0 = Sunday)
    and the Vixie flags dom_star / dow_star (the field started with `*`)."""

    def __init__(self, expr, minutes, hours, doms, months, dows, dom_star, dow_star):
        self.expr = expr
        self.minutes, self.hours, self.doms = tuple(sorted(minutes)), tuple(sorted(hours)), tuple(sorted(doms))
        self.months, self.dows = tuple(sorted(months)), tuple(sorted(dows))
        self.dom_star, self.dow_star = dom_star, dow_star

    def day_matches(self, d):
        dom_ok = d.day in self.doms
        dow_ok = (d.isoweekday() % 7) in self.dows
        if self.dom_star or self.dow_star:
            return dom_ok and dow_ok
        return dom_ok or dow_ok  # Vixie: both restricted means either one

    def min_gap_s(self):
        """The smallest distance between two fires in seconds (the wrap to the next day included)."""
        mins = sorted(h * 60 + m for h in self.hours for m in self.minutes)
        gaps = [b - a for a, b in zip(mins, mins[1:])] + [1440 - mins[-1] + mins[0]]
        return min(gaps) * 60 if len(mins) > 1 else 86400

    def naive_after(self, start, years=9):
        """Matching naive datetimes >= `start` (minute aligned), ascending."""
        day = start.date()
        end = day + dt.timedelta(days=366 * years)
        one = dt.timedelta(days=1)
        while day <= end:
            if day.month not in self.months:
                day = (day.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
                continue
            if self.day_matches(day):
                for h in self.hours:
                    for m in self.minutes:
                        cand = dt.datetime(day.year, day.month, day.day, h, m)
                        if cand >= start:
                            yield cand
            day += one


def _cron_value(tok, names, label):
    if tok.isdigit():
        return int(tok)
    if tok in names:
        return names[tok]
    raise ValueError(f"bad {label} value {tok!r}")


def _cron_field(text, lo, hi, names, label, top=None):
    top = hi if top is None else top
    out = set()
    for part in text.split(","):
        if not part:
            raise ValueError(f"empty item in {label} field {text!r}")
        base, sep, step_s = part.partition("/")
        step = 1
        if sep:
            if not step_s.isdigit() or int(step_s) < 1:
                raise ValueError(f"bad step {step_s!r} in {label} field {text!r}")
            step = int(step_s)
        if base == "*":
            a, b = lo, hi
        elif "-" in base:
            x, _, y = base.partition("-")
            a, b = _cron_value(x, names, label), _cron_value(y, names, label)
        else:
            a = _cron_value(base, names, label)
            b = hi if sep else a
        if a < lo or b > top or a > b:
            raise ValueError(f"{label} value out of range {lo}-{top} in {part!r}")
        out.update(range(a, b + 1, step))
    return out


def parse_cron(expr):
    """Parse `m h dom mon dow` (lists, ranges, steps, month and weekday names) or an `@hourly`-style
    macro. Raises ValueError with a message that names the bad field."""
    if not isinstance(expr, str):
        raise ValueError("cron expression must be text")
    text = " ".join(expr.strip().split())
    low = text.lower()
    if low.startswith("@"):
        if low not in _MACROS:
            raise ValueError(f"unknown macro {text!r} (use {', '.join(sorted(_MACROS))})")
        low = _MACROS[low]
    fields = low.split()
    if len(fields) != 5:
        raise ValueError(f"cron needs 5 fields (minute hour day-of-month month weekday), got {len(fields)}: {text!r}")
    mi = _cron_field(fields[0], 0, 59, {}, "minute")
    ho = _cron_field(fields[1], 0, 23, {}, "hour")
    dom = _cron_field(fields[2], 1, 31, {}, "day-of-month")
    mo = _cron_field(fields[3], 1, 12, _MONTHS, "month")
    dw = {d % 7 for d in _cron_field(fields[4], 0, 6, _DOWS, "weekday", top=7)}
    dom_star, dow_star = fields[2].startswith("*"), fields[4].startswith("*")
    if dow_star and not dom_star and not any(d <= _MAXDAY[m] for m in mo for d in dom):
        raise ValueError(f"{text!r} never fires (no such day in the chosen months)")
    return Cron(text, mi, ho, dom, mo, dw, dom_star, dow_star)


# ---------------------------------------------------------------- when

def validate_when(w):
    """Normalize and check a `when` object (cron | interval | once); raises ValueError."""
    if not isinstance(w, dict):
        raise ValueError("when must be an object with a type: cron, interval or once")
    t = w.get("type")
    if t == "cron":
        _no_unknown(w, ("type", "expr", "tz"), "when")
        cron = parse_cron(w.get("expr"))
        tz = w.get("tz", "local")
        _zone(tz if isinstance(tz, str) else "?")
        if cron.min_gap_s() < MIN_INTERVAL_S:
            raise ValueError(f"{cron.expr!r} fires more often than every {MIN_INTERVAL_S // 60} minutes")
        return {"type": "cron", "expr": cron.expr, "tz": tz}
    if t == "interval":
        _no_unknown(w, ("type", "every_s", "anchor"), "when")
        every = _int(w.get("every_s"), "when.every_s", MIN_INTERVAL_S, MAX_INTERVAL_S)
        anchor = _int(w.get("anchor"), "when.anchor", 0, 1 << 40)
        return {"type": "interval", "every_s": every, "anchor": anchor}
    if t == "once":
        _no_unknown(w, ("type", "at", "tz"), "when")
        tz = w.get("tz", "local")
        _zone(tz if isinstance(tz, str) else "?")
        n = _parse_naive(w.get("at"))
        secs = n.second != 0
        return {"type": "once", "at": _naive_str(n, secs), "tz": tz}
    raise ValueError("when.type must be cron, interval or once")


def _slot_local(when, epoch):
    """The naive local slot string of a fired slot (the `last_slot` argument of next_fire); None for
    interval schedules."""
    if when["type"] == "interval":
        return None
    return _naive_str(_zone(when.get("tz", "local")).naive(epoch))


def next_fire(when, after_epoch, last_slot=None):
    """The first fire time (UTC epoch, int) strictly after `after_epoch`, or None. `last_slot` is the
    naive local slot string `YYYY-MM-DDTHH:MM` of the slot handled last: slots at or before it never fire
    again, which makes a repeated hour (DST fall back) fire once. A nonexistent local time (spring
    forward) fires once at the shifted instant. Interval schedules fire at anchor + k * every_s, k >= 1."""
    t = when["type"]
    if t == "interval":
        every, anchor = when["every_s"], when["anchor"]
        if after_epoch < anchor:
            return int(anchor + every)
        return int(anchor + ((int(after_epoch) - anchor) // every + 1) * every)
    zone = _zone(when.get("tz", "local"))
    if t == "once":
        at = _parse_naive(when["at"])
        if last_slot and _naive_str(at) <= last_slot:
            return None
        e = zone.epoch(at)
        return e if e > after_epoch else None
    cron = parse_cron(when["expr"])
    start = zone.naive(after_epoch).replace(second=0, microsecond=0) - dt.timedelta(hours=3)
    for cand in cron.naive_after(start):
        if last_slot and _naive_str(cand) <= last_slot:
            continue
        e = zone.epoch(cand)
        if e > after_epoch:
            return e
    return None


def next_fires(when, after, n, last_slot=None):
    """The next `n` fire times after `after` (fewer for a once schedule or an impossible cron)."""
    out = []
    cur = after
    while len(out) < n:
        e = next_fire(when, cur, last_slot)
        if e is None:
            break
        out.append(e)
        cur, last_slot = e, None
    return out


_MON_ABBR = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
_DOW_ABBR = "sun mon tue wed thu fri sat".split()


def _ordinal(n):
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _dur_text(s):
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s % size == 0:
            return f"{s // size}{unit}"
    return f"{s // 60}m" if s >= 60 else f"{s}s"


def describe(when, now=None):
    """Short human text: `weekdays 09:00`, `every 30m`, `once Nov 3 09:00`, `daily 09:00`, `monthly 1st 09:00`;
    other cron expressions read `cron <expr>`. A zone other than local is appended in parentheses."""
    t = when["type"]
    if t == "interval":
        return f"every {_dur_text(when['every_s'])}"
    tz = when.get("tz", "local")
    suffix = "" if tz == "local" else f" ({tz})"
    if t == "once":
        n = _parse_naive(when["at"])
        year = f" {n.year}" if now is not None and n.year != _zone(tz).naive(now).year else ""
        sec = f":{n.second:02d}" if n.second else ""
        return f"once {_MON_ABBR[n.month - 1]} {n.day}{year} {n.hour:02d}:{n.minute:02d}{sec}{suffix}"
    c = parse_cron(when["expr"])
    return _describe_cron(c) + suffix


def _describe_cron(c):
    dows, all_months = set(c.dows), len(c.months) == 12
    fields = c.expr.lower().split() if not c.expr.startswith("@") else _MACROS.get(c.expr.lower(), c.expr).split()
    free_days = len(c.doms) == 31 or c.dom_star
    if all_months and free_days and len(dows) == 7:
        if c.minutes == (0,) and len(c.hours) == 24:
            return "hourly"
        m = re.fullmatch(r"\*/(\d+)", fields[0])
        if m and len(c.hours) == 24 and 1 < int(m.group(1)) < 60:
            return f"every {m.group(1)}m"
    if all_months and len(c.minutes) == 1:
        times = ",".join(f"{h:02d}:{c.minutes[0]:02d}" for h in c.hours)
        if free_days:
            if len(dows) == 7:
                return f"daily {times}"
            if dows == {1, 2, 3, 4, 5}:
                return f"weekdays {times}"
            if dows == {0, 6}:
                return f"weekends {times}"
            return f"{','.join(_DOW_ABBR[d] for d in sorted(dows))} {times}"
        if c.dow_star and len(dows) == 7:
            return f"monthly {','.join(_ordinal(d) for d in c.doms)} {times}"
    return f"cron {c.expr}"


# ---------------------------------------------------------------- natural language

_UNITS = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
          "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
          "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
          "d": 86400, "day": 86400, "days": 86400, "w": 604800, "week": 604800, "weeks": 604800}
_UNIT_RE = "|".join(sorted(_UNITS, key=len, reverse=True))
_HELP = ("try 'every 30m', 'daily at 9', 'weekdays 9:30', 'every monday at 7', 'monthly on the 1st at 9', "
         "'tomorrow 9am', 'in 2h', '2026-11-03 09:00' or a cron expression like '0 9 * * 1-5'")


def parse_duration(text):
    """`30m`, `2h`, `1d`, `90s`, `1.5h` or plain seconds -> seconds (int). Raises ValueError."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(" + _UNIT_RE + r")?\s*", str(text).lower())
    if not m:
        raise ValueError(f"bad duration {text!r} (use 30m, 2h, 1d)")
    return int(float(m.group(1)) * _UNITS[m.group(2) or "s"])


def _parse_time(tok):
    tok = tok.strip()
    if tok == "noon":
        return 12, 0
    if tok == "midnight":
        return 0, 0
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm|a\.m\.|p\.m\.)?", tok)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if mi > 59:
        raise ValueError(f"bad minutes in time {tok!r}")
    if ap:
        if not 1 <= h <= 12:
            raise ValueError(f"bad hour in time {tok!r}")
        h = h % 12 + (12 if ap.startswith("p") else 0)
    elif h > 23:
        raise ValueError(f"bad hour in time {tok!r}")
    return h, mi


def _split_time(tokens):
    """(rest, (h, m) or None): a trailing time token, with an `at` before it dropped."""
    if tokens:
        tm = _parse_time(tokens[-1])
        if tm is not None:
            rest = tokens[:-1]
            if rest and rest[-1] == "at":
                rest = rest[:-1]
            return rest, tm
    return tokens, None


def _cron_when(minute, hour, dom, dow, tz):
    return {"type": "cron", "expr": f"{minute} {hour} {dom} * {dow}", "tz": tz}


def _day_tokens(tokens):
    days = []
    for tok in tokens:
        if tok in ("and", "&", "on", "every", "each"):
            continue
        d = _DOWS.get(tok)
        if d is None and tok in ("tues", "weds", "thur", "thurs"):
            d = {"tues": 2, "weds": 3, "thur": 4, "thurs": 4}[tok]
        if d is None and tok.endswith("s"):
            d = _DOWS.get(tok[:-1])
        if d is None:
            return None
        days.append(d)
    return days or None


def _local_once(zone, now, day, tm):
    n = dt.datetime(day.year, day.month, day.day, tm[0], tm[1])
    return {"type": "once", "at": _naive_str(n), "tz": zone.name}


def _english(t, now, tz):
    zone = _zone(tz)
    t = re.sub(r"(\d)\s+(am|pm)\b", r"\1\2", t.replace(",", " "))
    t = " ".join(t.split())
    # ISO date and time, optionally with Z or an offset
    m = re.fullmatch(r"(?:once )?(\d{4}-\d{2}-\d{2})[t ](\d{1,2}:\d{2}(?::\d{2})?)(z|[+-]\d{2}:?\d{2})?", t)
    if m:
        n = _parse_naive(f"{m.group(1)}T{m.group(2)}")
        off = m.group(3)
        if off:
            sign = 0 if off == "z" else (1 if off[0] == "+" else -1)
            digits = off.lstrip("+-").replace(":", "") if off != "z" else "0000"
            secs = sign * (int(digits[:2]) * 3600 + int(digits[2:]) * 60)
            n = zone.naive(calendar.timegm(n.timetuple()) - secs)
        return {"type": "once", "at": _naive_str(n, n.second != 0), "tz": tz}
    m = re.fullmatch(r"(?:once )?in (?:(\d+(?:\.\d+)?)|an?) ?(" + _UNIT_RE + ")", t)
    if m:
        secs = int(float(m.group(1) or 1) * _UNITS[m.group(2)])
        if secs < 60:
            raise ValueError("'in' needs at least 1 minute")
        n = zone.naive(int(now) + secs)
        if n.second or n.microsecond:
            n = n.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
        return {"type": "once", "at": _naive_str(n), "tz": tz}
    tokens = t.split()
    if tokens and tokens[0] == "once":
        tokens = tokens[1:]
    rest, tm = _split_time(tokens)
    today = zone.naive(int(now)).date()
    if tm and len(rest) == 1 and rest[0] in ("today", "tomorrow"):
        return _local_once(zone, now, today + dt.timedelta(days=rest[0] == "tomorrow"), tm)
    if tokens and tokens[0] in ("today", "tomorrow") and tm is None:
        raise ValueError(f"{tokens[0]} needs a time, e.g. '{tokens[0]} 9am'")
    bare = len(tokens) == 1 and re.search(r"[:]|am$|pm$", tokens[0])
    if tm and (not rest or rest == ["once"]) and (bare or (len(tokens) >= 2 and tokens[-2] == "at")):
        day = today
        if zone.epoch(dt.datetime(day.year, day.month, day.day, tm[0], tm[1])) <= now:
            day += dt.timedelta(days=1)
        return _local_once(zone, now, day, tm)
    # recurring
    if tokens and tokens[0] == "every":
        tokens = tokens[1:]
    m = re.fullmatch(r"(\d+(?:\.\d+)?) ?(" + _UNIT_RE + ")", " ".join(tokens))
    if m:
        secs = int(float(m.group(1)) * _UNITS[m.group(2)])
        if secs < MIN_INTERVAL_S:
            raise ValueError(f"the shortest repeat is {MIN_INTERVAL_S // 60} minutes (every 5m)")
        return {"type": "interval", "every_s": secs, "anchor": int(now)}
    rest, tm = _split_time(tokens)
    head = rest[0] if rest else ""
    if head in ("hourly", "hour") and len(rest) == 1 and tm is None:
        return _cron_when("0", "*", "*", "*", tz)
    if head in ("daily", "day", "everyday") and len(rest) == 1:
        h, mi = tm or (0, 0)
        return _cron_when(mi, h, "*", "*", tz)
    if head in ("weekday", "weekdays") and len(rest) == 1:
        if tm is None:
            raise ValueError("weekdays needs a time, e.g. 'weekdays 9am'")
        return _cron_when(tm[1], tm[0], "*", "1-5", tz)
    if head in ("weekend", "weekends") and len(rest) == 1:
        if tm is None:
            raise ValueError("weekends needs a time, e.g. 'weekends 10:00'")
        return _cron_when(tm[1], tm[0], "*", "0,6", tz)
    if head in ("weekly", "week"):
        days = _day_tokens(rest[1:]) if len(rest) > 1 else [0]
        if days is None:
            raise ValueError("weekly: say the days like 'weekly on monday at 9'")
        h, mi = tm or (0, 0)
        return _cron_when(mi, h, "*", ",".join(str(d) for d in sorted(set(days))), tz)
    if head in ("monthly", "month"):
        extra = [x for x in rest[1:] if x not in ("on", "the", "day")]
        dom = 1
        if extra:
            mm = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?", extra[0]) if len(extra) == 1 else None
            if not mm or not 1 <= int(mm.group(1)) <= 31:
                raise ValueError("monthly: say the day like 'monthly on the 1st at 9'")
            dom = int(mm.group(1))
        h, mi = tm or (0, 0)
        return _cron_when(mi, h, str(dom), "*", tz)
    days = _day_tokens(rest)
    if days:
        if tm is None:
            raise ValueError("give a time of day, e.g. 'monday and friday 9am'")
        return _cron_when(tm[1], tm[0], "*", ",".join(str(d) for d in sorted(set(days))), tz)
    raise ValueError(f"cannot understand {t!r}; {_HELP}")


def parse_when(text, *, now, tz="local"):
    """Plain text -> `{"when": {...}, "next": [epochs]}` (3 fire times, 1 for a once schedule). A pure
    function of `now`: no LLM. Raises ValueError with a hint on anything it cannot read."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"empty schedule; {_HELP}")
    _zone(tz)
    t = " ".join(text.strip().lower().split())
    fields = t.split()
    when = None
    cron_err = None
    if t.startswith("@") or len(fields) == 5:
        try:
            parse_cron(t)
            when = {"type": "cron", "expr": t, "tz": tz}
        except ValueError as exc:
            if t.startswith("@") or fields[0][0] in "0123456789*":
                cron_err = exc
    if when is None:
        if cron_err is not None:
            raise cron_err
        when = _english(t, now, tz)
    when = validate_when(when)
    if when["type"] == "once":
        e = next_fire(when, now)
        if e is None:
            raise ValueError(f"{describe(when)} has already passed")
        return {"when": when, "next": [e]}
    return {"when": when, "next": next_fires(when, int(now), 3)}


# ---------------------------------------------------------------- validation

def _no_unknown(d, allowed, where):
    for k in d:
        if k not in allowed:
            raise ValueError(f"unknown key {k!r} in {where} (allowed: {', '.join(allowed)})")


def _int(v, name, lo, hi):
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError(f"{name} must be a whole number")
    if not lo <= v <= hi:
        raise ValueError(f"{name} must be between {lo} and {hi}")
    return v


def _bool(v, name):
    if not isinstance(v, bool):
        raise ValueError(f"{name} must be true or false")
    return v


def _choice(v, allowed, name):
    if v not in allowed:
        raise ValueError(f"{name} must be one of {', '.join(allowed)}")
    return v


def _line(v, name, maxlen, none_ok=False):
    if v is None and none_ok:
        return None
    if not isinstance(v, str) or not v.strip() or len(v) > maxlen:
        raise ValueError(f"{name} must be text of 1 to {maxlen} characters")
    if any(unicodedata.category(c) == "Cc" for c in v):
        raise ValueError(f"{name} must not contain control characters")
    return v.strip()


def _section(rec, key, defaults):
    sec = rec.get(key, {})
    if not isinstance(sec, dict):
        raise ValueError(f"{key} must be an object")
    _no_unknown(sec, tuple(defaults), key)
    out = copy.deepcopy(defaults)
    out.update(copy.deepcopy(sec))
    return out


def _abs_path(v, name):
    s = _line(v, name, 4096)
    if not os.path.isabs(os.path.expanduser(s)):
        raise ValueError(f"{name} must be an absolute path (got {s!r})")
    return s


def _str_list(v, name, maxn, maxlen=200):
    if not isinstance(v, list) or len(v) > maxn:
        raise ValueError(f"{name} must be a list of at most {maxn} items")
    out = []
    for x in v:
        s = _line(x, name, maxlen)
        if s not in out:
            out.append(s)
    return out


_TOP_KEYS = ("id", "name", "kind", "enabled", "when", "task", "run", "policy", "identity", "notify", "origin",
             "created", "updated")
_RUN_DEFAULTS = {"cwd": "~", "account": None, "model": None, "thinking": "medium", "mode": "headless",
                 "permissions": "edit", "max_time_s": 1800, "max_cost_usd": 2.0, "add_dirs": []}
_POLICY_DEFAULTS = {"catchup": "latest", "grace_s": 7200, "overlap": "skip", "retries": 2,
                    "backoff_s": [300, 1800], "quota_guard_pct": 90, "fallback_accounts": [],
                    "pause_after_failures": 5}
_IDENTITY_DEFAULTS = {"system": "", "memory": False, "continuity": "fresh", "memory_max_bytes": 8192}
_NOTIFY_DEFAULTS = {"on": ["failed", "limit", "missed"], "done": False}


def validate(rec, *, origin_rules=True):
    """Check a schedule record, fill every default and return the normalized deep copy. Raises ValueError
    (unknown keys included). With `origin_rules`, `permissions: "full"` is refused for origin "phone". A
    `full` record must carry `max_time_s` and `max_cost_usd` in `run` explicitly."""
    if not isinstance(rec, dict):
        raise ValueError("a schedule must be an object")
    _no_unknown(rec, _TOP_KEYS, "schedule")
    sid = rec.get("id")
    if not isinstance(sid, str) or not ID_RE.match(sid):
        raise ValueError("id must be 3 to 32 characters of a-z, 0-9 and '-'")
    kind = _choice(rec.get("kind", "task"), KINDS, "kind")
    origin = _choice(rec.get("origin", "cli"), ORIGINS, "origin")
    out = {"id": sid, "name": _line(rec.get("name"), "name", MAX_NAME), "kind": kind,
           "enabled": _bool(rec.get("enabled", True), "enabled"), "when": validate_when(rec.get("when"))}
    task = _section(rec, "task", {"prompt": ""})
    prompt = task["prompt"]
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("task.prompt is required" + (" (an agent needs its standing task)" if kind == "agent" else ""))
    if len(prompt) > MAX_PROMPT:
        raise ValueError(f"task.prompt is longer than {MAX_PROMPT} characters")
    out["task"] = {"prompt": prompt}
    run = _section(rec, "run", _RUN_DEFAULTS)
    explicit = rec.get("run") or {}
    out["run"] = {
        "cwd": _abs_path(run["cwd"], "run.cwd"),
        "account": _line(run["account"], "run.account", 100, none_ok=True),
        "model": _line(run["model"], "run.model", 200, none_ok=True),
        "thinking": _choice(run["thinking"], THINKING, "run.thinking"),
        "mode": _choice(run["mode"], MODES, "run.mode"),
        "permissions": _choice(run["permissions"], PERMISSIONS, "run.permissions"),
        "max_time_s": _int(run["max_time_s"], "run.max_time_s", 60, 86400),
        "max_cost_usd": run["max_cost_usd"],
        "add_dirs": [_abs_path(x, "run.add_dirs") for x in _str_list(run["add_dirs"], "run.add_dirs", 16, 4096)],
    }
    cost = out["run"]["max_cost_usd"]
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not 0 < cost <= 100:
        raise ValueError("run.max_cost_usd must be a number above 0 and at most 100")
    out["run"]["max_cost_usd"] = float(cost)
    if out["run"]["permissions"] == "full":
        if "max_time_s" not in explicit or "max_cost_usd" not in explicit:
            raise ValueError("permissions 'full' needs max_time_s and max_cost_usd set explicitly")
        if origin_rules and origin == "phone":
            raise ValueError("a phone cannot create or edit a schedule with permissions 'full'")
    pol = _section(rec, "policy", _POLICY_DEFAULTS)
    backoff = pol["backoff_s"]
    if not isinstance(backoff, list) or not backoff or len(backoff) > 10:
        raise ValueError("policy.backoff_s must be a list of 1 to 10 delays in seconds")
    out["policy"] = {
        "catchup": _choice(pol["catchup"], CATCHUP, "policy.catchup"),
        "grace_s": _int(pol["grace_s"], "policy.grace_s", 0, DUE_WINDOW_S),
        "overlap": _choice(pol["overlap"], OVERLAP, "policy.overlap"),
        "retries": _int(pol["retries"], "policy.retries", 0, 10),
        "backoff_s": [_int(b, "policy.backoff_s", 0, 86400) for b in backoff],
        "quota_guard_pct": _int(pol["quota_guard_pct"], "policy.quota_guard_pct", 1, 100),
        "fallback_accounts": _str_list(pol["fallback_accounts"], "policy.fallback_accounts", 8, 100),
        "pause_after_failures": _int(pol["pause_after_failures"], "policy.pause_after_failures", 0, 100),
    }
    ident = _section(rec, "identity", _IDENTITY_DEFAULTS)
    system = ident["system"]
    if not isinstance(system, str) or len(system) > 8000:
        raise ValueError("identity.system must be text of at most 8000 characters")
    explicit_ident = rec.get("identity") or {}
    out["identity"] = {
        "system": system,
        "memory": True if kind == "agent" else _bool(ident["memory"], "identity.memory"),
        # an agent has one persistent session: it continues it unless `fresh` is written explicitly
        "continuity": _choice(ident["continuity"] if "continuity" in explicit_ident
                              else ("resume" if kind == "agent" else "fresh"), CONTINUITY, "identity.continuity"),
        "memory_max_bytes": _int(ident["memory_max_bytes"], "identity.memory_max_bytes", 512, 65536),
    }
    if kind == "agent":
        _bool(ident["memory"], "identity.memory")
    nt = _section(rec, "notify", _NOTIFY_DEFAULTS)
    on = nt["on"]
    if not isinstance(on, list) or any(x not in NOTIFY_ON for x in on):
        raise ValueError(f"notify.on must be a list of {', '.join(NOTIFY_ON)}")
    done = _bool(nt["done"], "notify.done") or "done" in on
    on = [x for x in NOTIFY_ON if x in on or (x == "done" and done)]
    out["notify"] = {"on": on, "done": done}
    out["origin"] = origin
    out["created"] = _int(rec.get("created", 0), "created", 0, 1 << 40)
    out["updated"] = _int(rec.get("updated", 0), "updated", 0, 1 << 40)
    return out


def _slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower())
    return s.strip("-")


def new_id(name, existing=(), rng=None):
    """`<slug of name>-<3 random chars>`, at most 32 characters, not in `existing`."""
    rng = rng or random.SystemRandom()
    slug = _slug(name)[:28].strip("-") or "bot"
    while True:
        sid = slug + "-" + "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(3))
        if sid not in existing and ID_RE.match(sid):
            return sid


def new_schedule(spec, *, now=None, existing_ids=(), rng=None, origin="cli"):
    """Validate `spec` (a record without id, created, updated) and complete it: generated id, timestamps.
    The explicit-keys rule for `full` applies to `spec`'s run section."""
    if not isinstance(spec, dict):
        raise ValueError("a schedule must be an object")
    now = int(time.time() if now is None else now)
    rec = copy.deepcopy(spec)
    rec.setdefault("origin", origin)
    if "id" not in rec:
        rec["id"] = new_id(rec.get("name") if isinstance(rec.get("name"), str) else "", existing_ids, rng)
    rec["created"] = rec["updated"] = now
    return validate(rec)


# ---------------------------------------------------------------- store

def _merge_changes(rec, changes):
    out = copy.deepcopy(rec)
    for k, v in changes.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "when":
            out[k].update(copy.deepcopy(v))
        else:
            out[k] = copy.deepcopy(v)
    return out


class Store:
    """`schedules.json`: `{"version": 1, "schedules": [...]}`. Reads are cached by (mtime, size, inode);
    every change runs under an exclusive flock on `schedules.json.lock` and is written atomically (0600).
    Records that fail validation are kept untouched in the file, skipped by `read()` and listed in
    `errors` as `(id, message)`."""

    def __init__(self, path=None, env=None):
        self.path = path or schedules_path(env)
        self.errors = []
        self._sig = None
        self._recs = []

    def _load_raw(self):
        text = _read_text_trusted(self.path)
        if text is None:
            return []
        try:
            doc = json.loads(text)
        except ValueError as exc:
            raise ScheduleError(f"{self.path} is not valid JSON ({exc})") from None
        if not isinstance(doc, dict) or not isinstance(doc.get("schedules"), list):
            raise ScheduleError(f"{self.path} must be an object with a 'schedules' list")
        if not isinstance(doc.get("version"), int) or doc["version"] > SCHEMA:
            raise ScheduleError(f"{self.path} was written by a newer Hivegrid (version {doc.get('version')!r})")
        return doc["schedules"]

    def _split(self, raw):
        good, bad, errors, seen = [], [], [], set()
        for r in raw:
            try:
                v = validate(r)
                if v["id"] in seen:
                    raise ValueError(f"duplicate id {v['id']}")
                seen.add(v["id"])
                good.append(v)
            except ValueError as exc:
                rid = r.get("id") if isinstance(r, dict) else None
                errors.append((rid if isinstance(rid, str) else "?", str(exc)))
                bad.append(r)
        return good, bad, errors

    def read(self):
        """The valid schedules (deep copies), re-read when the file changed."""
        try:
            st = os.lstat(self.path)
            sig = (st.st_mtime_ns, st.st_size, st.st_ino)
        except FileNotFoundError:
            sig = None
        except OSError as exc:
            raise ScheduleError(f"cannot read {self.path}: {exc.strerror}") from None
        if sig != self._sig or sig is None:
            good, _bad, self.errors = self._split(self._load_raw())
            self._recs, self._sig = good, sig
        return copy.deepcopy(self._recs)

    def get(self, schedule_id):
        for r in self.read():
            if r["id"] == schedule_id:
                return r
        return None

    def mutate(self, fn):
        """Under the lock: call `fn(list_of_valid_records)` (edit it in place), validate, write. Returns
        what `fn` returns."""
        d = os.path.dirname(self.path)
        ensure_private_dir(d)
        with locked(self.path + ".lock"):
            good, bad, _errors = self._split(self._load_raw())
            result = fn(good)
            checked, seen = [], set()
            for r in good:
                v = validate(r)
                if v["id"] in seen:
                    raise ValueError(f"duplicate id {v['id']}")
                seen.add(v["id"])
                checked.append(v)
            doc = {"version": SCHEMA, "schedules": checked + bad}
            atomic_write(self.path, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
            self._sig = None
            return result

    def add(self, spec, *, now=None, rng=None, origin="cli"):
        """Create a schedule from `spec` (see new_schedule); returns the stored record."""
        def fn(recs):
            rec = new_schedule(spec, now=now, existing_ids={r["id"] for r in recs}, rng=rng, origin=origin)
            recs.append(rec)
            return copy.deepcopy(rec)
        return self.mutate(fn)

    def change(self, schedule_id, changes, *, now=None, via=None):
        """Apply `changes` (top-level keys; a section such as `run` merges key by key, `when` is replaced).
        `via` names who edits ("phone" may not make a schedule `full`). Returns the new record."""
        now = int(time.time() if now is None else now)

        def fn(recs):
            for i, r in enumerate(recs):
                if r["id"] == schedule_id:
                    if "id" in changes and changes["id"] != schedule_id:
                        raise ValueError("the id cannot change")
                    cand = _merge_changes(r, changes)
                    cand["updated"] = now
                    if via:
                        validate(dict(cand, origin=via))
                    recs[i] = validate(cand)
                    return copy.deepcopy(recs[i])
            raise KeyError(schedule_id)
        return self.mutate(fn)

    def remove(self, schedule_id):
        """Delete the definition (the state directory stays); returns whether it existed."""
        def fn(recs):
            n = len(recs)
            recs[:] = [r for r in recs if r["id"] != schedule_id]
            return len(recs) != n
        return self.mutate(fn)


# ---------------------------------------------------------------- state

def default_state():
    """`state.json` of a schedule. `last_slot` (epoch) and `last_slot_local` (naive slot string) mark the
    newest handled slot; `baseline` bounds the slots a resumed schedule may catch up on; `running` is
    `{run, pid, started}` (plus `chat: true` for a chat turn) while a run is live; `queued` / `run_now`
    are requests; `next_override` is a self-set next run; `done` is set by the agent. `session_file` is the
    bot's main session (the newest session a run or a chat turn used); `chat_queue` holds the messages
    waiting for the bot (`[{text, ts}]`, at most 5) and `chat` the state of the talk:
    `{active, started, error}`."""
    return {"version": SCHEMA, "last_slot": None, "last_slot_local": None, "baseline": None, "run_now": False,
            "queued": False, "running": None, "run_count": 0, "consec_failures": 0, "paused_reason": None,
            "next_override": None, "done": False, "chat_queue": [],
            "chat": {"active": False, "started": None, "error": None}}


def load_state(sdir):
    """The state of a schedule directory merged over the defaults (missing or unreadable file: defaults)."""
    st = default_state()
    try:
        text = _read_text_trusted(os.path.join(sdir, "state.json"), 1 << 20)
        doc = json.loads(text) if text else None
    except (ScheduleError, ValueError):
        doc = None
    if isinstance(doc, dict):
        st.update(doc)
    return st


def save_state(sdir, state):
    """Atomic write of `state.json` (no lock: use update_state for read-modify-write)."""
    ensure_private_dir(sdir)
    atomic_write(os.path.join(sdir, "state.json"), json.dumps(state, indent=1) + "\n")


def update_state(sdir, fn):
    """Locked read-modify-write of `state.json`: `fn(state)` edits or returns a new dict. Returns the
    saved state. Daemon and CLI both go through this."""
    ensure_private_dir(sdir)
    with locked(os.path.join(sdir, ".lock")):
        st = load_state(sdir)
        new = fn(st)
        st = st if new is None else new
        save_state(sdir, st)
        return st


def memory_path(schedule_id, env=None):
    return os.path.join(state_dir(schedule_id, env), "memory.md")


def report_path(schedule_id, run_no, env=None):
    """Where run number `run_no` writes its final report."""
    return os.path.join(state_dir(schedule_id, env), "reports", f"{int(run_no):04d}.md")


def init_state_dir(sched, now=None, env=None):
    """Create the state directory of a schedule (0700) with `notes/`, `reports/`, `identity.md`, an empty
    `memory.md` when it has memory, and a `state.json` whose baseline is `now`. Existing files stay."""
    sdir = state_dir(sched["id"], env)
    ensure_private_dir(state_root(env))
    ensure_private_dir(sdir)
    for sub in ("notes", "reports"):
        ensure_private_dir(os.path.join(sdir, sub))
    sync_identity(sched, env)
    if sched["identity"]["memory"] and not os.path.exists(os.path.join(sdir, "memory.md")):
        atomic_write(os.path.join(sdir, "memory.md"), "")
    if not os.path.exists(os.path.join(sdir, "state.json")):
        st = default_state()
        st["baseline"] = int(time.time() if now is None else now)
        save_state(sdir, st)
    return sdir


def sync_identity(sched, env=None, visible=False):
    """Write `identity.md` (`identity-visible.md` for a visible run), the text for `--append-system-prompt`, when it
    differs from identity_text."""
    path = os.path.join(state_dir(sched["id"], env), "identity-visible.md" if visible else "identity.md")
    text = identity_text(sched, visible)
    try:
        if _read_text_trusted(path, 1 << 20) == text:
            return path
    except ScheduleError:
        pass
    ensure_private_dir(os.path.dirname(path))
    atomic_write(path, text)
    return path


def is_active(sched, state):
    """Whether regular slots may fire: enabled, not finished by the agent, not paused by the breaker."""
    return bool(sched["enabled"]) and not state.get("done") and not state.get("paused_reason")


def advance(sched, state, slot, trigger):
    """The state after a slot was handled (a run started, or recorded as missed or skipped). `manual`
    clears the run-now request; `retry` changes nothing; every other trigger (and "missed") moves
    `last_slot` to `slot` and clears a self-set `next_override` that `slot` reached."""
    st = copy.deepcopy(state)
    if trigger == "manual":
        st["run_now"] = False
        return st
    if trigger == "retry":
        return st
    if st.get("last_slot") is None or slot > st["last_slot"]:
        st["last_slot"] = int(slot)
        st["last_slot_local"] = _slot_local(sched["when"], slot)
    ov = st.get("next_override")
    if ov is not None and slot >= ov:
        st["next_override"] = None
    return st


def skip_next(sched, state, now):
    """The state with the next scheduled slot marked handled, so it never fires. What the skip changed is
    remembered in `state["skips"]` (newest last, at most 5) so `unskip` can take it back."""
    after = max(int(now), state.get("last_slot") or 0)
    slot = next_fire(sched["when"], after, state.get("last_slot_local"))
    if slot is None:
        return state
    st = advance(sched, state, slot, "cron")
    skips = [s for s in (state.get("skips") or []) if isinstance(s, dict)][-4:]
    st["skips"] = skips + [{"slot": int(slot), "last_slot": state.get("last_slot"),
                            "last_slot_local": state.get("last_slot_local")}]
    return st


def skipped_slot(sched, state, now):
    """The epoch of the skipped run that `unskip` would bring back, else None. Only a skip that is still
    ahead counts, and only while nothing was handled after it (a later run, skip or missed slot)."""
    skips = state.get("skips")
    top = skips[-1] if isinstance(skips, list) and skips and isinstance(skips[-1], dict) else None
    if top is None or not isinstance(top.get("slot"), int) or top["slot"] <= int(now):
        return None
    return top["slot"] if state.get("last_slot") == top["slot"] else None


def unskip(sched, state, now):
    """The state with the newest skip undone: the skipped slot is due again. The state is returned unchanged
    when `skipped_slot` finds nothing to undo."""
    slot = skipped_slot(sched, state, now)
    if slot is None:
        return state
    st = copy.deepcopy(state)
    top = st["skips"].pop()
    st["last_slot"], st["last_slot_local"] = top.get("last_slot"), top.get("last_slot_local")
    if not st["skips"]:
        del st["skips"]
    return st


def next_run(sched, state, now):
    """The epoch of the next run (a self-set override wins), or None when disabled, finished or over."""
    if not sched["enabled"] or state.get("done"):
        return None
    ov = state.get("next_override")
    if ov is not None:
        return int(ov)
    after = max(int(now), state.get("last_slot") or 0)
    return next_fire(sched["when"], after, state.get("last_slot_local"))


def record_outcome(sched, state, rec):
    """The state after a finished run: `ok` resets `consec_failures`; failed, limit, timeout and cost add
    one; other statuses leave it. Sets `paused_reason` when breaker() trips."""
    st = copy.deepcopy(state)
    status = rec.get("status")
    if status == "ok":
        st["consec_failures"] = 0
    elif status in ("failed", "limit", "timeout", "cost"):
        st["consec_failures"] = int(st.get("consec_failures") or 0) + 1
    if breaker(st, sched) and not st.get("paused_reason"):
        st["paused_reason"] = f"{st['consec_failures']} runs in a row did not succeed"
    return st


# ---------------------------------------------------------------- policy

def _regular_due(sched, state, now):
    when = sched["when"]
    base = max(state.get("last_slot") or 0, state.get("baseline") or 0, sched.get("created") or 0)
    after = max(base, int(now) - DUE_WINDOW_S)
    out, last = [], state.get("last_slot_local")
    while len(out) < DUE_LIMIT:
        e = next_fire(when, after, last)
        if e is None or e > now:
            break
        out.append(e)
        after = e
    return out


def due(sched, state, now):
    """Slots (epochs, ascending) that are due at `now` and not handled yet: those after `last_slot`
    (and after the baseline and creation), at most 7 days back. A self-set `next_override` replaces the
    regular slots until it fired. Empty unless is_active()."""
    if not is_active(sched, state):
        return []
    ov = state.get("next_override")
    if ov is not None:
        return [int(ov)] if ov <= now else []
    return _regular_due(sched, state, now)


def plan_catchup(sched, state, now):
    """What to do with the due slots: `("fire", slot, trigger)` (trigger `manual`, `self`, `cron` or
    `catchup`), `("missed", [slots])` to record as missed, or None when nothing is due. A slot noticed
    within ONTIME_S of its time is a normal fire. Later (downtime), misses collapse into one `catchup`
    run of the newest slot when it is within `grace_s` and the policy is `latest`; otherwise they are
    `missed`. A once schedule missed by more than 24 h is `missed` (it stays enabled for a manual run).
    `run_now` always wins."""
    if state.get("run_now"):
        return ("fire", int(now), "manual")
    slots = due(sched, state, now)
    if not slots:
        return None
    newest = slots[-1]
    own = "self" if state.get("next_override") is not None else "cron"
    late = now - newest
    if late <= ONTIME_S:
        return ("fire", newest, own)
    if sched["policy"]["catchup"] == "skip":
        return ("missed", slots)
    limit = ONCE_MISSED_S if sched["when"]["type"] == "once" else sched["policy"]["grace_s"]
    if late <= limit:
        return ("fire", newest, "self" if own == "self" else "catchup")
    return ("missed", slots)


def overlap_action(sched, state):
    """`run` when nothing runs, else per policy `skip`, or `queue` (one queued run at most, then skip). A
    live chat turn always makes a due slot wait (`queue`, never lost; a second one is `skip`)."""
    running = state.get("running")
    if not running:
        return "run"
    chat = isinstance(running, dict) and running.get("chat")
    if (chat or sched["policy"]["overlap"] == "queue") and not state.get("queued"):
        return "queue"
    return "skip"


def retry_plan(sched, run_record, attempt, now, jitter=None):
    """Epoch of the next attempt, or None. Only failed, limit and timeout are retried (never cost) and only
    while `attempt` (the attempt that just ended, from 1) is at most `policy.retries`. A `limit` with
    `reset_at` retries at that time plus jitter (0 to 120 s; `jitter` is a number or a callable returning
    one, for tests); otherwise `now + backoff_s[attempt - 1]`, the last entry repeating."""
    status = run_record.get("status")
    pol = sched["policy"]
    if status not in ("failed", "limit", "timeout") or attempt > pol["retries"]:
        return None
    reset = run_record.get("reset_at")
    if status == "limit" and reset:
        j = random.randint(0, 120) if jitter is None else (jitter() if callable(jitter) else jitter)
        return int(max(reset + j, now))
    back = pol["backoff_s"]
    return int(now + back[min(max(attempt, 1), len(back)) - 1])


def breaker(state, sched):
    """True when `consec_failures` reached `pause_after_failures` (0 never pauses)."""
    n = sched["policy"]["pause_after_failures"]
    return n > 0 and int(state.get("consec_failures") or 0) >= n


# ---------------------------------------------------------------- runs

_RUN_KEYS = ("run", "schedule", "trigger", "mode", "started", "ended", "status", "attempt", "exit", "profile",
             "model", "session_file", "session_id", "cost_usd", "tokens", "summary", "report_path", "error",
             "reset_at", "seen", "level", "headline", "mode_fallback", "takeover")

_BIDI = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_STATUS_WORDS = {"failed": "failed", "limit": "hit its usage limit", "timeout": "timed out",
                 "cost": "hit its cost limit", "missed": "missed a run", "deferred": "was deferred",
                 "cancelled": "was cancelled"}


def clean_line(text, limit=HEADLINE_MAX):
    """`text` as one printable line: control and bidi characters become a space, whitespace collapses, the
    result is cut at `limit` characters (with an ellipsis)."""
    s = _BIDI.sub("", "".join(" " if unicodedata.category(c) == "Cc" else c for c in str(text or "")))
    s = " ".join(s.split())
    return s if len(s) <= limit else s[:max(limit - 1, 0)].rstrip() + "\u2026"


def auto_headline(name, rec):
    """The headline of a run that needs none from the agent: `<name> failed: <error first line>`, `<name> hit
    its usage limit`, `<name> timed out: ...`, `<name> missed a run: ...`. Without a `name` it reads `Run ...`."""
    status = rec.get("status")
    who = clean_line(name, 60) if name else "Run"
    text = f"{who} {_STATUS_WORDS.get(status, status or 'ended')}"
    detail = ""
    if status in ("failed", "timeout", "cancelled"):
        lines = (rec.get("error") or "").strip().splitlines()
        detail = lines[0] if lines else ""
    elif status in ("missed", "deferred"):
        lines = (rec.get("summary") or rec.get("error") or "").strip().splitlines()
        detail = lines[0] if lines else ""
    if detail:
        text += f": {detail}"
    return clean_line(text, HEADLINE_MAX)


def effective_level(status, trigger, error, given=None):
    """The level a run really has. A chat turn and a skipped or user-cancelled run are `none`; failed, limit,
    timeout, cost, missed, deferred and a run the scheduler stopped are always `important`; every other run
    has the level the agent gave (`info` when it gave none)."""
    if trigger == "chat" or status == "skipped":
        return "none"
    if status == "cancelled":
        return "none" if (error or "").startswith(CANCELLED_BY_USER) else "important"
    if status in OVERRIDE_STATUS:
        return "important"
    return given if given in LEVELS else "info"


_LEVEL_LINE = re.compile(r"^[\s>*_`#-]*level[\s*_`]*[:=][\s*_`]*([A-Za-z]+)[\s*_`.]*$", re.I)
_HEADLINE_LINE = re.compile(r"^[\s>*_`#-]*headline[\s*_`]*[:=][\s*_`]*(.*?)\s*$", re.I)


def _first_sentence(text, limit=HEADLINE_MAX):
    for line in text.splitlines():
        line = re.sub(r"^[\s>#*_`\u2022-]+", "", line).strip()
        if line:
            m = re.match(r"(.+?[.!?])(?:\s|$)", line)
            return clean_line(m.group(1) if m else line, limit)
    return ""


def parse_report(text, kind="agent"):
    """Split a report into `{level, headline, body, marked}`. The agent states its level on the first line
    (`level: none|info|important`) and a one-line `headline:` on the second, then a blank line and the body.
    Tolerant: case-insensitive, markdown emphasis and one fence around the header are stripped, the two lines
    may come in either order. A missing or unknown level is `important` for a `task` (its result is what the
    user wanted) and `info` for an agent; a missing headline is the first sentence of the body (<= 140
    characters, one line). `marked` says whether a header was found; `body` is then what follows it, else
    the whole text."""
    s = text.replace("\r\n", "\n").strip() if isinstance(text, str) else ""
    lines = s.split("\n") if s else []
    fenced = bool(lines) and lines[0].strip().startswith("```")
    work = lines[1:] if fenced else lines
    level = headline = None
    found, i = False, 0
    while i < len(work):
        ln = work[i]
        if not ln.strip():
            if found:
                break
            i += 1
            continue
        m = _LEVEL_LINE.match(ln)
        if m and level is None:
            level = m.group(1).lower() if m.group(1).lower() in LEVELS else ""
            found = True
            i += 1
            continue
        m = _HEADLINE_LINE.match(ln)
        if m and headline is None:
            headline = m.group(1)
            found = True
            i += 1
            continue
        break
    if fenced and not found:
        work, i = lines, 0
    rest = work[i:]
    if fenced and found:
        while rest and not rest[0].strip():
            rest = rest[1:]
        if rest and rest[0].strip() == "```":
            rest = rest[1:]
        elif rest and rest[-1].strip() == "```":
            rest = rest[:-1]
    body = "\n".join(rest).strip()
    level = level or ("important" if kind == "task" else "info")
    headline = clean_line(headline, HEADLINE_MAX) if headline and headline.strip() else _first_sentence(body)
    return {"level": level, "headline": headline, "body": body, "marked": found}


def _opt_num(v, name):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{name} must be a number or null")
    return v


def _opt_str(v, name, limit):
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError(f"{name} must be text or null")
    return v[:limit]


def normalize_run(rec, name=None):
    """Validate a run record, fill defaults, cut `summary` to 1000 characters. `level` (`none|info|important`)
    and `headline` (one line, <= 140 characters) are completed by effective_level(): the status can force
    `important` (and an automatic headline), a chat turn or a skipped run is `none`. `seen` defaults to False
    (True for skipped, deferred and cancelled runs, which need no reading, and for level `none`). Raises
    ValueError."""
    if not isinstance(rec, dict):
        raise ValueError("a run record must be an object")
    _no_unknown(rec, _RUN_KEYS, "run record")
    sid = rec.get("schedule")
    if not isinstance(sid, str) or not ID_RE.match(sid):
        raise ValueError("run record needs a valid schedule id")
    status = _choice(rec.get("status"), RUN_STATUS, "status")
    trigger = _choice(rec.get("trigger"), TRIGGERS, "trigger")
    error = _opt_str(rec.get("error"), "error", 2000)
    given = rec.get("level")
    if given is not None:
        _choice(given, LEVELS, "level")
    level = effective_level(status, trigger, error, given)
    headline = _opt_str(rec.get("headline"), "headline", 2000)
    headline = clean_line(headline, HEADLINE_MAX) if headline and headline.strip() else None
    if level == "important" and status in OVERRIDE_STATUS + ("cancelled",):
        # the status decides what the user reads, whatever the agent wrote: "<name> failed: <first error line>"
        headline = auto_headline(name, {"status": status, "error": error, "summary": rec.get("summary")})
    tokens = rec.get("tokens")
    if isinstance(tokens, bool) or (tokens is not None and not isinstance(tokens, (int, dict))):
        raise ValueError("tokens must be a number, an object or null")
    out = {
        "run": _int(rec.get("run"), "run", 0, 1 << 31),
        "schedule": sid,
        "trigger": trigger,
        "mode": _opt_str(rec.get("mode"), "mode", 20),
        "started": _opt_num(rec.get("started"), "started"),
        "ended": _opt_num(rec.get("ended"), "ended"),
        "status": status,
        "attempt": _int(rec.get("attempt", 1), "attempt", 1, 1000),
        "exit": rec.get("exit") if isinstance(rec.get("exit"), int) and not isinstance(rec.get("exit"), bool) else None,
        "profile": _opt_str(rec.get("profile"), "profile", 200),
        "model": _opt_str(rec.get("model"), "model", 200),
        "session_file": _opt_str(rec.get("session_file"), "session_file", 4096),
        "session_id": _opt_str(rec.get("session_id"), "session_id", 200),
        "cost_usd": _opt_num(rec.get("cost_usd"), "cost_usd"),
        "tokens": copy.deepcopy(tokens),
        "summary": _opt_str(rec.get("summary"), "summary", SUMMARY_MAX),
        "report_path": _opt_str(rec.get("report_path"), "report_path", 4096),
        "error": error,
        "reset_at": _opt_num(rec.get("reset_at"), "reset_at"),
    }
    if rec.get("mode_fallback"):    # why a visible or hybrid run was headless / could not open its pane
        out["mode_fallback"] = _opt_str(rec["mode_fallback"], "mode_fallback", 300)
    tk = rec.get("takeover")        # a hybrid run's pane the user took over in: {"pane": id}
    if isinstance(tk, dict) and isinstance(tk.get("pane"), int) and not isinstance(tk["pane"], bool):
        out["takeover"] = {"pane": tk["pane"]}
    out["level"], out["headline"] = level, headline
    seen = rec.get("seen")
    out["seen"] = (status in NO_ATTENTION_STATUS or level == "none") if seen is None else _bool(seen, "seen")
    return out


def read_runs(sdir, limit=None):
    """Run records of a schedule directory, oldest first (corrupt lines are skipped); the last `limit`."""
    try:
        text = _read_text_trusted(os.path.join(sdir, "runs.jsonl"), 64 * 1024 * 1024)
    except ScheduleError:
        return []
    out = []
    for line in (text or "").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out[-limit:] if limit else out


def append_run(sdir, rec, name=None):
    """Append one record to `runs.jsonl`; beyond 200 records the file is rewritten atomically with the
    newest 200, chat turns going first (and the reports of dropped runs are deleted). `name` is the bot's name,
    used for the automatic headline of a failed run. Returns the normalized record."""
    rec = normalize_run(rec, name)
    ensure_private_dir(sdir)
    path = os.path.join(sdir, "runs.jsonl")
    with locked(os.path.join(sdir, ".lock")):
        runs = read_runs(sdir)
        runs.append(rec)
        if len(runs) > MAX_RUNS:
            drop = len(runs) - MAX_RUNS
            chats = [i for i, r in enumerate(runs) if r.get("trigger") == "chat"][:drop]
            gone = set(chats)
            rest = [i for i in range(len(runs)) if i not in gone][:drop - len(chats)]
            gone.update(rest)
            runs = [r for i, r in enumerate(runs) if i not in gone]
            atomic_write(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in runs))
            _prune_reports(sdir, min(r.get("run", 0) for r in runs if isinstance(r.get("run"), int)))
        else:
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                os.write(fd, (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))
            finally:
                os.close(fd)
    return rec


def _prune_reports(sdir, oldest_run):
    rdir = os.path.join(sdir, "reports")
    try:
        names = os.listdir(rdir)
    except OSError:
        return
    for n in names:
        m = re.fullmatch(r"(\d{4,})\.md", n)
        if m and int(m.group(1)) < oldest_run:
            try:
                os.unlink(os.path.join(rdir, n))
            except OSError:
                pass


def _state_dirs(env):
    root = state_root(env)
    try:
        return sorted(os.path.join(root, n) for n in os.listdir(root)
                      if ID_RE.match(n) and os.path.isdir(os.path.join(root, n)))
    except OSError:
        return []


def unseen(sdir=None, *, env=None):
    """Run records not yet marked seen: of one schedule directory, or of all schedules when `sdir` is
    None. Oldest first."""
    out = []
    for d in ([sdir] if sdir else _state_dirs(env)):
        out += [r for r in read_runs(d) if r.get("seen") is False]
    out.sort(key=lambda r: (r.get("ended") or r.get("started") or 0, r.get("run") or 0))
    return out


def _mark_dir(sdir, pred, seen=True):
    """Set `seen` to `seen` on the runs of a state directory that have the opposite value and for which `pred(record)`
    holds; returns how many changed."""
    if not os.path.isdir(sdir):
        return 0
    ensure_private_dir(sdir)
    with locked(os.path.join(sdir, ".lock")):
        runs = read_runs(sdir)
        n = 0
        for r in runs:
            if r.get("seen") is (not seen) and pred(r):
                r["seen"] = seen
                n += 1
        if n:
            atomic_write(os.path.join(sdir, "runs.jsonl"),
                         "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in runs))
    return n


def mark_seen(schedule_id, run=None, *, env=None):
    """Mark one run (or every run when `run` is None) of a schedule seen; returns how many changed."""
    return _mark_dir(state_dir(schedule_id, env), lambda r: run is None or r.get("run") == run)


def mark_unseen(schedule_id, run, *, env=None):
    """Undo `mark_seen` for one run (the briefing's undo); returns how many changed."""
    return _mark_dir(state_dir(schedule_id, env), lambda r: r.get("run") == run, seen=False)


# ---------------------------------------------------------------- briefing

def _is_important(r):
    return r.get("trigger") != "chat" and effective_level(r.get("status"), r.get("trigger"), r.get("error"),
                                                          r.get("level")) == "important"


def _run_headline(name, r):
    """The headline of a run record: a failed, limit, timeout, cost, missed, deferred or cancelled run reads
    `<name> failed: ...` (always built from the status, so it names the bot whatever was stored), else the
    stored one, else the first sentence of the summary (records from before levels existed)."""
    if r.get("status") in OVERRIDE_STATUS + ("cancelled",):
        return auto_headline(name, r)
    h = clean_line(r.get("headline"), HEADLINE_MAX) if r.get("headline") else ""
    return h or _first_sentence(r.get("summary") or r.get("error") or "")


def _briefing_entries(sched, runs):
    out = []
    for r in runs:
        if not _is_important(r):
            continue
        e = {"bot_id": sched["id"], "bot_name": sched["name"], "kind": sched["kind"], "run": r.get("run"),
             "level": "important", "status": r.get("status"), "headline": _run_headline(sched["name"], r),
             "ended": r.get("ended") or r.get("started"), "seen": bool(r.get("seen"))}
        if r.get("session_id"):
            e["session_id"] = r["session_id"]
        out.append(e)
    return out


def _newest_first(entries):
    entries.sort(key=lambda e: (e["ended"] or 0, e["run"] or 0), reverse=True)
    return entries


def briefing(env=None, limit=50, include_seen=True):
    """The briefing: the `important` runs of every bot, newest first, as `{bot_id, bot_name, kind, run, level,
    status, headline, ended, seen, session_id?}`. A quiet run (level `none`), a routine one (`info`) and every
    chat turn stay in the bot's own history. `include_seen=False` leaves out what was already seen."""
    env = _env(env)
    out = []
    for s in Store(env=env).read():
        out += _briefing_entries(s, read_runs(state_dir(s["id"], env)))
    if not include_seen:
        out = [e for e in out if not e["seen"]]
    return _newest_first(out)[:limit] if limit else _newest_first(out)


def briefing_counts(env=None):
    """`{unseen, total}` of the briefing entries."""
    items = briefing(env, limit=0)
    return {"unseen": sum(1 for e in items if not e["seen"]), "total": len(items)}


def mark_briefing_seen(env=None):
    """Mark every important run of every bot seen (what opening the briefing does); returns how many changed."""
    env = _env(env)
    return sum(_mark_dir(d, _is_important) for d in _state_dirs(env))


# ---------------------------------------------------------------- prompts

def trim_memory(text, max_bytes):
    """`text` limited to `max_bytes` UTF-8 bytes: the tail is kept, cut on a line boundary."""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    tail = raw[-max_bytes:] if max_bytes > 0 else b""
    clean = max_bytes > 0 and raw[-max_bytes - 1:-max_bytes] == b"\n"
    s = tail.decode("utf-8", "ignore")
    if not clean:
        i = s.find("\n")
        if i >= 0:
            s = s[i + 1:]
    return s


def enforce_memory_cap(sched, env=None):
    """Trim `memory.md` to `identity.memory_max_bytes` after a run; returns True when it was cut."""
    path = memory_path(sched["id"], env)
    try:
        text = _read_text_trusted(path, 16 * 1024 * 1024)
    except ScheduleError:
        return False
    if text is None:
        return False
    cut = trim_memory(text, sched["identity"]["memory_max_bytes"])
    if cut == text:
        return False
    atomic_write(path, cut)
    return True


def identity_text(sched, visible=False):
    """The text for `--append-system-prompt`: a fixed preamble (unattended, or for a visible run that the user may
    be watching and may answer), the agent's name when it is a persistent agent, and `identity.system`."""
    parts = [PREAMBLE_VISIBLE if visible else PREAMBLE]
    if sched["kind"] == "agent":
        parts.append(f"Your name is {sched['name']}.")
    system = sched["identity"]["system"].strip()
    if system:
        parts.append(system)
    return "\n\n".join(parts) + "\n"


def _iso(now, tz):
    z = _zone(tz)
    n = z.naive(int(now))
    off = z.epoch(n) - calendar.timegm(n.timetuple())
    sign = "+" if off <= 0 else "-"
    off = abs(off)
    return n.strftime("%Y-%m-%dT%H:%M:%S") + f"{sign}{off // 3600:02d}:{off % 3600 // 60:02d}"


_REPORT_FORMAT = (
    "The report file starts with exactly these two lines, then a blank line and a free-form markdown body:\n"
    "level: none|info|important\n"
    "headline: <one sentence, at most 140 characters, what the user should read first>\n"
    "`important` = something the user should know or decide: a problem you found, a fix you made, a result they "
    "asked for, anything that is failing. `info` = routine work done that is worth keeping in your history but "
    "needs no attention. `none` = nothing happened (a check that found everything fine). Be honest: a quiet run "
    "is `none` and is not shown to the user.")


def compose_prompt(sched, state, runs, now, trigger, run_no, memory_text, env=None):
    """The user prompt of one run. A task: its prompt, the report path and the report format (level and
    headline lines). An agent: the standing task, `run #n at <iso>, trigger <trigger>`, the previous run's
    outcome, and the memory and report instructions. `runs` are the earlier run records (read_runs); `memory_text`
    is the current `memory.md` content (only its size is used: the agent reads the file itself)."""
    report = report_path(sched["id"], run_no, env)
    prompt = sched["task"]["prompt"].strip()
    if sched["kind"] == "task":
        return (f"{prompt}\n\nWhen you are done, write a short report of the result to {report}.\n"
                f"{_REPORT_FORMAT} A one-off task's result is what the user asked for: use `important` unless "
                "there is genuinely nothing to report.\n")
    mem = memory_path(sched["id"], env)
    cap = sched["identity"]["memory_max_bytes"]
    tz = sched["when"].get("tz", "local")
    lines = ["Standing task:", prompt, "",
             f"This is run #{run_no} at {_iso(now, tz)}, trigger {trigger}."]
    prev = [r for r in runs if r.get("status") in ("ok", "failed", "limit", "timeout", "cost", "cancelled")
            and r.get("trigger") != "chat" and isinstance(r.get("run"), int) and r["run"] < run_no]
    if prev:
        p = prev[-1]
        what = (p.get("summary") or p.get("error") or "no summary").strip()
        lines.append(f"Previous run (#{p['run']}, {p['status']}): {what}")
    else:
        lines.append("There is no previous run.")
    size = len(memory_text.encode("utf-8")) if memory_text else 0
    state_txt = f"currently {size} bytes" if size else "empty: this is a first start"
    lines += ["", f"Memory: read {mem} first ({state_txt}). At the end rewrite it concisely, at most {cap} bytes, "
              "keeping only durable facts and not a log of this run.",
              f"Write your final report to {report}. {_REPORT_FORMAT}"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- self scheduling

def _self_guard(schedule_id, env):
    env = _env(env)
    if env.get("HIVEGRID_SCHEDULE") != schedule_id:
        raise ValueError("a run may only change its own schedule (HIVEGRID_SCHEDULE must name it)")
    sched = Store(env=env).get(schedule_id)
    if sched is None:
        raise ValueError(f"unknown schedule {schedule_id}")
    return sched


def self_next(schedule_id, *, in_s=None, at=None, now=None, env=None):
    """Set the schedule's next run (replacing its regular slots until then). `in_s` seconds from now, or
    `at` (epoch, or `YYYY-MM-DDTHH:MM` in the schedule's zone). Floored to 300 s from now, at most 366 days.
    Only for the schedule named by `HIVEGRID_SCHEDULE`. Returns the epoch set."""
    if (in_s is None) == (at is None):
        raise ValueError("give exactly one of in_s and at")
    sched = _self_guard(schedule_id, env)
    now = int(time.time() if now is None else now)
    if in_s is not None:
        target = now + _int(in_s, "in_s", 0, 366 * 86400)
    elif isinstance(at, str):
        target = _zone(sched["when"].get("tz", "local")).epoch(_parse_naive(at))
    else:
        target = _int(at, "at", 0, 1 << 40)
    target = max(target, now + MIN_INTERVAL_S)
    if target > now + 366 * 86400:
        raise ValueError("the next run may be at most 366 days away")

    def fn(st):
        st["next_override"] = target
        st["done"] = False
    update_state(init_state_dir(sched, now, env), fn)
    return target


def self_done(schedule_id, *, env=None):
    """Mark the schedule finished: no further regular or self-set runs until it is resumed."""
    sched = _self_guard(schedule_id, env)

    def fn(st):
        st["done"] = True
        st["next_override"] = None
    update_state(init_state_dir(sched, env=env), fn)


def self_note(schedule_id, text, *, now=None, env=None):
    """Leave a note (at most 4000 characters) for the user in `notes/`; the newest 50 are kept. Returns
    the note path."""
    sched = _self_guard(schedule_id, env)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("the note is empty")
    now = int(time.time() if now is None else now)
    sdir = init_state_dir(sched, now, env)
    ndir = os.path.join(sdir, "notes")
    path = os.path.join(ndir, f"{now}-{os.getpid()}.md")
    atomic_write(path, text.strip()[:4000] + "\n")
    old = sorted(n for n in os.listdir(ndir) if n.endswith(".md"))[:-50]
    for n in old:
        try:
            os.unlink(os.path.join(ndir, n))
        except OSError:
            pass
    return path


# ---------------------------------------------------------------- summary

def _item_state(sched, state, runs):
    unseen_runs = [r for r in runs if r.get("seen") is False]
    live = state.get("running")
    running = bool(live)
    waiting = running and isinstance(live, dict) and bool(live.get("waiting"))   # a visible run asks for the user
    attention = (bool(state.get("paused_reason")) or waiting
                 or any(r.get("status") in ATTENTION_STATUS for r in unseen_runs))
    if running:
        kind = "attention" if waiting else "working"
    elif attention:
        kind = "attention"
    elif any(r.get("status") == "ok" for r in unseen_runs):
        kind = "done"
    elif not sched["enabled"] or state.get("done"):
        kind = "idle"
    else:
        kind = "schedule"
    return kind, attention, running, len(unseen_runs)


def _chat_info(state):
    chat = state.get("chat") if isinstance(state.get("chat"), dict) else {}
    queue = state.get("chat_queue")
    return {"active": bool(chat.get("active")), "queue": len(queue) if isinstance(queue, list) else 0,
            "error": chat.get("error") if isinstance(chat.get("error"), str) else None}


def build_summary(now, scheduler=None, *, env=None, schedules=None):
    """The document the panel, the phone and the header read: `{ts, scheduler, counts, items, briefing}`.
    `scheduler` (heartbeat, pid, ... of the daemon) comes from the caller. Item `state`: `working`
    (a run or a chat turn is live), `attention` (unseen failed/limit/timeout/cost/missed run, a run opened for the user's
    takeover, a visible run waiting for an answer, or paused),
    `done` (unseen ok), `schedule` (waiting for its next run), `idle` (disabled or finished). `last` is the
    newest run that is not a chat turn, with its `level` and `headline`; `chat` is `{active, queue, error}`.
    `briefing` is `{unseen, newest: [up to 3 briefing entries]}`."""
    env = _env(env)
    scheds = Store(env=env).read() if schedules is None else schedules
    items, total_unseen, entries = [], 0, []
    for s in scheds:
        sdir = state_dir(s["id"], env)
        st = load_state(sdir)
        runs = read_runs(sdir)
        kind, attention, running, n_unseen = _item_state(s, st, runs)
        total_unseen += n_unseen
        entries += _briefing_entries(s, runs)
        mine = [r for r in runs if r.get("trigger") != "chat"]
        last = mine[-1] if mine else None
        items.append({
            "id": s["id"], "name": s["name"], "kind": s["kind"], "enabled": s["enabled"],
            "when_text": describe(s["when"], now), "next_at": next_run(s, st, now), "state": kind,
            "last": None if last is None else {
                "status": last.get("status"), "ended": last.get("ended"), "summary": (last.get("summary") or "")[:300],
                "seen": bool(last.get("seen")),
                "level": effective_level(last.get("status"), last.get("trigger"), last.get("error"), last.get("level")),
                "headline": _run_headline(s["name"], last)},
            "running": running, "waiting": bool(isinstance(st.get("running"), dict) and st["running"].get("waiting")),
            "attention": attention, "paused_reason": st.get("paused_reason"),
            "skipped_at": skipped_slot(s, st, now),
            "chat": _chat_info(st),
        })
    order = {"attention": 0, "working": 1, "done": 2, "schedule": 3, "idle": 4}
    items.sort(key=lambda i: (order[i["state"]], i["next_at"] or 1 << 60, i["name"].lower()))
    entries = _newest_first(entries)
    return {"ts": int(now), "scheduler": dict(scheduler or {}), "counts": {
        "total": len(items), "enabled": sum(1 for i in items if i["enabled"]),
        "running": sum(1 for i in items if i["running"]), "attention": sum(1 for i in items if i["attention"]),
        "unseen": total_unseen}, "items": items,
        "briefing": {"unseen": sum(1 for e in entries if not e["seen"]), "newest": entries[:3]}}


def write_summary(path, doc):
    """Atomic write (0600) of the summary JSON; its directory must be a real directory of this user."""
    ensure_private_dir(os.path.dirname(path), create=False)
    atomic_write(path, json.dumps(doc, ensure_ascii=False) + "\n")


def read_summary(path):
    """The summary document, or None when missing, foreign, a symlink or not valid."""
    try:
        text = _read_text_trusted(path, 4 * 1024 * 1024)
        doc = json.loads(text) if text else None
    except (ScheduleError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def daemon_alive(env=None, now=None, max_age_s=HEARTBEAT_MAX_AGE_S):
    """Whether the runtime summary carries a `scheduler.heartbeat` newer than `max_age_s`."""
    doc = read_summary(summary_path(env))
    hb = doc.get("scheduler", {}).get("heartbeat") if doc and isinstance(doc.get("scheduler"), dict) else None
    now = time.time() if now is None else now
    return isinstance(hb, (int, float)) and not isinstance(hb, bool) and 0 <= now - hb <= max_age_s


# ---------------------------------------------------------------- command inbox

_CMD_RE = re.compile(r"^cmd\.(\d+)-(\d+)\.json$")


def send_command(cdir, cmd):
    """Drop `{"op": run-now|skip|cancel|reload|chat, "id": ..., "text": ...}` into the daemon inbox `cdir` as
    `cmd.<ns>-<pid>.json` (0600, atomic). `id` is required except for `reload`; `chat` also needs `text`
    (1 to 4000 characters), no other op takes one. Returns the path."""
    if not isinstance(cmd, dict):
        raise ValueError("a command must be an object")
    _no_unknown(cmd, ("op", "id", "text"), "command")
    op = _choice(cmd.get("op"), COMMAND_OPS, "op")
    if op != "reload" or "id" in cmd:
        if not isinstance(cmd.get("id"), str) or not ID_RE.match(cmd["id"]):
            raise ValueError("command needs a valid schedule id")
    if op == "chat":
        if not _chat_text_ok(cmd.get("text")):
            raise ValueError(f"a chat command needs a text of 1 to {CHAT_MAX} characters")
    elif "text" in cmd:
        raise ValueError("only a chat command carries a text")
    ensure_private_dir(cdir)
    path = os.path.join(cdir, f"cmd.{time.time_ns()}-{os.getpid()}.json")
    atomic_write(path, json.dumps(cmd) + "\n")
    return path


def _chat_text_ok(text):
    return isinstance(text, str) and bool(text.strip()) and len(text) <= CHAT_MAX


def _valid_command(cmd):
    if not isinstance(cmd, dict) or not set(cmd) <= {"op", "id", "text"} or cmd.get("op") not in COMMAND_OPS:
        return False
    if cmd["op"] == "chat":
        return isinstance(cmd.get("id"), str) and bool(ID_RE.match(cmd["id"])) and _chat_text_ok(cmd.get("text"))
    if "text" in cmd:
        return False
    return cmd.get("id") is None and cmd["op"] == "reload" or isinstance(cmd.get("id"), str) and bool(ID_RE.match(cmd["id"]))


def read_commands(cdir, uid=None):
    """Return the valid commands in the inbox (oldest first) and delete them. Junk (bad names, JSON or
    ops) is deleted and ignored; files of another user (`uid`, default ours) or symlinks are ignored
    and left alone. Raises ScheduleError when the directory itself is not trusted."""
    uid = _uid() if uid is None else uid
    ensure_private_dir(cdir)
    out = []
    for name in sorted(os.listdir(cdir)):
        m = _CMD_RE.match(name)
        path = os.path.join(cdir, name)
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if not m or not stat.S_ISREG(st.st_mode):
            continue
        if st.st_uid != uid:
            continue
        cmd = None
        if st.st_size <= 65536:
            try:
                with open(path, encoding="utf-8") as f:
                    cmd = json.load(f)
            except (OSError, ValueError):
                cmd = None
        try:
            os.unlink(path)
        except OSError:
            pass
        if _valid_command(cmd) and (st.st_size <= 4096 or cmd["op"] == "chat"):
            out.append(cmd)
    return out


# ---------------------------------------------------------------- talking to a bot

def conversation(schedule_id, env=None):
    """What a talk view needs of one bot: `{session_file, live, busy, queue, error, activity}`. `session_file` is
    the bot's main session (None before it has one); the transcript is read from it with the shared chat parser
    (`hivegrid_remote_data.ChatCache`). `live` is true while a run or chat turn is going (the file is growing),
    `busy` while a new message would have to wait (`live`, or messages already queued), `queue` how many messages
    wait, `error` why the last turn failed (None after a good one) and `activity` is `chat`, `run` or None."""
    st = load_state(state_dir(schedule_id, _env(env)))
    running = st.get("running")
    info = _chat_info(st)
    sf = st.get("session_file")
    return {"session_file": sf if isinstance(sf, str) and sf else None, "live": bool(running),
            "busy": bool(running) or info["queue"] > 0, "queue": info["queue"], "error": info["error"],
            "activity": None if not running else "chat" if isinstance(running, dict) and running.get("chat") else "run"}


def say(schedule_id, text, env=None, now=None):
    """Send `text` to a bot: a command for the daemon, which runs it as a chat turn (`omp -p --resume <main
    session>`) as soon as the bot is idle. Returns `{queued, position}`: `queued` is true when the bot is busy
    (a scheduled run or another turn) so the message waits, `position` its place in the queue (0 when it starts
    at once). Raises ValueError for an empty or over-long text and ScheduleError (`.code`) with `no_daemon`
    ("scheduler not running": nothing would ever run it), `unknown_bot` or `queue_full` (5 messages wait)."""
    env = _env(env)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("the message is empty")
    text = text.strip()
    if len(text) > CHAT_MAX:
        raise ValueError(f"the message is longer than {CHAT_MAX} characters")
    if Store(env=env).get(schedule_id) is None:
        raise ScheduleError(f"unknown bot {schedule_id}", "unknown_bot")
    if not daemon_alive(env, now):
        raise ScheduleError("scheduler not running", "no_daemon")
    st = load_state(state_dir(schedule_id, env))
    waiting = len(st["chat_queue"]) if isinstance(st.get("chat_queue"), list) else 0
    if waiting >= CHAT_QUEUE_MAX:
        raise ScheduleError(f"queue_full: {waiting} messages are already waiting for this bot", "queue_full")
    busy = bool(st.get("running")) or waiting > 0
    send_command(command_dir(env), {"op": "chat", "id": schedule_id, "text": text})
    return {"queued": busy, "position": waiting + 1 if busy else 0}


# ---------------------------------------------------------------- CLI

class _Ctx:
    def __init__(self, env, out, err, now):
        self.env, self.out, self.err, self.now = env, out, err, now

    def say(self, *a):
        print(*a, file=self.out)

    def warn(self, *a):
        print("hivegrid-schedule:", *a, file=self.err)


def _fmt_time(epoch, tz="local"):
    if epoch is None:
        return "-"
    return _zone(tz).naive(int(epoch)).strftime("%a %Y-%m-%d %H:%M")


def _fmt_ago(epoch, now):
    if not epoch:
        return "-"
    d = int(now - epoch)
    if d < 0:
        return "soon"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if d >= size:
            return f"{d // size}{unit} ago"
    return "just now"


def _table(rows, out):
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip(), file=out)


def _resolve(store, text):
    """A schedule by exact id, unique id prefix or exact name (case-insensitive)."""
    recs = store.read()
    for r in recs:
        if r["id"] == text:
            return r
    hits = [r for r in recs if r["id"].startswith(text)] or [r for r in recs if r["name"].lower() == text.lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise KeyError(f"no schedule {text!r} (see `hivegrid-schedule list`)")
    raise KeyError(f"{text!r} matches several schedules: {', '.join(h['id'] for h in hits)}")


def _read_prompt(args):
    if args.prompt_file or args.prompt == "-":
        if args.prompt_file and args.prompt_file != "-":
            try:
                with open(args.prompt_file, encoding="utf-8") as f:
                    return f.read().strip()
            except OSError as exc:
                raise ValueError(f"cannot read {args.prompt_file}: {exc.strerror}") from None
        return sys.stdin.read().strip()
    return args.prompt


def _read_system(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc.strerror}") from None


def _run_flags(args):
    run = {}
    for flag, key in (("cwd", "cwd"), ("account", "account"), ("model", "model"), ("thinking", "thinking"),
                      ("mode", "mode"), ("permissions", "permissions"), ("max_time", "max_time_s"),
                      ("max_cost", "max_cost_usd")):
        v = getattr(args, flag, None)
        if v is not None:
            run[key] = v
    return run


def _notify_daemon(ctx, op, sid=None):
    """Tell a live daemon; True when the command was sent."""
    if not daemon_alive(ctx.env, ctx.now):
        return False
    cmd = {"op": op} if sid is None else {"op": op, "id": sid}
    send_command(command_dir(ctx.env), cmd)
    return True


def _cmd_add(ctx, args):
    prompt = _read_prompt(args)
    parsed = parse_when(args.when, now=ctx.now, tz=args.tz)
    spec = {"name": args.name, "kind": args.kind, "enabled": not args.disabled, "when": parsed["when"],
            "task": {"prompt": prompt or ""}, "run": _run_flags(args)}
    if "cwd" not in spec["run"]:
        spec["run"]["cwd"] = os.getcwd()
    if args.system_file:
        spec["identity"] = {"system": _read_system(args.system_file)}
    store = Store(env=ctx.env)
    rec = store.add(spec, now=ctx.now)
    init_state_dir(rec, ctx.now, ctx.env)
    _notify_daemon(ctx, "reload")
    ctx.say(rec["id"])
    ctx.say(f"{describe(rec['when'], ctx.now)}; next: " + ", ".join(_fmt_time(e, rec["when"].get("tz", "local"))
                                                                  for e in parsed["next"]))
    return 0


def _cmd_list(ctx, args):
    doc = build_summary(ctx.now, env=ctx.env)
    if args.json:
        ctx.say(json.dumps(doc, indent=2, ensure_ascii=False))
        return 0
    if not doc["items"]:
        ctx.say("no schedules (hivegrid-schedule add --name NAME --prompt TEXT --when 'weekdays 9am')")
        return 0
    rows = [("ID", "NAME", "KIND", "STATE", "WHEN", "NEXT", "LAST")]
    for i in doc["items"]:
        last = i["last"]
        rows.append((i["id"], i["name"][:30], i["kind"], i["state"], i["when_text"], _fmt_time(i["next_at"]),
                     "-" if not last else f"{last['status']} {_fmt_ago(last['ended'], ctx.now)}"))
    _table(rows, ctx.out)
    return 0


def _cmd_show(ctx, args):
    store = Store(env=ctx.env)
    rec = _resolve(store, args.id)
    sdir = state_dir(rec["id"], ctx.env)
    st = load_state(sdir)
    runs = read_runs(sdir, 5)
    if args.json:
        ctx.say(json.dumps({"schedule": rec, "state": st, "runs": runs}, indent=2, ensure_ascii=False))
        return 0
    ctx.say(json.dumps(rec, indent=2, ensure_ascii=False))
    ctx.say(f"\nwhen: {describe(rec['when'], ctx.now)}   next: {_fmt_time(next_run(rec, st, ctx.now))}")
    ctx.say(f"state dir: {sdir}")
    ctx.say(f"running: {bool(st.get('running'))}  consecutive failures: {st.get('consec_failures')}"
            + (f"  paused: {st['paused_reason']}" if st.get("paused_reason") else ""))
    for r in runs:
        ctx.say(f"  #{r.get('run')} {r.get('status')} {_fmt_ago(r.get('ended'), ctx.now)}  {r.get('summary') or r.get('error') or ''}"[:160])
    return 0


def _cmd_edit(ctx, args):
    store = Store(env=ctx.env)
    rec = _resolve(store, args.id)
    changes = {}
    if args.name is not None:
        changes["name"] = args.name
    if args.kind is not None:
        changes["kind"] = args.kind
    if args.when is not None:
        changes["when"] = parse_when(args.when, now=ctx.now, tz=args.tz or rec["when"].get("tz", "local"))["when"]
    prompt = _read_prompt(args)
    if prompt is not None:
        changes["task"] = {"prompt": prompt}
    run = _run_flags(args)
    if run:
        changes["run"] = run
    if args.system_file:
        changes["identity"] = {"system": _read_system(args.system_file)}
    if not changes:
        raise ValueError("nothing to change (give --name, --when, --prompt, --cwd, ...)")
    new = store.change(rec["id"], changes, now=ctx.now)
    init_state_dir(new, ctx.now, ctx.env)
    sync_identity(new, ctx.env)
    _notify_daemon(ctx, "reload")
    ctx.say(f"{new['id']}: {describe(new['when'], ctx.now)}; next: {_fmt_time(next_run(new, load_state(state_dir(new['id'], ctx.env)), ctx.now))}")
    return 0


def _cmd_rm(ctx, args):
    store = Store(env=ctx.env)
    rec = _resolve(store, args.id)
    store.remove(rec["id"])
    sdir = state_dir(rec["id"], ctx.env)
    if not args.keep_state and os.path.isdir(sdir) and not os.path.islink(sdir):
        shutil.rmtree(sdir, ignore_errors=True)
    _notify_daemon(ctx, "reload")
    ctx.say(f"removed {rec['id']}" + (" (state kept)" if args.keep_state else ""))
    return 0


def _cmd_pause(ctx, args):
    store = Store(env=ctx.env)
    rec = _resolve(store, args.id)
    store.change(rec["id"], {"enabled": False}, now=ctx.now)
    _notify_daemon(ctx, "reload")
    ctx.say(f"paused {rec['id']}")
    return 0


def _cmd_resume(ctx, args):
    store = Store(env=ctx.env)
    rec = _resolve(store, args.id)
    new = store.change(rec["id"], {"enabled": True}, now=ctx.now)
    sdir = init_state_dir(new, ctx.now, ctx.env)

    def fn(st):
        st["paused_reason"], st["consec_failures"], st["done"] = None, 0, False
        st["baseline"] = int(ctx.now)
    st = update_state(sdir, fn)
    _notify_daemon(ctx, "reload")
    ctx.say(f"resumed {rec['id']}; next: {_fmt_time(next_run(new, st, ctx.now), new['when'].get('tz', 'local'))}")
    return 0


def _cmd_run(ctx, args):
    rec = _resolve(Store(env=ctx.env), args.id)
    if _notify_daemon(ctx, "run-now", rec["id"]):
        ctx.say(f"asked the scheduler to run {rec['id']} now")
        return 0

    def fn(st):
        st["run_now"] = True
    update_state(init_state_dir(rec, ctx.now, ctx.env), fn)
    ctx.say(f"{rec['id']} will run as soon as the scheduler starts (it is not running now)")
    return 0


def _cmd_skip(ctx, args):
    rec = _resolve(Store(env=ctx.env), args.id)
    if _notify_daemon(ctx, "skip", rec["id"]):
        ctx.say(f"asked the scheduler to skip the next run of {rec['id']}")
        return 0
    st = update_state(init_state_dir(rec, ctx.now, ctx.env), lambda s: skip_next(rec, s, ctx.now))
    ctx.say(f"skipped; next: {_fmt_time(next_run(rec, st, ctx.now), rec['when'].get('tz', 'local'))}")
    return 0


def _cmd_unskip(ctx, args):
    rec = _resolve(Store(env=ctx.env), args.id)
    st = load_state(init_state_dir(rec, ctx.now, ctx.env))
    if skipped_slot(rec, st, ctx.now) is None:
        ctx.say(f"nothing to undo: no skipped run of {rec['id']} is still ahead", True)
        return 1
    if _notify_daemon(ctx, "unskip", rec["id"]):
        ctx.say(f"asked the scheduler to bring back the skipped run of {rec['id']}")
        return 0
    st = update_state(init_state_dir(rec, ctx.now, ctx.env), lambda s: unskip(rec, s, ctx.now))
    ctx.say(f"skip undone; next: {_fmt_time(next_run(rec, st, ctx.now), rec['when'].get('tz', 'local'))}")
    return 0


def _cmd_logs(ctx, args):
    rec = _resolve(Store(env=ctx.env), args.id)
    sdir = state_dir(rec["id"], ctx.env)
    if args.run is not None:
        path = report_path(rec["id"], args.run, ctx.env)
        text = _read_text_trusted(path, 1 << 20)
        if text is None:
            raise KeyError(f"no report for run {args.run} ({path})")
        ctx.say(text.rstrip())
        return 0
    runs = read_runs(sdir, args.n)
    if args.json:
        ctx.say(json.dumps(runs, indent=2, ensure_ascii=False))
        return 0
    if not runs:
        ctx.say("no runs yet")
        return 0
    rows = [("RUN", "WHEN", "TRIGGER", "STATUS", "LEVEL", "TIME", "COST", "SEEN", "SUMMARY")]
    for r in runs:
        took = "-" if not (r.get("started") and r.get("ended")) else _dur_text(max(int(r["ended"] - r["started"]), 1))
        cost = "-" if r.get("cost_usd") is None else f"${r['cost_usd']:.2f}"
        rows.append((f"#{r.get('run')}", _fmt_time(r.get("started") or r.get("ended"), rec["when"].get("tz", "local")),
                     r.get("trigger"), r.get("status"), r.get("level") or "-", took, cost, "yes" if r.get("seen") else "NEW",
                     (r.get("headline") or r.get("summary") or r.get("error") or "").replace("\n", " ")[:60]))
    _table(rows, ctx.out)
    return 0


def _cmd_seen(ctx, args):
    store = Store(env=ctx.env)
    ids = [_resolve(store, args.id)["id"]] if args.id else [r["id"] for r in store.read()]
    n = sum(mark_seen(i, args.run, env=ctx.env) for i in ids)
    ctx.say(f"marked {n} run(s) seen")
    return 0


def _cmd_briefing(ctx, args):
    entries = briefing(ctx.env, limit=0, include_seen=args.all)
    if args.json:
        ctx.say(json.dumps(entries, indent=2, ensure_ascii=False))
    elif not entries:
        ctx.say("nothing important" + ("" if args.all else " that you have not seen (--all shows what you have)"))
    else:
        rows = [("BOT", "RUN", "WHEN", "STATUS", "SEEN", "HEADLINE")]
        for e in entries:
            rows.append((e["bot_name"][:24], f"#{e['run']}", _fmt_ago(e["ended"], ctx.now), e["status"],
                         "yes" if e["seen"] else "NEW", e["headline"]))
        _table(rows, ctx.out)
    if args.mark_seen:
        ctx.say(f"marked {mark_briefing_seen(ctx.env)} report(s) seen")
    return 0


def _cmd_say(ctx, args):
    rec = _resolve(Store(env=ctx.env), args.id)
    try:
        res = say(rec["id"], " ".join(args.text), ctx.env, ctx.now)
    except ScheduleError as exc:
        if exc.code == "queue_full":
            ctx.warn(f"{rec['name']} already has {CHAT_QUEUE_MAX} messages waiting; try again later")
            return 1
        raise
    ctx.say(f"queued as number {res['position']}: {rec['name']} is busy" if res["queued"]
            else f"sent to {rec['name']}")
    return 0


def _chat_line(item):
    text = (item.get("text") or "").rstrip()
    if item.get("kind") == "tool":
        tool = item.get("tool") or {}
        return f"  . {tool.get('name', 'tool')} {tool.get('summary') or ''} [{tool.get('status') or ''}]".rstrip()
    who = {"user": "you", "assistant": "bot"}.get(item.get("role"), item.get("role") or "")
    if item.get("kind") == "thinking":
        who += " (thinking)"
    return f"{who}: {text}"


def _cmd_chat(ctx, args):
    """Print the tail of the conversation with a bot (its main session, parsed by hivegrid_remote_data); with
    --follow keep printing what appears while a run or a chat turn goes on, until interrupted."""
    rec = _resolve(Store(env=ctx.env), args.id)
    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
    import hivegrid_remote as R
    import hivegrid_remote_data as D
    cache = D.ChatCache(R.sanitize_text, R.sanitize_line)
    last, shown = -1, False
    while True:
        info = conversation(rec["id"], ctx.env)
        path = D.session_path_ok(info["session_file"], ctx.env.get("HOME")) if info["session_file"] else None
        win = cache.window(path, 30 if last < 0 else 500) if path else None
        if win is None:
            if not shown:
                ctx.say("no conversation yet (`hivegrid-schedule say` starts one)")
                shown = True
        else:
            for item in win[0]:
                if item["n"] > last:
                    ctx.say(_chat_line(item))
                    last, shown = item["n"], True
        if not args.follow:
            if info["queue"]:
                ctx.say(f"({info['queue']} message(s) waiting)")
            if info["error"]:
                ctx.say(f"(last turn failed: {info['error']})")
            return 0
        ctx.out.flush()
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            return 0


def _cmd_parse(ctx, args):
    parsed = parse_when(" ".join(args.text), now=ctx.now, tz=args.tz)
    if args.json:
        ctx.say(json.dumps(parsed, indent=2))
        return 0
    when = parsed["when"]
    ctx.say(f"{describe(when, ctx.now)}   {json.dumps(when)}")
    ctx.say("next: " + ", ".join(_fmt_time(e, when.get("tz", "local")) for e in parsed["next"]))
    return 0


def _cmd_next(ctx, args):
    rec = _resolve(Store(env=ctx.env), args.id)
    st = load_state(state_dir(rec["id"], ctx.env))
    if not rec["enabled"] or st.get("done"):
        ctx.say("no upcoming runs (" + ("finished by the agent" if st.get("done") else "paused") + ")")
        return 0
    tz = rec["when"].get("tz", "local")
    first = next_run(rec, st, ctx.now)
    times = [] if first is None else [first] + next_fires(rec["when"], first, args.n - 1)
    if not times:
        ctx.say("no upcoming runs")
    for e in times:
        ctx.say(_fmt_time(e, tz))
    return 0


def _account_info(env):
    """{label: omp profile or None} from the settings and the discovered accounts; None when unreadable."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
        import hivegrid_settings_core as sc
        return {a["label"]: a.get("profile") for a in sc.Settings(sc.settings_path()).accounts()}
    except Exception:  # noqa: BLE001 - the settings module is optional context for a warning only
        return None


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, ValueError, TypeError, OverflowError):
        return True


def _cmd_doctor(ctx, args):
    issues = []

    def problem(level, where, text):
        issues.append(level)
        ctx.say(f"{level:5} {where}: {text}")

    store = Store(env=ctx.env)
    try:
        scheds = store.read()
    except ScheduleError as exc:
        problem("error", "schedules.json", str(exc))
        return 2
    for sid, msg in store.errors:
        problem("error", sid, msg)
    accounts = _account_info(ctx.env)
    if accounts is None:
        ctx.say("note  accounts could not be read; account checks skipped")
    root = state_root(ctx.env)
    if os.path.lexists(root):
        try:
            ensure_private_dir(root, create=False)
        except ScheduleError as exc:
            problem("error", "state", str(exc))
    for s in scheds:
        sid = s["id"]
        cwd = os.path.expanduser(s["run"]["cwd"])
        if not os.path.isdir(cwd):
            problem("warn", sid, f"cwd {cwd} does not exist")
        for d in s["run"]["add_dirs"]:
            if not os.path.isdir(os.path.expanduser(d)):
                problem("warn", sid, f"add_dir {d} does not exist")
        if accounts is not None:
            for label in ([s["run"]["account"]] if s["run"]["account"] else []) + s["policy"]["fallback_accounts"]:
                if label not in accounts:
                    problem("warn", sid, f"account {label!r} is unknown (settings panel, Accounts)")
                elif accounts[label] is None:
                    problem("warn", sid, f"account {label!r} has no omp profile (~/.omp/profiles/{label})")
        sdir = state_dir(sid, ctx.env)
        if os.path.lexists(sdir):
            try:
                ensure_private_dir(sdir, create=False)
            except ScheduleError as exc:
                problem("error", sid, str(exc))
                continue
            st = load_state(sdir)
            run = st.get("running")
            if run:
                started = run.get("started") if isinstance(run, dict) else None
                if isinstance(run, dict) and run.get("pid") and not _pid_alive(run["pid"]):
                    problem("warn", sid, f"state says run #{run.get('run')} is live but pid {run['pid']} is gone (stale)")
                elif isinstance(started, (int, float)) and ctx.now - started > 2 * s["run"]["max_time_s"]:
                    problem("warn", sid, "a run has been live for more than twice max_time_s (stale?)")
            if st.get("paused_reason"):
                problem("warn", sid, f"paused: {st['paused_reason']} (hivegrid-schedule resume {sid})")
            if s["enabled"] and not st.get("done") and next_run(s, st, ctx.now) is None and s["when"]["type"] != "once":
                problem("warn", sid, "no upcoming run")
        else:
            ctx.say(f"note  {sid}: no state directory yet (the scheduler creates it)")
    known = {s["id"] for s in scheds}
    for d in _state_dirs(ctx.env):
        if os.path.basename(d) not in known:
            problem("warn", os.path.basename(d), "state directory without a schedule (hivegrid-schedule rm cleans nothing now; delete it by hand)")
    alive = daemon_alive(ctx.env, ctx.now)
    ctx.say(f"note  scheduler: {'running' if alive else 'not running (runs wait until it starts)'}")
    if "error" in issues:
        return 2
    if issues:
        return 1
    ctx.say("ok    no problems found")
    return 0


def _cmd_self(ctx, args):
    sid = ctx.env.get("HIVEGRID_SCHEDULE")
    if not sid:
        raise ValueError("`self` works only inside a scheduled run (HIVEGRID_SCHEDULE is not set)")
    if args.selfcmd == "next":
        if (args.in_ is None) == (args.at is None):
            raise ValueError("give exactly one of --in and --at")
        e = self_next(sid, in_s=None if args.in_ is None else parse_duration(args.in_), at=args.at,
                      now=ctx.now, env=ctx.env)
        ctx.say(f"next run: {_fmt_time(e)}")
    elif args.selfcmd == "done":
        self_done(sid, env=ctx.env)
        ctx.say("marked done: no further runs")
    else:
        ctx.say(self_note(sid, " ".join(args.text), now=ctx.now, env=ctx.env))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(
        prog="hivegrid-schedule",
        description="Scheduled omp agents (Bots): one-time and recurring tasks, persistent agents. See docs/schedules.md. "
                    "Exit 0 success, 1 failure (doctor: warnings), 2 bad input (doctor: errors).")
    sub = ap.add_subparsers(dest="command", required=True)

    def fields(p, adding):
        p.add_argument("--name", required=adding, help="display name (up to 80 characters)")
        p.add_argument("--prompt", help="the task text; `-` reads stdin", **({} if adding else {"default": None}))
        p.add_argument("--prompt-file", help="read the task from a file (`-` is stdin)")
        p.add_argument("--when", required=adding, help="when to run: 'weekdays 9am', 'every 30m', 'in 2h', cron, ...")
        p.add_argument("--tz", default="local" if adding else None, help="time zone: local (default) or an IANA name")
        p.add_argument("--cwd", help="working directory (default: the current one)" if adding else "working directory")
        p.add_argument("--account", help="account label (an omp profile)")
        p.add_argument("--model")
        p.add_argument("--thinking", choices=THINKING)
        p.add_argument("--mode", choices=MODES, help="headless (background, default), visible (an interactive omp in "
                       "the Bots window of the running Hivegrid, you can watch and answer) or hybrid (headless first; "
                       "when it fails or times out the same session opens in a pane for you). Without a running "
                       "Hivegrid window the run is headless and its record says so")
        p.add_argument("--permissions", choices=PERMISSIONS)
        p.add_argument("--kind", choices=KINDS, default="task" if adding else None)
        p.add_argument("--max-time", type=int, help="seconds per run (60 to 86400, default 1800)")
        p.add_argument("--max-cost", type=float, help="USD per run (default 2)")
        p.add_argument("--system-file", help="file with the agent's identity text")

    a = sub.add_parser("add", help="create a schedule; prints its id and the next 3 fire times")
    fields(a, True)
    a.add_argument("--disabled", action="store_true", help="create it paused")
    ls = sub.add_parser("list", help="table of schedules")
    ls.add_argument("--json", action="store_true")
    sh = sub.add_parser("show", help="one schedule with its state and last runs")
    sh.add_argument("id")
    sh.add_argument("--json", action="store_true")
    ed = sub.add_parser("edit", help="change fields of a schedule")
    ed.add_argument("id")
    fields(ed, False)
    rm = sub.add_parser("rm", help="delete a schedule and its state")
    rm.add_argument("id")
    rm.add_argument("--keep-state", action="store_true", help="keep memory, runs and notes")
    for name, helptext in (("pause", "stop scheduling it"), ("resume", "schedule it again (clears a breaker pause)"),
                           ("run", "run it now"), ("skip", "skip its next run"), ("unskip", "undo the last skip")):
        sub.add_parser(name, help=helptext).add_argument("id")
    lg = sub.add_parser("logs", help="run history, or one run's report")
    lg.add_argument("id")
    lg.add_argument("-n", type=int, default=10, help="how many runs (default 10)")
    lg.add_argument("--run", type=int, help="print the report of this run")
    lg.add_argument("--json", action="store_true")
    se = sub.add_parser("seen", help="mark runs as read")
    se.add_argument("id", nargs="?", help="schedule (default: all)")
    se.add_argument("--run", type=int, help="only this run number")
    br = sub.add_parser("briefing", help="the important reports of all bots (newest first; unseen ones unless --all)")
    br.add_argument("--json", action="store_true")
    br.add_argument("--all", action="store_true", help="include reports you have already seen")
    br.add_argument("--mark-seen", action="store_true", help="mark every important report seen afterwards")
    sy = sub.add_parser("say", help="talk to a bot: the scheduler runs the message as a chat turn in its main session")
    sy.add_argument("id")
    sy.add_argument("text", nargs="+")
    ch = sub.add_parser("chat", help="print the tail of the conversation with a bot")
    ch.add_argument("id")
    ch.add_argument("--follow", action="store_true", help="keep printing what appears (Ctrl+C ends)")
    pa = sub.add_parser("parse", help="show how a schedule text is read")
    pa.add_argument("text", nargs="+")
    pa.add_argument("--tz", default="local")
    pa.add_argument("--json", action="store_true")
    nx = sub.add_parser("next", help="upcoming run times of a schedule")
    nx.add_argument("id")
    nx.add_argument("-n", type=int, default=5)
    sub.add_parser("doctor", help="check accounts, directories, expressions and stale state")
    sf = sub.add_parser("self", help="for a running agent: change its own schedule (needs HIVEGRID_SCHEDULE)")
    ss = sf.add_subparsers(dest="selfcmd", required=True)
    sn = ss.add_parser("next", help="set the next run (at least 5 minutes away)")
    sn.add_argument("--in", dest="in_", help="e.g. 2h, 1d")
    sn.add_argument("--at", help="YYYY-MM-DDTHH:MM in the schedule's zone")
    ss.add_parser("done", help="finish: no further runs")
    st = ss.add_parser("note", help="leave a note for the user")
    st.add_argument("text", nargs="+")
    return ap


_COMMANDS = {"add": _cmd_add, "list": _cmd_list, "show": _cmd_show, "edit": _cmd_edit, "rm": _cmd_rm,
             "pause": _cmd_pause, "resume": _cmd_resume, "run": _cmd_run, "skip": _cmd_skip, "unskip": _cmd_unskip, "logs": _cmd_logs,
             "seen": _cmd_seen, "parse": _cmd_parse, "next": _cmd_next, "doctor": _cmd_doctor, "self": _cmd_self,
             "briefing": _cmd_briefing, "say": _cmd_say, "chat": _cmd_chat}


def main(argv=None, *, env=None, out=None, err=None, now=None):
    """The CLI. Returns the exit status: 0 success, 1 failure (doctor: warnings), 2 bad input (doctor: errors)."""
    args = build_parser().parse_args(argv)
    ctx = _Ctx(_env(env), out or sys.stdout, err or sys.stderr, time.time() if now is None else now)
    try:
        return _COMMANDS[args.command](ctx, args)
    except ValueError as exc:
        ctx.warn(exc)
        return 2
    except KeyError as exc:
        ctx.warn(exc.args[0] if exc.args else exc)
        return 1
    except OSError as exc:
        ctx.warn(exc)
        return 1
