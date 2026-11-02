"""The overview dock's bots section: with_schedules turns hivegrid_schedule's runtime summary into the
document's "bots" key, render draws it below the agent rows in the rows they leave free (never moving
an agent row), and a click on a row asks the config (user var hivegrid_overview_bots)."""
import base64
import importlib.util
import os
import sys
import unittest
from importlib.machinery import SourceFileLoader

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin")
sys.dont_write_bytecode = True
sys.path.insert(0, BIN)
import hivegrid_ui as U  # noqa: E402


def load(name):
    spec = importlib.util.spec_from_loader(name, SourceFileLoader(name, os.path.join(BIN, name)))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


D = load("hivegrid-overview")
NOW = 1_700_000_000.0
WIDTH = 40
TITLE_ROWS = 2  # the title row and its rule: agent rows start here (no usage line in these fixtures)

# Dock heights, assuming 8x17 px cells (font 10.5): a window of H pixels has H // 17 rows, the header
# bar and the window padding take 2, so the dock pane is that high.
SIZES = {"1280x720": 720 // 17 - 2, "1920x1080": 1080 // 17 - 2, "2560x1440": 1440 // 17 - 2}


def panes(n):
    return [{"id": i + 1, "num": i + 1, "kind": "idle", "name": f"proj{i}", "title": f"task {i}",
             "since": NOW - 60, "active": i == 0} for i in range(n)]


def item(sid, state="schedule", at=None, enabled=True, running=False, name=None):
    return {"id": sid, "name": name or f"Bot {sid}", "kind": "agent", "enabled": enabled, "when_text": "daily",
            "next_at": at, "state": state, "last": None, "running": running, "attention": False,
            "paused_reason": None}


def summary(items, beat=NOW - 5):
    return {"ts": NOW, "scheduler": {"heartbeat": beat}, "counts": {}, "items": items}


FIXTURE = summary([
    item("run-one", "working", running=True), item("run-two", "working", running=True),
    item("late", at=NOW + 90000), item("soon", at=NOW + 7300), item("mid", at=NOW + 600),
    item("later", at=NOW + 40000), item("off", "idle", enabled=False, at=NOW + 10),
    item("bad id!", at=NOW + 1),
])


def doc_for(n, summ=FIXTURE):
    return D.with_schedules({"panes": panes(n)}, summ, NOW)


def draw(doc, h, w=WIDTH, **kw):
    return D.render(doc, w, h, NOW, lambda i: 0, lambda: 0, lambda: 0, **kw)


class WithSchedules(unittest.TestCase):
    def test_shape(self):
        bots = doc_for(2)["bots"]
        self.assertEqual(bots["running"], [{"id": "run-one", "name": "Bot run-one"},
                                          {"id": "run-two", "name": "Bot run-two"}])
        self.assertEqual([b["id"] for b in bots["next"]], ["mid", "soon", "later"])
        self.assertEqual(bots["next"][0], {"id": "mid", "name": "Bot mid", "next_at": NOW + 600})
        self.assertEqual(bots["more"], 1)  # "late"; disabled and invalid ids are not counted

    def test_unchanged_without_a_live_scheduler_or_work(self):
        base = {"panes": panes(2)}
        for summ in (None, "x", summary(FIXTURE["items"], beat=NOW - 61), summary(FIXTURE["items"], beat=None),
                     summary([]), summary([item("off", "idle", enabled=False)]),
                     {"items": FIXTURE["items"]}):
            out = D.with_schedules(base, summ, NOW)
            self.assertNotIn("bots", out, summ)
            self.assertEqual(out, base)
        self.assertIsNone(D.with_schedules(None, FIXTURE, NOW))

    def test_heartbeat_edge_and_input_untouched(self):
        base = {"panes": panes(1)}
        self.assertIn("bots", D.with_schedules(base, summary(FIXTURE["items"], beat=NOW - 60), NOW))
        self.assertNotIn("bots", base)

    def test_only_running(self):
        bots = D.with_schedules({"panes": []}, summary([item("a1x", "working", running=True)]), NOW)["bots"]
        self.assertEqual((len(bots["running"]), bots["next"], bots["more"]), (1, [], 0))


def brief(unseen, *entries):
    return {"unseen": unseen, "newest": list(entries)}


def entry(bot, headline, seen=False, name=None):
    return {"bot_id": bot, "bot_name": name or f"Bot {bot}", "run": 3, "level": "important", "status": "ok",
            "headline": headline, "ended": NOW - 60, "seen": seen}


class Briefing(unittest.TestCase):
    def doc(self, block, items=None):
        return D.with_schedules({"panes": panes(1)}, dict(summary(FIXTURE["items"] if items is None else items), briefing=block), NOW)

    def test_the_newest_unseen_headline_speaks_for_the_briefing(self):
        got = self.doc(brief(2, entry("ci-bot", "CI of repo x fixed: flaky timeout"), entry("old-one", "older")))["bots"]["brief"]
        self.assertEqual(got, {"unseen": 2, "id": "ci-bot", "name": "Bot ci-bot", "headline": "CI of repo x fixed: flaky timeout"})
        skip = self.doc(brief(1, entry("seen-one", "read", seen=True), entry("next-up", "fresh")))["bots"]["brief"]
        self.assertEqual((skip["id"], skip["headline"]), ("next-up", "fresh"))

    def test_nothing_unseen_or_a_bad_block_adds_nothing(self):
        for block in (None, "x", brief(0, entry("a1x", "seen", seen=True)), {"unseen": True}, {"unseen": -1}, {}):
            self.assertNotIn("brief", self.doc(block)["bots"], block)

    def test_an_unseen_report_alone_makes_the_section(self):
        out = self.doc(brief(1, entry("ci-bot", "Fixed")), items=[item("off", "idle", enabled=False)])
        self.assertEqual(out["bots"]["brief"]["id"], "ci-bot")
        self.assertEqual((out["bots"]["running"], out["bots"]["next"]), ([], []))
        self.assertNotIn("bots", self.doc(brief(0), items=[item("off", "idle", enabled=False)]))

    def test_entries_with_a_bad_bot_id_are_not_clickable(self):
        got = self.doc(brief(1, entry("Bad Id!", "x")))["bots"]["brief"]
        self.assertEqual((got["unseen"], got["id"]), (1, None))

    def test_the_section_leads_with_the_headline_and_the_count_on_its_title(self):
        doc = self.doc(brief(2, entry("ci-bot", "CI of repo x fixed")))
        rows = D.dump(draw(doc, 20))
        at = rows.index(next(r for r in rows if r.strip().startswith("Bots")))
        self.assertTrue(rows[at].endswith("● 2 +3"), rows[at])
        self.assertIn("CI of repo x fixed", rows[at + 1])
        self.assertTrue(rows[at + 1].lstrip().startswith("●"))
        self.assertIn("run-one", rows[at + 2])

    def test_a_click_on_the_headline_asks_for_that_bot(self):
        asked = []
        doc = self.doc(brief(1, entry("ci-bot", "Fixed")))
        cv = D.render(doc, WIDTH, 20, NOW, lambda i: 0, lambda: 0, lambda: 0, bot=asked.append)
        rows = D.dump(cv)
        y = next(i for i, r in enumerate(rows) if "Fixed" in r)
        cv.hit_at(5, y)("left")
        self.assertEqual(asked, ["ci-bot"])


class WhenText(unittest.TestCase):
    def test_relative(self):
        self.assertEqual(D.when_text(None, NOW), "")
        self.assertEqual(D.when_text(NOW + 10, NOW), "now")
        self.assertEqual(D.when_text(NOW + 600, NOW), "in 10m")
        self.assertEqual(D.when_text(NOW + 7300, NOW), "in 2h")

    def test_far_is_a_clock_time(self):
        import time
        self.assertEqual(D.when_text(NOW + 10 * 3600, NOW)[-5:], time.strftime("%H:%M", time.localtime(NOW + 10 * 3600)))


class BotsLines(unittest.TestCase):
    def test_header_then_rows_drop(self):
        bots = doc_for(1)["bots"]
        self.assertEqual([D.bots_lines(bots, f)[0] for f in (0, 1, 4, 5)], [False] * 3 + [True])
        self.assertEqual([len(D.bots_lines(bots, f)[1]) for f in (0, 1, 3, 4, 5, 9)], [0, 1, 3, 4, 4, 4])
        self.assertEqual(D.bots_lines(None, 9), (False, [], 0))


class Render(unittest.TestCase):
    def text(self, doc, h, **kw):
        return D.dump(draw(doc, h, **kw))

    def test_section_below_agents(self):
        rows = self.text(doc_for(2), 20)
        at = rows.index(next(r for r in rows if r.strip().startswith("Bots")))
        self.assertEqual(at, TITLE_ROWS + 2 * 3)  # right under two 3-row agents
        self.assertIn("run-one", rows[at + 1])
        self.assertIn(U.SPINNER[int(NOW * 8) % len(U.SPINNER)], rows[at + 1])
        self.assertIn("Bot run-two", rows[at + 2])
        self.assertTrue(rows[at + 3].lstrip().startswith("◷") and rows[at + 3].endswith("in 10m"))
        self.assertTrue(rows[at + 4].endswith("in 2h"))
        self.assertTrue(rows[at].endswith("+2"))  # 3 waiting + 1 more were cut to 4 rows: mid... hidden count
        self.assertFalse(any("Bot soon" in r or "Bot later" in r for r in rows[at + 5:]))
        self.assertEqual(rows[at + 5].strip(), "")

    def test_colours_and_marks(self):
        cv = draw(doc_for(1), 20)
        pal = U.DEFAULT_PAL
        y = TITLE_ROWS + 3  # the header; run rows follow, waiting rows after them
        self.assertEqual(cv.cells[y][2][1], pal["muted"])  # the title
        self.assertEqual(cv.cells[y + 3][2][0], "◷")
        self.assertEqual(cv.cells[y + 3][2][1], pal[U.STATES["schedule"].color])
        self.assertEqual(cv.cells[y + 1][2][1], pal[U.STATES["working"].color])

    def test_hidden_when_stale(self):
        stale = doc_for(2, summary(FIXTURE["items"], beat=NOW - 120))
        self.assertEqual(self.text(stale, 20), self.text({"panes": panes(2)}, 20))

    def test_click_sets_var_and_hover_key(self):
        called = []
        cv = D.render(doc_for(2), WIDTH, 20, NOW, lambda i: 0, lambda: 0, lambda: 0, bot=called.append)
        y = TITLE_ROWS + 6 + 3  # first waiting row
        self.assertEqual(cv.key_at(5, y), ("bot", "mid"))
        cv.hit_at(5, y)("left")
        self.assertEqual(called, ["mid"])
        self.assertIsNone(cv.hit_at(5, TITLE_ROWS + 6))  # the header has no region

    def test_without_callback_rows_are_not_clickable(self):
        cv = D.render(doc_for(2), WIDTH, 20, NOW, lambda i: 0, lambda: 0, lambda: 0)
        self.assertIsNone(cv.hit_at(5, TITLE_ROWS + 6 + 3))

    def test_tight_heights_drop_header_then_rows_then_all(self):
        n = 9  # at h=24: avail 21, rows of 2 (no blank, 18 rows used) leave 3
        per = D.layout(n, 24 - TITLE_ROWS - 1)
        self.assertEqual(per, 2)
        rows = self.text(doc_for(n), 24)
        self.assertNotIn("Bots", "\n".join(rows))
        self.assertEqual(sum("run-" in r or "◷" in r for r in rows), 3)
        for h, shown in ((23, 2), (22, 1), (21, 0)):
            rows = self.text(doc_for(n), h)
            self.assertEqual(sum("run-" in r or "◷" in r for r in rows), shown, h)

    def test_budget_at_window_sizes(self):
        """Agent rows are identical with and without the section; only rows below them differ, and the
        section's rows fit above the footer."""
        for label, h in SIZES.items():
            for n in (4, 9, 16):
                with self.subTest(size=label, panes=n, dock_rows=h):
                    per = D.layout(n, h - TITLE_ROWS - 1)
                    plain = D.dump(draw({"panes": panes(n)}, h))
                    full = D.dump(draw(doc_for(n), h))
                    end = TITLE_ROWS + n * per
                    self.assertEqual(full[:end], plain[:end])
                    self.assertEqual(full[h - 1], plain[h - 1])  # the footer
                    free = h - 1 - end
                    section = [r for r in full[end:h - 1] if r.strip()]
                    self.assertLessEqual(len(section), min(max(free, 0), 5))
                    self.assertEqual(len(section), min(max(free, 0), 5))
                    self.assertEqual(plain[end:h - 1], [""] * (h - 1 - end))

    def test_full_dock_shows_nothing(self):
        # 16 agents at one row each need 16 rows; a dock of 19 (avail 16) has none left; 14 panes scroll
        for n, h in ((16, 19), (30, 19)):
            plain = D.dump(draw({"panes": panes(n)}, h))
            self.assertEqual(D.dump(draw(doc_for(n), h)), plain)

    def test_agents_per_row_height_never_changes(self):
        for h in (12, 20, 30, 45):
            for n in range(1, 20):
                plain = draw({"panes": panes(n)}, h)
                full = draw(doc_for(n), h)
                per = D.layout(n, h - TITLE_ROWS - 1)
                end = TITLE_ROWS + n * per
                self.assertEqual(full.cells[:min(end, h - 1)], plain.cells[:min(end, h - 1)], (h, n))
                self.assertEqual((full.start, full.last), (plain.start, plain.last))


class DockClick(unittest.TestCase):
    def test_request_is_base64_of_the_id(self):
        class Out:
            text = ""

            def write(self, s):
                self.text += s

            def flush(self):
                pass

        dock = D.Dock("/nonexistent")
        dock.out = Out()
        dock.bot("run-one")
        name, _, value = dock.out.text[len("\x1b]1337;SetUserVar="):-1].partition("=")
        self.assertEqual(name, "hivegrid_overview_bots")
        self.assertEqual(base64.b64decode(value).decode(), "run-one:1")

    def test_summary_read_is_cached_two_seconds(self):
        dock = D.Dock("/nonexistent")
        reads = []
        real = D.HS.read_summary
        D.HS.read_summary = lambda path: reads.append(path) or FIXTURE
        try:
            self.assertIs(dock.schedules(10.0), FIXTURE)
            dock.schedules(11.9)
            self.assertEqual(len(reads), 1)
            dock.schedules(12.0)
            self.assertEqual(len(reads), 2)
        finally:
            D.HS.read_summary = real


if __name__ == "__main__":
    unittest.main()
