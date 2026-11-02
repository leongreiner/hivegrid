"""hivegrid-remote: the Bots API (bin/hivegrid_remote_bots.py, /api/bots in bin/hivegrid_remote.py). Temp directories and
fakes only: the schedules, their state and the scheduler's summary live in a temp home and runtime dir, a fake `wezterm
cli` stands behind the server, and nothing starts omp, reads a real home or talks to a scheduler. The checks that matter
most are the restrictions of a phone: no `full` permissions, a time and a cost cap on every bot, at most 50 bots, offered
directories and accounts only, a whitelist of fields, and session files that must lie below ~/.omp."""
import json
import os
import sys
import unittest
import unittest.mock

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import hivegrid_remote as R  # noqa: E402
import hivegrid_remote_bots as B  # noqa: E402
import hivegrid_remote_history as HI  # noqa: E402
import hivegrid_schedule as S  # noqa: E402
import test_remote as TR  # noqa: E402
from test_agents import NOW  # noqa: E402
from test_remote_history import HistoryCase, session_lines  # noqa: E402

CRON = {"type": "cron", "expr": "0 9 * * 1-5", "tz": "UTC"}


class BotCase(HistoryCase):
    """A temp home with `~/Projects/proj`, two accounts (alpha, plain) and a bot store the server and the test share."""

    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.home, "Projects", "proj"))
        self.env = self.app.bots.env
        self.store = S.Store(env=self.env)

    # -- fixtures
    def bot(self, name="digest", kind="task", run=None, **extra):
        spec = {"name": name, "kind": kind, "when": dict(CRON), "task": {"prompt": "Summarize my mail"},
                "run": dict({"cwd": self.work}, **(run or {})), **extra}
        rec = self.store.add(spec, now=NOW)
        S.init_state_dir(rec, NOW, self.env)
        return rec

    def run_record(self, rec, n, status="ok", **kw):
        row = {"run": n, "schedule": rec["id"], "trigger": "cron", "status": status, "started": NOW - 700 - n,
               "ended": NOW - 600 - n, "summary": f"result {n}"}
        row.update(kw)
        return S.append_run(S.state_dir(rec["id"], self.env), row)

    def beat(self, age=5):
        S.write_summary(S.summary_path(self.env), {"ts": NOW, "scheduler": {"heartbeat": NOW - age}, "counts": {}, "items": []})

    def commands(self):
        cdir = S.command_dir(self.env)
        return S.read_commands(cdir) if os.path.isdir(cdir) else []

    def state(self, rec):
        return S.load_state(S.state_dir(rec["id"], self.env))

    def body(self, **kw):
        body = {"name": "digest", "prompt": "Summarize my unread mail", "when": "weekdays 9am", "dir": "~/Projects/proj",
                "max_time_s": 1800, "max_cost_usd": 2}
        body.update(kw)
        return body

    def post(self, path, body=None, **kw):
        return self.call("POST", path, {} if body is None else body, **kw)

    def create(self, **kw):
        return self.post("/api/bots", self.body(**kw))

    def readonly_app(self):
        return self.make_quit_app(readonly=True)


class TestBotList(BotCase):
    def test_no_bots_is_an_empty_list_and_the_agent_list_says_nothing_about_bots(self):
        r, data = self.call("GET", "/api/bots")
        self.assertEqual((r.status, data["bots"], data["counts"]["total"], data["can"]), (200, [], 0, {"create": True}))
        self.assertNotIn("bots", self.call("GET", "/api/agents")[1])

    def test_a_bot_is_listed_with_what_a_card_shows(self):
        rec = self.bot()
        self.run_record(rec, 1, "ok", summary="all good", level="important", headline="all good today")
        _, data = self.call("GET", "/api/bots")
        (item,) = data["bots"]
        self.assertEqual((item["id"], item["name"], item["kind"], item["enabled"]), (rec["id"], "digest", "task", True))
        self.assertEqual((item["when_text"], item["state"], item["running"], item["unseen"]), ("weekdays 09:00 (UTC)", "done", False, 1))
        self.assertEqual((item["last"]["status"], item["last"]["summary"], item["last"]["seen"]), ("ok", "all good", False))
        self.assertEqual((item["permissions"], item["mode"], item["dir"], item["notify"], item["origin"]), ("edit", "headless", "~/work", True, "cli"))
        self.assertIsInstance(item["next_at"], int)
        self.assertEqual(item["can"], {"run": True, "skip": True, "pause": True, "resume": False, "cancel": False, "edit": True,
                                       "delete": True, "ack": True})
        self.assertNotIn(self.home, json.dumps(data), "directories are shown as ~/...")

    def test_states_follow_the_scheduler_summary_and_count_unseen_runs(self):
        a, b, c, d = self.bot("a-bot"), self.bot("b-bot"), self.bot("c-bot"), self.bot("d-bot")
        self.run_record(a, 1, "failed", error="boom", level="important", headline="a failed: boom")
        self.run_record(b, 1, "ok", level="important", headline="b has news")
        S.update_state(S.state_dir(c["id"], self.env), lambda s: s.update(running={"run": 1, "pid": 1, "started": NOW - 30}))
        self.store.change(d["id"], {"enabled": False}, now=NOW)
        _, data = self.call("GET", "/api/bots")
        states = {i["name"]: (i["state"], i["attention"], i["running"], i["unseen"]) for i in data["bots"]}
        self.assertEqual(states, {"a-bot": ("attention", True, False, 1), "b-bot": ("done", False, False, 1),
                                  "c-bot": ("working", False, True, 0), "d-bot": ("idle", False, False, 0)})
        self.assertEqual(data["counts"], {"total": 4, "enabled": 3, "running": 1, "attention": 1, "unseen": 2})
        self.assertEqual(data["bots"][0]["name"], "a-bot", "the scheduler's order: attention first")
        running = next(i for i in data["bots"] if i["running"])
        self.assertEqual((running["running_since"], running["can"]["run"], running["can"]["delete"], running["can"]["cancel"]),
                         (NOW - 30, False, False, False))

    def test_a_paused_bot_by_the_breaker_needs_a_look_and_can_resume(self):
        rec = self.bot()
        S.update_state(S.state_dir(rec["id"], self.env), lambda s: s.update(paused_reason="5 failures in a row"))
        (item,) = self.call("GET", "/api/bots")[1]["bots"]
        self.assertEqual((item["state"], item["paused_reason"], item["can"]["resume"]), ("attention", "5 failures in a row", True))

    def test_scheduler_health_comes_from_the_heartbeat(self):
        self.bot()
        sch = self.call("GET", "/api/bots")[1]["scheduler"]
        self.assertEqual((sch["alive"], sch["heartbeat"], sch["age_s"]), (False, None, None), "no summary file: not running")
        self.beat(5)
        sch = self.call("GET", "/api/bots")[1]["scheduler"]
        self.assertEqual((sch["alive"], sch["heartbeat"], sch["age_s"]), (True, NOW - 5, 5))
        self.beat(S.HEARTBEAT_MAX_AGE_S + 1)
        self.assertFalse(self.call("GET", "/api/bots")[1]["scheduler"]["alive"], "a stale heartbeat reads as down")
        S.write_summary(S.summary_path(self.env), {"scheduler": {"heartbeat": NOW + 0.6}})
        self.assertTrue(self.call("GET", "/api/bots")[1]["scheduler"]["alive"], "a heartbeat a fraction ahead of a whole-second clock is fresh")
        S.write_summary(S.summary_path(self.env), {"scheduler": {"heartbeat": NOW + 60}})
        self.assertFalse(self.call("GET", "/api/bots")[1]["scheduler"]["alive"], "one far in the future is not")
        S.write_summary(S.summary_path(self.env), {"scheduler": {"heartbeat": True}})
        self.assertFalse(self.call("GET", "/api/bots")[1]["scheduler"]["alive"])

    def test_etag_and_the_long_poll_wake_on_a_change_but_not_on_the_heartbeat(self):
        rec = self.bot()
        self.beat(5)
        r, data = self.call("GET", "/api/bots")
        etag = r.headers["ETag"]
        self.assertEqual(etag, f'"{data["rev"]}"')
        self.assertEqual(self.call("GET", "/api/bots", headers={"if-none-match": etag})[0].status, 304)
        self.beat(1)
        self.assertEqual(self.call("GET", "/api/bots", headers={"if-none-match": etag})[0].status, 304, "a newer heartbeat is no change")
        self.slept.clear()
        self.on_sleep = lambda n: self.run_record(rec, 1, "failed") if n == 2 else None
        r, data = self.call("GET", "/api/bots?wait=20", headers={"if-none-match": etag})
        self.assertEqual((r.status, data["bots"][0]["state"]), (200, "attention"))
        self.assertEqual(len(self.slept), 2, "held until the run was recorded, then answered at once")

    def test_the_agent_list_carries_the_counts_for_the_menu_dot_and_wakes_on_them(self):
        rec = self.bot()
        r, data = self.call("GET", "/api/agents")
        self.assertEqual(data["bots"], {"attention": 0, "unseen": 0, "running": 0, "alive": False})
        etag = r.headers["ETag"]
        self.run_record(rec, 1, "failed")
        r2, data2 = self.call("GET", "/api/agents", headers={"if-none-match": etag})
        self.assertEqual((r2.status, data2["bots"]["attention"], data2["bots"]["unseen"]), (200, 1, 1))
        self.assertNotEqual(data2["rev"], data["rev"])

    def test_read_only_lists_but_nothing_can_be_done(self):
        rec = self.bot()
        self.run_record(rec, 1)
        _, data = self.call("GET", "/api/bots", app=self.readonly_app())
        self.assertEqual(set(data["bots"][0]["can"].values()), {False})
        self.assertFalse(data["can"]["create"])

    def test_auth_methods_and_a_corrupt_store(self):
        self.assertEqual(self.call("GET", "/api/bots", token=None)[0].status, 401)
        self.assertEqual(self.call("DELETE", "/api/bots")[0].status, 405)
        self.assertEqual(self.call("POST", "/api/bots/abc-123/runs", {})[0].status, 405)
        self.assertEqual(self.call("GET", "/api/bots/abc-123/run")[0].status, 405)
        self.assertEqual(self.call("GET", "/api/bots/NOT_AN_ID")[0].status, 404)
        os.makedirs(os.path.dirname(self.store.path), exist_ok=True)
        with open(self.store.path, "w") as f:
            f.write("{not json")
        os.chmod(self.store.path, 0o600)
        r, data = self.call("GET", "/api/bots")
        self.assertEqual((r.status, data["error"]), (500, "internal"))
        self.assertNotIn(self.home, data["message"])
        self.assertEqual(self.call("GET", "/api/agents")[0].status, 200, "a bad schedules file never breaks the agent list")

    def test_text_from_a_run_is_cleaned(self):
        rec = self.bot("evil \u202ename")
        self.run_record(rec, 1, "ok", summary="\x1b[31mred\x1b[0m \u202etxt <img src=x onerror=1>")
        (item,) = self.call("GET", "/api/bots")[1]["bots"]
        for text in (item["name"], item["last"]["summary"]):
            self.assertNotIn("\x1b", text)
            self.assertNotIn("\u202e", text)
        self.assertEqual(item["name"], "evil name")


class TestBotDetail(BotCase):
    def test_detail_has_the_definition_the_memory_of_an_agent_and_the_last_runs(self):
        rec = self.bot("watcher", "agent", identity={"system": "You watch the build."})
        for n in range(1, 8):
            self.run_record(rec, n, "ok")
        with open(S.memory_path(rec["id"], self.env), "w") as f:
            f.write("build was red on Tuesday\n")
        r, data = self.call("GET", f"/api/bots/{rec['id']}")
        self.assertEqual(r.status, 200)
        d = data["definition"]
        self.assertEqual((d["name"], d["kind"], d["prompt"], d["identity"], d["memory"]),
                         ("watcher", "agent", "Summarize my mail", "You watch the build.", True))
        self.assertEqual((d["permissions"], d["max_time_s"], d["max_cost_usd"], d["dir"]), ("edit", 1800, 2.0, "~/work"))
        self.assertEqual(data["memory"]["text"], "build was red on Tuesday\n")
        self.assertEqual((data["memory"]["truncated"], data["memory"]["max_bytes"]), (False, 8192))
        self.assertEqual([x["run"] for x in data["runs"]], [7, 6, 5, 4, 3], "the five newest, newest first")
        self.assertEqual(data["total_runs"], 7)
        self.assertEqual(data["bot"]["id"], rec["id"])
        self.assertNotIn(self.home, json.dumps(data))

    def test_a_task_has_no_memory_and_an_unknown_bot_is_404(self):
        rec = self.bot()
        self.assertIsNone(self.call("GET", f"/api/bots/{rec['id']}")[1]["memory"])
        r, data = self.call("GET", "/api/bots/nope-abc")
        self.assertEqual((r.status, data["error"]), (404, "not_found"))

    def test_a_long_or_dirty_memory_is_cut_and_cleaned_and_a_linked_one_is_not_read(self):
        rec = self.bot("agent-one", "agent")
        path = S.memory_path(rec["id"], self.env)
        with open(path, "w") as f:
            f.write("\x1b[31mred\x1b[0m\n" + "x" * 60000)
        mem = self.call("GET", f"/api/bots/{rec['id']}")[1]["memory"]
        self.assertTrue(mem["truncated"])
        self.assertLessEqual(len(mem["text"]), B.MEMORY_VIEW)
        self.assertNotIn("\x1b", mem["text"])
        os.unlink(path)
        secret = os.path.join(self.tmp.name, "secret.txt")
        with open(secret, "w") as f:
            f.write("do not show")
        os.symlink(secret, path)
        self.assertEqual(self.call("GET", f"/api/bots/{rec['id']}")[1]["memory"]["text"], "", "a symbolic link is not followed")


class TestBotRuns(BotCase):
    def test_runs_come_newest_first_in_pages(self):
        rec = self.bot()
        for n in range(1, 6):
            self.run_record(rec, n, "ok" if n % 2 else "failed", cost_usd=0.5 * n, tokens={"input": 10, "output": 5}, error="x" if n % 2 == 0 else None)
        data = self.call("GET", f"/api/bots/{rec['id']}/runs?limit=2&offset=0")[1]
        self.assertEqual(([r["run"] for r in data["runs"]], data["more"], data["total"]), ([5, 4], True, 5))
        first = data["runs"][0]
        self.assertEqual((first["status"], first["cost_usd"], first["tokens"], first["duration"], first["trigger"]), ("ok", 2.5, 15, 100, "cron"))
        last = self.call("GET", f"/api/bots/{rec['id']}/runs?limit=2&offset=4")[1]
        self.assertEqual(([r["run"] for r in last["runs"]], last["more"]), ([1], False))
        self.assertEqual(self.call("GET", f"/api/bots/{rec['id']}/runs?limit=abc")[0].status, 400)
        self.assertEqual(len(self.call("GET", f"/api/bots/{rec['id']}/runs?limit=5000")[1]["runs"]), 5, "limits are clamped")

    def test_a_run_has_its_summary_and_report_and_fetching_it_marks_it_seen(self):
        rec = self.bot()
        self.run_record(rec, 1, "ok", summary="**done** in 3 steps")
        self.run_record(rec, 2, "ok")
        with open(S.report_path(rec["id"], 1, self.env), "w") as f:
            f.write("# Report\n\n- one\n- two\x1b[31m\n")
        r, data = self.call("GET", f"/api/bots/{rec['id']}/runs/1")
        self.assertEqual(r.status, 200)
        self.assertEqual((data["run"]["summary"], data["was_unseen"], data["run"]["seen"], data["run"]["report"]), ("**done** in 3 steps", True, True, True))
        self.assertEqual(data["run"]["report_text"], {"text": "# Report\n\n- one\n- two\n", "truncated": False})
        runs = {r["run"]: r["seen"] for r in S.read_runs(S.state_dir(rec["id"], self.env))}
        self.assertEqual(runs, {1: True, 2: False}, "only that run was marked")
        self.assertFalse(self.call("GET", f"/api/bots/{rec['id']}/runs/1")[1]["was_unseen"])
        self.assertIsNone(self.call("GET", f"/api/bots/{rec['id']}/runs/2")[1]["run"]["report_text"], "no report file")
        r, data = self.call("GET", f"/api/bots/{rec['id']}/runs/99")
        self.assertEqual((r.status, data["error"]), (404, "not_found"))

    def test_a_read_only_server_never_marks_anything_seen(self):
        rec = self.bot()
        self.run_record(rec, 1, "ok")
        data = self.call("GET", f"/api/bots/{rec['id']}/runs/1", app=self.readonly_app())[1]
        self.assertEqual((data["was_unseen"], data["run"]["seen"]), (True, False))
        self.assertEqual(len(S.unseen(S.state_dir(rec["id"], self.env))), 1)

    def test_whether_a_run_has_a_session_needs_a_session_file_of_ours(self):
        rec = self.bot()
        good = self.make_session(session_lines(self.work, "hello"), "r.jsonl", profile="alpha")
        outside = os.path.join(self.tmp.name, "elsewhere.jsonl")
        with open(outside, "w") as f:
            f.write(session_lines(self.work, "secret"))
        self.run_record(rec, 1, session_file=good)
        self.run_record(rec, 2, session_file=outside)
        self.run_record(rec, 3)
        runs = {r["run"]: r["session"] for r in self.call("GET", f"/api/bots/{rec['id']}/runs")[1]["runs"]}
        self.assertEqual(runs, {1: True, 2: False, 3: False})
        self.assertNotIn(good, json.dumps(self.call("GET", f"/api/bots/{rec['id']}/runs")[1]), "the phone never sees a session path")


class TestBotParse(BotCase):
    def test_a_when_is_read_with_the_next_three_times(self):
        r, data = self.post("/api/bots/parse", {"text": "weekdays 9am"})
        self.assertEqual(r.status, 200)
        self.assertEqual((data["describe"], data["error"], data["when"]["type"]), ("weekdays 09:00", None, "cron"))
        self.assertEqual(len(data["next"]), 3)
        self.assertEqual(data["next"], sorted(data["next"]))
        self.assertGreater(data["next"][0], NOW)
        data = self.post("/api/bots/parse", {"text": "in 2h"})[1]
        self.assertEqual((data["when"]["type"], len(data["next"])), ("once", 1))

    def test_a_text_that_cannot_be_read_is_an_error_message_not_an_http_error(self):
        for text in ("garbage", "every 4m", "*/2 * * * *", "", "x" * 300):
            r, data = self.post("/api/bots/parse", {"text": text})
            self.assertEqual((r.status, data["when"], data["next"]), (200, None, []), text[:20])
            self.assertTrue(data["error"], text[:20])
        self.assertIn("5 minutes", self.post("/api/bots/parse", {"text": "every 4m"})[1]["error"])

    def test_the_body_is_checked(self):
        for body in ({}, {"text": 5}, {"text": "daily", "extra": 1}, {"text": None}):
            self.assertEqual(self.post("/api/bots/parse", body)[0].status, 400, body)
        self.assertEqual(self.post("/api/bots/parse", {"text": "daily"}, token=None)[0].status, 401)
        self.assertEqual(self.call("GET", "/api/bots/parse")[0].status, 405, "a POST route")
        r, data = self.post("/api/bots/parse", {"text": "daily"}, app=self.readonly_app())
        self.assertEqual((r.status, data["error"]), (403, "readonly"))

    def test_the_zone_comes_from_the_schedule_settings(self):
        with open(self.settings, "w") as f:
            json.dump({"accounts": [], "schedule": {"tz": "Europe/Berlin"}}, f)
        data = self.post("/api/bots/parse", {"text": "daily 9am"})[1]
        self.assertEqual(data["when"]["tz"], "Europe/Berlin")


class TestBotCreate(BotCase):
    def test_a_bot_made_on_the_phone_is_phone_origin_with_its_caps_and_a_state_directory(self):
        r, data = self.create(notify=True, mode="visible", permissions="readonly", account="alpha")
        self.assertEqual((r.status, data["ok"]), (200, True))
        self.assertEqual(len(data["next"]), 3)
        rec = self.store.get(data["id"])
        self.assertEqual((rec["origin"], rec["kind"], rec["name"], rec["enabled"]), ("phone", "task", "digest", True))
        self.assertEqual((rec["run"]["cwd"], rec["run"]["account"], rec["run"]["mode"], rec["run"]["permissions"]),
                         (os.path.join(self.home, "Projects", "proj"), "alpha", "visible", "readonly"))
        self.assertEqual((rec["run"]["max_time_s"], rec["run"]["max_cost_usd"]), (1800, 2.0))
        self.assertEqual((rec["when"]["type"], rec["task"]["prompt"]), ("cron", "Summarize my unread mail"))
        self.assertEqual(rec["notify"]["on"], ["failed", "limit", "missed", "done"])
        self.assertTrue(os.path.isdir(S.state_dir(rec["id"], self.env)))
        self.assertEqual(self.logs[-1], f"write bot-create label={rec['id']} bytes={len('Summarize my unread mail')}", "no prompt text in the log")

    def test_defaults_are_edit_headless_and_every_result(self):
        rec = self.store.get(self.create()[1]["id"])
        self.assertEqual((rec["run"]["permissions"], rec["run"]["mode"], rec["run"]["account"]), ("edit", "headless", None))
        self.assertEqual(rec["notify"]["on"], ["failed", "limit", "missed", "done"])
        rec = self.store.get(self.create(notify=False, name="quiet")[1]["id"])
        self.assertEqual((rec["notify"]["on"], rec["notify"]["done"]), ([], False))

    def test_an_agent_has_memory_and_an_identity(self):
        data = self.create(kind="agent", name="watcher", identity="You watch the build.")[1]
        rec = self.store.get(data["id"])
        self.assertEqual((rec["kind"], rec["identity"]["memory"], rec["identity"]["system"]), ("agent", True, "You watch the build."))
        self.assertTrue(os.path.isfile(S.memory_path(rec["id"], self.env)))
        with open(os.path.join(S.state_dir(rec["id"], self.env), "identity.md")) as f:
            self.assertIn("You watch the build.", f.read())

    def test_only_whitelisted_fields_are_taken_and_origin_is_always_phone(self):
        for extra in ({"origin": "cli"}, {"id": "mine-abc"}, {"run": {"permissions": "full"}}, {"policy": {"retries": 99}},
                      {"enabled": False}, {"created": 5}, {"task": {"prompt": "x"}}, {"cwd": "/etc"}, {"add_dirs": ["/"]},
                      {"model": "x"}, {"thinking": "max"}, {"identity": {"system": "x"}, "x": 1}):
            r, data = self.create(**extra)
            self.assertEqual((r.status, data["error"]), (400, "bad_request"), extra)
        self.assertEqual(self.store.read(), [])

    def test_a_phone_can_never_make_a_full_permission_bot(self):
        for perms in ("full", "FULL", "admin", None, 1, "", ["edit"]):
            r, data = self.create(permissions=perms)
            self.assertEqual((r.status, data["error"]), (400, "bad_request"), perms)
        self.assertIn("laptop", self.create(permissions="full")[1]["message"])
        self.assertEqual(self.store.read(), [])

    def test_a_time_and_a_cost_cap_are_mandatory_and_bounded(self):
        for field in ("max_time_s", "max_cost_usd", "name", "prompt", "when", "dir"):
            body = self.body()
            del body[field]
            r, data = self.post("/api/bots", body)
            self.assertEqual((r.status, data["error"]), (400, "bad_request"), field)
            self.assertIn(field, data["message"])
        bad = [("max_time_s", v) for v in (0, 59, B.PHONE_MAX_TIME_S + 1, 86400, -5, True, "1800", None, 1800.5, float("nan"))]
        bad += [("max_cost_usd", v) for v in (0, -1, 0.001, B.PHONE_MAX_COST_USD + 0.01, 100, True, "2", None, float("inf"))]
        for field, value in bad:
            self.assertEqual(self.create(**{field: value})[0].status, 400, (field, value))
        self.assertEqual(self.store.read(), [])
        r, _ = self.create(max_time_s=B.PHONE_MAX_TIME_S, max_cost_usd=B.PHONE_MAX_COST_USD)
        self.assertEqual(r.status, 200, "the limits themselves are fine")

    def test_directories_must_be_offered_or_below_the_home_directory(self):
        os.makedirs(os.path.join(self.home, "work", "client"))
        os.makedirs(os.path.join(self.home, ".ssh", "x"))
        os.symlink("/etc", os.path.join(self.home, "etclink"))
        for shown in ("~", "~/Projects/proj", "~/work/client", os.path.join(self.home, "work")):
            self.assertEqual(self.create(dir=shown)[0].status, 200, shown)
            self.clock.t += 10
        for shown in ("/etc", "~/.ssh", "~/.ssh/x", "~/missing", "~/etclink", "relative", "~/../etc", "/", 5, None, ["~"], "x" * 400):
            r, data = self.create(dir=shown)
            self.assertEqual((r.status, data["error"]), (400, "bad_request"), shown)
            self.clock.t += 10
        self.assertEqual(len(self.store.read()), 4)

    def test_the_directory_of_a_live_agent_is_offered(self):
        live = os.path.join(self.home, "elsewhere")
        os.makedirs(live)
        self.write_state(3, cwd=live)
        self.assertEqual(self.create(dir="~/elsewhere")[0].status, 200)

    def test_an_account_must_be_an_offered_one_or_automatic(self):
        self.assertEqual(self.create(account="alpha")[0].status, 200)
        self.assertEqual(self.create(account=None, name="auto")[0].status, 200)
        for acct in ("nope", "", "alpha; rm -rf /", 5, ["alpha"]):
            self.assertEqual(self.create(account=acct)[0].status, 400, acct)

    def test_the_task_the_name_the_modes_and_the_when_are_checked(self):
        too_long = "x" * (R.MAX_PROMPT + 1)
        for field, value in (("prompt", too_long), ("prompt", ""), ("prompt", "  "), ("prompt", "a\x1bb"), ("prompt", 5),
                             ("name", ""), ("name", "n" * 81), ("name", "a\nb"), ("name", "a\x00b"), ("name", 5),
                             ("mode", "hybrid"), ("mode", "invisible"), ("notify", "yes"), ("notify", 1), ("kind", "robot"),
                             ("when", "garbage"), ("when", "every 4m"), ("when", "*/2 * * * *"), ("when", "1970-01-02 10:00"),
                             ("when", 5), ("when", ""), ("identity", "x" * 2001), ("identity", 5)):
            r, data = self.create(**{field: value})
            self.assertEqual((r.status, data["error"]), (400, "bad_request"), (field, value))
        self.assertEqual(self.store.read(), [])
        self.assertEqual(self.create(prompt="x" * R.MAX_PROMPT)[0].status, 200, "the limit itself is fine")

    def test_a_phone_cannot_repeat_more_often_than_every_five_minutes(self):
        self.assertEqual(B.MIN_INTERVAL_S, 300)
        for text in ("every 1m", "every 4m", "every 90s", "*/2 * * * *", "* * * * *"):
            self.assertEqual(self.create(when=text)[0].status, 400, text)
        self.assertEqual(self.create(when="every 5m")[0].status, 200)

    def test_at_most_fifty_bots(self):
        for i in range(B.MAX_BOTS):
            self.bot(f"bot {i}")
        r, data = self.create()
        self.assertEqual((r.status, data["error"]), (409, "conflict"))
        self.assertIn(str(B.MAX_BOTS), data["message"])
        self.assertEqual(len(self.store.read()), B.MAX_BOTS)
        self.assertFalse(self.call("GET", "/api/bots")[1]["can"]["create"])
        self.store.remove(self.store.read()[0]["id"])
        self.clock.t += 60
        self.assertEqual(self.create()[0].status, 200, "one fewer: one more")

    def test_creating_has_its_own_slow_bucket(self):
        self.tick = 0
        statuses = [self.create(name=f"b{i}")[0].status for i in range(R.BOT_BURST + 1)]
        self.assertEqual(statuses[-1], 429)
        self.assertNotIn(429, statuses[:-1])
        r, data = self.create()
        self.assertEqual((r.status, data["error"]), (429, "rate_limited"))
        self.assertGreater(int(r.headers["Retry-After"]), 0)
        self.assertEqual(len(self.store.read()), R.BOT_BURST)
        self.clock.t += R.BOT_EVERY
        self.assertEqual(self.create()[0].status, 200, "one more after the wait")

    def test_a_live_scheduler_is_told_to_reload_a_dead_one_is_not(self):
        self.create()
        self.assertEqual(self.commands(), [])
        self.beat()
        self.create(name="two")
        self.assertEqual(self.commands(), [{"op": "reload"}])

    def test_read_only_origin_auth_and_content_checks(self):
        r, data = self.create(app=self.readonly_app()) if False else self.post("/api/bots", self.body(), app=self.readonly_app())
        self.assertEqual((r.status, data["error"]), (403, "readonly"))
        self.assertEqual(self.post("/api/bots", self.body(), token=None)[0].status, 401)
        self.assertEqual(self.post("/api/bots", self.body(), headers={"origin": "http://evil.example"})[0].status, 403)
        self.assertEqual(self.post("/api/bots", self.body(), headers={"x-hivegrid-remote": None})[0].status, 403)
        self.assertEqual(self.post("/api/bots", self.body(), headers={"content-type": "text/plain"})[0].status, 415)
        self.assertEqual(self.post("/api/bots", [1])[0].status, 400)
        self.assertEqual(self.store.read(), [])


class TestBotEdit(BotCase):
    def edit(self, rec, **body):
        return self.post(f"/api/bots/{rec['id']}", body)

    def test_fields_change_and_the_rest_stays(self):
        rec = self.bot()
        r, data = self.edit(rec, name="renamed", prompt="Do something else", when="daily 8am", dir="~/Projects/proj", account="alpha",
                            mode="visible", permissions="readonly", max_time_s=900, max_cost_usd=1.5, notify=False)
        self.assertEqual((r.status, data["ok"], data["id"], len(data["next"])), (200, True, rec["id"], 3))
        new = self.store.get(rec["id"])
        self.assertEqual((new["name"], new["task"]["prompt"], new["when"]["expr"]), ("renamed", "Do something else", "0 8 * * *"))
        self.assertEqual((new["run"]["cwd"], new["run"]["account"], new["run"]["mode"], new["run"]["permissions"]),
                         (os.path.join(self.home, "Projects", "proj"), "alpha", "visible", "readonly"))
        self.assertEqual((new["run"]["max_time_s"], new["run"]["max_cost_usd"], new["notify"]["on"]), (900, 1.5, []))
        self.assertEqual((new["origin"], new["kind"], new["policy"]), (rec["origin"], "task", rec["policy"]))
        self.assertGreaterEqual(new["updated"], rec["updated"])
        self.assertEqual(self.logs[-1], f"write bot-edit label={rec['id']} bytes={len('Do something else')}")

    def test_one_field_alone_is_fine_and_an_agent_identity_is_rewritten(self):
        rec = self.bot("agent-x", "agent")
        self.assertEqual(self.edit(rec, identity="Be brief.")[0].status, 200)
        with open(os.path.join(S.state_dir(rec["id"], self.env), "identity.md")) as f:
            self.assertIn("Be brief.", f.read())
        self.assertEqual(self.edit(rec, name="new name")[0].status, 200)
        self.assertEqual(self.store.get(rec["id"])["identity"]["system"], "Be brief.")

    def test_the_same_restrictions_apply_as_on_create(self):
        rec = self.bot()
        for body in ({"permissions": "full"}, {"max_time_s": 99999}, {"max_cost_usd": 50}, {"max_cost_usd": 0}, {"dir": "/etc"},
                     {"account": "nope"}, {"when": "every 1m"}, {"when": "garbage"}, {"prompt": ""}, {"name": ""}, {"mode": "hybrid"},
                     {"notify": 1}):
            r, data = self.edit(rec, **body)
            self.assertEqual((r.status, data["error"]), (400, "bad_request"), body)
        self.assertEqual(self.store.get(rec["id"])["run"]["permissions"], "edit")
        self.assertEqual(self.store.get(rec["id"])["run"]["max_cost_usd"], 2.0)

    def test_unknown_fields_kind_origin_and_an_empty_body_are_refused(self):
        rec = self.bot()
        for body in ({}, {"kind": "agent"}, {"origin": "phone"}, {"id": "other-bot"}, {"enabled": False}, {"policy": {"retries": 0}},
                     {"name": "x", "run": {"permissions": "full"}}):
            self.assertEqual(self.edit(rec, **body)[0].status, 400, body)
        self.assertEqual(self.store.get(rec["id"])["kind"], "task")

    def test_a_full_permission_bot_of_the_laptop_can_only_be_lowered_from_the_phone(self):
        rec = self.bot("laptop-bot", run={"permissions": "full", "max_time_s": 600, "max_cost_usd": 3})
        item = self.call("GET", "/api/bots")[1]["bots"][0]
        self.assertEqual((item["permissions"], item["can"]["edit"]), ("full", False))
        for body in ({"name": "x"}, {"permissions": "full"}, {"max_cost_usd": 1}):
            r, data = self.edit(rec, **body)
            self.assertEqual((r.status, data["error"]), (400, "bad_request"), body)
        self.assertEqual(self.store.get(rec["id"])["name"], "laptop-bot")
        self.assertEqual(self.edit(rec, permissions="edit")[0].status, 200, "lowering is always allowed")
        self.assertEqual(self.store.get(rec["id"])["run"]["permissions"], "edit")
        self.assertEqual(self.edit(rec, name="now editable")[0].status, 200)

    def test_unknown_bot_read_only_and_the_bucket(self):
        r, data = self.post("/api/bots/nope-abc", {"name": "x"})
        self.assertEqual((r.status, data["error"]), (404, "not_found"))
        rec = self.bot()
        r, data = self.post(f"/api/bots/{rec['id']}", {"name": "x"}, app=self.readonly_app())
        self.assertEqual((r.status, data["error"]), (403, "readonly"))
        self.tick = 0
        statuses = [self.edit(rec, name=f"n{i}")[0].status for i in range(R.BOT_BURST + 1)]
        self.assertEqual(statuses[-1], 429)


class TestBotActions(BotCase):
    def do(self, rec, verb, body=None, **kw):
        return self.post(f"/api/bots/{rec['id']}/{verb}", body, **kw)

    def test_run_now_goes_to_a_live_scheduler_as_a_command(self):
        rec = self.bot()
        self.beat()
        r, data = self.do(rec, "run")
        self.assertEqual((r.status, data), (200, {"ok": True, "queued": False}))
        self.assertEqual(self.commands(), [{"op": "run-now", "id": rec["id"]}])
        self.assertFalse(self.state(rec)["run_now"])
        self.assertEqual(self.logs[-1], f"write bot-run label={rec['id']} bytes=0")

    def test_run_now_without_a_scheduler_waits_in_the_state_file(self):
        rec = self.bot()
        r, data = self.do(rec, "run")
        self.assertEqual((r.status, data), (200, {"ok": True, "queued": True}))
        self.assertTrue(self.state(rec)["run_now"])
        self.assertEqual(self.commands(), [])

    def test_a_running_bot_is_not_started_again_and_runs_have_their_own_bucket(self):
        rec = self.bot()
        S.update_state(S.state_dir(rec["id"], self.env), lambda s: s.update(running={"run": 1, "pid": 1, "started": NOW}))
        r, data = self.do(rec, "run")
        self.assertEqual((r.status, data["error"]), (409, "conflict"))
        S.update_state(S.state_dir(rec["id"], self.env), lambda s: s.update(running=None))
        self.tick = 0
        statuses = [self.do(rec, "run")[0].status for _ in range(R.BOT_BURST + 1)]
        self.assertEqual(statuses, [200] * R.BOT_BURST + [429])

    def test_skip_next_goes_to_the_scheduler_or_the_state(self):
        rec = self.bot()
        self.assertEqual(self.do(rec, "skip")[1], {"ok": True, "queued": True})
        self.assertIsNotNone(self.state(rec)["last_slot"], "no scheduler: the slot is marked handled")
        self.beat()
        self.assertEqual(self.do(rec, "skip")[1], {"ok": True, "queued": False})
        self.assertEqual(self.commands(), [{"op": "skip", "id": rec["id"]}])
        self.store.change(rec["id"], {"enabled": False}, now=NOW)
        self.assertEqual(self.do(rec, "skip")[0].status, 409)

    def test_cancel_needs_a_live_run_and_a_live_scheduler(self):
        rec = self.bot()
        self.beat()
        self.assertEqual(self.do(rec, "cancel")[0].status, 409, "nothing is running")
        S.update_state(S.state_dir(rec["id"], self.env), lambda s: s.update(running={"run": 1, "pid": 1, "started": NOW}))
        self.assertEqual(self.do(rec, "cancel")[1], {"ok": True})
        self.assertEqual(self.commands(), [{"op": "cancel", "id": rec["id"]}])
        os.unlink(S.summary_path(self.env))
        r, data = self.do(rec, "cancel")
        self.assertEqual((r.status, data["error"]), (409, "conflict"))

    def test_pause_and_resume_change_the_definition_and_a_resume_clears_the_breaker(self):
        rec = self.bot()
        self.beat()
        self.assertEqual(self.do(rec, "pause")[1], {"ok": True})
        self.assertFalse(self.store.get(rec["id"])["enabled"])
        self.assertEqual(self.commands(), [{"op": "reload"}])
        S.update_state(S.state_dir(rec["id"], self.env), lambda s: s.update(paused_reason="5 failures", consec_failures=5, done=True))
        self.assertEqual(self.do(rec, "resume")[1], {"ok": True})
        self.assertTrue(self.store.get(rec["id"])["enabled"])
        st = self.state(rec)
        self.assertEqual((st["paused_reason"], st["consec_failures"], st["done"], st["baseline"]), (None, 0, False, int(NOW)))

    def test_pausing_works_on_a_laptop_bot_with_full_permissions(self):
        rec = self.bot("laptop-bot", run={"permissions": "full", "max_time_s": 600, "max_cost_usd": 3})
        self.assertEqual(self.do(rec, "pause")[0].status, 200)
        self.assertEqual(self.do(rec, "resume")[0].status, 200)

    def test_ack_marks_every_run_seen(self):
        rec = self.bot()
        self.run_record(rec, 1)
        self.run_record(rec, 2, "failed")
        self.assertEqual(self.do(rec, "ack")[1], {"ok": True, "marked": 2})
        self.assertEqual(self.call("GET", "/api/bots")[1]["counts"]["unseen"], 0)

    def test_delete_needs_confirmation_and_takes_the_state_with_it(self):
        rec = self.bot("agent-x", "agent")
        self.run_record(rec, 1)
        for body in ({}, {"confirm": False}):
            r, data = self.do(rec, "delete", body)
            self.assertEqual((r.status, data["error"]), (409, "confirm_required"), body)
        self.assertEqual(self.do(rec, "delete", {"confirm": "yes"})[0].status, 400)
        self.assertEqual(self.do(rec, "delete", {"confirm": True, "x": 1})[0].status, 400)
        self.assertIsNotNone(self.store.get(rec["id"]))
        self.beat()
        r, data = self.do(rec, "delete", {"confirm": True})
        self.assertEqual((r.status, data), (200, {"ok": True}))
        self.assertIsNone(self.store.get(rec["id"]))
        self.assertFalse(os.path.exists(S.state_dir(rec["id"], self.env)))
        self.assertEqual(self.commands(), [{"op": "reload"}])
        self.assertEqual(self.do(rec, "delete", {"confirm": True})[0].status, 404)

    def test_a_running_bot_is_not_deleted(self):
        rec = self.bot()
        S.update_state(S.state_dir(rec["id"], self.env), lambda s: s.update(running={"run": 1, "pid": 1, "started": NOW}))
        r, data = self.do(rec, "delete", {"confirm": True})
        self.assertEqual((r.status, data["error"]), (409, "conflict"))
        self.assertIsNotNone(self.store.get(rec["id"]))

    def test_unknown_bots_verbs_methods_and_bodies(self):
        rec = self.bot()
        self.assertEqual(self.do(rec, "nope")[0].status, 404)
        self.assertEqual(self.post("/api/bots/nope-abc/run")[0].status, 404)
        self.assertEqual(self.call("GET", f"/api/bots/{rec['id']}/pause")[0].status, 405)
        self.assertEqual(self.do(rec, "pause", {"x": 1})[0].status, 400)
        self.assertEqual(self.do(rec, "pause", token=None)[0].status, 401)
        self.assertTrue(self.store.get(rec["id"])["enabled"])

    def test_read_only_refuses_every_action(self):
        rec = self.bot()
        app = self.readonly_app()
        for verb, body in (("run", {}), ("skip", {}), ("cancel", {}), ("pause", {}), ("resume", {}), ("ack", {}), ("delete", {"confirm": True})):
            r, data = self.do(rec, verb, body, app=app)
            self.assertEqual((r.status, data["error"]), (403, "readonly"), verb)
        self.assertEqual((self.state(rec)["run_now"], self.store.get(rec["id"])["enabled"]), (False, True))


class TestBotBriefing(BotCase):
    """`GET /api/bots/briefing` and `POST /api/bots/briefing/seen`: the important reports of every bot, and nothing else."""

    def setUp(self):
        super().setUp()
        self.a = self.bot("ci watch", kind="agent")
        self.b = self.bot("mail")

    def test_only_important_reports_are_in_the_briefing_newest_first(self):
        self.run_record(self.a, 1, level="none", headline="CI green, nothing to do", seen=True)
        self.run_record(self.a, 2, level="info", headline="rotated the log")
        self.run_record(self.a, 3, level="important", headline="CI of repo X fixed: flaky test timeout, now green", seen=False, ended=NOW - 500)
        self.run_record(self.b, 1, status="failed", level="important", headline="mail failed: no network", seen=False, ended=NOW - 100)
        r, data = self.call("GET", "/api/bots/briefing")
        self.assertEqual(r.status, 200)
        self.assertEqual([(e["bot_id"], e["run"]) for e in data["entries"]], [(self.b["id"], 1), (self.a["id"], 3)])
        first = data["entries"][0]
        self.assertEqual((first["bot_name"], first["headline"], first["status"], first["seen"]), ("mail", "mail failed", "failed", False))
        self.assertEqual((data["unseen"], data["total"]), (2, 2))

    def test_the_headline_is_one_clean_line(self):
        self.run_record(self.a, 1, level="important", headline="fixed\x1b[31m it\nnow", seen=False)
        e = self.call("GET", "/api/bots/briefing")[1]["entries"][0]
        self.assertNotIn("\x1b", e["headline"])
        self.assertNotIn("\n", e["headline"])

    def test_marking_seen_clears_the_count_and_the_menu_dot_counts_the_briefing(self):
        self.beat()
        self.run_record(self.a, 1, level="important", headline="one", seen=False)
        self.run_record(self.b, 1, level="info", headline="quiet", seen=False)
        self.assertEqual(self.call("GET", "/api/agents")[1]["bots"]["unseen"], 1, "an info report is not a reason for the dot")
        r, data = self.post("/api/bots/briefing/seen", {})
        self.assertEqual((r.status, data["ok"]), (200, True))
        got = self.call("GET", "/api/bots/briefing")[1]
        self.assertEqual((got["unseen"], got["total"], got["entries"][0]["seen"]), (0, 1, True))
        self.assertEqual(self.call("GET", "/api/agents")[1]["bots"]["unseen"], 0)

    def test_the_list_says_how_many_reports_of_a_bot_wait_and_what_the_last_one_said(self):
        self.run_record(self.a, 1, level="important", headline="CI fixed", seen=False)
        self.run_record(self.a, 2, level="none", headline="all quiet", seen=True)
        item = next(i for i in self.call("GET", "/api/bots")[1]["bots"] if i["id"] == self.a["id"])
        self.assertEqual(item["unseen"], 1)
        self.assertEqual((item["last"]["level"], item["last"]["headline"]), ("none", "all quiet"))

    def test_the_long_poll_wakes_on_a_new_report_and_keeps_to_the_rev(self):
        r, first = self.call("GET", "/api/bots/briefing")
        again = self.call("GET", "/api/bots/briefing", headers={"if-none-match": r.headers["ETag"]})[0]
        self.assertEqual(again.status, 304)
        self.run_record(self.a, 1, level="important", headline="new", seen=False)
        after = self.call("GET", "/api/bots/briefing", headers={"if-none-match": r.headers["ETag"]})[0]
        self.assertEqual(after.status, 200)

    def test_seen_is_a_write_and_the_briefing_is_readable_read_only(self):
        self.run_record(self.a, 1, level="important", headline="one", seen=False)
        app = self.readonly_app()
        self.assertEqual(self.call("GET", "/api/bots/briefing", app=app)[0].status, 200)
        r, data = self.post("/api/bots/briefing/seen", {}, app=app)
        self.assertEqual((r.status, data["error"]), (403, "readonly"))
        self.assertEqual(self.call("GET", "/api/bots/briefing")[1]["unseen"], 1)


class TestBotTalk(BotCase):
    """`POST /api/bots/<id>/say` and `GET /api/bots/<id>/conversation`: the chat with a bot's main session."""

    def setUp(self):
        super().setUp()
        self.rec = self.bot("pal", kind="agent")
        self.session = self.make_session(session_lines(self.work, "hello bot"), "main.jsonl", profile="alpha")
        S.update_state(S.state_dir(self.rec["id"], self.env), lambda s: s.update(session_file=self.session))

    def say(self, text="hi", rec=None, **kw):
        return self.post(f"/api/bots/{(rec or self.rec)['id']}/say", {"text": text}, **kw)

    def conv(self, query="", **kw):
        return self.call("GET", f"/api/bots/{self.rec['id']}/conversation{query}", **kw)

    def test_a_message_goes_to_the_scheduler_as_a_chat_command(self):
        self.beat()
        r, data = self.say("what is on your mind?")
        self.assertEqual((r.status, data["ok"], isinstance(data["queued"], bool), isinstance(data["position"], int)), (200, True, True, True))
        self.assertEqual([(c["op"], c["id"], c["text"]) for c in self.commands()], [("chat", self.rec["id"], "what is on your mind?")])
        self.assertEqual(self.logs[-1], f"write bot-say label={self.rec['id']} bytes=21")
        self.assertNotIn("what is on your mind", " ".join(self.logs), "the text is not logged")

    def test_without_a_running_scheduler_it_says_so_and_nothing_is_left_in_the_inbox(self):
        r, data = self.say()
        self.assertEqual((r.status, data["error"]), (409, "conflict"))
        self.assertIn("scheduler is not running", data["message"])
        self.assertEqual(self.commands(), [])

    def test_the_text_is_bounded_and_clean_and_the_body_has_nothing_else(self):
        self.beat()
        for text in ("", "   ", "x" * (B.SAY_MAX + 1), "bad\x1b[2Jtext", 5, None):
            r, data = self.say(text)
            self.assertEqual((r.status, data["error"]), (400, "bad_request"), repr(text)[:20])
        self.assertEqual(self.post(f"/api/bots/{self.rec['id']}/say", {"text": "hi", "path": "/x"})[0].status, 400)
        self.assertEqual(self.post(f"/api/bots/{self.rec['id']}/say", {})[0].status, 400)
        self.assertEqual(self.say("x" * B.SAY_MAX)[0].status, 200, "the limit itself is fine")
        self.assertEqual(self.say("x", rec={"id": "nope-abc"})[0].status, 404)

    def test_it_has_its_own_rate_bucket_and_does_not_use_the_one_of_edits(self):
        self.beat()
        for i in range(R.BOT_SAY_BURST):
            self.assertEqual(self.say(f"m{i}")[0].status, 200, i)
        r, data = self.say("one more")
        self.assertEqual((r.status, data["error"]), (429, "rate_limited"))
        self.assertIn("Retry-After", r.headers)
        self.assertEqual(self.post(f"/api/bots/{self.rec['id']}/pause", {})[0].status, 200, "editing is another bucket")

    def test_read_only_cannot_talk(self):
        self.beat()
        r, data = self.say(app=self.readonly_app())
        self.assertEqual((r.status, data["error"]), (403, "readonly"))
        self.assertEqual(self.say(token=None)[0].status, 401)
        self.assertEqual(self.commands(), [])

    def test_the_conversation_is_the_session_in_the_agent_chat_shape(self):
        r, data = self.conv()
        self.assertEqual(r.status, 200)
        self.assertEqual((data["source"], data["session"], data["has_older"]), ("session", True, False))
        self.assertEqual([i["role"] for i in data["items"]][:1], ["user"])
        self.assertIn("hello bot", json.dumps(data["items"]))
        self.assertEqual((data["live"], data["busy"], data["queue"], data["error"], data["state"]), (False, False, 0, None, "idle"))

    def test_the_conversation_follows_the_bot_while_it_works_and_says_what_waits(self):
        S.update_state(S.state_dir(self.rec["id"], self.env), lambda s: s.update(
            running={"run": 1, "pid": 1, "started": NOW}, chat_queue=[{"text": "and then?\x1b[2J", "ts": NOW}]))
        data = self.conv()[1]
        self.assertTrue(data["busy"] or data["live"])
        self.assertEqual(data["state"], "working")
        self.assertEqual([p["text"] for p in data["pending_prompts"]], ["and then?"])
        self.assertEqual(data["queue"], 1)

    def test_rev_and_the_long_poll_answer_unchanged_until_the_file_grows(self):
        first = self.conv()[1]
        r, same = self.conv(f"?rev={first['rev']}")
        self.assertEqual(r.status, 200)
        self.assertEqual(same, {"unchanged": True, "rev": first["rev"]})
        with open(self.session, "a") as f:
            f.write(json.dumps({"type": "message", "id": "zz1", "parentId": None, "timestamp": "2026-11-02T10:00:00.000Z",
                                "message": {"role": "assistant", "content": [{"type": "text", "text": "a fresh thought"}]}}) + "\n")
        grown = self.conv(f"?rev={first['rev']}")[1]
        self.assertNotIn("unchanged", grown)
        self.assertIn("a fresh thought", json.dumps(grown["items"]))
        self.assertEqual(self.conv("?rev=zz")[0].status, 400)
        self.assertEqual(self.conv("?before=0")[0].status, 400)

    def test_a_bot_that_never_talked_has_an_empty_conversation(self):
        fresh = self.bot("fresh")
        r, data = self.call("GET", f"/api/bots/{fresh['id']}/conversation")
        self.assertEqual((r.status, data["items"], data["session"]), (200, [], False))

    def test_a_session_file_outside_dot_omp_or_a_link_is_never_read(self):
        outside = os.path.join(self.tmp.name, "elsewhere.jsonl")
        with open(outside, "w") as f:
            f.write(session_lines(self.work, "secret"))
        for bad in (outside, "/etc/passwd", "relative.jsonl"):
            S.update_state(S.state_dir(self.rec["id"], self.env), lambda s, bad=bad: s.update(session_file=bad))
            data = self.conv()[1]
            self.assertEqual((data["items"], data["session"]), ([], False), bad)
            self.assertNotIn("secret", json.dumps(data))
        os.unlink(self.session)
        os.symlink(outside, self.session)
        S.update_state(S.state_dir(self.rec["id"], self.env), lambda s: s.update(session_file=self.session))
        self.assertEqual(self.conv()[1]["items"], [])

    def test_unknown_bot_and_wrong_method(self):
        self.assertEqual(self.call("GET", "/api/bots/nope-abc/conversation")[0].status, 404)
        self.assertEqual(self.post(f"/api/bots/{self.rec['id']}/conversation", {})[0].status, 405)
        self.assertEqual(self.call("GET", f"/api/bots/{self.rec['id']}/say")[0].status, 405)

    def test_there_is_no_way_left_to_revive_a_run_in_a_pane(self):
        self.run_record(self.rec, 1, session_file=self.session)
        self.assertEqual(self.post(f"/api/bots/{self.rec['id']}/runs/1/open", {"confirm": True})[0].status, 404)
        run = self.call("GET", f"/api/bots/{self.rec['id']}/runs/1")[1]["run"]
        self.assertNotIn("can_open", run)


class TestBotLimitsInMeta(unittest.TestCase):
    def test_the_limits_the_page_shows_are_the_ones_the_server_enforces(self):
        m = R.meta_json(False)
        self.assertEqual(m["limits"]["bots"], B.limits())
        self.assertIn("bots", m["capabilities"])
        lim = B.limits()
        self.assertEqual((lim["max_bots"], lim["min_interval_s"], lim["permissions"]), (50, 300, ["readonly", "edit"]))
        self.assertLessEqual(lim["default_time_s"], lim["max_time_s"])
        self.assertLessEqual(lim["default_cost_usd"], lim["max_cost_usd"])
        self.assertNotIn("bots", R.meta_json(False)["states"], "the vocabulary of agent states stays as it was")
        self.assertEqual(set(m["states"]), set(R.VOCAB))


if __name__ == "__main__":
    unittest.main()
