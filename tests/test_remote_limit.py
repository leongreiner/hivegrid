"""hivegrid-remote: the usage-limit state of an agent and switching its account from the phone.
Temp directories and fakes only: a fake `wezterm cli`, a fake `omp-switch` and a fake process table; nothing
reads the real home, starts omp or moves a chat."""
import json
import os
import subprocess
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import hivegrid_remote as R  # noqa: E402
import hivegrid_remote_data as D  # noqa: E402
import test_remote as TR  # noqa: E402
from test_agents import DEAD_PID, NOW, SELF_PID, limit_entry  # noqa: E402
from test_remote_data import RichCase, jl, msg, text  # noqa: E402

HIT = NOW - 120
ACCOUNTS = [
    {"label": "alpha", "profile": "alpha", "dir": "~/.claude-alpha", "auto": False, "hidden": False},
    {"label": "beta", "profile": "beta", "dir": "~/.claude-beta", "auto": False, "hidden": False},
    {"label": "gamma", "profile": None, "dir": "~/.claude-gamma", "auto": False, "hidden": False},
    {"label": "delta", "profile": "delta", "dir": "~/.claude-delta", "auto": False, "hidden": True},
]


def refusal(hit_at, retry_s):
    """A refused request as omp writes it, completed at `hit_at` (epoch seconds), reopening after `retry_s`."""
    body = {"type": "error", "error": {"type": "rate_limit_error", "message": "This request would exceed your account's rate limit."},
            "request_id": "req_x"}
    text_ = f"429 {json.dumps(body)} retry-after-ms={int(retry_s * 1000)}"
    return limit_entry(errorMessage=text_, completedAt=int(hit_at * 1000), timestamp=int(hit_at * 1000) - 300)


class LimitCase(RichCase):
    def setUp(self):
        super().setUp()
        with open(self.settings, "w") as f:
            json.dump({"accounts": ACCOUNTS}, f)
        self.quota = os.path.join(self.tmp.name, "quota")
        os.makedirs(self.quota, exist_ok=True)

    def quota_file(self, label, five=None, seven=None):
        """A claude-quota cache entry; five and seven are (pct, reset_at)."""
        doc = {"label": label, "state": "ok", "updated_at": NOW,
               "five_hour": {"pct": five[0], "reset_at": five[1]} if five else {"pct": 0.0, "reset_at": None},
               "seven_day": {"pct": seven[0], "reset_at": seven[1]} if seven else {"pct": 0.0, "reset_at": None}}
        with open(os.path.join(self.quota, f"{label}.json"), "w") as f:
            json.dump(doc, f)

    def row(self):
        r, data = self.call("GET", "/api/agents")
        self.assertEqual(r.status, 200)
        return data["agents"][0]


class TestLimitState(LimitCase):
    def test_a_limit_agent_shows_its_state_window_and_reset(self):
        self.write_state(3, state="limit", reason="usage limit", limit={"hit_at": HIT, "reset_at": NOW + 4 * 3600})
        self.quota_file("alpha", five=(100.0, NOW + 4 * 3600 + 1), seven=(33.0, NOW + 4 * 86400))
        row = self.row()
        self.assertEqual((row["state"], row["state_label"], row["rank"], row["reason"]), ("limit", "limit", 2, "usage limit"))
        self.assertEqual(row["limit"], {"window": "five_hour", "label": "5-hour limit", "hit_at": HIT, "reset_at": NOW + 4 * 3600,
                                        "account": "alpha"})
        self.assertEqual((row["can"]["prompt"], row["can"]["ack"], row["can"]["account"]), (True, False, True))
        _, data = self.call("GET", "/api/agents")
        self.assertEqual(data["totals"]["by_state"], {"limit": 1})

    def test_the_weekly_window_is_named_by_its_reset_or_by_the_horizon(self):
        week = NOW + 3 * 86400
        self.write_state(3, state="limit", limit={"hit_at": HIT, "reset_at": week})
        self.quota_file("alpha", five=(10.0, NOW + 3600), seven=(100.0, week - 2))
        self.assertEqual(self.row()["limit"]["window"], "seven_day")
        self.quota_file("alpha")                      # no usable numbers: the horizon alone says weekly
        self.assertEqual(self.row()["limit"]["label"], "weekly limit")

    def test_a_window_that_cannot_be_told_is_not_guessed(self):
        self.write_state(3, state="limit", limit={"hit_at": HIT, "reset_at": NOW + 2 * 3600})
        row = self.row()["limit"]
        self.assertEqual((row["window"], row["label"], row["reset_at"]), (None, "usage limit", NOW + 2 * 3600))
        self.write_state(3, state="limit", limit=None)       # an extension that gave no times at all
        self.clock.t += 5
        row = self.row()["limit"]
        self.assertEqual((row["window"], row["hit_at"], row["reset_at"]), (None, None, None))

    def test_a_normal_done_is_not_a_limit(self):
        ok = msg("assistant", text("all green"), stopReason="stop")
        path = self.make_session(jl(msg("user", text("go")), ok))
        self.write_state(3, state="done", sessionFile=path)
        row = self.row()
        self.assertEqual((row["state"], row["limit"]), ("done", None))

    def test_other_errors_are_not_a_limit(self):
        for bad in (limit_entry(stopReason="aborted"), limit_entry(errorStatus=500, errorMessage="500 internal error"),
                    limit_entry(errorStatus=None, errorMessage="The socket connection was closed unexpectedly"),
                    refusal(HIT, 8)):
            path = self.make_session(jl(msg("user", text("go")), bad))
            self.write_state(3, state="done", sessionFile=path)
            self.clock.t += 5
            self.assertEqual(self.row()["state"], "done", bad["message"].get("errorMessage"))

    def test_an_extension_without_the_state_is_read_from_the_session_file(self):
        path = self.make_session(jl(msg("user", text("go")), refusal(HIT, 4 * 3600)))
        self.write_state(3, state="done", sessionFile=path)
        row = self.row()
        self.assertEqual((row["state"], row["reason"], row["limit"]["reset_at"]), ("limit", "usage limit", HIT + 4 * 3600))
        self.assertEqual(row["limit"]["hit_at"], HIT)

    def test_a_chat_that_went_on_or_is_not_done_is_not_a_limit(self):
        # the user wrote after the refusal: the chat went on
        path = self.make_session(jl(refusal(HIT, 4 * 3600), msg("user", text("go on"))))
        self.write_state(3, state="done", sessionFile=path)
        self.assertEqual(self.row()["state"], "done")
        # a resumed chat whose last message is an old refusal sits idle until it is prompted (after a switch, say)
        path = self.make_session(jl(msg("user", text("go")), refusal(HIT, 4 * 3600)), name="resumed.jsonl")
        self.write_state(3, state="idle", sessionFile=path)
        self.clock.t += 5
        self.assertEqual(self.row()["state"], "idle")
        self.write_state(3, state="working", sessionFile=path)
        self.clock.t += 5
        self.assertEqual(self.row()["state"], "working")

    def test_the_chat_shows_the_refusal_and_the_limit(self):
        body = jl(msg("user", text("go")), refusal(HIT, 4 * 3600))
        path = self.make_session(body)
        self.write_state(3, state="limit", sessionFile=path, limit={"hit_at": HIT, "reset_at": HIT + 4 * 3600})
        r, data = self.call("GET", "/api/agents/3/chat")
        self.assertEqual(r.status, 200)
        kinds = [(i["role"], i["kind"]) for i in data["items"]]
        self.assertEqual(kinds, [("user", "text"), ("note", "limit")])
        self.assertEqual(data["items"][1]["limit"], {"hit_at": HIT, "reset_at": HIT + 4 * 3600})
        self.assertEqual((data["state"], data["limit"]["label"]), ("limit", "usage limit"))
        self.assertIsNone(data["switch"])
        # the same file under an agent that is not limited: the item stays history, the agent has no limit object
        self.write_state(3, state="idle", sessionFile=path)
        self.clock.t += 5
        _, data = self.call("GET", "/api/agents/3/chat")
        self.assertEqual((data["state"], data["limit"], data["items"][1]["kind"]), ("idle", None, "limit"))

    def test_a_burst_refusal_is_no_chat_item(self):
        path = self.make_session(jl(msg("user", text("go")), refusal(HIT, 8)))
        self.write_state(3, state="done", sessionFile=path)
        _, data = self.call("GET", "/api/agents/3/chat")
        self.assertEqual([i["kind"] for i in data["items"]], ["text"])

    def test_the_limit_note_takes_the_next_free_index_of_its_line(self):
        refused = refusal(HIT, 4 * 3600)
        refused["message"]["content"] = [{"type": "text", "text": "partial answer"}, {"type": "text", "text": "more"}]
        path = self.make_session(jl(msg("user", text("go")), refused))
        self.write_state(3, state="limit", sessionFile=path, limit={"hit_at": HIT, "reset_at": HIT + 4 * 3600})
        _, data = self.call("GET", "/api/agents/3/chat")
        self.assertEqual([(i["role"], i["kind"]) for i in data["items"]],
                         [("user", "text"), ("assistant", "text"), ("assistant", "text"), ("note", "limit")])
        ns = [i["n"] for i in data["items"]]
        self.assertEqual(ns, sorted(set(ns)), "unique and ascending")


class FakeOmpSwitch:
    """Stands in for `omp-switch`: answers `locate` and `transfer` and records the calls."""

    def __init__(self):
        self.calls = []
        self.locate = {"state": "ok", "session": None, "cwd": "/work/proj"}
        self.transfer = {"state": "ok", "session": None}

    def __call__(self, argv, **kw):
        self.calls.append(tuple(argv[1:]))
        answer = self.locate if argv[1] == "locate" else self.transfer
        return subprocess.CompletedProcess(argv, 0 if answer["state"] != "error" else 1, json.dumps(answer), "")


class SwitchCase(LimitCase):
    def setUp(self):
        super().setUp()
        self.src = self.make_session(jl(msg("user", text("go")), refusal(HIT, 4 * 3600)), profile="alpha")
        self.dst = self.make_session(jl(msg("user", text("go")), refusal(HIT, 4 * 3600)), profile="beta")
        self.write_state(3, state="limit", sessionFile=self.src, cwd="/work/proj", limit={"hit_at": HIT, "reset_at": HIT + 4 * 3600})
        self.omp = FakeOmpSwitch()
        self.omp.locate["session"], self.omp.transfer["session"] = self.src, self.dst
        self.jobs, self.running, self.shell, self.front = [], [True], ["bash"], ["omp"]
        self.fake.panes = [TR.pane(3, "π > ready")]
        self.app = self.make_switch_app()

    def make_switch_app(self, **kw):
        mux = self.mux()
        self.switcher = D.AccountSwitcher(mux, R.sanitize_line, home=self.home, run=self.omp, clock=self.clock,
                                          wall=lambda: NOW, sleep=self.sleep, alive=lambda pid: self.running[0],
                                          foreground=lambda: {"pts/3": (1, self.front[0] if self.running[0] else self.shell[0], 0, "")},
                                          spawn=self.jobs.append,
                                          switch_bin="/fake/omp-switch")
        return R.App(mux, R.TokenStore(self.tokfile), clock=self.clock, wall=lambda: NOW, log=self.logs.append,
                     theme_watch=TR.ThemeWatch(self.settings), home=self.home, sysstats=self.sysstats, fonts=self.fonts,
                     sleep=self.sleep, switcher=self.switcher, **kw)

    def go(self, body=None, ident="3"):
        return self.call("POST", f"/api/agents/{ident}/account", {"profile": "beta", "confirm": True} if body is None else body)

    def typed(self):
        """What was sent to pane 3: ("key", text) for each send-text call."""
        out = []
        for c in self.sends():
            out.append(("key" if "--no-paste" in c else "paste", c[-1]))
        return out

    def finish(self):
        self.assertEqual(len(self.jobs), 1)
        self.running[0] = False          # omp exits on Ctrl+D
        self.jobs.pop()()


class TestAccountOptions(SwitchCase):
    def test_the_accounts_a_chat_can_move_to(self):
        self.quota_file("beta", five=(5.0, NOW + 3600), seven=(40.0, NOW + 86400))
        r, data = self.call("GET", "/api/agents/3/accounts")
        self.assertEqual(r.status, 200)
        self.assertEqual(data["current"], {"label": "alpha", "profile": "alpha"})
        # not the current account, not the one without an omp profile, not the hidden one
        self.assertEqual([(a["label"], a["profile"]) for a in data["accounts"]], [("beta", "beta")])
        self.assertEqual(data["accounts"][0]["five_hour"], {"pct": 5.0, "reset_at": NOW + 3600})
        self.assertEqual((data["can"], data["why"], data["switch"]), (True, None, None))
        self.assertNotIn(self.home, json.dumps(data))

    def test_why_not(self):
        self.write_state(3, state="working", sessionFile=self.src)
        data = self.call("GET", "/api/agents/3/accounts")[1]
        self.assertEqual((data["can"], data["why"]), (False, "wait until the agent is idle or has stopped on its limit"))
        self.write_state(3, state="limit", profile=None)
        self.clock.t += 5
        data = self.call("GET", "/api/agents/3/accounts")[1]
        self.assertFalse(data["can"])
        self.assertIn("default configuration", data["why"])
        self.assertEqual(self.call("GET", "/api/agents/9/accounts")[0].status, 404)
        self.assertEqual(self.call("GET", "/api/agents/3/accounts", token=None)[0].status, 401)

    def test_read_only_offers_but_cannot(self):
        app = self.make_switch_app(readonly=True)
        data = self.call("GET", "/api/agents/3/accounts", app=app)[1]
        self.assertEqual((data["can"], data["why"]), (False, "this server is read-only"))
        self.assertFalse(self.call("GET", "/api/agents", app=app)[1]["agents"][0]["can"]["account"])


class TestAccountSwitch(SwitchCase):
    def test_the_switch_asks_omp_to_quit_then_moves_the_chat_and_resumes_it(self):
        r, data = self.go()
        self.assertEqual((r.status, data), (202, {"ok": True, "switching": True, "to": "beta"}))
        self.assertEqual(self.omp.calls, [("locate", "--pid", str(SELF_PID), "--from", "alpha")])
        self.assertEqual(self.typed(), [("key", "\x04")], "only Ctrl+D goes out before omp has quit")
        row = self.row()
        self.assertEqual(row["switch"]["state"], "running")
        self.assertEqual((row["switch"]["to"], row["can"]["account"]), ("beta", False))
        self.finish()
        self.assertEqual(self.omp.calls[1], ("transfer", "--session", self.src, "--from", "alpha", "--to", "beta"))
        self.assertEqual(self.typed()[1:], [("paste", f"cd /work/proj && omp --profile=beta --resume {self.dst}"), ("key", "\r")])
        status = self.switcher.status(3)
        self.assertEqual((status["state"], status["to"], status["message"]), ("done", "beta", "the chat continues on beta"))
        self.assertEqual(self.row()["switch"]["state"], "done")
        self.assertEqual(self.call("GET", "/api/agents/3/chat")[1]["switch"]["state"], "done")

    def test_the_result_is_forgotten_after_a_while(self):
        self.go()
        self.finish()
        self.clock.t += D.SWITCH_RESULT_TTL + 1
        self.assertIsNone(self.row()["switch"])

    def test_a_chat_without_a_message_starts_fresh_on_the_new_account(self):
        self.omp.locate = {"state": "none"}
        self.go()
        self.finish()
        self.assertEqual([c[0] for c in self.omp.calls], ["locate"], "nothing to transfer")
        self.assertEqual(self.typed()[1], ("paste", "cd /work/proj && omp --profile=beta"))

    def test_omp_that_stays_is_left_running_and_nothing_is_typed(self):
        self.go()
        self.jobs.pop()()            # omp never exits (a draft in its prompt)
        st = self.switcher.status(3)
        self.assertEqual(st["state"], "failed")
        self.assertIn("did not quit", st["message"])
        self.assertEqual(self.typed(), [("key", "\x04")])
        self.assertEqual([c[0] for c in self.omp.calls], ["locate"])
        self.assertGreaterEqual(sum(self.slept), D.EXIT_WAIT)

    def test_a_pane_that_is_not_at_a_shell_after_the_exit_is_left_alone(self):
        self.shell[0] = "htop"
        self.go()
        self.finish()
        self.assertEqual(self.switcher.status(3)["state"], "failed")
        self.assertEqual(self.typed(), [("key", "\x04")])
        self.assertEqual([c[0] for c in self.omp.calls], ["locate"], "the chat was not copied either")

    def test_a_failed_transfer_resumes_the_chat_where_it_was(self):
        self.omp.transfer = {"state": "error", "reason": "profile beta already has a different chat under this session name"}
        self.go()
        self.finish()
        st = self.switcher.status(3)
        self.assertEqual(st["state"], "failed")
        self.assertIn("could not be moved", st["message"])
        self.assertIn("continues on alpha", st["message"])
        self.assertEqual(self.typed()[1:], [("paste", f"cd /work/proj && omp --profile=alpha --resume {self.src}"), ("key", "\r")])

    def test_a_copy_outside_the_target_profile_is_never_typed(self):
        self.omp.transfer = {"state": "ok", "session": self.src}      # the source file is not a session of beta
        self.go()
        self.finish()
        self.assertEqual(self.switcher.status(3)["state"], "failed")
        self.assertNotIn("--profile=beta", "".join(t for _, t in self.typed()))

    def test_locate_failing_aborts_before_omp_is_touched(self):
        self.omp.locate = {"state": "error", "reason": "session not found: omp left no record for this terminal"}
        r, data = self.go()
        self.assertEqual((r.status, data["error"]), (409, "agent_not_ready"))
        self.assertIn("omp keeps running", data["message"])
        self.assertEqual(self.sends(), [])
        self.assertEqual(self.jobs, [])
        self.assertIsNone(self.switcher.status(3))
        self.omp.locate = {"state": "ok", "session": self.src, "cwd": "/work/proj"}
        self.assertEqual(self.go()[0].status, 202, "nothing is left behind that blocks a second try")

    def test_ctrl_d_is_never_sent_to_a_pane_where_omp_is_gone(self):
        for setup in (lambda: self.running.__setitem__(0, False), lambda: self.front.__setitem__(0, "vim")):
            setup()
            self.clock.t += R.ACCOUNT_EVERY
            r, data = self.go()
            self.assertEqual((r.status, data["error"]), (409, "agent_not_ready"))
            self.assertIn("no longer running", data["message"])
            self.assertEqual((self.sends(), self.jobs, self.switcher.status(3)), ([], [], None))
            self.running[0], self.front[0] = True, "omp"
        self.assertEqual(self.go()[0].status, 202, "nothing is left behind that blocks a second try")

    def test_a_worker_that_cannot_start_ends_the_job(self):
        def boom(fn):
            raise RuntimeError("no threads")
        self.switcher.spawn = boom
        self.assertEqual(self.go()[0].status, 202)
        st = self.switcher.status(3)
        self.assertEqual(st["state"], "failed")
        self.assertEqual(self.typed(), [("key", "\x04")])
        self.switcher.spawn = self.jobs.append
        self.clock.t += R.ACCOUNT_EVERY
        self.assertEqual(self.go()[0].status, 202, "a failed job does not block the next switch")

    def test_a_directory_or_session_path_with_a_backslash_is_refused(self):
        self.assertFalse(D._plain_path("/work/pro\\j"))
        self.assertTrue(D._plain_path("/work/proj"))
        self.omp.locate = {"state": "ok", "session": self.src, "cwd": "/work/pro\\j"}
        r, data = self.go()
        self.assertEqual((r.status, data["error"]), (409, "agent_not_ready"))
        self.assertEqual(self.sends(), [])

    def test_a_session_outside_the_profile_or_a_directory_with_control_characters_is_refused(self):
        other = self.make_session("", name="o.jsonl", profile="beta")
        for locate in ({"state": "ok", "session": other, "cwd": "/work/proj"},
                       {"state": "ok", "session": self.src, "cwd": "/work/pr\noj"},
                       {"state": "ok", "session": self.src, "cwd": "relative"},
                       {"state": "ok", "session": "/etc/passwd", "cwd": "/work"}):
            self.omp.locate = locate
            self.clock.t += R.ACCOUNT_EVERY
            r, data = self.go()
            self.assertEqual((r.status, data["error"]), (409, "agent_not_ready"), locate)
        self.assertEqual(self.sends(), [])

    def test_a_second_switch_while_one_runs_is_a_conflict(self):
        self.assertEqual(self.go()[0].status, 202)
        self.clock.t += 100
        r, data = self.go()
        self.assertEqual((r.status, data["error"]), (409, "conflict"))
        self.assertEqual(len(self.jobs), 1)

    def test_the_server_closing_ends_the_wait(self):
        self.switcher.sleep = lambda s: True          # App.sleep returns True when the server is closing
        self.go()
        self.jobs.pop()()
        self.assertEqual(self.switcher.status(3)["state"], "failed")
        self.assertEqual(self.typed(), [("key", "\x04")])

    def test_the_log_names_no_path(self):
        self.go()
        self.finish()
        text = "\n".join(self.logs)
        self.assertIn("write account label=1", text)
        for secret in (self.home, "/work/proj", "s.jsonl", "--resume"):
            self.assertNotIn(secret, text)


class TestAccountRefusals(SwitchCase):
    def test_no_token_wrong_origin_and_read_only(self):
        self.assertEqual(self.call("POST", "/api/agents/3/account", {"profile": "beta", "confirm": True}, token=None)[0].status, 401)
        r, _ = self.call("POST", "/api/agents/3/account", {"profile": "beta", "confirm": True}, headers={"origin": "http://evil"})
        self.assertEqual(r.status, 403)
        self.assertEqual(self.call("GET", "/api/agents/3/account")[0].status, 405)
        app = self.make_switch_app(readonly=True)
        r, data = self.call("POST", "/api/agents/3/account", {"profile": "beta", "confirm": True}, app=app)
        self.assertEqual((r.status, data["error"]), (403, "readonly"))

    def test_only_offered_accounts_are_accepted(self):
        self.omp.calls.clear()
        for body in ({"profile": "nope", "confirm": True}, {"profile": "alpha", "confirm": True},        # not offered / the current one
                     {"profile": "gamma", "confirm": True}, {"profile": "delta", "confirm": True},        # no profile / hidden
                     {"profile": "Beta", "confirm": True}, {"profile": "../beta", "confirm": True},
                     {"profile": "", "confirm": True}, {"profile": None, "confirm": True}, {"profile": ["beta"], "confirm": True},
                     {"profile": "beta", "confirm": "yes"}, {"confirm": True}, {}, {"profile": "beta", "confirm": True, "x": 1},
                     {"profile": "b" * 80, "confirm": True}):
            r, data = self.go(body)
            self.assertEqual((r.status, data["error"]), (400, "bad_request"), body)
        self.assertEqual((self.omp.calls, self.sends(), self.jobs), ([], [], []))

    def test_confirmation_is_required(self):
        r, data = self.go({"profile": "beta"})
        self.assertEqual((r.status, data["error"]), (409, "confirm_required"))
        self.assertEqual(self.go({"profile": "beta", "confirm": False})[1]["error"], "confirm_required")
        self.assertEqual((self.omp.calls, self.sends()), ([], []))

    def test_states_that_cannot_be_switched(self):
        for state, extra in (("working", {}), ("busy", {}), ("attention", {"pending": {"kind": "ask", "question": "q", "options": []}}),
                             ("exited", {"pid": DEAD_PID})):
            self.write_state(3, state=state, sessionFile=self.src, **extra)
            self.clock.t += 5
            r, data = self.go()
            self.assertEqual((r.status, data["error"]), (409, "agent_not_ready"), state)
        self.assertEqual((self.omp.calls, self.sends()), ([], []))
        for state in ("limit", "done", "idle"):
            self.write_state(3, state=state, sessionFile=self.src)
            self.clock.t += 20
            self.assertEqual(self.go()[0].status, 202, state)
            self.jobs.clear()
            self.switcher._jobs.clear()

    def test_an_agent_on_omps_default_profile_cannot_be_moved(self):
        self.write_state(3, state="limit", profile=None)
        r, data = self.go()
        self.assertEqual((r.status, data["error"]), (409, "agent_not_ready"))
        self.assertIn("default configuration", data["message"])

    def test_an_agent_without_a_process_id_or_with_a_custom_command_is_refused(self):
        self.write_state(3, state="limit", pid=None)
        self.assertEqual(self.go()[0].status, 409)
        self.write_state(3, state="limit")
        self.clock.t += 5
        with unittest.mock.patch.dict(os.environ, {"HIVEGRID_AGENT_CMD": "my-omp-wrapper"}):
            r, data = self.go()
        self.assertEqual((r.status, data["error"]), (409, "agent_not_ready"))

    def test_unknown_agent(self):
        self.assertEqual(self.go(ident="9")[0].status, 404)
        self.assertEqual(self.go(ident="x")[0].status, 404)

    def test_switches_are_rate_limited(self):
        codes = []
        for _ in range(4):
            r, data = self.go()
            codes.append(r.status)
            self.jobs.clear()
            self.switcher._jobs.clear()
        self.assertEqual(codes, [202, 202, 429, 429])
        self.assertIn("Retry-After", r.headers)
        self.clock.t += R.ACCOUNT_EVERY
        self.assertEqual(self.go()[0].status, 202)

    def test_a_refused_request_does_not_spend_the_allowance(self):
        for _ in range(5):
            self.go({"profile": "nope", "confirm": True})
            self.go({"profile": "beta"})
        self.assertEqual(self.go()[0].status, 202)

    def test_the_write_limit_of_the_api_applies_too(self):
        self.tick = 0
        for _ in range(10):
            self.go({"profile": "nope", "confirm": True})
        r, data = self.go({"profile": "nope", "confirm": True})
        self.assertEqual((r.status, data["error"]), (429, "rate_limited"))


if __name__ == "__main__":
    unittest.main()
