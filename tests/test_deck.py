"""hivegrid_deck: the key logic of the Stream Deck panel (next waiting agent, double tap, what each action sends
to the mux) and the FIFO line to the bars (hivegrid_deck_link). No device, no real mux, no network."""
import os
import shutil
import sys
import tempfile
import unittest

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin")
sys.dont_write_bytecode = True
sys.path.insert(0, BIN)
import hivegrid_agents as A  # noqa: E402
import hivegrid_deck as D  # noqa: E402
import hivegrid_deck_link as L  # noqa: E402


def agent(pane, state="idle", tab=1, window=0, cwd="/w/x", num=1):
    return A.Agent(pane=pane, tab=tab, window=window, cwd=cwd, title="", state=state, num=num)


class FakeMux:
    def __init__(self):
        self.calls = []

    def run(self, *args):
        self.calls.append(args)
        return ""


class NextWaiting(unittest.TestCase):
    def test_takes_the_next_one_after_the_focus_and_wraps(self):
        ags = [agent(1, "attention"), agent(2, "working"), agent(3, "limit"), agent(4, "done")]
        self.assertEqual(D.next_waiting(ags, 1).pane, 3)
        self.assertEqual(D.next_waiting(ags, 3).pane, 1)
        self.assertEqual(D.next_waiting(ags, 2).pane, 3)

    def test_spans_tabs_and_windows_in_order(self):
        ags = [agent(9, "attention", tab=1, window=0), agent(4, "attention", tab=2, window=0),
               agent(2, "attention", tab=1, window=1)]
        self.assertEqual(D.next_waiting(ags, 9).pane, 4)
        self.assertEqual(D.next_waiting(ags, 4).pane, 2)

    def test_none_when_only_the_focused_one_waits_or_nobody(self):
        self.assertIsNone(D.next_waiting([agent(1, "attention")], 1))
        self.assertIsNone(D.next_waiting([agent(1, "working")], 1))

    def test_a_focus_that_is_no_agent_starts_from_the_top(self):
        ags = [agent(5, "attention"), agent(6, "attention")]
        self.assertEqual(D.next_waiting(ags, 99).pane, 5)


class DoubleTap(unittest.TestCase):
    def test_one_press_is_equalize_once_the_window_passed(self):
        t = D.DoubleTap(0.3)
        self.assertIsNone(t.press(10.0))
        self.assertIsNone(t.due(10.2))
        self.assertEqual(t.due(10.4), "equalize")
        self.assertIsNone(t.due(10.5))

    def test_two_presses_are_arrange_and_nothing_follows(self):
        t = D.DoubleTap(0.3)
        t.press(10.0)
        self.assertEqual(t.press(10.2), "arrange")
        self.assertIsNone(t.due(11.0))

    def test_a_late_second_press_is_a_new_first_press(self):
        t = D.DoubleTap(0.3)
        t.press(10.0)
        self.assertIsNone(t.press(10.5))
        self.assertEqual(t.due(11.0), "equalize")


class Actions(unittest.TestCase):
    def test_enter_and_zoom_target_the_focused_pane(self):
        m = FakeMux()
        self.assertTrue(D.act_enter(m, 7))
        self.assertTrue(D.act_zoom(m, 7))
        self.assertEqual(m.calls, [("send-text", "--pane-id", "7", "--no-paste", "\r"),
                                   ("zoom-pane", "--pane-id", "7", "--toggle")])

    def test_nothing_without_a_focused_pane(self):
        m = FakeMux()
        self.assertFalse(D.act_enter(m, None))
        self.assertFalse(D.act_zoom(m, None))
        self.assertFalse(D.act_new(m, [], None))
        self.assertEqual(m.calls, [])

    def test_new_from_a_running_agent_opens_a_window_in_its_folder(self):
        m = FakeMux()
        self.assertTrue(D.act_new(m, [agent(7, "working", cwd="/w/proj")], 7))
        args = m.calls[-1]
        self.assertEqual(args[:6], ("spawn", "--new-window", "--pane-id", "7", "--cwd", "/w/proj"))
        self.assertEqual(args[6], "--")
        self.assertIn("omp", args[-1])

    def test_new_from_a_shell_splits_the_tab_instead(self):
        m = FakeMux()
        m.run = lambda *a: m.calls.append(a) or ("" if a[0] != "list" else "[]")
        D.act_new(m, [agent(7, "exited")], 7)
        self.assertFalse(any(c[:2] == ("spawn", "--new-window") for c in m.calls))


class Raise(unittest.TestCase):
    def fake(self, code, out="", exc=None):
        def run(argv, **_):
            if exc:
                raise exc
            return type("R", (), {"returncode": code, "stdout": out})()
        return run

    def test_true_only_when_the_extension_raised_a_window(self):
        self.assertTrue(D.raise_window(self.fake(0, "(true,)\n")))
        self.assertFalse(D.raise_window(self.fake(0, "(false,)\n")), "no Hivegrid window")
        self.assertFalse(D.raise_window(self.fake(1)), "extension not loaded")
        self.assertFalse(D.raise_window(self.fake(0, exc=OSError())), "no gdbus")


class Link(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = L.fifo_path({"XDG_RUNTIME_DIR": self.tmp})

    def test_a_line_reaches_a_bar_and_only_valid_lines_do(self):
        fd = L.open_reader(self.path)
        self.addCleanup(os.close, fd)
        self.assertTrue(L.send("arrange", 12, self.path))
        self.assertFalse(L.send("rm -rf", 12, self.path))
        self.assertFalse(L.send("arrange", "1;2", self.path))
        os.write(os.open(self.path, os.O_WRONLY | os.O_NONBLOCK), b"zoom:3\n\xff\xfe\nequalize:4\n")
        self.assertEqual(L.parse(os.read(fd, 4096)), ["arrange:12", "equalize:4"])

    def test_without_a_bar_reading_the_send_fails(self):
        os.makedirs(os.path.dirname(self.path))
        os.mkfifo(self.path)
        self.assertFalse(L.send("equalize", 1, self.path))

    def test_a_file_in_the_fifos_place_is_never_used(self):
        os.makedirs(os.path.dirname(self.path))
        open(self.path, "w").close()
        self.assertIsNone(L.open_reader(self.path))
        self.assertFalse(L.send("equalize", 1, self.path))

    def test_no_runtime_dir_no_fifo(self):
        self.assertIsNone(L.fifo_path({}))
        self.assertIsNone(L.fifo_path({"XDG_RUNTIME_DIR": "rel"}))


class Device(unittest.TestCase):
    def test_key_report_parsing(self):
        self.assertEqual(D.parse_report(bytes([1, 0, 1, 0, 0, 0, 0] + [0] * 10)), [False, True, False, False, False, False])
        self.assertIsNone(D.parse_report(bytes([2, 1, 1, 1, 1, 1, 1])))
        self.assertIsNone(D.parse_report(b"\x01\x01"))

    def test_every_key_renders_80_square(self):
        v = D.View(waiting=2, num=3, state="attention")
        for i in range(D.KEYS):
            self.assertEqual(D.render(i, v).size, (80, 80))
            self.assertEqual(D.render(i, D.View(alive=False)).size, (80, 80))


if __name__ == "__main__":
    unittest.main()
