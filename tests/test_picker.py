"""bin/hivegrid_picker.py: filter, keys, digits, confirm default and layout bounds."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import hivegrid_picker as HP  # noqa: E402

ROWS = [
    {"id": "a", "key": "2", "glyph": "?", "color": "violet", "cols": [{"text": "alpha"}, {"text": "needs you"}], "meta": "40s"},
    {"id": "b", "key": "1", "cols": [{"text": "beta"}, {"segs": [{"text": "5h "}, {"text": "━━", "color": "green"}]}]},
    {"id": "c", "key": "5", "cols": ["gamma"]},
]


def picker(**spec):
    return HP.Picker(dict({"rows": ROWS, "palette": {}}, **spec))


class PickerTest(unittest.TestCase):
    def test_typing_filters_and_enter_chooses_the_match(self):
        p = picker()
        for ch in "gam":
            p.key(set(), ch)
        self.assertEqual([r["id"] for r in p.shown], ["c"])
        p.key(set(), "Enter")
        self.assertEqual((p.done, p.result), (True, "c"))

    def test_digits_pick_by_key_and_j_k_move_until_a_filter_is_typed(self):
        p = picker()
        p.key(set(), "j")
        self.assertEqual(p.sel, 1)
        p.key(set(), "5")
        self.assertEqual(p.result, "c")
        q = picker()
        q.key(set(), "/")
        q.key(set(), "5")
        self.assertFalse(q.done, "after / a digit is filter text")
        self.assertEqual(q.filter, "5")

    def test_without_keys_digits_pick_the_nth_row_shown(self):
        p = HP.Picker({"rows": [{"id": "x", "cols": ["x"]}, {"id": "y", "cols": ["y"]}]})
        p.key(set(), "2")
        self.assertEqual(p.result, "y")
        p = HP.Picker({"rows": [{"id": "x", "cols": ["x"]}]})
        p.key(set(), "3")
        self.assertFalse(p.done, "no third row: nothing happens")

    def test_escape_clears_the_filter_first_then_closes(self):
        p = picker()
        p.key(set(), "z")
        p.key(set(), "Escape")
        self.assertEqual((p.filter, p.done), ("", False))
        p.key(set(), "Escape")
        self.assertEqual((p.done, p.result), (True, None))

    def test_segments_count_as_text_for_the_filter(self):
        self.assertTrue(HP.matches(ROWS[1], ["5h"]))
        self.assertFalse(HP.matches(ROWS[1], ["5h", "zzz"]))

    def test_confirm_defaults_to_no(self):
        p = HP.Picker({"confirm": {"text": "Sure?", "danger": True}})
        p.key(set(), "Enter")
        self.assertEqual(p.result, "no")
        p = HP.Picker({"confirm": {"text": "Sure?"}})
        p.key(set(), "RightArrow")
        p.key(set(), "Enter")
        self.assertEqual(p.result, "yes")

    def test_column_widths_fit_the_room(self):
        rows = [{"cols": [{"text": "x" * 40}, {"text": "y" * 30}, {"text": "z" * 50}]}]
        for room in (20, 60, 200):
            w = HP.column_widths(rows, room)
            self.assertLessEqual(sum(w) + 2 * (len(w) - 1), max(room, 4 * 2 + 4))

    def test_card_stays_inside_the_window_at_every_size(self):
        for cols, rows in ((40, 12), (80, 24), (160, 45), (320, 85)):
            p = picker()
            cv = p.frame(cols, rows)
            self.assertEqual((cv.w, cv.h), (cols, rows))
            self.assertLessEqual(p.card_width(cols), cols)


if __name__ == "__main__":
    unittest.main()
