import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import hivegrid_agents as A  # noqa: E402

NOW = 1_000_000.0
SELF_PID = os.getpid()
DEAD_PID = 2 ** 22 + 12345  # above pid_max default on most systems; verified in setUp


def put(path, body):
    with open(path, "w") as f:
        f.write(body)


def get(path):
    with open(path) as f:
        return json.load(f)


def pane(pid, title="bash", tab=0, window=0, tty=None, active=False, cwd="file://host/home/u/proj/"):
    return {"pane_id": pid, "tab_id": tab, "window_id": window, "title": title, "cwd": cwd,
            "is_active": active, "tty_name": tty or f"/dev/pts/{pid}"}


class FakeMux:
    """Fake `run`: records calls, serves `list`, optional per-command replies."""

    def __init__(self, panes, replies=None):
        self.panes = panes
        self.calls = []
        self.replies = replies or {}

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == "list":
            return json.dumps(self.panes)
        if args[0] in self.replies:
            r = self.replies[args[0]]
            return r(args) if callable(r) else r
        return ""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("XDG_RUNTIME_DIR")
        os.environ["XDG_RUNTIME_DIR"] = self.tmp.name
        self.dir = A.state_dir()
        self.assertFalse(A._pid_alive(DEAD_PID))

    def tearDown(self):
        if self.old is None:
            os.environ.pop("XDG_RUNTIME_DIR", None)
        else:
            os.environ["XDG_RUNTIME_DIR"] = self.old
        self.tmp.cleanup()

    def write_state(self, pane_id, **kw):
        d = {"v": 1, "pane": pane_id, "pid": SELF_PID, "sessionId": "s1", "cwd": "/abs/p", "title": "T",
             "profile": "work", "state": "working", "reason": None, "since": NOW - 30, "updated": NOW - 1,
             "pending": None, "error": None}
        d.update(kw)
        with open(os.path.join(self.dir, f"{pane_id}.json"), "w") as f:
            json.dump(d, f)

    def collect(self, panes, fg=None, now=NOW):
        return {a.pane: a for a in A.collect(FakeMux(panes), now=lambda: now, fg=fg or {})}


class TestCollect(Base):
    def test_state_dir_mode(self):
        self.assertEqual(os.stat(self.dir).st_mode & 0o777, 0o700)

    def test_no_mux(self):
        self.assertEqual(A.collect(lambda *a: None, now=lambda: NOW, fg={}), [])
        self.assertEqual(A.collect(lambda *a: "not json", now=lambda: NOW, fg={}), [])

    def test_state_file_wins_over_title(self):
        self.write_state(3, state="busy", reason="bash: npm test (42s)")
        a = self.collect([pane(3, "π ⠋ something")])[3]
        self.assertEqual((a.state, a.source, a.reason, a.since), ("busy", "ext", "bash: npm test (42s)", NOW - 30))
        self.assertEqual((a.profile, a.cwd, a.title, a.session_id, a.pid), ("work", "/abs/p", "T", "s1", SELF_PID))

    def test_dead_pid_is_exited(self):
        self.write_state(3, pid=DEAD_PID, state="working")
        self.assertEqual(self.collect([pane(3, "π ⠋ x")])[3].state, "exited")

    def test_stale_heartbeat_falls_back_to_title(self):
        self.write_state(3, state="busy", updated=NOW - 60)
        a = self.collect([pane(3, "π > my title")])[3]
        self.assertEqual((a.state, a.source, a.title), ("idle", "title", "T"))
        self.assertIsNone(a.pending)

    def test_fresh_heartbeat_not_stale(self):
        self.write_state(3, state="busy", updated=NOW - 10)
        self.assertEqual(self.collect([pane(3, "π > x")])[3].state, "busy")

    def test_title_glyphs(self):
        for title, want in [("π ⠧ doing", "working"), ("π - x", "working"), ("π \\ x", "working"),
                            ("π / x", "working"), ("π | x", "working"), ("π : x", "working"),
                            ("π > ready", "idle"), ("π ! need you", "attention"), ("π: plain", "idle")]:
            a = self.collect([pane(5, title)])[5]
            self.assertEqual((a.state, a.source), (want, "title"), title)
        self.assertEqual(self.collect([pane(5, "π ⠧ doing here")])[5].title, "doing here")
        self.assertEqual(A.omp_title("π ? foo"), (None, "foo"))
        self.assertEqual(A.omp_title("zsh"), (None, "zsh"))

    def test_title_since_persists_and_resets_on_change(self):
        a = self.collect([pane(5, "π ⠋ x")], now=NOW)[5]
        self.assertEqual(a.since, NOW)
        a = self.collect([pane(5, "π ⠙ x")], now=NOW + 50)[5]  # fresh process, same state
        self.assertEqual(a.since, NOW)
        a = self.collect([pane(5, "π > x")], now=NOW + 60)[5]
        self.assertEqual((a.state, a.since), ("idle", NOW + 60))

    def test_shell_and_dock_excluded(self):
        fg = {"pts/1": (10, "bash", 5, "-bash"),
              "pts/2": (11, "bash", 5, "bash -c st=$XDG/wezterm-sidebar-2 ..."),
              "pts/3": (12, "python3", 5, "python3 /x/hivegrid-overview"),
              "pts/4": (13, "omp", 5, "omp --profile team"),
              "pts/5": (14, "claude", 5, "claude"),
              "pts/6": (15, "bash", 5, "-bash")}
        rows = self.collect([pane(1), pane(2), pane(3), pane(4, "plain"), pane(5, "x"),
                             pane(6, "π > stale title")], fg=fg)
        self.assertEqual(sorted(rows), [4, 5])
        self.assertEqual((rows[4].state, rows[4].profile, rows[4].source), ("idle", "team", "title"))
        self.assertEqual((rows[5].source, rows[5].state), ("scrape", "idle"))

    def test_dock_with_state_file_still_excluded(self):
        self.write_state(2)
        fg = {"pts/2": (11, "python3", 5, "python3 hivegrid-overview")}
        self.assertEqual(self.collect([pane(2)], fg=fg), {})

    def test_stale_state_files_deleted(self):
        self.write_state(3)
        self.write_state(99)
        put(os.path.join(self.dir, "99.cmd.x.json"), "{}")
        self.collect([pane(3, "π > x")])
        left = sorted(os.listdir(self.dir))
        self.assertIn("3.json", left)
        self.assertNotIn("99.json", left)
        self.assertNotIn("99.cmd.x.json", left)

    def test_corrupt_json_tolerated(self):
        for name, body in [("3.json", "{not json"), ("4.json", "[]"), ("5.json", '{"state":"bogus"}'),
                           ("acks.json", "garbage"), ("seen.json", "[1]")]:
            put(os.path.join(self.dir, name), body)
        rows = self.collect([pane(3, "π ⠋ a"), pane(4, "π > b"), pane(5, "π ! c")])
        self.assertEqual({k: v.state for k, v in rows.items()}, {3: "working", 4: "idle", 5: "attention"})

    def test_odd_state_fields_tolerated(self):
        self.write_state(3, pid="x", since="nope", updated=None, pending="p", reason=5, error=[])
        a = self.collect([pane(3, "bash")], fg={})
        self.assertIn(3, a)  # no raise; pid invalid => exited
        self.assertEqual(a[3].state, "exited")

    def test_sorted_and_active_and_cwd(self):
        rows = A.collect(FakeMux([pane(9, "π > a", active=True), pane(2, "π > b", tab=1)]), now=lambda: NOW, fg={})
        self.assertEqual([a.pane for a in rows], [2, 9])
        self.assertEqual((rows[1].active, rows[0].tab, rows[0].cwd), (True, 1, "/home/u/proj/"))

    def test_pending_passthrough(self):
        pend = {"kind": "approval", "tool": "bash", "summary": "rm"}
        self.write_state(3, state="attention", pending=pend)
        self.assertEqual(self.collect([pane(3)])[3].pending, pend)

    def test_single_list_call(self):
        m = FakeMux([pane(1, "π > a"), pane(2, "π > b")])
        A.collect(m, now=lambda: NOW, fg={})
        self.assertEqual([c[0] for c in m.calls], ["list"])


class TestAck(Base):
    def test_ack_lifecycle(self):
        self.write_state(3, state="done", since=NOW - 5)
        a = self.collect([pane(3)])[3]
        self.assertEqual(a.state, "done")
        A.ack(a)
        self.assertEqual(self.collect([pane(3)])[3].state, "idle")
        # persisted: a fresh collect (new overlay) still sees it acked
        self.assertEqual(get(os.path.join(self.dir, "acks.json")), {"3": NOW - 5})
        # a new turn finishes -> new `since` -> done again
        self.write_state(3, state="done", since=NOW + 100)
        self.assertEqual(self.collect([pane(3)], now=NOW + 101)[3].state, "done")

    def test_ack_ignores_working(self):
        self.write_state(3, state="working")
        A.ack(self.collect([pane(3)])[3])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "acks.json")))

    def test_jump_acks(self):
        self.write_state(3, state="done", since=NOW - 5)
        a = self.collect([pane(3)])[3]
        m = FakeMux([], {"activate-pane": ""})
        self.assertTrue(A.jump(a, run=m))
        self.assertEqual(m.calls, [("activate-pane", "--pane-id", "3")])
        self.assertEqual(self.collect([pane(3)])[3].state, "idle")
        self.assertFalse(A.jump(a, run=lambda *x: None))


def agent(pid=3, state="idle", **kw):
    d = dict(pane=pid, tab=0, window=0, cwd="/x", title="t", state=state, since=NOW)
    d.update(kw)
    return A.Agent(**d)


class TestSend(Base):
    def test_sequence(self):
        m = FakeMux([])
        self.assertEqual(A.send_prompt(agent(7), "-fix it\nnow", run=m), (True, "sent"))
        self.assertEqual(m.calls, [("send-text", "--pane-id", "7", "--", "-fix it\nnow"),
                                   ("send-text", "--pane-id", "7", "--no-paste", "\r")])

    def test_refusals(self):
        m = FakeMux([])
        self.assertFalse(A.send_prompt(agent(state="attention"), "hi", run=m)[0])
        self.assertFalse(A.send_prompt(agent(state="exited"), "hi", run=m)[0])
        self.assertFalse(A.send_prompt(agent(), "  \n", run=m)[0])
        self.assertEqual(m.calls, [])

    def test_failures(self):
        self.assertFalse(A.send_prompt(agent(), "x", run=lambda *a: None)[0])
        n = []
        def run(*a):
            n.append(a)
            return "" if len(n) == 1 else None
        ok, msg = A.send_prompt(agent(), "x", run=run)
        self.assertFalse(ok)
        self.assertIn("Enter", msg)

    def test_acks_done(self):
        self.write_state(3, state="done", since=NOW - 5)
        a = self.collect([pane(3)])[3]
        A.send_prompt(a, "go", run=FakeMux([]))
        self.assertEqual(self.collect([pane(3)])[3].state, "idle")


def gpane(pid, left, top, cols, rows, tab=0, window=0, title="π > x", active=False, zoomed=False):
    return {"pane_id": pid, "tab_id": tab, "window_id": window, "title": title, "cwd": "file://h/w/",
            "is_active": active, "is_zoomed": zoomed, "tty_name": f"/dev/pts/{pid}",
            "left_col": left, "top_row": top, "size": {"cols": cols, "rows": rows}}


BAR_ARGS = "bash -c st=\"${XDG_RUNTIME_DIR:-/tmp}/wezterm-sidebar-$WEZTERM_PANE\" trap"


class Tiler:
    """Fake `run` that models the pane tree of a WezTerm mux just far enough for arrange/spawn/
    gather: list, list-clients, move-pane-to-new-tab, split-pane (spawn or --move-pane-id with
    --cells, one divider cell), activate-*, zoom-pane, kill-pane. When a tab is left with a single
    non-chrome pane that pane takes the whole grid region (the box the grid filled at start)."""

    def __init__(self, panes, chrome=(), fail_splits=0, client_focus=None):
        self.panes = {p["pane_id"]: dict(p, size=dict(p["size"])) for p in panes}
        self.chrome = set(chrome)
        self.region = {}
        for t in {p["tab_id"] for p in panes}:
            grid = [p for p in self.panes.values() if p["tab_id"] == t and p["pane_id"] not in self.chrome]
            if grid:
                x0 = min(p["left_col"] for p in grid)
                y0 = min(p["top_row"] for p in grid)
                self.region[t] = (x0, y0, max(p["left_col"] + p["size"]["cols"] for p in grid) - x0,
                                  max(p["top_row"] + p["size"]["rows"] for p in grid) - y0)
        self.calls, self.next_id, self.next_tab = [], 100, 100
        self.fail_splits, self.client_focus = fail_splits, client_focus

    def fg(self):
        return {f"pts/{i}": (1, "bash", 1, BAR_ARGS) for i in self.chrome}

    def ids(self, tab):
        return [i for i, p in self.panes.items() if p["tab_id"] == tab]

    def geo(self, i):
        p = self.panes[i]
        return (p["left_col"], p["top_row"], p["size"]["cols"], p["size"]["rows"])

    def set_geo(self, i, x, y, w, h):
        p = self.panes[i]
        p["left_col"], p["top_row"], p["size"] = x, y, {"cols": w, "rows": h}

    def __call__(self, *a):
        self.calls.append(a)
        cmd = a[0]
        if cmd == "list":
            return json.dumps(list(self.panes.values()))
        if cmd == "list-clients":
            return json.dumps([{"focused_pane_id": self.client_focus}]) if self.client_focus is not None else "[]"
        if cmd == "move-pane-to-new-tab":
            i = int(a[a.index("--pane-id") + 1])
            tab = self.panes[i]["tab_id"]
            self.panes[i]["tab_id"] = self.next_tab
            self.next_tab += 1
            self.regrow(tab)
            return ""
        if cmd == "split-pane":
            return self.split(a)
        if cmd == "kill-pane":
            i = int(a[a.index("--pane-id") + 1])
            tab = self.panes.pop(i)["tab_id"]
            self.regrow(tab)
            return ""
        if cmd == "zoom-pane":
            for p in self.panes.values():
                p["is_zoomed"] = False
            return ""
        return ""

    def regrow(self, tab):
        grid = [i for i in self.ids(tab) if i not in self.chrome]
        if len(grid) == 1 and tab in self.region:
            self.set_geo(grid[0], *self.region[tab])

    def split(self, a):
        if self.fail_splits:
            self.fail_splits -= 1
            return None
        target = int(a[a.index("--pane-id") + 1])
        x, y, w, h = self.geo(target)
        right = "--right" in a
        total = w if right else h
        cells = int(a[a.index("--cells") + 1]) if "--cells" in a else (total - 1) // 2
        if cells < 1 or total - cells - 1 < 1:
            return None
        tab = self.panes[target]["tab_id"]
        if "--move-pane-id" in a:
            new = int(a[a.index("--move-pane-id") + 1])
            old_tab = self.panes[new]["tab_id"]
            self.panes[new]["tab_id"] = tab
            self.regrow(old_tab)
        else:
            new = self.next_id
            self.next_id += 1
            self.panes[new] = gpane(new, 0, 0, 1, 1, tab=tab)
            self.cmd = list(a[a.index("--") + 1:])
            self.cwd = a[a.index("--cwd") + 1]
            for i in self.ids(tab):  # a split focuses the new pane
                self.panes[i]["is_active"] = False
            self.panes[new]["is_active"] = True
        if right:
            self.set_geo(target, x, y, total - cells - 1, h)
            self.set_geo(new, x + total - cells, y, cells, h)
        else:
            self.set_geo(target, x, y, w, total - cells - 1)
            self.set_geo(new, x, y + total - cells, w, cells)
        return f"{new}\n"

    def non_list(self):
        return [c for c in self.calls if c[0] not in ("list", "list-clients")]


def grid_rows(t, ids):
    return [[i for i in row] for row in
            ([p["pane_id"] for p in r] for r in A._rows_of([t.panes[i] for i in ids]))]


def assert_equal_cells(case, t, ids):
    rows = A._rows_of([t.panes[i] for i in ids])
    heights = [t.geo(i)[3] for i in ids]
    case.assertLessEqual(max(heights) - min(heights), 1, heights)
    for row in rows:
        widths = [t.geo(p["pane_id"])[2] for p in row]
        case.assertLessEqual(max(widths) - min(widths), 1, widths)


class EnvCase(Base):
    """Mux tests: no inherited origin hints, a controllable foreground-process table, a captured
    equalize escape."""

    def setUp(self):
        super().setUp()
        self.saved = {k: os.environ.pop(k, None) for k in ("WEZTERM_PANE", "HIVEGRID_ORIGIN_PANE", "HIVEGRID_AGENT_CMD")}
        self.fg_orig = A.foreground_by_tty
        self.fg = {}
        A.foreground_by_tty = lambda: self.fg
        self.out = io.StringIO()

    def tearDown(self):
        A.foreground_by_tty = self.fg_orig
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def strip(self, n, chrome_left=6, chrome_right=30, W=247, H=62):
        """Chrome bar (id 1) and dock (id 2) around n grid panes (ids 10..) in one row."""
        inner = W - chrome_left - chrome_right - 2
        panes = [gpane(1, 0, 0, chrome_left, H), gpane(2, W - chrome_right, 0, chrome_right, H)]
        widths = A._sizes(inner, n)
        x = chrome_left + 1
        for k in range(n):
            panes.append(gpane(10 + k, x, 0, widths[k], H, active=(k == 0)))
            x += widths[k] + 1
        t = Tiler(panes, chrome=(1, 2))
        self.fg = t.fg()
        return t


class TestChrome(unittest.TestCase):
    def test_detection(self):
        self.assertTrue(A.is_chrome("bash", BAR_ARGS))
        self.assertTrue(A.is_chrome("python3", "python3 /home/u/.local/bin/hivegrid-overview"))
        self.assertTrue(A.is_chrome("yazi", "/home/u/.local/bin/yazi"))
        self.assertTrue(A.is_chrome("python3", "env PWD=/x /home/u/.local/bin/hivegrid-git"))
        self.assertTrue(A.is_chrome("env", "env YAZI_CONFIG_HOME=/y /home/u/.local/bin/yazi"))
        self.assertTrue(A.is_chrome("lazygit", "lazygit"))
        self.assertFalse(A.is_chrome("omp", "omp --profile work"))
        self.assertFalse(A.is_chrome("bash", "bash"))
        self.assertFalse(A.is_chrome("", ""))

    def test_collect_skips_every_chrome_kind(self):
        fg = {"pts/1": (1, "bash", 1, BAR_ARGS), "pts/2": (2, "yazi", 1, "yazi"), "pts/3": (3, "omp", 1, "omp")}
        panes = [pane(1), pane(2), pane(3, "π ⠋ work")]
        tmp = tempfile.TemporaryDirectory()
        old = os.environ.get("XDG_RUNTIME_DIR")
        os.environ["XDG_RUNTIME_DIR"] = tmp.name
        try:
            self.assertEqual([a.pane for a in A.collect(FakeMux(panes), fg=fg)], [3])
        finally:
            if old is None:
                os.environ.pop("XDG_RUNTIME_DIR", None)
            else:
                os.environ["XDG_RUNTIME_DIR"] = old
            tmp.cleanup()


class TestLayoutMath(unittest.TestCase):
    def test_ideal_counts(self):
        self.assertEqual(A.ideal_counts(1, 200, 60), [1])
        self.assertEqual(A.ideal_counts(2, 200, 60), [2])
        self.assertEqual(A.ideal_counts(4, 200, 60), [2, 2])
        self.assertEqual(A.ideal_counts(9, 200, 60), [3, 3, 3])
        self.assertEqual(sum(A.ideal_counts(7, 200, 60)), 7)
        # a tall, narrow region stacks panes instead of squeezing columns
        self.assertEqual(A.ideal_counts(3, 90, 70), [1, 1, 1])

    def test_sizes_fill_the_total_with_one_divider_cell_between(self):
        for total in (50, 69, 100, 209):
            for k in (1, 2, 3, 5):
                s = A._sizes(total, k)
                self.assertEqual(sum(s) + k - 1, total)
                self.assertLessEqual(max(s) - min(s), 1)

    def test_reading_order_tolerates_off_by_one_rows(self):
        panes = [gpane(5, 100, 1, 50, 20), gpane(4, 0, 0, 50, 20), gpane(6, 0, 22, 50, 20)]
        self.assertEqual([p["pane_id"] for p in A._reading_order(panes)], [4, 5, 6])


class TestRequestEqualize(unittest.TestCase):
    def test_escape(self):
        out = io.StringIO()
        self.assertTrue(A.request_equalize(12, out))
        self.assertEqual(out.getvalue(), "\x1b]1337;SetUserVar=hivegrid_hive_equalize=MTI=\x07")

    def test_closed_stream(self):
        out = io.StringIO()
        out.close()
        self.assertFalse(A.request_equalize(1, out))


class TestOrigin(EnvCase):
    def panes(self):
        return [gpane(1, 0, 0, 10, 10, tab=0, active=True), gpane(2, 0, 0, 10, 10, tab=5, window=1, active=True),
                gpane(3, 11, 0, 10, 10, tab=5, window=1)]

    def test_env_hint_wins(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "3"
        os.environ["WEZTERM_PANE"] = "1"
        self.assertEqual(A.origin(Tiler(self.panes())), (3, 5, 1))

    def test_wezterm_pane_when_listed(self):
        os.environ["WEZTERM_PANE"] = "2"
        self.assertEqual(A.origin(Tiler(self.panes())), (2, 5, 1))

    def test_unlisted_ids_are_ignored_then_focused_client_pane(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "999"
        os.environ["WEZTERM_PANE"] = "998"
        self.assertEqual(A.origin(Tiler(self.panes(), client_focus=3)), (3, 5, 1))

    def test_fallback_active_pane_of_first_tab(self):
        self.assertEqual(A.origin(Tiler(self.panes(), client_focus=77)), (1, 0, 0))

    def test_no_mux(self):
        self.assertIsNone(A.origin(lambda *a: None))
        self.assertIsNone(A.origin(lambda *a: "[]"))


class TestArrange(EnvCase):
    def test_every_count_becomes_an_equal_grid_in_reading_order(self):
        for n in (2, 3, 4, 5, 7, 9):
            with self.subTest(n=n):
                t = self.strip(n)
                chrome_before = {i: t.geo(i) for i in (1, 2)}
                out = io.StringIO()
                self.assertEqual(A.arrange(0, t, 10, out)[0], True)
                ids = list(range(10, 10 + n))
                rows = grid_rows(t, ids)
                self.assertEqual([len(r) for r in rows], A.ideal_counts(n, 247 - 6 - 30 - 2, 62))
                self.assertEqual([i for r in rows for i in r], ids)  # reading order stays
                assert_equal_cells(self, t, ids)
                self.assertEqual({i: t.geo(i) for i in (1, 2)}, chrome_before)
                moved = {c[c.index("--pane-id") + 1] for c in t.calls if c[0] == "move-pane-to-new-tab"}
                self.assertFalse(moved & {"1", "2"})
                # covers the whole grid area: bounding box equals the original region
                self.assertEqual(max(t.geo(i)[0] + t.geo(i)[2] for i in ids), 247 - 30 - 1)
                self.assertEqual(max(t.geo(i)[1] + t.geo(i)[3] for i in ids), 62)

    def test_unequal_strip_is_rebuilt_with_exact_cells(self):
        t = self.strip(3)
        A.arrange(0, t, 10, self.out)
        splits = [c for c in t.calls if c[0] == "split-pane"]
        self.assertTrue(all("--cells" in c and "--move-pane-id" in c for c in splits))

    def test_idempotent_and_cheap_on_a_regular_grid(self):
        t = self.strip(4)
        A.arrange(0, t, 10, self.out)
        before = {i: t.geo(i) for i in t.panes}
        t.calls.clear()
        self.out.truncate(0)
        self.out.seek(0)
        self.assertEqual(A.arrange(0, t, 10, self.out), (True, "grid already regular"))
        self.assertEqual(t.non_list(), [])
        self.assertEqual(self.out.getvalue(), "")
        self.assertEqual({i: t.geo(i) for i in t.panes}, before)

    def test_focus_and_tab_restored_and_equalize_requested_for_rectangles(self):
        t = self.strip(4)
        A.arrange(0, t, 12, self.out)
        self.assertEqual(t.non_list()[-2:], [("activate-tab", "--tab-id", "0"), ("activate-pane", "--pane-id", "12")])
        self.assertEqual(self.out.getvalue(), "\x1b]1337;SetUserVar=hivegrid_hive_equalize=MTI=\x07")

    def test_focus_defaults_to_the_active_pane(self):
        t = self.strip(4)
        t.panes[12]["is_active"], t.panes[10]["is_active"] = True, False
        A.arrange(0, t, None, self.out)
        self.assertEqual(t.non_list()[-1], ("activate-pane", "--pane-id", "12"))

    def test_partly_filled_grid_is_not_equalized(self):
        t = self.strip(5)  # [3, 2]: equalize would align the last row's dividers to 3 columns
        self.assertEqual(A.arrange(0, t, 10, self.out)[1], "grid 3x2")
        self.assertEqual(self.out.getvalue(), "")

    def test_zoomed_pane_is_unzoomed_first(self):
        t = self.strip(3)
        t.panes[11]["is_zoomed"] = True
        A.arrange(0, t, 10, self.out)
        self.assertEqual(t.non_list()[0], ("zoom-pane", "--pane-id", "11", "--unzoom"))

    def test_plain_shell_panes_join_the_grid(self):
        t = self.strip(3)  # ids 10..12 are bare shells (no agent state): still grid panes
        self.fg.update({"pts/10": (5, "bash", 1, "bash")})
        A.arrange(0, t, 10, self.out)
        moved = {c[c.index("--pane-id") + 1] for c in t.calls if c[0] == "move-pane-to-new-tab"}
        self.assertEqual(moved, {"11", "12"})

    def test_single_grid_pane_and_missing_tab(self):
        t = self.strip(1)
        self.assertEqual(A.arrange(0, t, None, self.out), (True, "nothing to arrange"))
        self.assertEqual(t.non_list(), [])
        self.assertEqual(A.arrange(7, t, None, self.out), (True, "nothing to arrange"))
        self.assertEqual(A.arrange(0, lambda *a: None), (False, "no mux"))

    def test_failed_split_puts_every_pane_back(self):
        t = self.strip(4)
        t.fail_splits = 1
        ok, msg = A.arrange(0, t, 10, self.out)
        self.assertFalse(ok)
        self.assertEqual({t.panes[i]["tab_id"] for i in (10, 11, 12, 13)}, {0})
        self.assertEqual(self.out.getvalue(), "")
        self.assertEqual(t.non_list()[-1], ("activate-pane", "--pane-id", "10"))

    def test_failed_move_out_restores(self):
        t = self.strip(3)
        real = t.__call__
        count = [0]

        def run(*a):
            if a[0] == "move-pane-to-new-tab":
                count[0] += 1
                if count[0] == 2:
                    return None
            return real(*a)
        ok, _ = A.arrange(0, run, 10, self.out)
        self.assertFalse(ok)
        self.assertEqual({t.panes[i]["tab_id"] for i in (10, 11, 12)}, {0})


class TestSpawn(EnvCase):
    def test_always_splits_inside_the_origin_tab_and_regrids(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(8)
        A.arrange(0, t, 10, self.out)
        new = A.spawn_agent("/w", None, run=t, out=self.out)
        self.assertEqual(new, 100)
        self.assertEqual(t.panes[new]["tab_id"], 0)
        self.assertNotIn("spawn", [c[0] for c in t.calls])
        ids = [i for i in t.ids(0) if i not in (1, 2)]
        self.assertEqual(len(ids), 9)
        self.assertEqual(grid_rows(t, ids), [[10, 11, 12], [13, 14, 15], [16, 17, 100]])  # new pane last
        assert_equal_cells(self, t, ids)

    def test_command_cwd_and_default_agent_command(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(1)
        A.spawn_agent("/w", None, run=t, out=self.out)
        self.assertEqual(t.cmd, ["bash", "-ic", "omp; exec bash"])
        self.assertEqual(t.cwd, "/w")
        os.environ["HIVEGRID_AGENT_CMD"] = "sleep 100000"
        A.spawn_agent("/w", None, run=t, out=self.out)
        self.assertEqual(t.cmd, ["bash", "-ic", "sleep 100000; exec bash"])

    def test_near_only_gives_the_cwd_even_from_another_tab(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(2)
        t.panes[50] = gpane(50, 0, 0, 100, 40, tab=9)
        new = A.spawn_agent(None, agent(50, tab=9, cwd="/near"), run=t, out=self.out)
        self.assertEqual(t.cwd, "/near")
        self.assertEqual(t.panes[new]["tab_id"], 0)
        self.assertEqual(t.panes[50]["tab_id"], 9)

    def test_focus_returns_to_the_origin_pane_even_when_no_rebuild_is_needed(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(1)  # one split already gives the regular 2-column grid
        A.spawn_agent("/w", None, run=t, out=self.out)
        self.assertEqual(t.non_list()[-1], ("activate-pane", "--pane-id", "10"))
        self.assertNotIn("move-pane-to-new-tab", [c[0] for c in t.calls])

    def test_chrome_is_never_the_split_target(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(2)
        A.spawn_agent("/w", None, run=t, out=self.out)
        first = next(c for c in t.calls if c[0] == "split-pane" and "--move-pane-id" not in c)
        self.assertNotIn(first[first.index("--pane-id") + 1], ("1", "2"))

    def test_too_small_to_split_regrids_and_retries(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(2)
        t.fail_splits = 1
        new = A.spawn_agent("/w", None, run=t, out=self.out)
        self.assertIsNotNone(new)
        self.assertEqual(t.panes[new]["tab_id"], 0)

    def test_failures(self):
        self.assertIsNone(A.spawn_agent("/w", None, run=lambda *a: None))
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(2)
        t.fail_splits = 5
        self.assertIsNone(A.spawn_agent("/w", None, run=t, out=self.out))


class TestGather(EnvCase):
    def setUp(self):
        super().setUp()
        self.t = self.strip(2)
        # two agents in tab 7, one in tab 8 (omp is the foreground process), a plain shell in tab 7
        for i, tab in ((30, 7), (31, 7), (32, 8), (33, 7)):
            self.t.panes[i] = gpane(i, 0, 0, 100, 40, tab=tab, title="bash")
        self.t.panes[34] = gpane(34, 0, 0, 100, 40, tab=7, window=3, title="bash")  # another window: stays
        self.fg.update({f"pts/{i}": (i, "omp", 1, "omp") for i in (30, 31, 32, 34)})
        self.fg["pts/33"] = (33, "bash", 1, "bash")
        self.t.region[0] = (7, 0, 209, 62)

    def test_moves_agents_of_the_windows_other_tabs_and_regrids(self):
        ok, msg = A.gather(0, self.t, self.out)
        self.assertEqual((ok, msg), (True, "gathered 3 agents"))
        self.assertEqual({self.t.panes[i]["tab_id"] for i in (30, 31, 32)}, {0})
        self.assertEqual(self.t.panes[33]["tab_id"], 7)   # not an agent
        self.assertEqual(self.t.panes[34]["tab_id"], 7)   # other window
        ids = [i for i in self.t.ids(0) if i not in (1, 2)]
        self.assertEqual(sorted(ids), [10, 11, 30, 31, 32])
        assert_equal_cells(self, self.t, ids)
        self.assertEqual({i: self.t.geo(i)[2] for i in (1, 2)}, {1: 6, 2: 30})

    def test_nothing_to_gather(self):
        for i in (30, 31, 32, 34):
            self.fg.pop(f"pts/{i}")
        self.assertEqual(A.gather(0, self.t, self.out), (True, "no agents in other tabs"))
        self.assertEqual(self.t.non_list(), [])

    def test_unknown_tab(self):
        self.assertEqual(A.gather(42, self.t, self.out), (False, "origin tab not found"))

    def test_partial_failure_is_reported(self):
        self.t.fail_splits = 2  # the first move and its retry fail
        ok, msg = A.gather(0, self.t, self.out)
        self.assertFalse(ok)
        self.assertIn("of 3", msg)


class TestDelete(EnvCase):
    def test_closes_on_ctrl_d(self):
        panes = [pane(3)]
        m = FakeMux(panes)
        sleeps = []
        def sleep(s):
            sleeps.append(s)
            panes.clear()  # pane vanished after Ctrl+D
        ok, msg = A.delete_agent(agent(3), run=m, sleep=sleep, out=self.out)
        self.assertEqual((ok, msg), (True, "closed"))
        self.assertIn(("send-text", "--pane-id", "3", "--no-paste", "\x04"), m.calls)
        self.assertNotIn("kill-pane", [c[0] for c in m.calls])

    def test_kill_after_grace(self):
        panes = [pane(3)]
        def kill(args):
            panes.clear()
            return ""
        m = FakeMux(panes, {"kill-pane": kill})
        self.fg = {"pts/3": (1, "omp", 1, "omp")}
        sleeps = []
        ok, msg = A.delete_agent(agent(3), run=m, sleep=sleeps.append, grace=1.0, out=self.out)
        self.assertEqual((ok, msg), (True, "killed"))
        self.assertAlmostEqual(sum(sleeps), 1.0)
        self.assertEqual([c[0] for c in m.calls if c[0] not in ("list", "list-clients")], ["send-text", "kill-pane"])
        self.assertIn(("kill-pane", "--pane-id", "3"), m.calls)

    def test_kill_early_when_shell_remains(self):
        panes = [pane(3)]
        m = FakeMux(panes, {"kill-pane": lambda a: panes.clear() or ""})
        self.fg = {"pts/3": (1, "bash", 1, "bash")}
        sleeps = []
        ok, _ = A.delete_agent(agent(3), run=m, sleep=sleeps.append, grace=3.0, out=self.out)
        self.assertTrue(ok)
        self.assertEqual(len(sleeps), 1)

    def test_exited_skips_ctrl_d(self):
        panes = [pane(3)]
        m = FakeMux(panes, {"kill-pane": lambda a: panes.clear() or ""})
        self.assertTrue(A.delete_agent(agent(3, state="exited"), run=m, sleep=lambda s: None, out=self.out)[0])
        self.assertEqual([c[0] for c in m.calls if c[0] not in ("list", "list-clients")], ["kill-pane"])

    def test_kill_fails(self):
        m = FakeMux([pane(3)], {"kill-pane": None})
        self.fg = {"pts/3": (1, "omp", 1, "omp")}
        self.assertFalse(A.delete_agent(agent(3), run=m, sleep=lambda s: None, grace=0.5, out=self.out)[0])

    def test_regrids_the_origin_tab_after_closing_an_agent(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(4)
        A.arrange(0, t, 10, self.out)
        self.fg["pts/13"] = (13, "bash", 1, "bash")  # omp quit: only the keep-alive shell is left
        ok, msg = A.delete_agent(agent(13, tab=0), run=t, sleep=lambda s: None, out=self.out)
        self.assertEqual((ok, msg), (True, "killed"))
        ids = [i for i in t.ids(0) if i not in (1, 2)]
        self.assertEqual(ids, [10, 11, 12])
        self.assertEqual([len(r) for r in grid_rows(t, ids)], A.ideal_counts(3, 209, 62))
        assert_equal_cells(self, t, ids)

    def test_other_tab_agent_is_closed_without_touching_any_layout(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(2)
        t.panes[40] = gpane(40, 0, 0, 100, 40, tab=7)
        t.panes[41] = gpane(41, 0, 101, 100, 20, tab=7)
        self.fg["pts/40"] = (40, "bash", 1, "bash")
        ok, _ = A.delete_agent(agent(40, tab=7), run=t, sleep=lambda s: None, out=self.out)
        self.assertTrue(ok)
        self.assertEqual([c[0] for c in t.non_list()], ["send-text", "kill-pane"])
        self.assertNotIn("move-pane-to-new-tab", [c[0] for c in t.calls])
        self.assertNotIn("activate-tab", [c[0] for c in t.calls])


class TestPeekAndPlan(Base):
    def test_peek(self):
        m = FakeMux([], {"get-text": "a\n\n  b  \n\nc\n\n\n"})
        self.assertEqual(A.peek(4, 2, run=m), ["  b", "c"])
        self.assertEqual(m.calls[0], ("get-text", "--pane-id", "4"))
        self.assertEqual(A.peek(4, run=lambda *a: None), [])

    def pending(self):
        return {"kind": "plan_review", "reviewId": "r1", "title": "P", "choices": [{"id": "execute", "label": "Go"}]}

    def test_answer_writes_command_file(self):
        a = agent(19, state="attention", pending=self.pending())
        self.assertEqual(A.answer_plan_review(a, "refine", "tighter", "plan"), (True, "sent"))
        files = [f for f in os.listdir(self.dir)]
        self.assertEqual(len(files), 1)
        self.assertRegex(files[0], r"^19\.cmd\..+\.json$")
        self.assertEqual(get(os.path.join(self.dir, files[0])),
                         {"type": "plan_review", "reviewId": "r1", "choice": "refine", "feedback": "tighter", "role": "plan"})
        A.answer_plan_review(a, "execute")
        bodies = [get(os.path.join(self.dir, f)) for f in os.listdir(self.dir)]
        self.assertIn({"type": "plan_review", "reviewId": "r1", "choice": "execute"}, bodies)

    def test_answer_rejections(self):
        a = agent(19, pending=self.pending())
        self.assertFalse(A.answer_plan_review(a, "refine")[0])
        self.assertFalse(A.answer_plan_review(a, "nuke")[0])
        self.assertFalse(A.answer_plan_review(agent(19), "execute")[0])
        self.assertFalse(A.answer_plan_review(agent(19, pending={"kind": "ask"}), "execute")[0])
        self.assertEqual(os.listdir(self.dir), [])


if __name__ == "__main__":
    unittest.main()
