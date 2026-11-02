"""hivegrid-remote attach: the host pane that shows a phone terminal (bin/hivegrid_remote_attach.py) and the
placement of that pane (bin/hivegrid_agents.py). Fakes only: no server, no window, no real shell."""
import base64
import os
import sys
import threading
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import hivegrid_agents as A  # noqa: E402
import hivegrid_remote_attach as AT  # noqa: E402
from test_agents import FakeMux, pane  # noqa: E402


class FakeClient:
    def __init__(self, outs, sends=()):
        self.outs, self.sends, self.sent = list(outs), list(sends), []

    def out(self, pos):
        if not self.outs:
            raise AT.Gone(404)
        return 200, self.outs.pop(0)

    def send(self, seq, text):
        self.sent.append((seq, text))
        return self.sends.pop(0) if self.sends else (200, {"in": seq + 1})


def chunk(text, **kw):
    return {"data": base64.b64encode(text.encode()).decode(), "next": 1, "in": 0, "alive": True, **kw}


class TestMirror(unittest.TestCase):
    def test_output_is_written_until_the_shell_ends(self):
        c, out = FakeClient([chunk("hi "), chunk("there", alive=False, exit=0)]), []
        ended = AT.mirror(c, AT.Typist(c), out.append, threading.Event())
        self.assertEqual((ended, b"".join(out)), (True, b"hi there"))

    def test_a_reset_clears_the_screen_first(self):
        c, out = FakeClient([chunk("x", reset=True, alive=False)]), []
        AT.mirror(c, AT.Typist(c), out.append, threading.Event())
        self.assertEqual(out, [b"\x1bc", b"x"])

    def test_a_terminal_that_is_gone_ends_the_pane(self):
        c = FakeClient([])
        self.assertTrue(AT.mirror(c, AT.Typist(c), lambda d: None, threading.Event()))

    def test_detaching_leaves_the_shell_alone(self):
        stop = threading.Event()
        stop.set()
        c = FakeClient([chunk("never")])
        self.assertFalse(AT.mirror(c, AT.Typist(c), lambda d: None, stop))
        self.assertEqual(c.sent, [])


class TestTypist(unittest.TestCase):
    def test_it_follows_the_number_the_server_expects(self):
        c = FakeClient([], [(409, {"in": 7}), (200, {"in": 8})])
        t = AT.Typist(c)
        self.assertTrue(t.type("ls\r"))
        self.assertEqual((c.sent, t.seq), ([(0, "ls\r"), (7, "ls\r")], 8), "typed once, at the right number")

    def test_a_refusal_types_nothing_more(self):
        c = FakeClient([], [(503, {})])
        self.assertFalse(AT.Typist(c).type("x"))
        self.assertEqual(len(c.sent), 1)


class TestPlacement(unittest.TestCase):
    def fg(self, mapping):
        return lambda: mapping

    def test_a_terminal_window_is_never_the_origin_of_new_agents(self):
        term = dict(pane(9, "x"), tab_id=5, window_id=2, tty_name="/dev/pts/9")
        main = dict(pane(3, "π > ready"), tab_id=1, window_id=1, tty_name="/dev/pts/3")
        m = FakeMux([main, term])
        orig = A.foreground_by_tty
        A.foreground_by_tty = self.fg({"pts/9": (1, "python3", 0, "python3 hivegrid-remote attach --port 1 s")})
        try:
            self.assertEqual(A.origin(m)[0], 3)
        finally:
            A.foreground_by_tty = orig

    def test_the_first_terminal_opens_a_window_and_the_next_splits_it(self):
        main = dict(pane(3, "π"), tab_id=1, window_id=1, tty_name="/dev/pts/3")
        calls = []
        m = FakeMux([main], {"spawn": lambda a: calls.append(a) or "9\n"})
        orig = A.foreground_by_tty
        A.foreground_by_tty = self.fg({})
        try:
            self.assertEqual(A.spawn_terminal_pane(["hivegrid-remote", "attach", "--port", "1", "sid"], "/h", run=m), 9)
        finally:
            A.foreground_by_tty = orig
        self.assertIn("--new-window", calls[0])
        self.assertIn("hivegrid_arrange_new", calls[0][-1], "the new window asks to be arranged")

    def test_a_later_terminal_splits_the_terminal_window_and_the_keyboard_stays(self):
        from test_agents import Tiler, gpane
        main = gpane(3, 0, 0, 200, 60, tab=1, window=1, active=True)
        term_a = gpane(7, 0, 0, 100, 50, tab=5, window=2, active=True)
        term_b = gpane(8, 101, 0, 99, 50, tab=5, window=2)
        t = Tiler([main, term_a, term_b])
        orig = A.foreground_by_tty
        marker = "python3 hivegrid-remote attach --port 1 s"
        A.foreground_by_tty = self.fg({"pts/7": (1, "python3", 0, marker), "pts/8": (1, "python3", 0, marker)})
        try:
            new = A.spawn_terminal_pane(["hivegrid-remote", "attach", "--port", "1", "sid"], "/h", run=t)
        finally:
            A.foreground_by_tty = orig
        self.assertEqual(t.panes[new]["tab_id"], 5, "in the terminal window's tab, never the agents' tab")
        split = next(c for c in t.calls if c[0] == "split-pane")
        self.assertEqual(split[split.index("--pane-id") + 1], "8", "off the last terminal in reading order")
        self.assertTrue(t.panes[7]["is_active"], "the terminal that had the keys keeps them")
        self.assertFalse(t.panes[new]["is_active"])
        self.assertTrue(t.panes[3]["is_active"], "the agents' window is untouched")

    def test_a_zoomed_terminal_window_is_unzoomed_before_the_split(self):
        from test_agents import Tiler, gpane
        main = gpane(3, 0, 0, 200, 60, tab=1, window=1, active=True)
        term = gpane(7, 0, 0, 200, 50, tab=5, window=2, active=True, zoomed=True)
        t = Tiler([main, term])
        orig = A.foreground_by_tty
        A.foreground_by_tty = self.fg({"pts/7": (1, "python3", 0, "python3 hivegrid-remote attach --port 1 s")})
        try:
            A.spawn_terminal_pane(["hivegrid-remote", "attach", "--port", "1", "sid"], "/h", run=t)
        finally:
            A.foreground_by_tty = orig
        self.assertEqual([c[0] for c in t.non_list()][:2], ["zoom-pane", "split-pane"])


if __name__ == "__main__":
    unittest.main()
