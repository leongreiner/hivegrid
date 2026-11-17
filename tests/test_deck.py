"""hivegrid_deck: the key logic of the Stream Deck panel (next waiting agent, double tap, what each action sends
to the mux) and the FIFO line to the bars (hivegrid_deck_link). No device, no real mux, no network."""
import json
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

    def test_a_finished_agent_waits_too_but_urgent_ones_come_first(self):
        ags = [agent(1, "done"), agent(2, "working"), agent(3, "attention"), agent(4, "done")]
        self.assertEqual(D.next_waiting(ags, 2).pane, 3)
        self.assertEqual(D.next_waiting([agent(1, "done"), agent(2, "idle"), agent(4, "done")], 1).pane, 4)
        v, _, _ = D.read_view(FakeMux(), agents=ags, focus=2)
        self.assertEqual((v.waiting, v.urgent), (3, 1))

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
        self.assertFalse(D.act_new(m, [], None, accounts=[]))
        self.assertEqual(m.calls, [])

    def test_new_splits_the_focused_tab_in_the_focused_folder_and_never_opens_a_window(self):
        calls = []

        def run(*args):
            calls.append(args)
            if args[0] == "list":
                return json.dumps([{"pane_id": 7, "tab_id": 1, "window_id": 0, "tty_name": None, "cwd": "file:///w/proj",
                                    "size": {"cols": 100, "rows": 40}, "left_col": 0, "top_row": 0, "is_active": True}])
            return "8\n" if args[0] == "split-pane" else ""

        m = FakeMux()
        m.run = run
        self.assertTrue(D.act_new(m, [agent(7, "working", cwd="/w/proj")], 7, accounts=[]))
        split = next(c for c in calls if c[0] == "split-pane")
        self.assertEqual(split[split.index("--cwd") + 1], "/w/proj")
        self.assertFalse(any(c[0] == "spawn" for c in calls))


class NewOnBestAccount(unittest.TestCase):
    def test_the_new_agent_starts_with_the_profile_that_has_the_most_room(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        now = 1000000.0
        for label, five, seven in (("a", 80, 20), ("b", 10, 30)):
            with open(os.path.join(tmp, label + ".json"), "w") as f:
                json.dump({"state": "ok", "five_hour": {"pct": five, "reset_at": None},
                           "seven_day": {"pct": seven, "reset_at": None}, "updated_at": now}, f)
        old = os.environ.get("CLAUDE_QUOTA_CACHE_DIR")
        os.environ["CLAUDE_QUOTA_CACHE_DIR"] = tmp
        self.addCleanup(lambda: os.environ.pop("CLAUDE_QUOTA_CACHE_DIR") if old is None else os.environ.__setitem__("CLAUDE_QUOTA_CACHE_DIR", old))
        calls = []

        def run(*args):
            calls.append(args)
            if args[0] == "list":
                return json.dumps([{"pane_id": 7, "tab_id": 1, "window_id": 0, "tty_name": None, "cwd": "file:///w",
                                    "size": {"cols": 100, "rows": 40}, "left_col": 0, "top_row": 0, "is_active": True}])
            return "8\n" if args[0] == "split-pane" else ""

        m = FakeMux()
        m.run = run
        accounts = [{"label": x, "profile": x, "hidden": False} for x in ("a", "b")]
        self.assertTrue(D.act_new(m, [agent(7, "idle", cwd="/w")], 7, accounts=accounts, now=now))
        split = next(c for c in calls if c[0] == "split-pane")
        self.assertIn("omp --profile=b; exec bash", split[-1])


class NewInShell(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def run(*args):
            self.calls.append(args)
            if args[0] == "list":
                return json.dumps([{"pane_id": 7, "tab_id": 1, "window_id": 0, "tty_name": "/dev/pts/4", "cwd": "file:///w",
                                    "size": {"cols": 100, "rows": 40}, "left_col": 0, "top_row": 0, "is_active": True}])
            return "8\n" if args[0] == "split-pane" else ""

        self.mux = FakeMux()
        self.mux.run = run
        self.accounts = [{"label": "b", "profile": "b", "hidden": False}]

    def new(self, comm, args=""):
        return D.act_new(self.mux, [], 7, accounts=self.accounts, now=0.0, fg={"pts/4": (1, comm, 5, args or comm)})

    def test_a_plain_shell_gets_omp_typed_in_place_and_no_pane_is_added(self):
        self.assertTrue(self.new("bash"))
        self.assertEqual(self.calls[-1], ("send-text", "--pane-id", "7", "--no-paste", "omp\r"))
        self.assertFalse(any(c[0] in ("split-pane", "spawn") for c in self.calls))

    def test_a_pane_that_runs_something_else_gets_a_split(self):
        for comm, args in (("omp", "omp"), ("yazi", "yazi"), ("python3", "python3 /x/hivegrid-bar --surface #101010")):
            self.calls.clear()
            self.new(comm, args)
            self.assertTrue(any(c[0] == "split-pane" for c in self.calls), comm)
            self.assertFalse(any(c[0] == "send-text" for c in self.calls), comm)


class BestProfile(unittest.TestCase):
    NOW = 1000000.0

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def quota(self, label, five=None, seven=None, state="ok", age=30.0, reset=None):
        def w(pct):
            return None if pct is None else {"pct": pct, "reset_at": reset}
        with open(os.path.join(self.dir, label + ".json"), "w") as f:
            json.dump({"label": label, "state": state, "five_hour": w(five), "seven_day": w(seven), "updated_at": self.NOW - age}, f)

    def accounts(self, *labels):
        return [{"label": x, "profile": x, "dir": "~/." + x, "hidden": False} for x in labels]

    def best(self, *labels):
        return D.best_profile(self.accounts(*labels), self.NOW, self.dir)

    def test_the_tighter_window_decides(self):
        self.quota("a", five=5, seven=90)    # 10 % left in the week
        self.quota("b", five=60, seven=30)   # 40 % left in the 5 hours
        self.assertEqual(self.best("a", "b"), ("b", "b"))

    def test_an_account_with_only_a_weekly_window_counts(self):
        self.quota("a", five=None, seven=13)
        self.quota("b", five=48, seven=19)
        self.assertEqual(self.best("a", "b"), ("a", "a"))

    def test_login_stale_and_empty_readings_are_skipped(self):
        self.quota("a", five=0, seven=0, state="login")
        self.quota("b", five=0, seven=0, age=D.QUOTA_FRESH + 1)
        self.quota("c")
        self.quota("d", five=90, seven=90)
        self.assertEqual(self.best("a", "b", "c", "e", "d"), ("d", "d"))

    def test_a_window_past_its_reset_counts_as_empty(self):
        self.quota("a", five=99, seven=10, reset=self.NOW - 5)   # the 5 h window has reset
        self.quota("b", five=40, seven=40)
        self.assertEqual(self.best("a", "b"), ("a", "a"))

    def test_none_when_no_account_can_say_and_accounts_without_a_profile_never_win(self):
        self.assertIsNone(self.best("a"))
        self.quota("a", five=1, seven=1)
        self.assertIsNone(D.best_profile([{"label": "a", "profile": None, "hidden": False}], self.NOW, self.dir))

    def test_a_tie_goes_to_the_roomier_other_window(self):
        self.quota("a", five=50, seven=50)
        self.quota("b", five=50, seven=10)
        self.assertEqual(self.best("a", "b"), ("b", "b"))


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
