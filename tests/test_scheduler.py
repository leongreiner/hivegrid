"""bin/hivegrid_scheduler.py: what the daemon starts, records and decides. Real child processes run a fake `omp`
(a script that records its argv, stdin and environment and prints canned JSON lines, `HIVEGRID_OMP_BIN`),
the clock is a fake one, the notifier and the account/quota lookups are injected, and a temp HOME holds every
file. No real omp, network or home is touched."""
import calendar
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
sys.dont_write_bytecode = True

import hivegrid_schedule as S  # noqa: E402
import hivegrid_scheduler as H  # noqa: E402


def E(y, mo, d, h=0, mi=0, s=0):
    return calendar.timegm((y, mo, d, h, mi, s))


BASE = E(2026, 11, 2, 9, 0, 30)       # a Monday, 30 s after the 09:00 UTC slot
SLOT = E(2026, 11, 2, 9)
CRON = {"type": "cron", "expr": "0 9 * * *", "tz": "UTC"}
EVERY5 = {"type": "cron", "expr": "*/5 * * * *", "tz": "UTC"}

FAKE_OMP = r'''#!__PYTHON__
import json, os, sys, time

argv = sys.argv[1:]
stdin = sys.stdin.read()
d = os.environ["FAKE_OMP_DIR"]
os.makedirs(d, exist_ok=True)
cnt = os.path.join(d, "count")
n = int(open(cnt).read()) if os.path.exists(cnt) else 0
open(cnt, "w").write(str(n + 1))
with open(os.path.join(d, "call-%d.json" % n), "w") as f:
    json.dump({"argv": argv, "stdin": stdin, "env": dict(os.environ), "cwd": os.getcwd()}, f)
seq = os.environ.get("FAKE_OMP_SEQ", "ok").split(",")
mode = seq[min(n, len(seq) - 1)]


def opt(name):
    return argv[argv.index(name) + 1] if name in argv else None


home = os.environ["HOME"]
profile = opt("--profile")
resume = opt("--resume")
base = os.path.join(home, ".omp", "profiles", profile, "agent", "sessions") if profile else os.path.join(home, ".omp", "agent", "sessions")
if resume:
    path = resume
    sid = os.path.basename(path)[:-6].split("_", 1)[1]
else:
    sid = "sess%04d" % n
    path = os.path.join(base, "--work--", "20261102T090000Z_%s.jsonl" % sid)
os.makedirs(os.path.dirname(path), exist_ok=True)


def emit(ev):
    sys.stdout.write(json.dumps(ev) + "\n")
    sys.stdout.flush()


def entry(msg):
    with open(path, "a") as f:
        f.write(json.dumps({"type": "message", "id": "x", "timestamp": "2026-11-02T09:00:31.000Z", "message": msg}) + "\n")


def assistant(text, stop="stop", cost=0.05, **extra):
    m = {"role": "assistant", "content": [{"type": "text", "text": text}] if text else [], "stopReason": stop,
         "model": "claude-test", "provider": "anthropic", "timestamp": int(os.environ.get("FAKE_OMP_NOW_MS", "1793523630000")),
         "usage": {"input": 10, "output": 5, "totalTokens": 15, "cost": {"total": cost}}}
    m.update(extra)
    return m


emit({"type": "session", "version": 3, "id": sid, "timestamp": "2026-11-02T09:00:30.000Z", "cwd": os.getcwd()})
emit({"type": "message_update", "message": {"role": "assistant"}})
sys.stdout.write("this line is not json\n")
text = os.environ.get("FAKE_OMP_TEXT", "pong %d" % n)
entry({"role": "user", "content": [{"type": "text", "text": stdin}], "timestamp": 1793523630000})
if os.environ.get("FAKE_OMP_REPORT"):
    rp = os.environ["HIVEGRID_RUN_REPORT"]
    os.makedirs(os.path.dirname(rp), exist_ok=True)
    with open(rp, "w") as f:
        f.write(os.environ["FAKE_OMP_REPORT"])
slow = float(os.environ.get("FAKE_OMP_SLEEP", "0").split(",")[min(n, len(os.environ.get("FAKE_OMP_SLEEP", "0").split(",")) - 1)])
if slow:
    open(os.path.join(d, "live-%d" % n), "w").close()
    time.sleep(slow)


def finish(msg, code):
    entry(msg)
    emit({"type": "turn_end", "message": msg, "toolResults": []})
    emit({"type": "message_end", "message": msg})
    emit({"type": "agent_end", "messages": [msg]})
    sys.stderr.write("warning: MCP server unreachable\n")
    sys.exit(code)


if mode == "hang":
    time.sleep(600)
elif mode == "fail":
    finish(assistant("", "error", 0.01, errorMessage="boom: the model gave up"), 1)
elif mode == "cost":
    finish(assistant("partial", "stop", 0.31), 3)
elif mode == "limit":
    msg = assistant("", "error", 0.0, errorStatus=429,
                    errorMessage='429 {"type":"error","error":{"type":"rate_limit_error"}} retry-after-ms=7200000')
    finish(msg, 1)
elif mode == "refuse":
    emit({"type": "tool_execution_end", "toolCallId": "t1", "toolName": "bash", "isError": True,
          "result": {"content": [{"type": "text", "text": 'Tool "bash" requires approval but no interactive UI available.'}]}})
    finish(assistant("I could not run the command"), 0)
elif mode == "report":
    rp = os.environ["HIVEGRID_RUN_REPORT"]
    os.makedirs(os.path.dirname(rp), exist_ok=True)
    with open(rp, "w") as f:
        f.write("# Report\nfrom the file\n")
    finish(assistant("chat text"), 0)
elif mode == "bigmem":
    with open(os.path.join(opt("--add-dir"), "memory.md"), "w") as f:
        f.write("".join("line %04d\n" % i for i in range(1000)))
    finish(assistant("memory written"), 0)
elif mode == "nomodel":
    sys.stderr.write("warning: MCP server unreachable\nNo default model selected. Use /login.\n")
    sys.exit(1)
elif mode == "dirty":
    finish(assistant("bad\x07 text\nsecond line " + "x" * 400, "error", 0.0, errorMessage="bad\x07 error\nnext"), 1)
else:
    finish(assistant(text), 0)
'''


def slurp(path):
    with open(path) as f:
        return f.read()


class Clock:
    def __init__(self, t=BASE):
        self.t = t

    def __call__(self):
        return self.t


class SchedulerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.mkdtemp(prefix="hg-sched-fake-")
        cls.fake = os.path.join(cls.shared, "omp")
        with open(cls.fake, "w") as f:
            f.write(FAKE_OMP.replace("__PYTHON__", sys.executable))
        os.chmod(cls.fake, 0o755)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.shared, ignore_errors=True)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hg-sched-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        home = os.path.join(self.tmp, "home")
        rt = os.path.join(self.tmp, "rt")
        self.work = os.path.join(self.tmp, "work")
        for d in (home, rt, self.work):
            os.mkdir(d, 0o700)
        self.home = home
        self.env = {
            "HOME": home, "XDG_CONFIG_HOME": os.path.join(home, ".config"),
            "XDG_STATE_HOME": os.path.join(home, ".local", "state"), "XDG_RUNTIME_DIR": rt,
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "HIVEGRID_OMP_BIN": self.fake,
            "FAKE_OMP_DIR": os.path.join(self.tmp, "calls"), "HIVEGRID_SETTINGS": os.path.join(home, "settings.json"),
            "WEZTERM_PANE": "7", "WEZTERM_UNIX_SOCKET": "/nowhere", "WEZTERM_EXECUTABLE": "/nowhere/wezterm",
            "OMP_PROFILE": "parent", "PI_PROFILE": "parent", "PI_CODING_AGENT_DIR": "/nowhere",
            "PI_SESSION_FILE": "/nowhere/x.jsonl", "PI_SESSION_ID": "parent-id",
            "FAKE_OMP_NOW_MS": str(BASE * 1000),
        }
        self.clock = Clock()
        self.store = S.Store(env=self.env)
        self.notes = []
        self.cfg = {"max_parallel": 2, "tz": "local", "allow_full": False, "daily_budget_usd": 0,
                    "notify_events": ["failed", "limit", "missed"], "notify_url": "http://ntfy.test/hivegrid"}
        self.accounts = {}
        self.quotas = {}
        self.schedulers = []

    # ---- builders

    def scheduler(self, **kw):
        sch = H.Scheduler(self.env, clock=self.clock, accounts=lambda: self.accounts,
                          quota=lambda label, now: self.quotas.get(label, {"five_hour": None}),
                          notify=lambda *a: self.notes.append(a), jitter=0, log=lambda m: None,
                          settings=lambda: dict(self.cfg), **kw)
        self.schedulers.append(sch)
        self.addCleanup(self.reap, sch)
        sch.startup()
        return sch

    @staticmethod
    def reap(sch):
        for run in list(sch.live.values()):
            try:
                os.killpg(run.proc.pid, 9)
            except OSError:
                pass
            run.proc.wait(timeout=10)
            for t in run.threads:
                t.join(timeout=5)
            run.proc.stdout.close()
            run.proc.stderr.close()

    def spec(self, name="Digest", when=None, run=None, policy=None, identity=None, notify=None, kind="task",
             prompt="Summarize the news."):
        out = {"name": name, "kind": kind, "when": when or dict(CRON), "task": {"prompt": prompt},
               "run": dict({"cwd": self.work}, **(run or {}))}
        for key, val in (("policy", policy), ("identity", identity), ("notify", notify)):
            if val:
                out[key] = val
        return out

    def add(self, baseline=BASE - 9000, **kw):
        rec = self.store.add(self.spec(**kw), now=E(2026, 11, 1))
        S.init_state_dir(rec, baseline, self.env)
        return rec

    def sdir(self, rec):
        return S.state_dir(rec["id"], self.env)

    def state(self, rec):
        return S.load_state(self.sdir(rec))

    def runs(self, rec):
        return S.read_runs(self.sdir(rec))

    def calls(self):
        d = self.env["FAKE_OMP_DIR"]
        if not os.path.isdir(d):
            return []
        names = sorted((n for n in os.listdir(d) if n.startswith("call-")), key=lambda n: int(n[5:-5]))
        out = []
        for n in names:
            with open(os.path.join(d, n)) as f:
                out.append(json.load(f))
        return out

    def settle(self, sch, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            sch.tick()
            if not sch.live:
                return
            sch.wake.wait(0.05)
            sch.wake.clear()
        self.fail("runs did not finish: " + ", ".join(sch.live))

    def wait_live(self, sch, sid, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if os.path.exists(os.path.join(self.env["FAKE_OMP_DIR"], "count")):
                time.sleep(0.1)
                return
            time.sleep(0.02)
        self.fail("the fake omp never started")


class Firing(SchedulerCase):
    def test_cron_slot_fires_a_headless_run_and_records_it(self):
        rec = self.add()
        sch = self.scheduler()
        self.assertEqual(sch.tick(), 1)
        self.assertEqual(self.state(rec)["running"]["run"], 1)
        self.settle(sch)
        run, = self.runs(rec)
        self.assertEqual((run["status"], run["trigger"], run["mode"], run["attempt"], run["exit"]),
                         ("ok", "cron", "headless", 1, 0))
        self.assertEqual(run["summary"], "pong 0")
        self.assertEqual(run["session_id"], "sess0000")
        self.assertAlmostEqual(run["cost_usd"], 0.05)
        self.assertEqual(run["tokens"]["totalTokens"], 15)
        self.assertEqual(run["model"], "anthropic/claude-test")
        self.assertTrue(run["session_file"].endswith("20261102T090000Z_sess0000.jsonl"))
        self.assertTrue(os.path.isfile(run["session_file"]))
        self.assertFalse(run["seen"])
        st = self.state(rec)
        self.assertEqual((st["running"], st["last_slot"], st["run_count"], st["consec_failures"]), (None, SLOT, 1, 0))
        self.assertIn("warning: MCP server unreachable", slurp(os.path.join(self.sdir(rec), "run-1.log")))
        self.assertEqual(sch.tick(), 0, "the same slot never fires twice")
        self.assertEqual(len(self.calls()), 1)

    def test_once_schedule_fires_once(self):
        rec = self.add(when={"type": "once", "at": "2026-11-02T09:00", "tz": "UTC"})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        self.clock.t += 86400
        sch.tick()
        self.assertEqual([r["status"] for r in self.runs(rec)], ["ok"])

    def test_catchup_within_grace_runs_once_for_the_newest_slot(self):
        rec = self.add(baseline=E(2026, 11, 1, 8))
        self.clock.t = E(2026, 11, 2, 10, 0)   # asleep over the 09:00 slot, back an hour later
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        run, = self.runs(rec)
        self.assertEqual((run["status"], run["trigger"]), ("ok", "catchup"))
        self.assertEqual(self.state(rec)["last_slot"], SLOT)

    def test_missed_beyond_grace_is_recorded_and_notified_without_a_run(self):
        rec = self.add(baseline=E(2026, 11, 1, 8))
        self.clock.t = E(2026, 11, 2, 12, 0)   # three hours late, grace is two
        sch = self.scheduler()
        self.assertEqual(sch.tick(), 0)
        run, = self.runs(rec)
        self.assertEqual(run["status"], "missed")
        self.assertFalse(run["seen"])
        self.assertIn("missed", run["summary"])
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.state(rec)["last_slot"], SLOT)
        sch.tick()
        self.assertEqual(len(self.runs(rec)), 1)
        title, = [n[1] for n in self.notes]
        self.assertEqual(title, "Hivegrid bot: Digest missed")

    def test_visible_and_hybrid_modes_without_a_hivegrid_window_run_headless_and_say_so(self):
        # the real GUI lookup: no `gui-sock-*` in the (temp) runtime dir, so no window to open a pane in
        for mode in ("visible", "hybrid"):
            rec = self.add(name=mode, run={"mode": mode})
            sch = self.scheduler()
            sch.tick()
            self.settle(sch)
            run, = self.runs(rec)
            self.assertEqual((run["mode"], run["status"]), (f"headless/{mode}", "ok"))
            self.assertIn("no running Hivegrid window", run["mode_fallback"])


class Overlap(SchedulerCase):
    def test_overlap_skip_drops_the_new_slot(self):
        rec = self.add(when=dict(EVERY5), run={"max_time_s": 600}, policy={"overlap": "skip"})
        self.env["FAKE_OMP_SEQ"] = "hang"
        sch = self.scheduler()
        sch.tick()
        self.wait_live(sch, rec["id"])
        self.clock.t = E(2026, 11, 2, 9, 5, 20)
        sch.tick()
        self.assertEqual(len(sch.live), 1)
        skipped = [r for r in self.runs(rec) if r["status"] == "skipped"]
        self.assertEqual(len(skipped), 1)
        self.assertTrue(skipped[0]["seen"])
        self.assertEqual(self.state(rec)["last_slot"], E(2026, 11, 2, 9, 5))
        self.assertEqual(len(self.calls()), 1)

    def test_overlap_queue_keeps_one_queued_run_and_cancel_drops_it(self):
        self.env["FAKE_OMP_SEQ"] = "hang,ok"
        rec = self.add(when=dict(EVERY5), run={"max_time_s": 600}, policy={"overlap": "queue"})
        sch = self.scheduler()
        sch.tick()
        self.wait_live(sch, rec["id"])
        self.clock.t = E(2026, 11, 2, 9, 5, 20)
        sch.tick()
        self.assertTrue(self.state(rec)["queued"])
        self.clock.t = E(2026, 11, 2, 9, 10, 20)
        sch.tick()
        self.assertEqual([r["status"] for r in self.runs(rec)], ["skipped"], "only one run waits in the queue")
        S.send_command(S.command_dir(self.env), {"op": "cancel", "id": rec["id"]})
        self.settle(sch)
        self.assertEqual([r["status"] for r in self.runs(rec)], ["skipped", "cancelled"])
        self.assertFalse(self.state(rec)["queued"])
        self.assertEqual(len(self.calls()), 1)

    def test_queued_run_starts_when_the_live_one_ends(self):
        self.env["FAKE_OMP_SEQ"] = "hang,ok"
        rec = self.add(when=dict(EVERY5), run={"max_time_s": 600}, policy={"overlap": "queue", "retries": 0})
        sch = self.scheduler()
        sch.tick()
        self.wait_live(sch, rec["id"])
        self.clock.t = E(2026, 11, 2, 9, 5, 20)
        sch.tick()
        self.assertTrue(self.state(rec)["queued"])
        os.killpg(sch.live[rec["id"]].proc.pid, 9)  # the live run ends (badly)
        self.settle(sch)  # the queued run starts in the tick that reaps the dead one
        self.assertEqual([r["status"] for r in self.runs(rec)], ["failed", "ok"])
        self.assertFalse(self.state(rec)["queued"])

    def test_concurrency_cap_queues_the_rest_fifo(self):
        self.cfg["max_parallel"] = 1
        self.env["FAKE_OMP_SEQ"] = "ok"
        a = self.add(name="Early", when={"type": "cron", "expr": "0 8 * * *", "tz": "UTC"}, baseline=E(2026, 11, 2, 7))
        b = self.add(name="Late", baseline=E(2026, 11, 2, 7))
        self.clock.t = E(2026, 11, 2, 9, 0, 40)
        sch = self.scheduler()
        self.assertEqual(sch.tick(), 1)
        self.assertEqual(list(sch.live), [a["id"]], "the older slot goes first")
        self.assertEqual(sch.waiting, 1)
        end = time.monotonic() + 20
        while time.monotonic() < end and not self.runs(b):
            sch.tick()
            sch.wake.wait(0.05)
        self.assertEqual([r["status"] for r in self.runs(a)], ["ok"])
        self.settle(sch)
        self.assertEqual([r["status"] for r in self.runs(b)], ["ok"])
        self.assertEqual(sch.waiting, 0)


class Outcomes(SchedulerCase):
    def test_failed_run_keeps_the_model_error_and_schedules_a_retry(self):
        self.env["FAKE_OMP_SEQ"] = "fail"
        rec = self.add(policy={"retries": 1, "backoff_s": [300]})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        run, = self.runs(rec)
        self.assertEqual((run["status"], run["exit"]), ("failed", 1))
        self.assertIn("boom: the model gave up", run["error"])
        self.assertEqual(self.state(rec)["retry"]["at"], BASE + 300)
        self.assertEqual(self.notes, [], "an attempt that will be retried stays quiet")
        self.clock.t = BASE + 299
        self.assertEqual(sch.tick(), 0)
        self.clock.t = BASE + 301
        self.assertEqual(sch.tick(), 1)
        self.settle(sch)
        last = self.runs(rec)[-1]
        self.assertEqual((last["trigger"], last["attempt"], last["status"]), ("retry", 2, "failed"))
        self.assertIsNone(self.state(rec)["retry"], "retries are used up")
        self.assertEqual([n[1] for n in self.notes], ["Hivegrid bot: Digest failed"])

    def test_a_failure_without_a_model_error_shows_the_first_stderr_line(self):
        self.env["FAKE_OMP_SEQ"] = "nomodel"
        rec = self.add(policy={"retries": 0})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        run, = self.runs(rec)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error"], "omp exited with status 1: No default model selected. Use /login.")

    def test_cost_exit_is_status_cost_and_never_retried(self):
        self.env["FAKE_OMP_SEQ"] = "cost"
        rec = self.add(run={"max_cost_usd": 0.3})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        run, = self.runs(rec)
        self.assertEqual((run["status"], run["exit"]), ("cost", 3))
        self.assertAlmostEqual(run["cost_usd"], 0.31)
        self.assertIsNone(self.state(rec)["retry"])
        self.assertEqual(self.state(rec)["consec_failures"], 1)

    def test_timeout_kills_the_process_group_after_max_time_plus_a_minute(self):
        self.env["FAKE_OMP_SEQ"] = "hang"
        rec = self.add(run={"max_time_s": 60}, policy={"retries": 0})
        sch = self.scheduler()
        sch.tick()
        self.wait_live(sch, rec["id"])
        proc = sch.live[rec["id"]].proc
        self.clock.t = BASE + 100
        sch.tick()
        self.assertIsNone(proc.poll(), "max_time_s + 60 s has not passed yet")
        self.clock.t = BASE + 125
        self.settle(sch)
        run, = self.runs(rec)
        self.assertEqual(run["status"], "timeout")
        self.assertIn("killed", run["error"])
        self.assertIsNotNone(proc.poll())
        self.assertEqual([n[1] for n in self.notes], ["Hivegrid bot: Digest timeout"])

    def test_limit_retries_at_the_reset_time(self):
        self.env["FAKE_OMP_SEQ"] = "limit,ok"
        rec = self.add()
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        run, = self.runs(rec)
        reset = BASE + 7200
        self.assertEqual(run["status"], "limit")
        self.assertEqual(run["reset_at"], reset)
        self.assertEqual(self.state(rec)["retry"]["at"], reset)
        self.assertEqual(len(self.notes), 1)
        self.assertIn("limit", self.notes[0][1])
        self.assertIn("retrying at", self.notes[0][2])
        self.clock.t = reset - 1
        self.assertEqual(sch.tick(), 0)
        self.clock.t = reset
        self.assertEqual(sch.tick(), 1)
        self.settle(sch)
        last = self.runs(rec)[-1]
        self.assertEqual((last["trigger"], last["attempt"], last["status"]), ("retry", 2, "ok"))

    def test_limit_fails_over_to_a_fallback_account_and_resumes_the_session(self):
        self.env["FAKE_OMP_SEQ"] = "limit,ok"
        self.accounts = {"first": "pa", "second": "pb", "third": "pc"}
        self.quotas = {"second": {"five_hour": {"pct": 97.0, "reset_at": None}},
                       "third": {"five_hour": {"pct": 12.0, "reset_at": None}}}
        rec = self.add(run={"account": "first"}, policy={"fallback_accounts": ["second", "third"]})
        sch = self.scheduler()
        sch.tick()
        sch.live[rec["id"]].proc.wait(timeout=10)
        sch._reap(self.clock())  # one step: the run ended, the retry is planned but has not started
        first, = self.runs(rec)
        self.assertEqual((first["status"], first["profile"]), ("limit", "pa"))
        self.assertIn(os.path.join(".omp", "profiles", "pa"), first["session_file"])
        retry = self.state(rec)["retry"]
        self.assertEqual((retry["account"], retry["from_profile"]), ("third", "pa"))
        self.assertEqual(retry["at"], BASE, "a failover does not wait for the reset")
        self.settle(sch)
        second = self.runs(rec)[-1]
        self.assertEqual((second["status"], second["profile"], second["trigger"]), ("ok", "pc", "retry"))
        call = self.calls()[1]
        self.assertEqual(call["argv"][0:2], ["--profile", "pc"])
        resume = call["argv"][call["argv"].index("--resume") + 1]
        self.assertIn(os.path.join(".omp", "profiles", "pc", "agent", "sessions"), resume)
        self.assertTrue(os.path.isfile(resume), "omp-switch copied the session into the target profile")
        self.assertIn("usage limit", call["stdin"])

    def test_needs_approval_refusal_is_kept_in_the_record(self):
        self.env["FAKE_OMP_SEQ"] = "refuse"
        rec = self.add()
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        run, = self.runs(rec)
        self.assertEqual(run["status"], "ok")
        self.assertIn("needed approval", run["error"])
        self.assertEqual(run["summary"], "I could not run the command")

    def test_breaker_pauses_after_consecutive_failures(self):
        self.env["FAKE_OMP_SEQ"] = "fail"
        rec = self.add(policy={"retries": 0, "pause_after_failures": 2})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        self.clock.t = E(2026, 11, 3, 9, 0, 20)
        sch.tick()
        self.settle(sch)
        st = self.state(rec)
        self.assertEqual(st["consec_failures"], 2)
        self.assertIn("did not succeed", st["paused_reason"])
        self.clock.t = E(2026, 11, 4, 9, 0, 20)
        self.assertEqual(sch.tick(), 0)
        self.assertEqual(len(self.calls()), 2)

    def test_memory_file_is_capped_after_the_run(self):
        self.env["FAKE_OMP_SEQ"] = "bigmem"
        rec = self.add(kind="agent", identity={"memory_max_bytes": 1024})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        path = S.memory_path(rec["id"], self.env)
        text = slurp(path)
        self.assertLessEqual(len(text.encode()), 1024)
        self.assertTrue(text.endswith("line 0999\n"), "the newest lines survive")

    def test_report_file_becomes_the_summary(self):
        self.env["FAKE_OMP_SEQ"] = "report"
        rec = self.add()
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        run, = self.runs(rec)
        self.assertEqual(run["summary"], "# Report\nfrom the file")
        self.assertEqual(run["report_path"], S.report_path(rec["id"], 1, self.env))

    def test_without_a_report_the_final_text_is_saved_as_the_report(self):
        rec = self.add()
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        run, = self.runs(rec)
        self.assertEqual(slurp(run["report_path"]), "pong 0\n")

    def test_unknown_account_fails_clearly_and_does_not_start_omp(self):
        self.accounts = {"known": "pk"}
        rec = self.add(run={"account": "ghost"}, policy={"retries": 0})
        sch = self.scheduler()
        sch.tick()
        run, = self.runs(rec)
        self.assertEqual(run["status"], "failed")
        self.assertIn("'ghost' is unknown", run["error"])
        self.assertEqual(self.calls(), [])
        self.assertEqual(len(self.notes), 1)


class Prompts(SchedulerCase):
    def test_agent_prompt_has_the_previous_summary_and_the_memory_path(self):
        self.env["FAKE_OMP_TEXT"] = "all quiet on the feeds"
        rec = self.add(kind="agent", prompt="Watch the feeds.")
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        self.clock.t = E(2026, 11, 3, 9, 0, 20)
        sch.tick()
        self.settle(sch)
        first, second = [c["stdin"] for c in self.calls()]
        self.assertIn("There is no previous run.", first)
        self.assertIn("Previous run (#1, ok): all quiet on the feeds", second)
        self.assertIn("This is run #2", second)
        self.assertIn(S.memory_path(rec["id"], self.env), second)
        self.assertIn("Watch the feeds.", second)
        self.assertIn(S.report_path(rec["id"], 2, self.env), second)

    def test_task_prompt_goes_on_stdin_and_names_the_report(self):
        rec = self.add(prompt="Check the build.")
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        call, = self.calls()
        self.assertTrue(call["stdin"].startswith("Check the build."))
        self.assertIn(S.report_path(rec["id"], 1, self.env), call["stdin"])
        self.assertNotIn("Check the build.", " ".join(call["argv"]))

    def test_agent_with_resume_continuity_resumes_the_previous_session(self):
        rec = self.add(kind="agent", identity={"continuity": "resume"})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        self.clock.t = E(2026, 11, 3, 9, 0, 20)
        sch.tick()
        self.settle(sch)
        first, second = self.calls()
        self.assertNotIn("--resume", first["argv"])
        self.assertEqual(second["argv"][second["argv"].index("--resume") + 1], self.runs(rec)[0]["session_file"])
        self.assertEqual(self.runs(rec)[1]["session_id"], self.runs(rec)[0]["session_id"])

    def test_agent_with_fresh_continuity_never_resumes(self):
        self.add(kind="agent", identity={"continuity": "fresh"})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        self.clock.t = E(2026, 11, 3, 9, 0, 20)
        sch.tick()
        self.settle(sch)
        for call in self.calls():
            self.assertNotIn("--resume", call["argv"])


class Command(SchedulerCase):
    def test_argv_of_an_edit_run_is_an_exact_list(self):
        rec = self.add(run={"thinking": "high", "model": "anthropic/claude-test", "max_cost_usd": 0.3,
                            "max_time_s": 120, "add_dirs": [self.tmp]})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        call, = self.calls()
        sdir = self.sdir(rec)
        self.assertEqual(call["argv"], [
            "--mode", "json", "-p", "--max-time", "120", "--max-cost", "0.3", "--approval-mode", "write",
            "--thinking", "high", "--model", "anthropic/claude-test",
            "--append-system-prompt", os.path.join(sdir, "identity.md"), "--add-dir", sdir, "--add-dir", self.tmp])
        self.assertEqual(os.path.realpath(call["cwd"]), os.path.realpath(self.work))
        self.assertIn("unattended", slurp(os.path.join(sdir, "identity.md")))

    def test_readonly_run_gets_only_the_reading_tools(self):
        self.add(run={"permissions": "readonly"})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        argv = self.calls()[0]["argv"]
        self.assertEqual(argv[argv.index("--tools") + 1], "read,grep,find,glob,web_search,lsp")
        self.assertEqual(argv[argv.index("--approval-mode") + 1], "write")

    def test_full_permissions_need_the_allow_full_setting(self):
        run = {"permissions": "full", "max_time_s": 600, "max_cost_usd": 1}
        rec = self.add(run=run, policy={"retries": 0})
        sch = self.scheduler()
        sch.tick()
        refused, = self.runs(rec)
        self.assertEqual(refused["status"], "failed")
        self.assertIn("allow_full", refused["error"])
        self.assertEqual(self.calls(), [])
        self.cfg["allow_full"] = True
        S.update_state(self.sdir(rec), lambda st: st.update(run_now=True))
        sch.tick()
        self.settle(sch)
        argv = self.calls()[0]["argv"]
        self.assertEqual(argv[argv.index("--approval-mode") + 1], "yolo")
        self.assertNotIn("--tools", argv)

    def test_account_label_becomes_an_omp_profile(self):
        self.accounts = {"work": "work-profile", "plain": None}
        self.add(name="With", run={"account": "work"})
        self.add(name="Plain", run={"account": "plain"})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        by_profile = {("--profile" in c["argv"]): c["argv"] for c in self.calls()}
        self.assertEqual(by_profile[True][0:2], ["--profile", "work-profile"])
        self.assertNotIn("--profile", by_profile[False])

    def test_child_environment_is_scrubbed_and_names_the_run(self):
        self.accounts = {"work": "work-profile"}
        rec = self.add(run={"account": "work"})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        env = self.calls()[0]["env"]
        self.assertFalse([k for k in env if k.startswith("WEZTERM_")])
        for key in ("PI_SESSION_FILE", "PI_SESSION_ID", "OMP_PROFILE", "PI_PROFILE", "PI_CODING_AGENT_DIR"):
            self.assertNotIn(key, env)
        self.assertEqual(env["HIVEGRID_SCHEDULE"], rec["id"])
        self.assertEqual(env["HIVEGRID_RUN"], "1")
        self.assertEqual(env["HIVEGRID_RUN_REPORT"], S.report_path(rec["id"], 1, self.env))
        self.assertEqual(env["HOME"], self.home)
        self.assertEqual(env["PATH"], self.env["PATH"] + os.pathsep + os.path.join(self.home, ".local", "bin"))

    def test_without_an_account_the_parents_profile_is_not_inherited_either(self):
        self.add()
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        env = self.calls()[0]["env"]
        for key in ("PI_SESSION_FILE", "OMP_PROFILE", "PI_PROFILE", "PI_CODING_AGENT_DIR", "WEZTERM_PANE"):
            self.assertNotIn(key, env)
        self.assertNotIn("--profile", self.calls()[0]["argv"])

    def test_missing_cwd_fails_the_run(self):
        rec = self.add(policy={"retries": 0})
        shutil.rmtree(self.work)
        sch = self.scheduler()
        sch.tick()
        run, = self.runs(rec)
        self.assertEqual(run["status"], "failed")
        self.assertIn("does not exist", run["error"])

    def test_a_missing_omp_binary_is_a_failed_run_not_a_crash(self):
        self.env["HIVEGRID_OMP_BIN"] = os.path.join(self.tmp, "no-such-omp")
        rec = self.add(policy={"retries": 0})
        sch = self.scheduler()
        sch.tick()
        run, = self.runs(rec)
        self.assertEqual(run["status"], "failed")
        self.assertIn("cannot start omp", run["error"])

    def test_omp_in_the_home_bin_is_found_when_the_unit_path_lacks_it(self):
        # a systemd user unit's PATH has no ~/.local/bin, where omp is installed
        bindir = os.path.join(self.home, ".local", "bin")
        os.makedirs(bindir)
        os.symlink(self.fake, os.path.join(bindir, "omp"))
        del self.env["HIVEGRID_OMP_BIN"]
        self.env["PATH"] = "/usr/bin:/bin"
        self.add()
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        self.assertEqual(len(self.calls()), 1)


class Guards(SchedulerCase):
    def test_quota_guard_uses_a_fallback_account_under_the_threshold(self):
        self.accounts = {"first": "pa", "second": "pb"}
        self.quotas = {"first": {"five_hour": {"pct": 95.0, "reset_at": BASE + 3600}},
                       "second": {"five_hour": {"pct": 20.0, "reset_at": None}}}
        rec = self.add(run={"account": "first"}, policy={"fallback_accounts": ["second"]})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        self.assertEqual(self.runs(rec)[0]["profile"], "pb")

    def test_quota_guard_without_a_fallback_defers_to_the_reset(self):
        self.accounts = {"first": "pa"}
        self.quotas = {"first": {"five_hour": {"pct": 95.0, "reset_at": BASE + 3600}}}
        rec = self.add(run={"account": "first"})
        sch = self.scheduler()
        self.assertEqual(sch.tick(), 1)
        run, = self.runs(rec)
        self.assertEqual((run["status"], run["reset_at"]), ("deferred", BASE + 3600))
        self.assertTrue(run["seen"])
        self.assertIn("95%", run["summary"])
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.state(rec)["retry"]["at"], BASE + 3600)
        self.clock.t = BASE + 3601
        self.quotas["first"] = {"five_hour": {"pct": 3.0, "reset_at": None}}
        sch.tick()
        self.settle(sch)
        self.assertEqual([r["status"] for r in self.runs(rec)], ["deferred", "ok"])

    def test_quota_deferral_is_capped_at_six_hours(self):
        self.accounts = {"first": "pa"}
        self.quotas = {"first": {"five_hour": {"pct": 99.0, "reset_at": BASE + 40 * 3600}}}
        rec = self.add(run={"account": "first"})
        sch = self.scheduler()
        sch.tick()
        self.assertEqual(self.state(rec)["retry"]["at"], BASE + 6 * 3600)

    def test_missing_quota_data_never_blocks_a_run(self):
        self.accounts = {"first": "pa"}
        rec = self.add(run={"account": "first"})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        self.assertEqual(self.runs(rec)[0]["status"], "ok")

    def test_daily_budget_defers_runs_and_notifies_once(self):
        self.cfg["daily_budget_usd"] = 0.10
        a = self.add(name="One")
        b = self.add(name="Two")
        S.append_run(self.sdir(a), {"run": 1, "schedule": a["id"], "trigger": "cron", "status": "ok",
                                    "started": BASE - 600, "ended": BASE - 500, "cost_usd": 0.25})
        sch = self.scheduler()
        sch.tick()
        self.assertEqual(self.calls(), [])
        for rec in (a, b):
            deferred = [r for r in self.runs(rec) if r["status"] == "deferred"]
            self.assertEqual(len(deferred), 1)
            self.assertIn("daily budget", deferred[0]["summary"])
        self.assertEqual([n[1] for n in self.notes], ["Hivegrid bots: daily budget reached"])
        # a run asked for by hand is the user's decision
        S.send_command(S.command_dir(self.env), {"op": "run-now", "id": a["id"]})
        sch.tick()
        self.settle(sch)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.runs(a)[-1]["trigger"], "manual")

    def test_budget_off_by_default(self):
        rec = self.add()
        S.append_run(self.sdir(rec), {"run": 1, "schedule": rec["id"], "trigger": "cron", "status": "ok",
                                      "started": BASE - 60, "ended": BASE - 50, "cost_usd": 99})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        self.assertEqual(self.runs(rec)[-1]["status"], "ok")


class Inbox(SchedulerCase):
    def test_run_now_starts_a_manual_run(self):
        rec = self.add(baseline=BASE)
        self.clock.t = BASE - 3600
        sch = self.scheduler()
        self.assertEqual(sch.tick(), 0)
        S.send_command(S.command_dir(self.env), {"op": "run-now", "id": rec["id"]})
        self.assertEqual(sch.tick(), 1)
        self.settle(sch)
        run, = self.runs(rec)
        self.assertEqual((run["trigger"], run["status"]), ("manual", "ok"))
        self.assertFalse(self.state(rec)["run_now"])

    def test_skip_marks_the_next_slot_handled(self):
        rec = self.add(baseline=E(2026, 11, 2, 8))
        self.clock.t = E(2026, 11, 2, 8, 30)
        sch = self.scheduler()
        S.send_command(S.command_dir(self.env), {"op": "skip", "id": rec["id"]})
        sch.tick()
        self.assertEqual(self.state(rec)["last_slot"], SLOT)
        self.clock.t = BASE
        self.assertEqual(sch.tick(), 0)
        self.assertEqual(self.calls(), [])

    def test_unskip_brings_the_skipped_slot_back(self):
        rec = self.add(baseline=E(2026, 11, 2, 8))
        self.clock.t = E(2026, 11, 2, 8, 30)
        sch = self.scheduler()
        S.send_command(S.command_dir(self.env), {"op": "skip", "id": rec["id"]})
        sch.tick()
        self.assertEqual(self.state(rec)["last_slot"], SLOT)
        S.send_command(S.command_dir(self.env), {"op": "unskip", "id": rec["id"]})
        sch.tick()
        self.assertIsNone(self.state(rec)["last_slot"])

    def test_cancel_stops_the_live_run(self):
        self.env["FAKE_OMP_SEQ"] = "hang"
        rec = self.add(run={"max_time_s": 600}, policy={"retries": 2})
        sch = self.scheduler()
        sch.tick()
        self.wait_live(sch, rec["id"])
        proc = sch.live[rec["id"]].proc
        S.send_command(S.command_dir(self.env), {"op": "cancel", "id": rec["id"]})
        self.settle(sch)
        run, = self.runs(rec)
        self.assertEqual(run["status"], "cancelled")
        self.assertTrue(run["seen"])
        self.assertIsNotNone(proc.poll())
        st = self.state(rec)
        self.assertEqual((st["running"], st["consec_failures"], st.get("retry")), (None, 0, None))
        self.assertEqual(self.notes, [])

    def test_commands_for_unknown_schedules_and_reload_are_harmless(self):
        sch = self.scheduler()
        S.send_command(S.command_dir(self.env), {"op": "run-now", "id": "no-such-bot"})
        S.send_command(S.command_dir(self.env), {"op": "reload"})
        self.assertEqual(sch.tick(), 0)
        self.assertEqual(os.listdir(S.command_dir(self.env)), [])

    def test_new_schedule_is_picked_up_without_a_reload_command(self):
        sch = self.scheduler()
        sch.tick()
        self.add()
        self.assertEqual(sch.tick(), 1)
        self.settle(sch)


class Lifecycle(SchedulerCase):
    def test_stale_running_record_is_closed_as_failed_on_start(self):
        rec = self.add(policy={"retries": 0})
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        S.update_state(self.sdir(rec), lambda st: st.update(
            running={"run": 4, "pid": dead.pid, "started": BASE - 100}, run_count=4))
        self.scheduler()
        st = self.state(rec)
        self.assertIsNone(st["running"])
        self.assertEqual(st["consec_failures"], 1)
        run, = self.runs(rec)
        self.assertEqual((run["run"], run["status"]), (4, "failed"))
        self.assertIn("scheduler stopped", run["error"])
        self.assertEqual(len(self.notes), 1)

    def test_shutdown_stops_live_runs_and_records_them_cancelled(self):
        self.env["FAKE_OMP_SEQ"] = "hang"
        rec = self.add(run={"max_time_s": 600})
        sch = self.scheduler()
        sch.tick()
        self.wait_live(sch, rec["id"])
        proc = sch.live[rec["id"]].proc
        sch.shutdown("test")
        self.assertIsNotNone(proc.poll())
        run, = self.runs(rec)
        self.assertEqual(run["status"], "cancelled")
        self.assertIsNone(self.state(rec)["running"])
        doc = S.read_summary(S.summary_path(self.env))
        self.assertNotIn("heartbeat", doc["scheduler"])
        self.assertFalse(S.daemon_alive(self.env, self.clock()))

    def test_tick_writes_a_heartbeat_summary(self):
        rec = self.add()
        sch = self.scheduler()
        self.clock.t = BASE - 3600
        sch.tick()
        doc = S.read_summary(S.summary_path(self.env))
        self.assertEqual(doc["scheduler"]["heartbeat"], BASE - 3600)
        self.assertEqual(doc["scheduler"]["pid"], os.getpid())
        self.assertEqual([i["id"] for i in doc["items"]], [rec["id"]])
        self.assertTrue(S.daemon_alive(self.env, BASE - 3600 + 10))
        self.clock.t += 5
        sch.tick()
        self.assertEqual(S.read_summary(S.summary_path(self.env))["scheduler"]["heartbeat"], BASE - 3600,
                         "an idle tick does not rewrite the file more than every few seconds")
        self.clock.t += H.SUMMARY_EVERY_S
        sch.tick()
        self.assertEqual(S.read_summary(S.summary_path(self.env))["scheduler"]["heartbeat"], self.clock.t)

    def test_summary_follows_a_run(self):
        self.add()
        sch = self.scheduler()
        sch.tick()
        self.assertEqual(S.read_summary(S.summary_path(self.env))["items"][0]["state"], "working")
        self.settle(sch)
        sch.tick()
        item = S.read_summary(S.summary_path(self.env))["items"][0]
        self.assertEqual((item["state"], item["last"]["status"]), ("done", "ok"))

    def test_one_scheduler_per_user(self):
        fd = H.acquire_lock(self.env)
        self.assertIsNotNone(fd)
        self.addCleanup(os.close, fd)
        self.assertIsNone(H.acquire_lock(self.env))

    def test_run_due_once_fires_and_exits(self):
        rec = self.add(baseline=time.time(), when={"type": "once", "at": "2099-01-01T09:00", "tz": "UTC"})
        S.update_state(self.sdir(rec), lambda st: st.update(run_now=True))
        env = dict(self.env)
        rc = H.main(["run-due", "--once"], env=env)
        self.assertEqual(rc, 0)
        run, = self.runs(rec)
        self.assertEqual((run["trigger"], run["status"]), ("manual", "ok"))
        self.assertFalse(S.daemon_alive(self.env), "run-due leaves no heartbeat behind")

    def test_run_due_refuses_while_another_scheduler_holds_the_lock(self):
        fd = H.acquire_lock(self.env)
        self.addCleanup(os.close, fd)
        old = sys.stderr
        sys.stderr = io.StringIO()
        self.addCleanup(setattr, sys, "stderr", old)
        self.assertEqual(H.main(["run-due", "--once"], env=dict(self.env)), 1)
        self.assertIn("another scheduler", sys.stderr.getvalue())


class Notifications(SchedulerCase):
    def test_title_body_priority_and_sanitising(self):
        self.env["FAKE_OMP_SEQ"] = "dirty"
        self.add(name="Nightly build", policy={"retries": 0})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        (url, title, body, priority, tag), = self.notes
        self.assertEqual(url, "http://ntfy.test/hivegrid")
        self.assertEqual(title, "Hivegrid bot: Nightly build failed")
        self.assertEqual((priority, tag), ("4", "x"))
        self.assertNotRegex(body, r"[\x00-\x1f]")
        self.assertEqual(" ".join(body.split()), "Nightly build failed: bad error")
        self.assertLessEqual(len(body), 300 + 12)

    def test_done_notifies_only_when_enabled(self):
        self.add(name="Quiet", kind="agent")
        self.add(name="Loud", kind="agent", notify={"done": True})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        (url, title, body, priority, tag), = self.notes
        self.assertEqual(title, "Hivegrid bot: Loud done")
        self.assertIn("pong", body)
        self.assertRegex(body, r"^pong \d$", "the body is the headline, nothing else")

    def test_the_settings_event_list_filters_what_a_bot_asks_for(self):
        self.cfg["notify_events"] = ["done"]
        self.env["FAKE_OMP_SEQ"] = "fail"
        self.add(name="Quiet", policy={"retries": 0})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        self.assertEqual(self.notes, [], "the bot asks for failed, the settings only allow done")

    def test_no_url_means_no_push(self):
        self.cfg["notify_url"] = None
        self.env["FAKE_OMP_SEQ"] = "fail"
        self.add(policy={"retries": 0})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        self.assertEqual(self.notes, [])

    def test_a_failing_notifier_never_affects_the_run(self):
        self.env["FAKE_OMP_SEQ"] = "fail"
        rec = self.add(policy={"retries": 0})

        def broken(*a):
            raise OSError("network down")
        sch = H.Scheduler(self.env, clock=self.clock, accounts=lambda: {}, quota=lambda *a: {}, notify=broken,
                          jitter=0, log=lambda m: None, settings=lambda: dict(self.cfg))
        self.addCleanup(self.reap, sch)
        sch.startup()
        sch.tick()
        self.settle(sch)
        self.assertEqual(self.runs(rec)[0]["status"], "failed")
        self.assertIsNone(self.state(rec)["running"])


class Levels(SchedulerCase):
    """What the agent says about its own run decides whether it reaches the Briefing and the phone."""

    def run_once(self, rec, **env):
        self.env.update(env)
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        return self.runs(rec)[-1]

    def test_a_quiet_agent_run_is_none_seen_and_not_in_the_briefing(self):
        rec = self.add(kind="agent", notify={"on": ["failed", "limit", "missed", "done"]})
        run = self.run_once(rec, FAKE_OMP_REPORT="level: none\nheadline: CI green, nothing to do\n\nChecked 3 repos.\n")
        self.assertEqual((run["level"], run["headline"], run["seen"], run["status"]), ("none", "CI green, nothing to do", True, "ok"))
        self.assertIn("CI green, nothing to do", run["summary"])
        self.assertEqual(S.briefing(self.env), [])
        self.assertEqual(self.notes, [], "a quiet run never notifies, even with done switched on")
        self.assertEqual(S.build_summary(BASE, env=self.env)["briefing"], {"unseen": 0, "newest": []})

    def test_an_important_agent_run_is_in_the_briefing_and_notifies_with_the_headline(self):
        rec = self.add(kind="agent", name="CI watch")
        headline = "CI of repo X fixed: flaky test timeout, now green"
        run = self.run_once(rec, FAKE_OMP_REPORT=f"level: important\nheadline: {headline}\n\nChanged the timeout.\n")
        self.assertEqual((run["level"], run["headline"], run["seen"]), ("important", headline, False))
        entry, = S.briefing(self.env)
        self.assertEqual((entry["bot_name"], entry["headline"], entry["run"], entry["seen"]), ("CI watch", headline, 1, False))
        (url, title, body, priority, tag), = self.notes
        self.assertEqual((title, body), ("Hivegrid bot: CI watch done", headline))
        self.assertEqual(S.build_summary(BASE, env=self.env)["briefing"]["unseen"], 1)

    def test_a_routine_agent_run_is_info_and_without_a_marker_too(self):
        rec = self.add(kind="agent")
        run = self.run_once(rec, FAKE_OMP_REPORT="level: info\nheadline: Rotated 2 logs\n\nok\n")
        self.assertEqual((run["level"], run["headline"], run["seen"]), ("info", "Rotated 2 logs", False))
        self.assertEqual(S.briefing(self.env), [])
        rec2 = self.add(kind="agent", name="Other")
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        plain = self.runs(rec2)[-1]
        self.assertEqual(plain["level"], "info")
        self.assertEqual(self.notes, [])

    def test_a_task_defaults_to_important(self):
        rec = self.add(prompt="Summarize the news.")
        run = self.run_once(rec)
        self.assertEqual((run["level"], run["headline"]), ("important", "pong 0"))
        self.assertEqual([e["bot_id"] for e in S.briefing(self.env)], [rec["id"]])

    def test_a_failed_run_is_important_whatever_the_report_says(self):
        self.env["FAKE_OMP_SEQ"] = "fail"
        rec = self.add(kind="agent", name="CI watch", policy={"retries": 0})
        run = self.run_once(rec, FAKE_OMP_REPORT="level: none\nheadline: all fine\n\nbody")
        self.assertEqual((run["status"], run["level"], run["headline"]), ("failed", "important", "CI watch failed: boom: the model gave up"))
        self.assertEqual(S.briefing(self.env)[0]["headline"], "CI watch failed: boom: the model gave up")
        self.assertEqual(self.notes[0][2], "CI watch failed: boom: the model gave up")

    def test_status_forces_important_for_limit_timeout_cost_and_missed(self):
        for status in ("limit", "timeout", "cost", "missed", "deferred"):
            with self.subTest(status=status):
                self.assertEqual(S.normalize_run({"run": 1, "schedule": "abc", "trigger": "cron", "status": status,
                                                  "level": "none"})["level"], "important")
        for status, error in (("skipped", None), ("cancelled", S.CANCELLED_BY_USER)):
            self.assertEqual(S.normalize_run({"run": 1, "schedule": "abc", "trigger": "cron", "status": status,
                                              "error": error, "level": "important"})["level"], "none")
        stopped = S.normalize_run({"run": 1, "schedule": "abc", "trigger": "cron", "status": "cancelled",
                                   "error": "the scheduler was stopped"})
        self.assertEqual((stopped["level"], stopped["seen"]), ("important", True), "a stop the user did not ask for is shown")

    def test_a_run_cancelled_by_the_user_is_none(self):
        self.env["FAKE_OMP_SEQ"] = "hang"
        rec = self.add(kind="agent")
        sch = self.scheduler()
        sch.tick()
        self.wait_live(sch, rec["id"])
        S.send_command(S.command_dir(self.env), {"op": "cancel", "id": rec["id"]})
        self.settle(sch)
        run, = self.runs(rec)
        self.assertEqual((run["status"], run["level"], run["seen"]), ("cancelled", "none", True))
        self.assertEqual((S.briefing(self.env), self.notes), ([], []))

    def test_a_paused_bot_shows_in_the_briefing_through_its_failed_run(self):
        self.env["FAKE_OMP_SEQ"] = "fail"
        rec = self.add(kind="agent", policy={"retries": 0, "pause_after_failures": 1})
        self.run_once(rec)
        self.assertTrue(self.state(rec)["paused_reason"])
        self.assertEqual(len(S.briefing(self.env)), 1)

    def test_the_prompt_tells_the_agent_how_to_report(self):
        rec = self.add(kind="agent")
        self.run_once(rec)
        stdin = self.calls()[0]["stdin"]
        self.assertIn("level: none|info|important", stdin)
        self.assertIn("headline:", stdin)


class Chat(SchedulerCase):
    """Talking to a bot: a headless turn resumed in its one main session, run by the daemon."""

    def say(self, rec, text):
        S.send_command(S.command_dir(self.env), {"op": "chat", "id": rec["id"], "text": text})

    def quiet_bot(self, **kw):
        kw.setdefault("when", {"type": "once", "at": "2099-01-01T09:00", "tz": "UTC"})
        return self.add(baseline=BASE, **kw)

    def test_the_first_chat_starts_the_session_and_the_next_resumes_it(self):
        rec = self.quiet_bot(kind="agent", name="Pal")
        sch = self.scheduler()
        self.say(rec, "hello bot")
        self.assertEqual(sch.tick(), 1)
        st = self.state(rec)
        self.assertEqual((st["running"]["chat"], st["chat"]["active"], st["chat_queue"]), (True, True, []))
        self.assertEqual(S.build_summary(BASE, env=self.env)["items"][0]["state"], "working")
        self.settle(sch)
        self.say(rec, "and again")
        sch.tick()
        self.settle(sch)
        first, second = self.runs(rec)
        self.assertEqual((first["trigger"], first["level"], first["seen"], first["status"]), ("chat", "none", True, "ok"))
        self.assertEqual(first["summary"], "pong 0")
        self.assertEqual(first["session_id"], "sess0000")
        self.assertEqual(second["session_id"], "sess0000", "the same session")
        c1, c2 = self.calls()
        self.assertEqual(c1["stdin"], "hello bot", "the prompt is the user's text and nothing else")
        self.assertEqual(c2["stdin"], "and again")
        self.assertNotIn("--resume", c1["argv"])
        self.assertEqual(c2["argv"][c2["argv"].index("--resume") + 1], first["session_file"])
        self.assertEqual(self.state(rec)["chat"], {"active": False, "started": None, "error": None})
        self.assertEqual((S.briefing(self.env), self.notes), ([], []), "a chat turn never reaches the briefing or the phone")
        self.assertEqual(self.state(rec)["consec_failures"], 0)

    def test_the_chat_turn_runs_like_a_run_with_the_bots_permissions_and_caps(self):
        rec = self.quiet_bot(kind="agent", run={"permissions": "readonly", "max_cost_usd": 0.3, "max_time_s": 120})
        sch = self.scheduler()
        self.say(rec, "hi")
        sch.tick()
        self.settle(sch)
        call, = self.calls()
        argv = call["argv"]
        self.assertEqual(argv[argv.index("--tools") + 1], H.READONLY_TOOLS)
        self.assertEqual((argv[argv.index("--max-cost") + 1], argv[argv.index("--max-time") + 1]), ("0.3", "120"))
        self.assertIn("--append-system-prompt", argv)
        for key in H.SCRUB + ("WEZTERM_PANE",):
            self.assertNotIn(key, call["env"])
        self.assertEqual(call["env"]["HIVEGRID_SCHEDULE"], rec["id"])

    def test_a_chat_continues_the_session_of_the_latest_run_of_a_task(self):
        rec = self.add(prompt="Check it.")
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        self.say(rec, "what did you find?")
        sch.tick()
        self.settle(sch)
        run, chat = self.runs(rec)
        c0, c1 = self.calls()
        self.assertEqual(c1["argv"][c1["argv"].index("--resume") + 1], run["session_file"])
        self.assertEqual((chat["trigger"], chat["session_id"]), ("chat", run["session_id"]))

    def test_a_resumed_session_keeps_the_bots_cost_limit_on_top_of_what_it_already_cost(self):
        # omp's --max-cost limits the whole session: without this a second turn would stop at once (exit 3)
        rec = self.quiet_bot(kind="agent", run={"max_cost_usd": 0.5})
        sch = self.scheduler()
        self.say(rec, "one")
        sch.tick()
        self.settle(sch)
        self.say(rec, "two")
        sch.tick()
        self.settle(sch)
        c1, c2 = self.calls()
        self.assertEqual(c1["argv"][c1["argv"].index("--max-cost") + 1], "0.5")
        self.assertEqual(c2["argv"][c2["argv"].index("--max-cost") + 1], "0.5500", "0.5 plus the 0.05 the session cost")

    def test_the_scheduled_run_resumes_the_session_the_chat_grew(self):
        rec = self.quiet_bot(kind="agent")
        S.update_state(self.sdir(rec), lambda st: st.update(run_now=True))
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        self.say(rec, "remember: the repo is X")
        sch.tick()
        self.settle(sch)
        S.update_state(self.sdir(rec), lambda st: st.update(run_now=True))
        sch.tick()
        self.settle(sch)
        c0, c1, c2 = self.calls()
        self.assertEqual({c["argv"][c["argv"].index("--resume") + 1] for c in (c1, c2)},
                         {self.runs(rec)[0]["session_file"]})
        self.assertNotIn("Standing task", c1["stdin"])
        self.assertIn("Standing task", c2["stdin"])
        self.assertNotIn("remember", c2["stdin"])

    def test_messages_queue_behind_a_live_scheduled_run_and_run_in_order(self):
        self.env["FAKE_OMP_SLEEP"] = "1.0,0,0"
        rec = self.add(kind="agent")            # the 09:00 slot fires at once and takes a second
        sch = self.scheduler()
        sch.tick()
        self.wait_live(sch, rec["id"])
        self.say(rec, "first")
        self.say(rec, "second")
        sch.tick()
        self.assertEqual([m["text"] for m in self.state(rec)["chat_queue"]], ["first", "second"])
        self.assertEqual(S.conversation(rec["id"], self.env)["queue"], 2)
        self.assertTrue(S.conversation(rec["id"], self.env)["busy"])
        self.assertEqual(len(self.calls()), 1, "nothing starts while the run is live")
        self.settle(sch)
        self.assertEqual([r["trigger"] for r in self.runs(rec)], ["cron", "chat", "chat"])
        self.assertEqual([c["stdin"] for c in self.calls()][1:], ["first", "second"])
        self.assertEqual(self.state(rec)["chat_queue"], [])

    def test_the_queue_holds_five_and_refuses_the_rest_with_an_error(self):
        self.env["FAKE_OMP_SLEEP"] = "1.0,0,0,0,0,0,0"
        rec = self.quiet_bot(kind="agent")
        sch = self.scheduler()
        self.say(rec, "m0")
        sch.tick()
        self.wait_live(sch, rec["id"])
        for i in range(1, 8):
            self.say(rec, f"m{i}")
        sch.tick()
        st = self.state(rec)
        self.assertEqual([m["text"] for m in st["chat_queue"]], ["m1", "m2", "m3", "m4", "m5"])
        self.assertIn("queue_full", st["chat"]["error"])
        self.settle(sch)
        self.assertEqual([c["stdin"] for c in self.calls()], ["m0", "m1", "m2", "m3", "m4", "m5"])

    def test_a_slot_that_falls_due_during_a_chat_turn_is_queued_not_lost(self):
        self.env["FAKE_OMP_SLEEP"] = "1.0,0"
        rec = self.add(kind="agent", baseline=BASE, when=dict(EVERY5))
        sch = self.scheduler()
        self.say(rec, "talk")
        sch.tick()
        self.wait_live(sch, rec["id"])
        self.clock.t = E(2026, 11, 2, 9, 5, 10)
        sch.tick()
        st = self.state(rec)
        self.assertTrue(st["queued"])
        self.assertEqual(st["last_slot"], E(2026, 11, 2, 9, 5), "the slot is handled, not skipped")
        self.settle(sch)
        self.assertEqual([r["trigger"] for r in self.runs(rec)], ["chat", "cron"])
        self.assertEqual([r["status"] for r in self.runs(rec)], ["ok", "ok"])
        self.assertFalse(self.state(rec)["queued"])

    def test_a_scheduled_run_goes_before_a_waiting_message(self):
        rec = self.add(kind="agent", baseline=BASE - 9000)
        self.say(rec, "later")
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        self.assertEqual([r["trigger"] for r in self.runs(rec)], ["cron", "chat"])

    def test_a_failed_chat_turn_is_shown_in_the_conversation_only(self):
        self.env["FAKE_OMP_SEQ"] = "fail"
        rec = self.quiet_bot(kind="agent", policy={"retries": 2, "pause_after_failures": 1})
        sch = self.scheduler()
        self.say(rec, "hello")
        sch.tick()
        self.settle(sch)
        run, = self.runs(rec)
        self.assertEqual((run["trigger"], run["status"], run["level"], run["seen"]), ("chat", "failed", "none", True))
        st = self.state(rec)
        self.assertEqual(st["chat"]["error"], "boom: the model gave up")
        self.assertEqual((st["consec_failures"], st["paused_reason"], st.get("retry")), (0, None, None))
        self.assertEqual((S.briefing(self.env), self.notes), ([], []))
        self.assertEqual(S.conversation(rec["id"], self.env)["error"], "boom: the model gave up")
        self.assertEqual(S.build_summary(BASE, env=self.env)["items"][0]["chat"]["error"], "boom: the model gave up")

    def test_a_chat_that_cannot_start_reports_the_reason(self):
        rec = self.quiet_bot(kind="agent", run={"account": "nobody"})
        sch = self.scheduler()
        self.say(rec, "hello")
        sch.tick()
        run, = self.runs(rec)
        self.assertEqual((run["trigger"], run["status"], run["level"]), ("chat", "failed", "none"))
        self.assertIn("nobody", self.state(rec)["chat"]["error"])
        self.assertEqual(self.notes, [])

    def test_cancel_stops_a_live_turn_and_empties_the_queue(self):
        self.env["FAKE_OMP_SEQ"] = "hang"
        rec = self.quiet_bot(kind="agent")
        sch = self.scheduler()
        self.say(rec, "one")
        sch.tick()
        self.wait_live(sch, rec["id"])
        self.say(rec, "two")
        S.send_command(S.command_dir(self.env), {"op": "cancel", "id": rec["id"]})
        self.settle(sch)
        run, = self.runs(rec)
        self.assertEqual((run["trigger"], run["status"], run["level"]), ("chat", "cancelled", "none"))
        self.assertEqual(self.state(rec)["chat_queue"], [])
        self.assertIsNone(self.state(rec)["chat"]["error"])
        self.assertEqual(self.state(rec)["consec_failures"], 0)

    def test_a_chat_command_for_an_unknown_bot_or_with_junk_changes_nothing(self):
        rec = self.quiet_bot(kind="agent")
        sch = self.scheduler()
        S.send_command(S.command_dir(self.env), {"op": "chat", "id": "no-such-bot", "text": "hi"})
        with open(os.path.join(S.command_dir(self.env), "cmd.1-1.json"), "w") as f:
            json.dump({"op": "chat", "id": rec["id"]}, f)
        self.assertEqual(sch.tick(), 0)
        self.assertEqual(self.state(rec)["chat_queue"], [])

    def test_a_chat_turn_left_running_by_a_dead_scheduler_is_closed_as_a_chat_run(self):
        rec = self.quiet_bot(kind="agent")
        S.update_state(self.sdir(rec), lambda st: st.update(
            running={"run": 1, "pid": 2 ** 22 + 7, "started": BASE - 5, "chat": True}, run_count=1,
            chat={"active": True, "started": BASE - 5, "error": None}))
        self.scheduler()
        run, = self.runs(rec)
        self.assertEqual((run["trigger"], run["status"], run["level"]), ("chat", "failed", "none"))
        st = self.state(rec)
        self.assertEqual((st["running"], st["chat"]["active"], st["consec_failures"]), (None, False, 0))
        self.assertEqual((S.briefing(self.env), self.notes), ([], []))

    def test_the_conversation_is_read_from_the_session_file(self):
        import hivegrid_remote as R
        import hivegrid_remote_data as D
        rec = self.quiet_bot(kind="agent")
        sch = self.scheduler()
        self.say(rec, "what is up?")
        sch.tick()
        self.settle(sch)
        info = S.conversation(rec["id"], self.env)
        self.assertEqual((info["live"], info["busy"], info["queue"], info["error"]), (False, False, 0, None))
        path = D.session_path_ok(info["session_file"], self.home)
        self.assertEqual(path, os.path.realpath(info["session_file"]))
        items, _, _ = D.ChatCache(R.sanitize_text, R.sanitize_line).window(path, 20)
        self.assertEqual([(i["role"], i["text"]) for i in items], [("user", "what is up?"), ("assistant", "pong 0")])


class Pieces(unittest.TestCase):
    def feed(self, *events):
        s = H.Stream()
        for e in events:
            s.feed(json.dumps(e) if isinstance(e, dict) else e)
        return s

    def test_stream_sums_cost_takes_the_last_text_and_ignores_noise(self):
        msg = lambda text, cost, stop="stop": {"role": "assistant", "stopReason": stop, "usage": {  # noqa: E731
            "input": 1, "output": 2, "cost": {"total": cost}}, "content": [{"type": "text", "text": text}]}
        s = self.feed({"type": "session", "id": "abc"}, "garbage", "", "{broken",
                      {"type": "message_update", "message": {"role": "assistant"}},
                      {"type": "turn_end", "message": msg("first", 0.1)}, {"type": "message_end", "message": msg("first", 0.1)},
                      {"type": "turn_end", "message": msg("final answer", 0.25)},
                      {"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": "no"}]}},
                      {"type": "agent_end", "messages": []})
        self.assertEqual((s.session_id, s.text, s.last_stop), ("abc", "final answer", "stop"))
        self.assertAlmostEqual(s.cost, 0.35)
        self.assertEqual(s.tokens, {"input": 2, "output": 4})

    def test_stream_without_cost_reports_none(self):
        s = self.feed({"type": "turn_end", "message": {"role": "assistant", "stopReason": "stop", "content": []}})
        self.assertFalse(s.has_cost)

    def test_classify(self):
        ok = self.feed({"type": "message_end", "message": {"role": "assistant", "stopReason": "stop", "content": []}})
        bad = self.feed({"type": "message_end", "message": {"role": "assistant", "stopReason": "error", "errorMessage": "x broke",
                                                            "content": []}})
        args = dict(max_time=1800, killed=None, limit=None)
        self.assertEqual(H.classify(exit_code=0, stream=ok, elapsed=10, **args), ("ok", None))
        self.assertEqual(H.classify(exit_code=3, stream=ok, elapsed=10, **args)[0], "cost")
        self.assertEqual(H.classify(exit_code=1, stream=bad, elapsed=10, **args), ("failed", "x broke"))
        self.assertEqual(H.classify(exit_code=1, stream=bad, elapsed=1800, **args)[0], "timeout")
        self.assertEqual(H.classify(exit_code=1, stream=bad, elapsed=10, max_time=1800, killed=None,
                                    limit={"reset_at": 5})[0], "limit")
        self.assertEqual(H.classify(exit_code=-15, stream=H.Stream(), elapsed=10, max_time=1800, killed="cancel",
                                    limit=None)[0], "cancelled")
        self.assertEqual(H.classify(exit_code=-9, stream=H.Stream(), elapsed=10, max_time=1800, killed="watchdog",
                                    limit=None)[0], "timeout")
        self.assertIn("signal 9", H.classify(exit_code=-9, stream=H.Stream(), elapsed=10, **args)[1])
        self.assertIn("status 2", H.classify(exit_code=2, stream=H.Stream(), elapsed=10, **args)[1])

    def test_settings_reader_is_tolerant(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            env = {"HOME": d, "HIVEGRID_SETTINGS": path}
            self.assertEqual(H.read_settings(env)["max_parallel"], 2)
            with open(path, "w") as f:
                f.write("{not json")
            self.assertEqual(H.read_settings(env)["notify_url"], None)
            with open(path, "w") as f:
                json.dump({"schedule": {"max_parallel": 5, "daily_budget_usd": 1.5, "notify_events": ["done"]},
                           "remote": {"notify_url": "http://ntfy.test/x"}}, f)
            got = H.read_settings(env)
            self.assertEqual((got["max_parallel"], got["daily_budget_usd"], got["notify_events"], got["notify_url"]),
                             (5, 1.5, ["done"], "http://ntfy.test/x"))
            with open(path, "w") as f:
                json.dump({"schedule": {"max_parallel": 99, "allow_full": "yes"}}, f)
            got = H.read_settings(env)
            self.assertEqual((got["max_parallel"], got["allow_full"]), (2, False))

    def test_shim_is_executable_and_runs(self):
        shim = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin", "hivegrid-scheduler")
        self.assertTrue(os.stat(shim).st_mode & stat.S_IXUSR)
        r = subprocess.run([shim, "--help"], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0)
        self.assertIn("run-due", r.stdout)


if __name__ == "__main__":
    unittest.main()
