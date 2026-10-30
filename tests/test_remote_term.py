"""The terminal of hivegrid-remote: the pty sessions (real ptys running small scripted shells in a temp
home), the request framing, and the refusals of the API (off by default, read-only, token, host, origin,
rate limits). No real shell, home, network or display is used."""
import base64
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import hivegrid_remote as R  # noqa: E402
import hivegrid_remote_term as T  # noqa: E402
import hivegrid_settings_core as S  # noqa: E402
import test_remote as TR  # noqa: E402
import test_remote_http as TH  # noqa: E402

OWNER = "a" * 16
TOKEN = TR.TOKEN
SECRET = "do-not-leak-this-value"


def script(directory, name, body):
    """An executable `#!/bin/sh` script: the shell a session runs."""
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\n" + body)
    os.chmod(path, 0o755)
    return path


def wait_for(fn, timeout=5.0, every=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return fn()


def collect(session, want, pos=0, timeout=5.0):
    """The output of `session` from `pos` until it contains `want` (bytes); fails the test at the timeout."""
    seen = b""
    end = time.monotonic() + timeout
    while want not in seen:
        left = end - time.monotonic()
        if left <= 0:
            raise AssertionError(f"{want!r} did not appear in {seen!r}")
        r = session.read(pos, wait=min(left, 0.5))
        seen += r.data
        pos = r.next
    return seen


class TermCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.realpath(self.tmp.name)
        self.clock = TR.Clock(1000.0)
        self.logs = []
        self.cat = script(self.home, "cat-shell", "exec cat\n")

    def manager(self, shell=None, environ=None, **kw):
        environ = {"HOME": self.home, "PATH": os.environ.get("PATH", "/usr/bin:/bin")} if environ is None else environ
        m = T.Manager(clock=self.clock, environ=environ, shell=shell or self.cat, home=self.home, log=self.logs.append, **kw)
        self.addCleanup(m.shutdown)
        return m

    def shell(self, body, name="shell"):
        return script(self.home, name, body)


class TestFraming(unittest.TestCase):
    def test_sizes_are_whole_numbers_within_the_limits(self):
        self.assertEqual(T.valid_size(80, 24), (80, 24))
        self.assertEqual(T.valid_size(10, 3), (10, 3))
        self.assertEqual(T.valid_size(400, 200), (400, 200))
        for cols, rows in ((9, 24), (401, 24), (80, 2), (80, 201), (80.0, 24), ("80", 24), (True, 24), (None, 24), (80, None)):
            with self.subTest(cols=cols, rows=rows):
                with self.assertRaises(ValueError):
                    T.valid_size(cols, rows)

    def test_an_input_request_carries_a_number_and_any_text(self):
        self.assertEqual(T.parse_input({"seq": 0, "data": "ls\r"}), (0, b"ls\r"))
        self.assertEqual(T.parse_input({"seq": 7, "data": "\x1b[A\x03\x00é€"}), (7, "\x1b[A\x03\x00é€".encode()))
        self.assertEqual(len(T.parse_input({"seq": 1, "data": "x" * T.MAX_INPUT})[1]), T.MAX_INPUT)

    def test_malformed_input_requests_are_refused(self):
        for body in ({}, {"seq": 0}, {"data": "x"}, {"seq": -1, "data": "x"}, {"seq": 1.0, "data": "x"}, {"seq": True, "data": "x"},
                     {"seq": "0", "data": "x"}, {"seq": 1 << 53, "data": "x"}, {"seq": 0, "data": ""}, {"seq": 0, "data": 5},
                     {"seq": 0, "data": ["x"]}, {"seq": 0, "data": "x" * (T.MAX_INPUT + 1)}, {"seq": 0, "data": "a\ud800"}):
            with self.subTest(body=str(body)[:40]):
                with self.assertRaises(ValueError):
                    T.parse_input(body)

    def test_an_output_answer_is_base64_with_positions(self):
        read = T.Read(10, 15, 40, b"\x1b[0m\xe2\x82", False, True, None)
        doc = T.chunk_json(read)
        self.assertEqual(doc, {"ok": True, "from": 10, "next": 15, "end": 40, "data": base64.b64encode(b"\x1b[0m\xe2\x82").decode(),
                               "reset": False, "alive": True, "exit": None})
        self.assertEqual(base64.b64decode(doc["data"]), b"\x1b[0m\xe2\x82", "a split UTF-8 character survives")
        json.dumps(doc)


class TestEnvironment(TermCase):
    SERVER_ENV = {"HOME": "/home/x", "USER": "x", "PATH": "/opt/bin:/usr/bin", "LANG": "de_DE.UTF-8", "LC_TIME": "en_GB.UTF-8",
                  "DISPLAY": ":0", "SSH_AUTH_SOCK": "/run/agent", "XDG_RUNTIME_DIR": "/run/user/1000", "TERM": "dumb",
                  "HIVEGRID_REMOTE_TOKEN_FILE": "/secret/token", "HIVEGRID_REMOTE_TERMINAL": "1", "HIVEGRID_REMOTE_PORT": "9",
                  "WEZTERM_PANE": "3", "WEZTERM_UNIX_SOCKET": "/run/gui-sock-1", "ANTHROPIC_API_KEY": SECRET, "GITHUB_TOKEN": SECRET,
                  "AWS_SECRET_ACCESS_KEY": SECRET, "OLDPWD": "/elsewhere", "PYTHONPATH": "/evil", "LD_PRELOAD": "/evil.so"}

    def test_only_the_session_and_locale_variables_pass(self):
        env = T.session_env(self.SERVER_ENV, "/bin/zsh", "/home/y", "y")
        self.assertEqual(env, {"PATH": "/opt/bin:/usr/bin", "LANG": "de_DE.UTF-8", "LC_TIME": "en_GB.UTF-8", "DISPLAY": ":0",
                               "SSH_AUTH_SOCK": "/run/agent", "XDG_RUNTIME_DIR": "/run/user/1000", "HOME": "/home/y",
                               "SHELL": "/bin/zsh", "TERM": "xterm-256color", "COLORTERM": "truecolor",
                               "TERM_PROGRAM": "hivegrid-remote", "USER": "y", "LOGNAME": "y"})
        self.assertNotIn(SECRET, json.dumps(env))

    def test_a_service_without_locale_or_path_still_gets_a_utf8_shell(self):
        env = T.session_env({"HOME": "/h"}, "/bin/sh", "/h", "u")
        self.assertEqual((env["LANG"], env["PATH"]), ("C.UTF-8", T.DEFAULT_PATH))
        self.assertNotIn("LANG", T.session_env({"LC_ALL": "C"}, "/bin/sh", "/h", "u"), "an explicit LC_ALL decides")

    def test_the_shell_is_the_users_when_it_runs(self):
        sh = self.shell("exit 0\n")
        plain = os.path.join(self.home, "data")
        Path(plain).write_text("x")
        self.assertEqual(T.pick_shell({"SHELL": sh}, "/bin/sh"), sh)
        self.assertEqual(T.pick_shell({"SHELL": plain}, sh), sh, "not executable: the account's shell")
        self.assertEqual(T.pick_shell({"SHELL": "zsh"}, sh), sh, "a relative name is not trusted")
        self.assertEqual(T.pick_shell({"SHELL": os.path.join(self.home, "gone")}, sh), sh)
        self.assertEqual(T.pick_shell({}, os.path.join(self.home, "gone")), "/bin/sh")

    def test_home_is_a_directory(self):
        self.assertEqual(T.home_dir({"HOME": self.home}), self.home)
        self.assertNotEqual(T.home_dir({"HOME": os.path.join(self.home, "nope")}), os.path.join(self.home, "nope"))

    def test_the_shell_sees_that_environment_and_starts_in_home(self):
        environ = dict(self.SERVER_ENV, HOME=self.home, PATH=os.environ.get("PATH", "/usr/bin:/bin"))
        environ.pop("LANG")
        for k in [k for k in environ if k.startswith("LC_")]:
            del environ[k]
        sh = self.shell("pwd\nenv | sort\necho ENV-DONE\nexec cat\n")
        m = self.manager(shell=sh, environ=environ)
        s = m.open(80, 24, OWNER)
        out = collect(s, b"ENV-DONE").decode()
        lines = [x.strip("\r") for x in out.split("\n")]
        self.assertEqual(lines[0], self.home, "starts in $HOME")
        names = {x.split("=", 1)[0] for x in lines if "=" in x}
        for leak in ("HIVEGRID_REMOTE_TOKEN_FILE", "HIVEGRID_REMOTE_TERMINAL", "HIVEGRID_REMOTE_PORT", "WEZTERM_PANE",
                     "WEZTERM_UNIX_SOCKET", "ANTHROPIC_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "PYTHONPATH", "LD_PRELOAD", "OLDPWD"):
            self.assertNotIn(leak, names)
        self.assertNotIn(SECRET, out)
        self.assertIn(f"HOME={self.home}", lines)
        self.assertIn("TERM=xterm-256color", lines)
        self.assertIn("LANG=C.UTF-8", lines)
        self.assertIn("DISPLAY=:0", lines)

    @unittest.skipUnless(os.path.isfile("/bin/sh") and os.access("/bin/sh", os.X_OK), "no /bin/sh")
    def test_the_shell_is_a_login_shell(self):
        Path(self.home, ".profile").write_text("echo PROFILE-RAN\n")
        m = self.manager(shell="/bin/sh")
        s = m.open(80, 24, OWNER)
        collect(s, b"PROFILE-RAN")
        s.send(0, b"echo ARGV0=$0\n")
        self.assertIn(b"ARGV0=-", collect(s, b"ARGV0=-"), "a leading dash in $0 is what makes a shell a login shell")


class TestSession(TermCase):
    def test_input_goes_to_the_shell_and_output_comes_back(self):
        s = self.manager().open(80, 24, OWNER)
        self.assertEqual(s.send(0, b"hello\n"), ("ok", 1))
        out = collect(s, b"hello\r\nhello\r\n")
        self.assertEqual(out, b"hello\r\nhello\r\n", "the tty echoes, then cat answers")
        self.assertEqual(s.send(1, "grüße\n".encode()), ("ok", 2))
        self.assertIn("grüße\r\ngrüße\r\n".encode(), collect(s, "grüße\r\ngrüße\r\n".encode(), pos=len(out)))

    def test_control_characters_reach_the_shell(self):
        s = self.manager(shell=self.shell("echo READY\nexec cat\n")).open(80, 24, OWNER)
        collect(s, b"READY")
        s.send(0, b"a\x03")          # Ctrl-C: the tty turns it into SIGINT, which ends cat
        self.assertTrue(wait_for(lambda: s.exit is not None), "cat died of the interrupt")
        self.assertEqual(s.exit, 130)

    def test_a_retried_request_is_not_typed_twice(self):
        s = self.manager().open(80, 24, OWNER)
        self.assertEqual(s.send(0, b"a"), ("ok", 1))
        self.assertEqual(s.send(0, b"a"), ("dup", 1), "the answer got lost, the client sent it again")
        self.assertEqual(s.send(1, b"\n"), ("ok", 2))
        self.assertEqual(collect(s, b"a\r\na\r\n"), b"a\r\na\r\n")
        self.assertEqual(s.send(5, b"zzz"), ("gap", 2), "a client that is ahead is told where to start")
        self.assertEqual(s.send(0, b"zzz"), ("dup", 2))
        time.sleep(0.2)
        self.assertNotIn(b"z", bytes(s.ring))
        self.assertNotIn(b"aa", bytes(s.ring))

    def test_resize_reaches_the_program(self):
        s = self.manager(shell=self.shell("trap 'stty size' 28\nstty size\nwhile :; do sleep 0.05; done\n")).open(80, 24, OWNER)
        seen = collect(s, b"24 80\r\n")
        s.resize(100, 30)
        collect(s, b"30 100\r\n", pos=len(seen))
        self.assertEqual((s.info()["cols"], s.info()["rows"]), (100, 30))

    def test_a_full_screen_program_is_nudged_to_repaint_but_a_shell_prompt_is_left_alone(self):
        winch = "trap 'echo WINCH' 28\n"
        full = self.manager(shell=self.shell(winch + "printf '\\033[?1049h'\necho READY\nwhile :; do sleep 0.05; done\n", "full")).open(80, 24, OWNER)
        collect(full, b"READY")
        self.assertTrue(full.alt)
        full.resize(80, 24)
        time.sleep(0.3)
        self.assertNotIn(b"WINCH", bytes(full.ring), "same size, no redraw asked: nothing happens")
        full.resize(80, 24, redraw=True)
        collect(full, b"WINCH")
        plain = self.manager(shell=self.shell(winch + "echo READY\nwhile :; do sleep 0.05; done\n", "plain")).open(80, 24, OWNER)
        collect(plain, b"READY")
        self.assertFalse(plain.alt)
        plain.resize(80, 24, redraw=True)
        time.sleep(0.3)
        self.assertNotIn(b"WINCH", bytes(plain.ring))

    def test_the_alternate_screen_is_tracked_across_chunks(self):
        s = self.manager(shell=self.shell("printf '\\033[?10'\nsleep 0.2\nprintf '49h'\necho IN\nsleep 0.2\nprintf '\\033[?1049l'\necho OUT\nexec cat\n")).open(80, 24, OWNER)
        collect(s, b"IN")
        self.assertTrue(s.alt)
        collect(s, b"OUT")
        self.assertFalse(s.alt)

    def test_a_client_that_lost_its_connection_gets_the_same_shell_and_its_output(self):
        s = self.manager().open(80, 24, OWNER)
        s.send(0, b"one\n")
        first = collect(s, b"one\r\none\r\n")
        again = s.read(0)                          # a reloaded page asks from the start
        self.assertEqual((again.data, again.reset, again.alive), (first, False, True))
        half = s.read(len(b"one\r\n"))             # one that got part of it asks from where it stopped
        self.assertEqual(half.data, b"one\r\n")
        s.send(1, b"two\n")
        self.assertEqual(collect(s, b"two\r\ntwo\r\n", pos=len(first)), b"two\r\ntwo\r\n")

    def test_a_position_the_ring_no_longer_holds_starts_the_screen_afresh(self):
        sh = self.shell("i=0\nwhile [ $i -lt 200 ]; do echo \"line $i padding padding padding\"; i=$((i+1)); done\necho LAST\nexec cat\n")
        s = self.manager(shell=sh, ring_bytes=1024).open(80, 24, OWNER)
        wait_for(lambda: b"LAST" in bytes(s.ring))
        self.assertGreater(s.base, 0)
        self.assertLessEqual(len(s.ring), 1024)
        r = s.read(0)
        self.assertEqual((r.reset, r.start, r.data, r.end), (True, s.base, bytes(s.ring), s.end))
        self.assertEqual(s.read(s.end + 10).reset, True, "a position beyond the end is nonsense too")
        r = s.read(s.end)
        self.assertEqual((r.reset, r.data, r.next, r.alive), (False, b"", s.end, True))
        self.assertEqual(s.read(s.base).reset, False)

    def test_output_is_handed_out_in_chunks(self):
        sh = self.shell("head -c 100000 /dev/zero | tr '\\0' 'x'\necho DONE\nexec cat\n")
        s = self.manager(shell=sh).open(80, 24, OWNER)
        wait_for(lambda: b"DONE" in bytes(s.ring))
        r = s.read(0, max_bytes=1000)
        self.assertEqual((len(r.data), r.next), (1000, 1000))
        self.assertGreater(r.end, 100000)

    def test_a_held_request_wakes_when_the_shell_prints(self):
        s = self.manager().open(80, 24, OWNER)
        got = {}

        def poll():
            t0 = time.monotonic()
            got["read"] = s.read(s.end, wait=5)
            got["took"] = time.monotonic() - t0
        t = threading.Thread(target=poll)
        t.start()
        time.sleep(0.3)
        s.send(0, b"wake\n")
        t.join(6)
        self.assertIn(b"wake", got["read"].data)
        self.assertLess(got["took"], 3, "answered when the output came, not at the timeout")

    def test_a_held_request_times_out_empty_ends_for_a_gone_client_and_for_a_closing_server(self):
        s = self.manager().open(80, 24, OWNER)
        t0 = time.monotonic()
        r = s.read(s.end, wait=0.4)
        self.assertEqual((r.data, r.alive, r.reset), (b"", True, False))
        self.assertGreaterEqual(time.monotonic() - t0, 0.35)
        t0 = time.monotonic()
        self.assertIsNone(s.read(s.end, wait=5, alive=lambda: False))
        stop = threading.Event()
        stop.set()
        self.assertEqual(s.read(s.end, wait=5, stop=stop).data, b"")
        self.assertLess(time.monotonic() - t0, 2)

    def test_a_shell_that_ends_reports_how(self):
        m = self.manager(shell=self.shell("echo bye\nexit 3\n"))
        s = m.open(80, 24, OWNER)
        self.assertTrue(wait_for(lambda: s.exit is not None))
        r = s.read(0, wait=2)
        self.assertEqual((r.alive, r.exit), (False, 3))
        self.assertIn(b"bye", r.data)
        with self.assertRaises(T.TermError) as ctx:
            s.send(0, b"x")
        self.assertEqual(ctx.exception.code, "gone")
        with self.assertRaises(T.TermError):
            s.resize(90, 30)
        self.assertEqual([(i["alive"], i["exit"]) for i in m.listing(OWNER)], [(False, 3)])
        self.assertIn("terminal ended session=1 status=3", self.logs)
        m.sweep(OWNER)
        self.assertIsNotNone(m.get(s.id, OWNER), "kept for a while, so a reloaded page can show the end")
        self.clock.t += T.EXITED_TTL + 1
        m.sweep(OWNER)
        self.assertIsNone(m.get(s.id, OWNER))
        self.assertEqual(m.listing(OWNER), [])

    def test_a_signal_death_reads_like_a_shell_would_report_it(self):
        s = self.manager(shell=self.shell("kill -TERM $$\nsleep 5\n")).open(80, 24, OWNER)
        self.assertTrue(wait_for(lambda: s.exit is not None))
        self.assertEqual(s.exit, 128 + 15)

    def test_close_hangs_up_the_shell_and_forgets_the_session(self):
        m = self.manager()
        s = m.open(80, 24, OWNER)
        pid = s.proc.pid
        self.assertTrue(m.close(s.id, OWNER))
        self.assertFalse(m.close(s.id, OWNER), "closing twice does nothing")
        self.assertIsNone(m.get(s.id, OWNER))
        s.join(5)
        self.assertFalse(s.thread.is_alive())
        self.assertEqual(s.exit, 128 + 1)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertEqual(s.fd, -1, "the master end is closed")
        self.assertEqual(s.read(0).alive, False)
        self.assertIn("write terminal close session=1 reason=client", self.logs)
        m.sweep(OWNER)
        self.assertEqual(m.listing(OWNER), [])

    def test_a_shell_that_ignores_the_hangup_is_killed(self):
        m = self.manager(shell=self.shell("trap '' 1\necho READY\nwhile :; do sleep 1; done\n"))
        s = m.open(80, 24, OWNER)
        collect(s, b"READY")
        m.close(s.id, OWNER, "idle")
        s.join(10)
        self.assertFalse(s.thread.is_alive())
        self.assertEqual(s.exit, 128 + 9)

    def test_input_to_a_shell_that_does_not_read_fails_instead_of_hanging(self):
        s = self.manager(shell=self.shell("stty raw -echo\necho READY\nexec sleep 30\n")).open(80, 24, OWNER)
        collect(s, b"READY")
        with unittest.mock.patch.object(T, "WRITE_DEADLINE", 0.3):
            with self.assertRaises(T.TermError) as ctx:
                s.send(0, b"x" * 3_000_000)
        self.assertEqual(ctx.exception.code, "busy")
        self.assertEqual(s.in_seq, 1, "counted all the same: the retry is a dup, not a second typing")

    def test_the_output_of_a_session_is_never_logged(self):
        m = self.manager()
        s = m.open(80, 24, OWNER)
        s.send(0, b"typed-secret-text\n")
        collect(s, b"typed-secret-text\r\ntyped-secret-text")
        m.close(s.id, OWNER)
        text = "\n".join(self.logs)
        self.assertEqual(self.logs, ["write terminal open session=1", "write terminal close session=1 reason=client"])
        self.assertNotIn("typed-secret", text)
        self.assertNotIn(self.home, text)
        self.assertNotIn(s.id, text)


class TestManager(TermCase):
    def test_the_number_of_shells_is_bounded(self):
        m = self.manager(max_sessions=2)
        a, b = m.open(80, 24, OWNER), m.open(80, 24, OWNER)
        with self.assertRaises(T.TermError) as ctx:
            m.open(80, 24, OWNER)
        self.assertEqual(ctx.exception.code, "limit")
        self.assertEqual(len(m.listing(OWNER)), 2)
        m.close(a.id, OWNER)
        c = m.open(80, 24, OWNER)
        self.assertEqual([i["n"] for i in m.listing(OWNER)], [b.n, c.n])
        self.assertEqual(T.MAX_SESSIONS, 4)

    def test_an_ended_shell_does_not_hold_a_place(self):
        m = self.manager(max_sessions=1, shell=self.shell("exit 0\n"))
        first = m.open(80, 24, OWNER)
        self.assertTrue(wait_for(lambda: first.exit is not None))
        self.assertIsNotNone(m.open(80, 24, OWNER))

    def test_a_session_belongs_to_the_token_that_opened_it(self):
        m = self.manager()
        s = m.open(80, 24, OWNER)
        other = "b" * 16
        self.assertIs(m.get(s.id, OWNER), s)
        self.assertIsNone(m.get(s.id, other))
        self.assertEqual(m.listing(other), [])
        self.assertFalse(m.close(s.id, other))
        self.assertIsNotNone(m.get(s.id, OWNER))
        for bad in (None, 5, "", "short", "x" * 17, "a/b" + "c" * 13, "../../" + "x" * 10):
            self.assertIsNone(m.get(bad, OWNER))

    def test_ids_are_unguessable_and_distinct(self):
        m = self.manager(max_sessions=4)
        ids = {m.open(80, 24, OWNER).id for _ in range(3)}
        self.assertEqual(len(ids), 3)
        for i in ids:
            self.assertRegex(i, r"[A-Za-z0-9_-]{16}")

    def test_bad_sizes_never_reach_the_spawner(self):
        m = self.manager(spawn=lambda *a: self.fail("spawned"))
        with self.assertRaises(ValueError):
            m.open(5, 24, OWNER)

    def test_a_shell_that_cannot_start_is_a_clean_error(self):
        def refuse(*args):
            raise OSError(13, "Permission denied")
        m = self.manager(spawn=refuse)
        with self.assertRaises(T.TermError) as ctx:
            m.open(80, 24, OWNER)
        self.assertEqual(ctx.exception.code, "spawn")
        self.assertEqual(m.listing(OWNER), [])
        self.assertNotIn(self.home, ctx.exception.message)

    def test_nobody_looking_closes_a_session(self):
        m = self.manager(detached_ttl=60, idle_ttl=3600)
        s = m.open(80, 24, OWNER)
        self.clock.t += 59
        m.sweep(OWNER)
        self.assertIsNone(s.closed)
        s.read(0)                       # a client looks: the clock of "detached" starts again
        self.clock.t += 59
        m.sweep(OWNER)
        self.assertIsNone(s.closed)
        self.clock.t += 2
        m.sweep(OWNER)
        self.assertEqual(s.closed, "detached")
        self.assertIn("write terminal close session=1 reason=detached", self.logs)
        s.join(5)
        self.assertFalse(s.thread.is_alive())

    def test_no_keystroke_closes_a_session_even_while_a_client_looks(self):
        m = self.manager(detached_ttl=600, idle_ttl=1000)
        s = m.open(80, 24, OWNER)
        for _ in range(9):
            self.clock.t += 100
            s.read(0)                   # the page keeps polling
            m.sweep(OWNER)
        self.assertIsNone(s.closed)
        s.send(0, b"x")                 # a keystroke
        for _ in range(9):
            self.clock.t += 100
            s.read(0)
            m.sweep(OWNER)
        self.assertIsNone(s.closed, "the keystroke started the idle time again")
        self.clock.t += 101
        s.read(0)
        m.sweep(OWNER)
        self.assertEqual(s.closed, "idle")

    def test_a_rotated_or_missing_token_ends_the_session(self):
        m = self.manager()
        a, b = m.open(80, 24, OWNER), m.open(80, 24, "b" * 16)
        m.sweep("b" * 16)
        self.assertEqual((a.closed, b.closed), ("token", None))
        m.sweep(None)
        self.assertEqual(b.closed, "token")

    def test_the_limits_can_follow_the_settings(self):
        m = self.manager(detached_ttl=60, idle_ttl=3600)
        s = m.open(80, 24, OWNER)
        m.set_ttls(600, 3600)
        self.clock.t += 120
        m.sweep(OWNER)
        self.assertIsNone(s.closed)

    def test_shutdown_leaves_no_shell_behind(self):
        m = self.manager()
        sessions = [m.open(80, 24, OWNER) for _ in range(3)]
        pids = [s.proc.pid for s in sessions]
        m.shutdown()
        for s, pid in zip(sessions, pids):
            self.assertFalse(s.thread.is_alive())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        self.assertEqual(self.logs.count("write terminal close session=1 reason=shutdown"), 1)

    def test_a_shell_does_not_keep_the_servers_descriptors(self):
        sh = self.shell("ls /proc/self/fd | wc -l\necho DONE\nexec cat\n")
        s = self.manager(shell=sh).open(80, 24, OWNER)
        out = collect(s, b"DONE").decode().split()
        self.assertLessEqual(int(out[0]), 5, "0, 1, 2, the listing's own descriptor and at most one more")


class TestKeyedBuckets(unittest.TestCase):
    def test_each_device_has_its_own_allowance(self):
        clock = TR.Clock(0.0)
        b = R.KeyedBuckets(2.0, 3, clock)
        self.assertEqual([b.take("a") for _ in range(3)], [0.0, 0.0, 0.0])
        self.assertGreater(b.take("a"), 0)
        self.assertEqual(b.take("b"), 0.0)
        clock.t += 1
        self.assertEqual(b.take("a"), 0.0)

    def test_the_number_of_remembered_devices_is_bounded(self):
        b = R.KeyedBuckets(1.0, 1, TR.Clock(0.0), cap=3)
        for k in "abcd":
            b.take(k)
        self.assertEqual(len(b._buckets), 3)
        self.assertNotIn("a", b._buckets)


class TestRemoteSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "settings.json"

    def remote(self, data):
        if data is not None:
            self.path.write_text(json.dumps(data) if not isinstance(data, str) else data)
        return S.Settings(self.path).remote()

    def test_the_terminal_is_off_unless_the_file_says_true(self):
        self.assertEqual(self.remote(None), {"terminal": False, "terminal_detached_minutes": 15, "terminal_idle_minutes": 60})
        for stored in ({}, {"remote": {}}, {"remote": {"terminal": False}}, {"remote": {"terminal": 1}}, {"remote": {"terminal": "true"}},
                       {"remote": {"terminal": "yes"}}, {"remote": {"terminal": None}}, {"remote": "terminal"}, {"remote": [True]},
                       {"remote": True}, {"remote": {"Terminal": True}}, "{not json", "[1]"):
            with self.subTest(stored=str(stored)):
                self.assertIs(self.remote(stored)["terminal"], False)
        self.assertIs(self.remote({"remote": {"terminal": True}})["terminal"], True)

    def test_the_lifetimes_are_minutes_within_bounds(self):
        r = self.remote({"remote": {"terminal": True, "terminal_detached_minutes": 5, "terminal_idle_minutes": 240}})
        self.assertEqual((r["terminal_detached_minutes"], r["terminal_idle_minutes"]), (5, 240))
        for bad in (0, -3, 1441, 2.5, "10", True, None, [5]):
            with self.subTest(bad=bad):
                r = self.remote({"remote": {"terminal_detached_minutes": bad, "terminal_idle_minutes": bad}})
                self.assertEqual((r["terminal_detached_minutes"], r["terminal_idle_minutes"]), (15, 60))

    def test_saving_other_settings_keeps_the_key(self):
        self.path.write_text(json.dumps({"remote": {"terminal": True, "future": 1}}))
        s = S.Settings(self.path)
        s.write(s.theme(), s.keys(), [], s.web(), s.agent())
        self.assertEqual(json.loads(self.path.read_text())["remote"], {"terminal": True, "future": 1})
        self.assertIs(S.Settings(self.path).remote()["terminal"], True)


class TestTheme(unittest.TestCase):
    def test_the_terminal_colours_come_from_the_theme_with_defaults_for_bad_values(self):
        default = S.PRESETS[S.DEFAULT_PRESET]["terminal"]
        self.assertEqual(R.ansi_colors(default), default["ansi"] + default["brights"])
        self.assertEqual(R.ansi_colors(None), default["ansi"] + default["brights"])
        got = R.ansi_colors({"ansi": ["#112233", "red;}", 5, None], "brights": "nope"})
        self.assertEqual(got[0], "#112233")
        self.assertEqual(got[1:], (default["ansi"] + default["brights"])[1:])
        self.assertEqual(len(got), 16)

    def test_theme_css_names_the_sixteen_colours(self):
        css = R.theme_css({}, R.ansi_colors(None))
        self.assertEqual(re.findall(r"--ansi(\d+): #[0-9a-f]{6};", css), [str(i) for i in range(16)])
        self.assertNotIn("--ansi", R.theme_css({}))


class TerminalAppCase(TR.AppCase):
    """The API against a real manager that runs `cat` as the shell."""

    def setUp(self):
        super().setUp()
        self.cat = script(self.home, "cat-shell", "exec cat\n")
        self.spawned = []
        self.terms = self.make_manager()
        self.app = self.make_app(terminals=self.terms)
        self.tick = 0.05

    def make_manager(self, **kw):
        environ = {"HOME": self.home, "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        m = T.Manager(clock=self.clock, environ=environ, shell=self.cat, home=self.home, log=self.logs.append, **kw)
        self.addCleanup(m.shutdown)
        return m

    def enable(self, value=True):
        Path(self.settings).write_text(json.dumps({"remote": {"terminal": value}}))

    def open(self, cols=80, rows=24, **kw):
        r, data = self.call("POST", "/api/terminals", {"cols": cols, "rows": rows}, **kw)
        self.assertEqual(r.status, 200, data)
        return data

    def out(self, sid, pos=0, wait=0, **kw):
        return self.call("GET", f"/api/terminals/{sid}/out?from={pos}&wait={wait}", **kw)

    def typed(self, sid, seq, text, **kw):
        return self.call("POST", f"/api/terminals/{sid}/in", {"seq": seq, "data": text}, **kw)

    def text(self, sid, want, pos=0, timeout=5.0):
        """Output text from `pos` until `want` appears (API reads, so the whole path is exercised)."""
        seen = b""
        end = time.monotonic() + timeout
        while want.encode() not in seen:
            if time.monotonic() > end:
                raise AssertionError(f"{want!r} not in {seen!r}")
            r, data = self.out(sid, pos, wait=1)
            self.assertEqual(r.status, 200, data)
            seen += base64.b64decode(data["data"])
            pos = data["next"]
        return seen


class TestTerminalOffByDefault(TerminalAppCase):
    ROUTES = (("GET", "/api/terminals", None), ("POST", "/api/terminals", {}),
              ("GET", "/api/terminals/abcdefghijklmnop/out", None), ("POST", "/api/terminals/abcdefghijklmnop/in", {"seq": 0, "data": "x"}),
              ("POST", "/api/terminals/abcdefghijklmnop/resize", {"cols": 80, "rows": 24}), ("POST", "/api/terminals/abcdefghijklmnop/close", {}))

    def setUp(self):
        super().setUp()
        self.terms = self.make_manager(spawn=lambda *a: self.fail("a shell was started although the terminal is off"))
        self.app = self.make_app(terminals=self.terms)

    def test_nothing_is_on_without_the_setting_or_the_flag(self):
        for method, path, body in self.ROUTES:
            with self.subTest(route=f"{method} {path}"):
                r, data = self.call(method, path, body)
                self.assertEqual((r.status, data["error"]), (403, "terminal_disabled"))
        meta = self.call("GET", "/api/meta")[1]
        self.assertIs(meta["terminal"], False)
        self.assertNotIn("terminal", meta["capabilities"])
        self.assertEqual(self.terms.listing(R.hashlib.sha256(TOKEN.encode()).hexdigest()[:16]), [])

    def test_settings_that_do_not_say_true_leave_it_off(self):
        for stored in ({}, {"remote": {}}, {"remote": {"terminal": False}}, {"remote": {"terminal": "true"}}, {"remote": {"terminal": 1}}, "{oops"):
            with self.subTest(stored=str(stored)):
                Path(self.settings).write_text(json.dumps(stored) if not isinstance(stored, str) else stored)
                self.clock.t += 1
                self.assertEqual(self.call("POST", "/api/terminals", {})[1]["error"], "terminal_disabled")

    def test_the_setting_turns_it_on_and_taking_it_away_turns_it_off_at_once(self):
        self.enable()
        meta = self.call("GET", "/api/meta")[1]
        self.assertIs(meta["terminal"], True)
        self.assertIn("terminal", meta["capabilities"])
        self.assertEqual(self.call("GET", "/api/terminals")[0].status, 200)
        self.enable(False)
        self.assertEqual(self.call("GET", "/api/terminals")[1]["error"], "terminal_disabled")
        self.assertIs(self.call("GET", "/api/meta")[1]["terminal"], False)

    def test_the_flag_alone_turns_it_on(self):
        app = self.make_app(terminal=True, terminals=self.make_manager())
        self.assertEqual(self.call("GET", "/api/terminals", app=app)[0].status, 200)
        self.assertIs(self.call("GET", "/api/meta", app=app)[1]["terminal"], True)

    def test_the_agents_list_tells_the_page_to_reload_the_meta_when_it_switches(self):
        before = self.call("GET", "/api/agents")[1]["meta_rev"]
        self.assertEqual(before, self.call("GET", "/api/meta")[1]["meta_rev"])
        self.enable()
        after = self.call("GET", "/api/agents")[1]["meta_rev"]
        self.assertNotEqual(before, after)
        self.assertEqual(after, self.call("GET", "/api/meta")[1]["meta_rev"])


class TestTerminalRefusals(TerminalAppCase):
    def setUp(self):
        super().setUp()
        self.enable()

    def test_read_only_refuses_every_route_even_when_enabled_twice(self):
        app = self.make_app(readonly=True, terminal=True, terminals=self.make_manager(spawn=lambda *a: self.fail("started")))
        for method, path, body in TestTerminalOffByDefault.ROUTES:
            with self.subTest(route=f"{method} {path}"):
                r, data = self.call(method, path, body, app=app)
                self.assertEqual((r.status, data["error"]), (403, "readonly"))
        meta = self.call("GET", "/api/meta", app=app)[1]
        self.assertEqual((meta["readonly"], meta["terminal"]), (True, False))
        self.assertNotIn("terminal", meta["capabilities"])

    def test_no_token_or_a_wrong_one_is_401_and_counts_as_a_failed_guess(self):
        self.tick = 61      # one guess per lock window: this is about the route, the lockout has its own tests
        for token in (None, "wrong", TOKEN + "x"):
            for method, path, body in TestTerminalOffByDefault.ROUTES:
                with self.subTest(token=token, route=f"{method} {path}"):
                    r, data = self.call(method, path, body, token=token)
                    self.assertEqual((r.status, data["error"]), (401, "unauthorized"))
        self.assertEqual(self.terms.listing("x"), [])

    def test_the_host_must_be_a_known_name(self):
        for method, path, body in TestTerminalOffByDefault.ROUTES:
            with self.subTest(route=f"{method} {path}"):
                r, data = self.call(method, path, body, host="evil.example")
                self.assertEqual((r.status, data["error"]), (403, "forbidden_host"))

    def test_posts_need_the_json_headers_and_a_same_site_origin(self):
        cases = (({"origin": "https://evil.example"}, 403, "forbidden_origin"),
                 ({"x-hivegrid-remote": None}, 403, "forbidden_origin"),
                 ({"content-type": "text/plain"}, 415, "unsupported_media_type"),
                 ({"origin": "http://127.0.0.1:8787"}, 200, None))
        for headers, status, error in cases:
            with self.subTest(headers=headers):
                r, data = self.call("POST", "/api/terminals", {}, headers=headers)
                self.assertEqual(r.status, status, data)
                if error:
                    self.assertEqual(data["error"], error)
                self.terms.close_all("test")
                self.clock.t += 10

    def test_the_methods_and_routes(self):
        sid = self.open()["id"]
        for method, path in (("POST", f"/api/terminals/{sid}/out"), ("GET", f"/api/terminals/{sid}/in"), ("GET", f"/api/terminals/{sid}/close"),
                             ("GET", f"/api/terminals/{sid}/resize")):
            with self.subTest(route=f"{method} {path}"):
                self.assertEqual(self.call(method, path, {} if method == "POST" else None)[0].status, 405)
        for path in (f"/api/terminals/{sid}", f"/api/terminals/{sid}/other", f"/api/terminals/{sid}/out/x", "/api/terminals/"):
            with self.subTest(path=path):
                self.assertEqual(self.call("GET", path)[0].status, 404)
        for method in ("PUT", "DELETE", "PATCH"):
            self.assertEqual(self.call(method, "/api/terminals")[0].status, 405)

    def test_an_unknown_or_foreign_session_is_404(self):
        self.open()
        for sid in ("abcdefghijklmnop", "short", "x" * 40, "..%2f..%2fetc"):
            for method, tail, body in (("GET", "out", None), ("POST", "in", {"seq": 0, "data": "x"}), ("POST", "resize", {"cols": 80, "rows": 24}),
                                       ("POST", "close", {})):
                with self.subTest(sid=sid, tail=tail):
                    r, data = self.call(method, f"/api/terminals/{sid}/{tail}", body)
                    self.assertEqual((r.status, data["error"]), (404, "not_found"))

    def test_bodies_are_validated(self):
        sid = self.open()["id"]
        for path, body in (("", {"cols": 5, "rows": 24}), ("", {"cols": 80, "rows": "24"}), ("", {"cols": 80, "rows": 24, "shell": "/bin/sh"}),
                           ("", {"cmd": "id"}), (f"/{sid}/in", {"seq": -1, "data": "x"}), (f"/{sid}/in", {"seq": 0, "data": ""}),
                           (f"/{sid}/in", {"seq": 0, "data": "x", "extra": 1}), (f"/{sid}/in", {"seq": 0, "data": "x" * (T.MAX_INPUT + 1)}),
                           (f"/{sid}/resize", {"cols": 80}), (f"/{sid}/resize", {"cols": 500, "rows": 24}),
                           (f"/{sid}/resize", {"cols": 80, "rows": 24, "redraw": "yes"}), (f"/{sid}/close", {"force": True})):
            with self.subTest(path=path, body=body):
                r, data = self.call("POST", "/api/terminals" + path, body)
                self.assertEqual((r.status, data["error"]), (400, "bad_request"))
        for query in ("from=-1", "from=x", "wait=x", "wait=999x"):
            with self.subTest(query=query):
                self.assertEqual(self.call("GET", f"/api/terminals/{sid}/out?{query}")[0].status, 400)
        self.assertEqual(self.call("GET", f"/api/terminals/{sid}/out?wait=999")[0].status, 200, "an over-long wait is cut to the maximum")


class TestTerminalFlow(TerminalAppCase):
    def setUp(self):
        super().setUp()
        self.enable()

    def test_open_type_read_resize_list_close(self):
        opened = self.open(100, 30)
        sid = opened["id"]
        self.assertEqual((opened["cols"], opened["rows"], opened["alive"], opened["in"], opened["end"], opened["n"]), (100, 30, True, 0, 0, 1))
        self.assertEqual(opened["limits"]["input"], T.MAX_INPUT)
        self.assertEqual(opened["limits"]["max"], 4)
        r, data = self.typed(sid, 0, "echo-me\r")
        self.assertEqual((r.status, data), (200, {"ok": True, "in": 1, "dup": False}))
        seen = self.text(sid, "echo-me\r\necho-me\r\n")
        self.assertEqual(seen, b"echo-me\r\necho-me\r\n")
        r, data = self.out(sid, 0)
        self.assertEqual((r.status, data["from"], data["next"], data["end"], data["reset"], data["alive"], data["exit"], data["in"]),
                         (200, 0, len(seen), len(seen), False, True, None, 1))
        self.assertEqual((data["cols"], data["rows"]), (100, 30))
        self.assertEqual(self.call("POST", f"/api/terminals/{sid}/resize", {"cols": 120, "rows": 40})[1], {"ok": True})
        listing = self.call("GET", "/api/terminals")[1]
        self.assertEqual([(s["id"], s["cols"], s["rows"], s["in"]) for s in listing["sessions"]], [(sid, 120, 40, 1)])
        self.assertEqual(self.call("POST", f"/api/terminals/{sid}/close", {})[1], {"ok": True})
        self.assertEqual(self.call("GET", f"/api/terminals/{sid}/out")[0].status, 404)
        self.assertEqual(self.call("GET", "/api/terminals")[1]["sessions"], [])

    def test_a_reloaded_page_lists_the_session_and_reads_it_from_the_start(self):
        sid = self.open()["id"]
        self.typed(sid, 0, "kept\r")
        seen = self.text(sid, "kept\r\nkept\r\n")
        listing = self.call("GET", "/api/terminals")[1]["sessions"]
        self.assertEqual([s["id"] for s in listing], [sid])
        self.assertEqual(listing[0]["in"], 1, "the page continues numbering from here")
        r, data = self.out(sid, 0)
        self.assertEqual(base64.b64decode(data["data"]), seen)
        self.assertEqual(self.typed(sid, listing[0]["in"], "more\r")[1]["in"], 2)

    def test_a_retry_is_recognised_and_a_gap_is_reported(self):
        sid = self.open()["id"]
        self.assertEqual(self.typed(sid, 0, "a")[1], {"ok": True, "in": 1, "dup": False})
        self.assertEqual(self.typed(sid, 0, "a")[1], {"ok": True, "in": 1, "dup": True})
        r, data = self.typed(sid, 4, "zz")
        self.assertEqual((r.status, data["error"], data["in"]), (409, "conflict", 1))

    def test_an_ended_shell_is_reported_and_refuses_input(self):
        self.terms.shell = script(self.home, "short-lived", "echo gone-now\nexit 7\n")
        sid = self.open()["id"]
        self.assertTrue(wait_for(lambda: self.out(sid)[1]["exit"] == 7))
        data = self.out(sid)[1]
        self.assertEqual((data["alive"], data["exit"]), (False, 7))
        self.assertIn(b"gone-now", base64.b64decode(data["data"]))
        r, data = self.typed(sid, 0, "x")
        self.assertEqual((r.status, data["error"]), (409, "conflict"))

    def test_the_session_limit_and_the_opening_rate(self):
        ids = []
        for _ in range(T.MAX_SESSIONS):
            ids.append(self.open()["id"])
            self.clock.t += R.TERM_OPEN_EVERY
        r, data = self.call("POST", "/api/terminals", {})
        self.assertEqual((r.status, data["error"]), (409, "conflict"))
        self.assertIn("4 terminals are open", data["message"])
        self.call("POST", f"/api/terminals/{ids[0]}/close", {})
        self.clock.t += R.TERM_OPEN_EVERY
        self.open()

    def test_opening_is_rate_limited_per_device(self):
        self.tick = 0
        first = [self.call("POST", "/api/terminals", {})[0].status for _ in range(R.TERM_OPEN_BURST)]
        self.assertEqual(first, [200] * R.TERM_OPEN_BURST)
        self.terms.close_all("test")
        r, data = self.call("POST", "/api/terminals", {})
        self.assertEqual((r.status, data["error"]), (429, "rate_limited"))
        self.assertGreaterEqual(int(r.headers["Retry-After"]), 1)
        r, data = self.call("POST", "/api/terminals", {}, headers={"x-forwarded-for": "100.64.0.9"})
        self.assertEqual(r.status, 200, "another device has its own allowance")

    def test_terminal_traffic_has_its_own_allowance_per_device(self):
        self.tick = 0
        sid = self.open()["id"]
        statuses = [self.out(sid)[0].status for _ in range(R.TERM_IO_BURST + 5)]
        self.assertEqual(statuses.count(200), R.TERM_IO_BURST - 1)
        r, data = self.out(sid)
        self.assertEqual((r.status, data["error"]), (429, "rate_limited"))
        self.assertEqual(self.call("GET", "/api/meta")[0].status, 200, "the agent calls are not starved")
        self.assertEqual(self.out(sid, headers={"x-forwarded-for": "100.64.0.9"})[0].status, 200, "nor another device")
        self.clock.t += 1
        self.assertEqual(self.out(sid)[0].status, 200)

    def test_a_rotated_token_ends_the_terminals_of_the_old_one(self):
        sid = self.open()["id"]
        session = self.terms.get(sid, R.hashlib.sha256(TOKEN.encode()).hexdigest()[:16])
        new = R.generate_token()
        R.write_token(self.tokfile, new)
        self.assertEqual(self.out(sid)[0].status, 401)
        r, data = self.call("GET", "/api/terminals", token=new)
        self.assertEqual((r.status, data["sessions"]), (200, []))
        self.assertEqual(self.out(sid, token=new)[0].status, 404, "the new token cannot reach the old session either")
        self.assertEqual(session.closed, "token")
        session.join(5)
        self.assertFalse(session.thread.is_alive())

    def test_switching_the_setting_off_ends_the_shells(self):
        sid = self.open()["id"]
        session = self.terms.get(sid, R.hashlib.sha256(TOKEN.encode()).hexdigest()[:16])
        self.enable(False)
        self.assertEqual(self.out(sid)[1]["error"], "terminal_disabled")
        self.assertEqual(session.closed, "disabled")
        self.enable(True)
        self.assertEqual(self.out(sid)[0].status, 404, "turning it on again does not bring the old shell back")

    def test_idle_and_detached_time_follow_the_settings(self):
        Path(self.settings).write_text(json.dumps({"remote": {"terminal": True, "terminal_detached_minutes": 2, "terminal_idle_minutes": 30}}))
        sid = self.open()["id"]
        self.assertEqual(self.call("GET", "/api/terminals")[1]["limits"]["detached"], 120)
        self.assertEqual(self.terms.idle_ttl, 1800)
        self.tick = 0
        self.clock.t += 121
        self.app.terminal_tick()
        self.assertEqual(self.call("GET", "/api/terminals")[1]["sessions"], [])
        self.assertEqual(self.out(sid)[0].status, 404)

    def test_the_log_holds_the_actions_only(self):
        sid = self.open()["id"]
        self.typed(sid, 0, f"{SECRET}\r")
        self.text(sid, SECRET + "\r\n" + SECRET)
        self.call("POST", f"/api/terminals/{sid}/close", {})
        text = "\n".join(self.logs)
        self.assertIn("write terminal open session=1", text)
        self.assertIn("write terminal close session=1 reason=client", text)
        for forbidden in (SECRET, sid, self.home, "cat-shell", TOKEN):
            self.assertNotIn(forbidden, text)

    def test_the_terminal_is_not_a_pane_of_the_window(self):
        sid = self.open()["id"]
        self.typed(sid, 0, "x")
        self.assertEqual(self.sends(), [], "nothing went through wezterm cli")
        self.assertEqual([a for a in self.fake.calls if a[0] in ("spawn", "send-text", "kill-pane")], [])


class TestStatic(TR.AppCase):
    def test_the_emulator_is_served_to_the_page_without_a_token(self):
        for path, ctype in (("/term.js", "text/javascript"), ("/term-view.js", "text/javascript"), ("/term.css", "text/css")):
            r, _ = self.call("GET", path, token=None)
            self.assertEqual(r.status, 200, path)
            self.assertTrue(r.content_type.startswith(ctype))
            self.assertGreater(len(r.body), 1000)
            self.assertEqual(r.headers["Cache-Control"], "no-cache")
            self.assertEqual(self.call("GET", path, token=None, headers={"if-none-match": r.headers["ETag"]})[0].status, 304)
            self.assertEqual(self.call("GET", path, token=None, host="evil.example")[0].status, 403)
        self.assertEqual(self.call("POST", "/term.js", {}, token=None)[0].status, 405)

    def test_the_theme_carries_the_terminal_colours(self):
        css = self.call("GET", "/theme.css", token=None)[0].body.decode()
        default = S.PRESETS[S.DEFAULT_PRESET]["terminal"]
        self.assertIn(f"--ansi0: {default['ansi'][0]};", css)
        self.assertIn(f"--ansi15: {default['brights'][7]};", css)
        Path(self.settings).write_text(json.dumps({"theme": {"preset": "forest"}}))
        css = self.call("GET", "/theme.css", token=None)[0].body.decode()
        self.assertIn(f"--ansi1: {S.PRESETS['forest']['terminal']['ansi'][1]};", css)
        self.assertIn(f"--ansi8: {S.PRESETS['forest']['terminal']['brights'][0]};", css)


class TestTerminalOverHttp(TH.HttpCase):
    """The terminal over a real socket: held polls, the answer to a client that left, what the request log shows."""

    def make_app(self, **kw):
        self.cat = script(self.home, "cat-shell", "exec cat\n")
        environ = {"HOME": self.home, "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        self.terms = T.Manager(clock=self.clock, environ=environ, shell=self.cat, home=self.home, log=self.logs.append)
        self.addCleanup(self.terms.shutdown)
        return super().make_app(terminals=self.terms, terminal=True, **kw)

    def setUp(self):
        super().setUp()
        self.tick = 0.05

    def test_a_held_poll_is_answered_when_the_shell_prints_and_the_log_shows_no_ids(self):
        r, opened = self.jreq("POST", "/api/terminals", {"cols": 80, "rows": 24})
        self.assertEqual(r.status, 200, opened)
        sid = opened["id"]
        got = {}

        def poll():
            t0 = time.monotonic()
            got["r"], got["doc"] = self.jreq("GET", f"/api/terminals/{sid}/out?from=0&wait=10")
            got["took"] = time.monotonic() - t0
        t = threading.Thread(target=poll)
        t.start()
        time.sleep(0.4)
        r, doc = self.jreq("POST", f"/api/terminals/{sid}/in", {"seq": 0, "data": f"{SECRET}\r"})
        self.assertEqual((r.status, doc["ok"]), (200, True))
        t.join(12)
        self.assertEqual(got["r"].status, 200)
        self.assertLess(got["took"], 6, "answered when the output came")
        self.assertIn(SECRET, base64.b64decode(got["doc"]["data"]).decode())
        self.jreq("POST", f"/api/terminals/{sid}/close", {})
        time.sleep(0.2)
        text = "\n".join(self.logs)
        self.assertIn("POST /api/terminals 200", text)
        self.assertIn("write terminal open session=1", text)
        for forbidden in (sid, SECRET, self.home, "out?from", "wait=10"):
            self.assertNotIn(forbidden, text)
        self.assertNotIn("/in", text)
        self.jreq("GET", "/api/terminals/abcdefghijklmnop/out")
        self.assertIn("GET /api/terminals/* 404", "\n".join(self.logs), "a failure is logged without the id")

    def test_the_answers_carry_the_security_headers_and_no_cors(self):
        r, opened = self.jreq("POST", "/api/terminals", {})
        sid = opened["id"]
        for method, path, body in (("GET", "/api/terminals", None), ("GET", f"/api/terminals/{sid}/out", None),
                                   ("POST", f"/api/terminals/{sid}/in", {"seq": 0, "data": "x"}), ("GET", "/term.js", None)):
            r, _ = self.req(method, path, body)
            self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff", path)
            self.assertIn("script-src 'self'", r.getheader("Content-Security-Policy"))
            self.assertEqual(r.getheader("Content-Security-Policy").count("unsafe"), 0)
            self.assertFalse([n for n, _ in r.getheaders() if n.lower().startswith("access-control-")])

    def test_a_held_poll_of_a_client_that_left_is_dropped(self):
        r, opened = self.jreq("POST", "/api/terminals", {})
        sid = opened["id"]
        import socket
        c = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        c.sendall((f"GET /api/terminals/{sid}/out?from=0&wait=20 HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
                   f"Authorization: Bearer {TOKEN}\r\n\r\n").encode())
        time.sleep(0.4)
        c.close()
        t0 = time.monotonic()
        ok = wait_for(lambda: not self.server._conns, timeout=4)
        self.assertTrue(ok, "the connection slot was freed within a look")
        self.assertLess(time.monotonic() - t0, 4)

    def test_a_terminal_survives_the_connection_that_opened_it(self):
        r, opened = self.jreq("POST", "/api/terminals", {})
        sid = opened["id"]
        self.jreq("POST", f"/api/terminals/{sid}/in", {"seq": 0, "data": "across\r"})
        time.sleep(0.3)
        r, doc = self.jreq("GET", f"/api/terminals/{sid}/out?from=0")   # another connection altogether
        self.assertEqual(base64.b64decode(doc["data"]), b"across\r\nacross\r\n")


class TestFingerprint(unittest.TestCase):
    def test_the_fingerprint_names_the_token_without_revealing_it(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "token")
            store = R.TokenStore(path)
            self.assertIsNone(store.fingerprint())
            R.write_token(path, TOKEN)
            fp = store.fingerprint()
            self.assertEqual(fp, R.hashlib.sha256(TOKEN.encode()).hexdigest()[:16])
            self.assertNotIn(TOKEN[:8], fp)
            R.write_token(path, R.generate_token())
            self.assertNotEqual(store.fingerprint(), fp)
            os.chmod(path, 0o644)
            self.assertIsNone(store.fingerprint(), "an unsafe token file is no token")


if __name__ == "__main__":
    unittest.main()
