"""hivegrid-remote without sockets: token, checks, limiters, sanitizers, the mux and the request
pipeline (App.dispatch) against the fake mux of test_agents."""
import argparse
import contextlib
import io
import json
import os
import re
import shutil
import stat
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest
import unittest.mock
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import hivegrid_agents as A  # noqa: E402
import hivegrid_remote as R  # noqa: E402
from hivegrid_settings_core import ThemeWatch  # noqa: E402
from test_agents import DEAD_PID, NOW, Base, FakeMux, pane  # noqa: E402

TOKEN = "k" * 43


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeSys:
    """Stands in for D.SysStats: a fixed snapshot."""

    def snapshot(self):
        return {"ts": NOW, "cpu": 12.5, "cores": 4, "load": [0.5, 0.7, 0.9], "mem": {"used": 1.0, "total": 4.0, "pct": 25.0},
                "disk": None, "gpu": None, "uptime": 99.0, "history": []}


class TestSanitize(unittest.TestCase):
    def test_display_text_loses_escapes_and_controls(self):
        for raw, want in (
            ("plain", "plain"),
            ("\x1b[31mred\x1b[0m", "red"),
            ("a\x1b]0;title\x07b", "ab"),
            ("a\x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\b", "alinkb"),
            ("a\x1bb", "a"),
            ("a\x1b", "a"),
            ("a\x00b\x7fc\x9bd", "abcd"),
            ("a\u202eb\u2066c\u2069d", "abcd"),
            ("tab\there\nnew", "tab\there\nnew"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(R.sanitize_text(raw, 100), want)

    def test_non_strings_read_empty_and_long_text_is_cut(self):
        for v in (None, 5, b"x", ["a"]):
            self.assertEqual(R.sanitize_text(v, 10), "")
        self.assertEqual(R.sanitize_text("x" * 50, 10), "x" * 9 + "…")

    def test_a_line_is_one_line(self):
        self.assertEqual(R.sanitize_line("a\n b\t c", 50), "a b c")
        self.assertEqual(R.sanitize_line("  \tx\x1b[1m  y  ", 50, collapse=False), "      x  y")
        self.assertEqual(len(R.sanitize_line("y" * 900, 500, collapse=False)), 500)

    def test_prompts_keep_tabs_and_newlines_but_never_escapes(self):
        self.assertIsNone(R.validate_text("a\tb\nc", 100))
        for bad in ("a\x1bb", "\x1b[201~rm -rf", "a\rb", "a\x00", "a\x7f", "a\x85b"):
            with self.subTest(bad=bad):
                self.assertIn("control", R.validate_text(bad, 100))
        self.assertIn("empty", R.validate_text("  \n", 100))
        self.assertIn("longer", R.validate_text("x" * 101, 100))
        self.assertIn("string", R.validate_text(5, 100))
        self.assertIsNone(R.validate_text("", 100, allow_empty=True))


class TestBearer(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(R.parse_bearer("Bearer abc"), "abc")
        self.assertEqual(R.parse_bearer("bearer abc"), "abc")
        for bad in (None, "", "Bearer", "Bearer ", "Basic abc", "abc", "Bearer " + "x" * 300):
            with self.subTest(bad=bad):
                self.assertIsNone(R.parse_bearer(bad))


class TestToken(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_generated_tokens_are_long_and_url_safe(self):
        t = R.generate_token()
        self.assertEqual(len(t), 43)
        self.assertRegex(t, r"^[A-Za-z0-9_-]+$")
        self.assertNotEqual(t, R.generate_token())

    def test_file_and_directory_are_private(self):
        path = self.dir / "sub" / "remote-token"
        R.write_token(path, TOKEN)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(R.read_token(path), TOKEN)
        self.assertEqual([p.name for p in path.parent.iterdir()], ["remote-token"], "no tmp file is left")

    def test_unusable_files_are_refused(self):
        path = self.dir / "t"
        with self.assertRaisesRegex(R.TokenError, "no token file"):
            R.read_token(path)
        R.write_token(path, TOKEN)
        os.chmod(path, 0o644)
        with self.assertRaisesRegex(R.TokenError, "chmod 600"):
            R.read_token(path)
        os.chmod(path, 0o600)
        link = self.dir / "link"
        link.symlink_to(path)
        with self.assertRaisesRegex(R.TokenError, "symlink"):
            R.read_token(link)
        for bad in ("short", "a" * 31, "a" * 40 + "!", ""):
            with self.subTest(bad=bad):
                R.write_token(path, "x")
                path.write_text(bad)
                os.chmod(path, 0o600)
                with self.assertRaisesRegex(R.TokenError, "does not hold a token"):
                    R.read_token(path)
        with self.assertRaisesRegex(R.TokenError, "regular file"):
            R.read_token(self.dir)

    def test_check_is_exact_and_a_rotation_is_live(self):
        path = self.dir / "t"
        R.write_token(path, TOKEN)
        store = R.TokenStore(path)
        self.assertTrue(store.check(TOKEN))
        for bad in ("", "x", TOKEN + "x", TOKEN[:-1], "k" * 300, None):
            self.assertFalse(store.check(bad), bad)
        new = R.generate_token()
        R.write_token(path, new)
        self.assertFalse(store.check(TOKEN), "the old token dies without a restart")
        self.assertTrue(store.check(new))

    def test_a_broken_file_locks_everybody_out_and_a_fixed_one_lets_in(self):
        path = self.dir / "t"
        R.write_token(path, TOKEN)
        store = R.TokenStore(path)
        self.assertTrue(store.check(TOKEN))
        os.chmod(path, 0o644)
        self.assertFalse(store.check(TOKEN))
        self.assertIn("chmod", store.error)
        os.chmod(path, 0o600)
        self.assertTrue(store.check(TOKEN))

    def test_load_raises_for_the_startup_check(self):
        with self.assertRaises(R.TokenError):
            R.TokenStore(self.dir / "none").load()

    def test_default_path_follows_the_settings_dir(self):
        with unittest.mock.patch.dict(os.environ, {"HIVEGRID_SETTINGS": str(self.dir / "cfg" / "settings.json")}):
            os.environ.pop("HIVEGRID_REMOTE_TOKEN_FILE", None)
            self.assertEqual(R.default_token_path(), str(self.dir / "cfg" / "remote-token"))
        self.assertEqual(R.default_token_path({"HIVEGRID_REMOTE_TOKEN_FILE": "/x/t"}), "/x/t")


class TestChecks(unittest.TestCase):
    def test_host_allow_list(self):
        for host in ("127.0.0.1:8787", "localhost", "[::1]:80", "box.tail1234.ts.net", "BOX.ts.net:443", "mine.test"):
            with self.subTest(host=host):
                self.assertTrue(R.host_allowed(host, ("mine.test",)))
        for host in (None, "", "evil.example", "127.0.0.1.evil.example", "ts.net.evil.example", "a b", "x:y:z", "[::1", "h:port",
                     "evil.example:80", "mine.test.evil.example"):
            with self.subTest(host=host):
                self.assertFalse(R.host_allowed(host, ("mine.test",)))

    def test_origin_and_fetch_site(self):
        host = "box.ts.net"
        self.assertTrue(R.origin_ok("https://box.ts.net", host))
        self.assertTrue(R.origin_ok(None, host))
        self.assertTrue(R.origin_ok(None, host, "same-origin"))
        self.assertTrue(R.origin_ok(None, host, "none"))
        self.assertFalse(R.origin_ok("https://evil.example", host))
        self.assertFalse(R.origin_ok("null", host))
        self.assertFalse(R.origin_ok("https://box.ts.net:444", host))
        self.assertFalse(R.origin_ok(None, host, "cross-site"))
        self.assertFalse(R.origin_ok(None, host, "same-site"))

    def test_bind(self):
        for ok in ("127.0.0.1", "::1", "100.64.1.2", "100.127.255.254", "fd7a:115c:a1e0::1", "localhost"):
            R.check_bind(ok)
        for bad in ("0.0.0.0", "::", "192.168.1.5", "100.128.0.1", "example.com", "10.0.0.1"):
            with self.subTest(addr=bad):
                with self.assertRaises(ValueError):
                    R.check_bind(bad)
                R.check_bind(bad, allow_any=True)


class TestLimits(unittest.TestCase):
    def test_lockout_doubles_and_recovers(self):
        c = Clock()
        lim = R.AuthLimiter(c)
        for _ in range(4):
            lim.fail("a")
        self.assertEqual(lim.blocked("a"), 0)
        lim.fail("a")
        self.assertAlmostEqual(lim.blocked("a"), 60)
        self.assertEqual(lim.blocked("b"), 0, "another client is not locked")
        c.t += 61
        self.assertEqual(lim.blocked("a"), 0)
        for _ in range(5):
            lim.fail("a")
        self.assertAlmostEqual(lim.blocked("a"), 120)
        c.t += 121
        self.assertEqual(lim.blocked("a"), 0)

    def test_failures_expire_and_success_clears(self):
        c = Clock()
        lim = R.AuthLimiter(c)
        for _ in range(4):
            lim.fail("a")
        c.t += 61
        lim.fail("a")
        self.assertEqual(lim.blocked("a"), 0, "old failures do not count")
        for _ in range(3):
            lim.fail("a")
        lim.ok("a")
        lim.fail("a")
        lim.fail("a")
        self.assertEqual(lim.blocked("a"), 0)

    def test_global_backstop(self):
        lim = R.AuthLimiter(Clock(), global_max=50)
        for i in range(50):
            lim.fail(f"k{i}")
        self.assertGreater(lim.blocked("fresh"), 0)

    def test_fail_reports_a_new_lock_and_idle_keys_are_pruned(self):
        c = Clock()
        lim = R.AuthLimiter(c)
        self.assertEqual([lim.fail("a") for _ in range(5)], [0, 0, 0, 0, 60])
        for i in range(1100):
            lim.fail(f"old{i}")
        c.t += 61
        lim.fail("trigger")
        self.assertLessEqual(len(lim.keys), 2, "keys with no failure inside the window and no lock are dropped")

    def test_a_success_does_not_lift_an_active_lock(self):
        c = Clock()
        lim = R.AuthLimiter(c)
        for _ in range(5):
            lim.fail("a")
        lim.ok("a")
        self.assertAlmostEqual(lim.blocked("a"), 60)
        c.t += 61
        lim.ok("a")
        self.assertEqual(lim.blocked("a"), 0)

    def test_bucket(self):
        c = Clock()
        b = R.Bucket(2.0, 3, c)
        self.assertEqual([b.take() for _ in range(3)], [0, 0, 0])
        self.assertAlmostEqual(b.take(), 0.5)
        c.t += 0.5
        self.assertEqual(b.take(), 0)
        c.t += 100
        self.assertEqual([b.take() for _ in range(3)], [0, 0, 0], "refill stops at the burst")
        self.assertGreater(b.take(), 0)


class TestAgentModel(unittest.TestCase):
    def ag(self, **kw):
        d = dict(pane=3, tab=0, window=0, cwd="/x", title="T", state="idle", num=2, tab_index=1)
        d.update(kw)
        return A.Agent(**d)

    def test_label(self):
        a, b = self.ag(), self.ag(pane=4, tab=1, num=0, tab_index=2)
        self.assertEqual(A.agent_label(a, [a]), "2")
        self.assertEqual(A.agent_label(b, [b]), "4", "no number: the pane id, as the dock does")
        self.assertEqual(A.agent_label(a, [a, b]), "1·2")
        self.assertEqual(A.agent_label(b, [a, b]), "2·4")

    def test_can_flags_follow_the_state(self):
        for state, want in (("working", (True, True, False, False)), ("busy", (True, True, False, False)),
                            ("done", (True, True, False, True)), ("idle", (True, True, False, True)),
                            ("attention", (False, True, False, False)), ("exited", (False, False, False, False))):
            a = self.ag(state=state)
            can = R.agent_json(a, [a], NOW)["can"]
            self.assertEqual((can["prompt"], can["keys"], can["plan"], can["ack"]), want, state)
            self.assertEqual(set(R.agent_json(a, [a], NOW, readonly=True)["can"].values()), {False}, state)

    def test_plan_review_enables_plan(self):
        a = self.ag(state="attention", pending={"kind": "plan_review", "reviewId": "r", "title": "t", "choices": []})
        j = R.agent_json(a, [a], NOW)
        self.assertTrue(j["can"]["plan"])
        self.assertNotIn("reviewId", json.dumps(j))
        self.assertEqual([c["id"] for c in j["pending"]["choices"]], list(A.PLAN_CHOICES), "omp 18 sends none: the fixed set")

    def test_pending_is_whitelisted_sanitized_and_capped(self):
        ap = R.pending_json({"kind": "approval", "tool": "ba\x1b[1msh", "summary": "x" * 500, "secret": "no"})
        self.assertEqual(ap, {"kind": "approval", "tool": "bash", "summary": "x" * 199 + "…"})
        ask = R.pending_json({"kind": "ask", "question": "q\x1b]0;x\x07?", "options": ["a"] * 20 + [5]})
        self.assertEqual(ask["question"], "q?")
        self.assertEqual(len(ask["options"]), 8)
        plan = R.pending_json({"kind": "plan_review", "title": "T", "choices": [
            {"id": "execute", "label": "Go\x1b[0m"}, {"id": "rm -rf", "label": "x"}, "junk"]})
        self.assertEqual(plan["choices"], [{"id": "execute", "label": "Go"}])
        for junk in (None, "x", {"kind": "other"}, {}):
            self.assertIsNone(R.pending_json(junk))

    def test_fields(self):
        a = self.ag(title="📂 fix \x1b[1mlogin", cwd=os.path.expanduser("~") + "/proj/", since=NOW - 125, reason="ok\nline",
                    usage=A.Usage(1_300_000, 4.82, "reported"), profile="alpha", active=True)
        j = R.agent_json(a, [a], NOW)
        self.assertEqual((j["id"], j["label"], j["num"], j["tab"], j["rank"]), (3, "2", 2, 1, 7))
        self.assertEqual((j["title"], j["cwd"], j["age"], j["reason"]), ("fix login", "~/proj", "2m", "ok line"))
        self.assertEqual(j["usage"], {"text": "1.3M tok · $4.82", "tokens": 1_300_000, "cost": 4.82, "cost_kind": "reported"})
        self.assertEqual((j["state_label"], j["active"], j["profile"]), ("idle", True, "alpha"))

    def test_list_is_ordered_and_hashed_without_ages(self):
        a = self.ag(pane=5, num=2, tab_index=1, state="working", since=NOW - 5)
        b = self.ag(pane=6, num=1, tab_index=2, state="attention", since=NOW - 5)
        c = self.ag(pane=4, num=1, tab_index=1, state="done", since=NOW - 5, usage=A.Usage(10, 0.5, "reported"))
        j = R.agents_json([a, b, c], NOW)
        self.assertEqual([r["id"] for r in j["agents"]], [4, 5, 6])
        self.assertEqual(j["totals"]["by_state"], {"done": 1, "working": 1, "attention": 1})
        self.assertEqual(j["totals"]["agents"], 3)
        self.assertEqual(j["totals"]["usage"], "10 tok · $0.50")
        self.assertEqual(j["rev"], R.agents_json([a, b, c], NOW + 1)["rev"])
        self.assertNotEqual(j["rev"], R.agents_json([a, b], NOW)["rev"])

    def test_the_revision_has_no_age_in_it(self):
        young, older = self.ag(since=NOW - 1), self.ag(since=NOW - 65)
        for agent in (young, older):
            self.assertEqual({R.agents_json([agent], NOW + s)["rev"] for s in (0, 3, 4, 50, 55, 600)}, {R.agents_json([agent], NOW)["rev"]})
        self.assertEqual(R.agents_json([older], NOW)["agents"][0]["age"], "1m")
        self.assertEqual(R.agents_json([older], NOW + 600)["agents"][0]["age"], "11m", "the age text still moves")
        self.assertIsNone(R.agents_json([self.ag(since=None)], NOW)["agents"][0]["age"])

    def test_model_thinking_context_session_fields(self):
        a = self.ag(model={"id": "p/m", "name": "M\x1b[1m", "provider": "p"}, thinking="high",
                    context={"tokens": 5.0, "window": 10.0, "pct": 50.0})
        j = R.agent_json(a, [a], NOW, session=True)
        self.assertEqual((j["model"], j["thinking"], j["context"], j["session"]),
                         ({"id": "p/m", "name": "M", "provider": "p"}, "high", {"tokens": 5.0, "window": 10.0, "pct": 50.0}, True))
        self.assertTrue(j["can"]["model"])
        bare = R.agent_json(self.ag(), [self.ag()], NOW)
        self.assertEqual((bare["model"], bare["thinking"], bare["context"], bare["session"], bare["can"]["model"]),
                         (None, None, None, False, False), "an older extension: no model controls")
        self.assertFalse(R.agent_json(a, [a], NOW, readonly=True)["can"]["model"])
        self.assertFalse(R.agent_json(self.ag(state="exited", model=a.model), [a], NOW)["can"]["model"])
        rows = R.agents_json([a], NOW, sessions=lambda x: x is a)["agents"]
        self.assertTrue(rows[0]["session"])
        self.assertNotEqual(R.agents_json([a], NOW)["rev"], R.agents_json([a], NOW, sessions=lambda x: True)["rev"])

    def test_meta(self):
        m = R.meta_json(False)
        self.assertEqual(set(m["states"]), set(R.VOCAB))
        self.assertEqual(m["states"]["attention"], {"glyph": "?", "label": "needs you", "color": "violet", "rank": 1})
        self.assertEqual(m["dangerous"], ["ctrl-c", "ctrl-d"])
        self.assertNotEqual(m["meta_rev"], R.meta_json(True)["meta_rev"])
        self.assertEqual(m["capabilities"], ["chat", "accounts", "system", "sessions", "model", "longpoll"])
        self.assertEqual((m["limits"]["chat_max"], m["limits"]["wait_max"]), (200, 20))


class TestTail(unittest.TestCase):
    def test_rows_are_clean_and_trimmed(self):
        text = "\x1b[32mone\x1b[0m\n  two\ttab\n\n\x1b]0;x\x07three  \n\n \n"
        self.assertEqual(R.tail_lines(text, 60), ["one", "  two    tab", "", "three"])
        self.assertEqual(R.tail_lines(text, 2), ["", "three"])
        self.assertEqual(R.tail_lines("", 5), [])

    def test_caps(self):
        self.assertEqual(len(R.tail_lines("y" * 2000, 5)[0]), R.TAIL_LINE)
        rows = R.tail_lines("\n".join("z" * 400 for _ in range(400)), 400)
        self.assertLessEqual(sum(len(r.encode()) + 1 for r in rows), R.TAIL_BYTES)
        self.assertGreater(len(rows), 100)


class TestTheme(unittest.TestCase):
    def test_only_valid_hex_survives(self):
        css = R.theme_css({"deep": "#112233", "text": "red;}body{x:y", "accent": 5, "cyan": "#12345"})
        self.assertIn("--deep: #112233;", css)
        self.assertIn(f"--text: {R.U.DEFAULT_PAL['text']};", css)
        self.assertIn(f"--accent: {R.U.DEFAULT_PAL['accent']};", css)
        self.assertIn(f"--cyan: {R.U.DEFAULT_PAL['cyan']};", css)
        self.assertEqual(len(re.findall(r"--[a-z-]+: #[0-9a-f]{6};", css)), len(R.THEME_VARS))
        self.assertNotIn("body{", css)
        self.assertIn("--edge-hi:", css)
        self.assertEqual(R.theme_css(None).count("--"), len(R.THEME_VARS))


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.bin = self.dir / "opt" / "bin"
        self.bin.mkdir(parents=True)
        (self.bin / "wezterm").write_text("")
        self.gui = str(self.bin / "wezterm-gui")
        self.run_dir = self.dir / "run"
        self.run_dir.mkdir()
        self.exes = {}

    def tearDown(self):
        self.tmp.cleanup()

    def sock(self, pid, mtime, exe=None):
        p = self.run_dir / f"gui-sock-{pid}"
        p.write_text("")
        os.utime(p, (mtime, mtime))
        self.exes[pid] = exe or self.gui
        return str(p)

    def find(self, dead=()):
        return R.discover_gui_socket(str(self.run_dir), proc_exe=self.exes.get, alive=lambda p: p not in dead)

    def test_newest_live_gui_wins_and_the_cli_is_its_sibling(self):
        self.sock(111, 100)
        newest = self.sock(222, 200)
        self.assertEqual(self.find(), (newest, str(self.bin / "wezterm")))

    def test_dead_and_foreign_processes_are_skipped(self):
        old = self.sock(111, 100)
        self.sock(222, 200)
        self.sock(333, 300, exe="/usr/bin/sleep")
        self.assertEqual(self.find(dead=(222,))[0], old)
        self.assertEqual(self.find()[0], str(self.run_dir / "gui-sock-222"))
        self.assertIsNone(self.find(dead=(111, 222)))

    def test_a_published_link_beats_age(self):
        first = self.sock(111, 100)
        self.sock(222, 200)
        (self.run_dir / "x11-0-test").symlink_to("gui-sock-111")
        self.assertEqual(self.find()[0], first)
        (self.run_dir / "x11-0-test").unlink()
        (self.run_dir / "wayland-test").symlink_to("gui-sock-gone")
        self.assertEqual(self.find()[0], str(self.run_dir / "gui-sock-222"), "a link to nothing live is ignored")

    def test_no_directory_no_sockets(self):
        self.assertIsNone(R.discover_gui_socket(str(self.dir / "none")))
        self.assertIsNone(self.find())

    def test_exe_without_sibling_is_used_itself(self):
        (self.bin / "wezterm").unlink()
        self.sock(111, 100)
        self.assertEqual(self.find()[1], self.gui)

    def test_runtime_dir(self):
        self.assertEqual(R.runtime_dir({"XDG_RUNTIME_DIR": "/run/user/1"}), "/run/user/1/wezterm")
        with unittest.mock.patch.dict(os.environ, {"HOME": str(self.dir)}):
            self.assertEqual(R.runtime_dir({}), str(self.dir / ".local" / "share" / "wezterm"))


class TestCliRunner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.exe = os.path.join(self.tmp.name, "bin", "wezterm")
        os.makedirs(os.path.join(self.tmp.name, "bin", "lib"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_command_line_and_environment(self):
        calls = []

        def runner(argv, **kw):
            calls.append((argv, kw))
            return subprocess.CompletedProcess(argv, 0, "out", "")
        with unittest.mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": "/old"}):
            run = R.cli_runner(self.exe, "/s/sock", runner=runner)
            self.assertEqual(run("list", "--format", "json"), "out")
        argv, kw = calls[0]
        self.assertEqual(argv, [self.exe, "cli", "list", "--format", "json"])
        self.assertEqual(kw["timeout"], 4)
        self.assertEqual(kw["env"]["WEZTERM_UNIX_SOCKET"], "/s/sock")
        self.assertEqual(kw["env"]["LD_LIBRARY_PATH"], os.path.join(self.tmp.name, "bin", "lib") + os.pathsep + "/old")
        self.assertTrue(kw["capture_output"] and kw["text"])

    def test_no_lib_dir_leaves_the_library_path(self):
        os.rmdir(os.path.join(self.tmp.name, "bin", "lib"))
        calls = []
        with unittest.mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": "/old"}):
            R.cli_runner(self.exe, "/s", runner=lambda a, **k: calls.append(k) or subprocess.CompletedProcess(a, 0, "", ""))("x")
        self.assertEqual(calls[0]["env"]["LD_LIBRARY_PATH"], "/old")

    def test_failures_read_as_none(self):
        def boom(exc):
            def runner(argv, **kw):
                raise exc
            return runner
        self.assertIsNone(R.cli_runner(self.exe, "/s", runner=boom(OSError()))("x"))
        self.assertIsNone(R.cli_runner(self.exe, "/s", runner=boom(subprocess.TimeoutExpired("x", 4)))("x"))
        self.assertIsNone(R.cli_runner(self.exe, "/s", runner=lambda a, **k: subprocess.CompletedProcess(a, 1, "out", ""))("x"))


class MuxCase(Base):
    """Base (temp XDG_RUNTIME_DIR for the state files) plus a fake mux behind a real `Mux`."""

    def setUp(self):
        super().setUp()
        self.clock = Clock()
        self.fake = FakeMux([pane(3, "π > ready")])
        self.located = 0
        self.write_state(3, state="idle")

    def locate(self):
        self.located += 1
        return "/sock", "/exe"

    def mux(self, **kw):
        return R.Mux(locate=self.locate, make_run=lambda exe, sock: self.fake, now=lambda: NOW, fg=lambda: {},
                     clock=self.clock, exists=lambda p: True, **kw)

    def lists(self):
        return sum(1 for c in self.fake.calls if c[0] == "list")


class TestMux(MuxCase):
    def test_agents_and_cache(self):
        m = self.mux()
        self.assertEqual([a.pane for a in m.agents()], [3])
        m.agents()
        self.assertEqual(self.lists(), 1, "the second read within the ttl is cached")
        self.clock.t += 2
        m.agents()
        self.assertEqual(self.lists(), 2)
        m.agents(fresh=True)
        self.assertEqual(self.lists(), 3)
        m.agents()
        self.assertEqual(self.lists(), 3)
        m.invalidate()
        m.agents()
        self.assertEqual(self.lists(), 4)

    def test_a_failed_list_is_retried_once_with_a_new_look(self):
        n = [0]

        def make_run(exe, sock):
            def run(*a):
                if a[0] == "list":
                    n[0] += 1
                    if n[0] == 1:
                        return None
                return self.fake(*a)
            return run
        m = R.Mux(locate=self.locate, make_run=make_run, now=lambda: NOW, fg=lambda: {}, clock=self.clock, exists=lambda p: True)
        self.assertEqual([a.pane for a in m.agents()], [3])
        self.assertEqual((n[0], self.located), (2, 2))
        self.fake.panes = None      # every list answers garbage from now on: one retry, then NoMux
        self.clock.t += 5
        before = self.located
        with self.assertRaises(R.NoMux):
            m.agents()
        self.assertEqual(self.located, before + 1)

    def test_the_list_ttl_is_a_second_and_a_half(self):
        self.assertEqual(R.LIST_TTL, 1.5)
        m = self.mux()
        m.agents()
        self.clock.t += 1.4
        m.agents()
        self.assertEqual(self.lists(), 1)
        self.clock.t += 0.2
        m.agents()
        self.assertEqual(self.lists(), 2)

    def test_act_runs_under_the_collect_lock_and_returns_the_result(self):
        m = self.mux()
        seen = []
        self.assertEqual(m.act(lambda: seen.append(m._lock.locked()) or 42), 42)
        self.assertEqual(seen, [True])
        self.assertFalse(m._lock.locked())

    def test_a_failed_list_is_no_mux_not_no_agents(self):
        self.fake.replies["list"] = None
        self.fake.panes = None
        m = R.Mux(locate=self.locate, make_run=lambda e, s: (lambda *a: None), now=lambda: NOW, fg=lambda: {}, clock=self.clock,
                  exists=lambda p: True)
        with self.assertRaises(R.NoMux):
            m.agents()
        m2 = R.Mux(locate=self.locate, make_run=lambda e, s: (lambda *a: "garbage"), now=lambda: NOW, fg=lambda: {}, clock=self.clock,
                   exists=lambda p: True)
        with self.assertRaises(R.NoMux):
            m2.agents()
        self.fake.panes = []
        self.assertEqual(self.mux().agents(), [], "an empty window is fine")

    def test_no_gui_is_no_mux(self):
        m = R.Mux(locate=lambda: None, fg=lambda: {}, clock=self.clock)
        with self.assertRaises(R.NoMux):
            m.agents()
        self.assertIsNone(m.run("list"))

    def test_the_socket_is_found_again_after_a_failure_or_a_vanished_socket(self):
        m = self.mux()
        m.agents()
        m.agents(fresh=True)
        self.assertEqual(self.located, 1)
        self.fake.replies["get-text"] = None
        with self.assertRaises(R.MuxFailed):
            m.tail(3, 10)
        m.agents(fresh=True)
        self.assertEqual(self.located, 2, "a failed call triggers a new look")
        gone = R.Mux(locate=self.locate, make_run=lambda e, s: self.fake, now=lambda: NOW, fg=lambda: {}, clock=self.clock,
                     exists=lambda p: False)
        before = self.located
        gone.agents()
        gone.agents(fresh=True)
        self.assertEqual(self.located, before + 2)

    def test_tail_and_keys_calls(self):
        m = self.mux()
        self.fake.replies["get-text"] = "hi\n"
        self.assertEqual(m.tail(3, 60), "hi\n")
        self.assertEqual(self.fake.calls[-1], ("get-text", "--pane-id", "3", "--start-line", "-60"))
        self.assertEqual(m.send_keys(3, ["\x1b", "y"], "txt"), 3)
        self.assertEqual(self.fake.calls[-3:], [("send-text", "--pane-id", "3", "--", "txt"),
                                                ("send-text", "--pane-id", "3", "--no-paste", "\x1b"),
                                                ("send-text", "--pane-id", "3", "--no-paste", "y")])


class AppCase(MuxCase):
    def setUp(self):
        super().setUp()
        self.logs = []
        self.tick = 0.6   # seconds the fake clock moves after each call: the limits stay out of the way
        self.settings = os.path.join(self.tmp.name, "settings.json")
        self.tokfile = os.path.join(self.tmp.name, "token")
        R.write_token(self.tokfile, TOKEN)
        # nothing reads the real home: accounts, quota caches, fonts and the monitor log live in the temp dir
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(self.home)
        env = unittest.mock.patch.dict(os.environ, {"HOME": self.home, "CLAUDE_QUOTA_CACHE_DIR": os.path.join(self.tmp.name, "quota"),
                                                    "XDG_CACHE_HOME": os.path.join(self.tmp.name, "cache")})
        env.start()
        self.addCleanup(env.stop)
        self.slept = []
        self.on_sleep = None     # called after each fake sleep with the number of sleeps so far
        self.sysstats = FakeSys()
        self.fonts = {"/fonts/mono.ttf": (os.path.join(self.tmp.name, "mono.ttf"),),
                      "/fonts/mono-bold.ttf": (os.path.join(self.tmp.name, "mono-bold.ttf"),)}
        self.app = self.make_app()

    def sleep(self, seconds):
        """The held requests' wait: time passes on the fake clock, then the test may change the world."""
        self.slept.append(seconds)
        self.clock.t += seconds
        if self.on_sleep:
            self.on_sleep(len(self.slept))
        return False

    def make_app(self, **kw):
        kw.setdefault("sleep", self.sleep)
        kw.setdefault("home", self.home)
        kw.setdefault("sysstats", self.sysstats)
        kw.setdefault("fonts", self.fonts)
        return R.App(self.mux(), R.TokenStore(self.tokfile), clock=self.clock, wall=lambda: NOW, log=self.logs.append,
                     theme_watch=ThemeWatch(self.settings), **kw)

    def call(self, method, path, body=None, token=TOKEN, headers=None, host="127.0.0.1:8787", app=None):
        raw = json.dumps(body).encode() if body is not None else b""
        h = {"host": host}
        if token:
            h["authorization"] = "Bearer " + token
        if method == "POST":
            h.update({"content-type": "application/json", "x-hivegrid-remote": "1", "content-length": str(len(raw))})
        for k, v in (headers or {}).items():
            if v is None:
                h.pop(k, None)
            else:
                h[k] = v
        resp = (app or self.app).dispatch(R.Request(method, path, h, "127.0.0.1", lambda n: raw[:n]))
        self.clock.t += self.tick
        data = json.loads(resp.body) if resp.body and resp.content_type == "application/json" else None
        return resp, data

    def sends(self):
        return [c for c in self.fake.calls if c[0] == "send-text"]


class TestAuth(AppCase):
    def test_tokens(self):
        self.tick = 15
        self.assertEqual(self.call("GET", "/api/meta")[0].status, 200)
        for token, headers in ((None, {}), ("wrong", {}), ("", {"authorization": "Bearer "}), (None, {"authorization": "Basic " + TOKEN}),
                               (TOKEN + "x", {}), ("k" * 300, {}), (None, {"authorization": TOKEN})):
            with self.subTest(token=token, headers=headers):
                r, data = self.call("GET", "/api/meta", token=token, headers=headers)
                self.assertEqual((r.status, data["error"]), (401, "unauthorized"))
                self.assertEqual(r.headers["WWW-Authenticate"], "Bearer")
        self.assertEqual(self.call("GET", "/api/meta", token=None, headers={"authorization": "bearer " + TOKEN})[0].status, 200)

    def test_rotation_needs_no_restart(self):
        self.assertEqual(self.call("GET", "/api/meta")[0].status, 200)
        new = R.generate_token()
        R.write_token(self.tokfile, new)
        self.assertEqual(self.call("GET", "/api/meta")[0].status, 401)
        self.assertEqual(self.call("GET", "/api/meta", token=new)[0].status, 200)

    def test_a_valid_token_is_never_locked_out(self):
        self.tick = 0
        for _ in range(5):
            self.assertEqual(self.call("GET", "/api/meta", token="bad")[0].status, 401)
        r, data = self.call("GET", "/api/meta", token="bad")
        self.assertEqual((r.status, data["error"]), (429, "rate_limited"))
        self.assertEqual(r.headers["Retry-After"], "60")
        self.assertEqual(self.call("GET", "/api/meta")[0].status, 200, "the right token passes the lock")
        self.assertEqual(self.call("GET", "/api/meta", token="bad")[0].status, 429, "and does not lift it")
        self.clock.t += 61
        self.assertEqual(self.call("GET", "/api/meta", token="bad")[0].status, 401)

    def test_a_valid_token_passes_the_global_backstop(self):
        self.tick = 0
        for i in range(50):
            self.app.auth_limit.fail(f"k{i}")
        self.assertEqual(self.call("GET", "/api/meta", token="bad")[0].status, 429)
        self.assertEqual(self.call("GET", "/api/meta")[0].status, 200)

    def test_failures_and_lockouts_are_logged_without_the_token(self):
        self.tick = 0
        for _ in range(5):
            self.call("GET", "/api/meta", token="guess-secret-1")
        text = "\n".join(self.logs)
        self.assertEqual(text.count("auth failed key=127.0.0.1||"), 5)
        self.assertIn("locked for 60s", text)
        self.assertNotIn("guess-secret", text)

    def test_forwarding_headers_count_only_from_loopback(self):
        def key(peer, **h):
            return self.app._client_key(R.Request("GET", "/", {k.replace("_", "-"): v for k, v in h.items()}, peer))
        spoof = {"x_forwarded_for": "9.9.9.9", "tailscale_user_login": "a@b"}
        self.assertEqual(key("203.0.113.5", **spoof), "203.0.113.5")
        self.assertEqual(key("127.0.0.1", **spoof), "127.0.0.1|9.9.9.9|a@b")
        self.assertEqual(key("127.0.0.1", x_forwarded_for="9.9.9.9, 1.1.1.1"), "127.0.0.1|9.9.9.9|")
        self.assertEqual(key("::1"), "::1||")
        self.assertEqual(key("::ffff:127.0.0.1", x_forwarded_for="8.8.8.8"), "::ffff:127.0.0.1|8.8.8.8|")
        self.assertEqual(key("100.64.1.2", x_forwarded_for="8.8.8.8"), "100.64.1.2")

    def test_a_missing_header_is_no_guess(self):
        for _ in range(8):
            self.assertEqual(self.call("GET", "/api/meta", token=None)[0].status, 401)
        self.assertEqual(self.call("GET", "/api/meta")[0].status, 200)

    def test_read_and_write_buckets(self):
        self.tick = 0
        for _ in range(30):
            self.assertEqual(self.call("GET", "/api/meta")[0].status, 200)
        r, data = self.call("GET", "/api/meta")
        self.assertEqual((r.status, data["error"]), (429, "rate_limited"))
        self.assertGreaterEqual(int(r.headers["Retry-After"]), 1)
        self.clock.t += 5
        self.assertEqual(self.call("GET", "/api/meta")[0].status, 200)

    def test_host_checks_come_first_and_cover_static_files(self):
        for path in ("/", "/healthz", "/api/meta"):
            r, data = self.call("GET", path, host="evil.example")
            self.assertEqual((r.status, data["error"]), (403, "forbidden_host"), path)
        self.assertEqual(self.call("GET", "/healthz", host="box.tail.ts.net")[0].status, 200)
        self.assertEqual(self.call("GET", "/healthz", host="mine.test", app=self.make_app(allow_hosts=("Mine.test",)))[0].status, 200)

    def test_methods_and_routes(self):
        for m in ("PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"):
            self.assertEqual(self.call(m, "/api/meta")[0].status, 405, m)
        self.assertEqual(self.call("POST", "/api/meta", {})[0].status, 405)
        self.assertEqual(self.call("GET", "/api/agents/3/prompt")[0].status, 405)
        self.assertEqual(self.call("POST", "/healthz", {})[0].status, 405)
        for path in ("/nope", "/api/nope", "/api/agents/3/nope", "/api/agents/3", "/api/agents/3/tail/x"):
            self.assertEqual(self.call("GET", path)[0].status, 404, path)
        self.assertEqual(self.call("GET", "http://evil/api/meta")[0].status, 400)


class TestStatic(AppCase):
    def test_pages_need_no_token_and_cache_by_etag(self):
        for path, ctype in (("/", "text/html"), ("/app.js", "text/javascript"), ("/app.css", "text/css"),
                            ("/theme.css", "text/css"), ("/manifest.webmanifest", "application/manifest+json"), ("/healthz", "text/plain")):
            r, _ = self.call("GET", path, token=None)
            self.assertEqual(r.status, 200, path)
            self.assertTrue(r.content_type.startswith(ctype), path)
            self.assertEqual(r.headers["Cache-Control"], "no-cache")
            r2, _ = self.call("GET", path, token=None, headers={"if-none-match": r.headers["ETag"]})
            self.assertEqual((r2.status, r2.body), (304, b""), path)
        self.assertEqual(self.call("GET", "/healthz", token=None)[0].body, b"ok")
        self.assertEqual(self.call("GET", "/app.js?x=1", token=None)[0].body, R.UI.APP_JS.encode())

    def test_theme_follows_the_saved_settings(self):
        first = self.call("GET", "/theme.css", token=None)[0].body.decode()
        self.assertIn(f"--accent: {R.U.DEFAULT_PAL['accent']};", first)
        Path(self.settings).write_text(json.dumps({"theme": {"accent": "#123456"}}))
        second = self.call("GET", "/theme.css", token=None)[0]
        self.assertIn("--accent: #123456;", second.body.decode())
        self.assertNotEqual(second.headers["ETag"], self.call("GET", "/", token=None)[0].headers["ETag"])


class TestApi(AppCase):
    def test_agents_list_etag(self):
        r, data = self.call("GET", "/api/agents")
        self.assertEqual(r.status, 200)
        self.assertEqual([a["id"] for a in data["agents"]], [3])
        self.assertEqual(data["agents"][0]["state"], "idle")
        self.assertEqual(r.headers["ETag"], f'"{data["rev"]}"')
        r2, _ = self.call("GET", "/api/agents", headers={"if-none-match": r.headers["ETag"]})
        self.assertEqual((r2.status, r2.body), (304, b""))
        self.write_state(3, state="working")
        self.clock.t += 1
        r3, data3 = self.call("GET", "/api/agents", headers={"if-none-match": r.headers["ETag"]})
        self.assertEqual((r3.status, data3["agents"][0]["state"]), (200, "working"))

    def test_no_gui_is_503(self):
        app = R.App(R.Mux(locate=lambda: None, fg=lambda: {}, clock=self.clock), R.TokenStore(self.tokfile), clock=self.clock,
                    log=self.logs.append)
        for method, path, body in (("GET", "/api/agents", None), ("GET", "/api/agents/3/tail", None),
                                   ("POST", "/api/agents/3/ack", {}), ("POST", "/api/agents/3/prompt", {"text": "x"})):
            r, data = self.call(method, path, body, app=app)
            self.assertEqual((r.status, data["error"]), (503, "no_mux"), path)
        self.assertEqual(self.call("GET", "/api/meta", app=app)[0].status, 200)

    def test_ids_must_be_agents(self):
        self.fake.panes = [pane(3, "π > ready"), pane(9, "bash"), pane(10, "hivegrid-bar", tab=1)]
        for ident in ("007", "-1", "1e3", "99999999999", "5", "9", "10", "x", "0"):
            for method, tail, body in (("GET", "tail", None), ("POST", "ack", {}), ("POST", "prompt", {"text": "x"})):
                r, data = self.call(method, f"/api/agents/{ident}/{tail}", body)
                self.assertEqual((r.status, data["error"]), (404, "not_found"), (ident, tail))
        self.assertEqual(self.call("GET", "/api/agents/3/tail")[0].status, 200)

    def test_tail(self):
        self.fake.replies["get-text"] = "\x1b[1mhello\x1b[0m\n<img src=x onerror=alert(1)>\n\n\n"
        r, data = self.call("GET", "/api/agents/3/tail")
        self.assertEqual(data["lines"], ["hello", "<img src=x onerror=alert(1)>"])
        self.assertEqual((data["id"], data["state"], data["label"]), (3, "idle", "1"))
        self.assertEqual(self.fake.calls[-1], ("get-text", "--pane-id", "3", "--start-line", "-60"))
        r2, _ = self.call("GET", "/api/agents/3/tail", headers={"if-none-match": r.headers["ETag"]})
        self.assertEqual(r2.status, 304)
        self.call("GET", "/api/agents/3/tail?lines=1000")
        self.assertEqual(self.fake.calls[-1][-1], "-400")
        self.call("GET", "/api/agents/3/tail?lines=1")
        self.assertEqual(self.fake.calls[-1][-1], "-5")
        self.assertEqual(self.call("GET", "/api/agents/3/tail?lines=abc")[0].status, 400)
        self.fake.replies["get-text"] = None
        r, data = self.call("GET", "/api/agents/3/tail")
        self.assertEqual((r.status, data["error"]), (502, "mux_failed"))

    def test_titles_are_data(self):
        self.write_state(3, title="<script>alert(1)</script>\x1b[31m", cwd="/x/<b>")
        r, data = self.call("GET", "/api/agents")
        self.assertEqual(data["agents"][0]["title"], "<script>alert(1)</script>")
        self.assertEqual(data["agents"][0]["cwd"], "/x/<b>")
        self.assertNotIn(b"\x1b", r.body)


class TestWrites(AppCase):
    def test_prompt(self):
        r, data = self.call("POST", "/api/agents/3/prompt", {"text": "hello\n\tworld"})
        self.assertEqual((r.status, data), (200, {"ok": True, "message": "sent"}))
        self.assertEqual(self.sends(), [("send-text", "--pane-id", "3", "--", "hello\n\tworld"),
                                        ("send-text", "--pane-id", "3", "--no-paste", "\r")])

    def test_prompt_rejects_bad_text_and_sends_nothing(self):
        for text in ("a\x1bb", "\x1b[201~rm", "", "  ", "x" * 8001, 5, None, ["x"], "a\rb"):
            r, data = self.call("POST", "/api/agents/3/prompt", {"text": text})
            self.assertEqual((r.status, data["error"]), (400, "bad_request"), repr(text))
        self.assertEqual(self.call("POST", "/api/agents/3/prompt", {})[0].status, 400)
        self.assertEqual(self.call("POST", "/api/agents/3/prompt", {"text": "x", "extra": 1})[0].status, 400)
        self.assertEqual(self.sends(), [])

    def test_prompt_refused_while_waiting_or_exited(self):
        self.write_state(3, state="attention", pending={"kind": "approval", "tool": "bash", "summary": "rm"})
        r, data = self.call("POST", "/api/agents/3/prompt", {"text": "x"})
        self.assertEqual((r.status, data["error"]), (409, "agent_not_ready"))
        self.write_state(3, state="working", pid=DEAD_PID)
        r, data = self.call("POST", "/api/agents/3/prompt", {"text": "x"})
        self.assertEqual((r.status, data["error"]), (409, "agent_not_ready"))
        self.assertEqual(self.sends(), [])

    def test_prompt_send_failure_is_502(self):
        self.fake.replies["send-text"] = None
        r, data = self.call("POST", "/api/agents/3/prompt", {"text": "x"})
        self.assertEqual((r.status, data["error"]), (502, "mux_failed"))

    def test_keys_are_sent_one_call_each_in_order(self):
        r, data = self.call("POST", "/api/agents/3/keys", {"keys": ["esc", "y", "enter"]})
        self.assertEqual((r.status, data), (200, {"ok": True, "sent": 3}))
        self.assertEqual(self.sends(), [("send-text", "--pane-id", "3", "--no-paste", b)
                                        for b in ("\x1b", "y", "\r")])

    def test_key_table(self):
        want = {"enter": "\r", "esc": "\x1b", "tab": "\t", "shift-tab": "\x1b[Z", "backspace": "\x7f", "space": " ",
                "up": "\x1b[A", "down": "\x1b[B", "right": "\x1b[C", "left": "\x1b[D", "ctrl-c": "\x03", "ctrl-d": "\x04"}
        self.assertEqual(R.KEYS, want)
        self.assertEqual(R.meta_json(False)["keys"], list(want))
        for name, byte in want.items():
            self.fake.calls.clear()
            self.clock.t += 1
            r, _ = self.call("POST", "/api/agents/3/keys", {"keys": [name], "confirm": True})
            self.assertEqual(r.status, 200, name)
            self.assertEqual(self.sends(), [("send-text", "--pane-id", "3", "--no-paste", byte)], name)

    def test_keys_validation(self):
        bad = [{"keys": ["!"]}, {"keys": ["ab"]}, {"keys": ["F5"]}, {"keys": ["enter"] * 9}, {"keys": "enter"}, {"keys": [1]},
               {"keys": ["y"], "confirm": "yes"}, {"keys": ["y"], "extra": 1}, {}, {"keys": []}, {"text": "a\x1bb"},
               {"text": "x" * 257}, {"keys": [""]}, {"keys": ["é"]}]
        for body in bad:
            r, data = self.call("POST", "/api/agents/3/keys", body)
            self.assertEqual((r.status, data["error"]), (400, "bad_request"), body)
        self.assertEqual(self.sends(), [])

    def test_keys_text_goes_first_pasted(self):
        r, data = self.call("POST", "/api/agents/3/keys", {"text": "-n", "keys": ["enter"]})
        self.assertEqual(data["sent"], 2)
        self.assertEqual(self.sends(), [("send-text", "--pane-id", "3", "--", "-n"), ("send-text", "--pane-id", "3", "--no-paste", "\r")])
        self.fake.calls.clear()
        self.assertEqual(self.call("POST", "/api/agents/3/keys", {"text": "only"})[1]["sent"], 1)

    def test_dangerous_keys_need_confirmation(self):
        for keys in (["ctrl-c"], ["y", "ctrl-d"]):
            r, data = self.call("POST", "/api/agents/3/keys", {"keys": keys})
            self.assertEqual((r.status, data["error"]), (409, "confirm_required"))
            self.assertEqual(data["dangerous"], [k for k in keys if k.startswith("ctrl")])
        self.assertEqual(self.sends(), [], "nothing is sent before the confirmation")
        r, _ = self.call("POST", "/api/agents/3/keys", {"keys": ["ctrl-c"], "confirm": True})
        self.assertEqual(r.status, 200)
        self.assertEqual(self.sends(), [("send-text", "--pane-id", "3", "--no-paste", "\x03")])
        self.assertEqual(self.call("POST", "/api/agents/3/keys", {"keys": ["esc"]})[0].status, 200, "esc needs none")

    def test_keys_work_while_waiting_but_not_after_exit(self):
        self.write_state(3, state="attention", pending={"kind": "approval", "tool": "bash", "summary": "rm"})
        self.assertEqual(self.call("POST", "/api/agents/3/keys", {"keys": ["y"]})[0].status, 200)
        self.write_state(3, state="working", pid=DEAD_PID)
        self.fake.calls.clear()
        r, data = self.call("POST", "/api/agents/3/keys", {"keys": ["y", "enter"], "text": "x"})
        self.assertEqual((r.status, data["error"]), (409, "agent_not_ready"))
        self.assertEqual(self.sends(), [], "keys never reach the shell an exited agent left")

    def test_plan_answers_go_through_a_command_file(self):
        self.write_state(3, state="attention", pending={"kind": "plan_review", "reviewId": "r1", "title": "P", "choices": []})
        r, data = self.call("POST", "/api/agents/3/plan", {"choice": "refine", "feedback": "more tests\nplease"})
        self.assertEqual((r.status, data["ok"]), (200, True))
        files = [f for f in os.listdir(self.dir) if ".cmd." in f]
        self.assertEqual(len(files), 1)
        with open(os.path.join(self.dir, files[0])) as f:
            self.assertEqual(json.load(f), {"type": "plan_review", "reviewId": "r1", "choice": "refine", "feedback": "more tests\nplease"})

    def test_plan_validation(self):
        self.write_state(3, state="attention", pending={"kind": "plan_review", "reviewId": "r1", "title": "P", "choices": []})
        for body in ({"choice": "rm"}, {}, {"choice": "refine"}, {"choice": "refine", "feedback": "  "}, {"choice": "execute", "feedback": "a\x1b"},
                     {"choice": "execute", "feedback": "x" * 4001}, {"choice": "execute", "feedback": 3}, {"choice": "execute", "x": 1}):
            r, data = self.call("POST", "/api/agents/3/plan", body)
            self.assertEqual((r.status, data["error"]), (400, "bad_request"), body)
        self.assertFalse([f for f in os.listdir(self.dir) if ".cmd." in f])

    def test_plan_without_a_review_is_409(self):
        r, data = self.call("POST", "/api/agents/3/plan", {"choice": "execute"})
        self.assertEqual((r.status, data["error"]), (409, "agent_not_ready"))

    def test_ack_clears_done(self):
        self.write_state(3, state="done", since=NOW - 30)
        self.assertEqual(self.call("GET", "/api/agents")[1]["agents"][0]["state"], "done")
        r, data = self.call("POST", "/api/agents/3/ack", {})
        self.assertEqual((r.status, data), (200, {"ok": True}))
        with open(os.path.join(self.dir, "acks.json")) as f:
            self.assertEqual(json.load(f), {"3": NOW - 30})
        self.assertEqual(self.call("GET", "/api/agents")[1]["agents"][0]["state"], "idle")
        self.assertEqual(self.call("POST", "/api/agents/3/ack", {"x": 1})[0].status, 400)

    def test_ack_of_an_agent_that_is_not_done_or_idle_is_409(self):
        for state in ("working", "busy", "attention", "exited"):
            self.write_state(3, state=state, pid=DEAD_PID if state == "exited" else os.getpid(),
                             pending={"kind": "approval", "tool": "bash", "summary": "x"} if state == "attention" else None)
            self.clock.t += 2
            r, data = self.call("POST", "/api/agents/3/ack", {})
            self.assertEqual((r.status, data["error"]), (409, "agent_not_ready"), state)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "acks.json")))

    def test_a_limit_can_be_acknowledged_only_after_its_reset(self):
        self.write_state(3, state="limit", since=NOW - 7000, limit={"hit_at": NOW - 7000, "reset_at": NOW + 600})
        row = self.call("GET", "/api/agents")[1]["agents"][0]
        self.assertEqual((row["state"], row["can"]["ack"]), ("limit", False))
        r, data = self.call("POST", "/api/agents/3/ack", {})
        self.assertEqual((r.status, data["error"]), (409, "agent_not_ready"))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "acks.json")))
        self.write_state(3, state="limit", since=NOW - 7000, limit={"hit_at": NOW - 7000, "reset_at": NOW - 5})
        self.clock.t += 2
        self.assertTrue(self.call("GET", "/api/agents")[1]["agents"][0]["can"]["ack"])
        self.assertEqual(self.call("POST", "/api/agents/3/ack", {})[0].status, 200)
        with open(os.path.join(self.dir, "acks.json")) as f:
            self.assertEqual(json.load(f), {"3": NOW - 7000})
        self.clock.t += 2
        self.assertEqual(self.call("GET", "/api/agents")[1]["agents"][0]["state"], "idle")

    def test_writes_run_under_the_mux_lock(self):
        held = []
        lock = self.app.mux._lock
        self.fake.replies["send-text"] = lambda args: held.append(lock.locked()) or ""
        self.assertEqual(self.call("POST", "/api/agents/3/prompt", {"text": "x"})[0].status, 200)
        self.assertEqual(self.call("POST", "/api/agents/3/keys", {"keys": ["y"]})[0].status, 200)
        self.assertEqual(held, [True, True, True])
        with unittest.mock.patch.object(R.A, "ack", side_effect=lambda agent, now=None: held.append(lock.locked())):
            self.assertEqual(self.call("POST", "/api/agents/3/ack", {})[0].status, 200)
        self.assertEqual(held[-1], True)
        with unittest.mock.patch.object(R.A, "answer_plan_review", side_effect=lambda *a: held.append(lock.locked()) or (True, "sent")):
            self.write_state(3, state="attention", pending={"kind": "plan_review", "reviewId": "r", "title": "t", "choices": []})
            self.clock.t += 2
            self.assertEqual(self.call("POST", "/api/agents/3/plan", {"choice": "execute"})[0].status, 200)
        self.assertEqual(held[-1], True)
        self.assertEqual(len(held), 5)

    def test_a_path_with_a_trailing_newline_is_no_agent(self):
        with self.assertRaises(R.HttpError) as cm:
            self.app._find("3\n", False)
        self.assertEqual(cm.exception.code, "not_found")
        self.assertEqual(self.app._find("3", False)[0].pane, 3)

    def test_every_write_is_logged_without_its_content(self):
        self.call("POST", "/api/agents/3/prompt", {"text": "secret-prompt"})
        self.call("POST", "/api/agents/3/keys", {"keys": ["y"], "text": "secret-text"})
        self.call("POST", "/api/agents/3/ack", {})
        text = "\n".join(self.logs)
        self.assertRegex(text, r"write prompt label=\S+ bytes=13")
        self.assertRegex(text, r"write keys label=\S+ bytes=12")
        self.assertIn("write ack", text)
        self.assertNotIn("secret", text)
        self.assertNotIn(TOKEN, text)

    def test_post_pipeline(self):
        def post(**kw):
            r, data = self.call("POST", "/api/agents/3/ack", kw.pop("body", {}), **kw)
            return r.status, (data or {}).get("error")
        self.assertEqual(post(headers={"x-hivegrid-remote": None}), (403, "forbidden_origin"))
        self.assertEqual(post(headers={"x-hivegrid-remote": "0"}), (403, "forbidden_origin"))
        self.assertEqual(post(headers={"content-type": "text/plain"}), (415, "unsupported_media_type"))
        self.assertEqual(post(headers={"content-type": "Application/JSON; charset=utf-8"}), (200, None))
        self.assertEqual(post(headers={"origin": "https://evil.example"}), (403, "forbidden_origin"))
        self.assertEqual(post(headers={"origin": "http://127.0.0.1:8787"}), (200, None))
        self.assertEqual(post(headers={"sec-fetch-site": "cross-site"}), (403, "forbidden_origin"))
        self.assertEqual(post(headers={"content-length": None}), (411, "length_required"))
        self.assertEqual(post(headers={"transfer-encoding": "chunked"}), (411, "length_required"))
        self.assertEqual(post(headers={"content-length": "16385"}), (413, "too_large"))
        self.assertEqual(post(headers={"content-length": "x"}), (400, "bad_request"))
        self.assertEqual(post(headers={"content-length": "50"}), (400, "bad_request"), "short body")

    def test_body_must_be_a_json_object(self):
        for raw in (b"{", b"[]", b'"x"', b"null", b"\xff", b""):
            h = {"host": "127.0.0.1", "authorization": "Bearer " + TOKEN, "content-type": "application/json",
                 "x-hivegrid-remote": "1", "content-length": str(len(raw))}
            r = self.app.dispatch(R.Request("POST", "/api/agents/3/ack", h, "p", lambda n: raw[:n]))
            self.assertEqual(r.status, 400, raw)

    def test_read_only(self):
        app = self.make_app(readonly=True)
        for path, body in (("/api/agents/3/prompt", {"text": "x"}), ("/api/agents/3/keys", {"keys": ["y"]}),
                           ("/api/agents/3/plan", {"choice": "execute"}), ("/api/agents/3/ack", {})):
            r, data = self.call("POST", path, body, app=app)
            self.assertEqual((r.status, data["error"]), (403, "readonly"), path)
        self.assertEqual(self.sends(), [])
        r, data = self.call("GET", "/api/agents", app=app)
        self.assertEqual(r.status, 200)
        self.assertEqual(set(data["agents"][0]["can"].values()), {False})
        self.assertTrue(self.call("GET", "/api/meta", app=app)[1]["readonly"])


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = os.path.join(self.tmp.name, "cfg", "remote-token")

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = R.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_token_create_show_rotate(self):
        code, out, _ = self.cli("token", "--token-file", self.file)
        first = out.strip()
        self.assertEqual((code, len(first)), (0, 43))
        self.assertEqual(R.read_token(self.file), first)
        code, out, err = self.cli("token", "--token-file", self.file)
        self.assertEqual((code, out), (0, ""), "an existing token is not printed or replaced")
        self.assertIn(self.file, err)
        self.assertEqual(self.cli("token", "--show", "--token-file", self.file)[1].strip(), first)
        code, out, _ = self.cli("token", "--rotate", "--token-file", self.file)
        self.assertNotEqual(out.strip(), first)
        self.assertEqual(R.read_token(self.file), out.strip())

    def test_the_default_file_sits_next_to_settings(self):
        with unittest.mock.patch.dict(os.environ, {"HIVEGRID_SETTINGS": os.path.join(self.tmp.name, "s", "settings.json")}):
            os.environ.pop("HIVEGRID_REMOTE_TOKEN_FILE", None)
            self.assertEqual(self.cli("token")[0], 0)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp.name, "s", "remote-token")))

    def test_show_without_a_token_fails(self):
        code, out, err = self.cli("token", "--show", "--token-file", self.file)
        self.assertEqual((code, out), (1, ""))
        self.assertIn("hivegrid-remote token", err)

    def test_a_bad_port_is_a_clean_error(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            R.main(["serve", "--port", "70000"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("outside 1-65535", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        R.write_token(self.file, TOKEN)
        with unittest.mock.patch.dict(os.environ, {"HIVEGRID_REMOTE_PORT": "abc"}):
            code, _, err = self.cli("serve", "--token-file", self.file)
            self.assertEqual(code, 2)
            self.assertIn("HIVEGRID_REMOTE_PORT", err)
            self.assertNotIn("Traceback", err)
            code, out, _ = self.cli("token", "--show", "--token-file", self.file)
            self.assertEqual((code, out.strip()), (0, TOKEN), "the token command ignores the port variable")

    def test_serve_refuses_without_a_token_and_on_a_public_bind(self):
        code, _, err = self.cli("serve", "--token-file", self.file, "--port", "8787")
        self.assertEqual(code, 1)
        self.assertIn("hivegrid-remote token", err)
        R.write_token(self.file, TOKEN)
        code, _, err = self.cli("serve", "--token-file", self.file, "--port", "8787", "--bind", "0.0.0.0")
        self.assertEqual(code, 2)
        self.assertIn("--allow-any-bind", err)

    def test_serve_refuses_a_state_directory_others_can_write(self):
        R.write_token(self.file, TOKEN)
        run = os.path.join(self.tmp.name, "run")
        os.makedirs(os.path.join(run, "hivegrid-agents"))
        os.chmod(os.path.join(run, "hivegrid-agents"), 0o777)
        with unittest.mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": run}):
            code, _, err = self.cli("serve", "--token-file", self.file, "--port", "8787")
        self.assertEqual(code, 1)
        self.assertIn("state directory is not trusted", err)
        self.assertIn("written by others", err)


class TestHardening(unittest.TestCase):
    def test_whole_string_matches_only(self):
        for ok in ("y", "Z", "7"):
            self.assertTrue(R.SINGLE_KEY.fullmatch(ok), ok)
        for bad in ("y\n", "\ny", "yy", "", "é"):
            self.assertFalse(R.SINGLE_KEY.fullmatch(bad), repr(bad))
        self.assertTrue(R.AGENT_ID.fullmatch("3"))
        self.assertFalse(R.AGENT_ID.fullmatch("3\n"))
        self.assertTrue(R.TOKEN_RE.fullmatch("k" * 43))
        self.assertFalse(R.TOKEN_RE.fullmatch("k" * 43 + "\n"))

    def test_text_that_cannot_be_utf8_is_refused(self):
        self.assertIn("not valid", R.validate_text("a\ud800b", 100))
        self.assertIn("not valid", R.validate_text("\udfff", 100, allow_empty=True))

    def test_line_separators_are_refused_in_prompts(self):
        for ch in ("\u2028", "\u2029"):
            self.assertIn("control", R.validate_text("a" + ch + "b", 100), repr(ch))
            self.assertIn("control", R.validate_text("a" + ch + "b", 100, "feedback"), repr(ch))

    def test_invisible_characters_leave_display_text(self):
        for ch in ("\u2028", "\u2029", "\u200b", "\u200c", "\u200d", "\ufeff", "\U000e0041", "\U000e007f", "\ud800", "\u202e"):
            self.assertEqual(R.sanitize_text("a" + ch + "b", 10), "ab", repr(ch))
            self.assertEqual(R.sanitize_line("a" + ch + "b", 10), "ab", repr(ch))

    def test_the_runner_survives_arguments_that_cannot_be_passed(self):
        for exc in (UnicodeEncodeError("utf-8", "x", 0, 1, "surrogates not allowed"), ValueError("embedded null byte")):
            def boom(argv, exc=exc, **kw):
                raise exc
            self.assertIsNone(R.cli_runner("/x/wezterm", "/s", runner=boom)("send-text", "a"))

    def test_the_opened_token_file_must_be_the_one_checked(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t")
            R.write_token(path, TOKEN)
            real = os.fstat

            def other_inode(fd):
                st = real(fd)
                return os.stat_result((st.st_mode, st.st_ino + 1, st.st_dev, st.st_nlink, st.st_uid, st.st_gid, st.st_size, 0, 0, 0))

            def open_mode(fd):
                st = real(fd)
                return os.stat_result((stat.S_IFREG | 0o644, st.st_ino, st.st_dev, st.st_nlink, st.st_uid, st.st_gid, st.st_size, 0, 0, 0))

            def other_owner(fd):
                st = real(fd)
                return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid + 1, st.st_gid, st.st_size, 0, 0, 0))
            with unittest.mock.patch("hivegrid_remote.os.fstat", other_inode):
                with self.assertRaisesRegex(R.TokenError, "changed while"):
                    R.read_token(path)
            for patched in (open_mode, other_owner):
                with unittest.mock.patch("hivegrid_remote.os.fstat", patched):
                    with self.assertRaisesRegex(R.TokenError, "not private"):
                        R.read_token(path)
            self.assertEqual(R.read_token(path), TOKEN)

    def test_log_request_cleans_the_method_and_drops_the_query(self):
        logs = []
        h = R.Handler.__new__(R.Handler)
        h.app = types.SimpleNamespace(log=logs.append)
        h.command, h.path = "G\x1b[31mET\nX", "/a?token=secret"
        h.log_request(200)
        self.assertEqual(logs, ["GET X /a 200"])

    def test_deadline_reader_stops_a_slow_client(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        times = iter([0.0, 20.0])
        rd = R.DeadlineReader(a, lambda: next(times), per_recv=0.5)
        rd.deadline = 10.0
        b.sendall(b"GET / HT")          # a partial line, then nothing more in time
        with self.assertRaises(TimeoutError):
            rd.readline(65537)

    def test_deadline_reader_reads_lines_and_bodies(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        rd = R.DeadlineReader(a, per_recv=2)
        rd.deadline = time.monotonic() + 5
        b.sendall(b"one\r\ntwo\r\n{}")
        self.assertEqual((rd.readline(65537), rd.readline(65537), rd.read(2)), (b"one\r\n", b"two\r\n", b"{}"))
        b.sendall(b"abcdef\n")
        self.assertEqual(rd.readline(3), b"abc", "a line limit is honoured")
        b.close()
        self.assertEqual((rd.readline(65537), rd.readline(65537), rd.read(5)), (b"def\n", b"", b""))

    def test_drain_gives_up_at_its_deadline(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        times = iter([0.0, 100.0])
        h = R.Handler.__new__(R.Handler)
        h.connection, h.clock = a, lambda: next(times)
        b.sendall(b"x" * 10)
        h._drain()
        a.settimeout(1)
        self.assertEqual(a.recv(100), b"x" * 10, "nothing was read once the deadline had passed")


class TestCliHelpers(unittest.TestCase):
    def ns(self, **kw):
        return argparse.Namespace(**{"allow_host": [], "bind": "127.0.0.1", "socket": None, "wezterm": None, "port": None, **kw})

    def test_allowed_hosts_come_from_the_flag_and_the_allow_hosts_variable(self):
        env = {"HIVEGRID_REMOTE_ALLOW_HOSTS": "a.test, B.test,,"}
        self.assertEqual(R.allowed_hosts(self.ns(allow_host=["c.test", "A.test"]), env), ["c.test", "a.test", "b.test"])
        self.assertEqual(R.allowed_hosts(self.ns(), {"HIVEGRID_REMOTE_HOSTS": "old.test"}), [], "the old name is gone")
        self.assertEqual(R.allowed_hosts(self.ns(), {}), [])

    def test_a_tailnet_bind_address_is_an_allowed_host(self):
        names = R.allowed_hosts(self.ns(bind="100.64.1.2"), {})
        self.assertEqual(names, ["100.64.1.2"])
        self.assertTrue(R.host_allowed("100.64.1.2:8787", names))
        self.assertFalse(R.host_allowed("100.64.1.3:8787", names))
        names6 = R.allowed_hosts(self.ns(bind="fd7a:115c:a1e0::1"), {})
        self.assertEqual(names6, ["[fd7a:115c:a1e0::1]"])
        self.assertTrue(R.host_allowed("[fd7a:115c:a1e0::1]:8787", names6))
        self.assertEqual(R.allowed_hosts(self.ns(bind="127.0.0.1"), {}), [], "loopback is allowed anyway")
        self.assertEqual(R.allowed_hosts(self.ns(bind="100.64.1.2", allow_host=["100.64.1.2"]), {}), ["100.64.1.2"])

    def test_ports(self):
        for ok, want in (("8787", 8787), (" 1 ", 1), ("65535", 65535)):
            self.assertEqual(R.parse_port(ok), want)
        for bad in ("0", "65536", "-1", "x", "", " ", "1.5", "80a"):
            with self.assertRaises(ValueError, msg=bad):
                R.parse_port(bad)
        self.assertEqual(R.resolve_port(self.ns(port=99), {"HIVEGRID_REMOTE_PORT": "bad"}), 99)
        self.assertEqual(R.resolve_port(self.ns(), {"HIVEGRID_REMOTE_PORT": "9000"}), 9000)
        self.assertEqual(R.resolve_port(self.ns(), {"HIVEGRID_REMOTE_PORT": " "}), R.DEFAULT_PORT)
        self.assertEqual(R.resolve_port(self.ns(), {}), R.DEFAULT_PORT)
        with self.assertRaisesRegex(ValueError, "HIVEGRID_REMOTE_PORT"):
            R.resolve_port(self.ns(), {"HIVEGRID_REMOTE_PORT": "70000"})

    def test_exe_for_socket_is_the_sibling_of_the_gui(self):
        with tempfile.TemporaryDirectory() as d:
            gui = os.path.join(d, "wezterm-gui")
            sibling = os.path.join(d, "wezterm")
            Path(sibling).write_text("")
            which = lambda name: "/usr/bin/" + name  # noqa: E731
            exe = lambda sock, pids={77: gui, 78: "/usr/bin/other"}: R.exe_for_socket(  # noqa: E731
                sock, proc_exe=pids.get, which=which)
            self.assertEqual(exe("/run/wezterm/gui-sock-77"), sibling)
            self.assertEqual(exe("/run/wezterm/gui-sock-78"), "/usr/bin/wezterm", "not a wezterm-gui")
            self.assertEqual(exe("/run/wezterm/gui-sock-79"), "/usr/bin/wezterm", "no such process")
            self.assertEqual(exe("/run/custom.sock"), "/usr/bin/wezterm", "a name that carries no pid")
            os.unlink(sibling)
            self.assertEqual(exe("/run/wezterm/gui-sock-77"), "/usr/bin/wezterm", "no sibling")

    def test_locator(self):
        discover = lambda: ("/s/gui-sock-5", "/g/wezterm")  # noqa: E731
        self.assertIs(R._locator(self.ns(), discover=discover), discover)
        self.assertEqual(R._locator(self.ns(socket="/r/gui-sock-5"), exe_for=lambda s: "/opt/wezterm")(),
                         ("/r/gui-sock-5", "/opt/wezterm"))
        self.assertEqual(R._locator(self.ns(socket="/r/s", wezterm="/w/wezterm"), exe_for=lambda s: self.fail("asked"))(),
                         ("/r/s", "/w/wezterm"))
        self.assertIsNone(R._locator(self.ns(socket="/r/s"), exe_for=lambda s: None)())
        self.assertEqual(R._locator(self.ns(wezterm="/w/wezterm"), discover=discover)(), ("/s/gui-sock-5", "/w/wezterm"))
        self.assertIsNone(R._locator(self.ns(wezterm="/w/wezterm"), discover=lambda: None)())

    def test_wezterm_gui_path_becomes_its_sibling_cli(self):
        # a Hivegrid pane exports WEZTERM_EXECUTABLE=.../wezterm-gui, which has no `cli` subcommand
        d = self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for n in ("wezterm", "wezterm-gui"):
            open(os.path.join(d, n), "w").close()
        gui, cli = os.path.join(d, "wezterm-gui"), os.path.join(d, "wezterm")
        self.assertEqual(R.cli_exe(gui), cli)
        self.assertEqual(R.cli_exe(cli), cli)
        self.assertEqual(R.cli_exe("/nowhere/wezterm-gui"), "/nowhere/wezterm-gui")
        self.assertIsNone(R.cli_exe(None))
        self.assertEqual(R._locator(self.ns(socket="/r/s", wezterm=gui))(), ("/r/s", cli))
        self.assertEqual(R._locator(self.ns(wezterm=gui), discover=lambda: ("/s/gui-sock-5", "/g/wezterm"))(),
                         ("/s/gui-sock-5", cli))


if __name__ == "__main__":
    unittest.main()
