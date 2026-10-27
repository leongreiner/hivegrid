import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import hivegrid_web_signin as W  # noqa: E402
import hivegrid_settings_core as core  # noqa: E402


class SignIn(unittest.TestCase):
    def test_the_profile_directory_is_the_panes(self):
        self.assertEqual(W.profile_dir("main", {"XDG_DATA_HOME": "/x/d"}), Path("/x/d/hivegrid/web/profile-main"))
        with tempfile.TemporaryDirectory() as h:
            os.environ["HOME"], old = h, os.environ.get("HOME")
            try:
                self.assertEqual(W.profile_dir("w.1-a_b", {"XDG_DATA_HOME": "rel"}),
                                 Path(h) / ".local/share/hivegrid/web/profile-w.1-a_b", "a relative XDG_DATA_HOME is ignored")
            finally:
                os.environ["HOME"] = old

    def test_a_running_browser_holds_the_profile_but_a_stale_lock_does_not(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(W.browser_in_use(d))
            os.symlink("some-host-4242", os.path.join(d, "SingletonLock"))
            self.assertEqual(W.browser_in_use(d, alive=lambda pid: pid == 4242), 4242)
            self.assertIsNone(W.browser_in_use(d, alive=lambda pid: False))

    def test_the_browser_choice_follows_the_panes_precedence(self):
        auto = dict(core.WEB_DEFAULTS)
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a"), os.path.join(d, "b")
            for p in (a, b):
                Path(p).write_text("#!/bin/sh\n")
                os.chmod(p, 0o755)
            found = lambda: ([("Google Chrome", b)], [])
            self.assertEqual(W.choose_browser(auto, {"HIVEGRID_CHROME": a}, found), (a, None))
            self.assertEqual(W.choose_browser(dict(auto, browser=a), {}, found), (a, None))
            self.assertEqual(W.choose_browser(auto, {}, found), (b, None))
            self.assertIsNone(W.choose_browser(dict(auto, browser="/nope"), {}, found)[0], "a missing choice does not fall back")
            self.assertIsNone(W.choose_browser(auto, {"HIVEGRID_CHROME": "/nope"}, found)[0])
            self.assertIsNone(W.choose_browser(auto, {}, lambda: ([], []))[0])

    def test_the_window_is_plain_chrome_with_the_panes_password_store(self):
        cmd = W.command("/b/chrome", "/d/profile-main")
        self.assertEqual(cmd[0], "/b/chrome")
        self.assertIn("--user-data-dir=/d/profile-main", cmd)
        self.assertIn("--password-store=basic", cmd)
        self.assertFalse([a for a in cmd if "remote-debugging" in a or "headless" in a])

    def test_main_refuses_a_busy_profile_and_runs_chrome_otherwise(self):
        with tempfile.TemporaryDirectory() as h:
            env = {"HOME": h, "XDG_DATA_HOME": "", "HIVEGRID_SETTINGS": os.path.join(h, "s.json"), "HIVEGRID_CHROME": "/bin/true"}
            old = {k: os.environ.get(k) for k in env}
            os.environ.update(env)
            ran = []
            try:
                self.assertEqual(W.main([], run=lambda c, **kw: ran.append(c) or 0), 0)
                self.assertEqual(len(ran), 1)
                lock = W.profile_dir("main") / "SingletonLock"
                os.symlink(f"host-{os.getpid()}", lock)
                self.assertEqual(W.main([], run=lambda c, **kw: ran.append(c) or 0), 1)
                self.assertEqual(len(ran), 1, "nothing was started")
                self.assertEqual(W.main(["../x"], run=lambda c, **kw: 0), 2)
            finally:
                for k, v in old.items():
                    os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


if __name__ == "__main__":
    unittest.main()
