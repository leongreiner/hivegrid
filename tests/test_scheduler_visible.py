"""bin/hivegrid_scheduler.py, visible and hybrid runs: an interactive omp in a tab of the Bots window of the running
Hivegrid, observed (never signalled) through the pane's agent state file and its session file. The GUI is a fake
`wezterm cli` (`FakeMux`), the agent's state file and session file are written by the tests, the clock is a fake
one, and nothing touches a real home, display or omp."""
import json
import os
import shlex
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.dont_write_bytecode = True

import hivegrid_schedule as S  # noqa: E402
import hivegrid_scheduler as H  # noqa: E402
from test_scheduler import BASE, SLOT, SchedulerCase, slurp  # noqa: E402

LIMIT_TEXT = '429 {"type":"error","error":{"type":"rate_limit_error"}} retry-after-ms=7200000'


class FakeMux:
    """`wezterm cli` of a Hivegrid with one canvas window; `spawn` opens panes, `list` shows them. Every call is kept."""

    def __init__(self):
        self.calls = []
        self.up = True
        self.refuse_spawn = False
        self.panes = [{"pane_id": 3, "window_id": 0, "tab_id": 0, "tab_title": "", "title": "bash", "tty_name": "/dev/pts/3"}]
        self.next_pane, self.next_window = 40, 7

    def __call__(self, *args):
        self.calls.append(args)
        if not self.up:
            return None
        if args[0] == "list":
            return json.dumps(self.panes)
        if args[0] == "spawn":
            if self.refuse_spawn:
                return None
            cmd = list(args[args.index("--") + 1:])
            if "--new-window" in args:
                window = self.next_window
                self.next_window += 1
            else:
                window = int(args[args.index("--window-id") + 1])
            pane = self.next_pane
            self.next_pane += 1
            self.panes.append({"pane_id": pane, "window_id": window, "tab_id": 100 + pane, "tab_title": "", "title": "bash",
                               "tty_name": f"/dev/pts/{pane}", "cmd": cmd, "cwd": args[args.index("--cwd") + 1]})
            return f"{pane}\n"
        if args[0] == "set-tab-title":
            pane = int(args[args.index("--pane-id") + 1])
            for p in self.panes:
                if p["pane_id"] == pane:
                    p["tab_title"] = args[-1]
            return ""
        return ""

    def spawns(self):
        return [c for c in self.calls if c[0] == "spawn"]

    def touched_panes(self):
        """The calls that would disturb a pane's agent."""
        return [c for c in self.calls if c[0] in ("kill-pane", "send-text", "activate-pane", "split-pane")]

    def close(self, pane):
        self.panes = [p for p in self.panes if p["pane_id"] != pane]


class VisibleCase(SchedulerCase):
    def setUp(self):
        super().setUp()
        self.mux = FakeMux()
        self.agents = os.path.join(self.env["XDG_RUNTIME_DIR"], "hivegrid-agents")
        os.makedirs(self.agents, mode=0o700)
        self.sessions = os.path.join(self.home, ".omp", "agent", "sessions", "--work--")
        os.makedirs(self.sessions)

    def scheduler(self, **kw):
        return super().scheduler(gui=H.Gui(self.env, run=self.mux), **kw)

    @staticmethod
    def reap(sch):
        sch.live = {sid: run for sid, run in sch.live.items() if run.vis is None}   # a pane is not ours to kill
        SchedulerCase.reap(sch)

    # ---- what the agent in the pane does

    def session(self, name="20261102T090100Z_vis0001.jsonl"):
        return os.path.join(self.sessions, name)

    def agent_state(self, pane, state, session=None, **kw):
        doc = {"v": 1, "pane": pane, "pid": os.getpid(), "sessionId": "vis0001", "sessionFile": session or self.session(),
               "cwd": self.work, "state": state, "reason": None, "since": BASE, "updated": self.clock.t, "pending": None,
               "error": None, "limit": None}
        doc.update(kw)
        with open(os.path.join(self.agents, f"{pane}.json"), "w") as f:
            json.dump(doc, f)

    def say(self, text="all done", cost=0.07, stop="stop", path=None, **extra):
        """The agent's session file gains a user message and an assistant message."""
        path = path or self.session()
        msg = {"role": "assistant", "content": [{"type": "text", "text": text}] if text else [], "stopReason": stop,
               "model": "claude-test", "provider": "anthropic", "timestamp": BASE * 1000,
               "usage": {"input": 10, "output": 5, "totalTokens": 15, "cost": {"total": cost}}}
        msg.update(extra)
        fresh = not os.path.exists(path)
        with open(path, "a") as f:
            if fresh:
                f.write(json.dumps({"type": "session", "version": 3, "id": "vis0001", "cwd": self.work}) + "\n")
            f.write(json.dumps({"type": "message", "id": "m", "timestamp": "2026-11-02T09:00:40.000Z", "message": msg}) + "\n")

    def report(self, rec, text="level: important\nheadline: Fixed the build\n\nBody text.\n", run=1):
        path = S.report_path(rec["id"], run, self.env)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def at(self, sch, seconds):
        self.clock.t = BASE + seconds
        sch.tick()


class Opening(VisibleCase):
    def test_a_visible_run_opens_a_tab_in_a_new_bots_window_and_later_ones_reuse_it(self):
        a = self.add(name="Alpha", run={"mode": "visible"})
        b = self.add(name="Beta", run={"mode": "visible"})
        sch = self.scheduler()
        self.assertEqual(sch.tick(), 2)
        first, second = self.mux.spawns()
        self.assertIn("--new-window", first)
        self.assertNotIn("--new-window", second)
        wins = {p["window_id"] for p in self.mux.panes if p["pane_id"] >= 40}
        self.assertEqual(len(wins), 1, "one place for every visible bot run: the canvas window is never touched")
        self.assertEqual(sorted(p["tab_title"] for p in self.mux.panes if p["pane_id"] >= 40),
                         [H.BOT_TAB_PREFIX + "Alpha #1", H.BOT_TAB_PREFIX + "Beta #1"])
        self.assertEqual(self.mux.touched_panes(), [])
        for rec in (a, b):
            running = self.state(rec)["running"]
            self.assertEqual((running["run"], running["pid"]), (1, None))
            self.assertIn("pane", running["visible"])

    def test_the_pane_runs_omp_with_the_composed_prompt_the_bots_settings_and_the_run_environment(self):
        self.accounts = {"work": "work-profile"}
        rec = self.add(run={"mode": "visible", "account": "work", "thinking": "high", "model": "anthropic/claude-test",
                            "max_cost_usd": 0.5, "add_dirs": [self.tmp]})
        sch = self.scheduler()
        sch.tick()
        pane = self.mux.panes[-1]
        self.assertEqual(os.path.realpath(pane["cwd"]), os.path.realpath(self.work))
        self.assertEqual(pane["cmd"][:2], ["bash", "-ic"])
        line = pane["cmd"][2]
        self.assertTrue(line.endswith("; exec bash"), "the pane keeps its shell so the user can read what is left")
        # run the pane's command for real with the fake omp, in the environment a GUI pane would have
        env = dict(self.env, WEZTERM_PANE="41")
        r = subprocess.run(["bash", "-c", line[:-len("; exec bash")]], env=env, cwd=pane["cwd"], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        call, = self.calls()
        sdir = self.sdir(rec)
        prompt_file = os.path.join(sdir, "prompts", "run-1.md")
        self.assertEqual(call["argv"], [
            "--profile", "work-profile", "--max-cost", "0.5", "--approval-mode", "write", "--thinking", "high",
            "--model", "anthropic/claude-test", "--append-system-prompt", os.path.join(sdir, "identity-visible.md"),
            "--add-dir", sdir, "--add-dir", self.tmp, "@" + prompt_file])
        self.assertNotIn("--mode", call["argv"], "interactive: the pane is the UI")
        self.assertNotIn("-p", call["argv"])
        self.assertNotIn("--max-time", call["argv"], "the daemon counts the working time itself")
        self.assertEqual(os.path.realpath(call["cwd"]), os.path.realpath(self.work))
        env = call["env"]
        self.assertEqual((env["HIVEGRID_SCHEDULE"], env["HIVEGRID_RUN"]), (rec["id"], "1"))
        self.assertEqual(env["HIVEGRID_RUN_REPORT"], S.report_path(rec["id"], 1, self.env))
        self.assertEqual(env["WEZTERM_PANE"], "41", "the state extension needs the pane id")
        for key in H.SCRUB:
            self.assertNotIn(key, env, "a bot never inherits the profile of the pane the scheduler started in")
        prompt = slurp(prompt_file)
        self.assertIn("Summarize the news.", prompt)
        self.assertIn(S.report_path(rec["id"], 1, self.env), prompt)
        self.assertIn("level: none|info|important", prompt)
        self.assertEqual(slurp(os.path.join(sdir, "run-1.exit")).strip(), "0")

    def test_the_visible_identity_says_the_user_may_watch_and_answer_and_the_headless_one_does_not(self):
        rec = self.add(run={"mode": "visible"})
        sch = self.scheduler()
        sch.tick()
        sdir = self.sdir(rec)
        visible = slurp(os.path.join(sdir, "identity-visible.md"))
        self.assertIn("may be watching", visible)
        self.assertIn("finish with the short report file", visible)
        self.assertNotIn("Nobody can answer", visible)
        self.assertIn("Nobody can answer", slurp(os.path.join(sdir, "identity.md")))

    def test_permissions_map_to_the_attended_approval_modes(self):
        want = {"readonly": ["--tools", H.READONLY_TOOLS, "--approval-mode", "write"],
                "edit": ["--approval-mode", "write"], "full": ["--approval-mode", "yolo"]}
        self.cfg["allow_full"] = True
        for perms, expected in want.items():
            self.mux.panes = self.mux.panes[:1]
            rec = self.add(name=perms, run={"mode": "visible", "permissions": perms, "max_time_s": 600, "max_cost_usd": 1})
            sch = self.scheduler()
            sch.tick()
            line = self.mux.panes[-1]["cmd"][2]
            words = shlex.split(line.split("; rc=$?")[0])
            self.assertEqual(words[words.index("--max-cost") + 2:][:len(expected)], expected, perms)
            S.update_state(self.sdir(rec), lambda st: st.update(running=None))
            self.store.remove(rec["id"])

    def test_full_still_needs_allow_full_in_visible_mode(self):
        rec = self.add(run={"mode": "visible", "permissions": "full", "max_time_s": 600, "max_cost_usd": 1}, policy={"retries": 0})
        sch = self.scheduler()
        sch.tick()
        run, = self.runs(rec)
        self.assertEqual(run["status"], "failed")
        self.assertIn("allow_full", run["error"])
        self.assertEqual(self.mux.spawns(), [])

    def test_a_missing_cwd_fails_before_any_pane_opens(self):
        rec = self.add(run={"mode": "visible"}, policy={"retries": 0})
        os.rmdir(self.work)
        sch = self.scheduler()
        sch.tick()
        self.assertIn("does not exist", self.runs(rec)[0]["error"])
        self.assertEqual(self.mux.spawns(), [])


class Outcomes(VisibleCase):
    def start(self, **run):
        rec = self.add(run=dict({"mode": "visible"}, **run))
        sch = self.scheduler()
        sch.tick()
        return rec, sch, self.mux.panes[-1]["pane_id"]

    def test_ok_when_the_agent_finishes_its_turn_with_the_report(self):
        rec, sch, pane = self.start()
        self.agent_state(pane, "working")
        self.at(sch, 10)
        self.assertEqual(self.runs(rec), [], "still working")
        self.say("all done", cost=0.07)
        self.report(rec)
        self.agent_state(pane, "done")
        self.at(sch, 20)
        self.assertEqual(self.runs(rec), [], "a finished turn must stay finished for a few seconds")
        self.at(sch, 20 + H.VISIBLE_SETTLE_S)
        run, = self.runs(rec)
        self.assertEqual((run["status"], run["mode"], run["trigger"], run["level"]), ("ok", "visible", "cron", "important"))
        self.assertEqual(run["headline"], "Fixed the build")
        self.assertAlmostEqual(run["cost_usd"], 0.07)
        self.assertEqual(run["session_file"], self.session())
        self.assertEqual(run["session_id"], "vis0001")
        self.assertEqual(run["model"], "anthropic/claude-test")
        self.assertEqual(run["report_path"], S.report_path(rec["id"], 1, self.env))
        self.assertNotIn("takeover", run)
        st = self.state(rec)
        self.assertEqual((st["running"], st["session_file"], st["consec_failures"]), (None, self.session(), 0))
        self.assertEqual(self.mux.touched_panes(), [], "the pane stays open, untouched")
        self.assertEqual(len(self.mux.spawns()), 1)
        self.assertEqual(sch.live, {})

    def test_the_final_text_becomes_the_report_when_the_agent_wrote_none(self):
        rec, sch, pane = self.start()
        self.say("pong from the pane")
        self.agent_state(pane, "done")
        self.at(sch, 5)
        self.at(sch, 5 + H.VISIBLE_SETTLE_S)
        run, = self.runs(rec)
        self.assertEqual((run["status"], run["summary"]), ("ok", "pong from the pane"))
        self.assertTrue(os.path.isfile(S.report_path(rec["id"], 1, self.env)))

    def test_a_turn_that_starts_again_within_the_settle_time_is_not_the_end(self):
        rec, sch, pane = self.start()
        self.say("first part")
        self.agent_state(pane, "done")
        self.at(sch, 5)
        self.agent_state(pane, "working")
        self.at(sch, 7)
        self.agent_state(pane, "done")
        self.at(sch, 9)
        self.at(sch, 9 + H.VISIBLE_SETTLE_S - 1)
        self.assertEqual(self.runs(rec), [])
        self.at(sch, 9 + H.VISIBLE_SETTLE_S)
        self.assertEqual([r["status"] for r in self.runs(rec)], ["ok"])

    def test_needs_you_is_not_a_failure_the_run_waits_and_shows_attention(self):
        rec, sch, pane = self.start(max_time_s=60)
        self.agent_state(pane, "working")
        self.at(sch, 10)
        self.agent_state(pane, "attention", pending={"kind": "ask", "question": "Which branch?", "options": [], "recommended": 0})
        self.at(sch, 20)
        item, = [i for i in S.build_summary(self.clock.t, env=self.env, schedules=self.store.read())["items"]]
        self.assertEqual((item["state"], item["running"], item["waiting"], item["attention"]), ("attention", True, True, True))
        self.assertEqual(self.state(rec)["running"]["waiting"], True)
        for hours in (1, 3, 11):   # the user is away: far beyond max_time_s, still no timeout
            self.at(sch, hours * 3600)
        self.assertEqual(self.runs(rec), [])
        self.assertIn(rec["id"], sch.live)
        self.agent_state(pane, "working")
        self.at(sch, 11 * 3600 + 5)
        item, = S.build_summary(self.clock.t, env=self.env, schedules=self.store.read())["items"]
        self.assertEqual((item["state"], item["waiting"]), ("working", False))
        self.say("answered and done")
        self.agent_state(pane, "done")
        self.at(sch, 11 * 3600 + 10)
        self.at(sch, 11 * 3600 + 10 + H.VISIBLE_SETTLE_S)
        run, = self.runs(rec)
        self.assertEqual(run["status"], "ok")
        self.assertEqual(self.mux.touched_panes(), [])

    def test_a_waiting_visible_run_does_not_hold_a_slot(self):
        self.cfg["max_parallel"] = 1
        a = self.add(name="Alpha", run={"mode": "visible"})
        self.add(name="Beta", run={"mode": "visible"})
        sch = self.scheduler()
        self.assertEqual(sch.tick(), 1, "one slot, one run")
        pane = self.mux.panes[-1]["pane_id"]
        self.agent_state(pane, "attention", pending={"kind": "ask", "question": "?", "options": [], "recommended": 0})
        self.clock.t = BASE + 10
        self.assertEqual(sch.tick(), 1, "Alpha waits for the user: Beta may start")
        self.assertEqual(len(self.mux.spawns()), 2)
        self.assertIn(a["id"], sch.live)

    def test_the_watchdog_counts_only_the_time_spent_working(self):
        rec, sch, pane = self.start(max_time_s=60)
        self.agent_state(pane, "working")
        self.at(sch, 20)
        self.at(sch, 40)
        self.at(sch, 50)                       # 30 s of work
        self.agent_state(pane, "attention", pending={"kind": "approval", "tool": "bash", "summary": "ls"})
        for t in range(60, 4000, 29):          # an hour of waiting costs nothing (40 s of work so far)
            self.at(sch, t)
        self.assertEqual(self.runs(rec), [])
        self.agent_state(pane, "working")
        self.at(sch, 4000)
        self.at(sch, 4010)
        self.at(sch, 4015)                     # 55 s of work
        self.assertEqual(self.runs(rec), [])
        self.at(sch, 4025)                     # 65 s > max_time_s
        run, = self.runs(rec)
        self.assertEqual(run["status"], "timeout")
        self.assertIn("max_time_s", run["error"])
        self.assertIn("left open", run["error"])
        self.assertEqual(self.mux.touched_panes(), [], "nothing is sent to a pane or killed in it")
        self.assertEqual(self.state(rec)["consec_failures"], 1)
        self.assertIsNone(self.state(rec)["retry"], "the pane is the user's: no second pane from the retry policy")

    def test_a_suspended_machine_does_not_count_as_working_time(self):
        rec, sch, pane = self.start(max_time_s=100)
        self.agent_state(pane, "working")
        self.at(sch, 10)
        self.at(sch, 7200)   # the laptop slept for two hours between two ticks: one look counts 30 s at most
        self.at(sch, 7230)
        self.at(sch, 7260)   # 90 s
        self.assertEqual(self.runs(rec), [])
        self.at(sch, 7290)
        self.assertEqual([r["status"] for r in self.runs(rec)], ["timeout"])

    def test_the_wait_for_the_user_is_capped_at_twelve_hours(self):
        rec, sch, pane = self.start()
        self.agent_state(pane, "attention", pending={"kind": "ask", "question": "?", "options": [], "recommended": 0})
        self.at(sch, 10)
        for hours in (3, 6, 11, 12):
            self.at(sch, hours * 3600 - 20)
        self.assertEqual(self.runs(rec), [])
        self.at(sch, 12 * 3600 + 20)
        run, = self.runs(rec)
        self.assertEqual(run["status"], "timeout")
        self.assertIn("12 h", run["error"])
        self.assertIn("left open", run["error"])
        self.assertEqual(self.mux.touched_panes(), [])
        self.assertEqual(sch.live, {})

    def test_the_usage_limit_ends_the_run_as_limit_with_its_reset(self):
        rec, sch, pane = self.start()
        self.agent_state(pane, "working")
        self.at(sch, 10)
        self.say("", cost=0.0, stop="error", errorStatus=429, errorMessage=LIMIT_TEXT)
        self.agent_state(pane, "limit", limit={"hit_at": BASE, "reset_at": BASE + 7200})
        self.at(sch, 20)
        run, = self.runs(rec)
        self.assertEqual((run["status"], run["level"]), ("limit", "important"))
        self.assertEqual(run["reset_at"], BASE + 7200)
        self.assertIsNone(self.state(rec)["retry"])
        self.assertEqual(self.state(rec)["consec_failures"], 1)
        self.assertEqual([n[1] for n in self.notes], ["Hivegrid bot: Digest limit"])

    def test_a_failed_turn_with_a_model_error_is_failed(self):
        rec, sch, pane = self.start()
        self.say("", cost=0.01, stop="error", errorMessage="boom: the model gave up")
        self.agent_state(pane, "done")
        self.at(sch, 5)
        self.at(sch, 5 + H.VISIBLE_SETTLE_S)
        run, = self.runs(rec)
        self.assertEqual(run["status"], "failed")
        self.assertIn("boom", run["error"])

    def test_the_pane_vanishing_before_the_agent_finished_is_failed(self):
        rec, sch, pane = self.start()
        self.agent_state(pane, "working")
        self.at(sch, 10)
        self.mux.close(pane)
        self.at(sch, 10 + int(H.VISIBLE_LIST_EVERY_S))
        run, = self.runs(rec)
        self.assertEqual(run["status"], "failed")
        self.assertIn("pane was closed", run["error"])
        self.assertEqual(sch.live, {})

    def test_a_pane_closed_right_after_the_finished_turn_still_counts_as_ok(self):
        rec, sch, pane = self.start()
        self.say("fine")
        self.agent_state(pane, "done")
        self.at(sch, 5)
        self.mux.close(pane)
        self.at(sch, 5 + int(H.VISIBLE_LIST_EVERY_S))
        self.assertEqual([r["status"] for r in self.runs(rec)], ["ok"])

    def test_omp_exiting_non_zero_is_failed_with_its_status(self):
        rec, sch, pane = self.start()
        self.agent_state(pane, "working")
        self.at(sch, 10)
        with open(os.path.join(self.sdir(rec), "run-1.exit"), "w") as f:
            f.write("1\n")
        os.unlink(os.path.join(self.agents, f"{pane}.json"))   # omp removes its state file when it quits
        self.at(sch, 12)
        run, = self.runs(rec)
        self.assertEqual((run["status"], run["exit"]), ("failed", 1))
        self.assertIn("exited with status 1", run["error"])

    def test_omp_quitting_before_it_did_anything_is_cancelled_not_a_failure(self):
        rec, sch, pane = self.start()
        with open(os.path.join(self.sdir(rec), "run-1.exit"), "w") as f:
            f.write("0\n")
        self.at(sch, 12)
        run, = self.runs(rec)
        self.assertEqual(run["status"], "cancelled")
        self.assertTrue(run["seen"])
        self.assertEqual(self.state(rec)["consec_failures"], 0)

    def test_omp_quitting_after_finishing_is_ok(self):
        rec, sch, pane = self.start()
        self.say("finished, then ctrl-d")
        self.agent_state(pane, "working")
        self.at(sch, 5)
        with open(os.path.join(self.sdir(rec), "run-1.exit"), "w") as f:
            f.write("0\n")
        self.at(sch, 7)
        run, = self.runs(rec)
        self.assertEqual((run["status"], run["exit"]), ("ok", 0))

    def test_an_agent_that_never_starts_needs_the_user(self):
        rec, sch, pane = self.start()
        self.agent_state(pane, "idle")   # omp is up but waits (a login, a model to choose)
        self.at(sch, 10)
        item, = S.build_summary(self.clock.t, env=self.env, schedules=self.store.read())["items"]
        self.assertEqual(item["state"], "working")
        self.at(sch, H.VISIBLE_START_S + 5)
        item, = S.build_summary(self.clock.t, env=self.env, schedules=self.store.read())["items"]
        self.assertEqual((item["state"], item["waiting"]), ("attention", True))
        self.assertEqual(self.runs(rec), [])

    def test_a_pane_that_cannot_be_opened_falls_back_to_headless_and_says_why(self):
        self.mux.refuse_spawn = True
        rec = self.add(run={"mode": "visible"})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        run, = self.runs(rec)
        self.assertEqual((run["mode"], run["status"]), ("headless/visible", "ok"))
        self.assertIn("could not open a pane", run["mode_fallback"])

    def test_user_cancel_records_the_run_and_leaves_the_pane_alone(self):
        rec, sch, pane = self.start()
        self.agent_state(pane, "working")
        self.at(sch, 10)
        S.send_command(S.command_dir(self.env), {"op": "cancel", "id": rec["id"]})
        self.at(sch, 12)
        run, = self.runs(rec)
        self.assertEqual(run["status"], "cancelled")
        self.assertEqual(run["error"], S.CANCELLED_BY_USER)
        self.assertEqual(self.mux.touched_panes(), [])
        self.assertIsNone(self.state(rec)["running"])


class Restarts(VisibleCase):
    def test_a_stopped_scheduler_leaves_the_pane_and_the_next_one_observes_the_run_again(self):
        rec = self.add(run={"mode": "visible", "max_time_s": 60})
        sch = self.scheduler()
        sch.tick()
        pane = self.mux.panes[-1]["pane_id"]
        self.agent_state(pane, "working")
        self.at(sch, 20)
        sch.shutdown()
        self.assertEqual(self.runs(rec), [], "stopping the daemon does not end a run that lives in a pane")
        self.assertEqual(self.mux.touched_panes(), [])
        self.assertIsNotNone(self.state(rec)["running"])
        again = self.scheduler()
        self.assertIn(rec["id"], again.live)
        self.clock.t = BASE + 40
        self.assertEqual(again.tick(), 0)
        self.say("done after the restart")
        self.agent_state(pane, "done")
        self.at(again, 50)
        self.at(again, 50 + H.VISIBLE_SETTLE_S)
        run, = self.runs(rec)
        self.assertEqual((run["status"], run["mode"]), ("ok", "visible"))
        self.assertEqual(len(self.mux.spawns()), 1)

    def test_working_time_survives_a_restart(self):
        rec = self.add(run={"mode": "visible", "max_time_s": 60})
        sch = self.scheduler()
        sch.tick()
        pane = self.mux.panes[-1]["pane_id"]
        self.agent_state(pane, "working")
        self.at(sch, 20)
        self.at(sch, 45)   # 25 s of work
        sch._vis_save(sch.live[rec["id"]], self.clock.t)
        sch.shutdown()
        again = self.scheduler()
        self.at(again, 46)
        self.at(again, 70)   # 49 s: not yet
        self.assertEqual(self.runs(rec), [])
        self.at(again, 100)  # 79 s with the 25 s from before the restart (54 s without them)
        self.assertEqual([r["status"] for r in self.runs(rec)], ["timeout"])

    def test_a_pane_that_is_gone_after_the_restart_closes_the_run_as_failed(self):
        rec = self.add(run={"mode": "visible"})
        sch = self.scheduler()
        sch.tick()
        sch.shutdown()
        self.mux.panes = self.mux.panes[:1]
        again = self.scheduler()
        run, = self.runs(rec)
        self.assertEqual((run["status"], run["mode"]), ("failed", "visible"))
        self.assertIn("pane of this visible run is gone", run["error"])
        self.assertEqual(again.live, {})


class Hybrid(VisibleCase):
    def takeovers(self):
        return [p for p in self.mux.panes if p["pane_id"] >= 40]

    def test_a_failed_headless_run_opens_the_same_session_in_a_pane(self):
        self.env["FAKE_OMP_SEQ"] = "fail"
        rec = self.add(run={"mode": "hybrid", "permissions": "readonly"}, policy={"retries": 2})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        run, = self.runs(rec)
        self.assertEqual((run["status"], run["mode"]), ("failed", "hybrid"))
        pane, = self.takeovers()
        self.assertEqual(run["takeover"], {"pane": pane["pane_id"]})
        self.assertEqual(pane["tab_title"], H.BOT_TAB_PREFIX + "Digest #1 takeover")
        self.assertEqual(len(self.calls()), 1, "the headless step ran once; the takeover is not a retry")
        words = shlex.split(pane["cmd"][2].split("; rc=$?")[0])
        self.assertEqual(words[words.index("--resume") + 1], run["session_file"], "the same session continues")
        self.assertEqual(words[words.index("--approval-mode") + 1], "write")
        self.assertEqual(words[words.index("--tools") + 1], H.READONLY_TOOLS)
        self.assertNotIn("-p", words)
        first = slurp(words[-1][1:])
        self.assertTrue(words[-1].startswith("@"))
        self.assertEqual(first, "The unattended run stopped: boom: the model gave up. Please continue or tell me what you need.\n")
        self.assertIsNone(self.state(rec)["retry"], "the user has the session: no unattended retry on top of it")
        self.assertFalse(run["seen"])
        self.assertEqual(run["level"], "important")
        item, = S.build_summary(self.clock.t, env=self.env, schedules=self.store.read())["items"]
        self.assertEqual((item["state"], item["attention"]), ("attention", True))
        self.assertEqual(self.mux.touched_panes(), [])

    def test_a_timed_out_headless_run_opens_a_takeover(self):
        self.env["FAKE_OMP_SEQ"] = "hang"
        rec = self.add(run={"mode": "hybrid", "max_time_s": 60}, policy={"retries": 0})
        sch = self.scheduler()
        sch.tick()
        self.wait_live(sch, rec["id"])
        self.clock.t = BASE + 125
        self.settle(sch)
        run, = self.runs(rec)
        self.assertEqual(run["status"], "timeout")
        self.assertEqual(run["takeover"], {"pane": self.takeovers()[0]["pane_id"]})
        words = shlex.split(self.takeovers()[0]["cmd"][2].split("; rc=$?")[0])
        self.assertIn("The unattended run stopped: no exit", slurp(words[-1][1:]))

    def test_ok_limit_and_cost_never_open_a_pane(self):
        for seq in ("ok", "limit", "cost"):
            self.env["FAKE_OMP_SEQ"] = seq
            self.env["FAKE_OMP_DIR"] = os.path.join(self.tmp, "calls-" + seq)
            rec = self.add(name=seq, run={"mode": "hybrid"}, policy={"retries": 0})
            sch = self.scheduler()
            sch.tick()
            self.settle(sch)
            run, = self.runs(rec)
            self.assertEqual(run["status"], seq)
            self.assertEqual(run["mode"], "hybrid")
            self.assertNotIn("takeover", run, seq)
        self.assertEqual(self.mux.spawns(), [])

    def test_a_visible_run_never_opens_a_second_pane_on_failure(self):
        rec = self.add(run={"mode": "visible"}, policy={"retries": 2})
        sch = self.scheduler()
        sch.tick()
        pane = self.mux.panes[-1]["pane_id"]
        self.mux.close(pane)
        self.at(sch, 6)
        self.at(sch, 12)
        self.assertEqual([r["status"] for r in self.runs(rec)], ["failed"])
        self.assertEqual(len(self.mux.spawns()), 1)

    def test_when_no_pane_can_be_opened_the_failure_stands_and_the_reason_is_recorded(self):
        self.env["FAKE_OMP_SEQ"] = "fail"
        rec = self.add(run={"mode": "hybrid"}, policy={"retries": 1})
        sch = self.scheduler()
        sch.tick()
        self.mux.refuse_spawn = True
        self.settle(sch)
        run, = self.runs(rec)
        self.assertEqual(run["status"], "failed")
        self.assertNotIn("takeover", run)
        self.assertIn("no pane for the takeover", run["mode_fallback"])
        self.assertIsNotNone(self.state(rec)["retry"], "without a pane the retry policy applies as for any headless run")


class NoGui(VisibleCase):
    def test_without_a_hivegrid_window_visible_runs_headless_and_the_record_says_why(self):
        self.mux.up = False
        rec = self.add(run={"mode": "visible"})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        run, = self.runs(rec)
        self.assertEqual((run["mode"], run["status"]), ("headless/visible", "ok"))
        self.assertIn("no running Hivegrid window", run["mode_fallback"])
        self.assertEqual(self.mux.spawns(), [])
        argv = self.calls()[0]["argv"]
        self.assertEqual(argv[:3], ["--mode", "json", "-p"], "the headless runner, exactly as before")
        self.assertTrue(os.path.isfile(os.path.join(self.sdir(rec), "identity.md")))
        self.assertEqual(argv[argv.index("--append-system-prompt") + 1], os.path.join(self.sdir(rec), "identity.md"))

    def test_without_a_window_hybrid_runs_headless_and_a_failure_stays_a_failure(self):
        self.mux.up = False
        self.env["FAKE_OMP_SEQ"] = "fail"
        rec = self.add(run={"mode": "hybrid"}, policy={"retries": 0})
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        run, = self.runs(rec)
        self.assertEqual((run["mode"], run["status"]), ("headless/hybrid", "failed"))
        self.assertIn("no running Hivegrid window", run["mode_fallback"])
        self.assertNotIn("takeover", run)

    def test_a_headless_bot_never_asks_the_gui_and_has_no_fallback_note(self):
        rec = self.add()
        sch = self.scheduler()
        sch.tick()
        self.settle(sch)
        run, = self.runs(rec)
        self.assertEqual(run["mode"], "headless")
        self.assertNotIn("mode_fallback", run)
        self.assertEqual(self.mux.calls, [])

    def test_a_chat_turn_with_a_visible_bot_is_headless_and_not_a_fallback(self):
        rec = self.add(run={"mode": "visible"})
        S.update_state(self.sdir(rec), lambda st: st.update(chat_queue=[{"text": "hi", "ts": 1}], last_slot=SLOT))
        sch = self.scheduler()
        self.clock.t = BASE
        sch.tick()
        self.settle(sch)
        turn, = [r for r in self.runs(rec) if r["trigger"] == "chat"]
        self.assertEqual(turn["mode"], "headless")
        self.assertEqual(self.mux.spawns(), [])


class GuiPieces(unittest.TestCase):
    def test_the_bots_window_is_found_by_its_tab_titles_and_a_closed_one_is_opened_again(self):
        mux = FakeMux()
        gui = H.Gui({"XDG_RUNTIME_DIR": "/nonexistent"}, run=mux)
        a = gui.open_tab(["bash"], "/tmp", H.BOT_TAB_PREFIX + "A #1")
        b = gui.open_tab(["bash"], "/tmp", H.BOT_TAB_PREFIX + "B #1")
        win = {p["pane_id"]: p["window_id"] for p in mux.panes}
        self.assertEqual(win[a], win[b])
        mux.panes = mux.panes[:1]   # the user closed the Bots window
        c = gui.open_tab(["bash"], "/tmp", H.BOT_TAB_PREFIX + "C #1")
        self.assertNotIn("--window-id", [x for call in mux.spawns()[-1:] for x in call])
        self.assertEqual(mux.panes[-1]["pane_id"], c)

    def test_no_mux_is_a_clear_problem_and_an_open_tab_raises(self):
        mux = FakeMux()
        mux.up = False
        gui = H.Gui({}, run=mux)
        self.assertIn("no running Hivegrid window", gui.problem())
        with self.assertRaises(H.GuiError):
            gui.open_tab(["bash"], "/tmp", "x")

    def test_a_stale_state_file_of_a_reused_pane_id_is_forgotten(self):
        import tempfile
        with tempfile.TemporaryDirectory() as rt:
            gui = H.Gui({"XDG_RUNTIME_DIR": rt}, run=FakeMux())
            os.makedirs(os.path.join(rt, "hivegrid-agents"))
            path = gui.state_file(44)
            with open(path, "w") as f:
                json.dump({"state": "done", "pid": os.getpid()}, f)
            self.assertEqual(gui.agent(44)["state"], "done")
            gui.forget(44)
            self.assertIsNone(gui.agent(44))


class StreamEntries(unittest.TestCase):
    def test_session_entries_feed_the_same_bookkeeping_as_the_json_stream(self):
        s = H.Stream()
        msg = {"role": "assistant", "content": [{"type": "text", "text": "hi"}], "stopReason": "stop", "model": "m",
               "provider": "p", "usage": {"input": 1, "output": 2, "totalTokens": 3, "cost": {"total": 0.25}}}
        s.feed_entry({"type": "session", "id": "abc"})
        s.feed_entry({"type": "message", "message": {"role": "user", "content": []}})
        s.feed_entry({"type": "message", "message": msg})
        s.feed_entry({"type": "message", "message": dict(msg, usage={"cost": {"total": 0.5}})})
        s.feed_entry("junk")
        self.assertEqual((s.session_id, s.text, s.last_stop, s.model), ("abc", "hi", "stop", "p/m"))
        self.assertAlmostEqual(s.cost, 0.75)


if __name__ == "__main__":
    unittest.main()
