"""The agent tools (bin/hivegrid_remote_mcp.py): against the real server on 127.0.0.1:0 with a fake mux behind it (what a
tool sends is what the server accepts) and scripted answers for the flows that need a live omp. No window, no network
beyond loopback, no real home."""
import json
import os
import socket
import sys
import unittest
import unittest.mock

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import hivegrid_remote_mcp as M  # noqa: E402
import test_remote_http as TH  # noqa: E402
from test_agents import NOW, pane  # noqa: E402
from test_remote import TOKEN, Clock  # noqa: E402


class Driver:
    """Runs tools at once against a remote; `self.out` collects nothing but keeps the old shape of the helpers."""

    def start(self, remote, clock):
        self.remote, self.out = remote, []

    def tool(self, name, **args):
        """(the tool's answer: the decoded document, or the text of an error; whether it is an error)"""
        text, failed = M.run_tool(name, args, M.Call(self.remote, self.clock, lambda: NOW))
        return (text if failed else json.loads(text)), failed

    def ok(self, name, **args):
        answer, failed = self.tool(name, **args)
        self.assertFalse(failed, answer)
        return answer

    def refused(self, name, **args):
        answer, failed = self.tool(name, **args)
        self.assertTrue(failed, answer)
        return answer


class LiveCase(TH.HttpCase, Driver):
    """Two agents: panes 3 and 7, which the dock numbers 1 and 2."""

    def setUp(self):
        super().setUp()
        self.fake.panes = [pane(3, "π > ready"), pane(7, "π > ready")]
        self.write_state(7, state="idle", title="\U000f0b84 billing")
        self.start(M.Remote("box", "127.0.0.1", self.port, lambda: TOKEN), self.clock)

    def tool(self, name, **args):
        self.clock.t += 5          # the server's limits stay out of the way, as `tick` does for the other tests
        return super().tool(name, **args)

    def keys(self):
        return [c[-1] for c in self.sends()]


class TestAgentsByDockNumber(LiveCase):
    def test_the_list_shows_dock_numbers_and_what_an_agent_needs(self):
        self.write_state(3, state="attention", pending={"kind": "ask", "question": "Which?", "options": ["a", "b"], "recommended": 1})
        got = self.ok("list_agents")
        self.assertEqual([(a["agent"], a["state"]) for a in got["agents"]], [("1", "needs you"), ("2", "idle")])
        self.assertEqual(got["agents"][0]["needs"], {"question": "Which?", "options": ["1: a", "2: b"], "recommended": 2})
        self.assertEqual((got["host"], got["agents"][1]["title"]), ("box", "billing"))
        self.assertNotIn("needs", got["agents"][1])

    def test_a_prompt_goes_to_the_pane_behind_the_number(self):
        self.assertEqual(self.ok("send_prompt", agent=2, text="run the tests"), {"sent": True, "agent": "2"})
        self.assertEqual(self.sends(), [("send-text", "--pane-id", "7", "--", "run the tests"),
                                        ("send-text", "--pane-id", "7", "--no-paste", "\r")])

    def test_a_pane_id_is_not_an_agent(self):
        for ref in ("7", "3", 9, "billing"):
            text = self.refused("send_prompt", agent=ref, text="x")
            self.assertIn(f"no agent {ref}", text)
            self.assertIn("2 (billing)", text, "the answer names the agents there are")
        self.assertEqual(self.sends(), [])

    def test_what_the_server_refuses_is_a_tool_error_with_its_reason(self):
        self.write_state(3, state="attention", pending={"kind": "approval", "tool": "bash", "summary": "rm -r x"})
        text = self.refused("send_prompt", agent="1", text="x")
        self.assertTrue(text.startswith("409 agent_not_ready: "), text)
        self.assertEqual(self.sends(), [])

    def test_without_a_chat_the_screen_is_read(self):
        self.fake.replies["get-text"] = "one\ntwo\n"
        got = self.ok("read_agent", agent="1")
        self.assertEqual((got["screen"], got["agent"]), (["one", "two"], "1"))
        self.assertNotIn("messages", got)


class TestAnswers(LiveCase):
    def ask(self, recommended=1):
        self.write_state(3, state="attention", pending={"kind": "ask", "question": "Which?", "options": ["a", "b", "c"],
                                                         "recommended": recommended})

    def test_an_option_is_reached_from_the_recommended_one(self):
        for option, keys in ((3, ["\x1b[B", "\r"]), (1, ["\x1b[A", "\r"]), (2, ["\r"])):
            self.ask()
            del self.fake.calls[:]
            self.ok("answer_agent", agent="1", option=option)
            self.assertEqual(self.keys(), keys, option)

    def test_an_option_that_is_not_there_presses_nothing(self):
        self.ask()
        for option in (0, 4):
            self.assertIn("no option", self.refused("answer_agent", agent="1", option=option))
        self.assertEqual(self.sends(), [])

    def test_an_answer_of_the_wrong_kind_presses_nothing(self):
        self.ask()
        self.assertIn("does not wait for an approval", self.refused("answer_agent", agent="1", approve=True))
        self.assertIn("exactly one", self.refused("answer_agent", agent="1", approve=True, option=1))
        self.assertIn("exactly one", self.refused("answer_agent", agent="1"))
        self.assertIn("not waiting", self.refused("answer_agent", agent="2", option=1))
        self.assertEqual(self.sends(), [])

    def test_an_approval_is_y_or_n(self):
        for approve, key in ((True, "y"), (False, "n")):
            self.write_state(3, state="attention", pending={"kind": "approval", "tool": "bash", "summary": "ls"})
            del self.fake.calls[:]
            self.ok("answer_agent", agent="1", approve=approve)
            self.assertEqual(self.keys(), [key])

    def test_a_text_answer_is_typed_and_entered(self):
        self.ask()
        self.ok("answer_agent", agent="1", text="neither")
        self.assertEqual(self.keys(), ["neither", "\r"])

    def test_interrupt_is_escape(self):
        self.write_state(3, state="working")
        self.ok("interrupt_agent", agent="1")
        self.assertEqual(self.keys(), ["\x1b"])


class TestWaiting(LiveCase):
    def test_it_returns_when_a_working_agent_stops(self):
        self.write_state(3, state="working")
        self.on_sleep = lambda n: self.write_state(3, state="done") if n == 3 else None
        got = self.ok("wait_for_agents", timeout=300)
        self.assertEqual([(a["agent"], a["state"]) for a in got["stopped"]], [("1", "done")])
        self.assertEqual(got["still_working"], [])
        self.assertNotIn("timed_out", got)
        self.assertLess(sum(self.slept), 10, "it did not wait for the timeout")

    def test_the_timeout_says_who_still_works(self):
        self.write_state(3, state="working")
        got = self.ok("wait_for_agents", agents=["1"], timeout=45)
        self.assertEqual((got["stopped"], got["still_working"], got["timed_out"]), ([], ["1"], True))
        self.assertGreaterEqual(sum(self.slept), 45)
        self.assertLess(sum(self.slept), 50)

    def test_all_waits_for_the_last_one(self):
        self.write_state(3, state="working")
        self.write_state(7, state="working")

        def world(n):
            if n == 2:
                self.write_state(3, state="done")
            if n == 6:
                self.write_state(7, state="attention", pending={"kind": "approval", "tool": "bash", "summary": "ls"})
        self.on_sleep = world
        got = self.ok("wait_for_agents", all=True, timeout=300)
        self.assertEqual(sorted((a["agent"], a["state"]) for a in got["stopped"]), [("1", "done"), ("2", "needs you")])
        self.assertGreaterEqual(len(self.slept), 6)

    def test_an_agent_that_is_gone_has_stopped(self):
        self.write_state(7, state="working")
        self.on_sleep = lambda n: setattr(self.fake, "panes", [pane(3, "π > ready")]) if n == 2 else None
        got = self.ok("wait_for_agents", agents=["2"], timeout=300)
        self.assertEqual(got["stopped"], [{"agent": "2", "state": "gone"}])

    def test_nothing_working_returns_at_once(self):
        self.assertEqual(self.ok("wait_for_agents")["stopped"], [])
        self.assertEqual(self.slept, [])


class TestToken(LiveCase):
    def test_no_answer_carries_the_token(self):
        self.ok("list_agents")
        self.ok("host_status")
        self.refused("send_prompt", agent="9", text="x")
        self.start(M.Remote("box", "127.0.0.1", self.port, lambda: "w" * 43), self.clock)
        self.assertTrue(self.refused("list_agents").startswith("401 unauthorized"))
        self.assertNotIn("w" * 43, "".join(self.out))

    def test_a_server_that_is_down_is_said_in_words(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            closed = s.getsockname()[1]
        self.start(M.Remote("box", "127.0.0.1", closed, lambda: TOKEN), self.clock)
        self.assertIn("hivegrid-remote on box does not answer", self.refused("list_agents"))

def agent(pane_id, label, state="idle", **kw):
    return {"id": pane_id, "label": label, "state": state, "state_label": {"attention": "needs you"}.get(state, state),
            "title": kw.pop("title", "T"), "cwd": "~/p", "pending": None, "limit": None, "switch": None, "model": None, **kw}


class Script:
    """Stands in for Remote: fixed answers per (method, path without query); the agent list is a sequence of lists,
    one per look (the last one stays), and every look takes a second on the clock."""

    name = "box"

    def __init__(self, clock, lists, routes=None):
        self.clock, self.lists, self.routes, self.calls = clock, list(lists), routes or {}, []

    def call(self, method, path, body=None, etag=None, timeout=None):
        route = path.split("?")[0]
        self.calls.append((method, route, body))
        if (method, route) == ("GET", "/api/agents"):
            self.clock.t += 1
            agents = self.lists.pop(0) if len(self.lists) > 1 else self.lists[0]
            return 200, {"now": NOW, "agents": agents, "totals": {"by_state": {}, "usage": None}}, '"r"'
        answer = self.routes.get((method, route))
        if answer is None:
            raise M.ApiError(404, "not_found", "not found")
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, Exception):
            raise answer
        return 200, answer, None

    def posts(self):
        return [(route, body) for method, route, body in self.calls if method == "POST"]


class ScriptCase(unittest.TestCase, Driver):
    def script(self, lists, routes=None):
        self.clock = Clock()
        self.remote = Script(self.clock, lists, routes)
        self.start(self.remote, self.clock)
        return self.remote


OPTIONS = {"profiles": [{"label": "Work", "profile": "work"}, {"label": "Home", "profile": "home"}], "dirs": [{"path": "~/Projects/x", "label": "x"}]}
UP = {"id": "anthropic/m", "name": "M", "provider": "anthropic"}


class TestStarting(ScriptCase):
    def test_the_first_prompt_waits_until_omp_is_up(self):
        r = self.script([[], [agent(9, "1")], [agent(9, "1")], [agent(9, "1", model=UP)]],
                        {("GET", "/api/sessions/options"): OPTIONS, ("POST", "/api/sessions"): {"ok": True, "id": 9},
                         ("POST", "/api/agents/9/prompt"): {"ok": True}})
        got = self.ok("start_agent", dir="~/Projects/x", account="work", prompt="fix the build")
        self.assertEqual((got["agent"]["agent"], got["prompt"]), ("1", "sent"))
        self.assertEqual(r.posts(), [("/api/sessions", {"profile": "work", "dir": "~/Projects/x"}),
                                     ("/api/agents/9/prompt", {"text": "fix the build"})])
        looks = [i for i, c in enumerate(r.calls) if c[1] == "/api/agents"]
        self.assertGreater(r.calls.index(("POST", "/api/agents/9/prompt", {"text": "fix the build"})), looks[3],
                           "not typed into a pane whose omp has not started")

    def test_a_prompt_is_not_typed_into_an_agent_that_never_gets_ready(self):
        r = self.script([[agent(9, "1")]], {("POST", "/api/sessions"): {"ok": True, "id": 9}})
        got = self.ok("start_agent", dir="~/Projects/x", prompt="fix the build")
        self.assertTrue(got["prompt"].startswith("not sent"), got)
        self.assertEqual(r.posts(), [("/api/sessions", {"profile": None, "dir": "~/Projects/x"})])
        self.assertGreaterEqual(self.clock.t - 1000, M.START_WAIT)

    def test_without_a_prompt_it_returns_once_the_agent_is_listed(self):
        self.script([[], [agent(9, "4", "working")]], {("POST", "/api/sessions"): {"ok": True, "id": 9}})
        self.assertEqual(self.ok("start_agent", dir="~")["agent"]["agent"], "4")
        self.assertLess(self.clock.t - 1000, 5)

    def test_an_account_is_named_by_label_or_profile_and_never_guessed(self):
        r = self.script([[agent(9, "1")]], {("GET", "/api/sessions/options"): OPTIONS, ("POST", "/api/sessions"): {"ok": True, "id": 9}})
        self.ok("start_agent", dir="~", account="HOME")
        self.assertEqual(r.posts()[-1], ("/api/sessions", {"profile": "home", "dir": "~"}))
        self.assertIn("several match account 'o'", self.refused("start_agent", dir="~", account="o"))
        self.assertIn("no account 'school'. Known: Work, Home", self.refused("start_agent", dir="~", account="school"))
        self.assertEqual(len(r.posts()), 1)

    def test_a_revived_session_is_reported_by_its_number(self):
        sid = "ab" * 12
        r = self.script([[agent(4, "2")]], {("POST", f"/api/history/{sid}/revive"): {"ok": True, "id": 4}})
        self.assertEqual(self.ok("revive_session", id=sid)["agent"]["agent"], "2")
        self.assertEqual(r.posts(), [(f"/api/history/{sid}/revive", {"confirm": True})])
        self.assertIn("session id", self.refused("revive_session", id="../etc"))


def chat(*items, gen=1):
    return {"source": "session", "gen": gen, "items": [{"n": n, "role": role, "kind": "text", "text": text} for n, role, text in items]}


class TestPromptAndWait(ScriptCase):
    def test_the_reply_is_what_the_agent_said_after_the_prompt(self):
        old = chat((5, "assistant", "old answer"))
        new = chat((5, "assistant", "old answer"), (6, "user", "and now?"), (7, "assistant", "new answer"))
        self.script([[agent(3, "1")], [agent(3, "1")], [agent(3, "1", "working")], [agent(3, "1", "done")]],
                    {("GET", "/api/agents/3/chat"): [old, new], ("POST", "/api/agents/3/prompt"): {"ok": True}})
        got = self.ok("send_prompt", agent="1", text="and now?", wait=120)
        self.assertEqual((got["sent"], got["state"], got["reply"]), (True, "done", "new answer"))
        self.assertNotIn("timed_out", got)

    def test_an_agent_that_never_starts_does_not_repeat_its_old_answer(self):
        old = chat((5, "assistant", "old answer"))
        self.script([[agent(3, "1")]], {("GET", "/api/agents/3/chat"): old, ("POST", "/api/agents/3/prompt"): {"ok": True}})
        got = self.ok("send_prompt", agent="1", text="/compact", wait=120)
        self.assertNotIn("reply", got)
        self.assertLess(self.clock.t - 1000, 15, "a command that starts no turn is not waited for")

    def test_a_turn_longer_than_the_wait_says_so(self):
        self.script([[agent(3, "1")], [agent(3, "1", "working")]],
                    {("GET", "/api/agents/3/chat"): chat(), ("POST", "/api/agents/3/prompt"): {"ok": True}})
        got = self.ok("send_prompt", agent="1", text="long job", wait=30)
        self.assertEqual((got["state"], got["timed_out"]), ("working", True))


class TestQueuedPrompt(ScriptCase):
    """A prompt sent to a working agent waits in omp's queue: the end of the current turn is not its answer."""

    def test_the_old_answer_is_never_passed_off_as_the_new_one(self):
        old = {**chat((5, "user", "rebuild"), (6, "assistant", "rebuilt and pushed")), "pending_prompts": [{"text": "add a widget?"}]}
        new = chat((5, "user", "rebuild"), (6, "assistant", "rebuilt and pushed"), (7, "user", "add a widget?"), (8, "assistant", "only a shortcut"))
        self.script([[agent(3, "1", "working")], [agent(3, "1", "done")], [agent(3, "1", "working")], [agent(3, "1", "done")]],
                    {("GET", "/api/agents/3/chat"): [old, old, new]})
        got = self.ok("wait_for_agents", agents=["1"], timeout=300)["stopped"][0]
        self.assertEqual((got["reply"], got["reply_to"]), ("only a shortcut", "add a widget?"))

    def test_an_agent_holding_the_prompt_is_still_waited_for(self):
        held = {**chat((5, "user", "rebuild"), (6, "assistant", "rebuilt")), "pending_prompts": [{"text": "add a widget?"}]}
        self.script([[agent(3, "1", "done")]], {("GET", "/api/agents/3/chat"): held})
        got = self.ok("wait_for_agents", agents=["1"], timeout=10)
        self.assertEqual((got["stopped"], got["still_working"], got["timed_out"]), ([], ["1"], True),
                         "an agent that still holds the prompt is not done with it, and its old answer is not reported")


class TestManaging(ScriptCase):
    def two(self, routes):
        return self.script([[agent(3, "1"), agent(7, "2", title="billing")]], routes)

    def test_quit_confirms_for_the_pane_behind_the_number(self):
        r = self.two({("POST", "/api/agents/7/quit"): {"ok": True, "quit": True}})
        self.assertEqual(self.ok("quit_agent", agent="2"), {"quit": True, "agent": "2", "title": "billing"})
        self.assertEqual(r.posts(), [("/api/agents/7/quit", {"confirm": True})])

    def test_a_refused_quit_is_the_servers_words(self):
        self.two({("POST", "/api/agents/7/quit"): M.ApiError(409, "agent_not_ready", "wait until the agent is idle")})
        self.assertEqual(self.refused("quit_agent", agent="2"), "409 agent_not_ready: wait until the agent is idle")

    def test_a_model_is_named_by_a_unique_part(self):
        models = {"current": "a/sonnet-5", "models": [{"id": "a/sonnet-5", "name": "Sonnet"}, {"id": "a/opus-5", "name": "Opus"},
                                                       {"id": "o/gpt", "name": "GPT"}]}
        r = self.two({("GET", "/api/agents/3/models"): models, ("POST", "/api/agents/3/model"): {"ok": True, "confirmed": True},
                      ("POST", "/api/agents/3/thinking"): {"ok": True, "confirmed": False}})
        got = self.ok("set_model", agent="1", model="opus", thinking="high")
        self.assertEqual((got["model"], got["model_confirmed"], got["thinking_confirmed"]), ("a/opus-5", True, False))
        self.assertEqual(r.posts(), [("/api/agents/3/model", {"model": "a/opus-5"}), ("/api/agents/3/thinking", {"level": "high"})])
        self.assertIn("several match model 'a/'", self.refused("set_model", agent="1", model="a/"))
        self.assertIn("give model", self.refused("set_model", agent="1"))
        self.assertEqual(len(r.posts()), 2)

    def test_an_account_switch_names_the_profile_and_confirms(self):
        offered = {"can": True, "why": None, "accounts": [{"label": "Home", "profile": "home"}, {"label": "Work", "profile": "work"}]}
        r = self.two({("GET", "/api/agents/3/accounts"): offered, ("POST", "/api/agents/3/account"): {"ok": True, "switching": True}})
        self.assertEqual(self.ok("switch_account", agent="1", account="Work")["to"], "Work")
        self.assertEqual(r.posts(), [("/api/agents/3/account", {"profile": "work", "confirm": True})])

    def test_a_switch_that_cannot_start_says_why_and_asks_nothing(self):
        r = self.two({("GET", "/api/agents/3/accounts"): {"can": False, "why": "wait until the agent is idle", "accounts": []}})
        self.assertIn("wait until the agent is idle", self.refused("switch_account", agent="1", account="Work"))
        self.assertEqual(r.posts(), [])

    def test_a_bot_is_named_by_id_or_name(self):
        bots = {"now": NOW, "scheduler": {"alive": True}, "bots": [{"id": "nightly-a1", "name": "Nightly tests", "enabled": True},
                                                                   {"id": "inbox-b2", "name": "Inbox", "enabled": True}]}
        r = self.script([[]], {("GET", "/api/bots"): bots, ("POST", "/api/bots/nightly-a1/run"): {"ok": True, "queued": True},
                               ("POST", "/api/bots/inbox-b2/say"): {"ok": True, "queued": False, "position": 0}})
        self.assertEqual(self.ok("bot_action", bot="nightly", action="run"), {"bot": "nightly-a1", "action": "run", "queued": True})
        self.assertEqual(self.ok("message_bot", bot="inbox-b2", text="anything new?")["sent"], True)
        self.assertEqual(r.posts(), [("/api/bots/nightly-a1/run", {}), ("/api/bots/inbox-b2/say", {"text": "anything new?"})])
        self.assertIn("must be one of", self.refused("bot_action", bot="inbox", action="delete"))


if __name__ == "__main__":
    unittest.main()
