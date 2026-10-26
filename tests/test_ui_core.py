"""Terminal toolkit of the panels and views (bin/hivegrid_ui.py): canvas clipping and wide chars,
click regions, tty input decoding and colour codes."""
import os
import re
import sys
import unittest

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin")
sys.dont_write_bytecode = True  # bin/ is copied wholesale by install.sh: no __pycache__ there
sys.path.insert(0, BIN)
import hivegrid_ui as U  # noqa: E402

FG, BG = "#ffffff", "#000000"
WIDE = "界"  # East Asian wide: two cells


def text(cv, y):
    """Row y as the terminal shows it (the second half of a wide char prints nothing)."""
    return "".join(c[0] for c in cv.cells[y])


def strip_ansi(s):
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", s)


class ColorTest(unittest.TestCase):
    def setUp(self):
        self.saved = U.TRUECOLOR
        self.addCleanup(setattr, U, "TRUECOLOR", self.saved)

    def test_truecolor_codes(self):
        U.TRUECOLOR = True
        self.assertEqual(U.sgr_color("#4f9dff", False), "38;2;79;157;255")
        self.assertEqual(U.sgr_color("#4F9DFF", True), "48;2;79;157;255")

    def test_256_colour_cube_fallback(self):
        U.TRUECOLOR = False
        self.assertEqual(U.sgr_color("#000000", False), "38;5;16")
        self.assertEqual(U.sgr_color("#ffffff", True), "48;5;231")
        self.assertEqual(U.sgr_color("#ff0000", False), "38;5;196")
        # cube level boundaries: 0 below 48, 1 below 115, then steps of 40
        for v, level in ((47, 0), (48, 1), (114, 1), (115, 2), (155, 3), (195, 4), (235, 5), (255, 5)):
            with self.subTest(v=v):
                self.assertEqual(U.sgr_color("#%02x0000" % v, False), f"38;5;{16 + 36 * level}")

    def test_render_uses_the_active_colour_mode(self):
        cv = U.Canvas(2, 1, "#ff0000", "#0000ff")
        U.TRUECOLOR = True
        self.assertIn("38;2;255;0;0", cv.render())
        U.TRUECOLOR = False
        out = cv.render()
        self.assertIn("38;5;196", out)
        self.assertNotIn(";2;", out)

    def test_mix(self):
        self.assertEqual(U.mix("#000000", "#ffffff", 0), "#000000")
        self.assertEqual(U.mix("#000000", "#ffffff", 1), "#ffffff")
        self.assertEqual(U.mix("#000000", "#ff8000", 0.5), "#804000")


class WidthTest(unittest.TestCase):
    def test_widths(self):
        self.assertEqual(U.text_width("ab"), 2)
        self.assertEqual(U.text_width(WIDE), 2)
        self.assertEqual(U.text_width("e\u0301"), 1)  # combining accent
        self.assertEqual(U.text_width("a\u200db"), 2)  # zero-width joiner
        self.assertEqual(U.text_width("👍🏽"), 4)  # wide emoji + wide skin-tone modifier

    def test_fit_cells_never_exceeds_width(self):
        self.assertEqual(U.fit_cells("hello", 5), "hello")
        self.assertEqual(U.fit_cells("hello!", 5), "hell…")
        self.assertEqual(U.fit_cells("a" + WIDE * 3, 4), "a" + WIDE + "…")  # a wide char that would straddle is dropped
        self.assertEqual(U.fit_cells("hello", 0), "")
        for w in range(0, 8):
            with self.subTest(w=w):
                self.assertLessEqual(U.text_width(U.fit_cells(WIDE * 4 + "xy", w)), w)


class CanvasTest(unittest.TestCase):
    def cv(self, w=6, h=2):
        return U.Canvas(w, h, FG, BG)

    def test_clipping_at_all_edges(self):
        cv = self.cv()
        self.assertEqual(cv.put(-2, 0, "abcdefgh"), 8)  # returns the cells the text takes, clipped or not
        self.assertEqual(text(cv, 0), "cdefgh")
        cv = self.cv()
        cv.put(4, 1, "xyz")
        self.assertEqual(text(cv, 1), "    xy")
        for y in (-1, 2):
            self.assertEqual(cv.put(0, y, "abc"), 0)
        self.assertEqual([len(row) for row in cv.cells], [6, 6])

    def test_wide_char_takes_two_cells_and_renders_once(self):
        cv = self.cv()
        self.assertEqual(cv.put(1, 0, "a" + WIDE + "b"), 4)
        self.assertEqual(text(cv, 0), " a" + WIDE + "b ")
        self.assertEqual(cv.cells[0][3][0], "")
        self.assertEqual(U.text_width(strip_ansi(cv.render())), 2 * 6)  # both rows, no extra cells

    def test_wide_char_cut_by_the_right_edge_leaves_a_blank(self):
        cv = self.cv()
        cv.put(5, 0, WIDE)
        self.assertEqual(text(cv, 0), " " * 6)

    def test_wide_char_cut_by_the_left_edge_leaves_no_half(self):
        cv = self.cv()
        cv.put(-1, 0, WIDE + "a")  # the char's right half would be cell 0: it is not drawn at all
        self.assertEqual(text(cv, 0), " a    ")
        self.assertEqual(U.text_width(text(cv, 0)), 6)

    def test_overwriting_half_a_wide_char_blanks_the_other_half(self):
        for x, expect in ((2, " " + " " + "x" + "   "), (1, " x" + " " + "   ")):
            with self.subTest(x=x):
                cv = self.cv()
                cv.put(1, 0, WIDE)
                cv.put(x, 0, "x")
                self.assertEqual(text(cv, 0), expect)
                self.assertEqual(U.text_width(text(cv, 0)), 6)

    def test_fill_over_half_a_wide_char(self):
        cv = self.cv()
        cv.put(0, 0, WIDE + WIDE + WIDE)  # cells 0-1, 2-3, 4-5
        cv.fill(1, 0, 3, 1, bg="#123456", ch=".")  # right half of the 1st, all of the 2nd
        self.assertEqual(text(cv, 0), " ..." + WIDE)  # the 1st loses its left half too; the 3rd stays
        self.assertEqual(U.text_width(text(cv, 0)), 6)

    def test_zero_width_chars_join_the_previous_cell(self):
        cv = self.cv()
        n = cv.put(0, 0, "e\u0301x")
        self.assertEqual(n, 2)
        self.assertEqual(cv.cells[0][0][0], "e\u0301")
        self.assertEqual(cv.cells[0][1][0], "x")

    def test_fill_is_clipped(self):
        cv = self.cv()
        cv.fill(-3, -3, 5, 4, ch="#")
        self.assertEqual(text(cv, 0), "##    ")
        cv.fill(4, 1, 99, 99, ch="#")
        self.assertEqual(text(cv, 1), "    ##")

    def test_put_keeps_colours_it_is_not_given(self):
        cv = self.cv()
        cv.fill(0, 0, 6, 1, bg="#111111")
        cv.put(0, 0, "a", "#222222")
        self.assertEqual(cv.cells[0][0][1:3], ("#222222", "#111111"))

    def test_render_positions_each_row(self):
        cv = self.cv()
        cv.put(0, 1, "hi")
        out = cv.render()
        self.assertIn("\x1b[1;1H", out)
        self.assertIn("\x1b[2;1H", out)
        self.assertTrue(out.endswith("\x1b[0m"))


class HitTest(unittest.TestCase):
    def test_region_edges_are_half_open(self):
        cv = U.Canvas(20, 10, FG, BG)
        cv.hit(2, 3, 4, 2, "r")
        inside = [(2, 3), (5, 3), (2, 4), (5, 4)]
        outside = [(1, 3), (6, 3), (2, 2), (2, 5), (6, 5)]
        self.assertEqual([cv.hit_at(x, y) for x, y in inside], ["r"] * 4)
        self.assertEqual([cv.hit_at(x, y) for x, y in outside], [None] * 5)

    def test_last_registered_region_wins(self):
        cv = U.Canvas(20, 10, FG, BG)
        cv.hit(0, 0, 20, 10, "page")
        cv.hit(5, 5, 2, 1, "button")
        self.assertEqual(cv.hit_at(5, 5), "button")
        self.assertEqual(cv.hit_at(7, 5), "page")

    def test_keys_are_separate_from_unkeyed_regions(self):
        cv = U.Canvas(20, 10, FG, BG)
        cv.hit(0, 0, 10, 1, "a", key="row-a")
        cv.hit(0, 0, 5, 1, "b")  # covers part of row-a without a key
        self.assertEqual(cv.hit_at(1, 0), "b")
        self.assertEqual(cv.key_at(1, 0), "row-a")
        self.assertIsNone(cv.key_at(10, 0))

    def test_empty_region_never_hits(self):
        cv = U.Canvas(20, 10, FG, BG)
        cv.hit(3, 3, 0, 1, "zero")
        self.assertIsNone(cv.hit_at(3, 3))


def key(*mods_and_key):
    *mods, k = mods_and_key
    return ("key", frozenset(mods), k)


class DecodeTest(unittest.TestCase):
    def d(self, data):
        return U.decode(data)

    def test_sgr_mouse(self):
        cases = {
            b"\x1b[<0;1;1M": ("mouse", "press", "left", 0, 0),
            b"\x1b[<0;120;45m": ("mouse", "release", "left", 119, 44),
            b"\x1b[<2;5;6M": ("mouse", "press", "right", 4, 5),
            b"\x1b[<1;5;6M": ("mouse", "press", "middle", 4, 5),
            b"\x1b[<32;3;4M": ("mouse", "drag", "left", 2, 3),
            b"\x1b[<34;3;4M": ("mouse", "drag", "right", 2, 3),
            b"\x1b[<64;9;9M": ("mouse", "wheel", "up", 8, 8),
            b"\x1b[<65;9;9M": ("mouse", "wheel", "down", 8, 8),
            b"\x1b[<4;2;2M": ("mouse", "press", "left", 1, 1),  # shift held: still a left press
            b"\x1b[<80;2;2M": ("mouse", "wheel", "up", 1, 1),  # ctrl+wheel
        }
        for data, ev in cases.items():
            with self.subTest(data=data):
                self.assertEqual(self.d(data), [ev])

    def test_csi_keys_with_modifiers(self):
        cases = {
            b"\x1b[A": key("UpArrow"), b"\x1b[1;5C": key("CTRL", "RightArrow"),
            b"\x1b[1;2D": key("SHIFT", "LeftArrow"), b"\x1b[1;3B": key("ALT", "DownArrow"),
            b"\x1b[1;8H": key("SHIFT", "ALT", "CTRL", "Home"),
            b"\x1b[3~": key("Delete"), b"\x1b[5;5~": key("CTRL", "PageUp"), b"\x1b[24~": key("F12"),
            b"\x1b[Z": key("SHIFT", "Tab"), b"\x1bOP": key("F1"), b"\x1bOA": key("UpArrow"),
            b"\x1b[13;2u": key("SHIFT", "Enter"), b"\x1b[27u": key("Escape"), b"\x1b[97;5u": key("CTRL", "a"),
        }
        for data, ev in cases.items():
            with self.subTest(data=data):
                self.assertEqual(self.d(data), [ev])

    def test_plain_and_control_keys(self):
        cases = {
            b"a": key("a"), b"A": key("SHIFT", "a"), b"\r": key("Enter"), b"\n": key("Enter"), b"\t": key("Tab"),
            b"\x7f": key("Backspace"), b"\x08": key("Backspace"), b"\x01": key("CTRL", "a"),
            b"\x00": key("CTRL", "Space"), b" ": key("Space"), b"1": key("1"),
            "é".encode(): key("é"), "界".encode(): key("界"),
        }
        for data, ev in cases.items():
            with self.subTest(data=data):
                self.assertEqual(self.d(data), [ev])

    def test_escape_alone_and_alt_keys(self):
        self.assertEqual(self.d(b"\x1b"), [key("Escape")])
        self.assertEqual(self.d(b"\x1bx"), [key("ALT", "x")])
        self.assertEqual(self.d(b"\x1bX"), [key("ALT", "SHIFT", "x")])

    def test_several_events_in_one_read(self):
        data = b"j\x1b[<0;3;4M\x1b[<0;3;4mq\x1b[B\x1b"
        self.assertEqual(self.d(data), [key("j"), ("mouse", "press", "left", 2, 3), ("mouse", "release", "left", 2, 3),
                                        key("q"), key("DownArrow"), key("Escape")])

    def test_invalid_utf8_does_not_raise(self):
        events = self.d(b"\xff\xfeq")
        self.assertEqual(events[-1], key("q"))


class SplitReadTest(unittest.TestCase):
    """A read boundary can cut an escape sequence or a UTF-8 char (fast motion reports): the cut-off
    tail must wait for the next read instead of turning into keystrokes."""

    STREAM = ("j\x1b[<35;12;3M\x1b[<0;12;3m\x1b[1;5C\x1bOP\x1bx界é\x1b[3~q").encode()

    def feed(self, chunks):
        events, tail = [], b""
        for chunk in chunks:
            head, tail = U.split_complete(tail + chunk)
            events += U.decode(head)
        return events + (U.decode(tail) if tail else [])

    def test_every_split_point_gives_the_same_events(self):
        whole = U.decode(self.STREAM)
        self.assertEqual(len(whole), 10)
        for cut in range(1, len(self.STREAM)):
            with self.subTest(cut=cut):
                self.assertEqual(self.feed([self.STREAM[:cut], self.STREAM[cut:]]), whole)

    def test_byte_by_byte(self):
        self.assertEqual(self.feed([bytes([b]) for b in self.STREAM]), U.decode(self.STREAM))

    def test_complete_input_has_no_tail(self):
        self.assertEqual(U.split_complete(self.STREAM), (self.STREAM, b""))

    def ui(self):
        r, w = os.pipe()
        self.addCleanup(os.close, r)
        self.addCleanup(os.close, w)
        ui = U.UI.__new__(U.UI)  # only the input side: next_events needs nothing but the fd
        ui.fd = r
        return ui, w

    def test_ui_carries_a_split_mouse_report_to_the_next_read(self):
        ui, w = self.ui()
        os.write(w, b"a\x1b[<0;12;")
        self.assertEqual(ui.next_events(0.1), [key("a")])
        os.write(w, b"3M")
        self.assertEqual(ui.next_events(0.1), [("mouse", "press", "left", 11, 2)])

    def test_ui_flushes_a_lone_escape_when_nothing_follows(self):
        ui, w = self.ui()
        os.write(w, b"\x1b")
        self.assertEqual(ui.next_events(0.1), [])
        self.assertEqual(ui.next_events(0.1), [key("Escape")])


PAL = U.DEFAULT_PAL


def canvas(w, h=6):
    return U.Canvas(w, h, PAL["text"], PAL["surface"])


class FitTest(unittest.TestCase):
    def test_fit_path_keeps_the_repository_name(self):
        self.assertEqual(U.fit_path("a/reference/unitree_ros2", 30), "a/reference/unitree_ros2")
        self.assertEqual(U.fit_path("g1-workspace/reference/unitree_ros2", 26), "…/reference/unitree_ros2")
        self.assertEqual(U.fit_path("g1-workspace/reference/unitree_ros2", 14), "…/unitree_ros2")
        self.assertEqual(U.fit_path("g1-workspace/reference/unitree_ros2", 8), "unitree…")

    def test_fit_tail_keeps_the_end_and_counts_cells(self):
        self.assertEqual(U.fit_tail("~/work/deep/dir", 8), "…eep/dir")
        self.assertEqual(U.fit_tail("short", 8), "short")
        self.assertEqual(U.text_width(U.fit_tail("ab" + WIDE * 4, 6)), 5)  # a wide char never straddles
        self.assertEqual(U.fit_text("abcdef", 0), "")

    def test_home_path(self):
        home = os.path.expanduser("~")
        self.assertEqual(U.home_path(home + "/Projects/x/"), "~/Projects/x")
        self.assertEqual(U.home_path(home), "~")
        self.assertEqual(U.home_path(home + "x/y"), home + "x/y")  # a sibling of home is not home
        self.assertEqual(U.home_path("/"), "/")


class FooterTest(unittest.TestCase):
    HINTS = [("↵", "open"), ("r", "rescan"), ("q", "close")]

    def test_pairs_drop_from_the_end_when_narrow(self):
        self.assertEqual(U.footer_fit(self.HINTS, 25), self.HINTS)  # exactly "↵ open  r rescan  q close"
        self.assertEqual(U.footer_fit(self.HINTS, 24), self.HINTS[:2])
        self.assertEqual(U.footer_fit(self.HINTS, 5), [])
        self.assertEqual(U.footer_fit("1-9 jump  q close", 9), [("1-9", "jump")])

    def test_footer_draws_keys_and_labels_in_their_colours(self):
        cv = canvas(30)
        U.footer(cv, PAL, self.HINTS)
        self.assertEqual(text(cv, 5), " ↵ open  r rescan  q close    ")
        self.assertEqual(cv.cells[5][1][1], PAL["text"])  # the key
        self.assertEqual(cv.cells[5][3][1], PAL["muted"])  # its label

    def test_right_text_keeps_its_place_and_two_cells_gap(self):
        cv = canvas(24)
        U.footer(cv, PAL, self.HINTS, right=("3/40", PAL["muted"]))
        self.assertEqual(text(cv, 5), " ↵ open  r rescan  3/40 ")
        cv = canvas(6)
        U.footer(cv, PAL, self.HINTS, right=("too wide", PAL["muted"]))  # does not fit at all: dropped
        self.assertEqual(text(cv, 5).strip(), "")


class TitleRowTest(unittest.TestCase):
    def test_crumb_shrinks_then_right_segments_drop_from_the_end(self):
        right = [("●1", PAL["red"]), ("●4", PAL["amber"])]
        cv = canvas(40)
        t = U.title_row(cv, PAL, "Git", "~/Projects/hivegrid", right)
        self.assertEqual(text(cv, 0), " ⬡ Git · ~/Projects/hivegrid      ●1 ●4 ")
        self.assertEqual((t.left, t.right, t.body), (28, 34, 2))
        cv = canvas(28)
        U.title_row(cv, PAL, "Git", "~/Projects/hivegrid", right)
        self.assertEqual(text(cv, 0), " ⬡ Git · …/hivegrid   ●1 ●4 ")
        cv = canvas(22)
        U.title_row(cv, PAL, "Git", "~/Projects/hivegrid", right)
        self.assertEqual(text(cv, 0), " ⬡ Git · hivegrid  ●1 ")

    def test_rule_is_readable_on_the_row_background(self):
        cv = canvas(20)
        U.title_row(cv, PAL, "Overview")
        self.assertEqual(text(cv, 1), "─" * 20)
        fg, bg = cv.cells[1][5][1:3]
        self.assertEqual(bg, PAL["surface"])
        self.assertEqual(fg, U.mix(PAL["surface"], PAL["edge_hi"], 0.45))

    def test_chips_sit_at_the_right_edge_with_regions(self):
        cv = canvas(30)
        U.title_row(cv, PAL, "Overview", right=[("?2", PAL["violet"])],
                    chips=[U.Chip("⊞", key="arrange"), U.Chip("▦", key="equalize")])
        self.assertEqual(text(cv, 0), " ⬡ Overview        ?2  ⊞   ▦  ")
        self.assertEqual((cv.key_at(23, 0), cv.key_at(27, 0), cv.key_at(25, 0)), ("arrange", "equalize", None))


class ListRowTest(unittest.TestCase):
    def test_columns_and_selection_marker(self):
        cv = canvas(30)
        row = U.list_row(cv, PAL, 2, lines=2, selected=True, glyph="↻", glyph_color=PAL["accent"], num=3,
                         name="hivegrid", right="working 2m", sub="writing tests")
        self.assertEqual(text(cv, 2), "▎ ↻ 3 hivegrid     working 2m ")
        self.assertEqual(text(cv, 3), "▎     writing tests           ")
        self.assertEqual(cv.cells[2][0][1], PAL["accent"])  # the marker
        self.assertEqual({c[2] for c in cv.cells[2] + cv.cells[3]}, {PAL["raised"]})  # the slab, both lines
        self.assertEqual(cv.cells[2][6][1:], (PAL["bright"], PAL["raised"], U.BOLD))
        self.assertEqual(cv.cells[2][4][1], PAL["accent"])  # the selected row's number
        self.assertEqual(row, U.Row(PAL["raised"], 6, 29))

    def test_unselected_row_has_no_marker_and_hover_tints(self):
        cv = canvas(30)
        U.list_row(cv, PAL, 0, glyph="·", num=1, name="idle", hover=1.0)
        self.assertEqual(text(cv, 0)[0], " ")
        self.assertEqual(cv.cells[0][10][2], U.mix(PAL["surface"], PAL["raised"], U.HOVER_BLEND))
        self.assertEqual(cv.cells[0][6][3], 0)  # not bold

    def test_name_ellipsis_and_right_text_dropped_before_the_name_vanishes(self):
        cv = canvas(20)
        U.list_row(cv, PAL, 0, glyph="·", num=1, name="a-very-long-name", right="idle 3m")
        self.assertEqual(text(cv, 0), "  · 1 a-ve… idle 3m ")
        cv = canvas(16)  # the name would get 3 cells: the right text goes
        U.list_row(cv, PAL, 0, glyph="·", num=1, name="a-very-long-name", right="idle 3m")
        self.assertEqual(text(cv, 0), "  · 1 a-very-l… ")

    def test_optional_columns_and_path_fit(self):
        cv = canvas(24)
        row = U.list_row(cv, PAL, 0, name="g1-workspace/reference/unitree_ros2", name_fit="path")
        self.assertEqual(row.name_x, 2)
        self.assertEqual(text(cv, 0).rstrip(), "  …/unitree_ros2")

    def test_action_takes_the_right_end_with_its_own_region(self):
        cv = canvas(30)
        row = U.list_row(cv, PAL, 0, selected=True, glyph="·", num=1, name="beta", right="idle 3m",
                         key=6, action=U.Action("✕", key=("x", 6)))
        self.assertEqual(text(cv, 0), "▎ · 1 beta          idle 3m ✕ ")
        self.assertEqual((cv.key_at(28, 0), cv.key_at(20, 0)), (("x", 6), 6))
        self.assertEqual(row.end, 27)


class ChipTest(unittest.TestCase):
    def test_kinds(self):
        cv = canvas(40)
        x = 0
        for kind, sel in (("tab", True), ("tab", False), ("button", False), ("primary", False), ("disabled", False)):
            x += U.chip(cv, PAL, x, 0, "Ab", kind=kind, selected=sel, hit=lambda b: None) + 1
        self.assertEqual(text(cv, 0)[:20], " Ab   Ab   Ab   Ab  ")
        styles = [cv.cells[0][i][1:] for i in (1, 6, 11, 16, 21)]
        self.assertEqual(styles[0], (PAL["bright"], PAL["raised"], U.BOLD))
        self.assertEqual(styles[1], (PAL["muted"], PAL["surface"], 0))
        self.assertEqual(styles[2], (PAL["bright"], PAL["raised"], 0))
        self.assertEqual(styles[3], (PAL["deep"], PAL["accent"], U.BOLD))
        self.assertEqual(styles[4], (PAL["dim"], PAL["pill"], 0))
        self.assertIsNone(cv.hit_at(21, 0))  # disabled: not clickable
        self.assertEqual(U.chip(cv, PAL, 0, 2, "Ab", round=True), U.chip_width("Ab", round=True))
        self.assertEqual(text(cv, 2)[:6], U.CAP_L + " Ab " + U.CAP_R)


class StateTableTest(unittest.TestCase):
    def test_states_match_the_design_table(self):
        want = {"working": ("↻", "accent"), "busy": ("◔", "amber"), "attention": ("?", "violet"),
                "done": ("✓", "green"), "idle": ("·", "grey"), "exited": ("○", "muted"), "bell": ("!", "red"),
                "shell": ("›", "dim")}
        for kind, (glyph, color) in want.items():
            self.assertEqual((U.STATES[kind].glyph, U.STATES[kind].color), (glyph, color), kind)
        self.assertEqual(U.STATES["attention"].label, "needs you")
        self.assertEqual(U.state_style(PAL, "nonsense")[0], "·")
        self.assertIn(U.state_style(PAL, "working", now=0.0)[0], U.SPINNER)

    def test_lua_twin_has_the_same_table(self):
        with open(os.path.join(BIN, "..", "config", "theme.lua"), encoding="utf-8") as f:
            lua = f.read()
        rows = dict((k, (g, t, c, int(r))) for k, g, t, c, r in
                    re.findall(r"(\w+) = \{ '([^']+)', '([^']+)', '(\w+)', (\d+) \}", lua))
        self.assertEqual(rows, {k: tuple(s) for k, s in U.STATES.items()})


class HoverTest(unittest.TestCase):
    def test_fade_in_out_and_pointer_shape(self):
        h = U.Hover()
        self.assertTrue(h.set("a", now=10.0))  # nothing -> something: the hand pointer
        self.assertFalse(h.set("b", now=10.0))  # still a hand
        h.advance(10.0 + U.FADE_IN / 2)
        self.assertEqual(h.levels(), {"b": 0.5})
        self.assertFalse(h.advance(11.0))
        self.assertTrue(h.set(None, now=20.0))
        self.assertTrue(h.advance(20.0 + U.FADE_OUT / 2))
        self.assertFalse(h.advance(21.0))
        self.assertEqual(h.levels(), {})


class ContentBoxTest(unittest.TestCase):
    def test_capped_and_centred(self):
        self.assertEqual(U.content_box(80), (1, 78))
        self.assertEqual(U.content_box(240), (65, 110))
        self.assertEqual(U.content_box(1), (0, 0))


if __name__ == "__main__":
    unittest.main()
