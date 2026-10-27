"""Hover fades of the sidebar bar and the overview dock (no terminal needed: time is passed in)."""
import base64
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


COLORS = {"surface": "#111722", "raised": "#222f45", "dim": "#7a879d", "bright": "#f5f8fc", "accent": "#78a9ff"}
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
        r, g, b = (round(a + (c - a) * B.HOVER_BLEND) for a, c in zip((17, 23, 34), (34, 47, 69)))
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
        self.assertIn("\x1b[2;1H\x1b[0;48;2;34;47;69;38;2;120;169;255m▎", out)  # raised + accent marker

    @unittest.skipUnless(B.find_font() and B.Image, "needs PIL and the icon font")
    def test_button_image_chip_and_stripe(self):
        # 8 x 17 px cells, 5 columns: the chip spans x 4..39 (half a cell from the window edge,
        # the divider sits half a cell right of the bar); (5, 25) is inside it, left of the icon.
        self.bar.font_path = B.find_font()
        rest = self.bar.button_png("git", False, 0, 8, 17, 5)
        self.assertEqual(self.pixel(rest, 5, 25)[3], 0)  # no chip at rest: the surface shows
        hover = self.bar.button_png("git", False, B.STEPS, 8, 17, 5)
        r, g, b = (round(a + (c - a) * B.HOVER_BLEND) for a, c in zip((17, 23, 34), (34, 47, 69)))
        self.assertNear(self.pixel(hover, 5, 25), (r, g, b, 255))
        self.assertEqual(self.pixel(hover, 0, 25)[3], 0)  # the stripe marks only an open view
        hot = self.bar.button_png("files", True, B.STEPS, 8, 17, 5)
        self.assertNear(self.pixel(hot, 5, 25), (34, 47, 69, 255))  # raised
        self.assertNear(self.pixel(hot, 0, 25), (120, 169, 255, 255))  # accent stripe

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

    def test_first_geometry_paints_at_once(self):
        self.assertEqual(self.bar.wait_for((58, 5, 8, 17), 10.0), 0.0)
        self.assertTrue(self.bar.dirty)

    def test_a_new_size_is_painted_in_full_once_it_held(self):
        """The bar widened to split the view off it, then narrowed: one full repaint, at 5 cols."""
        self.bar.wait_for((58, 5, 8, 17), 10.0)
        self.bar.dirty = False
        self.assertEqual(self.bar.wait_for((58, 5, 8, 17), 10.5), 0.0)
        self.assertFalse(self.bar.dirty, "an unchanged size is no reason to repaint")
        self.assertGreater(self.bar.wait_for((58, 52, 8, 17), 11.0), 0.0)
        self.assertGreater(self.bar.wait_for((58, 5, 8, 16), 11.03), 0.0, "another change restarts the wait")
        self.assertFalse(self.bar.dirty)
        self.assertEqual(self.bar.wait_for((58, 5, 8, 16), 11.031 + B.SETTLE), 0.0)
        self.assertTrue(self.bar.dirty, "a cell pixel change alone repaints everything")
        self.assertEqual(self.bar.geom, (58, 5, 8, 16))

    def test_a_resize_back_to_the_painted_size_still_repaints(self):
        """WezTerm rewraps the screen on each resize: a 5 -> 58 -> 5 round trip loses the images."""
        self.bar.wait_for((58, 5, 8, 17), 10.0)
        self.bar.dirty = False
        self.assertGreater(self.bar.wait_for((58, 5, 8, 17), 11.0, resized=True), 0.0)
        self.assertEqual(self.bar.wait_for((58, 5, 8, 17), 11.001 + B.SETTLE), 0.0)
        self.assertTrue(self.bar.dirty)

    def test_state_files_of_an_earlier_wezterm_are_dropped_at_start(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "wezterm-sidebar-1")
            for p in (path, path + ".ov"):
                with open(p, "w") as f:
                    f.write("open git\n")
            os.utime(path + ".ov", (NOW - 3600, NOW - 3600))
            os.utime(path, (NOW - 1, NOW - 1))
            B.drop_stale(path, NOW)
            self.assertTrue(os.path.exists(path), "written for this bar: kept")
            self.assertFalse(os.path.exists(path + ".ov"), "an hour old: dropped")

    def test_a_full_paint_draws_every_button(self):
        self.bar.draw(24, 5, (None, False), True)
        self.bar.dirty = False
        self.bar.draw(30, 5, (None, False), True)
        out = self.bar.out.take()
        self.assertIn("\x1b[2J", out)
        for name in B.BUTTONS:
            self.assertEqual(self.bar.drawn[name][:2], (False, 0), name)
        self.assertIn("\x1b[27;", out, "the settings button moved with the new height (row 30 - 4 + 1)")

    def test_state_file_carries_the_gear_nudge(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "wezterm-sidebar-1")
            with open(path, "w") as f:
                f.write("open git -6\n")
            self.assertEqual(B.read_state(path), ("git", False, -6))
            with open(path, "w") as f:
                f.write("closed files\n")
            self.assertEqual(B.read_state(path), (None, False, 0), "older config: no nudge")

    @unittest.skipUnless(B.Image and B.find_font(), "needs PIL and the icon font")
    def test_the_gear_moves_by_the_nudge_inside_its_five_rows(self):
        """The chip of a hovered gear starts `inset` px into its 3 rows; on the 5-row image that
        is one row lower, plus the nudge."""
        self.bar.font_path = B.find_font()
        cw, ch = 8, 17
        inset = max(ch // 8, 2)
        for nudge in (-6, 0, 9):
            img = B.Image.open(io.BytesIO(self.bar.gear_png(False, B.STEPS, cw, ch, 5, nudge))).convert("RGBA")
            self.assertEqual(img.size, (5 * cw, 5 * ch))
            column = [img.getpixel((cw * 3, y))[3] for y in range(5 * ch)]
            self.assertEqual(column.index(next(a for a in column if a > 128)), ch + nudge + inset, nudge)
        out = self.bar.button("settings", 26, 5, cw, ch, False, 0, 9)
        self.assertIn("\x1b[26;1H\x1b]1337;File=inline=1;doNotMoveCursor=1;", out, "drawn from the row above")
        self.assertIn("height=5", out)


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
        self.assertEqual(row[0][2], D.mix("#111722", "#222f45", D.HOVER_BLEND))
        self.assertEqual(cv.cells[2][0][2], "#222f45")
        self.assertEqual(self.render({"arrange": 1.0}).cells[5][0][2], "#111722")

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
        self.assertEqual(dock.fade.key, 6)
        dock.fade.clock = 100.0
        self.assertTrue(dock.advance(100.075))
        self.assertAlmostEqual(dock.fade.fades[6], 0.5)
        self.assertFalse(dock.advance(100.2))  # reached the target: the fade is over
        self.assertEqual(dock.fade.fades[6], 1.0)
        self.assertIsNone(dock.fade.clock)
        dock.handle(mouse("drag", -1, -1))  # the pointer left the dock
        self.assertIsNone(dock.fade.key)
        dock.fade.clock = 200.0
        self.assertTrue(dock.advance(200.1))
        self.assertAlmostEqual(dock.fade.fades[6], 0.5)
        self.assertFalse(dock.advance(200.3))
        self.assertEqual(dock.fade.fades, {})

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

    DOC = {"panes": [{"id": 100 + n, "num": n, "kind": "shell", "glyph": "›", "color": "#7a879d", "name": f"p{n}",
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
        self.assertTrue(D.dump(D.render(self.DOC, 16, 40, NOW))[3].lstrip("▎ ").startswith("…"))

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

    def test_footer_drops_hints_from_the_end(self):
        def footer(n, w, digits=""):
            return D.dump(D.render({"panes": self.DOC["panes"][:n]}, w, 40, NOW, digits=digits))[-1].strip()
        self.assertEqual(footer(12, 80), "1-12 jump  ↵ go  q close  e equalize  a arrange  x close pane")
        self.assertEqual(footer(12, 60), "1-12 jump  ↵ go  q close  e equalize  a arrange")
        self.assertEqual(footer(12, 30), "1-12 jump  ↵ go  q close")
        self.assertEqual(footer(1, 20), "1 jump  ↵ go")
        self.assertEqual(footer(12, 30, "1"), "go to 1…")

    def test_keyboard_selection(self):
        dock = self.dock()
        dock.draw = lambda size: setattr(dock, "cv", D.render(dock.doc, 40, 20, NOW, sel=dock.sel))
        dock.draw(None)
        self.assertEqual(dock.cv.sel, 101)  # no active pane: the first row
        for key, want in (("j", 102), ("DownArrow", 103), ("k", 102), ("G", 112), ("j", 112), ("g", 101)):
            dock.handle(("key", set(), key))
            dock.draw(None)
            self.assertEqual(dock.cv.sel, want, key)
        dock.handle(("key", {"SHIFT"}, "g"))  # G as the terminal sends it
        dock.draw(None)
        self.assertEqual(dock.cv.sel, 112)
        dock.handle(("key", set(), "Enter"))
        self.assertEqual(self.gotos(dock), 1)

    def test_selection_scrolls_into_view_and_follows_focus(self):
        doc = {"panes": [dict(p, active=p["num"] == 2) for p in self.DOC["panes"]]}
        cv = D.render(doc, 40, 12, NOW, sel=112)
        self.assertTrue(any("p12" in line for line in D.dump(cv)))
        self.assertEqual(D.render(doc, 40, 12, NOW).sel, 102)  # sel None: the active pane
        dock = self.dock()
        dock.doc, dock.sel, dock.active = doc, 105, 101
        dock.draw((40, 12))  # the focus moved (101 -> 102): the mark follows it again
        self.assertIsNone(dock.sel)
        self.assertEqual(dock.cv.sel, 102)

    def test_dense_rows_are_striped(self):
        cv = D.render(self.DOC, 40, 20, NOW)  # 12 panes in 17 rows: one line each
        bgs = [cv.cells[y][20][2] for y in range(3, 6)]
        self.assertNotEqual(bgs[0], bgs[1])
        self.assertEqual(bgs[0], bgs[2])
class RowTitleTest(unittest.TestCase):
    """A row is named by the agent's own title; the directory is not repeated when every pane shares it."""

    def doc(self, cwds):
        return {"panes": [{"id": 10 + i, "num": i + 1, "kind": "working" if i == 0 else "idle", "name": "hivegrid",
                           "cwd": cwd, "proc": "omp", "since": NOW - 5, "text": "", "title": f"Task {i + 1}",
                           "reason": "edit x" if i == 0 else ""} for i, cwd in enumerate(cwds)]}

    def test_title_replaces_the_directory_name_and_a_shared_directory_is_left_out(self):
        lines = D.dump(D.render(self.doc(["~/p/hivegrid"] * 2), 40, 12, NOW))
        self.assertIn("1 Task 1", lines[2])
        self.assertEqual(lines[3].strip("▎ "), "edit x")  # working: what it is doing
        self.assertIn("2 Task 2", lines[5])
        self.assertEqual(lines[6].strip(), "")  # idle: nothing to add
        self.assertFalse([line for line in lines if "hivegrid" in line and "⬡" not in line])

    def test_directory_goes_to_the_second_line_when_panes_differ(self):
        lines = D.dump(D.render(self.doc(["~/p/hivegrid", "~/p/other"]), 40, 12, NOW))
        self.assertIn("2 Task 2", lines[5])
        self.assertEqual(lines[6].strip(), "~/p/other")

    def test_title_row_buttons_never_cover_the_title(self):
        doc = self.doc(["~/p/hivegrid"])
        for w in range(10, 40):
            top = D.dump(D.render(doc, w, 12, NOW, lambda i: 0, lambda: 0, lambda: 0, None, close_tab=lambda: 0))[0]
            if "Overview" not in top:  # too narrow for the whole title: the buttons are gone, not drawn over it
                self.assertFalse(set(top) & set("✕⊞▦"), (w, top))
            self.assertTrue(top.startswith(" ⬡ "), (w, top))


        roomy = D.render({"panes": self.DOC["panes"][:3]}, 40, 20, NOW)  # blank lines between: no stripes
        self.assertEqual(roomy.cells[5][20][2], roomy.cells[8][20][2])


class DockCloseTest(unittest.TestCase):
    """Closing panes and the tab from the dock: which closes ask first, the ✕ buttons, x / Delete /
    X, the answer, and the requests to the config."""

    KINDS = ("shell", "working", "attention", "done", "exited", "other")
    DOC = {"panes": [{"id": 10 + i, "num": i + 1, "active": i == 0, "kind": k, "name": f"a{i + 1}",
                      "proc": {"shell": "bash", "exited": "bash", "other": "htop"}.get(k, "omp"),
                      "since": NOW - 60, "text": "htop" if k == "other" else "", "title": f"u@h: ~/w/a{i + 1}"}
                     for i, k in enumerate(KINDS)]}
    SIZE = (43, 24)

    def dock(self, doc=None):
        dock = D.Dock("/nonexistent")
        dock.out, dock.doc = Out(), doc or self.DOC
        dock.draw(self.SIZE)
        dock.out.take()
        return dock

    @staticmethod
    def closes(dock):
        """The close requests the dock sent, without their serials."""
        out = []
        for part in dock.out.take().split("SetUserVar=hivegrid_overview_close=")[1:]:
            value = base64.b64decode(part.split("\x07")[0]).decode()
            out.append(value.rsplit(":", 1)[0])
        return out

    @staticmethod
    def where(cv, key):
        for y in range(cv.h):
            for x in range(cv.w):
                if cv.key_at(x, y) == key:
                    return x, y
        return None

    def key(self, dock, key, mods=()):
        dock.handle(("key", set(mods), key))
        dock.draw(self.SIZE)

    def test_close_rule(self):
        safe = [k for k in ("shell", "exited", "done", "idle", "web", "working", "busy", "attention", "bell", "other", None)
                if not D.close_need({"kind": k})]
        self.assertEqual(safe, ["shell", "exited", "done", "idle", "web"])

    def test_questions(self):
        def texts(pane_id=None, panes=self.DOC["panes"]):
            return [D.question_text(q) for q in D.close_question(panes, pane_id)]
        self.assertEqual(texts(11), ["close 2 · working?", "close 2?"])
        self.assertEqual(texts(15)[0], "close 6 · htop?")
        self.assertEqual(texts()[:3], ["close tab · 6 panes · 1 needs you, 1 working, 1 running?",
                                       "close 6 panes · 1 needs you, 1 working, 1 running?",
                                       "close 6 panes? ?1 ↻1 ›1"])
        self.assertEqual(texts(None, self.DOC["panes"][:1]), ["close tab · 1 pane?", "close tab?"])

    def test_x_closes_a_safe_row_at_once_and_moves_on(self):
        dock = self.dock()
        self.key(dock, "x")  # the selected row: the active pane, a shell
        self.assertEqual(self.closes(dock), ["pane:10:dock"])
        self.assertIsNone(dock.ask)
        self.assertNotIn("a1", "".join(D.dump(dock.cv)), "its row goes at once")
        self.assertEqual(dock.cv.sel, 11, "the selection moves to the next row")
        self.key(dock, "Delete")  # pane 2 works: asks
        self.assertEqual(self.closes(dock), [])
        self.assertEqual(dock.ask, ("pane", 11))

    def test_a_closing_pane_asks_and_defaults_to_no(self):
        dock = self.dock()
        self.key(dock, "j")
        self.key(dock, "x")
        self.assertEqual(dock.ask, ("pane", 11))
        lines = D.dump(dock.cv)
        self.assertIn("close 2 · working?", lines[6])
        self.assertIn("yes   no", lines[6])
        self.assertEqual(lines[-1].strip(), "y close  n keep")
        for key in ("n", "Escape", "Enter", "q", "x"):
            self.key(dock, key)
            self.assertIsNone(dock.ask, key)
            self.assertTrue(dock.running, "Esc and q answer the question, they do not close the dock")
            self.key(dock, "x")
        self.assertEqual(self.closes(dock), [])
        self.key(dock, "y")
        self.assertEqual(self.closes(dock), ["pane:11:dock"])

    def test_row_x_follows_the_pointer_one_at_a_time(self):
        closed = []
        cv = D.render(self.DOC, 43, 24, NOW, lambda i: None, close=closed.append, pointer=13)
        lines = D.dump(cv)
        self.assertEqual([y for y, line in enumerate(lines) if line.endswith("✕")], [11], "row 4 only")
        self.assertEqual(self.where(cv, ("x", 13)), (40, 11))
        cv.hit_at(41, 11)("left")
        self.assertEqual(closed, [13])
        cv = D.render(self.DOC, 43, 24, NOW, lambda i: None, close=closed.append)
        self.assertTrue(D.dump(cv)[2].endswith("✕"), "without a pointer: on the selected row")

    def test_click_on_x_closes_and_hands_the_keyboard_to_the_grid(self):
        dock = self.dock()
        dock.handle(mouse("drag", 10, 11))  # over row 4 (done)
        dock.draw(self.SIZE)
        x, y = self.where(dock.cv, ("x", 13))
        dock.handle(mouse("press", x, y))
        self.assertEqual(self.closes(dock), ["pane:13:grid"])
        self.assertNotIn("goto", dock.out.take())

    def test_question_buttons_and_clicks_elsewhere(self):
        dock = self.dock()
        dock.handle(mouse("drag", 10, 8))  # row 3 needs you
        dock.draw(self.SIZE)
        dock.handle(mouse("press", *self.where(dock.cv, ("x", 12))))
        dock.draw(self.SIZE)
        self.assertEqual(dock.ask, ("pane", 12))
        dock.handle(mouse("press", *self.where(dock.cv, "ask_no")))
        self.assertIsNone(dock.ask)
        dock.ask = ("pane", 12)
        dock.draw(self.SIZE)
        dock.handle(mouse("press", 10, 2))  # another row: keeps the pane, jumps nowhere
        self.assertIsNone(dock.ask)
        self.assertNotIn("SetUserVar", dock.out.take())
        dock.ask = ("pane", 12)
        dock.draw(self.SIZE)
        dock.handle(mouse("press", *self.where(dock.cv, "ask_yes")))
        self.assertEqual(self.closes(dock), ["pane:12:grid"])

    def test_tab_close_always_asks_with_the_count(self):
        dock = self.dock({"panes": self.DOC["panes"][:2]})
        dock.handle(mouse("press", *self.where(dock.cv, "close_tab")))
        dock.draw(self.SIZE)
        self.assertEqual(dock.ask, ("tab", None))
        self.assertEqual(D.dump(dock.cv)[0].strip(), "close 2 panes · 1 working?      yes   no")
        self.key(dock, "y")
        self.assertEqual(self.closes(dock), ["tab"])
        dock = self.dock({"panes": self.DOC["panes"][:1]})  # nothing at work: still asks
        self.key(dock, "X")
        self.assertEqual(dock.ask, ("tab", None))
        self.key(dock, "x", {"SHIFT"})
        self.assertIsNone(dock.ask, "any key answers no")
        self.assertEqual(self.closes(dock), [])

    def test_question_goes_with_its_pane_and_a_refused_close_comes_back(self):
        dock = self.dock()
        dock.ask = ("pane", 11)
        dock.doc = {"panes": [p for p in self.DOC["panes"] if p["id"] != 11]}
        dock.draw(self.SIZE)
        self.assertIsNone(dock.ask)
        dock = self.dock()
        self.key(dock, "x")
        self.assertNotIn("a1", "".join(D.dump(dock.cv)))
        dock.closing[10] -= D.CLOSE_HIDE  # the config never dropped it
        dock.draw(self.SIZE)
        self.assertIn("a1", "".join(D.dump(dock.cv)))


if __name__ == "__main__":
    unittest.main()
