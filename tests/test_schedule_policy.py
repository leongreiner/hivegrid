"""bin/hivegrid_schedule.py, policy half: due slots, catch-up after downtime, overlap, retries, the failure
breaker, outcome bookkeeping and the prompt the agent receives. Pure functions on records and an explicit
`now`; the only disk use is a temp dir for the prompt paths."""
import calendar
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
sys.dont_write_bytecode = True

import hivegrid_schedule as S  # noqa: E402


def E(y, mo, d, h=0, mi=0, s=0):
    return calendar.timegm((y, mo, d, h, mi, s))


MON9 = E(2026, 11, 2, 9)  # a Monday
ENV = {"HOME": "/h", "XDG_STATE_HOME": "/s"}


def sched(when=None, policy=None, **over):
    rec = {"id": "digest-abc", "name": "Digest", "when": when or {"type": "cron", "expr": "0 9 * * *", "tz": "UTC"},
           "task": {"prompt": "Summarize the news."}, "run": {"cwd": "/tmp"}, "created": E(2026, 1, 1),
           "policy": policy or {}}
    rec.update(over)
    return S.validate(rec)


def state(**over):
    st = S.default_state()
    st.update(over)
    return st


def slot(day, h=9, mi=0):
    return E(2026, 11, day, h, mi)


class Due(unittest.TestCase):
    def test_nothing_before_the_first_slot(self):
        st = state(baseline=slot(2, 8))
        self.assertEqual(S.due(sched(), st, slot(2, 8, 59)), [])
        self.assertEqual(S.due(sched(), st, slot(2)), [slot(2)])

    def test_after_last_slot_only(self):
        st = state(last_slot=slot(2), last_slot_local="2026-11-02T09:00", baseline=slot(1))
        self.assertEqual(S.due(sched(), st, slot(4, 10)), [slot(3), slot(4)])
        self.assertEqual(S.due(sched(), st, slot(2, 12)), [])

    def test_baseline_and_creation_bound_history(self):
        st = state(baseline=slot(4, 12))
        self.assertEqual(S.due(sched(), st, slot(5, 10)), [slot(5)])
        young = sched(created=slot(4, 12))
        self.assertEqual(S.due(young, state(), slot(5, 10)), [slot(5)])

    def test_window_is_seven_days(self):
        st = state(baseline=slot(1) - 40 * 86400)
        self.assertEqual(len(S.due(sched(), st, slot(30, 10))), 7)

    def test_inactive_schedules_have_no_slots(self):
        late = slot(5, 10)
        self.assertEqual(S.due(sched(enabled=False), state(baseline=slot(1)), late), [])
        self.assertEqual(S.due(sched(), state(baseline=slot(1), done=True), late), [])
        self.assertEqual(S.due(sched(), state(baseline=slot(1), paused_reason="x"), late), [])

    def test_interval_slots(self):
        w = {"type": "interval", "every_s": 3600, "anchor": slot(2, 0)}
        st = state(baseline=slot(2, 0))
        self.assertEqual(S.due(sched(w), st, slot(2, 3, 30)), [slot(2, 1), slot(2, 2), slot(2, 3)])

    def test_once_is_due_once(self):
        w = {"type": "once", "at": "2026-11-02T09:00", "tz": "UTC"}
        st = state(baseline=slot(1))
        self.assertEqual(S.due(sched(w), st, slot(2, 9, 1)), [slot(2)])
        st = S.advance(sched(w), st, slot(2), "cron")
        self.assertEqual(S.due(sched(w), st, slot(3)), [])


class Catchup(unittest.TestCase):
    def plan(self, now, *, last=None, policy=None, when=None, **st):
        s = sched(when, policy)
        base = state(baseline=slot(1), **st)
        if last:
            base["last_slot"], base["last_slot_local"] = last, S._slot_local(s["when"], last)
        return S.plan_catchup(s, base, now)

    def test_on_time_fire_is_cron(self):
        self.assertEqual(self.plan(slot(2, 9, 0, ), last=slot(1)), ("fire", slot(2), "cron"))
        self.assertEqual(self.plan(slot(2) + 119, last=slot(1)), ("fire", slot(2), "cron"))

    def test_nothing_due(self):
        self.assertIsNone(self.plan(slot(2, 8), last=slot(1)))
        self.assertIsNone(self.plan(slot(2, 12), last=slot(2)))

    def test_downtime_within_grace_collapses_into_one_catchup(self):
        # daemon was down for two days; only the newest missed slot runs, once
        self.assertEqual(self.plan(slot(4, 10), last=slot(1)), ("fire", slot(4), "catchup"))
        self.assertEqual(self.plan(slot(4, 10), last=slot(1), policy={"grace_s": 3600}), ("fire", slot(4), "catchup"))

    def test_beyond_grace_is_recorded_as_missed(self):
        kind, slots = self.plan(slot(4, 12), last=slot(1))
        self.assertEqual(kind, "missed")
        self.assertEqual(slots, [slot(2), slot(3), slot(4)])
        self.assertEqual(self.plan(slot(4, 9, 30), last=slot(3), policy={"grace_s": 600}), ("missed", [slot(4)]))
        self.assertEqual(self.plan(slot(4, 9, 30), last=slot(3), policy={"grace_s": 1800}), ("fire", slot(4), "catchup"))

    def test_catchup_skip_never_runs_a_missed_slot(self):
        r = self.plan(slot(2, 9, 10), last=slot(1), policy={"catchup": "skip"})
        self.assertEqual(r, ("missed", [slot(2)]))
        self.assertEqual(self.plan(slot(2, 9, 1), last=slot(1), policy={"catchup": "skip"}), ("fire", slot(2), "cron"))

    def test_once_within_a_day_catches_up_later_is_missed_but_stays_enabled(self):
        w = {"type": "once", "at": "2026-11-02T09:00", "tz": "UTC"}
        self.assertEqual(self.plan(slot(2, 20), when=w), ("fire", slot(2), "catchup"))  # 11 h late, over grace_s
        self.assertEqual(self.plan(slot(3, 9), when=w), ("fire", slot(2), "catchup"))  # exactly 24 h
        self.assertEqual(self.plan(slot(3, 9, 1), when=w), ("missed", [slot(2)]))
        s = sched(w)
        st = S.advance(s, state(baseline=slot(1)), slot(2), "cron")  # recorded as missed
        self.assertTrue(S.is_active(s, st))
        self.assertTrue(s["enabled"])
        self.assertEqual(S.plan_catchup(s, dict(st, run_now=True), slot(9)), ("fire", slot(9), "manual"))

    def test_manual_run_wins_and_ignores_pause(self):
        s = sched()
        st = state(baseline=slot(1), run_now=True, paused_reason="5 failures")
        self.assertEqual(S.plan_catchup(s, st, slot(2, 5)), ("fire", slot(2, 5), "manual"))
        self.assertEqual(S.advance(s, st, slot(2, 5), "manual")["run_now"], False)

    def test_self_scheduled_run_has_trigger_self(self):
        s = sched()
        st = state(baseline=slot(1), next_override=slot(2, 15))
        self.assertIsNone(S.plan_catchup(s, st, slot(2, 14)))
        self.assertEqual(S.plan_catchup(s, st, slot(2, 15)), ("fire", slot(2, 15), "self"))
        self.assertEqual(S.plan_catchup(s, st, slot(2, 16)), ("fire", slot(2, 15), "self"))  # 1 h late, in grace

    def test_advance_bookkeeping(self):
        s = sched()
        st = S.advance(s, state(), slot(3), "cron")
        self.assertEqual((st["last_slot"], st["last_slot_local"]), (slot(3), "2026-11-03T09:00"))
        self.assertEqual(S.advance(s, st, slot(2), "cron")["last_slot"], slot(3))  # never goes back
        self.assertEqual(S.advance(s, st, slot(9), "retry")["last_slot"], slot(3))
        after = S.advance(s, state(next_override=slot(5)), slot(5), "self")
        self.assertIsNone(after["next_override"])
        self.assertEqual(state()["last_slot"], None)  # input untouched

    def test_after_catchup_the_next_regular_slot_is_not_double_fired(self):
        s = sched()
        st = state(baseline=slot(1))
        action = S.plan_catchup(s, st, slot(4, 10))
        st = S.advance(s, st, action[1], action[2])
        self.assertIsNone(S.plan_catchup(s, st, slot(4, 10, 5)))
        self.assertEqual(S.next_run(s, st, slot(4, 10)), slot(5))

    def test_skip_next(self):
        s = sched()
        st = S.skip_next(s, state(baseline=slot(1)), slot(2, 8))
        self.assertEqual(S.next_run(s, st, slot(2, 8)), slot(3))
        self.assertIsNone(S.plan_catchup(s, st, slot(2, 9, 1)))

    def test_unskip_brings_the_skipped_run_back(self):
        s = sched()
        before = state(baseline=slot(1))
        skipped = S.skip_next(s, before, slot(2, 8))
        self.assertEqual(S.skipped_slot(s, skipped, slot(2, 8)), slot(2))
        back = S.unskip(s, skipped, slot(2, 8))
        self.assertEqual(S.next_run(s, back, slot(2, 8)), slot(2))
        self.assertEqual((back["last_slot"], back["last_slot_local"]), (before["last_slot"], before["last_slot_local"]))
        self.assertNotIn("skips", back)
        self.assertEqual(S.plan_catchup(s, back, slot(2, 9, 1))[:2], ("fire", slot(2)))

    def test_skips_stack_and_unskip_takes_back_the_newest(self):
        s = sched()
        st = S.skip_next(s, state(baseline=slot(1)), slot(2, 8))
        st = S.skip_next(s, st, slot(2, 8))
        self.assertEqual(S.skipped_slot(s, st, slot(2, 8)), slot(3))
        st = S.unskip(s, st, slot(2, 8))
        self.assertEqual(S.skipped_slot(s, st, slot(2, 8)), slot(2))
        self.assertEqual(S.next_run(s, st, slot(2, 8)), slot(3))

    def test_nothing_to_undo_once_the_skipped_slot_is_past_or_something_ran_after(self):
        s = sched()
        st = S.skip_next(s, state(baseline=slot(1)), slot(2, 8))
        self.assertIsNone(S.skipped_slot(s, st, slot(2, 9, 1)))
        self.assertEqual(S.unskip(s, st, slot(2, 9, 1)), st)
        later = S.advance(s, st, slot(3), "cron")
        self.assertIsNone(S.skipped_slot(s, later, slot(2, 8)))
        self.assertEqual(S.unskip(s, later, slot(2, 8)), later)
        self.assertIsNone(S.skipped_slot(s, state(), slot(2, 8)))


class Overlap(unittest.TestCase):
    def test_table(self):
        running = {"run": 3, "pid": 9, "started": 1}
        cases = [
            ("skip", None, False, "run"), ("queue", None, False, "run"),
            ("skip", running, False, "skip"), ("queue", running, False, "queue"),
            ("queue", running, True, "skip"),
        ]
        for overlap, run, queued, want in cases:
            with self.subTest(overlap=overlap, running=bool(run), queued=queued):
                self.assertEqual(S.overlap_action(sched(policy={"overlap": overlap}), state(running=run, queued=queued)), want)

    def test_a_live_chat_turn_makes_a_due_slot_wait_whatever_the_policy(self):
        chat = {"run": 3, "pid": 9, "started": 1, "chat": True}
        for overlap in ("skip", "queue"):
            with self.subTest(overlap=overlap):
                s = sched(policy={"overlap": overlap})
                self.assertEqual(S.overlap_action(s, state(running=chat)), "queue")
                self.assertEqual(S.overlap_action(s, state(running=chat, queued=True)), "skip")


class Retry(unittest.TestCase):
    NOW = slot(2, 9, 5)

    def plan(self, status, attempt, policy=None, now=None, jitter=0, **rec):
        return S.retry_plan(sched(policy=policy), dict(rec, status=status), attempt, self.NOW if now is None else now, jitter)

    def test_backoff_table_clamps_to_the_last_entry(self):
        pol = {"retries": 4, "backoff_s": [300, 1800]}
        self.assertEqual(self.plan("failed", 1, pol), self.NOW + 300)
        self.assertEqual(self.plan("failed", 2, pol), self.NOW + 1800)
        self.assertEqual(self.plan("failed", 3, pol), self.NOW + 1800)
        self.assertEqual(self.plan("timeout", 4, pol), self.NOW + 1800)
        self.assertIsNone(self.plan("failed", 5, pol))

    def test_defaults_give_two_retries(self):
        self.assertEqual(self.plan("failed", 1), self.NOW + 300)
        self.assertEqual(self.plan("failed", 2), self.NOW + 1800)
        self.assertIsNone(self.plan("failed", 3))
        self.assertIsNone(self.plan("failed", 1, {"retries": 0}))

    def test_only_failed_limit_timeout_are_retried(self):
        for status in ("ok", "cost", "skipped", "missed", "cancelled", "deferred"):
            self.assertIsNone(self.plan(status, 1), status)
        for status in ("failed", "limit", "timeout"):
            self.assertIsNotNone(self.plan(status, 1), status)

    def test_limit_waits_for_the_reset_plus_jitter(self):
        reset = self.NOW + 5000
        self.assertEqual(self.plan("limit", 1, reset_at=reset, jitter=0), reset)
        self.assertEqual(self.plan("limit", 1, reset_at=reset, jitter=77), reset + 77)
        self.assertEqual(self.plan("limit", 1, reset_at=reset, jitter=lambda: 120), reset + 120)
        self.assertEqual(self.plan("limit", 2, reset_at=reset, jitter=5), reset + 5)
        self.assertEqual(self.plan("limit", 1, reset_at=self.NOW - 60, jitter=3), self.NOW)  # reset already past: now
        self.assertEqual(self.plan("limit", 1), self.NOW + 300)  # no reset known: backoff
        self.assertIsNone(self.plan("limit", 3, reset_at=reset))

    def test_default_jitter_stays_within_120s(self):
        reset = self.NOW + 5000
        for _ in range(50):
            got = S.retry_plan(sched(), {"status": "limit", "reset_at": reset}, 1, self.NOW)
            self.assertTrue(reset <= got <= reset + 120)

    def test_reset_at_only_matters_for_limit(self):
        self.assertEqual(self.plan("failed", 1, reset_at=self.NOW + 9000), self.NOW + 300)


class Breaker(unittest.TestCase):
    def test_table(self):
        for threshold, fails, want in ((5, 4, False), (5, 5, True), (5, 9, True), (0, 99, False), (1, 1, True), (3, 0, False)):
            with self.subTest(threshold=threshold, fails=fails):
                self.assertIs(S.breaker(state(consec_failures=fails), sched(policy={"pause_after_failures": threshold})), want)

    def test_outcomes_count_failures_and_pause(self):
        s = sched(policy={"pause_after_failures": 3})
        st = state()
        for status in ("failed", "limit", "timeout"):
            st = S.record_outcome(s, st, {"status": status})
        self.assertEqual(st["consec_failures"], 3)
        self.assertIn("3 runs", st["paused_reason"])
        self.assertFalse(S.is_active(s, st))
        self.assertEqual(S.due(s, st, slot(9)), [])
        st2 = S.record_outcome(s, state(consec_failures=2), {"status": "ok"})
        self.assertEqual((st2["consec_failures"], st2["paused_reason"]), (0, None))
        st3 = S.record_outcome(s, state(consec_failures=2), {"status": "skipped"})
        self.assertEqual(st3["consec_failures"], 2)
        st4 = S.record_outcome(s, state(consec_failures=2), {"status": "cost"})
        self.assertIsNotNone(st4["paused_reason"])


class Prompts(unittest.TestCase):
    NOW = slot(2, 9, 0)

    def agent(self, **over):
        return sched(kind="agent", name="Build watcher", task={"prompt": "Watch CI of the main repo and report red builds."},
                     identity=over.pop("identity", {"system": "You are terse."}), **over)

    def test_task_prompt_has_the_report_footer(self):
        s = sched()
        text = S.compose_prompt(s, state(), [], self.NOW, "cron", 7, "", env=ENV)
        self.assertTrue(text.startswith("Summarize the news."))
        self.assertIn("/s/hivegrid/schedules/digest-abc/reports/0007.md", text)
        self.assertNotIn("memory", text.lower())
        self.assertNotIn("run #", text)

    def test_agent_prompt_first_run(self):
        s = self.agent()
        text = S.compose_prompt(s, state(), [], self.NOW, "manual", 1, "", env=ENV)
        self.assertIn("Standing task:\nWatch CI of the main repo and report red builds.", text)
        self.assertIn("run #1 at 2026-11-02T09:00:00+00:00, trigger manual", text)
        self.assertIn("no previous run", text)
        self.assertIn("/s/hivegrid/schedules/digest-abc/memory.md", text)
        self.assertIn("first start", text)
        self.assertIn("at most 8192 bytes", text)
        self.assertIn("/s/hivegrid/schedules/digest-abc/reports/0001.md", text)

    def test_agent_prompt_later_run_mentions_the_previous_summary(self):
        s = self.agent(identity={"memory_max_bytes": 2048})
        runs = [{"run": 1, "status": "ok", "summary": "old one"},
                {"run": 2, "status": "skipped", "summary": "ignored: skipped runs say nothing"},
                {"run": 3, "status": "failed", "summary": "", "error": "exit 1: boom"}]
        text = S.compose_prompt(s, state(), runs, self.NOW, "retry", 4, "fact: repo is main\n", env=ENV)
        self.assertIn("run #4 at", text)
        self.assertIn("trigger retry", text)
        self.assertIn("Previous run (#3, failed): exit 1: boom", text)
        self.assertNotIn("old one", text)
        self.assertNotIn("ignored", text)
        self.assertIn("at most 2048 bytes", text)
        self.assertNotIn("first start", text)
        # a run record at or after this run number is not "previous"
        again = S.compose_prompt(s, state(), runs + [{"run": 4, "status": "ok", "summary": "same run"}], self.NOW, "cron", 4, "x", env=ENV)
        self.assertNotIn("same run", again)

    def test_iso_follows_the_schedule_zone(self):
        try:
            import zoneinfo
            zoneinfo.ZoneInfo("America/New_York")
        except Exception:  # noqa: BLE001
            self.skipTest("no tz database")
        s = self.agent(when={"type": "cron", "expr": "0 9 * * *", "tz": "America/New_York"})
        text = S.compose_prompt(s, state(), [], self.NOW, "cron", 1, "", env=ENV)
        self.assertIn("run #1 at 2026-11-02T04:00:00-05:00", text)

    def test_identity_text(self):
        t = S.identity_text(self.agent())
        self.assertTrue(t.startswith(S.PREAMBLE))
        self.assertIn("Your name is Build watcher.", t)
        self.assertTrue(t.rstrip().endswith("You are terse."))
        plain = S.identity_text(sched())
        self.assertEqual(plain.strip(), S.PREAMBLE)
        self.assertIn("never ask", S.PREAMBLE)

    def test_agent_always_has_memory(self):
        self.assertTrue(self.agent(identity={"memory": False})["identity"]["memory"])


if __name__ == "__main__":
    unittest.main()
