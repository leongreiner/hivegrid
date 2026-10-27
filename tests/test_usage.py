"""Per-agent token and cost telemetry: the state file's `usage` parsed and formatted by
hivegrid_agents, drawn by the overview dock and the Agents panel."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin")
sys.dont_write_bytecode = True
sys.path.insert(0, BIN)
import hivegrid_agents as A  # noqa: E402
import hivegrid_panels as P  # noqa: E402
import hivegrid_ui as U  # noqa: E402

_spec = importlib.util.spec_from_loader("hivegrid-overview", SourceFileLoader("hivegrid-overview", os.path.join(BIN, "hivegrid-overview")))
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)
NOW = 1_000_000.0


def usage(**kw):
    base = {"input": 10, "output": 52, "cacheRead": 0, "cacheWrite": 0, "total": 1_300_000, "cost": 4.82,
            "subagentCost": 0, "costKind": "reported"}
    base.update(kw)
    return base


class ParseTest(unittest.TestCase):
    def test_reported_values(self):
        u = A.parse_usage(usage())
        self.assertEqual((u.tokens, u.cost, u.cost_kind), (1_300_000, 4.82, "reported"))

    def test_missing_or_junk_is_none(self):
        for doc in (None, [], "x", {}, {"total": None, "cost": None}, {"total": "5", "cost": -1}, {"total": float("nan")}):
            self.assertIsNone(A.parse_usage(doc), doc)

    def test_tokens_fall_back_to_input_plus_output_and_stay_unknown_otherwise(self):
        self.assertEqual(A.parse_usage({"input": 7, "output": 3}).tokens, 10)
        u = A.parse_usage({"cost": 1.0, "costKind": "estimated"})
        self.assertIsNone(u.tokens)
        self.assertEqual(u.cost_kind, "estimated")

    def test_a_cost_of_unknown_origin_counts_as_estimated_and_zero_has_no_kind(self):
        self.assertEqual(A.parse_usage({"total": 5, "cost": 2.0}).cost_kind, "estimated")
        self.assertIsNone(A.parse_usage({"total": 5, "cost": 0, "costKind": "reported"}).cost_kind)
        self.assertIsNone(A.parse_usage({"total": 5}).cost)

    def test_collect_reads_usage_from_the_state_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("XDG_RUNTIME_DIR")
            os.environ["XDG_RUNTIME_DIR"] = tmp
            try:
                d = A.state_dir()
                doc = {"v": 1, "pane": 3, "pid": os.getpid(), "state": "idle", "since": NOW, "updated": NOW,
                       "usage": usage()}
                with open(os.path.join(d, "3.json"), "w") as f:
                    json.dump(doc, f)
                run = lambda *a: json.dumps([{"pane_id": 3, "tab_id": 1, "window_id": 1, "title": "π > x", "cwd": "file:///w",
                                              "is_active": True, "tty_name": "/dev/pts/3"}]) if a[0] == "list" else ""
                got = A.collect(run, now=lambda: NOW, fg={"pts/3": (os.getpid(), "omp", 5, "omp")})
                self.assertEqual(got[0].usage.tokens, 1_300_000)
                doc.pop("usage")
                with open(os.path.join(d, "3.json"), "w") as f:
                    json.dump(doc, f)
                got = A.collect(run, now=lambda: NOW, fg={"pts/3": (os.getpid(), "omp", 5, "omp")})
                self.assertIsNone(got[0].usage)
            finally:
                if old is None:
                    os.environ.pop("XDG_RUNTIME_DIR", None)
                else:
                    os.environ["XDG_RUNTIME_DIR"] = old


class FormatTest(unittest.TestCase):
    def test_tokens(self):
        self.assertEqual([A.format_tokens(n) for n in (None, 0, 999, 1234, 12_500, 1_300_000, 45_000_000, 2.5e9)],
                         ["—", "0", "999", "1.2k", "12k", "1.3M", "45M", "2.5B"])

    def test_cost_marks_estimates_and_never_invents(self):
        self.assertEqual(A.format_cost(None), "—")
        self.assertEqual(A.format_cost(4.82, "reported"), "$4.82")
        self.assertEqual(A.format_cost(4.82, "estimated"), "~$4.82")
        self.assertEqual(A.format_cost(4.82, "mixed"), "~$4.82")
        self.assertEqual(A.format_cost(0.001, "estimated"), "~<$0.01")
        self.assertEqual(A.format_cost(0, "estimated"), "$0.00")
        self.assertEqual(A.format_cost(1234.5), "$1,234.50")

    def test_line(self):
        self.assertEqual(A.usage_text(A.parse_usage(usage())), "1.3M tok · $4.82")
        self.assertEqual(A.usage_text(A.parse_usage(usage(costKind="estimated"))), "1.3M tok · ~$4.82")
        self.assertEqual(A.usage_text(A.parse_usage({"total": 900})), "900 tok · —")
        self.assertIsNone(A.usage_text(None))


class TotalsTest(unittest.TestCase):
    def test_reported_and_estimated_cost_stay_apart(self):
        us = [A.parse_usage(usage(total=1000, cost=1.2)), A.parse_usage(usage(total=2000, cost=3.0, costKind="estimated")),
              A.parse_usage(usage(total=500, cost=0.5, costKind="mixed")), None, A.parse_usage({"total": 100})]
        t = A.usage_totals(us)
        self.assertEqual((t["agents"], t["tokens"], t["reported"]), (4, 3600, 1.2))
        self.assertAlmostEqual(t["estimated"], 3.5)
        self.assertEqual(A.totals_text(t), "3.6k tok · $1.20 + ~$3.50 est")

    def test_nothing_reported(self):
        t = A.usage_totals([None, None])
        self.assertIsNone(A.totals_text(t))
        self.assertEqual(A.totals_text(A.usage_totals([A.parse_usage({"cost": 2.0, "costKind": "estimated"})])), "— tok · ~$2.00")
        self.assertEqual(A.totals_text(A.usage_totals([A.parse_usage({"total": 5})])), "5 tok · —")


def pane(i, kind="idle", u=None, **kw):
    p = {"id": i, "num": i, "kind": kind, "glyph": "·", "text": kind, "color": "grey", "name": f"proj{i}", "cwd": "/w",
         "title": f"task {i}", "reason": None, "proc": "omp", "since": NOW - 60, "active": i == 1}
    if u is not None:
        p["usage"] = u
    p.update(kw)
    return p


def frame(panes, w=44, h=24):
    cv = D.render({"panes": panes}, w, h, NOW, goto=lambda _: None)
    return D.dump(cv)


class DockTest(unittest.TestCase):
    def test_third_line_total_and_dim_dash(self):
        lines = frame([pane(1, u=usage(total=1_300_000)), pane(2, u=usage(total=2000, cost=1.0, costKind="estimated")),
                       pane(3), pane(4, "shell", proc="bash")])
        text = "\n".join(lines)
        self.assertIn("1.3M tok · $4.82", text)
        self.assertIn("2.0k tok · ~$1.00", text)
        self.assertIn("total 1.3M tok · $4.82 + ~$1.00 est", text)
        row3 = next(i for i, l in enumerate(lines) if "task 3" in l)
        self.assertEqual(lines[row3 + 2].strip(), "—")  # reported nothing, an agent: a dash
        row4 = next(i for i, l in enumerate(lines) if "proj4" in l or "task 4" in l)
        self.assertEqual(lines[row4 + 2].strip(), "")  # a shell has no usage line

    def test_pal_colours(self):
        cv = D.render({"panes": [pane(1, u=usage()), pane(2)]}, 44, 24, NOW, goto=lambda _: None)
        pal = dict(U.DEFAULT_PAL)
        cells = [(y, x) for y, row in enumerate(cv.cells) for x, c in enumerate(row) if c[0] == "—"]
        self.assertTrue(cells)
        y, x = cells[0]
        self.assertEqual(cv.cells[y][x][1], pal["dim"])

    def test_no_telemetry_leaves_the_layout_alone(self):
        with_none = frame([pane(1), pane(2)])
        self.assertNotIn("total", "\n".join(with_none))
        self.assertNotIn("—", "\n".join(with_none))
        self.assertEqual(D.layout(5, 17, usage=False), 3)

    def test_layout_falls_back_when_the_lines_do_not_fit(self):
        self.assertEqual(D.layout(4, 15, usage=True), 4)
        self.assertEqual(D.layout(4, 14, usage=True), 3)
        self.assertEqual(D.layout(4, 8, usage=True), 2)

    def test_narrow_dock_clips_instead_of_overflowing(self):
        lines = frame([pane(1, u=usage())], w=16, h=10)
        self.assertTrue(all(U.text_width(l) <= 16 for l in lines))


class PanelTest(unittest.TestCase):
    def test_parse_panes_carries_usage_for_omp_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("XDG_RUNTIME_DIR")
            os.environ["XDG_RUNTIME_DIR"] = tmp
            try:
                with open(os.path.join(A.state_dir(), "1.json"), "w") as f:
                    json.dump({"profile": "p", "usage": usage(costKind="estimated")}, f)
                fg = {"pts/1": (1, "omp", 5, "omp --profile=q"), "pts/2": (2, "omp", 5, "omp"), "pts/3": (3, "bash", 5, "bash")}
                rows = P.parse_panes(json.dumps([{"pane_id": i, "tty_name": f"/dev/pts/{i}"} for i in (1, 2, 3)]), fg, home="/h")
                self.assertEqual((rows[0]["usage"].cost, rows[0]["usage"].cost_kind, rows[0]["profile"]), (4.82, "estimated", "q"))
                self.assertIsNone(rows[1]["usage"])
                self.assertIsNone(rows[2]["usage"])
            finally:
                if old is None:
                    os.environ.pop("XDG_RUNTIME_DIR", None)
                else:
                    os.environ["XDG_RUNTIME_DIR"] = old

    def test_cells(self):
        p = dict(U.DEFAULT_PAL)
        row = {"agent": True, "usage": A.parse_usage(usage(costKind="estimated"))}
        self.assertEqual([c[0] for c in P.AgentsPage.usage_cells(row, p)], ["1.3M", "~$4.82"])
        none = P.AgentsPage.usage_cells({"agent": True, "usage": None}, p)
        self.assertEqual([(c[0], c[1]) for c in none], [("—", p["dim"])] * 2)
        self.assertEqual([c[0] for c in P.AgentsPage.usage_cells({"agent": False, "usage": None}, p)], ["", ""])


if __name__ == "__main__":
    unittest.main()
