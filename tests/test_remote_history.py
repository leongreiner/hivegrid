"""hivegrid-remote: quitting a session from the phone and the history of past sessions (bin/hivegrid_remote_history.py).
Temp directories and fakes only: a fake `wezterm cli`, a fake process table and session files in a temp home; nothing
starts omp, types into a real pane or reads a real home."""
import json
import os
import sys
import unittest
import unittest.mock

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import hivegrid_remote as R  # noqa: E402
import hivegrid_remote_data as D  # noqa: E402
import hivegrid_remote_history as HI  # noqa: E402
import test_remote as TR  # noqa: E402
from test_agents import DEAD_PID, NOW  # noqa: E402
from test_remote_data import RichCase, jl, msg, text  # noqa: E402

ACCOUNTS = [{"label": "alpha", "profile": "alpha", "dir": "~/.claude-alpha", "auto": False, "hidden": False},
            {"label": "plain", "profile": None, "dir": "~/.claude", "auto": False, "hidden": False}]


class QuitCase(RichCase):
    def setUp(self):
        super().setUp()
        self.write_state(3, state="idle")
        self.fake.panes = [TR.pane(3, "π > ready")]
        self.running, self.front = [True], ["omp"]
        self.app = self.make_quit_app()
        self.tick = 0.6

    def make_quit_app(self, **kw):
        mux = self.mux()
        self.quitter = HI.SessionQuitter(mux, clock=self.clock, sleep=self.sleep, alive=lambda pid: self.running[0],
                                         foreground=lambda: {"pts/3": (1, self.front[0] if self.running[0] else "bash", 0, "")})
        # omp exits on the first Ctrl+D unless the test says it stays
        self.on_sleep = lambda n: None
        return R.App(mux, R.TokenStore(self.tokfile), clock=self.clock, wall=lambda: NOW, log=self.logs.append,
                     theme_watch=TR.ThemeWatch(self.settings), home=self.home, sysstats=self.sysstats, fonts=self.fonts,
                     sleep=self.sleep, quitter=self.quitter, **kw)

    def quit(self, body=None, ident="3", app=None):
        return self.call("POST", f"/api/agents/{ident}/quit", {"confirm": True} if body is None else body, app=app)

    def keys(self):
        return [c[-1] for c in self.sends() if "--no-paste" in c]

    def omp_exits_on_ctrl_d(self):
        self.on_sleep = lambda n: self.running.__setitem__(0, False) if self.keys() else None


class TestQuit(QuitCase):
    def test_quit_sends_only_ctrl_d_and_waits_for_omp_to_leave(self):
        self.omp_exits_on_ctrl_d()
        r, data = self.quit()
        self.assertEqual((r.status, data), (200, {"ok": True, "quit": True}))
        self.assertEqual(self.keys(), ["\x04"], "one Ctrl+D, nothing else: no kill, no text")
        self.assertEqual([c for c in self.sends() if "--no-paste" not in c], [])
        self.assertIn("write quit label=", self.logs[-1])

    def test_a_quit_session_is_gone_from_the_list_not_listed_as_exited(self):
        _, data = self.call("GET", "/api/agents")
        self.assertEqual([(a["state"], a["can"]["quit"]) for a in data["agents"]], [("idle", True)])
        self.write_state(3, state="idle", pid=DEAD_PID)       # omp has quit: only the shell is left in the pane
        self.clock.t += 5
        _, data = self.call("GET", "/api/agents")
        self.assertEqual(data["agents"], [])
        self.assertEqual(data["totals"]["agents"], 0)
        self.assertEqual(self.call("GET", "/api/agents/3/chat")[0].status, 200, "its chat can still be read until the pane goes")

    def test_a_chat_that_is_being_moved_to_another_account_stays_listed(self):
        self.write_state(3, state="idle", pid=DEAD_PID)
        with unittest.mock.patch.object(self.app.switcher, "status", return_value={"state": "running", "to": "beta", "message": "", "since": NOW}):
            _, data = self.call("GET", "/api/agents")
        self.assertEqual([a["state"] for a in data["agents"]], ["exited"])

    def test_confirmation_is_required_and_nothing_is_sent_without_it(self):
        for body in ({}, {"confirm": False}):
            r, data = self.quit(body)
            self.assertEqual((r.status, data["error"]), (409, "confirm_required"), body)
        self.assertEqual(self.keys(), [])

    def test_only_an_idle_done_or_limited_agent_can_be_quit(self):
        for state in ("working", "busy", "attention"):
            self.write_state(3, state=state)
            self.clock.t += 5
            r, data = self.quit()
            self.assertEqual((r.status, data["error"]), (409, "agent_not_ready"), state)
        self.write_state(3, state="exited", pid=DEAD_PID)
        self.clock.t += 5
        r, data = self.quit()
        self.assertEqual((r.status, data["error"]), (409, "agent_not_ready"))
        self.assertEqual(self.keys(), [], "a turn is never interrupted and a shell never gets Ctrl+D")

    def test_omp_that_stays_keeps_running_and_the_page_is_told(self):
        r, data = self.quit()                                   # omp ignores Ctrl+D (unsent text in its prompt)
        self.assertEqual((r.status, data["error"]), (409, "agent_not_ready"))
        self.assertIn("keeps running", data["message"])
        self.assertEqual(self.keys(), ["\x04"])

    def test_if_omp_is_no_longer_in_front_nothing_is_typed(self):
        self.front[0] = "bash"                                  # a shell: Ctrl+D would close the pane
        r, data = self.quit()
        self.assertEqual((r.status, data["error"]), (409, "agent_not_ready"))
        self.assertEqual(self.keys(), [])

    def test_a_second_quit_never_reaches_the_shell_that_is_left(self):
        self.omp_exits_on_ctrl_d()
        self.assertEqual(self.quit()[0].status, 200)
        r, _ = self.quit()
        self.assertEqual(r.status, 409)
        self.assertEqual(self.keys(), ["\x04"], "still the one Ctrl+D")

    def test_read_only_unknown_agents_methods_and_bodies(self):
        app = self.make_quit_app(readonly=True)
        r, data = self.quit(app=app)
        self.assertEqual((r.status, data["error"]), (403, "readonly"))
        self.assertFalse(self.call("GET", "/api/agents", app=app)[1]["agents"][0]["can"]["quit"])
        self.assertEqual(self.quit(ident="9")[0].status, 404)
        self.assertEqual(self.call("GET", "/api/agents/3/quit")[0].status, 405)
        self.assertEqual(self.call("POST", "/api/agents/3/quit", {"confirm": True}, token=None)[0].status, 401)
        for body in ({"confirm": "yes"}, {"confirm": True, "extra": 1}):
            self.assertEqual(self.quit(body)[0].status, 400, body)
        self.assertEqual(self.keys(), [])

    def test_quitting_has_its_own_limit(self):
        self.tick = 0
        self.omp_exits_on_ctrl_d()                    # no waiting on the fake clock, which would refill the bucket
        statuses = [self.quit()[0].status for _ in range(HI.QUIT_BURST + 1)]
        self.assertEqual(statuses[-1], 429)
        self.assertNotIn(429, statuses[:-1])
        r, _ = self.quit()
        self.assertGreater(int(r.headers["Retry-After"]), 0)
        self.clock.t += HI.QUIT_EVERY
        self.assertNotEqual(self.quit()[0].status, 429, "one more after the wait")


def session_lines(cwd, first, title=None):
    rows = [{"type": "session", "cwd": cwd}]
    if title:
        rows.append({"type": "title", "title": title})
    rows.append(msg("user", text(first)))
    return jl(*rows)


class HistoryCase(QuitCase):
    def setUp(self):
        super().setUp()
        with open(self.settings, "w") as f:
            json.dump({"accounts": ACCOUNTS}, f)
        self.work = os.path.join(self.home, "work")
        os.makedirs(self.work)
        self.spawned = []
        patcher = unittest.mock.patch.object(R.A, "spawn_agent", side_effect=self.fake_spawn)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_spawn(self, cwd=None, near=None, run=None, profile=None, arrange=False, resume=None):
        self.spawned.append((cwd, profile, arrange, resume))
        return 41

    def history(self, query=""):
        r, data = self.call("GET", "/api/history" + query)
        self.assertEqual(r.status, 200)
        return data

    def stamp(self, path, when):
        os.utime(path, (when, when))


class TestHistoryListing(HistoryCase):
    def test_past_sessions_come_newest_first_with_what_a_person_needs(self):
        old = self.make_session(session_lines(self.work, "fix the parser", "Parser fix"), "old.jsonl", profile="alpha")
        new = self.make_session(session_lines(self.work, "add a cache"), "new.jsonl", profile="alpha")
        self.stamp(old, NOW - 5000)
        self.stamp(new, NOW - 10)
        data = self.history()
        self.assertEqual([i["title"] for i in data["items"]], ["add a cache", "Parser fix"])
        first, second = data["items"]
        self.assertEqual((first["dir"], first["account"], first["live"], first["can_revive"]), ("~/work", "alpha", False, True))
        self.assertEqual((second["preview"], second["ts"]), ("fix the parser", NOW - 5000))
        self.assertNotIn(self.home, json.dumps(data), "directories are shown as ~/...")
        self.assertNotIn(old, json.dumps(data), "the phone only ever sees opaque ids")
        self.assertRegex(first["id"], r"^[0-9a-f]{24}$")

    def test_a_session_nobody_ever_wrote_to_is_not_listed(self):
        self.make_session(jl({"type": "session", "cwd": self.work}), "empty.jsonl")
        self.assertEqual(self.history()["items"], [])

    def test_pages(self):
        for i in range(5):
            self.stamp(self.make_session(session_lines(self.work, f"task {i}"), f"s{i}.jsonl"), NOW - i)
        page = self.history("?limit=2&offset=0")
        self.assertEqual(([i["title"] for i in page["items"]], page["more"], page["total"]), (["task 0", "task 1"], True, 5))
        last = self.history("?limit=2&offset=4")
        self.assertEqual(([i["title"] for i in last["items"]], last["more"]), (["task 4"], False))
        for bad in ("?limit=0", "?limit=abc", "?offset=-1", "?limit=999"):
            r = self.call("GET", "/api/history" + bad)[0]
            self.assertIn(r.status, (200, 400), bad)

    def test_a_session_open_in_an_agent_is_marked_and_cannot_be_started_twice(self):
        path = self.make_session(session_lines(self.work, "busy chat"), "live.jsonl")
        self.write_state(3, state="idle", sessionFile=path)
        (item,) = self.history()["items"]
        self.assertEqual((item["live"], item["can_revive"]), (True, False))
        self.assertIn("agent", item["why"])
        r, data = self.call("POST", f"/api/history/{item['id']}/revive", {"confirm": True})
        self.assertEqual((r.status, data["error"]), (409, "conflict"))
        self.assertEqual(self.spawned, [])

    def test_text_from_a_session_file_is_cleaned(self):
        evil = "\x1b[31mred\x1b[0m \u202etxt <img src=x onerror=1>"
        self.make_session(session_lines(self.work, evil), "evil.jsonl")
        (item,) = self.history()["items"]
        for field in (item["title"], item["preview"]):
            self.assertNotIn("\x1b", field)
            self.assertNotIn("\u202e", field)

    def test_files_that_are_not_sessions_of_ours_are_left_out(self):
        outside = os.path.join(self.tmp.name, "elsewhere.jsonl")
        with open(outside, "w") as f:
            f.write(session_lines(self.work, "secret"))
        base = os.path.join(self.home, ".omp", "profiles", "alpha", "agent", "sessions", "-p")
        os.makedirs(base, exist_ok=True)
        os.symlink(outside, os.path.join(base, "link.jsonl"))
        self.assertEqual(self.history()["items"], [])

    def test_read_only_lists_but_cannot_revive(self):
        self.make_session(session_lines(self.work, "x"), "a.jsonl")
        app = self.make_quit_app(readonly=True)
        r, data = self.call("GET", "/api/history", app=app)
        self.assertEqual((r.status, data["items"][0]["can_revive"]), (200, False))
        self.assertEqual(self.call("GET", "/api/history", token=None)[0].status, 401)
        self.assertEqual(self.call("POST", "/api/history", {})[0].status, 405)


class TestRevive(HistoryCase):
    def revive(self, sid, body=None, **kw):
        return self.call("POST", f"/api/history/{sid}/revive", {"confirm": True} if body is None else body, **kw)

    def one(self, profile="alpha", cwd=None, first="hello"):
        path = self.make_session(session_lines(cwd or self.work, first), "r.jsonl", profile=profile)
        return path, self.history()["items"][0]["id"]

    def test_revive_resumes_the_file_in_its_directory_on_its_account_and_arranges(self):
        path, sid = self.one()
        r, data = self.revive(sid)
        self.assertEqual((r.status, data), (200, {"ok": True, "id": 41}))
        self.assertEqual(self.spawned, [(self.work, "alpha", True, path)])
        self.assertNotIn(path, " ".join(self.logs), "no paths in the log")

    def test_the_default_configuration_has_no_profile(self):
        base = os.path.join(self.home, ".omp", "agent", "sessions", "-p")
        os.makedirs(base)
        path = os.path.realpath(os.path.join(base, "d.jsonl"))
        with open(path, "w") as f:
            f.write(session_lines(self.work, "hi"))
        sid = self.history()["items"][0]["id"]
        self.assertEqual(self.revive(sid)[0].status, 200)
        self.assertEqual(self.spawned, [(self.work, None, True, path)])

    def test_confirmation_unknown_ids_and_bodies(self):
        _, sid = self.one()
        for body in ({}, {"confirm": False}):
            r, data = self.revive(sid, body)
            self.assertEqual((r.status, data["error"]), (409, "confirm_required"), body)
        for bad in ("0" * 24, "zz", "../../etc/passwd", "a" * 25):
            self.assertIn(self.revive(bad)[0].status, (404, 400), bad)
        self.assertEqual(self.revive(sid, {"confirm": True, "path": "/etc/passwd"})[0].status, 400)
        self.assertEqual(self.revive(sid, {"confirm": 1})[0].status, 400)
        self.assertEqual(self.revive(sid, token=None)[0].status, 401)
        self.assertEqual(self.spawned, [])

    def test_a_directory_that_is_gone_or_unsafe_is_refused(self):
        _, sid = self.one(cwd=os.path.join(self.home, "gone"))
        r, data = self.revive(sid)
        self.assertEqual(r.status, 409)
        self.assertIn("directory", data["message"])
        self.assertEqual(self.spawned, [])

    def test_shell_metacharacters_in_a_directory_name_are_harmless_but_a_backslash_is_refused(self):
        weird = os.path.join(self.home, "wo rk;$(id)")       # the directory is an argv entry and the file is quoted: fine
        os.makedirs(weird)
        _, sid = self.one(cwd=weird)
        self.assertEqual(self.revive(sid)[0].status, 200)
        self.assertEqual(self.spawned[0][0], weird)
        self.spawned.clear()
        self.clock.t += R.SESSION_REPEAT
        slash = os.path.join(self.home, "back\\slash")      # fish reads a backslash in single quotes as an escape
        os.makedirs(slash)
        self.make_session(session_lines(slash, "x"), "b.jsonl")
        item = next(i for i in self.history()["items"] if i["dir"].endswith("slash"))
        self.assertFalse(item["can_revive"])
        self.assertEqual(self.revive(item["id"])[0].status, 409)
        self.assertEqual(self.spawned, [])

    def test_a_file_swapped_for_a_link_after_listing_is_not_followed(self):
        path, sid = self.one()
        os.unlink(path)
        os.symlink("/etc/hostname", path)
        r, data = self.revive(sid)
        self.assertEqual((r.status, data["error"]), (404, "not_found"))
        self.assertEqual(self.spawned, [])

    def test_the_same_session_is_not_started_twice_in_a_row(self):
        self.tick = 0
        _, sid = self.one()
        self.assertEqual(self.revive(sid)[0].status, 200)
        r, data = self.revive(sid)
        self.assertEqual((r.status, data["error"]), (409, "conflict"))
        self.assertEqual(len(self.spawned), 1)
        self.clock.t += R.SESSION_REPEAT
        self.assertEqual(self.revive(sid)[0].status, 200)

    def test_a_failed_start_may_be_retried_and_failures_are_reported(self):
        self.tick = 0
        _, sid = self.one()
        with unittest.mock.patch.object(R.A, "spawn_agent", return_value=None):
            self.assertEqual(self.revive(sid)[0].status, 502)
        self.assertEqual(self.revive(sid)[0].status, 200)
        with unittest.mock.patch.object(R.A, "spawn_agent", side_effect=ValueError("a profile or a resumed session needs the plain omp command")):
            self.clock.t += R.SESSION_REPEAT
            self.assertEqual(self.revive(sid)[0].status, 400)

    def test_read_only_cannot_revive(self):
        _, sid = self.one()
        r, data = self.revive(sid, app=self.make_quit_app(readonly=True))
        self.assertEqual((r.status, data["error"]), (403, "readonly"))
        self.assertEqual(self.spawned, [])


class TestResumeCommand(unittest.TestCase):
    def test_resume_adds_the_quoted_file_after_the_profile_and_stays_plain_omp(self):
        cmd = R.A._agent_command("beta", True, "/h/.omp/profiles/beta/agent/sessions/-p/a b.jsonl")[2]
        self.assertTrue(cmd.endswith("omp --profile=beta --resume '/h/.omp/profiles/beta/agent/sessions/-p/a b.jsonl'; exec bash"), cmd)
        self.assertEqual(R.A._agent_command(None, False, "/s/a.jsonl")[2], "omp --resume /s/a.jsonl; exec bash")
        with unittest.mock.patch.dict(os.environ, {"HIVEGRID_AGENT_CMD": "sleep 1"}):
            with self.assertRaises(ValueError):
                R.A._agent_command(None, False, "/s/a.jsonl")


if __name__ == "__main__":
    unittest.main()
