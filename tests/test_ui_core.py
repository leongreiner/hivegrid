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


if __name__ == "__main__":
    unittest.main()
