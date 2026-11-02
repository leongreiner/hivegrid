"""hivegrid_botspage: making sure the local hivegrid-remote answers for the Bots page of the window, the
status file the config reads, and the one-shot page that carries the access token to the web pane."""
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin")
sys.dont_write_bytecode = True
sys.path.insert(0, BIN)
import hivegrid_botspage as B  # noqa: E402
import hivegrid_remote as R  # noqa: E402

TOKEN = "k3J9-x_Q7mZ2vB8nL4tYw6RcD1fHs0Ae"


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.run_dir = os.path.join(self.tmp, "run")
        os.makedirs(self.run_dir, mode=0o700)
        self.env = {"HOME": self.tmp, "XDG_RUNTIME_DIR": self.run_dir, "XDG_CONFIG_HOME": os.path.join(self.tmp, "config")}
        os.makedirs(os.path.join(self.tmp, ".config", "hivegrid"))
        self.token_file = os.path.join(self.tmp, ".config", "hivegrid", "remote-token")
        self.clock = [100.0]
        self.sleeps = []
        self.started = []
        self.answers = []   # what probe() says, in order; the last one repeats

    def token(self, text=TOKEN):
        with open(self.token_file, "w") as f:
            f.write(text + "\n")

    def probe(self, host="127.0.0.1", p=0, timeout=1.0):
        self.probed = (host, p)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]

    def sleep(self, s):
        self.sleeps.append(s)
        self.clock[0] += s

    def popen(self, argv, **kw):
        self.started.append((argv, kw))

    def ensure(self, **kw):
        return B.ensure(self.env, probe=self.probe, popen=self.popen, exists=lambda p: False, sleep=self.sleep,
                        clock=lambda: self.clock[0], wall=lambda: 1_700_000_000.5, **kw)

    def status(self):
        with open(B.status_path(self.env)) as f:
            return json.load(f)


class Ensure(Case):
    def test_a_running_server_is_used_as_it_is(self):
        self.token()
        self.answers = [True]
        doc = self.ensure()
        self.assertEqual((doc["ok"], doc["state"], doc["url"]), (True, "up", "http://127.0.0.1:8787/"))
        self.assertEqual(self.started, [])
        self.assertEqual(self.status(), doc)
        self.assertEqual(doc["ts"], 1_700_000_000.5)
        self.assertEqual(self.probed, ("127.0.0.1", 8787))

    def test_a_server_that_is_down_is_started_on_loopback_only_and_waited_for(self):
        self.token()
        self.answers = [False, False, False, True]
        doc = self.ensure()
        self.assertEqual((doc["ok"], doc["state"]), (True, "started"))
        (argv, kw), = self.started
        self.assertEqual(argv[1:], ["serve", "--bind", "127.0.0.1", "--port", "8787"])
        self.assertTrue(kw["start_new_session"], "it outlives the program that started it")
        self.assertEqual(self.sleeps, [0.25, 0.25])

    def test_the_user_unit_is_preferred_when_installed(self):
        self.token()
        self.answers = [False, True]
        unit = os.path.join(self.tmp, "config", "systemd", "user", B.UNIT)
        ran = []

        def run(argv, **kw):
            ran.append(argv)
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        doc = B.ensure(self.env, probe=self.probe, run=run, popen=self.popen, exists=lambda p: p == unit,
                       sleep=self.sleep, clock=lambda: self.clock[0])
        self.assertEqual(ran, [["systemctl", "--user", "start", "hivegrid-remote.service"]])
        self.assertEqual((self.started, doc["state"], doc["command"]), ([], "started", "systemctl --user start hivegrid-remote"))

    def test_a_failing_unit_says_why_and_the_command(self):
        self.token()
        self.answers = [False]
        unit = os.path.join(self.tmp, "config", "systemd", "user", B.UNIT)
        run = lambda argv, **kw: type("R", (), {"returncode": 5, "stdout": "", "stderr": "Unit not found.\n"})()  # noqa: E731
        doc = B.ensure(self.env, probe=self.probe, run=run, exists=lambda p: p == unit, sleep=self.sleep,
                       clock=lambda: self.clock[0])
        self.assertFalse(doc["ok"])
        self.assertIn("Unit not found.", doc["message"])
        self.assertIn("systemctl --user start hivegrid-remote", doc["message"])

    def test_no_token_is_said_and_nothing_is_started(self):
        self.answers = [False]
        doc = self.ensure()
        self.assertEqual((doc["ok"], doc["state"], doc["command"]), (False, "no_token", "hivegrid-remote token"))
        self.assertIn("hivegrid-remote token", doc["message"])
        self.assertEqual(self.started, [])
        self.assertEqual(self.status()["state"], "no_token")

    def test_a_token_of_the_wrong_shape_counts_as_none(self):
        for bad in ("", "short", "has space in it 123456789", "x" * 200, "a/b" * 10):
            self.token(bad)
            self.answers = [True]
            self.assertEqual(self.ensure()["state"], "no_token", bad)

    def test_a_server_that_never_answers_ends_with_the_start_command(self):
        self.token()
        self.answers = [False]
        doc = self.ensure(wait=1.0)
        self.assertEqual((doc["ok"], doc["state"]), (False, "down"))
        self.assertIn("hivegrid-remote serve", doc["message"])
        self.assertEqual(len(self.sleeps), 4)

    def test_a_program_that_cannot_run(self):
        self.token()
        self.answers = [False]

        def boom(argv, **kw):
            raise FileNotFoundError(2, "No such file or directory")
        doc = B.ensure(self.env, probe=self.probe, popen=boom, exists=lambda p: False, sleep=self.sleep, clock=lambda: self.clock[0])
        self.assertFalse(doc["ok"])
        self.assertIn("could not be started", doc["message"])

    def test_the_port_follows_the_environment(self):
        self.assertEqual(B.port({"HIVEGRID_REMOTE_PORT": "18711"}), 18711)
        for bad in ("x", "0", "70000", "-1", ""):
            self.assertEqual(B.port({"HIVEGRID_REMOTE_PORT": bad}), 8787, bad)
        self.assertEqual(B.DEFAULT_PORT, R.DEFAULT_PORT, "the same default as the server")

    def test_the_token_path_is_the_servers(self):
        for env in ({"HOME": "/h"}, {"HOME": "/h", "XDG_CONFIG_HOME": "/c"}, {"HIVEGRID_SETTINGS": "/s/settings.json"},
                    {"HIVEGRID_REMOTE_TOKEN_FILE": "/t/tok"}):
            old = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME", "HIVEGRID_SETTINGS", "HIVEGRID_REMOTE_TOKEN_FILE")}
            try:
                for k in old:
                    os.environ.pop(k, None)
                os.environ.update(env)
                theirs = R.default_token_path()
            finally:
                for k, v in old.items():
                    os.environ.pop(k, None)
                    if v is not None:
                        os.environ[k] = v
            self.assertEqual(B.token_path(env), theirs, env)


class OpenPage(Case):
    def test_the_page_sends_the_pane_to_the_server_with_the_token_in_the_fragment(self):
        self.token()
        path = B.write_open("/bots/daily-news", self.env)
        self.assertEqual(path, os.path.join(self.run_dir, B.OPEN_NAME))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path) as f:
            html = f.read()
        self.assertIn(json.dumps(f"http://127.0.0.1:8787/?embed=1#t={TOKEN}&r=%2Fbots%2Fdaily-news"), html)
        self.assertNotIn("&n=1", html)
        B.write_open("/bots", self.env, new=True)
        with open(path) as f:
            self.assertIn("&r=%2Fbots&n=1", f.read())

    def test_ensure_names_the_page_only_when_it_is_ok(self):
        self.token()
        self.answers = [True]
        doc = self.ensure(route="/bots/x-bot")
        self.assertEqual(doc["open"], os.path.join(self.run_dir, B.OPEN_NAME))
        self.answers = [False]
        self.token("")
        os.unlink(B.open_path(self.env))
        self.assertIsNone(self.ensure()["open"])
        self.assertFalse(os.path.exists(B.open_path(self.env)))

    def test_routes_are_only_the_bots_page_or_one_bot(self):
        self.token()
        for bad in ("", "/", "/bots/", "/bots/A", "/bots/a", "/bots/ab/cd", "/term", "/bots?x=1", "//evil", "/bots/a b c"):
            self.assertIsNone(B.write_open(bad, self.env), bad)
        for good in ("/bots", "/bots/abc", "/bots/ci-bot-9f2"):
            self.assertTrue(B.write_open(good, self.env), good)

    def test_the_token_never_goes_outside_the_runtime_directory(self):
        self.token()
        self.assertIsNone(B.write_open("/bots", {k: v for k, v in self.env.items() if k != "XDG_RUNTIME_DIR"}))
        self.assertIsNone(B.write_open("/bots", dict(self.env, XDG_RUNTIME_DIR="relative")))

    def test_the_status_file_and_the_command_output_carry_no_token(self):
        self.token()
        self.answers = [True]
        doc = self.ensure()
        with open(B.status_path(self.env)) as f:
            self.assertNotIn(TOKEN, f.read())
        self.assertNotIn(TOKEN, json.dumps(doc))
        self.assertEqual(stat.S_IMODE(os.stat(B.status_path(self.env)).st_mode), 0o600)

    def test_an_unwritable_status_directory_does_not_raise(self):
        self.assertFalse(B.write_status({"ok": True}, {"XDG_RUNTIME_DIR": "/proc/nope"}))


class Probe(unittest.TestCase):
    def test_a_closed_port_is_not_a_server(self):
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            free = s.getsockname()[1]
        self.assertFalse(B.probe("127.0.0.1", free, timeout=0.5))

    def test_only_an_ok_from_healthz_counts(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body, code = (b"ok", 200) if self.server.good else (b"<html>other service</html>", 200)
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        srv = HTTPServer(("127.0.0.1", 0), H)
        self.addCleanup(srv.server_close)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        srv.good = True
        self.assertTrue(B.probe("127.0.0.1", srv.server_address[1]))
        srv.good = False
        self.assertFalse(B.probe("127.0.0.1", srv.server_address[1]), "another service on the port is not hivegrid-remote")


class Main(Case):
    def test_exit_status_and_output(self):
        import contextlib
        import io
        self.token()
        old = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = B.main(["ensure", "--wait", "0", "--route", "/bots/x-bot"])
            self.assertIn(code, (0, 1))
            doc = json.loads(out.getvalue())
            self.assertNotIn("token_file", doc)
            self.assertNotIn(TOKEN, out.getvalue())
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(B.main(["ensure", "--route", "/etc/passwd"]), 2)
        finally:
            for k, v in old.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
