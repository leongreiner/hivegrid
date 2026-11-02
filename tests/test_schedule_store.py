"""bin/hivegrid_schedule.py, storage half: validation and defaults, the atomic and locked schedules.json,
per-schedule state, runs.jsonl rotation, the memory cap, the results inbox, the daemon command inbox, the
summary states and the self-scheduling commands. Temp dirs only; every path comes from an explicit env dict."""
import json
import multiprocessing
import os
import stat
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
sys.dont_write_bytecode = True

import hivegrid_schedule as S  # noqa: E402

NOW = 1_800_000_000
WHEN = {"type": "cron", "expr": "0 9 * * 1-5", "tz": "UTC"}


def slurp(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def jload(path):
    return json.loads(slurp(path))


def spit(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def spec(**over):
    d = {"name": "Morning digest", "when": dict(WHEN), "task": {"prompt": "Summarize the news."},
         "run": {"cwd": "/tmp"}}
    d.update(over)
    return d


class TempEnv(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = os.path.join(self._tmp.name, "home")
        self.rt = os.path.join(self._tmp.name, "rt")
        os.makedirs(self.home, mode=0o700)
        os.makedirs(self.rt, mode=0o700)
        self.env = {"HOME": self.home, "XDG_RUNTIME_DIR": self.rt}

    def store(self):
        return S.Store(env=self.env)

    def add(self, **over):
        rec = self.store().add(spec(**over), now=NOW)
        S.init_state_dir(rec, NOW, self.env)
        return rec

    def sdir(self, rec):
        return S.state_dir(rec["id"], self.env)


class Validation(unittest.TestCase):
    def rec(self, **over):
        d = dict(spec(), id="digest-abc", origin="cli")
        d.update(over)
        return d

    def test_defaults_are_filled(self):
        r = S.validate(self.rec())
        self.assertEqual((r["kind"], r["enabled"]), ("task", True))
        self.assertEqual(r["run"], {"cwd": "/tmp", "account": None, "model": None, "thinking": "medium", "mode": "headless",
                                    "permissions": "edit", "max_time_s": 1800, "max_cost_usd": 2.0, "add_dirs": []})
        self.assertEqual(r["policy"], {"catchup": "latest", "grace_s": 7200, "overlap": "skip", "retries": 2,
                                       "backoff_s": [300, 1800], "quota_guard_pct": 90, "fallback_accounts": [],
                                       "pause_after_failures": 5})
        self.assertEqual(r["identity"], {"system": "", "memory": False, "continuity": "fresh", "memory_max_bytes": 8192})
        self.assertEqual(r["notify"], {"on": ["failed", "limit", "missed"], "done": False})
        self.assertEqual(S.validate(r), r)  # idempotent

    def test_agent_forces_memory_and_needs_a_task(self):
        r = S.validate(self.rec(kind="agent", identity={"memory": False}))
        self.assertTrue(r["identity"]["memory"])
        with self.assertRaises(ValueError):
            S.validate(self.rec(kind="agent", task={"prompt": "  "}))
        with self.assertRaises(ValueError):
            S.validate(self.rec(task={}))

    def test_rejections(self):
        bad = [
            ("unknown top key", self.rec(color="red")),
            ("unknown run key", self.rec(run={"cwd": "/tmp", "shell": "x"})),
            ("unknown policy key", self.rec(policy={"retry": 3})),
            ("bad id", self.rec(id="AB")),
            ("short id", self.rec(id="ab")),
            ("bad kind", self.rec(kind="bot")),
            ("control char name", self.rec(name="a\x07b")),
            ("newline name", self.rec(name="a\nb")),
            ("empty name", self.rec(name="")),
            ("long name", self.rec(name="x" * 81)),
            ("long prompt", self.rec(task={"prompt": "x" * 20001})),
            ("relative cwd", self.rec(run={"cwd": "work"})),
            ("relative add_dir", self.rec(run={"cwd": "/tmp", "add_dirs": ["rel"]})),
            ("time too small", self.rec(run={"cwd": "/tmp", "max_time_s": 59})),
            ("time too big", self.rec(run={"cwd": "/tmp", "max_time_s": 86401})),
            ("cost zero", self.rec(run={"cwd": "/tmp", "max_cost_usd": 0})),
            ("cost huge", self.rec(run={"cwd": "/tmp", "max_cost_usd": 100.5})),
            ("cost bool", self.rec(run={"cwd": "/tmp", "max_cost_usd": True})),
            ("thinking", self.rec(run={"cwd": "/tmp", "thinking": "deep"})),
            ("mode", self.rec(run={"cwd": "/tmp", "mode": "loud"})),
            ("permissions", self.rec(run={"cwd": "/tmp", "permissions": "root"})),
            ("catchup", self.rec(policy={"catchup": "all"})),
            ("overlap", self.rec(policy={"overlap": "kill"})),
            ("backoff empty", self.rec(policy={"backoff_s": []})),
            ("notify", self.rec(notify={"on": ["boom"]})),
            ("continuity", self.rec(identity={"continuity": "forever"})),
            ("memory bytes", self.rec(identity={"memory_max_bytes": 10})),
            ("origin", self.rec(origin="fax")),
            ("when type", self.rec(when={"type": "sometimes"})),
            ("when short interval", self.rec(when={"type": "interval", "every_s": 60, "anchor": 0})),
            ("enabled", self.rec(enabled="yes")),
        ]
        for label, rec in bad:
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    S.validate(rec)

    def test_cwd_tilde_is_kept_and_must_resolve_absolute(self):
        self.assertEqual(S.validate(self.rec(run={"cwd": "~/work"}))["run"]["cwd"], "~/work")

    def test_full_needs_explicit_limits_and_is_refused_from_a_phone(self):
        full = {"cwd": "/tmp", "permissions": "full"}
        with self.assertRaises(ValueError):
            S.validate(self.rec(run=full))
        with self.assertRaises(ValueError):
            S.validate(self.rec(run=dict(full, max_time_s=600)))
        ok = dict(full, max_time_s=600, max_cost_usd=1)
        self.assertEqual(S.validate(self.rec(run=ok))["run"]["permissions"], "full")
        with self.assertRaises(ValueError) as cm:
            S.validate(self.rec(run=ok, origin="phone"))
        self.assertIn("phone", str(cm.exception))
        self.assertEqual(S.validate(self.rec(run=ok, origin="phone"), origin_rules=False)["origin"], "phone")
        self.assertEqual(S.validate(self.rec(run=ok, origin="panel"))["origin"], "panel")

    def test_notify_done_is_consistent(self):
        r = S.validate(self.rec(notify={"on": ["failed"], "done": True}))
        self.assertEqual(r["notify"], {"on": ["failed", "done"], "done": True})

    def test_id_generation(self):
        import random
        rng = random.Random(1)
        a = S.new_id("Morning Digest!", rng=rng)
        self.assertRegex(a, r"^morning-digest-[a-z0-9]{3}$")
        self.assertEqual(S.new_id("***", rng=rng)[:4], "bot-")
        long = S.new_id("x" * 80, rng=rng)
        self.assertLessEqual(len(long), 32)
        self.assertRegex(long, S.ID_RE.pattern)
        ids = set()
        for _ in range(50):
            ids.add(S.new_id("a", existing=ids, rng=rng))
        self.assertEqual(len(ids), 50)


class StoreFile(TempEnv):
    def test_add_writes_private_json_and_reads_back(self):
        rec = self.store().add(spec(), now=NOW)
        path = S.schedules_path(self.env)
        self.assertEqual(path, os.path.join(self.home, ".config", "hivegrid", "schedules.json"))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o700)
        doc = jload(path)
        self.assertEqual(doc["version"], 1)
        self.assertEqual(doc["schedules"][0]["id"], rec["id"])
        self.assertEqual((rec["created"], rec["updated"]), (NOW, NOW))
        self.assertEqual(self.store().get(rec["id"]), rec)
        self.assertEqual(os.listdir(os.path.dirname(path)).count(".schedules.json.lock"), 0)
        self.assertFalse([n for n in os.listdir(os.path.dirname(path)) if n.endswith(".tmp")])

    def test_xdg_dirs_are_honoured_at_call_time(self):
        env = {"HOME": self.home, "XDG_CONFIG_HOME": os.path.join(self.home, "cfg"), "XDG_STATE_HOME": os.path.join(self.home, "st")}
        self.assertEqual(S.schedules_path(env), os.path.join(self.home, "cfg", "hivegrid", "schedules.json"))
        self.assertEqual(S.state_root(env), os.path.join(self.home, "st", "hivegrid", "schedules"))
        self.assertEqual(S.state_root({"HOME": "/h"}), "/h/.local/state/hivegrid/schedules")
        self.assertEqual(S.command_dir({"XDG_RUNTIME_DIR": "/run/u/1"}), "/run/u/1/hivegrid-schedule")
        self.assertEqual(S.command_dir({}), f"/tmp/hivegrid-schedule-{os.getuid()}")
        self.assertEqual(S.summary_path({"XDG_RUNTIME_DIR": "/run/u/1"}), "/run/u/1/hivegrid-schedule.json")

    def test_change_merges_sections_and_bumps_updated(self):
        rec = self.add()
        new = self.store().change(rec["id"], {"run": {"model": "m1"}, "enabled": False}, now=NOW + 5)
        self.assertEqual(new["run"]["model"], "m1")
        self.assertEqual(new["run"]["cwd"], "/tmp")
        self.assertFalse(new["enabled"])
        self.assertEqual((new["created"], new["updated"]), (NOW, NOW + 5))
        with self.assertRaises(ValueError):
            self.store().change(rec["id"], {"run": {"max_time_s": 5}})
        with self.assertRaises(ValueError):
            self.store().change(rec["id"], {"id": "other-one"})
        with self.assertRaises(KeyError):
            self.store().change("nope-nope", {"enabled": False})
        self.assertEqual(self.store().get(rec["id"])["run"]["model"], "m1")  # failed edits changed nothing

    def test_phone_cannot_make_a_schedule_full(self):
        rec = self.add()
        with self.assertRaises(ValueError):
            self.store().change(rec["id"], {"run": {"permissions": "full", "max_time_s": 600, "max_cost_usd": 1}}, via="phone")

    def test_remove(self):
        rec = self.add()
        self.assertTrue(self.store().remove(rec["id"]))
        self.assertFalse(self.store().remove(rec["id"]))
        self.assertEqual(self.store().read(), [])

    def test_reread_by_mtime(self):
        st = self.store()
        self.assertEqual(st.read(), [])
        rec = S.Store(env=self.env).add(spec(), now=NOW)
        self.assertEqual([r["id"] for r in st.read()], [rec["id"]])
        S.Store(env=self.env).change(rec["id"], {"name": "Renamed"}, now=NOW + 1)
        self.assertEqual(st.read()[0]["name"], "Renamed")

    def test_read_returns_copies(self):
        rec = self.add()
        st = self.store()
        st.read()[0]["name"] = "mutated"
        self.assertEqual(st.get(rec["id"])["name"], rec["name"])

    def test_invalid_records_are_listed_and_kept_untouched(self):
        rec = self.add()
        path = S.schedules_path(self.env)
        doc = jload(path)
        doc["schedules"].append({"id": "broken-one", "name": "x", "bogus": 1})
        spit(path, json.dumps(doc))
        st = self.store()
        self.assertEqual([r["id"] for r in st.read()], [rec["id"]])
        self.assertEqual(st.errors[0][0], "broken-one")
        self.assertIn("bogus", st.errors[0][1])
        st.change(rec["id"], {"enabled": False})
        self.assertEqual([r.get("id") for r in jload(path)["schedules"]], [rec["id"], "broken-one"])

    def test_corrupt_or_newer_file_is_refused_not_overwritten(self):
        path = S.schedules_path(self.env)
        os.makedirs(os.path.dirname(path), mode=0o700)
        for text in ("{not json", '{"version": 1}', '{"version": 9, "schedules": []}', "[]"):
            with open(path, "w") as f:
                f.write(text)
            with self.assertRaises(S.ScheduleError, msg=text):
                self.store().read()
            with self.assertRaises(S.ScheduleError, msg=text):
                self.store().add(spec())
            self.assertEqual(slurp(path), text)

    def test_symlinked_or_foreign_locations_are_refused(self):
        path = S.schedules_path(self.env)
        real = os.path.join(self._tmp.name, "elsewhere")
        os.makedirs(real, mode=0o700)
        os.makedirs(os.path.dirname(path), mode=0o700)
        with open(os.path.join(real, "x.json"), "w") as f:
            f.write('{"version": 1, "schedules": []}')
        os.symlink(os.path.join(real, "x.json"), path)
        with self.assertRaises(S.ScheduleError):
            self.store().read()
        os.unlink(path)
        os.rmdir(os.path.dirname(path))
        os.symlink(real, os.path.dirname(path))
        with self.assertRaises(S.ScheduleError):
            self.store().add(spec())
        os.unlink(os.path.dirname(path))
        os.makedirs(os.path.dirname(path), mode=0o777)
        os.chmod(os.path.dirname(path), 0o777)
        with self.assertRaises(S.ScheduleError):
            self.store().add(spec())

    def test_atomic_write_leaves_old_content_when_it_fails(self):
        path = os.path.join(self.home, "f.txt")
        S.atomic_write(path, "one")
        os.mkdir(path + ".d")
        with self.assertRaises(OSError):
            S.atomic_write(os.path.join(path + ".d", "x", "y"), "boom")
        S.atomic_write(path, b"two")
        self.assertEqual(slurp(path), "two")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual([n for n in os.listdir(self.home) if n.startswith(".f.txt")], [])

    def test_concurrent_edits_from_threads_lose_nothing(self):
        errors = []

        def worker(i):
            try:
                S.Store(env=self.env).add(spec(name=f"bot {i}"), now=NOW + i)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        recs = self.store().read()
        self.assertEqual(len(recs), 12)
        self.assertEqual(len({r["id"] for r in recs}), 12)

    def test_concurrent_edits_from_processes_lose_nothing(self):
        ctx = multiprocessing.get_context("fork")
        procs = [ctx.Process(target=_add_in_process, args=(self.env, i)) for i in range(6)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(60)
        self.assertEqual([p.exitcode for p in procs], [0] * 6)
        self.assertEqual(len(self.store().read()), 18)


def _add_in_process(env, i):
    for j in range(3):
        S.Store(env=env).add(spec(name=f"proc {i} {j}"), now=NOW)


class StateAndRuns(TempEnv):
    def run_rec(self, rec, n, status="ok", **over):
        d = {"run": n, "schedule": rec["id"], "trigger": "cron", "mode": "headless", "started": NOW + n,
             "ended": NOW + n + 5, "status": status, "attempt": 1, "summary": f"run {n}"}
        d.update(over)
        return d

    def test_state_dir_layout_and_permissions(self):
        rec = self.add(kind="agent", identity={"system": "You watch the build."})
        d = self.sdir(rec)
        self.assertEqual(d, os.path.join(self.home, ".local", "state", "hivegrid", "schedules", rec["id"]))
        self.assertEqual(sorted(os.listdir(d)), ["identity.md", "memory.md", "notes", "reports", "state.json"])
        self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)
        self.assertIn("unattended", slurp(os.path.join(d, "identity.md")))
        self.assertIn("You watch the build.", slurp(os.path.join(d, "identity.md")))
        self.assertEqual(S.load_state(d)["baseline"], NOW)

    def test_task_without_memory_has_no_memory_file(self):
        rec = self.add()
        self.assertNotIn("memory.md", os.listdir(self.sdir(rec)))

    def test_symlinked_state_dir_is_refused(self):
        rec = self.store().add(spec(), now=NOW)
        root = S.state_root(self.env)
        os.makedirs(root, mode=0o700)
        target = os.path.join(self._tmp.name, "target")
        os.makedirs(target, mode=0o700)
        os.symlink(target, os.path.join(root, rec["id"]))
        with self.assertRaises(S.ScheduleError):
            S.init_state_dir(rec, NOW, self.env)
        with self.assertRaises(S.ScheduleError):
            S.append_run(os.path.join(root, rec["id"]), self.run_rec(rec, 1))
        self.assertEqual(os.listdir(target), [])

    def test_update_state_roundtrip_and_defaults(self):
        rec = self.add()
        d = self.sdir(rec)

        def bump(st):
            st["run_count"] += 1
        self.assertEqual(S.update_state(d, bump)["run_count"], 1)
        self.assertEqual(S.update_state(d, bump)["run_count"], 2)
        self.assertEqual(S.load_state(d)["run_count"], 2)
        with open(os.path.join(d, "state.json"), "w") as f:
            f.write("garbage")
        self.assertEqual(S.load_state(d), S.default_state())

    def test_append_run_normalizes_and_cuts_summary(self):
        rec = self.add()
        d = self.sdir(rec)
        out = S.append_run(d, self.run_rec(rec, 1, summary="x" * 2000))
        self.assertEqual(len(out["summary"]), 1000)
        self.assertIs(out["seen"], False)
        self.assertIs(S.append_run(d, self.run_rec(rec, 2, status="skipped"))["seen"], True)
        self.assertEqual([r["run"] for r in S.read_runs(d)], [1, 2])
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, "runs.jsonl")).st_mode), 0o600)
        for bad in (dict(self.run_rec(rec, 3), status="weird"), dict(self.run_rec(rec, 3), trigger="dream"),
                    dict(self.run_rec(rec, 3), extra=1), dict(self.run_rec(rec, 3), schedule="X")):
            with self.assertRaises(ValueError):
                S.append_run(d, bad)

    def test_rotation_keeps_the_last_200_and_their_reports(self):
        rec = self.add()
        d = self.sdir(rec)
        for n in (1, 2, 250):
            with open(os.path.join(d, "reports", f"{n:04d}.md"), "w") as f:
                f.write("r")
        for n in range(1, 206):
            S.append_run(d, self.run_rec(rec, n))
        runs = S.read_runs(d)
        self.assertEqual(len(runs), 200)
        self.assertEqual((runs[0]["run"], runs[-1]["run"]), (6, 205))
        self.assertEqual(sorted(os.listdir(os.path.join(d, "reports"))), ["0250.md"])
        self.assertEqual(len(slurp(os.path.join(d, "runs.jsonl")).splitlines()), 200)

    def test_corrupt_lines_are_skipped(self):
        rec = self.add()
        d = self.sdir(rec)
        S.append_run(d, self.run_rec(rec, 1))
        with open(os.path.join(d, "runs.jsonl"), "a") as f:
            f.write("not json\n[1]\n")
        S.append_run(d, self.run_rec(rec, 2))
        self.assertEqual([r["run"] for r in S.read_runs(d)], [1, 2])
        self.assertEqual([r["run"] for r in S.read_runs(d, 1)], [2])

    def test_unseen_and_mark_seen(self):
        a, b = self.add(name="alpha"), self.add(name="beta")
        S.append_run(self.sdir(a), self.run_rec(a, 1, "failed"))
        S.append_run(self.sdir(a), self.run_rec(a, 2, "ok"))
        S.append_run(self.sdir(b), self.run_rec(b, 1, "ok"))
        S.append_run(self.sdir(b), self.run_rec(b, 2, "skipped"))
        self.assertEqual(len(S.unseen(env=self.env)), 3)
        self.assertEqual([r["run"] for r in S.unseen(self.sdir(a))], [1, 2])
        self.assertEqual(S.mark_seen(a["id"], 1, env=self.env), 1)
        self.assertEqual([r["run"] for r in S.unseen(self.sdir(a))], [2])
        self.assertEqual(S.mark_seen(a["id"], 1, env=self.env), 0)
        self.assertEqual(S.mark_seen(a["id"], env=self.env), 1)
        self.assertEqual(S.mark_seen(b["id"], env=self.env), 1)
        self.assertEqual(S.unseen(env=self.env), [])
        self.assertEqual(S.mark_seen("nothing-here", env=self.env), 0)
        self.assertEqual(len(S.read_runs(self.sdir(a))), 2)  # nothing dropped

    def test_trim_memory_keeps_the_tail_on_a_line_boundary(self):
        text = "".join(f"line {i:02d}\n" for i in range(10))  # 8 bytes a line
        self.assertEqual(S.trim_memory(text, 1000), text)
        self.assertEqual(S.trim_memory(text, 24), "line 07\nline 08\nline 09\n")
        self.assertEqual(S.trim_memory(text, 28), "line 07\nline 08\nline 09\n")  # no partial line
        self.assertEqual(S.trim_memory(text, 7), "")
        self.assertEqual(S.trim_memory("a" * 50, 20), "a" * 20)  # one huge line: bytes kept
        u = "é" * 10 + "\n" + "é" * 10
        out = S.trim_memory(u, 25)
        self.assertLessEqual(len(out.encode()), 25)
        out.encode("utf-8")  # still valid text

    def test_enforce_memory_cap_rewrites_the_file(self):
        rec = self.add(kind="agent", identity={"memory_max_bytes": 512})
        path = S.memory_path(rec["id"], self.env)
        with open(path, "w") as f:
            f.write("".join(f"fact {i:04d}\n" for i in range(200)))
        self.assertTrue(S.enforce_memory_cap(rec, self.env))
        text = slurp(path)
        self.assertLessEqual(len(text.encode()), 512)
        self.assertTrue(text.endswith("fact 0199\n"))
        self.assertFalse(S.enforce_memory_cap(rec, self.env))


class CommandInbox(TempEnv):
    def cdir(self):
        return os.path.join(self.rt, "hivegrid-schedule")

    def test_send_and_read_roundtrip(self):
        p = S.send_command(self.cdir(), {"op": "run-now", "id": "digest-abc"})
        self.assertRegex(os.path.basename(p), r"^cmd\.\d+-\d+\.json$")
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.cdir()).st_mode), 0o700)
        S.send_command(self.cdir(), {"op": "reload"})
        self.assertEqual(S.read_commands(self.cdir()), [{"op": "run-now", "id": "digest-abc"}, {"op": "reload"}])
        self.assertEqual(os.listdir(self.cdir()), [])
        self.assertEqual(S.read_commands(self.cdir()), [])

    def test_bad_commands_are_rejected_when_sending(self):
        for cmd in ({"op": "format"}, {"op": "skip"}, {"op": "skip", "id": "Bad Id"}, {"op": "run-now", "id": "abc", "x": 1}, "run"):
            with self.assertRaises(ValueError, msg=repr(cmd)):
                S.send_command(self.cdir(), cmd)

    def test_junk_is_dropped_and_foreign_files_are_left_alone(self):
        d = self.cdir()
        os.makedirs(d, mode=0o700)
        good = S.send_command(d, {"op": "cancel", "id": "digest-abc"})
        junk = {"cmd.1-1.json": "{not json", "cmd.2-1.json": '{"op": "rm -rf", "id": "abc"}', "cmd.3-1.json": "[1]",
                "cmd.4-1.json": '{"op": "skip"}', "cmd.5-1.json": '{"op": "skip", "id": "abc", "extra": 1}',
                "cmd.6-1.json": "x" * 10000, "other.json": "{}", "cmd.x.json": "{}"}
        for name, text in junk.items():
            with open(os.path.join(d, name), "w") as f:
                f.write(text)
        os.symlink(good, os.path.join(d, "cmd.7-1.json"))
        self.assertEqual(S.read_commands(d), [{"op": "cancel", "id": "digest-abc"}])
        left = sorted(os.listdir(d))
        self.assertEqual(left, ["cmd.7-1.json", "cmd.x.json", "other.json"])
        # files of another owner: pretend we are somebody else, nothing is read or removed
        S.send_command(d, {"op": "reload"})
        before = sorted(os.listdir(d))
        self.assertEqual(S.read_commands(d, uid=os.getuid() + 1), [])
        self.assertEqual(sorted(os.listdir(d)), before)

    def test_untrusted_directory_is_refused(self):
        d = self.cdir()
        os.makedirs(d, mode=0o700)
        os.chmod(d, 0o777)
        with self.assertRaises(S.ScheduleError):
            S.send_command(d, {"op": "reload"})
        with self.assertRaises(S.ScheduleError):
            S.read_commands(d)
        os.chmod(d, 0o700)
        link = os.path.join(self.rt, "link")
        os.symlink(d, link)
        with self.assertRaises(S.ScheduleError):
            S.send_command(link, {"op": "reload"})

    def test_daemon_alive_follows_the_heartbeat(self):
        path = S.summary_path(self.env)
        self.assertFalse(S.daemon_alive(self.env, NOW))
        S.write_summary(path, {"ts": NOW, "scheduler": {"heartbeat": NOW - 5}})
        self.assertTrue(S.daemon_alive(self.env, NOW))
        self.assertFalse(S.daemon_alive(self.env, NOW + 100))
        S.write_summary(path, {"ts": NOW, "scheduler": {}})
        self.assertFalse(S.daemon_alive(self.env, NOW))


class Summary(TempEnv):
    def item(self, doc, rec):
        return next(i for i in doc["items"] if i["id"] == rec["id"])

    def rec_run(self, rec, n, status, **over):
        d = {"run": n, "schedule": rec["id"], "trigger": "cron", "started": NOW - 100 + n, "ended": NOW - 90 + n,
             "status": status, "summary": f"s{n}"}
        d.update(over)
        return S.append_run(self.sdir(rec), d)

    def test_state_vocabulary_and_counts(self):
        waiting = self.add(name="waiting")
        off = self.add(name="off", enabled=False)
        busy = self.add(name="busy")
        broke = self.add(name="broke")
        fine = self.add(name="fine")
        seen_ok = self.add(name="seen ok")
        paused = self.add(name="paused")
        S.update_state(self.sdir(busy), lambda st: st.update(running={"run": 1, "pid": 1, "started": NOW - 5}))
        self.rec_run(broke, 1, "failed")
        self.rec_run(fine, 1, "ok")
        self.rec_run(seen_ok, 1, "ok", seen=True)
        self.rec_run(paused, 1, "ok", seen=True)
        S.update_state(self.sdir(paused), lambda st: st.update(paused_reason="5 runs in a row did not succeed"))
        doc = S.build_summary(NOW, {"heartbeat": NOW, "pid": 7}, env=self.env)
        got = {i["name"]: i["state"] for i in doc["items"]}
        self.assertEqual(got, {"waiting": "schedule", "off": "idle", "busy": "working", "broke": "attention",
                               "fine": "done", "seen ok": "schedule", "paused": "attention"})
        self.assertEqual(doc["counts"], {"total": 7, "enabled": 6, "running": 1, "attention": 2, "unseen": 2})
        self.assertEqual(doc["scheduler"], {"heartbeat": NOW, "pid": 7})
        self.assertEqual(doc["ts"], NOW)
        self.assertEqual(self.item(doc, broke)["last"], {"status": "failed", "ended": NOW - 89, "summary": "s1", "seen": False,
                                                          "level": "important", "headline": "broke failed"})
        self.assertEqual(self.item(doc, waiting)["when_text"], "weekdays 09:00 (UTC)")
        self.assertIsNotNone(self.item(doc, waiting)["next_at"])
        self.assertIsNone(self.item(doc, off)["next_at"])
        self.assertTrue(self.item(doc, busy)["running"])
        self.assertEqual(self.item(doc, paused)["paused_reason"], "5 runs in a row did not succeed")
        self.assertEqual([i["state"] for i in doc["items"]][:2], ["attention", "attention"])  # attention sorts first
        S.mark_seen(broke["id"], env=self.env)
        self.assertEqual(self.item(S.build_summary(NOW, env=self.env), broke)["state"], "schedule")

    def test_unseen_ok_after_unseen_failure_is_still_attention(self):
        rec = self.add()
        self.rec_run(rec, 1, "limit")
        self.rec_run(rec, 2, "ok")
        self.assertEqual(self.item(S.build_summary(NOW, env=self.env), rec)["state"], "attention")

    def test_done_agent_is_idle_and_has_no_next_run(self):
        rec = self.add()
        S.update_state(self.sdir(rec), lambda st: st.update(done=True))
        it = self.item(S.build_summary(NOW, env=self.env), rec)
        self.assertEqual((it["state"], it["next_at"]), ("idle", None))

    def test_write_summary_is_atomic_and_private(self):
        path = S.summary_path(self.env)
        doc = S.build_summary(NOW, env=self.env)
        S.write_summary(path, doc)
        self.assertEqual(jload(path), doc)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(S.read_summary(path), doc)
        self.assertEqual([n for n in os.listdir(self.rt) if n.endswith(".tmp")], [])
        os.unlink(path)
        os.symlink("/etc/passwd", path)
        self.assertIsNone(S.read_summary(path))

    def test_empty_store(self):
        doc = S.build_summary(NOW, env=self.env)
        self.assertEqual(doc["counts"], {"total": 0, "enabled": 0, "running": 0, "attention": 0, "unseen": 0})
        self.assertEqual(doc["items"], [])


class SelfScheduling(TempEnv):
    def setUp(self):
        super().setUp()
        self.rec = self.add(kind="agent")
        self.env["HIVEGRID_SCHEDULE"] = self.rec["id"]

    def state(self):
        return S.load_state(self.sdir(self.rec))

    def test_next_in_and_at_with_floor(self):
        self.assertEqual(S.self_next(self.rec["id"], in_s=3600, now=NOW, env=self.env), NOW + 3600)
        self.assertEqual(self.state()["next_override"], NOW + 3600)
        self.assertEqual(S.self_next(self.rec["id"], in_s=10, now=NOW, env=self.env), NOW + 300)
        self.assertEqual(S.self_next(self.rec["id"], at=NOW + 7200, now=NOW, env=self.env), NOW + 7200)
        self.assertEqual(S.self_next(self.rec["id"], at=NOW - 5, now=NOW, env=self.env), NOW + 300)
        t = S.self_next(self.rec["id"], at="2027-01-20T09:00", now=NOW, env=self.env)
        self.assertEqual(S._zone("UTC").naive(t).isoformat(), "2027-01-20T09:00:00")
        for kw in ({}, {"in_s": 5, "at": 7}, {"in_s": -1}, {"in_s": 400 * 86400}):
            with self.assertRaises(ValueError, msg=str(kw)):
                S.self_next(self.rec["id"], now=NOW, env=self.env, **kw)

    def test_next_overrides_the_regular_slots_until_it_is_reached(self):
        S.self_next(self.rec["id"], in_s=7200, now=NOW, env=self.env)
        st, rec = self.state(), self.rec
        self.assertEqual(S.next_run(rec, st, NOW), NOW + 7200)
        self.assertEqual(S.due(rec, st, NOW + 7199), [])
        self.assertEqual(S.due(rec, st, NOW + 7200), [NOW + 7200])
        st = S.advance(rec, st, NOW + 7200, "self")
        self.assertIsNone(st["next_override"])

    def test_done_stops_everything_and_next_revives(self):
        S.self_done(self.rec["id"], env=self.env)
        self.assertTrue(self.state()["done"])
        self.assertIsNone(S.next_run(self.rec, self.state(), NOW))
        self.assertEqual(S.due(self.rec, self.state(), NOW + 10 * 86400), [])
        S.self_next(self.rec["id"], in_s=600, now=NOW, env=self.env)
        self.assertFalse(self.state()["done"])

    def test_note_is_stored_and_capped(self):
        p = S.self_note(self.rec["id"], "build is red since Tuesday", now=NOW, env=self.env)
        self.assertEqual(os.path.dirname(p), os.path.join(self.sdir(self.rec), "notes"))
        self.assertEqual(slurp(p), "build is red since Tuesday\n")
        with self.assertRaises(ValueError):
            S.self_note(self.rec["id"], "  ", env=self.env)
        for i in range(60):
            S.self_note(self.rec["id"], f"n{i}", now=NOW + i, env=self.env)
        self.assertEqual(len(os.listdir(os.path.join(self.sdir(self.rec), "notes"))), 50)

    def test_only_the_named_schedule_may_be_changed(self):
        other = self.add(name="other")
        for fn in (lambda: S.self_next(other["id"], in_s=600, now=NOW, env=self.env),
                   lambda: S.self_done(other["id"], env=self.env),
                   lambda: S.self_note(other["id"], "hi", env=self.env)):
            with self.assertRaises(ValueError):
                fn()
        env = dict(self.env)
        del env["HIVEGRID_SCHEDULE"]
        with self.assertRaises(ValueError):
            S.self_done(self.rec["id"], env=env)
        self.assertFalse(S.load_state(self.sdir(other))["done"])


class Cli(TempEnv):
    def call(self, *argv, now=NOW):
        import io
        out, err = io.StringIO(), io.StringIO()
        rc = S.main(list(argv), env=self.env, out=out, err=err, now=now)
        return rc, out.getvalue(), err.getvalue()

    def test_add_list_show_pause_resume_rm(self):
        rc, out, _ = self.call("add", "--name", "t", "--prompt", "hi", "--when", "weekdays 9am", "--cwd", "/tmp", "--tz", "UTC")
        self.assertEqual(rc, 0)
        sid = out.splitlines()[0]
        self.assertRegex(sid, r"^t-[a-z0-9]{3}$")
        self.assertIn("weekdays 09:00 (UTC); next:", out)
        self.assertEqual(out.splitlines()[1].count(","), 2)  # three fire times
        rc, out, _ = self.call("list")
        self.assertIn(sid, out)
        doc = json.loads(self.call("list", "--json")[1])
        self.assertEqual(doc["items"][0]["state"], "schedule")
        self.assertEqual(self.call("show", sid)[0], 0)
        self.assertEqual(self.call("show", sid[:3])[0], 0)  # unique prefix
        self.assertEqual(self.call("pause", sid)[0], 0)
        self.assertEqual(json.loads(self.call("list", "--json")[1])["items"][0]["state"], "idle")
        self.assertEqual(self.call("resume", sid)[0], 0)
        self.assertEqual(self.call("run", sid)[0], 0)
        self.assertTrue(S.load_state(S.state_dir(sid, self.env))["run_now"])
        self.assertEqual(self.call("skip", sid)[0], 0)
        self.assertEqual(self.call("seen", sid)[0], 0)
        self.assertEqual(self.call("logs", sid)[1].strip(), "no runs yet")
        self.assertEqual(self.call("next", sid)[0], 0)
        rc, out, _ = self.call("rm", sid)
        self.assertEqual((rc, self.store().read()), (0, []))
        self.assertFalse(os.path.exists(S.state_dir(sid, self.env)))

    def test_exit_codes(self):
        self.assertEqual(self.call("show", "nothing")[0], 1)
        self.assertEqual(self.call("add", "--name", "x", "--prompt", "p", "--when", "bogus", "--cwd", "/tmp")[0], 2)
        self.assertEqual(self.call("add", "--name", "x", "--when", "daily 9", "--cwd", "/tmp")[0], 2)  # no prompt
        self.assertEqual(self.call("add", "--name", "x", "--prompt", "p", "--when", "daily 9", "--cwd", "rel")[0], 2)
        self.assertEqual(self.call("add", "--name", "x", "--prompt", "p", "--when", "daily 9", "--cwd", "/tmp", "--permissions", "full")[0], 2)
        self.assertEqual(self.store().read(), [])

    def test_parse_and_edit(self):
        rc, out, _ = self.call("parse", "every", "monday", "at", "7", "--tz", "UTC")
        self.assertEqual(rc, 0)
        self.assertIn("mon 07:00 (UTC)", out)
        self.assertIn("0 7 * * 1", out)
        self.assertEqual(self.call("parse", "gibberish")[0], 2)
        sid = self.call("add", "--name", "t", "--prompt", "hi", "--when", "daily 9", "--cwd", "/tmp", "--tz", "UTC")[1].splitlines()[0]
        rc, out, _ = self.call("edit", sid, "--when", "weekends 10:00", "--model", "m1")
        self.assertEqual(rc, 0)
        rec = self.store().get(sid)
        self.assertEqual(rec["when"]["expr"], "0 10 * * 0,6")
        self.assertEqual(rec["run"]["model"], "m1")
        self.assertEqual(self.call("edit", sid)[0], 2)

    def test_doctor_reports_problems_with_exit_codes(self):
        sid = self.call("add", "--name", "t", "--prompt", "hi", "--when", "daily 9", "--cwd", "/tmp")[1].splitlines()[0]
        self.assertEqual(self.call("doctor")[0], 0)
        self.store().change(sid, {"run": {"cwd": "/does/not/exist"}})
        rc, out, _ = self.call("doctor")
        self.assertEqual(rc, 1)
        self.assertIn("cwd /does/not/exist does not exist", out)
        S.update_state(S.state_dir(sid, self.env), lambda st: st.update(running={"run": 1, "pid": 2 ** 22 + 12345, "started": NOW}))
        self.assertIn("stale", self.call("doctor")[1])
        path = S.schedules_path(self.env)
        with open(path, "w") as f:
            f.write("{broken")
        self.assertEqual(self.call("doctor")[0], 2)


class Reports(unittest.TestCase):
    def test_the_two_header_lines_set_level_and_headline(self):
        r = S.parse_report("level: important\nheadline: CI of repo X fixed: flaky test timeout, now green\n\n# Details\nbody", "agent")
        self.assertEqual(r, {"level": "important", "headline": "CI of repo X fixed: flaky test timeout, now green",
                             "body": "# Details\nbody", "marked": True})

    def test_tolerant_header(self):
        cases = [
            ("LEVEL: None\nHeadline: CI green\n\nok", "none", "CI green", "ok"),
            ("**level:** important\n**headline:** Disk full\nbody", "important", "Disk full", "body"),
            ("```\nlevel: info\nheadline: Rotated logs\n```\n\nDone.", "info", "Rotated logs", "Done."),
            ("headline: Reversed\nlevel: important\n\nb", "important", "Reversed", "b"),
            ("\n\n  level : none  \r\n headline: x\r\n\r\nb", "none", "x", "b"),
        ]
        for text, level, headline, body in cases:
            with self.subTest(text=text):
                r = S.parse_report(text, "agent")
                self.assertEqual((r["level"], r["headline"], r["body"]), (level, headline, body))

    def test_missing_marker_defaults_by_kind_and_headline_is_the_first_sentence(self):
        text = "# Report\nThe build was red because of a typo. I fixed it.\nMore."
        agent, task = S.parse_report(text, "agent"), S.parse_report(text, "task")
        self.assertEqual((agent["level"], task["level"]), ("info", "important"))
        self.assertEqual(agent["headline"], "Report")
        self.assertFalse(agent["marked"])
        self.assertEqual(agent["body"], text)
        self.assertEqual(S.parse_report("The build was red. Fixed.", "agent")["headline"], "The build was red.")
        self.assertEqual(S.parse_report("", "task"), {"level": "important", "headline": "", "body": "", "marked": False})

    def test_unknown_level_falls_back_and_headline_is_one_clean_line_of_at_most_140(self):
        r = S.parse_report("level: urgent\nheadline: " + "x" * 300 + "\x07\n\nb", "agent")
        self.assertEqual(r["level"], "info")
        self.assertEqual(len(r["headline"]), 140)
        self.assertTrue(r["headline"].endswith("\u2026"))
        self.assertNotRegex(r["headline"], r"[\x00-\x1f]")
        self.assertEqual(S.parse_report("level: urgent\nheadline: h", "task")["level"], "important")

    def test_a_body_that_merely_starts_with_prose_about_levels_is_not_a_header(self):
        r = S.parse_report("Levels are fine today.\nlevel: none", "agent")
        self.assertFalse(r["marked"])


class RunLevels(unittest.TestCase):
    def norm(self, status, **over):
        d = {"run": 1, "schedule": "abc", "trigger": "cron", "status": status}
        d.update(over)
        return S.normalize_run(d, "Watcher")

    def test_status_overrides_and_automatic_headlines(self):
        r = self.norm("failed", error="boom: bad\nsecond", level="none", headline="all fine")
        self.assertEqual((r["level"], r["headline"], r["seen"]), ("important", "Watcher failed: boom: bad", False))
        self.assertEqual(self.norm("limit")["headline"], "Watcher hit its usage limit")
        self.assertEqual(self.norm("timeout", error="used max_time_s")["headline"], "Watcher timed out: used max_time_s")
        self.assertEqual(self.norm("missed", summary="2 runs were missed")["headline"], "Watcher missed a run: 2 runs were missed")
        self.assertEqual(self.norm("cost")["level"], "important")
        self.assertEqual(len(self.norm("failed", error="e" * 500)["headline"]), 140)

    def test_quiet_statuses_and_chat(self):
        self.assertEqual((self.norm("skipped", level="important")["level"], self.norm("skipped")["seen"]), ("none", True))
        self.assertEqual(self.norm("cancelled", error=S.CANCELLED_BY_USER)["level"], "none")
        self.assertEqual(self.norm("cancelled", error="the scheduler was stopped")["level"], "important")
        chat = self.norm("failed", trigger="chat", error="x")
        self.assertEqual((chat["level"], chat["seen"], chat["headline"]), ("none", True, None))

    def test_ok_runs_keep_the_level_and_none_is_seen(self):
        self.assertEqual(self.norm("ok")["level"], "info")
        self.assertEqual(self.norm("ok", level="important", headline=" a\nb ")["headline"], "a b")
        r = self.norm("ok", level="none")
        self.assertEqual((r["seen"], r["level"]), (True, "none"))
        with self.assertRaises(ValueError):
            self.norm("ok", level="loud")


class BriefingApi(TempEnv):
    def record(self, rec, n, status="ok", ended=0, **over):
        d = {"run": n, "schedule": rec["id"], "trigger": "cron", "started": NOW - 500 + ended, "ended": NOW - 400 + ended,
             "status": status, "summary": f"s{n}", "session_id": f"sess{n}"}
        d.update(over)
        return S.append_run(self.sdir(rec), d, rec["name"])

    def setUp(self):
        super().setUp()
        self.ci = self.add(name="CI watch", kind="agent")
        self.mail = self.add(name="Mail", kind="agent")

    def test_only_important_runs_across_bots_newest_first(self):
        self.record(self.ci, 1, level="none", headline="CI green")
        self.record(self.ci, 2, level="important", headline="CI fixed", ended=10)
        self.record(self.mail, 1, level="info", headline="3 mails filed", ended=20)
        self.record(self.mail, 2, "failed", error="imap down", ended=30)
        self.record(self.mail, 3, level="important", headline="Invoice due", ended=5, trigger="chat")  # a chat turn never counts
        got = S.briefing(self.env)
        self.assertEqual([(e["bot_name"], e["run"], e["headline"]) for e in got],
                         [("Mail", 2, "Mail failed: imap down"), ("CI watch", 2, "CI fixed")])
        e = got[1]
        self.assertEqual((e["bot_id"], e["kind"], e["level"], e["status"], e["seen"], e["session_id"], e["ended"]),
                         (self.ci["id"], "agent", "important", "ok", False, "sess2", NOW - 390))
        self.assertEqual(S.briefing_counts(self.env), {"unseen": 2, "total": 2})
        self.assertEqual(len(S.briefing(self.env, limit=1)), 1)

    def test_seen_handling(self):
        self.record(self.ci, 1, level="important", headline="one")
        self.record(self.mail, 1, level="important", headline="two", ended=1)
        self.assertEqual(S.mark_briefing_seen(self.env), 2)
        self.assertEqual(S.mark_briefing_seen(self.env), 0)
        self.assertEqual(S.briefing(self.env, include_seen=False), [])
        self.assertEqual([e["seen"] for e in S.briefing(self.env)], [True, True])
        self.assertEqual(S.briefing_counts(self.env), {"unseen": 0, "total": 2})

    def test_marking_the_briefing_seen_leaves_other_unseen_runs_alone(self):
        self.record(self.ci, 1, level="info")
        self.record(self.ci, 2, level="important", headline="x", ended=1)
        S.mark_briefing_seen(self.env)
        self.assertEqual([r["seen"] for r in S.read_runs(self.sdir(self.ci))], [False, True])

    def test_runs_from_before_levels_existed_are_read_sensibly(self):
        with open(os.path.join(self.sdir(self.ci), "runs.jsonl"), "w") as f:
            f.write(json.dumps({"run": 1, "schedule": self.ci["id"], "trigger": "cron", "status": "failed", "seen": False,
                                "ended": NOW, "error": "old failure"}) + "\n")
            f.write(json.dumps({"run": 2, "schedule": self.ci["id"], "trigger": "cron", "status": "ok", "seen": False,
                                "ended": NOW, "summary": "Old ok. More"}) + "\n")
        self.assertEqual([e["headline"] for e in S.briefing(self.env)], ["CI watch failed: old failure"])
        item = next(i for i in S.build_summary(NOW, env=self.env)["items"] if i["id"] == self.ci["id"])
        self.assertEqual((item["last"]["level"], item["last"]["headline"]), ("info", "Old ok."))

    def test_summary_carries_the_briefing_and_levels(self):
        for n in range(1, 6):
            self.record(self.ci, n, level="important", headline=f"h{n}", ended=n)
        self.record(self.mail, 1, level="none", headline="quiet")
        doc = S.build_summary(NOW, env=self.env)
        self.assertEqual(doc["briefing"]["unseen"], 5)
        self.assertEqual([e["headline"] for e in doc["briefing"]["newest"]], ["h5", "h4", "h3"])
        items = {i["name"]: i for i in doc["items"]}
        self.assertEqual((items["CI watch"]["last"]["level"], items["CI watch"]["last"]["headline"]), ("important", "h5"))
        self.assertEqual((items["Mail"]["last"]["level"], items["Mail"]["last"]["seen"]), ("none", True))
        self.assertEqual(items["Mail"]["chat"], {"active": False, "queue": 0, "error": None})

    def test_a_chat_turn_is_not_the_last_result(self):
        self.record(self.ci, 1, level="info", headline="routine")
        self.record(self.ci, 2, trigger="chat", level="none", ended=5)
        item = next(i for i in S.build_summary(NOW, env=self.env)["items"] if i["id"] == self.ci["id"])
        self.assertEqual(item["last"]["headline"], "routine")

    def test_rotation_drops_chat_turns_first(self):
        for n in range(1, 4):
            self.record(self.ci, n, level="important", headline=f"r{n}")
        for n in range(4, S.MAX_RUNS + 3):
            self.record(self.ci, n, trigger="chat")
        runs = S.read_runs(self.sdir(self.ci))
        self.assertEqual(len(runs), S.MAX_RUNS)
        self.assertEqual([r["run"] for r in runs if r["trigger"] != "chat"], [1, 2, 3])


class Talking(TempEnv):
    def setUp(self):
        super().setUp()
        self.rec = self.add(name="Pal", kind="agent")

    def alive(self, on=True):
        S.write_summary(S.summary_path(self.env), S.build_summary(NOW, {"heartbeat": NOW - 2} if on else {}, env=self.env))

    def inbox(self):
        return S.read_commands(S.command_dir(self.env))

    def test_say_needs_a_running_daemon_and_never_queues_into_the_void(self):
        with self.assertRaises(S.ScheduleError) as cm:
            S.say(self.rec["id"], "hi", self.env, NOW)
        self.assertEqual((cm.exception.code, str(cm.exception)), ("no_daemon", "scheduler not running"))
        self.alive(False)
        with self.assertRaises(S.ScheduleError):
            S.say(self.rec["id"], "hi", self.env, NOW)
        self.assertFalse(os.path.isdir(S.command_dir(self.env)) and os.listdir(S.command_dir(self.env)))

    def test_say_sends_a_chat_command_and_reports_the_queue_position(self):
        self.alive()
        self.assertEqual(S.say(self.rec["id"], "  hello  ", self.env, NOW), {"queued": False, "position": 0})
        self.assertEqual(self.inbox(), [{"op": "chat", "id": self.rec["id"], "text": "hello"}])
        S.update_state(self.sdir(self.rec), lambda st: st.update(running={"run": 1, "pid": 5, "started": NOW}))
        self.assertEqual(S.say(self.rec["id"], "again", self.env, NOW), {"queued": True, "position": 1})
        S.update_state(self.sdir(self.rec), lambda st: st.update(chat_queue=[{"text": "a", "ts": 1}] * 2))
        self.assertEqual(S.say(self.rec["id"], "third", self.env, NOW), {"queued": True, "position": 3})

    def test_say_refuses_bad_input_unknown_bots_and_a_full_queue(self):
        self.alive()
        for text in ("", "   ", None, "x" * 4001):
            with self.assertRaises(ValueError, msg=repr(text)):
                S.say(self.rec["id"], text, self.env, NOW)
        self.assertEqual(S.say(self.rec["id"], "x" * 4000, self.env, NOW)["queued"], False)
        with self.assertRaises(S.ScheduleError) as cm:
            S.say("no-such-bot", "hi", self.env, NOW)
        self.assertEqual(cm.exception.code, "unknown_bot")
        S.update_state(self.sdir(self.rec), lambda st: st.update(chat_queue=[{"text": "a", "ts": 1}] * 5))
        with self.assertRaises(S.ScheduleError) as cm:
            S.say(self.rec["id"], "sixth", self.env, NOW)
        self.assertEqual(cm.exception.code, "queue_full")

    def test_conversation(self):
        self.assertEqual(S.conversation(self.rec["id"], self.env),
                         {"session_file": None, "live": False, "busy": False, "queue": 0, "error": None, "activity": None})
        S.update_state(self.sdir(self.rec), lambda st: st.update(
            session_file="/x/s.jsonl", running={"run": 3, "pid": 5, "started": NOW, "chat": True},
            chat_queue=[{"text": "a", "ts": 1}], chat={"active": True, "started": NOW, "error": "earlier failure"}))
        self.assertEqual(S.conversation(self.rec["id"], self.env),
                         {"session_file": "/x/s.jsonl", "live": True, "busy": True, "queue": 1,
                          "error": "earlier failure", "activity": "chat"})
        S.update_state(self.sdir(self.rec), lambda st: st.update(running={"run": 4, "pid": 5, "started": NOW}))
        self.assertEqual(S.conversation(self.rec["id"], self.env)["activity"], "run")

    def test_chat_command_validation(self):
        d = S.command_dir(self.env)
        for bad in ({"op": "chat", "id": self.rec["id"]}, {"op": "chat", "id": self.rec["id"], "text": " "},
                    {"op": "chat", "id": self.rec["id"], "text": "x" * 4001}, {"op": "chat", "text": "hi"},
                    {"op": "skip", "id": self.rec["id"], "text": "hi"}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                S.send_command(d, bad)
        S.send_command(d, {"op": "chat", "id": self.rec["id"], "text": "ok"})
        with open(os.path.join(d, "cmd.1-1.json"), "w") as f:
            json.dump({"op": "chat", "id": self.rec["id"], "text": 7}, f)
        with open(os.path.join(d, "cmd.2-1.json"), "w") as f:
            json.dump({"op": "skip", "id": self.rec["id"], "text": "no"}, f)
        self.assertEqual(S.read_commands(d), [{"op": "chat", "id": self.rec["id"], "text": "ok"}])

    def test_a_long_chat_command_file_is_read_a_long_skip_is_not(self):
        d = S.command_dir(self.env)
        S.send_command(d, {"op": "chat", "id": self.rec["id"], "text": "é" * 4000})
        self.assertEqual(len(S.read_commands(d)[0]["text"]), 4000)

    def test_agents_continue_their_session_unless_fresh_is_written(self):
        self.assertEqual(self.rec["identity"]["continuity"], "resume")
        self.assertEqual(self.add(name="Task")["identity"]["continuity"], "fresh")
        fresh = self.add(name="Fresh", kind="agent", identity={"continuity": "fresh"})
        self.assertEqual(fresh["identity"]["continuity"], "fresh")
        self.assertEqual(S.validate(self.rec)["identity"]["continuity"], "resume")


class CliBriefingAndChat(TempEnv):
    def call(self, *argv, now=NOW):
        import io
        out, err = io.StringIO(), io.StringIO()
        rc = S.main(list(argv), env=self.env, out=out, err=err, now=now)
        return rc, out.getvalue(), err.getvalue()

    def setUp(self):
        super().setUp()
        self.rec = self.add(name="CI watch", kind="agent")

    def test_briefing(self):
        self.assertIn("nothing important", self.call("briefing")[1])
        S.append_run(self.sdir(self.rec), {"run": 1, "schedule": self.rec["id"], "trigger": "cron", "status": "ok",
                                           "ended": NOW - 60, "level": "important", "headline": "CI fixed: flaky test"})
        rc, out, _ = self.call("briefing")
        self.assertEqual(rc, 0)
        self.assertRegex(out, r"CI watch\s+#1\s+1m ago\s+ok\s+NEW\s+CI fixed: flaky test")
        doc = json.loads(self.call("briefing", "--json")[1])
        self.assertEqual((doc[0]["bot_id"], doc[0]["headline"]), (self.rec["id"], "CI fixed: flaky test"))
        self.assertIn("marked 1 report(s) seen", self.call("briefing", "--mark-seen")[1])
        self.assertIn("nothing important", self.call("briefing")[1])
        self.assertIn("CI fixed", self.call("briefing", "--all")[1])

    def test_say_needs_the_daemon_then_sends(self):
        rc, _, err = self.call("say", "ci", "hello", "there")
        self.assertEqual((rc, err.strip()), (1, "hivegrid-schedule: scheduler not running"))
        S.write_summary(S.summary_path(self.env), S.build_summary(NOW, {"heartbeat": NOW}, env=self.env))
        rc, out, _ = self.call("say", "ci", "hello", "there")
        self.assertEqual((rc, out.strip()), (0, "sent to CI watch"))
        self.assertEqual(S.read_commands(S.command_dir(self.env)), [{"op": "chat", "id": self.rec["id"], "text": "hello there"}])
        S.update_state(self.sdir(self.rec), lambda st: st.update(running={"run": 1, "pid": 5, "started": NOW}))
        self.assertIn("queued as number 1", self.call("say", "ci", "later")[1])
        S.update_state(self.sdir(self.rec), lambda st: st.update(chat_queue=[{"text": "a", "ts": 1}] * 5))
        rc, _, err = self.call("say", "ci", "overflow")
        self.assertEqual(rc, 1)
        self.assertIn("5 messages waiting", err)

    def test_chat_prints_the_conversation_from_the_session_file(self):
        self.assertIn("no conversation yet", self.call("chat", "ci")[1])
        sess = os.path.join(self.home, ".omp", "agent", "sessions", "--w--", "20261102T090000Z_abc.jsonl")
        os.makedirs(os.path.dirname(sess))
        with open(sess, "w") as f:
            for msg in ({"role": "user", "content": [{"type": "text", "text": "how is CI?"}]},
                        {"role": "assistant", "content": [{"type": "toolCall", "id": "t1", "name": "bash", "arguments": {"command": "gh run list"}},
                                                          {"type": "text", "text": "All green."}]}):
                f.write(json.dumps({"type": "message", "id": "i%d" % len(msg), "timestamp": "2026-11-02T09:00:31.000Z", "message": msg}) + "\n")
        S.update_state(self.sdir(self.rec), lambda st: st.update(session_file=sess, chat_queue=[{"text": "a", "ts": 1}],
                                                                 chat={"active": False, "started": None, "error": "boom"}))
        rc, out, _ = self.call("chat", "ci")
        self.assertEqual(rc, 0)
        self.assertIn("you: how is CI?", out)
        self.assertIn("bash gh run list", out)
        self.assertIn("bot: All green.", out)
        self.assertIn("(1 message(s) waiting)", out)
        self.assertIn("(last turn failed: boom)", out)

    def test_logs_table_shows_level_and_headline(self):
        S.append_run(self.sdir(self.rec), {"run": 1, "schedule": self.rec["id"], "trigger": "cron", "status": "ok",
                                           "started": NOW - 60, "ended": NOW - 30, "level": "none", "headline": "CI green"})
        out = self.call("logs", "ci")[1]
        self.assertIn("LEVEL", out)
        self.assertRegex(out, r"ok\s+none\s+.*CI green")


if __name__ == "__main__":
    unittest.main()
