"""The terminal Bots page (hivegrid_panels.BotsPage): the Briefing and Talk views over a temp HOME, with the
shared chat parser reading a fixture session file that grows. The scheduler is never started."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_panels_bots as TB  # noqa: E402

H, P = TB.H, TB.P


def entry(i, parent, msg):
    return json.dumps({"type": "message", "id": f"e{i}", "parentId": parent, "timestamp": "2026-11-02T06:00:00Z", "message": msg})


class TalkCase(TB.BotsCase):
    def setUp(self):
        super().setUp()
        self.rec = self.add("ci watch", "agent", "every 30m")
        self.sdir = H.state_dir(self.rec["id"])
        self.sess = os.path.join(self.tmp, "s.jsonl")
        self.n = 0
        self.lines = []

    def append(self, msg):
        self.n += 1
        self.lines.append(entry(self.n, f"e{self.n - 1}" if self.n > 1 else None, msg))
        with open(self.sess, "w") as f:
            f.write("\n".join(self.lines) + "\n")
        H.update_state(self.sdir, lambda s: s.update(session_file=self.sess))

    def text(self, role, t):
        self.append({"role": role, "content": [{"type": "text", "text": t}]})

    def tool(self, name, **args):
        self.append({"role": "assistant", "content": [{"type": "toolCall", "id": f"c{self.n}", "name": name, "arguments": args}]})

    def talk(self):
        self.show()
        self.press("t")
        return self.screen()


class BriefingTest(TalkCase):
    def test_only_important_reports_newest_first_and_a_marks_all_seen(self):
        self.run_rec(self.rec, 1, "ok", 7200, False, level="info", headline="routine")
        self.run_rec(self.rec, 2, "ok", 3600, False, level="important", headline="CI of repo x fixed: flaky timeout")
        self.run_rec(self.rec, 3, "ok", 600, False, level="none", headline="CI green")
        self.run_rec(self.rec, 4, "failed", 60, False, error="boom")
        self.show()
        self.press("b")
        text = self.screen()
        self.assertIn("2 new", text)
        self.assertIn("CI of repo x fixed: flaky timeout", text)
        self.assertNotIn("routine", text)
        self.assertNotIn("CI green", text, "a quiet run never reaches the Briefing")
        lines = text.splitlines()
        self.assertLess(next(i for i, l in enumerate(lines) if "failed" in l and "ci watch" in l),
                        next(i for i, l in enumerate(lines) if "CI of repo x" in l), "newest first")
        self.press("a")
        self.assertIn("marked 2 reports seen", self.ui.message)
        self.assertEqual(H.briefing_counts()["unseen"], 0)
        self.assertIn("nothing new", self.screen())

    def test_enter_reads_the_report_and_marks_it_seen(self):
        self.run_rec(self.rec, 1, "ok", 600, False, level="important", headline="Fixed it")
        path = H.report_path(self.rec["id"], 1)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        TB.put(path, "level: important\nheadline: Fixed it\n\nThe timeout is now 30s.\n")
        self.show()
        self.press("b", "Enter")
        self.assertIn("The timeout is now 30s.", self.screen())
        self.assertEqual(H.briefing_counts()["unseen"], 0)
        self.press("Escape")
        self.assertEqual(self.page.mode, "list")

    def test_empty_briefing_says_so(self):
        self.show()
        self.press("b")
        self.assertIn("Nothing important.", self.screen())
        self.assertIn("Quiet runs stay", self.screen())


class TalkTest(TalkCase):
    def test_the_transcript_comes_from_the_session_file_and_follows_it(self):
        self.text("user", "check CI")
        self.text("assistant", "Looking at it.")
        text = self.talk()
        self.assertIn("› check CI", text)
        self.assertIn("Looking at it.", text)
        self.tool("bash", command="gh run list --repo example/x")
        self.assertIn("▸ bash gh run list --repo example/x", self.screen(), "a tool call is one line, and it appears as the file grows")
        self.assertNotIn("TypeError", text)

    def test_the_view_scrolls_to_the_end_and_back(self):
        for i in range(60):
            self.text("assistant" if i % 2 else "user", f"message number {i}")
        text = self.talk()
        self.assertIn("message number 59", text)
        self.assertNotIn("message number 0\n", text)
        self.press("PageUp")
        self.assertIn("↓", self.screen())
        self.press("PageDown", "PageDown", "PageDown", "PageDown", "PageDown", "PageDown")
        self.assertIn("message number 59", self.screen())

    def test_refresh_is_about_every_second_while_the_bot_works_and_on_change_otherwise(self):
        self.text("user", "hi")
        self.talk()
        self.page._at = self.now
        self.assertFalse(self.page.tick(self.ui), "idle and unchanged: nothing to redraw")
        self.text("assistant", "more")
        self.assertTrue(self.page.tick(self.ui), "idle but the file changed")
        self.screen()
        H.update_state(self.sdir, lambda s: s.update(running={"run": 1, "pid": 1, "started": self.now - 5}))
        self.page.refresh()
        self.page._at = self.now
        self.page._talk_at = self.now
        self.assertFalse(self.page.tick(self.ui))
        self.now += 1.1
        self.page._at = self.now
        self.assertTrue(self.page.tick(self.ui))

    def test_enter_sends_through_say_and_reports_the_queue(self):
        self.text("user", "hi")
        self.talk()
        self.type("also repo y")
        self.assertIn("also repo y", self.screen())
        with mock.patch.object(H, "say", return_value={"queued": True, "position": 2}) as say:
            self.press("Enter")
        say.assert_called_once()
        self.assertEqual(say.call_args[0][:2], (self.rec["id"], "also repo y"))
        self.assertIn("queued #2", self.screen())
        self.assertEqual(self.page.draft, "")

    def test_errors_keep_the_text_and_say_the_exact_start_command(self):
        self.alive(False)
        self.talk()
        self.type("hello")
        self.press("Enter")
        screen = self.screen()
        self.assertIn("scheduler not running: hivegrid-scheduler", screen)
        self.assertEqual(self.page.draft, "hello", "nothing is lost")
        err = H.ScheduleError("queue_full: 5 messages", "queue_full")
        with mock.patch.object(H, "say", side_effect=err):
            self.press("Enter")
        self.assertIn("queue_full", self.screen())

    def test_a_real_message_reaches_the_inbox_when_the_scheduler_runs(self):
        self.alive()
        self.talk()
        self.type("ping")
        self.press("Enter")
        self.assertIn({"op": "chat", "id": self.rec["id"], "text": "ping"}, self.commands())

    def test_keys_are_typed_not_commands_and_escape_goes_back(self):
        self.talk()
        self.type("rdsn")
        self.assertEqual(self.page.draft, "rdsn")
        self.assertEqual(self.commands(), [])
        self.press("Escape")
        self.assertEqual(self.page.mode, "list")
        self.assertIsNotNone(self.ui.running)

    def test_a_failed_chat_turn_shows_its_error(self):
        self.text("user", "hi")
        H.update_state(self.sdir, lambda s: s.update(chat={"active": False, "started": None, "error": "omp exited 1"}))
        self.assertIn("last turn failed: omp exited 1", self.talk())

    def test_control_characters_of_the_transcript_never_reach_the_screen(self):
        self.text("assistant", "bad \x1b]52;c;AAAA\x07 text")
        self.talk()
        for row in self.ui.cv.cells:
            for ch, *_ in row:
                self.assertNotIn(ch, ("\x1b", "\x07"))

    def test_layout_at_three_sizes(self):
        self.text("user", "check CI")
        self.tool("bash", command="gh run view")
        self.text("assistant", "done " * 40)
        for size in ((80, 24), (130, 32), (300, 80)):
            self.ui.size = size
            screen = self.talk()
            self.assertIn("check CI", screen, size)
            self.assertIn("Talk to the bot", screen, size)
            self.assertEqual(len(screen.splitlines()), size[1], size)
            self.press("Escape")
