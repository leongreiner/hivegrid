"""Hover fades of the sidebar bar and the overview dock (no terminal needed: time is passed in)."""
import importlib.util
import os
import sys
import unittest
from importlib.machinery import SourceFileLoader

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin")
sys.dont_write_bytecode = True  # bin/ is copied wholesale by install.sh: no __pycache__ there
sys.path.insert(0, BIN)
import hivegrid_ui as U  # noqa: E402


def load(name):
    spec = importlib.util.spec_from_loader(name, SourceFileLoader(name, os.path.join(BIN, name)))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


B = load("hivegrid-bar")
D = load("hivegrid-overview")


def setUpModule():
    global _truecolor, NULL_FD
    _truecolor, U.TRUECOLOR = U.TRUECOLOR, True  # the tests look at 24-bit colour codes
    NULL_FD = os.open(os.devnull, os.O_RDONLY)


def tearDownModule():
    U.TRUECOLOR = _truecolor
    os.close(NULL_FD)


COLORS = {"surface": "#111b30", "raised": "#1d3a70", "dim": "#7487ad", "bright": "#eaf1ff", "accent": "#4f9dff"}
NOW = 1_700_000_000.0


class Out:
    """Collects what a program writes to the terminal."""

    def __init__(self):
        self.parts = []

    def write(self, text):
        self.parts.append(text)

    def flush(self):
        pass

    def fileno(self):
        return NULL_FD  # not a tty: cell_pixels falls back to its default size

    def take(self):
        text, self.parts = "".join(self.parts), []
        return text


def mouse(action, x, y, button="left"):
    return ("mouse", action, button, x, y)


class BarTest(unittest.TestCase):
    def setUp(self):
        self.bar = B.Bar(COLORS)
        self.bar.out = Out()

    def run_fade(self, seconds, step=1 / 30):
        """Advances the fade by `seconds` of fake time; returns how many frames were drawn."""
        frames, now = 0, 100.0
        self.bar.clock = now
        for _ in range(round(seconds / step)):
            now += step
            self.bar.advance(now)
            self.bar.draw(24, 5, (None, False), False)
            frames += bool(self.bar.out.take())
        return frames

    def test_fade_in_and_out_durations(self):
        self.bar.set_hover("git")
        self.assertEqual(self.bar.level["git"], 0.0)
        self.run_fade(B.FADE_IN * 0.5)
        self.assertAlmostEqual(self.bar.level["git"], 0.5, delta=0.1)
        self.run_fade(B.FADE_IN * 0.5 + 0.05)
        self.assertEqual(self.bar.level["git"], 1.0)
        self.assertIsNone(self.bar.clock)  # settled: nothing animates any more
        self.bar.set_hover(None)
        self.run_fade(B.FADE_OUT * 0.5)
        self.assertAlmostEqual(self.bar.level["git"], 0.5, delta=0.1)
        self.run_fade(B.FADE_OUT * 0.5 + 0.05)
        self.assertEqual(self.bar.level["git"], 0.0)
        self.assertIsNone(self.bar.clock)

    def test_idle_draws_nothing(self):
        self.bar.draw(24, 5, (None, False), True)
        self.bar.out.take()
        self.assertFalse(self.bar.advance(5.0))
        self.bar.draw(24, 5, (None, False), False)
        self.assertEqual(self.bar.out.take(), "")

    def test_only_the_changed_button_is_repainted(self):
        self.bar.draw(24, 5, (None, False), True)
        self.bar.out.take()
        self.bar.set_hover("git")
        self.bar.clock = 100.0
        self.bar.advance(100.1)
        self.bar.draw(24, 5, (None, False), False)
        out = self.bar.out.take()
        self.assertIn("\x1b[5;1H", out)  # git rows 5-7
        self.assertNotIn("\x1b[2;1H", out)  # files rows 2-4 untouched
        self.assertNotIn("\x1b[2J", out)

    def test_fade_shades_the_background_between_surface_and_raised(self):
        self.bar.set_hover("git")
        self.bar.level["git"] = 1.0
        self.bar.draw(24, 5, (None, False), True)
        r, g, b = (round(a + (c - a) * B.HOVER_BLEND) for a, c in zip((17, 27, 48), (29, 58, 112)))
        self.assertIn(f"\x1b[5;1H\x1b[0;48;2;{r};{g};{b};", self.bar.out.take())

    def test_open_view_keeps_its_highlight(self):
        self.bar.level["files"] = 1.0
        self.assertEqual(self.bar.shade("files", ("files", False)), (True, B.STEPS))
        self.assertEqual(self.bar.shade("files", (None, False)), (False, B.STEPS))
        self.assertEqual(self.bar.shade("overview", (None, True)), (True, B.STEPS))
        self.bar.hover = "files"
        self.bar.draw(24, 5, ("files", False), True)
        out = self.bar.out.take()
        self.assertIn("\x1b[2;1H\x1b[0;48;2;29;58;112;38;2;79;157;255m▎", out)  # raised + accent marker

    @unittest.skipUnless(B.find_font() and B.Image, "needs PIL and the icon font")
    def test_icon_cache_is_bounded(self):
        for name in B.BUTTONS:
            for step in range(B.STEPS + 1):
                self.bar.icon_png(name, step, 36, 54)
                self.bar.icon_png(name, step, 36, 54)
        self.assertEqual(len(self.bar.icons), len(B.BUTTONS) * (B.STEPS + 1))

    def test_pointer_and_leave_report(self):
        self.bar.handle(mouse("drag", 2, 5), 24)  # no-button motion over the git button (rows 4-6)
        self.assertEqual(self.bar.hover, "git")
        self.assertEqual(self.bar.out.take(), "\x1b]22;pointer\x07")
        self.bar.handle(mouse("drag", 2, 6), 24)
        self.assertEqual(self.bar.out.take(), "")  # still a button: the hand stays, nothing sent
        self.bar.handle(mouse("drag", 2, 12), 24)  # between buttons
        self.assertIsNone(self.bar.hover)
        self.assertEqual(self.bar.out.take(), "\x1b]22;\x07")
        self.bar.handle(mouse("drag", 2, 5), 24)
        self.bar.out.take()
        self.bar.handle(mouse("drag", -1, -1), 24)  # CSI < 35 ; 0 ; 0 M: the pointer left the bar
        self.assertIsNone(self.bar.hover)
        self.assertEqual(self.bar.out.take(), "\x1b]22;\x07")

    def test_click_still_announces_the_button(self):
        self.bar.handle(mouse("press", 2, 8), 24)
        self.assertIn("SetUserVar=sidebar_click=", self.bar.out.take())


class DockTest(unittest.TestCase):
    DOC = {"panes": [
        {"id": 5, "num": 1, "active": True, "kind": "idle", "glyph": "·", "color": "#8a9cc2", "name": "alpha",
         "proc": "omp", "since": NOW - 7200, "text": "idle", "title": "", "zoomed": False},
        {"id": 6, "num": 2, "active": False, "kind": "idle", "glyph": "·", "color": "#8a9cc2", "name": "beta",
         "proc": "omp", "since": NOW - 7200, "text": "idle", "title": "", "zoomed": False},
    ]}

    def render(self, hover=None):
        return D.render(self.DOC, 36, 16, NOW, lambda i: 0, lambda: 0, lambda: 0, hover)

    def test_no_hover_renders_as_before(self):
        self.assertEqual(self.render().render(), self.render({}).render())
        self.assertEqual(D.dump(self.render()), D.dump(self.render({6: 1.0, "arrange": 1.0})))

    def test_hovered_row_and_button_are_tinted(self):
        rest, hot = self.render().render(), self.render({6: 1.0, "arrange": 1.0}).render()
        self.assertNotEqual(rest, hot)
        # The inactive row tints from surface towards raised; the active row is raised already.
        cv = self.render({6: 1.0})
        row = cv.cells[5]
        self.assertEqual(row[0][2], D.mix("#111b30", "#1d3a70", D.HOVER_BLEND))
        self.assertEqual(cv.cells[2][0][2], "#1d3a70")
        self.assertEqual(self.render({"arrange": 1.0}).cells[5][0][2], "#111b30")

    def test_regions_have_keys(self):
        cv = self.render()
        self.assertEqual(cv.key_at(10, 5), 6)
        self.assertEqual(cv.key_at(10, 2), 5)
        self.assertEqual(cv.key_at(29, 0), "arrange")
        self.assertEqual(cv.key_at(33, 0), "equalize")
        self.assertIsNone(cv.key_at(10, 12))

    def test_motion_leave_and_fade(self):
        dock = D.Dock("/nonexistent")
        dock.out = Out()
        dock.cv = self.render()
        dock.handle(mouse("drag", 10, 5))
        self.assertEqual(dock.hover, 6)
        dock.clock = 100.0
        self.assertTrue(dock.advance(100.075))
        self.assertAlmostEqual(dock.levels[6], 0.5)
        self.assertFalse(dock.advance(100.2))  # reached the target: the fade is over
        self.assertEqual(dock.levels[6], 1.0)
        self.assertIsNone(dock.clock)
        dock.handle(mouse("drag", -1, -1))  # the pointer left the dock
        self.assertIsNone(dock.hover)
        dock.clock = 200.0
        self.assertTrue(dock.advance(200.1))
        self.assertAlmostEqual(dock.levels[6], 0.5)
        self.assertFalse(dock.advance(200.3))
        self.assertEqual(dock.levels, {})

    def test_clicks_unchanged(self):
        dock = D.Dock("/nonexistent")
        dock.out = Out()
        dock.cv = D.render(self.DOC, 36, 16, NOW, dock.goto, dock.equalize, dock.arrange)
        dock.handle(mouse("press", 10, 5))
        self.assertIn("SetUserVar=hivegrid_overview_goto=", dock.out.take())
        dock.handle(mouse("press", 29, 0))
        self.assertIn("SetUserVar=hivegrid_overview_arrange=", dock.out.take())


if __name__ == "__main__":
    unittest.main()
