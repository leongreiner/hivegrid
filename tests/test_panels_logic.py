"""Logic of the usage, agents, monitor and settings panels (bin/hivegrid_panels.py) without a
terminal: history loading and filtering, the usage page's notes and reset markers on its braille
chart, `wezterm cli list` parsing, the monitor's sampling, and the wide (two-pane) layouts."""
import io
import json
import os
import shutil
import sys
import tempfile
import time
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


def sample(ts, label="alpha", five=None, seven=None, **extra):
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
        lines = [json.dumps(good[0]), "", "not json", "[1, 2]", json.dumps({"ts": "yesterday", "label": "alpha"}),
                 json.dumps({"ts": NOW, "label": 7}), json.dumps({"label": "alpha"}), json.dumps(good[1]),
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
        series, _, first = P.series_for(samples, "alpha", NOW - 6 * H)
        self.assertEqual(series["five_hour"], [(NOW - 6 * H, 2.0), (NOW - H, 3.0)])
        self.assertEqual(series["seven_day"], [(NOW - H, 30.0)])
        self.assertEqual(first, NOW - 10 * H)

    def test_points_are_sorted_and_bad_windows_skipped(self):
        samples = [sample(NOW - 1, five={"pct": 9}), sample(NOW - 3, five={"pct": 7}),
                   sample(NOW - 2, five={"pct": "high"}, seven="broken")]
        series, _, _ = P.series_for(samples, "alpha", NOW - H)
        self.assertEqual(series["five_hour"], [(NOW - 3, 7.0), (NOW - 1, 9.0)])
        self.assertEqual(series["seven_day"], [])

    def test_reset_times_are_deduplicated_to_the_minute(self):
        r = 1_700_010_000  # a whole minute
        samples = [sample(NOW - 3, five={"pct": 1, "reset_at": r + 12}), sample(NOW - 2, five={"pct": 2, "reset_at": r - 20}),
                   sample(NOW - 1, five={"pct": 3, "reset_at": r + 5 * H})]
        _, resets, _ = P.series_for(samples, "alpha", NOW - H)
        self.assertEqual(resets["five_hour"], {r, r + 5 * H})

    def test_samples_follow_the_account_directory(self):
        samples = [sample(NOW - 3, "2", five={"pct": 1}),  # old label-only sample of ~/.claude-2
                   sample(NOW - 2, "beta", five={"pct": 2}, dir="~/.claude-2"),
                   sample(NOW - 1, "beta", five={"pct": 3}, dir="~/.claude-other")]  # another account, same label
        series, _, _ = P.series_for(samples, "beta", NOW - H, acc_dir="~/.claude-2")
        self.assertEqual([v for _, v in series["five_hour"]], [1.0, 2.0])

    def test_other_accounts_do_not_count_as_history(self):
        _, _, first = P.series_for([sample(NOW - 5, "other", five={"pct": 1})], "alpha", NOW - H, acc_dir="~/.claude-alpha")
        self.assertIsNone(first)


class UsagePageTest(Env):
    def page(self, samples, range_name="24h"):
        pg = P.UsagePage(Model([{"label": "alpha", "dir": "~/.claude-alpha"}]), "alpha", range_name,
                         history=os.path.join(self.tmp, "none.jsonl"), now=lambda: NOW)
        pg.samples = samples
        return pg

    def render(self, pg, w=100, h=30):
        ui = FakeUI()
        cv = U.Canvas(w, h, "#ffffff", ui.pal["base"])
        pg.render(ui, cv, 0, 0, w, h)
        return ui, cv, ["".join(c[0] for c in row) for row in cv.cells]

    def view(self, pg):
        """What the page shows below the account line, by place and colour rather than wording:
        the note row (y+3) and its colour, the first row of the chart area (y+5), and whether a
        plot (deep background) is drawn."""
        ui, cv, rows = self.render(pg)
        note = rows[3].strip()
        note_fg = next((c[1] for c in cv.cells[3] if c[0] != " "), None)
        role = {ui.pal["amber"]: "amber", ui.pal["muted"]: "muted"}.get(note_fg)
        chart = any(c[2] == ui.pal["deep"] for row in cv.cells[5:] for c in row)
        return {"note": note, "note_role": role, "body": rows[5].strip(), "chart": chart}

    def test_history_starting_inside_the_range_is_noted(self):
        first = NOW - 3 * H
        samples = [sample(first, five={"pct": 10}), sample(NOW - H, five={"pct": 20})]
        for rng in ("6h", "24h", "7d"):
            with self.subTest(rng=rng):
                v = self.view(self.page(samples, rng))
                self.assertEqual(v["note_role"], "muted")
                self.assertIn(time.strftime("%H:%M", time.localtime(first)), v["note"])  # names when it starts
                self.assertTrue(v["chart"])

    def test_history_covering_the_range_is_not_noted(self):
        samples = [sample(NOW - 25 * H, five={"pct": 10}), sample(NOW - H, five={"pct": 20})]
        v = self.view(self.page(samples, "24h"))
        self.assertEqual(v["note"], "")
        self.assertTrue(v["chart"])

    def test_history_older_than_the_range_asks_for_a_longer_range(self):
        v = self.view(self.page([sample(NOW - 10 * H, five={"pct": 10})], "6h"))
        self.assertEqual(v["note"], "", "there is history, just not in this range")
        self.assertIn("6h", v["body"])
        self.assertFalse(v["chart"])

    def test_account_without_history(self):
        v = self.view(self.page([sample(NOW - H, "other", five={"pct": 10})]))
        self.assertEqual(v["note_role"], "amber")
        self.assertNotEqual(v["note"], "")
        self.assertEqual(v["body"], "", "no range hint: a longer range would not help")
        self.assertFalse(v["chart"])

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

    def test_unrecorded_span_is_named_inside_it_only(self):
        first = NOW - 12 * H  # half of the 24h range has no history
        pg = self.page([sample(first, five={"pct": 10}), sample(NOW - H, five={"pct": 20})])
        _, cv, rows = self.render(pg)
        hits = [(y, row.index("not recorded")) for y, row in enumerate(rows) if y >= 5 and "not recorded" in row]
        self.assertEqual(len(hits), 2, "once per window plot")
        gx, gw = 2 + 6, 100 - 4 - 6  # braille(): plot column and width at x + 2
        since_col = gx + round(12 * H / (24 * H) * (gw - 1))
        for y, x in hits:
            self.assertLessEqual(x + len("not recorded"), since_col, "never over the recorded span")
            self.assertEqual(cv.cells[y][x][2], cv.cells[y][gx][2], "drawn on the shaded background")

    def test_short_unrecorded_span_has_no_label(self):
        pg = self.page([sample(NOW - 23 * H, five={"pct": 10}), sample(NOW - H, five={"pct": 20})])
        self.assertFalse(any("not recorded" in row for row in self.render(pg)[2][5:]))

    def test_wide_layout_lists_every_account_beside_the_chart(self):
        pg = P.UsagePage(Model([{"label": "alpha", "dir": "~/.claude-alpha"}, {"label": "beta", "dir": "~/.claude-2"},
                                {"label": "gone", "dir": "~/.claude-gone", "hidden": True}]),
                         "alpha", "24h", history=os.path.join(self.tmp, "none.jsonl"), now=lambda: NOW)
        pg.samples = [sample(NOW - 30 * H, five={"pct": 10}), sample(NOW - H, five={"pct": 20})]
        ui, w = FakeUI(), 240
        cv = U.Canvas(w, 50, "#ffffff", ui.pal["base"])
        col = pg.render(ui, cv, 0, 0, w, 50)
        rows = ["".join(c[0] for c in row) for row in cv.cells]
        ux, uw = U.content_box(w, P.UsagePage.CHART_MAX)
        self.assertEqual(col, (ux - 2, uw + 4), "status and footer line up with the composition")
        sx = ux + uw - P.UsagePage.SIDE_W
        self.assertEqual(rows[0][sx + 2:sx + 9], "Account")
        self.assertEqual([rows[2][sx + 2:sx + 9].strip(), rows[3][sx + 2:sx + 9].strip()], ["alpha", "beta"])
        self.assertNotIn("gone", "".join(rows), "hidden accounts are not offered")
        self.assertNotIn("Account ", rows[0][:sx], "no account chips: the table picks")
        chart = [x for row in cv.cells[5:] for x, c in enumerate(row) if c[2] == ui.pal["deep"]]
        self.assertLess(max(chart), sx, "the chart ends before the table")
        cv.hit_at(sx + 4, 3)("left")
        self.assertEqual(pg.account, "beta")


class QuotaLinesTest(unittest.TestCase):
    def text(self, e, now=NOW):
        return ["".join(t for t, _, _ in line) for line in P.quota_lines(Pal(), e, now)]

    def test_windows_then_a_non_ok_state(self):
        e = {"five_hour": {"pct": 12.4, "reset_at": NOW + 3 * H + 600}, "seven_day": {"pct": 40, "reset_at": NOW - 5},
             "state": "rate_limited"}
        self.assertEqual(self.text(e), ["5h 12% resets in 3h10m", "7d 40% ", "rate limited"])

    def test_ok_state_and_missing_entry(self):
        self.assertEqual(self.text({"seven_day": {"pct": 1}, "state": "ok"}), ["7d 1% "])
        self.assertEqual(self.text(None), ["no data yet"])
        self.assertEqual("".join(t for t, _, _ in P.quota_segments(Pal(), {"five_hour": {"pct": 1}, "seven_day": {"pct": 2},
                                                                           "state": "ok"}, NOW)), "5h 1%     7d 2% ")


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
    HOME = "/home/alex"

    def panes(self, panes, fg=None):
        return P.parse_panes(json.dumps(panes), fg or {}, home=self.HOME)

    def test_tabs_and_windows_are_numbered_in_mux_order(self):
        rows = self.panes([{"pane_id": 5, "tab_id": 40, "window_id": 9}, {"pane_id": 6, "tab_id": 40, "window_id": 9},
                           {"pane_id": 7, "tab_id": 12, "window_id": 9}, {"pane_id": 8, "tab_id": 3, "window_id": 2}])
        self.assertEqual([(r["pane"], r["tab"], r["window"]) for r in rows], [(5, 1, 1), (6, 1, 1), (7, 2, 1), (8, 1, 2)])

    def test_cwd_from_url_shortened_under_home_only(self):
        rows = self.panes([{"pane_id": 1, "tty_name": "/dev/pts/1", "cwd": "file://box/home/alex/proj/"},
                           {"pane_id": 2, "tty_name": "/dev/pts/2", "cwd": "file://box/home/alex"},
                           {"pane_id": 3, "tty_name": "/dev/pts/3", "cwd": "file:///home/alexander/x"},
                           {"pane_id": 4, "tty_name": "/dev/pts/4", "cwd": "/"}])
        self.assertEqual([r["cwd"] for r in rows], ["~/proj", "~", "/home/alexander/x", "/"])

    def test_foreground_process_profile_and_kind(self):
        fg = {"pts/1": (10, "omp", 75, "omp --profile=alpha"), "pts/2": (11, "omp", 5, "omp --profile beta"),
              "pts/3": (12, "bash", 9, "-bash"), "pts/4": (13, "claude", 3, "claude --profile=x")}
        rows = self.panes([{"pane_id": i, "tty_name": f"/dev/pts/{i}"} for i in (1, 2, 3, 4, 5)], fg)
        self.assertEqual([(r["proc"], r["profile"], r["agent"], r["idle"]) for r in rows],
                         [("omp", "alpha", True, False), ("omp", "beta", True, False), ("bash", "", False, True),
                          ("claude", "", True, False), ("?", "", False, False)])
        self.assertEqual(rows[0]["age"], 75)

    def test_hivegrid_chrome_panes_are_left_out(self):
        fg = {"pts/1": (1, "python3", 1, "python3 /home/alex/.local/bin/hivegrid-bar"), "pts/2": (2, "yazi", 1, "yazi"),
              "pts/3": (3, "omp", 1, "omp")}
        rows = self.panes([{"pane_id": i, "tty_name": f"/dev/pts/{i}"} for i in (1, 2, 3)], fg)
        self.assertEqual([r["pane"] for r in rows], [3])

    def test_a_web_pane_is_a_row_of_its_own_kind_with_the_page_as_directory(self):
        fg = {"pts/1": (1, "omp", 4, "omp")}
        rows = self.panes([
            {"pane_id": 1, "tty_name": "/dev/pts/1", "cwd": "file://box/home/alex/proj/", "tab_id": 4, "window_id": 1},
            {"pane_id": 2, "tty_name": None, "cwd": "https://docs.example.com/guide/intro", "title": "Guide · Example",
             "tab_id": 4, "window_id": 1, "is_active": True},
            {"pane_id": 3, "tty_name": None, "cwd": "https://example.org/", "title": "Example", "tab_id": 4, "window_id": 1},
        ], fg)
        self.assertEqual([r["pane"] for r in rows], [1, 2, 3])
        web = rows[1]
        self.assertEqual((web["proc"], web["cwd"], web["title"], web["agent"], web["idle"], web["profile"], web["age"]),
                         ("web", "docs.example.com/guide/intro", "Guide · Example", False, False, "", 0))
        self.assertEqual((web["tab"], web["window"], web["active"]), (1, 1, True))
        self.assertEqual(rows[2]["cwd"], "example.org", "a bare / is not shown")
        self.assertEqual(rows[0]["cwd"], "~/proj", "terminals are unchanged")

    def test_a_local_page_is_web_by_the_fork_marker_not_by_a_missing_tty(self):
        rows = self.panes([
            {"pane_id": 1, "tty_name": None, "cwd": "file:///home/alex/slides.html", "title": "Slides", "is_web": True},
            {"pane_id": 2, "tty_name": None, "cwd": "file://remote-box/home/alex/proj", "title": "omp"},
            {"pane_id": 3},
        ])
        self.assertEqual([(r["proc"], r["cwd"]) for r in rows],
                         [("web", "file:///home/alex/slides.html"), ("?", "~/proj"), ("?", "")],
                         "an SSH or mux-client pane (no tty, file://host cwd) is no web pane")

    def test_page_controlled_text_loses_its_control_characters(self):
        evil = "T\x1b]52;c;QUJD\x07\x1b]1337;SetUserVar=hivegrid_capture=MQ==\x07\u2028end"
        rows = self.panes([{"pane_id": 2, "tty_name": None, "cwd": "https://a.test/\x1b[2Jx", "title": evil,
                            "is_web": True}])
        for key in ("title", "cwd"):
            self.assertFalse([c for c in rows[0][key] if ord(c) < 0x20 or c in "\u2028\u2029"], key)
        self.assertEqual(rows[0]["title"], "T]52;c;QUJD]1337;SetUserVar=hivegrid_capture=MQ==end")

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

    def sampler(self, stats, log=None):
        s = P.Sampler(log=log or os.path.join(tempfile.gettempdir(), "no-such-hivegrid-sysstats.jsonl"))
        s.gpu_kind = None  # never run nvidia-smi
        files = {"/proc/stat": iter(stats), "/proc/meminfo": None}
        s.read = lambda path: next(files[path]) if path == "/proc/stat" else "MemTotal: 100 kB\nMemAvailable: 40 kB\n"
        return s

    def tick_at(self, s, t):
        with mock.patch.object(P.time, "time", return_value=t):
            return s.tick()

    def log_file(self, lines):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "sysstats.jsonl")
        with open(path, "w") as f:
            f.writelines(json.dumps(line) + "\n" for line in lines)
        return path

    def test_long_range_comes_from_the_log_without_the_panel_having_sampled(self):
        now = 100_000.0
        path = self.log_file([
            {"ts": now - 3600, "cpu": 10.0, "ram": 50.0},
            {"ts": now - 1000, "cpu": 20.0},
            {"ts": now - 1003, "cpu": 40.0},  # same 6 s slot as the line above: their mean
            {"ts": now - 15, "cpu": 55.0, "gpu": 7.0},
            {"ts": now, "cpu": 70.0},
            {"ts": now - 8000, "cpu": 99.0},  # before the range
            {"ts": now - 500, "cpu": "bad"},
        ])
        s = self.sampler([], log=path)
        s.refresh_log(now)
        v = s.values("CPU", 3600, now=now)
        self.assertEqual(len(v), 601)
        self.assertEqual((v[0], v[-1]), (10.0, 70.0))
        self.assertAlmostEqual(v[600 - round(1000 / 6)], 30.0)
        self.assertIsNone(v[300], "no line for minutes: a gap, not the value held across it")
        self.assertEqual(s.values("RAM", 3600, now=now)[0], 50.0)
        gpu = s.values("GPU", 3600, now=now)
        self.assertEqual({x for x in gpu if x is not None}, {7.0})
        self.assertIsNone(gpu[0])

    def test_short_range_shows_the_log_until_the_panels_own_samples_replace_it(self):
        path = self.log_file([{"ts": 1000.0, "cpu": 30.0}, {"ts": 1015.0, "cpu": 31.0}])
        s = self.sampler(["cpu 0 0 0 0", "cpu 100 0 0 100 0 0 0 0"], log=path)
        self.tick_at(s, 1020.0)  # no CPU rate yet: the log's last value stands
        v = s.values("CPU", 60, now=1020.0)
        self.assertEqual((v[40], v[45], v[55], v[60]), (30.0, 30.0, 31.0, 31.0))
        self.tick_at(s, 1021.0)
        v = s.values("CPU", 60, now=1021.0)
        self.assertAlmostEqual(v[60], 50.0)
        self.assertEqual(v[54], 31.0, "older slots keep the log's value")

    def test_log_is_read_incrementally_and_again_after_rotation(self):
        path = self.log_file([{"ts": 1000.0, "cpu": 1.0}])
        s = self.sampler([], log=path)
        s.refresh_log(2000.0)
        self.assertEqual([r[0] for r in s.log], [1000.0])
        with open(path, "a") as f:
            f.write(json.dumps({"ts": 1015.0, "cpu": 2.0}) + "\n" + '{"ts": 103')  # a line mid-write
        s.refresh_log(2001.0)
        self.assertEqual([r[0] for r in s.log], [1000.0], "at most one look every 5 s")
        s.refresh_log(2006.0)
        self.assertEqual([r[0] for r in s.log], [1000.0, 1015.0], "the partial line waits")
        os.replace(path, path + ".1")  # rotation: the file the sampler follows moves to `.1`
        with open(path, "w") as f:
            f.write(json.dumps({"ts": 1030.0, "cpu": 3.0}) + "\n")
        s.refresh_log(2012.0)
        self.assertEqual([r[0] for r in s.log], [1000.0, 1015.0, 1030.0])

    def test_missing_log_is_no_error(self):
        s = self.sampler([])
        s.refresh_log(5000.0)
        self.assertEqual(s.values("CPU", 3600, now=5000.0), [None] * 601)

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


# ---- settings: one column, or section list + content (wide) ----------------------------


class SettingsLayoutTest(Env):
    ABOUT = {"executable": "/x/wezterm", "version": "wezterm test", "clicks": True, "overlay": True, "custom": True}

    def setUp(self):
        super().setUp()
        import hivegrid_settings_core as S
        self.model = S.Model(S.Settings(os.path.join(self.tmp, "settings.json")))
        self.page = P.SettingsPage(self.model)
        self.page.about = self.ABOUT

    def draw(self, w, h=50, sub=0):
        self.page.sub = sub
        ui = U.UI(self.model, [self.page])
        ui.size, ui.out = (w, h), io.StringIO()
        ui.draw()
        return ui, ["".join(c[0] for c in row) for row in ui.cv.cells]

    def box(self, w):
        """(x, width) of the wide composition: section list, separator, content."""
        return U.content_box(w, self.page.SUB_W + 5 + self.page.content_w(w))

    def test_narrow_keeps_the_section_strip(self):
        _, rows = self.draw(U.WIDE_COLS - 1)
        self.assertFalse(self.page.two_pane)
        self.assertRegex(rows[2], r"^\s+Theme\s+Keys\s+Accounts\s+Web\s+Agent\s+About\s*$")

    def test_theme_page_keeps_the_accent_row_with_every_preset_at_120x30(self):
        _, rows = self.draw(120, 30, sub=0)
        text = "\n".join(rows)
        self.assertIn("Custom", text)
        self.assertIn("Edit…", text)

    def test_wide_content_stays_in_its_column_for_every_section(self):
        for w in (U.WIDE_COLS, 240, 320):
            lx, tw = self.box(w)
            sep = lx + self.page.SUB_W + 1
            for sub in range(len(self.page.SUBS)):
                with self.subTest(w=w, section=self.page.SUBS[sub]):
                    _, rows = self.draw(w, sub=sub)
                    self.assertTrue(self.page.two_pane)
                    for y, row in enumerate(rows[2:-2], 2):
                        used = [x for x, ch in enumerate(row) if ch != " "]
                        if used:
                            self.assertGreaterEqual(min(used), lx, f"row {y} left of the composition")
                            self.assertLess(max(used), lx + tw, f"row {y} right of the composition")
                        self.assertEqual(row[sep], "│", f"row {y}: the separator is not overdrawn")
                    self.assertEqual(rows[-1].index("↑↓"), lx, "the footer lines up")

    def test_about_arrows_walk_the_sections(self):
        about = self.page.SUBS.index("About")
        self.page.sub = about
        self.page.key(None, set(), "UpArrow")
        self.assertEqual(self.page.SUBS[self.page.sub], "Agent")
        self.page.sub = about
        self.page.key(None, set(), "DownArrow")
        self.assertEqual(self.page.SUBS[self.page.sub], "About", "stays on the last section")

    def test_section_list_shows_unsaved_sections_and_switches_on_click(self):
        w = 240
        lx, _ = self.box(w)
        ui, rows = self.draw(w)
        keys_row = next(y for y, r in enumerate(rows) if r[lx + 2:lx + 6] == "Keys")
        self.assertNotIn("●", rows[keys_row])
        self.model.keys["equalize"] = "CTRL+ALT+e"
        ui, rows = self.draw(w)
        self.assertIn("● 1 changed", rows[keys_row])
        theme_row = rows[keys_row - 1]
        self.assertNotIn("●", theme_row, "only the edited section is marked")
        ui.cv.hit_at(lx + 3, keys_row + 1)("left")
        self.assertEqual(self.page.SUBS[self.page.sub], "Accounts")
        self.model.keys["equalize"] = "CTRL+SHIFT+e"  # undoing the edit clears the mark
        self.assertNotIn("●", self.draw(w)[1][keys_row])

    def test_wide_keys_detail_follows_the_cursor_and_actions_move_there(self):
        w = 240
        names = list(P.BINDINGS)
        self.page.cursor[1] = names.index("overview_toggle")
        ui, rows = self.draw(w, sub=1)
        self.assertTrue(any(r.rstrip().endswith("Overview dock: toggle") for r in rows[2:4]), "detail title")
        self.assertEqual(sum(" Change " in r for r in rows), 1, "one Change button, in the detail")
        x = next(r.index(" Change ") for r in rows if " Change " in r)
        y = next(i for i, r in enumerate(rows) if " Change " in r)
        modal = []
        self.page.change_binding = lambda ui_, n: modal.append(n)
        ui.cv.hit_at(x + 2, y)("left")
        self.assertEqual(modal, ["overview_toggle"])


class AgentSettingsTest(Env):
    """The Agent section: omp or Claude Code for new tabs, grids and the account pills, saved as
    settings.json "agent"."""

    def setUp(self):
        super().setUp()
        import hivegrid_settings_core as S
        self.path = os.path.join(self.tmp, "settings.json")
        self.model = S.Model(S.Settings(self.path))
        self.page = P.SettingsPage(self.model)
        self.page.about = SettingsLayoutTest.ABOUT
        self.page.sub = self.page.SUBS.index("Agent")
        self.ui = mock.Mock()

    def draw(self, w=120, h=40):
        ui = U.UI(self.model, [self.page])
        ui.size, ui.out = (w, h), io.StringIO()
        ui.draw()
        return ui, ["".join(c[0] for c in row) for row in ui.cv.cells]

    def test_omp_is_the_default_and_the_cursor_starts_on_the_saved_choice(self):
        self.assertEqual(self.model.agent, "omp")
        self.assertFalse(self.model.dirty)
        self.assertEqual(self.page.section_meta(self.page.sub), ("omp", "dim"))
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"agent": "claude"}, f)
        import hivegrid_settings_core as S
        page = P.SettingsPage(S.Model(S.Settings(self.path)))
        self.assertEqual(page.cursor[4], 1, "Claude Code is selected when it is the saved choice")

    def test_enter_chooses_the_selected_agent_and_r_goes_back_to_omp(self):
        self.page.key(self.ui, set(), "DownArrow")
        self.page.key(self.ui, set(), "Enter")
        self.assertEqual(self.model.agent, "claude")
        self.assertTrue(self.model.dirty)
        self.assertEqual(self.page.section_meta(self.page.sub), ("● Claude Code", "amber"))
        self.page.key(self.ui, set(), "r")
        self.assertEqual(self.model.agent, "omp")
        self.assertFalse(self.model.dirty, "choosing the saved agent again is no change")

    def test_the_arrows_stay_inside_the_two_rows(self):
        for _ in range(3):
            self.page.key(self.ui, set(), "DownArrow")
        self.assertEqual(self.page.cursor[4], 1)
        for _ in range(3):
            self.page.key(self.ui, set(), "UpArrow")
        self.assertEqual(self.page.cursor[4], 0)

    def test_a_click_on_use_chooses_that_agent_and_the_current_one_is_marked(self):
        ui, rows = self.draw()
        self.assertEqual(sum("● in use" in r for r in rows), 1)
        use = next(i for i, r in enumerate(rows) if " Use " in r)
        self.assertIn("Claude Code", rows[use])
        ui.cv.hit_at(rows[use].index(" Use ") + 2, use)("left")
        self.assertEqual(self.model.agent, "claude")
        _, rows = self.draw()
        in_use = next(r for r in rows if "● in use" in r)
        self.assertIn("Claude Code", in_use)

    def test_save_writes_the_choice_and_omp_leaves_the_file_without_it(self):
        self.page.key(self.ui, set(), "DownArrow")
        self.page.key(self.ui, set(), "Enter")
        self.assertTrue(self.model.save()[0])
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["agent"], "claude")
        self.page.key(self.ui, set(), "r")
        self.assertTrue(self.model.save()[0])
        with open(self.path, encoding="utf-8") as f:
            self.assertNotIn("agent", json.load(f))



class WebSettingsTest(Env):
    """The Web section: links target, browser, home page, search engine and default profile, saved as
    settings.json "web"."""

    def setUp(self):
        super().setUp()
        import hivegrid_settings_core as S
        self.S = S
        self.path = os.path.join(self.tmp, "settings.json")
        self.model = S.Model(S.Settings(self.path))
        self.page = P.SettingsPage(self.model)
        self.page.about = SettingsLayoutTest.ABOUT
        self.page.sub = self.page.SUBS.index("Web")
        self.page.web_built = True
        self.page.browsers = ([], [])  # not what this machine has installed
        self.ui = mock.Mock()

    def modal_for(self, row):
        """Moves the cursor to `row`, presses Enter and returns the prompt that opened."""
        self.page.cursor[3] = P.WEB_ROWS.index(row)
        self.ui.open_modal.reset_mock()
        self.page.key(self.ui, set(), "Enter")
        return self.ui.open_modal.call_args[0][0]

    def draw(self, w, h=50):
        ui = U.UI(self.model, [self.page])
        ui.size, ui.out = (w, h), io.StringIO()
        ui.draw()
        return ["".join(c[0] for c in row) for row in ui.cv.cells]

    def test_defaults_and_the_section_summary(self):
        self.assertEqual(self.model.web, self.S.WEB_DEFAULTS)
        self.assertEqual((self.model.web["browser"], self.model.web["home"]), ("auto", "about:blank"))
        self.assertFalse(self.model.dirty)
        self.assertEqual(self.page.section_meta(self.page.sub), ("pane · main", "dim"))

    def test_enter_on_links_switches_between_pane_and_system(self):
        self.page.cursor[3] = 0
        self.page.key(self.ui, set(), "Enter")
        self.assertEqual(self.model.web["links"], "system")
        self.assertTrue(self.model.dirty)
        self.assertEqual(self.page.section_meta(self.page.sub), ("● system · main", "amber"))
        self.page.key(self.ui, set(), "Space")
        self.assertEqual(self.model.web["links"], "pane")
        self.assertFalse(self.model.dirty, "switching back is no change")

    def test_profile_prompt_validates_the_name(self):
        modal = self.modal_for("profile")
        for bad in ("", "../evil", "a b", "-x", "x" * 33):
            with self.subTest(name=bad):
                self.assertTrue(modal.on_ok(bad), "an error text")
                self.assertEqual(self.model.web["profile"], "main")
        self.assertIsNone(modal.on_ok(" work-2 "))
        self.assertEqual(self.model.web["profile"], "work-2")

    def test_search_prompt_needs_a_url_template_with_the_placeholder(self):
        modal = self.modal_for("search")
        for bad in ("duck.example/?q=%s", "https://duck.example/?q=", "ftp://x/%s"):
            with self.subTest(template=bad):
                self.assertTrue(modal.on_ok(bad))
        self.assertEqual(self.model.web["search"], self.S.WEB_DEFAULTS["search"])
        self.assertIsNone(modal.on_ok("https://duck.example/?q=%s"))
        self.assertEqual(self.model.web["search"], "https://duck.example/?q=%s")
        self.assertIsNone(modal.on_ok(""), "empty turns the search off")
        self.assertEqual(self.model.web["search"], "")

    def test_home_page_prompt_takes_an_address_not_a_search(self):
        modal = self.modal_for("home")
        for bad in ("", "javascript:alert(1)", "chrome://settings", "two words"):
            with self.subTest(home=bad):
                self.assertTrue(modal.on_ok(bad), "an error text")
        self.assertEqual(self.model.web["home"], "about:blank")
        self.assertIsNone(modal.on_ok(" duck.example/start "))
        self.assertEqual(self.model.web["home"], "https://duck.example/start", "stored as the address it stands for")
        self.assertIsNone(modal.on_ok("http://localhost:3000"))
        self.assertEqual(self.model.web["home"], "http://localhost:3000")

    def installed(self, found, skipped=()):
        self.page.browsers = (list(found), list(skipped))

    def choose(self, index):
        """Opens the browser list and picks entry `index`; returns the list's options."""
        modal = self.modal_for("browser")
        self.assertIsInstance(modal, U.ChoiceModal)
        modal.on_pick(index)
        return modal.options

    def test_the_browser_list_is_auto_the_detected_browsers_and_a_custom_path(self):
        self.installed([("Google Chrome", "/opt/google/chrome/chrome"), ("Brave", "/usr/bin/brave-browser")])
        options = self.choose(2)
        self.assertEqual(len(options), 4)
        self.assertTrue(options[0].startswith("Auto"))
        self.assertIn("/usr/bin/brave-browser", options[2])
        self.assertTrue(options[3].startswith("Custom"))
        self.assertEqual(self.model.web["browser"], "/usr/bin/brave-browser")
        self.assertTrue(self.model.dirty)
        self.choose(1)
        self.assertEqual(self.model.web["browser"], "/opt/google/chrome/chrome")
        self.choose(0)
        self.assertEqual(self.model.web["browser"], "auto")
        self.assertFalse(self.model.dirty, "back to auto is no change")

    def test_no_browser_is_listed_that_cannot_work_and_the_panel_says_why(self):
        self.installed([("Chromium", "/usr/bin/chromium")], [("/snap/bin/brave", "confined")])
        options = self.choose(0)
        self.assertEqual(len(options), 3, "auto, chromium, custom: the snap is not offered")
        text = "\n".join(self.draw(140))
        self.assertIn("Firefox", text, "the panel says why Firefox is missing")
        self.assertIn("Snap and Flatpak", text)
        self.assertIn("brave", text, "and which one was skipped here")

    def test_the_list_opens_on_the_current_choice(self):
        self.installed([("Google Chrome", "/opt/google/chrome/chrome"), ("Vivaldi", "/usr/bin/vivaldi")])
        self.model.web["browser"] = "/usr/bin/vivaldi"
        self.assertEqual(self.modal_for("browser").idx, 2)
        self.model.web["browser"] = "/home/u/my-chrome"
        self.assertEqual(self.modal_for("browser").idx, 3, "a custom path sits on Custom")
        self.model.web["browser"] = "auto"
        self.assertEqual(self.modal_for("browser").idx, 0)

    def test_custom_path_is_validated_before_it_is_taken(self):
        import stat
        self.installed([])
        self.choose(1)  # auto, custom
        modal = self.ui.open_modal.call_args[0][0]
        self.assertIsInstance(modal, U.PromptModal)
        plain = os.path.join(self.tmp, "plain")
        with open(plain, "w") as f:
            f.write("x")
        for bad in ("", "chromium", os.path.join(self.tmp, "missing"), self.tmp, plain):
            with self.subTest(path=bad):
                self.assertTrue(modal.on_ok(bad), "an error text")
        self.assertEqual(self.model.web["browser"], "auto")
        good = os.path.join(self.tmp, "my-chrome")
        with open(good, "w") as f:
            f.write("#!/bin/sh\necho 'Chromium 120.0'\n")
        os.chmod(good, os.stat(good).st_mode | stat.S_IXUSR)
        self.assertIsNone(modal.on_ok(good))
        self.assertEqual(self.model.web["browser"], good)
        self.assertEqual(self.page.web_value("browser"), "Custom  " + P.tilde(good))

    def test_reset_of_the_browser_row_goes_back_to_auto(self):
        self.model.web["browser"] = "/usr/bin/chromium"
        self.page.cursor[3] = P.WEB_ROWS.index("browser")
        self.page.key(self.ui, set(), "r")
        self.assertEqual(self.model.web["browser"], "auto")


    def test_arrows_move_the_cursor_and_r_resets_a_row(self):
        for _ in P.WEB_ROWS:
            self.page.key(self.ui, set(), "DownArrow")
        self.assertEqual(self.page.cursor[3], len(P.WEB_ROWS) - 1, "stops at the last row")
        self.assertEqual(P.WEB_ROWS[-1], "profile")
        self.model.web["profile"] = "work"
        self.page.key(self.ui, set(), "r")
        self.assertEqual(self.model.web["profile"], "main")
        self.page.key(self.ui, set(), "UpArrow")
        self.assertEqual(self.page.cursor[3], len(P.WEB_ROWS) - 2)
        self.page.wheel(self.ui, -50)
        self.assertEqual(self.page.cursor[3], 0)

    def test_save_writes_only_non_default_values_and_keeps_foreign_entries(self):
        with open(self.path, "w") as f:
            json.dump({"web": {"future": 1}}, f)
        model = self.S.Model(self.S.Settings(self.path))
        model.web.update(links="system", profile="work")
        ok, _ = model.save()
        self.assertTrue(ok)
        with open(self.path) as f:
            self.assertEqual(json.load(f)["web"], {"future": 1, "links": "system", "profile": "work"})
        self.assertEqual(self.S.Settings(self.path).web(), dict(self.S.WEB_DEFAULTS, links="system", profile="work"))
        model.web.update(links="pane", profile="main")
        model.save()
        with open(self.path) as f:
            self.assertEqual(json.load(f)["web"], {"future": 1})
        os.unlink(self.path)
        model = self.S.Model(self.S.Settings(self.path))
        model.save()
        with open(self.path) as f:
            self.assertNotIn("web", json.load(f), "all defaults: no web entry")

    def test_the_section_draws_in_both_layouts(self):
        self.model.web.update(links="system", search="https://duck.example/?q=%s")
        for w in (100, 240):
            with self.subTest(w=w):
                text = "\n".join(self.draw(w))
                for want in ("https://duck.example/?q=%s", "main"):
                    self.assertIn(want, text, "the values are drawn")
                self.assertEqual(self.page.SUBS[self.page.sub], "Web")

    def test_a_click_on_a_value_edits_it(self):
        rows = self.draw(100)
        y = next(i for i, r in enumerate(rows) if P.WEB_LABELS["profile"] in r)
        ui = U.UI(self.model, [self.page])
        ui.size, ui.out = (100, 50), io.StringIO()
        ui.draw()
        opened = []
        ui.open_modal = opened.append
        ui.cv.hit_at(rows[y].index("main"), y)("left")
        self.assertEqual(len(opened), 1)
        self.assertIsInstance(opened[0], U.PromptModal)
        self.assertEqual(opened[0].text, "main", "it edits the profile")

    def test_a_wezterm_without_the_web_pane_is_said_so_beside_the_settings(self):
        self.page.web_built = False
        without = "\n".join(self.draw(120))
        self.page.web_built = True
        with_web = "\n".join(self.draw(120))
        extra = [line for line in without.splitlines() if line not in with_web.splitlines()]
        self.assertEqual(len(extra), 1, "one more line when the build lacks the web pane")
        self.assertEqual(self.page.cursor[3], 0, "the settings stay editable")

    def test_the_build_is_looked_up_once_when_the_section_draws(self):
        page = P.SettingsPage(self.model)
        page.sub = page.SUBS.index("Web")
        calls = []
        with mock.patch.object(P, "web_build_cached", lambda: calls.append(1) or True):
            ui = U.UI(self.model, [page])
            ui.size, ui.out = (120, 50), io.StringIO()
            ui.draw()
            ui.draw()
        self.assertEqual(calls, [1])

    def test_a_scan_still_running_shows_no_warning_and_is_asked_again(self):
        page = P.SettingsPage(self.model)
        page.sub = page.SUBS.index("Web")
        answers = iter([None, None, False])
        with mock.patch.object(P, "web_build_cached", lambda: next(answers)):
            ui = U.UI(self.model, [page])
            ui.size, ui.out = (120, 50), io.StringIO()
            ui.draw()
            self.assertIsNone(page.web_built)
            ui.draw()
            ui.draw()
            self.assertIs(page.web_built, False)


class FrameTest(Env):
    class Col(U.Page):
        title = "Col"

        def render(self, ui, cv, x, y, w, h):
            return 40, 60

        def hints(self):
            return "a one  b two"

    def test_status_and_footer_line_up_with_the_page_column(self):
        model = mock.Mock(dirty=False)
        model.current_theme.return_value = {"palette": U.DEFAULT_PAL}
        ui = U.UI(model, [self.Col()])
        ui.size, ui.out = (200, 20), io.StringIO()
        ui.say("saved")
        ui.draw()
        rows = ["".join(c[0] for c in row) for row in ui.cv.cells]
        self.assertEqual(rows[-2].index("saved"), 42)
        self.assertEqual(rows[-1].index("a one"), 42)
        self.assertTrue(ui.out.getvalue().startswith("\x1b[?2026h") and ui.out.getvalue().endswith("\x1b[?2026l"))


if __name__ == "__main__":
    unittest.main()
