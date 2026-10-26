"""Hover fades of the sidebar bar and the overview dock (no terminal needed: time is passed in)."""
import importlib.util
import io
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
        self.bar.theme = None  # the tests never read the user's settings.json

    def pixel(self, png, x, y):
        img = B.Image.open(io.BytesIO(png)).convert("RGBA")
        return img.getpixel((x, y))

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

    def test_text_fallback_shades_the_cells_between_surface_and_raised(self):
        self.bar.font_path = None
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
        self.bar.font_path = None
        self.bar.hover = "files"
        self.bar.draw(24, 5, ("files", False), True)
        out = self.bar.out.take()
        self.assertIn("\x1b[2;1H\x1b[0;48;2;29;58;112;38;2;79;157;255m▎", out)  # raised + accent marker

    @unittest.skipUnless(B.find_font() and B.Image, "needs PIL and the icon font")
    def test_button_image_chip_and_stripe(self):
        # 8 x 17 px cells, 5 columns: the chip spans x 4..39 (half a cell from the window edge,
        # the divider sits half a cell right of the bar); (5, 25) is inside it, left of the icon.
        self.bar.font_path = B.find_font()
        rest = self.bar.button_png("git", False, 0, 8, 17, 5)
        self.assertEqual(self.pixel(rest, 5, 25)[3], 0)  # no chip at rest: the surface shows
        hover = self.bar.button_png("git", False, B.STEPS, 8, 17, 5)
        r, g, b = (round(a + (c - a) * B.HOVER_BLEND) for a, c in zip((17, 27, 48), (29, 58, 112)))
        self.assertNear(self.pixel(hover, 5, 25), (r, g, b, 255))
        self.assertEqual(self.pixel(hover, 0, 25)[3], 0)  # the stripe marks only an open view
        hot = self.bar.button_png("files", True, B.STEPS, 8, 17, 5)
        self.assertNear(self.pixel(hot, 5, 25), (29, 58, 112, 255))  # raised
        self.assertNear(self.pixel(hot, 0, 25), (79, 157, 255, 255))  # accent stripe

    def assertNear(self, got, want):
        """Colours equal up to the rounding of the supersampled shapes' downscale."""
        self.assertTrue(all(abs(a - b) <= 2 for a, b in zip(got, want)), f"{got} != {want}")

    @unittest.skipUnless(B.find_font() and B.Image, "needs PIL and the icon font")
    def test_icon_cache_is_bounded(self):
        self.bar.font_path = B.find_font()
        for name in B.BUTTONS:
            for step in range(B.STEPS + 1):
                self.bar.button_png(name, False, step, 8, 17, 5)
                self.bar.button_png(name, False, step, 8, 17, 5)
        self.assertEqual(len(self.bar.icons), len(B.BUTTONS) * (B.STEPS + 1))

    def test_saved_theme_restyles_the_bar(self):
        palette = dict(COLORS, surface="#202020", accent="#ff0000", text="#ffffff")
        self.bar.theme = type("Watch", (), {"poll": lambda self: palette})()
        self.bar.icons["stale"] = b""
        self.assertTrue(self.bar.restyle(10.0))
        self.assertEqual(self.bar.c["surface"], "#202020")
        self.assertNotIn("text", self.bar.c)  # only the bar's own colours are taken
        self.assertEqual(self.bar.icons, {})
        self.assertIn("\x1b]11;#202020\x07", self.bar.out.take())  # the padding follows the surface
        self.assertFalse(self.bar.restyle(10.5))  # at most one look a second

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

    def test_hand_pointer_over_rows_and_buttons(self):
        dock = D.Dock("/nonexistent")
        dock.out = Out()
        dock.cv = self.render()
        dock.handle(mouse("drag", 10, 5))
        self.assertEqual(dock.out.take(), "\x1b]22;pointer\x07")
        dock.handle(mouse("drag", 29, 0))  # row to button: still a hand, nothing to send
        self.assertEqual(dock.out.take(), "")
        dock.handle(mouse("drag", 10, 12))  # empty space under the rows
        self.assertEqual(dock.out.take(), "\x1b]22;\x07")


class DockNumbersTest(unittest.TestCase):
    """Past pane 9 the numbers take two columns and a typed digit may start a longer number."""

    DOC = {"panes": [{"id": 100 + n, "num": n, "kind": "shell", "glyph": "›", "color": "#7487ad", "name": f"p{n}",
                      "proc": "bash", "since": NOW - 5, "text": "shell", "title": "u@h: ~/work/deep/dir"}
                     for n in range(1, 13)]}

    def dock(self):
        dock = D.Dock("/nonexistent")
        dock.out, dock.doc = Out(), self.DOC
        return dock

    def gotos(self, dock):
        return dock.out.take().count("SetUserVar=hivegrid_overview_goto=")

    def test_names_line_up(self):
        rows = [line for line in D.dump(D.render(self.DOC, 40, 40, NOW)) if " p" in line]
        self.assertEqual({line.index(" p") for line in rows}, {6})

    def test_shell_title_shows_the_directory(self):
        lines = D.dump(D.render(self.DOC, 40, 40, NOW))
        self.assertIn("~/work/deep/dir", lines[3])
        self.assertTrue(D.dump(D.render(self.DOC, 16, 40, NOW))[3].strip().startswith("…"))

    def test_unambiguous_digit_jumps_at_once(self):
        dock = self.dock()
        dock.number("5")
        self.assertEqual(self.gotos(dock), 1)
        self.assertEqual(dock.digits, "")

    def test_two_digit_number(self):
        dock = self.dock()
        dock.number("1")  # 1, 10, 11 or 12: wait
        self.assertEqual((self.gotos(dock), dock.digits), (0, "1"))
        dock.number("2")
        self.assertEqual((self.gotos(dock), dock.digits), (1, ""))

    def test_lone_digit_jumps_after_the_wait(self):
        dock = self.dock()
        dock.number("1")
        self.assertIsNotNone(dock.settle(dock.digits_at + D.DIGIT_WAIT / 2))
        self.assertIsNone(dock.settle(dock.digits_at + D.DIGIT_WAIT))
        self.assertEqual(self.gotos(dock), 1)

    def test_footer_shortens_with_the_width(self):
        self.assertEqual(D.hints(12, 60), "1-12 jump  e equalize  a arrange  q close")
        self.assertEqual(D.hints(12, 30), "1-12 jump  e ▦  a ⊞  q close")
        self.assertEqual(D.hints(1, 20), "1 jump  q close")
        self.assertEqual(D.hints(12, 30, "1"), "go to 1…")


if __name__ == "__main__":
    unittest.main()
