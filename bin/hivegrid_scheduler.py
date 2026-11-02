"""hivegrid-scheduler: the daemon that runs the Bots (scheduled omp agents). See docs/schedules.md, "Scheduler".

The definitions, state files, policies and run records are `hivegrid_schedule`; this module is the part
that starts `omp` for them. One `Scheduler` is a tick: reload the definitions, read the control inbox,
reap finished runs, stop runs past their deadline, decide what is due (catch-up, overlap, retries,
guards) and start it within `schedule.max_parallel`, write the summary with a fresh heartbeat. Nothing
inside a tick blocks on a child: every run is a `subprocess.Popen` in its own session whose JSON-lines
stream is read by two threads. The clock, the account lookup, the quota reader, the notifier and the
jitter are injectable, so the tests drive ticks with a fake clock and a fake `omp` script.

Standard library only (the other hivegrid modules are imported when needed).
"""
import argparse
import collections
import contextlib
import fcntl
import glob
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time

import hivegrid_schedule as S

TICK_S = 2.0                  # longest sleep between ticks (a finished run or a signal wakes the loop at once)
SUMMARY_EVERY_S = 10.0        # the summary (and with it the heartbeat) is rewritten at least this often
WATCHDOG_GRACE_S = 60         # omp enforces --max-time itself; the watchdog kills the group this much later
KILL_ESCALATE_S = 10          # SIGTERM first, SIGKILL when the group is still there this much later
DEFER_CAP_S = 6 * 3600        # a quota deferral waits for the window's reset, at most this long
DEFER_NO_RESET_S = 3600       # ... and this long when the quota cache names no reset time
RETRY_STALE_S = 86400         # a pending retry older than this is dropped
REPORT_MAX = 65536
N_JOIN_S = 6                  # a push still being sent at shutdown gets this long
LOG_TAIL = 32768
LOGS_KEPT = 50
SESSION_TAIL = 262144
STDERR_LINES = 400
SWITCH_TIMEOUT_S = 120
READERS_JOIN_S = 5
SETTINGS_DEFAULTS = {"max_parallel": 2, "tz": "local", "allow_full": False, "daily_budget_usd": 0,
                     "notify_events": ["failed", "limit", "missed"]}
READONLY_TOOLS = "read,grep,find,glob,web_search,lsp"
# What a parent omp (the pane the scheduler was started from) leaves in the environment. A bot runs on the
# profile its account names or, without one, on omp's default configuration, never on the parent's.
SCRUB = ("PI_SESSION_FILE", "PI_SESSION_ID", "OMP_PROFILE", "PI_PROFILE", "PI_CODING_AGENT_DIR")
PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
REFUSAL_RE = re.compile(r"requires approval|is not approved to run without asking")
_TYPE_RE = re.compile(r'^\{\s*"type"\s*:\s*"([a-z_]+)"')
_WANTED = {"session", "turn_end", "message_end", "tool_execution_end"}
EVENT_OF_STATUS = {"ok": "done", "failed": "failed", "timeout": "failed", "cost": "failed", "limit": "limit",
                   "missed": "missed"}
NOTIFY_LOOK = {  # status -> (ntfy priority, ntfy tag)
    "ok": ("3", "white_check_mark"), "failed": ("4", "x"), "timeout": ("4", "alarm_clock"),
    "cost": ("4", "moneybag"), "limit": ("3", "hourglass_flowing_sand"), "missed": ("3", "calendar"),
}


# ---------------------------------------------------------------- settings (tolerant)

def settings_path(env):
    return env.get("HIVEGRID_SETTINGS") or os.path.join(env.get("HOME") or os.path.expanduser("~"),
                                                        ".config", "hivegrid", "settings.json")


def read_settings(env=None):
    """The effective `schedule` settings plus `notify_url` (settings.json `remote.notify_url`): defaults for a
    missing or invalid file, key or value. Uses `hivegrid_settings_core` when it is importable and knows the
    key; without it the defaults below apply (and no notification URL)."""
    env = os.environ if env is None else env
    data = {}
    try:
        with open(settings_path(env), "rb") as f:
            doc = json.loads(f.read(1 << 20))
        data = doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        data = {}
    out = dict(SETTINGS_DEFAULTS, notify_events=list(SETTINGS_DEFAULTS["notify_events"]), notify_url=None, push=False)
    try:
        import hivegrid_settings_core as sc
        out.update(sc.schedule_settings(data.get("schedule")))
        remote = sc.remote_settings(data.get("remote"))
        out["notify_url"] = remote.get("notify_url")
        out["push"] = bool(remote.get("push"))
    except Exception:  # noqa: BLE001 - a missing or broken settings module means the defaults, never a stopped scheduler
        pass
    return out


def default_accounts(env):
    """{account label: omp profile or None} from the settings and the discovered accounts (the settings panel's
    list). Raises when the settings module cannot be used."""
    import hivegrid_settings_core as sc
    stored = sc.Settings(sc.settings_path(env.get("HIVEGRID_SETTINGS")))
    return {a["label"]: a.get("profile") or None for a in stored.accounts()}


def default_quota(label, now):
    import hivegrid_remote_data as D
    return D.read_quota(label, now)


def default_notify(url, title, body, priority, tag):
    import hivegrid_remote_notify as N
    N.send(url, title, body, priority, tag)


def default_push(env, event, urgency, log=None):
    """Web Push to the phone (settings.json `remote.push`); the server's devices and key are in push.json beside it."""
    import hivegrid_remote_push as P
    P.send_from_settings(event, urgency, settings_path(env), log=log)


# ---------------------------------------------------------------- the omp event stream

class Stream:
    """The JSON-lines stdout of `omp --mode json -p`, parsed one line at a time (only the events that matter are
    decoded): session id, final assistant text, summed cost and tokens, how the last assistant message ended,
    and the tool calls omp refused for lack of approval."""

    def __init__(self):
        self.session_id = None
        self.text = ""
        self.last_stop = None
        self.error_message = None
        self.last_assistant = None
        self.cost = 0.0
        self.has_cost = False
        self.tokens = {}
        self.model = None
        self.refusals = 0
        self.lines = 0

    def feed(self, raw):
        line = raw.decode("utf-8", "replace").strip() if isinstance(raw, bytes) else raw.strip()
        if not line.startswith("{") or len(line) > (8 << 20):
            return
        self.lines += 1
        m = _TYPE_RE.match(line[:160])
        if m and m.group(1) not in _WANTED:
            return
        if not m and len(line) > (1 << 20):
            return
        try:
            ev = json.loads(line)
        except (ValueError, RecursionError):
            return
        if not isinstance(ev, dict):
            return
        kind = ev.get("type")
        if kind == "session":
            if isinstance(ev.get("id"), str):
                self.session_id = ev["id"]
        elif kind == "turn_end":
            msg = ev.get("message")
            if isinstance(msg, dict):
                self._usage(msg)
                self._assistant(msg)
        elif kind == "message_end":
            msg = ev.get("message")
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                self._assistant(msg)
        elif kind == "tool_execution_end":
            if ev.get("isError") and REFUSAL_RE.search(json.dumps(ev.get("result"))[:4000]):
                self.refusals += 1

    def _usage(self, msg):
        usage = msg.get("usage")
        if not isinstance(usage, dict):
            return
        cost = usage.get("cost")
        total = cost.get("total") if isinstance(cost, dict) else None
        if isinstance(total, (int, float)) and not isinstance(total, bool):
            self.cost += float(total)
            self.has_cost = True
        for key in ("input", "output", "cacheRead", "cacheWrite", "totalTokens"):
            v = usage.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                self.tokens[key] = self.tokens.get(key, 0) + int(v)

    def _assistant(self, msg):
        if msg.get("role") != "assistant":
            return
        if isinstance(msg.get("stopReason"), str):
            self.last_stop = msg["stopReason"]
        err = msg.get("errorMessage")
        if isinstance(err, str) and err:
            self.error_message = err
        elif msg.get("stopReason") not in ("error", "aborted"):
            self.error_message = None
        self.last_assistant = {k: msg[k] for k in ("role", "stopReason", "errorMessage", "errorStatus", "timestamp",
                                                   "completedAt") if k in msg}
        if isinstance(msg.get("model"), str) and msg["model"]:
            prov = msg.get("provider")
            self.model = f"{prov}/{msg['model']}" if isinstance(prov, str) and prov and "/" not in msg["model"] else msg["model"]
        parts = [b.get("text") for b in msg.get("content") or [] if isinstance(b, dict) and b.get("type") == "text"
                 and isinstance(b.get("text"), str)]
        text = "\n".join(p for p in parts if p.strip()).strip()
        if text:
            self.text = text


def classify(*, exit_code, stream, elapsed, max_time, killed, limit):
    """(status, error) of a finished run. `killed` is "watchdog" (no exit within max_time + 60 s), "cancel" or
    "stop" (a command, or the scheduler going down) or None; `limit` is what `A.limit_of_entry` found on the
    session's last entry (or None). Exit 3 is omp's `--max-cost` stop; a failed run that used up its
    `--max-time` is a timeout."""
    if killed == "watchdog":
        return "timeout", f"no exit {WATCHDOG_GRACE_S} s after max_time_s ({max_time} s): the run was killed"
    if killed in ("cancel", "stop"):
        return "cancelled", "cancelled by the user" if killed == "cancel" else "the scheduler was stopped"
    if exit_code == 3:
        return "cost", "the run stopped at its max_cost_usd"
    clean = exit_code == 0 and stream.last_stop not in ("error", "aborted")
    if clean:
        if stream.refusals:
            return "ok", (f"{stream.refusals} tool call(s) needed approval and were refused "
                          "(unattended runs approve reads and writes only, see permissions)")
        return "ok", None
    if limit:
        return "limit", "usage limit of the account"
    if elapsed is not None and elapsed >= max_time - 3:
        return "timeout", f"the run used its max_time_s ({max_time} s)"
    if exit_code is not None and exit_code < 0:
        return "failed", f"omp was killed by signal {-exit_code}"
    msg = (stream.error_message or "").strip()
    return "failed", (msg[:500] if msg else f"omp exited with status {exit_code}")


# ---------------------------------------------------------------- one live run

class Run:
    def __init__(self, sched, run_no, trigger, attempt, started, profile, label, argv, mode, resume_file, note):
        self.sched, self.run_no, self.trigger, self.attempt = sched, run_no, trigger, attempt
        self.started, self.profile, self.label, self.argv = started, profile, label, argv
        self.mode, self.resume_file, self.note = mode, resume_file, note
        self.chat = trigger == "chat"   # a turn of the user's conversation with the bot, not a scheduled run
        self.deadline = started + sched["run"]["max_time_s"] + WATCHDOG_GRACE_S
        self.stream = Stream()
        self.stderr = collections.deque(maxlen=STDERR_LINES)
        self.proc = None
        self.threads = []
        self.killed = None
        self.kill_sent = None
        self.hard_killed = False


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, ValueError, TypeError, OverflowError):
        return True


def _killpg(pid, sig):
    try:
        os.killpg(pid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _midnight(now, days=0):
    t = time.localtime(now)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday + days, 0, 0, 0, 0, 0, -1))


def _hhmm(epoch):
    return time.strftime("%H:%M", time.localtime(epoch))


# ---------------------------------------------------------------- the scheduler

class Scheduler:
    def __init__(self, env=None, *, clock=time.time, accounts=None, quota=None, notify=None, notify_async=False,
                 jitter=None, log=None, settings=None, push=None):
        self.env = dict(os.environ if env is None else env)
        self.clock = clock
        self.accounts_fn = accounts or (lambda: default_accounts(self.env))
        self.quota_fn = quota or default_quota
        self.notify_fn = notify or default_notify
        self.push_fn = push or (lambda event, urgency: default_push(self.env, event, urgency, log=self.log))
        self.notify_async = notify_async
        self.jitter = jitter
        self.log_fn = log or (lambda msg: print(f"hivegrid-scheduler: {msg}", file=sys.stderr, flush=True))
        self.settings_fn = settings or (lambda: read_settings(self.env))
        self.store = S.Store(env=self.env)
        self.scheds = []
        self.live = {}
        self.wake = threading.Event()
        self.stopping = False
        self.pid = os.getpid()
        self.t0 = None
        self.waiting = 0
        self._dirty = True
        self._last_summary = 0.0
        self._inited = set()
        self._budget_noted = None
        self._cmd_error = None
        self._pushes = []

    # ---- small helpers

    def log(self, msg):
        self.log_fn(msg)

    def sdir(self, sid):
        return S.state_dir(sid, self.env)

    def settings(self):
        try:
            return self.settings_fn()
        except Exception:  # noqa: BLE001 - a broken settings reader must never stop the scheduler
            return dict(SETTINGS_DEFAULTS, notify_url=None, push=False)

    def omp_bin(self):
        return self.env.get("HIVEGRID_OMP_BIN") or "omp"

    def switch_bin(self):
        return self.env.get("HIVEGRID_OMP_SWITCH") or os.path.join(os.path.dirname(os.path.realpath(__file__)), "omp-switch")

    # ---- startup and shutdown

    def startup(self):
        """Close what a previous scheduler left in `running`: a dead pid is a failed run, a live one that is
        still our child by its environment is stopped and recorded cancelled."""
        self.t0 = self.clock()
        S.ensure_private_dir(S.command_dir(self.env))
        self.reload()
        for sched in self.scheds:
            self._ensure_state(sched)
            sid = sched["id"]
            st = S.load_state(self.sdir(sid))
            run = st.get("running")
            if not run:
                continue
            self._close_stale(sched, run)
        self._dirty = True

    def _close_stale(self, sched, run):
        sid = sched["id"]
        pid, no = run.get("pid") if isinstance(run, dict) else None, run.get("run") if isinstance(run, dict) else None
        is_chat = isinstance(run, dict) and bool(run.get("chat"))
        now = self.clock()
        status, error = "failed", "the scheduler stopped while this run was live (its process is gone)"
        if pid and _pid_alive(pid):
            if self._is_ours(pid, sid, no):
                _killpg(pid, signal.SIGTERM)
                for _ in range(30):
                    if not _pid_alive(pid):
                        break
                    time.sleep(0.1)
                if _pid_alive(pid):
                    _killpg(pid, signal.SIGKILL)
                status, error = "cancelled", "the scheduler restarted: the run left over from before was stopped"
            else:
                error = "the scheduler stopped while this run was live (its pid now belongs to another process)"
        rec = {"run": no if isinstance(no, int) else self._alloc_run(self.sdir(sid)), "schedule": sid,
               "trigger": "chat" if is_chat else "cron", "mode": "headless",
               "started": run.get("started") if isinstance(run, dict) else None,
               "ended": now, "status": status, "attempt": 1, "error": error}
        rec = S.append_run(self.sdir(sid), rec)

        def fn(st):
            st["running"] = None
            if is_chat:  # a chat turn is not a scheduled run: no breaker, no retry; the queue stays for the next start
                st["chat"] = {"active": False, "started": None, "error": error}
                return st
            return S.record_outcome(sched, st, rec) if status == "failed" else st
        S.update_state(self.sdir(sid), fn)
        self.log(f"{sid}: closed run #{rec['run']} left over from before ({status})")
        if status == "failed" and not is_chat:
            self._notify(sched, rec)

    @staticmethod
    def _is_ours(pid, sid, no):
        try:
            with open(f"/proc/{int(pid)}/environ", "rb") as f:
                env = f.read(1 << 20).split(b"\0")
        except (OSError, ValueError):
            return False
        return f"HIVEGRID_SCHEDULE={sid}".encode() in env and (no is None or f"HIVEGRID_RUN={no}".encode() in env)

    def shutdown(self, reason="stop"):
        """Stop every live run (SIGTERM to its group, SIGKILL after a few seconds), record them cancelled and
        publish a summary without heartbeat so panels see the scheduler is gone."""
        for run in self.live.values():
            run.killed = run.killed or "stop"
            if run.kill_sent is None:
                run.kill_sent = self.clock()
                _killpg(run.proc.pid, signal.SIGTERM)
        deadline = time.monotonic() + 8
        while self.live and time.monotonic() < deadline:
            self._reap(self.clock())
            time.sleep(0.05)
        for run in list(self.live.values()):
            _killpg(run.proc.pid, signal.SIGKILL)
            with contextlib.suppress(Exception):
                run.proc.wait(timeout=5)
        self._reap(self.clock())
        try:
            S.write_summary(S.summary_path(self.env), S.build_summary(
                self.clock(), {"stopped": int(self.clock()), "pid": self.pid, "reason": reason}, env=self.env,
                schedules=self.scheds))
        except (OSError, ValueError) as exc:
            self.log(f"could not write the final summary: {exc}")

    # ---- definitions and state

    def reload(self):
        try:
            self.scheds = self.store.read()
        except OSError as exc:  # ScheduleError: keep the last good list
            self.log(f"schedules.json unusable ({exc}); keeping the last good list")
            return
        known = {s["id"] for s in self.scheds}
        self._inited &= known

    def _ensure_state(self, sched):
        sid = sched["id"]
        if sid in self._inited:
            return True
        try:
            S.init_state_dir(sched, self.clock(), self.env)
        except OSError as exc:
            self.log(f"{sid}: state directory unusable ({exc})")
            return False
        self._inited.add(sid)
        return True

    def _alloc_run(self, sdir):
        got = []

        def fn(st):
            st["run_count"] = int(st.get("run_count") or 0) + 1
            got.append(st["run_count"])
        S.update_state(sdir, fn)
        return got[0]

    # ---- the tick

    def tick(self):
        """One pass; returns how many runs it started."""
        now = self.clock()
        self.reload()
        self._inbox(now)
        self._reap(now)
        self._watchdog(now)
        started = 0 if self.stopping else self._plan(now)
        if self._dirty or now - self._last_summary >= SUMMARY_EVERY_S:
            self._write_summary(now)
        return started

    def _write_summary(self, now):
        try:
            doc = S.build_summary(now, {"heartbeat": now, "pid": self.pid, "started": self.t0,
                                        "max_parallel": self.settings()["max_parallel"], "running": len(self.live),
                                        "waiting": self.waiting, "version": 1}, env=self.env, schedules=self.scheds)
            S.write_summary(S.summary_path(self.env), doc)
            self._last_summary, self._dirty = now, False
        except (OSError, ValueError) as exc:
            self.log(f"could not write the summary: {exc}")

    # ---- control inbox

    def _inbox(self, now):
        try:
            cmds = S.read_commands(S.command_dir(self.env))
            self._cmd_error = None
        except OSError as exc:
            if str(exc) != self._cmd_error:
                self.log(f"control inbox unusable: {exc}")
            self._cmd_error = str(exc)
            return
        by_id = {s["id"]: s for s in self.scheds}
        for cmd in cmds:
            op, sid = cmd["op"], cmd.get("id")
            self._dirty = True
            if op == "reload":
                self.reload()
                continue
            sched = by_id.get(sid)
            if sched is None:
                self.log(f"command {op}: unknown schedule {sid}")
                continue
            try:
                self._command(op, sched, now, cmd)
            except OSError as exc:
                self.log(f"command {op} {sid}: {exc}")

    def _command(self, op, sched, now, cmd=None):
        sid = sched["id"]
        sdir = S.init_state_dir(sched, now, self.env)
        if op == "run-now":
            S.update_state(sdir, lambda st: st.update(run_now=True))
        elif op == "skip":
            S.update_state(sdir, lambda st: S.skip_next(sched, st, now))
        elif op == "chat":
            self._chat_message(sdir, (cmd or {}).get("text"), now)
        elif op == "cancel":
            live = self.live.get(sid)
            chat_live = live is not None and live.chat

            def fn(st):
                st["queued"], st["retry"], st["run_now"] = False, None, False
                if chat_live:  # stopping the conversation stops what the user queued behind the turn
                    st["chat_queue"] = []
            S.update_state(sdir, fn)
            if live is not None and live.kill_sent is None:
                live.killed, live.kill_sent = "cancel", now
                _killpg(live.proc.pid, signal.SIGTERM)

    def _chat_message(self, sdir, text, now):
        """Queue a user message for the bot (at most CHAT_QUEUE_MAX waiting; a full queue refuses the new one and
        says so in `chat.error`)."""
        if not isinstance(text, str) or not text.strip():
            return

        def fn(st):
            queue = st.get("chat_queue") if isinstance(st.get("chat_queue"), list) else []
            if len(queue) >= S.CHAT_QUEUE_MAX:
                chat = dict(st.get("chat") or {})
                chat["error"] = f"queue_full: {len(queue)} messages are already waiting; the last one was not accepted"
                st["chat"] = chat
                return
            st["chat_queue"] = queue + [{"text": text.strip(), "ts": int(now)}]
        S.update_state(sdir, fn)

    # ---- reaping and the watchdog

    def _watchdog(self, now):
        for run in self.live.values():
            if run.kill_sent is None and now >= run.deadline:
                run.killed, run.kill_sent = "watchdog", now
                self.log(f"{run.sched['id']}: run #{run.run_no} passed max_time_s + {WATCHDOG_GRACE_S} s; killing it")
                _killpg(run.proc.pid, signal.SIGTERM)
            elif run.kill_sent is not None and not run.hard_killed and now - run.kill_sent >= KILL_ESCALATE_S:
                run.hard_killed = True
                _killpg(run.proc.pid, signal.SIGKILL)

    def _reap(self, now):
        for sid, run in list(self.live.items()):
            rc = run.proc.poll()
            if rc is None:
                continue
            del self.live[sid]
            try:
                self._finish(run, rc, self.clock())
            except OSError as exc:
                self.log(f"{sid}: cannot record run #{run.run_no}: {exc}")
            self._dirty = True
            self.wake.set()

    # ---- planning

    def _plan(self, now):
        cfg = self.settings()
        capacity = int(cfg["max_parallel"]) - len(self.live)
        candidates = []
        for sched in self.scheds:
            if not self._ensure_state(sched):
                continue
            try:
                cand = self._consider(sched, now)
            except OSError as exc:
                self.log(f"{sched['id']}: {exc}")
                continue
            if cand:
                candidates.append(cand)
        candidates.sort(key=lambda c: (c["slot"], c["sched"]["id"]))
        self.waiting = max(0, len(candidates) - max(capacity, 0))
        started = 0
        for cand in candidates:
            if capacity <= 0:
                break
            self._launch(cand, now)
            capacity -= 1
            started += 1
        return started

    def _consider(self, sched, now):
        """Handle what needs no run slot (missed, skipped, queued) and return the fire candidate, if any. A
        waiting chat message starts when the bot is idle and nothing scheduled is due or queued ahead of it."""
        sid = sched["id"]
        sdir = self.sdir(sid)
        state = S.load_state(sdir)
        running = sid in self.live
        policy_state = dict(state, running=state.get("running") if running else None)
        plan = S.plan_catchup(sched, policy_state, now)
        retry = state.get("retry") if isinstance(state.get("retry"), dict) else None
        if retry and now - (retry.get("at") or 0) > RETRY_STALE_S:
            S.update_state(sdir, lambda st: st.update(retry=None))
            retry = None
        if plan and plan[0] == "missed":
            self._missed(sched, state, plan[1], now)
            return None
        if running:
            if plan and plan[0] == "fire":
                self._overlap(sched, policy_state, plan, now)
            return None
        if plan and plan[0] == "fire":
            return {"sched": sched, "slot": plan[1], "trigger": plan[2], "attempt": 1, "retry": None, "queued": False}
        if state.get("queued"):
            return {"sched": sched, "slot": now, "trigger": "cron", "attempt": 1, "retry": None, "queued": True}
        if retry and retry.get("at", 0) <= now and S.is_active(sched, state):
            return {"sched": sched, "slot": retry["at"], "trigger": "retry", "attempt": int(retry.get("attempt") or 2),
                    "retry": retry, "queued": False}
        queue = state.get("chat_queue")
        first = queue[0] if isinstance(queue, list) and queue and isinstance(queue[0], dict) else None
        if first is not None and isinstance(first.get("text"), str) and first["text"].strip():
            return {"sched": sched, "slot": first.get("ts") or now, "trigger": "chat", "attempt": 1, "retry": None,
                    "queued": False, "chat": first["text"]}
        if queue:  # junk in the queue must not wedge it
            S.update_state(sdir, lambda st: st.update(chat_queue=[m for m in st.get("chat_queue") or []
                                                                 if isinstance(m, dict) and isinstance(m.get("text"), str)
                                                                 and m["text"].strip()]))
        return None

    def _missed(self, sched, state, slots, now):
        sid = sched["id"]
        sdir = self.sdir(sid)
        newest = slots[-1]
        own = "self" if state.get("next_override") is not None else "cron"
        run_no = self._alloc_run(sdir)
        text = (f"{len(slots)} scheduled run(s) were missed while the scheduler was not running; "
                f"the newest was due {time.strftime('%a %Y-%m-%d %H:%M', time.localtime(newest))}")
        rec = S.append_run(sdir, {"run": run_no, "schedule": sid, "trigger": own, "mode": "headless", "started": now,
                                  "ended": now, "status": "missed", "attempt": 1, "summary": text}, sched["name"])
        S.update_state(sdir, lambda st: S.advance(sched, st, newest, own))
        self.log(f"{sid}: {text}")
        self._dirty = True
        self._notify(sched, rec)

    def _overlap(self, sched, policy_state, plan, now):
        sid = sched["id"]
        sdir = self.sdir(sid)
        _, slot, trigger = plan
        action = S.overlap_action(sched, policy_state)
        self._dirty = True
        if action == "queue":
            def fn(st):
                n = S.advance(sched, st, slot, trigger)
                n["queued"] = True
                return n
            S.update_state(sdir, fn)
            return
        run_no = self._alloc_run(sdir)
        live = self.live[sid]
        S.append_run(sdir, {"run": run_no, "schedule": sid, "trigger": trigger, "mode": "headless", "started": now,
                            "ended": now, "status": "skipped", "attempt": 1,
                            "summary": "skipped: " + ("a chat turn" if live.chat else f"run #{live.run_no}")
                            + " is still running"}, sched["name"])
        S.update_state(sdir, lambda st: S.advance(sched, st, slot, trigger))

    # ---- accounts, quota, budget

    def _accounts(self):
        return self.accounts_fn()

    def _quota_pct(self, label, now):
        try:
            q = self.quota_fn(label, now)
        except Exception:  # noqa: BLE001 - an unreadable quota cache means "no data", never a stopped run
            return None, None
        w = q.get("five_hour") if isinstance(q, dict) else None
        if not isinstance(w, dict) or not isinstance(w.get("pct"), (int, float)):
            return None, None
        return float(w["pct"]), w.get("reset_at")

    def _pick_fallback(self, sched, current, now, accounts):
        guard = sched["policy"]["quota_guard_pct"]
        for label in sched["policy"]["fallback_accounts"]:
            if label == current or label not in accounts:
                continue
            pct, _ = self._quota_pct(label, now)
            if pct is None or pct < guard:
                return label
        return None

    def _spent_today(self, now):
        start, total = _midnight(now), 0.0
        root = S.state_root(self.env)
        try:
            names = [n for n in os.listdir(root) if S.ID_RE.match(n)]
        except OSError:
            names = []
        for name in names:
            for r in S.read_runs(os.path.join(root, name)):
                stamp = r.get("ended") or r.get("started") or 0
                if stamp >= start and isinstance(r.get("cost_usd"), (int, float)):
                    total += r["cost_usd"]
        return total

    # ---- starting a run

    def _launch(self, cand, now):
        sched, trigger, attempt, retry = cand["sched"], cand["trigger"], cand["attempt"], cand["retry"]
        chat = cand.get("chat")   # the user's message when this is a chat turn
        sid = sched["id"]
        sdir = S.init_state_dir(sched, now, self.env)
        run_no = []

        def adv(st):
            if chat is not None:  # a chat turn takes the first waiting message and touches no schedule bookkeeping
                n = st
                n["chat_queue"] = list(n.get("chat_queue") or [])[1:]
                n["chat"] = {"active": True, "started": int(now), "error": None}
            else:
                # a queued run's slot was advanced when it was queued; a retry never moves last_slot
                n = st if cand["queued"] else S.advance(sched, st, cand["slot"], trigger)
                n["retry"] = None
                n["queued"] = False
            n["run_count"] = int(n.get("run_count") or 0) + 1
            run_no.append(n["run_count"])
            return n
        state = S.update_state(sdir, adv)
        run_no = run_no[0]
        self._dirty = True

        def fail(err):
            self._start_failed(sched, run_no, trigger, attempt, now, err)
        cfg = self.settings()
        run = sched["run"]
        if run["permissions"] == "full" and not cfg.get("allow_full"):
            return fail("permissions 'full' is not allowed: set schedule.allow_full in settings.json (the host panel's "
                        "Bots settings) or change the bot to 'edit'")
        label, profile = (retry or {}).get("account") or run["account"], None
        accounts = None
        if label:
            try:
                accounts = self._accounts()
            except Exception as exc:  # noqa: BLE001 - the accounts could not be read: a clear failed run, not a crash
                return fail(f"cannot read the accounts: {exc}")
            if label not in accounts:
                return fail(f"account {label!r} is unknown (settings, Accounts); the bot was not run")
        if trigger not in ("manual", "chat"):   # a run the user asked for is not held back by the guards
            if label:
                pct, reset = self._quota_pct(label, now)
                if pct is not None and pct >= sched["policy"]["quota_guard_pct"]:
                    fb = self._pick_fallback(sched, label, now, accounts)
                    if fb:
                        self.log(f"{sid}: {label} is at {pct:.0f}% of its 5-hour quota; running on {fb}")
                        label = fb
                    else:
                        until = min(reset or now + DEFER_NO_RESET_S, now + DEFER_CAP_S)
                        return self._defer(sched, run_no, trigger, attempt, now, max(until, now + 60),
                                           f"5-hour quota of {label} at {pct:.0f}% (guard {sched['policy']['quota_guard_pct']}%): "
                                           f"waiting until {_hhmm(until)}", retry)
            budget = cfg.get("daily_budget_usd") or 0
            if budget > 0:
                spent = self._spent_today(now)
                if spent >= budget:
                    self._budget_note(now, spent, budget)
                    until = _midnight(now, 1)
                    return self._defer(sched, run_no, trigger, attempt, now, until,
                                       f"daily budget ${budget:g} reached (${spent:.2f} spent today): waiting until tomorrow",
                                       retry)
        if label:
            profile = accounts.get(label)
            if profile is not None and not PROFILE_RE.match(profile):
                return fail(f"account {label!r}: {profile!r} is not a valid omp profile name")
        note = None
        resume, note = self._resume_file(sched, state, retry, profile, label, chat=chat is not None)
        mode = "headless" if run["mode"] == "headless" else f"headless/{run['mode']}"  # visible and hybrid fall back
        sync = S.sync_identity(sched, self.env)
        runs = S.read_runs(sdir)
        mem = None
        if sched["identity"]["memory"]:
            with contextlib.suppress(OSError):
                mem = S._read_text_trusted(S.memory_path(sid, self.env), 16 * 1024 * 1024)
        prompt = chat if chat is not None else S.compose_prompt(sched, state, runs, now, trigger, run_no, mem or "", self.env)
        if retry and retry.get("resume_file") and resume:
            prompt = ("Your previous attempt stopped because the account hit its usage limit; the conversation so far "
                      "is that attempt. Continue it and finish the task.\n\n" + prompt)
        argv = self._argv(sched, profile, sync, sdir, resume, self._session_spend(resume) if resume else 0.0)
        env = self._child_env(sched, run_no, profile)
        cwd = os.path.expanduser(run["cwd"])
        if not os.path.isdir(cwd):
            return fail(f"cwd {cwd} does not exist")
        started = self.clock()
        r = Run(sched, run_no, trigger, attempt, started, profile, label, argv, mode, resume, note)
        try:
            proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, start_new_session=True, close_fds=True)
        except OSError as exc:
            return fail(f"cannot start omp ({argv[0]}): {exc.strerror or exc}")
        r.proc = proc
        self._spawn_threads(r, prompt)
        self.live[sid] = r
        S.update_state(sdir, lambda st: st.update(running=dict({"run": run_no, "pid": proc.pid, "started": started},
                                                               **({"chat": True} if chat is not None else {}))))
        self.log(f"{sid}: started run #{run_no} ({trigger}, attempt {attempt}, pid {proc.pid})")

    @staticmethod
    def _session_spend(path):
        """USD already spent in a session file (the sum of `usage.cost.total` of its assistant messages): omp's
        `--max-cost` limits the spend of the whole session, so a resumed session needs its own spend on top of the
        bot's per-run limit or its next turn stops at once (exit 3)."""
        total = 0.0
        try:
            with open(path, "rb") as f:
                for raw in f:
                    if b'"cost"' not in raw or b'"assistant"' not in raw:
                        continue
                    try:
                        msg = json.loads(raw).get("message")
                        cost = msg["usage"]["cost"]["total"]
                    except (ValueError, RecursionError, AttributeError, KeyError, TypeError):
                        continue
                    if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost > 0:
                        total += float(cost)
        except OSError:
            return 0.0
        return total

    def _argv(self, sched, profile, identity_path, sdir, resume, spent=0.0):
        run = sched["run"]
        argv = [self.omp_bin()]
        if profile:
            argv += ["--profile", profile]
        cap = run["max_cost_usd"] + spent   # a resumed session counts what it already cost
        argv += ["--mode", "json", "-p", "--max-time", str(int(run["max_time_s"])),
                 "--max-cost", f"{cap:g}" if not spent else f"{cap:.4f}"]
        perms = run["permissions"]
        if perms == "readonly":
            argv += ["--tools", READONLY_TOOLS, "--approval-mode", "write"]
        elif perms == "edit":
            argv += ["--approval-mode", "write"]
        else:
            argv += ["--approval-mode", "yolo"]
        argv += ["--thinking", run["thinking"]]
        if run["model"]:
            argv += ["--model", run["model"]]
        argv += ["--append-system-prompt", identity_path, "--add-dir", sdir]
        for d in run["add_dirs"]:
            argv += ["--add-dir", os.path.expanduser(d)]
        if resume:
            argv += ["--resume", resume]
        return argv

    def _child_env(self, sched, run_no, profile):
        env = {k: v for k, v in self.env.items() if not k.startswith("WEZTERM_")}
        for k in SCRUB:
            env.pop(k, None)
        env["HIVEGRID_SCHEDULE"] = sched["id"]
        env["HIVEGRID_RUN"] = str(run_no)
        env["HIVEGRID_RUN_REPORT"] = S.report_path(sched["id"], run_no, self.env)
        return env

    def _spawn_threads(self, run, prompt):
        proc = run.proc

        def feed():
            try:
                proc.stdin.write(prompt.encode("utf-8"))
                proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass

        def read_out():
            try:
                for raw in iter(proc.stdout.readline, b""):
                    run.stream.feed(raw)
            except (OSError, ValueError):  # closed under us after a leftover process held the pipe
                pass

        def read_err():
            try:
                for raw in iter(proc.stderr.readline, b""):
                    run.stderr.append(raw.decode("utf-8", "replace").rstrip("\n"))
            except (OSError, ValueError):
                pass

        def waiter():
            proc.wait()
            self.wake.set()
        for fn in (feed, read_out, read_err, waiter):
            t = threading.Thread(target=fn, daemon=True)
            t.start()
            run.threads.append(t)

    # ---- resume and failover

    def _profile_of_session(self, path):
        m = re.search(r"/\.omp/profiles/([^/]+)/agent/sessions/", path)
        return m.group(1) if m else None

    def _resume_file(self, sched, state, retry, profile, label, chat=False):
        """(path to `--resume`, note). The failed session after a failover; for a chat turn the bot's main session
        (the newest session of any run or turn), else the previous session of an agent with continuity `resume`;
        moved to the target profile with omp-switch when the account differs."""
        cand, src, failover = None, None, False
        if retry and retry.get("resume_file"):
            cand, src, failover = retry["resume_file"], retry.get("from_profile"), True
        elif state.get("session_file") and (chat or sched["kind"] == "agent" and sched["identity"]["continuity"] == "resume"):
            cand = state["session_file"]
            src = state.get("session_profile", self._profile_of_session(cand))
        if cand and chat and not os.path.isfile(cand):
            return None, "the bot's session file is gone; this message started a new conversation"
        if not cand or not os.path.isfile(cand):
            return None, None
        if src == profile:
            return cand, None
        if not src or not profile:
            return None, "the session could not move between accounts (one has no omp profile); started fresh"
        try:
            r = subprocess.run([sys.executable, self.switch_bin(), "transfer", "--session", cand, "--from", src, "--to", profile],
                               capture_output=True, text=True, timeout=SWITCH_TIMEOUT_S, env=self.env)
            out = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
        except (OSError, ValueError, subprocess.TimeoutExpired, IndexError) as exc:
            return None, f"omp-switch failed ({exc}); started fresh on {label}"
        if out.get("state") == "ok" and isinstance(out.get("session"), str) and os.path.isfile(out["session"]):
            return out["session"], None
        return None, f"omp-switch: {out.get('reason') or 'no session'}; started fresh on {label}" + (" (failover)" if failover else "")

    # ---- starts that never become a process

    def _start_failed(self, sched, run_no, trigger, attempt, now, error):
        sid, sdir = sched["id"], self.sdir(sched["id"])
        rec = S.append_run(sdir, {"run": run_no, "schedule": sid, "trigger": trigger, "mode": "headless", "started": now,
                                  "ended": now, "status": "failed", "attempt": attempt, "error": error}, sched["name"])
        if trigger == "chat":   # shown in the conversation only: no breaker, no retry, no notification
            S.update_state(sdir, lambda st: st.update(chat={"active": False, "started": None, "error": error}))
            self.log(f"{sid}: chat turn #{run_no} not started: {error}")
            return
        S.update_state(sdir, lambda st: S.record_outcome(sched, st, rec))
        self.log(f"{sid}: run #{run_no} not started: {error}")
        self._notify(sched, rec)

    def _defer(self, sched, run_no, trigger, attempt, now, until, text, retry):
        sid, sdir = sched["id"], self.sdir(sched["id"])
        S.append_run(sdir, {"run": run_no, "schedule": sid, "trigger": trigger, "mode": "headless", "started": now,
                            "ended": now, "status": "deferred", "attempt": attempt, "summary": text,
                            "reset_at": int(until)}, sched["name"])
        keep = dict(retry or {})
        keep.update(at=int(until), attempt=attempt)
        S.update_state(sdir, lambda st: st.update(retry=keep))
        self.log(f"{sid}: run #{run_no} deferred: {text}")

    def _budget_note(self, now, spent, budget):
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if self._budget_noted == day:
            return
        self._budget_noted = day
        self._push("Hivegrid bots: daily budget reached",
                   f"${spent:.2f} of ${budget:g} spent today; bots wait until tomorrow (manual runs still start)", "3",
                   "moneybag")

    # ---- a finished run

    def _locate_session(self, run):
        sid = run.stream.session_id
        if run.resume_file and os.path.isfile(run.resume_file):
            return run.resume_file
        if not sid:
            return None
        home = self.env.get("HOME") or os.path.expanduser("~")
        base = (os.path.join(home, ".omp", "profiles", run.profile, "agent", "sessions") if run.profile
                else os.path.join(home, ".omp", "agent", "sessions"))
        hits = glob.glob(os.path.join(glob.escape(base), "*", f"*_{glob.escape(sid)}.jsonl"))
        return max(hits, key=lambda p: os.path.getmtime(p)) if hits else None

    @staticmethod
    def _session_limit(path):
        """`A.limit_of_entry` of the last message entry of a session file (its last SESSION_TAIL bytes)."""
        import hivegrid_agents as A
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                start = max(0, size - SESSION_TAIL)
                f.seek(start)
                data = f.read(SESSION_TAIL)
        except OSError:
            return None
        lines = data.split(b"\n")
        if start > 0:
            lines = lines[1:]
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if isinstance(entry, dict) and entry.get("type") == "message":
                return A.limit_of_entry(entry)
        return None

    def _finish(self, run, rc, ended):
        sched, sid = run.sched, run.sched["id"]
        sdir = self.sdir(sid)
        _killpg(run.proc.pid, signal.SIGTERM)  # leftovers of the run (background jobs) must not outlive it
        for t in run.threads:
            t.join(timeout=READERS_JOIN_S)  # the readers end at EOF; a leftover holding the pipes costs this much
        for pipe in (run.proc.stdout, run.proc.stderr):
            with contextlib.suppress(OSError, ValueError):
                pipe.close()
        stream = run.stream
        session_file = self._locate_session(run)
        limit = None
        if run.killed is None and rc != 3 and (rc != 0 or stream.last_stop in ("error", "aborted")):
            limit = self._session_limit(session_file) if session_file else None
            if limit is None and stream.last_assistant:
                import hivegrid_agents as A
                limit = A.limit_of_entry({"type": "message", "message": stream.last_assistant})
        elapsed = ended - run.started
        killed = run.killed or ("stop" if self.stopping and rc is not None and rc < 0 else None)
        status, error = classify(exit_code=rc, stream=stream, elapsed=elapsed, max_time=sched["run"]["max_time_s"],
                                 killed=killed, limit=limit)
        if status == "failed" and error and error.startswith("omp exited with status"):
            hint = next((ln.strip() for ln in run.stderr if ln.strip() and not ln.lower().startswith("warning")), "")
            error = f"{error}: {hint[:300]}" if hint else error
        if run.note:
            error = f"{error}; {run.note}" if error else run.note
        if run.chat:   # a chat turn: the reply is its summary; no report file, no level, nothing for the briefing
            report_file, report, parsed = None, None, None
            summary = stream.text[:200] if stream.text else None
        else:
            report_file = S.report_path(sid, run.run_no, self.env)
            report = self._read_report(report_file)
            if report is None and stream.text:
                report = self._save_report(report_file, stream.text)
            parsed = S.parse_report(report or "", sched["kind"])
            # an agent that marked its report (level/headline) is summarised by headline and body; else the text as is
            summary = ((parsed["headline"] + ("\n\n" + parsed["body"] if parsed["body"] else "")) if parsed["marked"] and parsed["headline"]
                       else report if report else stream.text) or None
        self._write_log(sdir, run, rc, error)
        headline = (parsed["headline"] or None) if parsed else None   # a failed run gets its automatic one in append_run
        rec = {"run": run.run_no, "schedule": sid, "trigger": run.trigger, "mode": run.mode, "started": run.started,
               "ended": ended, "status": status, "attempt": run.attempt, "exit": rc, "profile": run.profile,
               "model": sched["run"]["model"] or stream.model, "session_file": session_file,
               "session_id": stream.session_id, "cost_usd": round(stream.cost, 6) if stream.has_cost else None,
               "tokens": dict(stream.tokens) or None, "summary": summary[:S.SUMMARY_MAX] if summary else None,
               "report_path": report_file if report is not None else None, "error": error,
               "reset_at": limit["reset_at"] if limit else None,
               "level": parsed["level"] if parsed else "none", "headline": headline}
        rec = S.append_run(sdir, rec, sched["name"])
        accounts, fallback = None, None
        if status == "limit":
            try:
                accounts = self._accounts() if sched["policy"]["fallback_accounts"] else {}
            except Exception:  # noqa: BLE001 - failover is best effort; the plain retry at reset_at still applies
                accounts = {}
            fallback = self._pick_fallback(sched, run.label, ended, accounts)

        retrying = []

        def fn(st):
            st["running"] = None
            if session_file:
                st["session_file"], st["session_profile"] = session_file, run.profile
            if run.chat:   # no breaker, no retry; a failed turn is shown in the conversation only
                gone_by_user = status == "cancelled" and (error or "").startswith(S.CANCELLED_BY_USER)
                st["chat"] = {"active": False, "started": None,
                              "error": None if status == "ok" or gone_by_user else (error or status)}
                return st
            if status == "cancelled":
                return st
            st = S.record_outcome(sched, st, rec)
            if st.get("paused_reason"):
                st["retry"] = None
                return st
            at = S.retry_plan(sched, rec, run.attempt, ended, self.jitter)
            if at is not None:
                retry = {"at": int(at), "attempt": run.attempt + 1, "account": None, "resume_file": None,
                         "from_profile": None}
                if status == "limit" and fallback:
                    retry.update(at=int(ended), account=fallback, resume_file=session_file, from_profile=run.profile)
                st["retry"] = retry
                retrying.append(at)
            return st
        S.update_state(sdir, fn)
        with contextlib.suppress(OSError):
            S.enforce_memory_cap(sched, self.env)
        self._prune_logs(sdir)
        self.log(f"{sid}: {'chat turn' if run.chat else 'run'} #{run.run_no} {status}"
                 + (f" (${rec['cost_usd']:.3f})" if rec["cost_usd"] else ""))
        # a failed attempt that will be retried stays quiet; the final outcome (and every limit) notifies
        if not run.chat and (not retrying or status == "limit"):
            self._notify(sched, rec, retry_at=retrying[0] if retrying else None)
        return rec

    @staticmethod
    def _read_report(path):
        try:
            st = os.lstat(path)
        except OSError:
            return None
        if st.st_size > REPORT_MAX or st.st_size == 0:
            return None
        try:
            text = S._read_text_trusted(path, REPORT_MAX)
        except OSError:
            return None
        return text.strip() or None

    @staticmethod
    def _save_report(path, text):
        try:
            S.atomic_write(path, text.strip() + "\n")
        except OSError:
            return None
        return text.strip()

    @staticmethod
    def _write_log(sdir, run, rc, error):
        tail = "\n".join(run.stderr)[-LOG_TAIL:]
        head = f"# run #{run.run_no} exit {rc}" + (f" ({error})" if error else "") + "\n"
        try:
            S.atomic_write(os.path.join(sdir, f"run-{run.run_no}.log"), head + tail + ("\n" if tail else ""))
        except OSError:
            pass

    @staticmethod
    def _prune_logs(sdir):
        try:
            logs = sorted((n for n in os.listdir(sdir) if re.fullmatch(r"run-\d+\.log", n)),
                          key=lambda n: int(n[4:-4]))
        except OSError:
            return
        for n in logs[:-LOGS_KEPT]:
            with contextlib.suppress(OSError):
                os.unlink(os.path.join(sdir, n))

    # ---- notifications

    def _notify(self, sched, rec, retry_at=None):
        """Push one message for a run that matters, body = its headline. A quiet run (`none`) and a chat turn never
        notify. A failure, limit or miss is `important` and follows the bot's `notify.on` and the settings' event
        list as before. An `ok` run of level `important` (the agent's own report) notifies whenever the bot has
        notifications at all; an `ok` run of level `info` only through the `done` opt-in."""
        status, level = rec["status"], rec.get("level") or "info"
        if rec.get("trigger") == "chat" or level == "none":
            return
        event = EVENT_OF_STATUS.get(status)
        allowed = set(self.settings().get("notify_events") or SETTINGS_DEFAULTS["notify_events"])
        if sched["notify"]["done"]:
            allowed.add("done")  # `notify.done` of a bot is an explicit opt-in
        if status == "ok" and level == "important":
            if not sched["notify"]["on"]:
                return
        elif event is None or event not in sched["notify"]["on"] or event not in allowed:
            return
        import hivegrid_remote_notify as N
        name = N.line(sched["name"], 60)
        word = "done" if status == "ok" else status
        body = N.line(rec.get("headline") or rec.get("error") or rec.get("summary") or "", 300)
        if status == "limit":
            body = N.line((body + "; " if body else "") + (f"retrying at {_hhmm(retry_at)}" if retry_at else "no retry left"), 300)
        body = body or word
        prio, tag = NOTIFY_LOOK.get(status, ("3", None))
        self._push(f"Hivegrid bot: {name} {word}", body, prio, tag)

    def _push(self, title, body, priority, tag):
        cfg = self.settings()
        url, web = cfg.get("notify_url"), bool(cfg.get("push"))
        if not url and not web:
            return

        def go():
            if url:
                try:
                    self.notify_fn(url, title, body, priority, tag)
                except Exception:  # noqa: BLE001 - a notification that did not arrive never affects a run
                    pass
            if web:
                try:
                    self.push_fn({"title": title, "body": body, "tag": "bots", "url": "#/bots"},
                                 "high" if priority == "4" else "normal")
                except Exception as exc:  # noqa: BLE001 - Web Push is best effort, but the daemon says why it did not go
                    self.log(f"push failed: {type(exc).__name__}")
        if self.notify_async:
            t = threading.Thread(target=go, daemon=True)
            self._pushes = [p for p in self._pushes if p.is_alive()] + [t]
            t.start()
        else:
            go()

    # ---- loops

    def run_forever(self):
        self.startup()
        self.log(f"started (pid {self.pid}, max_parallel {self.settings()['max_parallel']})")
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._on_signal)
        try:
            while not self.stopping:
                self.wake.clear()
                self.tick()
                self.wake.wait(TICK_S)
        finally:
            self.shutdown("signal")
        for t in self._pushes:
            t.join(timeout=N_JOIN_S)
        self.log("stopped")
        return 0

    def run_once(self):
        """Fire what is due, wait for every run (and the ones that had to wait for a free slot), exit."""
        self.startup()
        while True:
            self.wake.clear()
            started = self.tick()
            if not self.live and not started:
                break
            self.wake.wait(0.2)
        self.shutdown("once")
        return 0

    def _on_signal(self, signum, frame):  # noqa: ARG002
        self.stopping = True
        self.wake.set()


# ---------------------------------------------------------------- entry

def acquire_lock(env):
    """The exclusive, non-blocking flock that makes the scheduler a single instance; None when held. The file
    stays open (and locked) for the life of the process; it holds the pid for humans."""
    cdir = S.command_dir(env)
    S.ensure_private_dir(cdir)
    path = os.path.join(cdir, "scheduler.lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        st = os.fstat(fd)
        if not os.path.isfile(path) or st.st_uid != os.getuid():
            raise S.ScheduleError(f"{path} is not a lock file of this user")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return fd


def build_parser():
    ap = argparse.ArgumentParser(
        prog="hivegrid-scheduler",
        description="Runs the Bots (scheduled omp agents). See docs/schedules.md, 'Scheduler'. Without a command it runs "
                    "the daemon in the foreground.")
    sub = ap.add_subparsers(dest="command")
    sub.add_parser("run", help="the daemon: tick until SIGTERM or SIGINT (the default command)")
    due = sub.add_parser("run-due", help="start what is due now, wait for those runs and exit (no daemon)")
    due.add_argument("--once", action="store_true", help="accepted for clarity; run-due always runs once")
    return ap


def main(argv=None, *, env=None):
    args = build_parser().parse_args(argv)
    env = dict(os.environ if env is None else env)
    try:
        fd = acquire_lock(env)
    except OSError as exc:
        print(f"hivegrid-scheduler: {exc}", file=sys.stderr)
        return 1
    if fd is None:
        print("hivegrid-scheduler: another scheduler is running (one per user); "
              "`hivegrid-schedule run ID` asks it to run a bot now", file=sys.stderr)
        return 1
    sched = Scheduler(env, notify_async=args.command != "run-due")
    try:
        return sched.run_once() if args.command == "run-due" else sched.run_forever()
    except OSError as exc:
        print(f"hivegrid-scheduler: {exc}", file=sys.stderr)
        return 1
    finally:
        os.close(fd)
