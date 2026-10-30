"""Prompts sent from the phone while omp is busy (bin/hivegrid_remote_pending.py): they are shown, greyed, in
the chat answer until the session file has the real user item. Temp directories and fakes only."""
import json
import os
import sys
import types
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import hivegrid_remote as R  # noqa: E402
import hivegrid_remote_pending as PQ  # noqa: E402
import test_remote as TR  # noqa: E402
from test_agents import NOW, pane  # noqa: E402
from test_remote_data import RichCase, jl, msg, text  # noqa: E402


def queued(*texts, ts=NOW, failed=False):
    return [{"text": t, "ts": ts, "failed": failed} for t in texts]


class PendingCase(RichCase):
    def setUp(self):
        super().setUp()
        self.tick = 15      # the write limit stays out of the way
        self.path = self.make_session(jl(msg("user", text("hello")), msg("assistant", text("hi"))))
        self.write_state(3, state="working", sessionFile=self.path)

    def say(self, body, pane_id=3, **kw):
        return self.call("POST", f"/api/agents/{pane_id}/prompt", {"text": body}, **kw)

    def chat(self, pane_id=3, query="", **kw):
        r, data = self.call("GET", f"/api/agents/{pane_id}/chat" + query, **kw)
        self.assertEqual(r.status, 200)
        return data

    def accept(self, *texts, path=None):
        """omp writes the prompts to the session file, as it does when it takes them off its queue."""
        with open(path or self.path, "a", encoding="utf-8") as f:
            f.write(jl(*[msg("user", text(t)) for t in texts]))


class TestRecording(PendingCase):
    def test_a_sent_prompt_shows_after_the_last_item_until_omp_has_it(self):
        self.assertEqual(self.chat()["pending_prompts"], [])
        self.assertEqual(self.say("do it")[0].status, 200)
        data = self.chat()
        self.assertEqual(data["pending_prompts"], queued("do it"))
        self.assertEqual([i["text"] for i in data["items"]], ["hello", "hi"], "the real items are untouched")

    def test_only_a_successful_prompt_is_recorded(self):
        self.fake.replies["send-text"] = None
        self.assertEqual(self.say("lost")[0].status, 502)
        self.fake.replies.pop("send-text")
        self.write_state(3, state="attention", pending={"kind": "approval", "tool": "bash", "summary": "rm"}, sessionFile=self.path)
        self.assertEqual(self.say("too early")[0].status, 409)
        self.assertEqual(self.say("a\x1bb")[0].status, 400)
        self.write_state(3, state="working", sessionFile=self.path)
        self.assertEqual(self.chat()["pending_prompts"], [])

    def test_a_read_only_page_records_nothing(self):
        app = self.make_app(readonly=True)
        self.assertEqual(self.say("x", app=app)[0].status, 403)
        self.assertEqual(self.chat(app=app)["pending_prompts"], [])
        self.assertEqual(self.chat()["pending_prompts"], [])

    def test_answers_typed_into_a_question_are_not_queued_prompts(self):
        self.write_state(3, state="attention", pending={"kind": "question", "title": "t", "options": []}, sessionFile=self.path)
        r, _ = self.call("POST", "/api/agents/3/keys", {"text": "yes", "keys": ["enter"]})
        self.assertEqual(r.status, 200)
        self.assertEqual(self.chat()["pending_prompts"], [])

    def test_the_text_is_data(self):
        hostile = "<img src=x onerror=alert(1)>\n\ttab"
        self.say(hostile)
        r, data = self.call("GET", "/api/agents/3/chat")
        self.assertEqual(data["pending_prompts"][0]["text"], hostile)
        self.assertEqual(r.content_type, "application/json")

    def test_another_agent_does_not_see_it(self):
        self.fake.panes = [pane(3, "π > ready"), pane(4, "π > ready")]
        other = self.make_session(jl(msg("user", text("elsewhere"))), name="t.jsonl")
        self.write_state(4, state="working", sessionFile=other)
        self.say("for three")
        self.assertEqual(self.chat(4)["pending_prompts"], [])
        self.accept("for three", path=other)
        self.assertEqual(self.chat(4)["pending_prompts"], [])
        self.assertEqual(self.chat(3)["pending_prompts"], queued("for three"), "an item of another chat consumes nothing")

    def test_without_a_session_file_it_shows_on_the_screen_answer_too(self):
        self.write_state(3, state="working")
        self.say("early")
        data = self.chat()
        self.assertEqual((data["source"], data["pending_prompts"]), ("screen", queued("early")))

    def test_the_older_pages_have_none(self):
        self.say("x")
        first = self.chat()
        self.assertEqual(self.chat(query=f"?before={first['items'][1]['n']}")["pending_prompts"], [])


class TestMatching(PendingCase):
    def test_the_real_item_replaces_it_in_the_same_answer(self):
        self.say("do it")
        self.accept("do it")
        data = self.chat()
        self.assertEqual(data["pending_prompts"], [])
        self.assertEqual([i["text"] for i in data["items"]], ["hello", "hi", "do it"])

    def test_text_is_compared_without_trailing_blanks_and_blank_edges(self):
        self.say("line one  \nline two\n\n")
        self.accept("\nline one\nline two")
        self.assertEqual(self.chat()["pending_prompts"], [])

    def test_a_different_text_does_not_consume(self):
        self.say("do it")
        self.accept("do it please", "something typed in the terminal")
        self.assertEqual(self.chat()["pending_prompts"], queued("do it"))

    def test_an_older_message_with_the_same_text_is_not_the_one(self):
        self.say("hello")        # the chat already has a "hello"
        self.assertEqual(self.chat()["pending_prompts"], queued("hello"))
        self.accept("hello")
        self.assertEqual(self.chat()["pending_prompts"], [])

    def test_identical_prompts_are_replaced_one_item_at_a_time(self):
        self.say("go")
        self.say("go")
        self.assertEqual(len(self.chat()["pending_prompts"]), 2)
        self.accept("go")
        self.assertEqual(len(self.chat()["pending_prompts"]), 1)
        self.assertEqual(len(self.chat()["pending_prompts"]), 1, "the same item is never used twice")
        self.accept("go")
        self.assertEqual(self.chat()["pending_prompts"], [])

    def test_prompts_are_replaced_in_the_order_omp_took_them(self):
        for t in ("a", "b", "c"):
            self.say(t)
        self.accept("b")
        self.assertEqual([p["text"] for p in self.chat()["pending_prompts"]], ["a", "c"])
        self.accept("a")
        self.assertEqual([p["text"] for p in self.chat()["pending_prompts"]], ["c"])

    def test_pending_prompts_keep_their_send_order(self):
        times = iter(range(100, 110))
        self.app.wall = lambda: float(next(times))
        for t in ("first", "second", "third"):
            self.say(t)
        self.app.wall = lambda: 200.0
        self.assertEqual([(p["text"], p["ts"]) for p in self.chat()["pending_prompts"]],
                         [("first", 100.0), ("second", 102.0), ("third", 104.0)])

    def test_a_prompt_accepted_while_nobody_looked(self):
        self.say("while away")
        self.accept("while away")
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(jl(msg("assistant", text("done")), *[msg("user", text(f"later {i}")) for i in range(5)]))
        self.assertEqual(self.chat()["pending_prompts"], [])

    def test_a_second_page_sees_the_same(self):
        self.say("x")
        self.assertEqual(self.chat()["pending_prompts"], self.chat()["pending_prompts"])
        self.accept("x")
        self.assertEqual((self.chat()["pending_prompts"], self.chat()["pending_prompts"]), ([], []))


class TestLifetime(PendingCase):
    def test_it_ages_out(self):
        self.say("stuck")
        self.app.wall = lambda: NOW + PQ.TTL - 1
        self.assertEqual(len(self.chat()["pending_prompts"]), 1)
        self.app.wall = lambda: NOW + PQ.TTL + 1
        self.assertEqual(self.chat()["pending_prompts"], [])

    def test_at_most_twenty_per_agent_the_newest_stay(self):
        for i in range(25):
            self.assertEqual(self.say(f"p{i}")[0].status, 200)
        self.assertEqual([p["text"] for p in self.chat()["pending_prompts"]], [f"p{i}" for i in range(5, 25)])

    def test_a_working_agent_keeps_it_an_idle_one_gives_it_up(self):
        self.say("queued")
        self.app.wall = lambda: NOW + PQ.QUIET + 1
        self.assertEqual(len(self.chat()["pending_prompts"]), 1, "still working: omp holds it")
        self.write_state(3, state="idle", sessionFile=self.path, since=NOW + 1)
        self.app.wall = lambda: NOW + PQ.QUIET - 1
        self.assertEqual(len(self.chat()["pending_prompts"]), 1, "idle for a moment only")
        self.app.wall = lambda: NOW + 1 + PQ.QUIET + 1
        self.assertEqual(self.chat()["pending_prompts"], [], "idle for good: it was a command or was taken back")

    def test_an_exited_agent_leaves_it_as_not_delivered(self):
        self.say("last words")
        self.write_state(3, state="working", pid=TR.DEAD_PID, sessionFile=self.path)
        self.assertEqual(self.chat()["pending_prompts"], queued("last words", failed=True))
        self.app.wall = lambda: NOW + PQ.FAILED_TTL + 1
        self.assertEqual(self.chat()["pending_prompts"], [])

    def test_another_process_in_the_pane_does_not_inherit_it(self):
        self.say("for the old one")
        self.write_state(3, state="working", pid=os.getppid(), sessionFile=self.path)
        self.assertEqual(self.chat()["pending_prompts"], [])

    def test_a_pane_that_is_gone_is_forgotten(self):
        self.say("x")
        self.fake.panes = [pane(4, "π > ready")]
        self.write_state(4, state="working")
        self.mux_agents_stale()
        self.chat(4)
        self.assertEqual(self.app.queued._panes, {})

    def mux_agents_stale(self):
        self.clock.t += 10     # past the agent list cache


class TestRevision(PendingCase):
    def test_a_held_chat_wakes_when_a_prompt_is_queued(self):
        first = self.chat()
        self.on_sleep = lambda n: self.say("wake up") if n == 2 else None
        woke = self.chat(query=f"?rev={first['rev']}&wait=20")
        self.assertEqual(woke["pending_prompts"], queued("wake up"))
        self.assertNotEqual(woke["rev"], first["rev"])
        self.assertLessEqual(len(self.slept), 3)

    def test_a_held_chat_wakes_when_it_is_replaced_and_when_it_ages_out(self):
        self.say("x")
        first = self.chat()
        self.on_sleep = lambda n: self.accept("x") if n == 1 else None
        woke = self.chat(query=f"?rev={first['rev']}&wait=20")
        self.assertEqual((woke["pending_prompts"], woke["items"][-1]["text"]), ([], "x"))
        self.on_sleep = None
        self.say("y")
        held = self.chat()
        self.app.wall = lambda: NOW + PQ.TTL + 5
        gone = self.chat(query=f"?rev={held['rev']}&wait=2")
        self.assertEqual(gone["pending_prompts"], [])
        self.assertNotEqual(gone["rev"], held["rev"])

    def test_nothing_changing_keeps_the_revision(self):
        self.say("x")
        first = self.chat()
        again = self.chat(query=f"?rev={first['rev']}")
        self.assertEqual(again, {"unchanged": True, "rev": first["rev"]})


def agent(pane_id=3, pid=10, state="working", since=0.0):
    return types.SimpleNamespace(pane=pane_id, pid=pid, state=state, since=since)


class TestQueued(unittest.TestCase):
    """The matching rules on their own, with the session file's user items given by a function."""

    def setUp(self):
        self.q = PQ.Queued(R.sanitize_text, 8000)
        self.items = []

    def look(self, a=None, path="/s/a.jsonl", now=1000.0):
        def items(p, min_n, min_ts):
            return [i for i in self.items if (min_n is None or i["n"] >= min_n) and (min_ts is None or i["ts"] is None or i["ts"] >= min_ts)]
        return [p["text"] for p in self.q.view(a or agent(), path, items, now)]

    def item(self, n, body, ts=None):
        self.items.append({"n": n, "ts": ts, "text": body})

    def test_marks_decide_when_the_file_is_known(self):
        self.item(16, "x", ts=1.0)       # before the send (the file ended at 32), whatever its time says
        self.q.add(agent(), "x", 1000.0, "/s/a.jsonl", 32, 1000.0)
        self.assertEqual(self.look(), ["x"])
        self.item(48, "x", ts=1.0)
        self.assertEqual(self.look(), [])

    def test_time_decides_when_the_file_was_not_known(self):
        self.q.add(agent(), "x", 1000.0, None, None, 1000.0)
        self.item(16, "x", ts=990.0)
        self.assertEqual(self.look(), ["x"])
        self.item(32, "x", ts=999.5)      # a little clock slack
        self.assertEqual(self.look(), [])

    def test_a_new_session_file_is_matched_by_time(self):
        self.q.add(agent(), "x", 1000.0, "/s/a.jsonl", 32, 1000.0)
        self.item(0, "x", ts=1001.0)      # the chat moved to a fresh file: its numbers start over
        self.assertEqual(self.look(path="/s/b.jsonl"), [])

    def test_no_file_means_nothing_can_match(self):
        self.q.add(agent(), "x", 1000.0, "/s/a.jsonl", 0, 1000.0)
        self.assertEqual(self.look(path=None), ["x"])

    def test_text_is_sanitized_and_blank_prompts_are_not_kept(self):
        self.q.add(agent(), "a\x1b[31mb\u202ec", 1000.0, None, None, 1000.0)
        self.q.add(agent(), "  \n ", 1000.0, None, None, 1000.0)
        self.assertEqual(self.look(), ["abc"])

    def test_bounds(self):
        for i in range(PQ.PER_AGENT + 5):
            self.q.add(agent(), f"m{i}", 1000.0, None, None, 1000.0)
        self.assertEqual(self.look(), [f"m{i}" for i in range(5, PQ.PER_AGENT + 5)])
        for pane_id in range(100, 100 + PQ.PANES_MAX + 3):
            self.q.add(agent(pane_id), "x", 1000.0, None, None, 1000.0 + pane_id)
        self.assertEqual(len(self.q._panes), PQ.PANES_MAX)
        self.assertNotIn(3, self.q._panes, "the pane touched longest ago went first")

    def test_the_agent_list_is_what_keeps_a_pane(self):
        self.q.add(agent(3), "x", 1000.0, None, None, 1000.0)
        self.q.add(agent(4), "y", 1000.0, None, None, 1000.0)
        self.q.retain({4})
        self.assertEqual((self.look(agent(3)), self.look(agent(4))), ([], ["y"]))

    def test_the_old_ones_go_when_a_new_one_comes(self):
        self.q.add(agent(), "old", 1000.0, None, None, 1000.0)
        self.q.add(agent(), "new", 1000.0 + PQ.TTL + 1, None, None, 1000.0 + PQ.TTL + 1)
        self.assertEqual(self.look(now=1000.0 + PQ.TTL + 1), ["new"])


if __name__ == "__main__":
    unittest.main()
