"""Logic of the usage, agents and monitor panels (bin/hivegrid_panels.py) without a terminal:
history loading and filtering, the usage page's notes and reset markers on its braille chart,
`wezterm cli list` parsing and the monitor's sampling."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin")
sys.dont_write_bytecode = True  # bin/ is copied wholesale by install.sh: no __pycache__ there
sys.path.insert(0, BIN)
import hivegrid_panels as P  # noqa: E402
import hivegrid_ui as U  # noqa: E402

NOW = 1_700_000_000.0
H = 3600


class Pal(dict):
    """Palette with a distinct colour for every key, so a cell's role shows in its colour."""

    def __missing__(self, key):
        value = "#%06x" % (hash(key) & 0xFFFFFF)
        self[key] = value
        return value


class FakeUI:
    def __init__(self):
        self.pal = Pal(accent="#0000f1", amber="#0000f2", muted="#0000f3", base="#000001", deep="#000002")


class Model:
    def __init__(self, accounts):
        self.accounts = accounts


def sample(ts, label="work", five=None, seven=None, **extra):
    s = {"ts": ts, "label": label, **extra}
    if five is not None:
        s["five_hour"] = five
    if seven is not None:
        s["seven_day"] = seven
    return s


class Env(unittest.TestCase):
    """Fake HOME and quota cache; braille charts instead of images."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        patcher = mock.patch.dict(os.environ, {"HOME": self.tmp, "CLAUDE_QUOTA_CACHE_DIR": os.path.join(self.tmp, "cache"),
                                               "HIVEGRID_NO_IMAGES": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)


# ---- history ---------------------------------------------------------------------------


class LoadHistoryTest(Env):
    def test_malformed_lines_are_skipped(self):
        path = os.path.join(self.tmp, "history.jsonl")
        good = [sample(NOW - 60, five={"pct": 10}), sample(NOW, "2", seven={"pct": 5.5})]
        lines = [json.dumps(good[0]), "", "not json", "[1, 2]", json.dumps({"ts": "yesterday", "label": "work"}),
                 json.dumps({"ts": NOW, "label": 7}), json.dumps({"label": "work"}), json.dumps(good[1]),
                 '{"ts": 1700000100, "label": "se']  # a write cut short
        with open(path, "w") as f:
            f.write("\n".join(lines))
        self.assertEqual(P.load_history(path), good)

    def test_missing_file_is_empty(self):
        self.assertEqual(P.load_history(os.path.join(self.tmp, "nope.jsonl")), [])


class SeriesForTest(Env):
    def test_range_start_is_inclusive_and_first_ignores_the_range(self):
        samples = [sample(NOW - 10 * H, five={"pct": 1}), sample(NOW - 6 * H, five={"pct": 2}),
                   sample(NOW - H, five={"pct": 3}, seven={"pct": 30})]
        series, _, first = P.series_for(samples, "work", NOW - 6 * H)
        self.assertEqual(series["five_hour"], [(NOW - 6 * H, 2.0), (NOW - H, 3.0)])
        self.assertEqual(series["seven_day"], [(NOW - H, 30.0)])
        self.assertEqual(first, NOW - 10 * H)

    def test_points_are_sorted_and_bad_windows_skipped(self):
        samples = [sample(NOW - 1, five={"pct": 9}), sample(NOW - 3, five={"pct": 7}),
                   sample(NOW - 2, five={"pct": "high"}, seven="broken")]
        series, _, _ = P.series_for(samples, "work", NOW - H)
        self.assertEqual(series["five_hour"], [(NOW - 3, 7.0), (NOW - 1, 9.0)])
        self.assertEqual(series["seven_day"], [])

    def test_reset_times_are_deduplicated_to_the_minute(self):
        r = 1_700_010_000  # a whole minute
        samples = [sample(NOW - 3, five={"pct": 1, "reset_at": r + 12}), sample(NOW - 2, five={"pct": 2, "reset_at": r - 20}),
                   sample(NOW - 1, five={"pct": 3, "reset_at": r + 5 * H})]
        _, resets, _ = P.series_for(samples, "work", NOW - H)
        self.assertEqual(resets["five_hour"], {r, r + 5 * H})

    def test_samples_follow_the_account_directory(self):
        samples = [sample(NOW - 3, "2", five={"pct": 1}),  # old label-only sample of ~/.claude-2
                   sample(NOW - 2, "team", five={"pct": 2}, dir="~/.claude-2"),
                   sample(NOW - 1, "team", five={"pct": 3}, dir="~/.claude-other")]  # another account, same label
        series, _, _ = P.series_for(samples, "team", NOW - H, acc_dir="~/.claude-2")
        self.assertEqual([v for _, v in series["five_hour"]], [1.0, 2.0])

    def test_other_accounts_do_not_count_as_history(self):
        _, _, first = P.series_for([sample(NOW - 5, "other", five={"pct": 1})], "work", NOW - H, acc_dir="~/.claude-work")
        self.assertIsNone(first)


class UsagePageTest(Env):
    def page(self, samples, range_name="24h"):
        pg = P.UsagePage(Model([{"label": "work", "dir": "~/.claude-work"}]), "work", range_name,
                         history=os.path.join(self.tmp, "none.jsonl"), now=lambda: NOW)
        pg.samples = samples
        return pg

    def render(self, pg, w=100, h=30):
        ui = FakeUI()
        cv = U.Canvas(w, h, "#ffffff", ui.pal["base"])
        pg.render(ui, cv, 0, 0, w, h)
        return ui, cv, ["".join(c[0] for c in row) for row in cv.cells]

    def text(self, pg):
        return "\n".join(self.render(pg)[2])

    def test_history_starting_inside_the_range_is_noted(self):
        samples = [sample(NOW - 3 * H, five={"pct": 10}), sample(NOW - H, five={"pct": 20})]
        for rng in ("6h", "24h", "7d"):
            with self.subTest(rng=rng):
                self.assertIn("History starts", self.text(self.page(samples, rng)))

    def test_history_covering_the_range_is_not_noted(self):
        samples = [sample(NOW - 25 * H, five={"pct": 10}), sample(NOW - H, five={"pct": 20})]
        self.assertNotIn("History starts", self.text(self.page(samples, "24h")))

    def test_history_older_than_the_range_asks_for_a_longer_range(self):
        text = self.text(self.page([sample(NOW - 10 * H, five={"pct": 10})], "6h"))
        self.assertIn("No samples in the last 6h", text)
        self.assertNotIn("No history", text)

    def test_account_without_history(self):
        text = self.text(self.page([sample(NOW - H, "other", five={"pct": 10})]))
        self.assertIn("No history for this account", text)

    def markers(self, pg):
        ui, cv, _ = self.render(pg)
        found = [cell[1] for row in cv.cells for cell in row if cell[0] == "▲"]
        return found.count(ui.pal["accent"]), found.count(ui.pal["amber"])

    def test_5h_resets_marked_up_to_24h_and_7d_resets_at_every_range(self):
        samples = [sample(NOW - 2 * H, five={"pct": 40, "reset_at": NOW - 90 * 60}, seven={"pct": 10, "reset_at": NOW - H}),
                   sample(NOW - 600, five={"pct": 5}, seven={"pct": 12})]
        self.assertEqual(self.markers(self.page(samples, "6h")), (1, 1))
        self.assertEqual(self.markers(self.page(samples, "24h")), (1, 1))
        self.assertEqual(self.markers(self.page(samples, "7d")), (0, 1))
        self.assertEqual(self.markers(self.page(samples, "30d")), (0, 1))

    def test_resets_outside_the_range_are_not_marked(self):
        samples = [sample(NOW - H, five={"pct": 40, "reset_at": NOW + 2 * H}, seven={"pct": 10, "reset_at": NOW + 3 * 86400})]
        self.assertEqual(self.markers(self.page(samples, "6h")), (0, 0))

    def test_range_keys(self):
        pg = self.page([], "bogus")
        self.assertEqual(P.RANGES[pg.range][0], "24h")
        pg.key(None, frozenset(), "1")
        self.assertEqual(P.RANGES[pg.range][0], "6h")
        pg.key(None, frozenset(), "DownArrow")
        self.assertEqual(P.RANGES[pg.range][0], "6h")
        pg.key(None, frozenset(), "4")
        pg.key(None, frozenset(), "UpArrow")
        self.assertEqual(P.RANGES[pg.range][0], "30d")


class BrailleTest(unittest.TestCase):
    BOTTOM = 0x40 | 0x80
    TOP = 0x01 | 0x08

    def chart(self, pts, cols=10, rows=3, x0=0, x1=100):
        return U.braille_chart(cols, rows, [{"pts": pts, "color": "c"}], x0, x1, 100.0)

    def bits(self, chart):
        return [[ord(ch) - 0x2800 if ch != " " else 0 for ch, _ in row] for row in chart]

    def test_zero_percent_is_the_bottom_dot_row_across_the_range(self):
        b = self.bits(self.chart([(0, 0), (100, 0)]))
        self.assertTrue(all(v == 0 for row in b[:-1] for v in row))
        self.assertTrue(all(v == self.BOTTOM for v in b[-1]))

    def test_hundred_percent_and_above_is_the_top_dot_row(self):
        b = self.bits(self.chart([(0, 100), (100, 250)]))
        self.assertTrue(all(v == self.TOP for v in b[0]))
        self.assertTrue(all(v == 0 for row in b[1:] for v in row))

    def test_negative_values_clamp_to_the_bottom(self):
        b = self.bits(self.chart([(50, -20)]))
        self.assertEqual(sum(1 for row in b[:-1] for v in row if v), 0)
        self.assertTrue(any(b[-1]))

    def test_empty_series_draws_nothing(self):
        chart = self.chart([])
        self.assertEqual(len(chart), 3)
        self.assertTrue(all(len(row) == 10 and all(ch == " " and c is None for ch, c in row) for row in chart))

    def test_points_outside_the_time_range_are_dropped(self):
        b = self.bits(self.chart([(-50, 100), (150, 100)]))
        self.assertTrue(all(v == 0 for row in b for v in row))

    def test_single_point_is_one_dot_at_the_range_end(self):
        chart = self.chart([(100, 100)])
        lit = [(r, c) for r, row in enumerate(chart) for c, (ch, _) in enumerate(row) if ch != " "]
        self.assertEqual(lit, [(0, 9)])
        self.assertEqual(ord(chart[0][9][0]) - 0x2800, 0x08)  # right column, top dot
        self.assertEqual(chart[0][9][1], "c")


# ---- agents ----------------------------------------------------------------------------


class ParsePanesTest(unittest.TestCase):
    HOME = "/home/leon"

    def panes(self, panes, fg=None):
        return P.parse_panes(json.dumps(panes), fg or {}, home=self.HOME)

    def test_tabs_and_windows_are_numbered_in_mux_order(self):
        rows = self.panes([{"pane_id": 5, "tab_id": 40, "window_id": 9}, {"pane_id": 6, "tab_id": 40, "window_id": 9},
                           {"pane_id": 7, "tab_id": 12, "window_id": 9}, {"pane_id": 8, "tab_id": 3, "window_id": 2}])
        self.assertEqual([(r["pane"], r["tab"], r["window"]) for r in rows], [(5, 1, 1), (6, 1, 1), (7, 2, 1), (8, 1, 2)])

    def test_cwd_from_url_shortened_under_home_only(self):
        rows = self.panes([{"pane_id": 1, "cwd": "file://box/home/leon/proj/"}, {"pane_id": 2, "cwd": "file://box/home/leon"},
                           {"pane_id": 3, "cwd": "file:///home/leonard/x"}, {"pane_id": 4, "cwd": "/"}])
        self.assertEqual([r["cwd"] for r in rows], ["~/proj", "~", "/home/leonard/x", "/"])

    def test_foreground_process_profile_and_kind(self):
        fg = {"pts/1": (10, "omp", 75, "omp --profile=work"), "pts/2": (11, "omp", 5, "omp --profile team"),
              "pts/3": (12, "bash", 9, "-bash"), "pts/4": (13, "claude", 3, "claude --profile=x")}
        rows = self.panes([{"pane_id": i, "tty_name": f"/dev/pts/{i}"} for i in (1, 2, 3, 4, 5)], fg)
        self.assertEqual([(r["proc"], r["profile"], r["agent"], r["idle"]) for r in rows],
                         [("omp", "work", True, False), ("omp", "team", True, False), ("bash", "", False, True),
                          ("claude", "", True, False), ("?", "", False, False)])
        self.assertEqual(rows[0]["age"], 75)

    def test_hivegrid_chrome_panes_are_left_out(self):
        fg = {"pts/1": (1, "python3", 1, "python3 /home/leon/.local/bin/hivegrid-bar"), "pts/2": (2, "yazi", 1, "yazi"),
              "pts/3": (3, "omp", 1, "omp")}
        rows = self.panes([{"pane_id": i, "tty_name": f"/dev/pts/{i}"} for i in (1, 2, 3)], fg)
        self.assertEqual([r["pane"] for r in rows], [3])

    def test_malformed_list_output(self):
        for text in ("", "not json", "{}", "null"):
            with self.subTest(text=text):
                self.assertEqual(P.parse_panes(text, {}, home=self.HOME), [])


# ---- monitor ---------------------------------------------------------------------------


class MonitorTest(unittest.TestCase):
    def test_cpu_line_counts_iowait_as_idle(self):
        self.assertEqual(P.parse_cpu_line("cpu  10 0 10 70 10 0 0 0 5 0\ncpu0 1 2 3 4"), (100, 80))
        self.assertIsNone(P.parse_cpu_line("intr 1 2 3"))

    def test_meminfo(self):
        pct, total, avail = P.parse_meminfo("MemTotal: 1000 kB\nMemFree: 1 kB\nMemAvailable: 250 kB\n")
        self.assertEqual((pct, total, avail), (75.0, 1000, 250))
        self.assertIsNone(P.parse_meminfo("garbage")[0])

    def sampler(self, stats):
        s = P.Sampler()
        s.gpu_kind = None  # never run nvidia-smi
        files = {"/proc/stat": iter(stats), "/proc/meminfo": None}
        s.read = lambda path: next(files[path]) if path == "/proc/stat" else "MemTotal: 100 kB\nMemAvailable: 40 kB\n"
        return s

    def tick_at(self, s, t):
        with mock.patch.object(P.time, "time", return_value=t):
            return s.tick()

    def test_cpu_percent_from_successive_samples(self):
        s = self.sampler(["cpu 100 0 0 900 0 0 0 0", "cpu 130 0 0 960 10 0 0 0", "cpu 10 0 0 10 0 0 0 0"])
        self.assertTrue(self.tick_at(s, 1000.0))
        self.assertIsNone(s.current("CPU"), "one sample gives no rate")
        self.assertFalse(self.tick_at(s, 1000.5), "at most one sample per second")
        self.assertTrue(self.tick_at(s, 1001.0))
        self.assertAlmostEqual(s.current("CPU"), 30.0)  # 30 busy of 100 jiffies, iowait idle
        self.assertAlmostEqual(s.current("RAM"), 60.0)
        self.assertTrue(self.tick_at(s, 1002.0))  # counters went backwards (e.g. after a suspend)
        self.assertIsNone(s.current("CPU"))

    def test_values_place_samples_by_second_with_gaps_before(self):
        s = self.sampler(["cpu 0 0 0 0"] * 3)
        for t in (100.0, 101.0, 102.0):
            self.tick_at(s, t)
        self.assertEqual(s.values("RAM", 5), [None, None, None, 60.0, 60.0, 60.0])
        self.assertEqual(s.values("RAM", 5, now=110.0), [None] * 6)


if __name__ == "__main__":
    unittest.main()
