import dataclasses
import json
import os
import sys
import tempfile
import unittest
import unittest.mock

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
             "profile": "alpha", "state": "working", "reason": None, "since": NOW - 30, "updated": NOW - 1,
             "pending": None, "error": None}
        d.update(kw)
        with open(os.path.join(self.dir, f"{pane_id}.json"), "w") as f:
            json.dump(d, f)

    def collect(self, panes, fg=None, now=NOW):
        return {a.pane: a for a in A.collect(FakeMux(panes), now=lambda: now, fg=fg or {})}


class TestCollect(Base):
    def test_state_dir_mode(self):
        self.assertEqual(os.stat(self.dir).st_mode & 0o777, 0o700)

    def test_state_dir_problem(self):
        self.assertIsNone(A.state_dir_problem())
        other = os.path.join(self.tmp.name, "other")
        os.mkdir(other)
        os.chmod(other, 0o777)
        self.assertIn("written by others", A.state_dir_problem(other))
        os.chmod(other, 0o755)
        self.assertIsNone(A.state_dir_problem(other), "readable by others is fine, writable is not")
        link = os.path.join(self.tmp.name, "link")
        os.symlink(other, link)
        self.assertIn("not a directory", A.state_dir_problem(link), "a link to a directory is not trusted")
        self.assertIn("cannot read", A.state_dir_problem(os.path.join(self.tmp.name, "gone")))
        with unittest.mock.patch.object(A.os, "getuid", return_value=os.getuid() + 1):
            self.assertIn("another user", A.state_dir_problem())

    def test_no_mux(self):
        self.assertEqual(A.collect(lambda *a: None, now=lambda: NOW, fg={}), [])
        self.assertEqual(A.collect(lambda *a: "not json", now=lambda: NOW, fg={}), [])

    def test_state_file_wins_over_title(self):
        self.write_state(3, state="busy", reason="bash: npm test (42s)")
        a = self.collect([pane(3, "π ⠋ something")])[3]
        self.assertEqual((a.state, a.source, a.reason, a.since), ("busy", "ext", "bash: npm test (42s)", NOW - 30))
        self.assertEqual((a.profile, a.cwd, a.title, a.session_id, a.pid), ("alpha", "/abs/p", "T", "s1", SELF_PID))

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

    def test_shell_where_an_agent_ran_is_exited(self):
        fg = {"pts/5": (10, "omp", 5, "omp")}
        self.assertEqual(self.collect([pane(5, "π > x")], fg=fg)[5].state, "idle")
        fg = {"pts/5": (10, "bash", 5, "-bash"), "pts/6": (11, "bash", 5, "-bash")}
        rows = self.collect([pane(5, "u@h: ~"), pane(6, "u@h: ~")], fg=fg, now=NOW + 5)
        self.assertEqual((rows[5].state, rows[5].source, rows[5].since), ("exited", "shell", NOW + 5))
        self.assertNotIn(6, rows, "a shell where no agent ran is not an agent")

    def test_numbers_follow_reading_order_per_tab(self):
        def at(pid, left, top, tab=0):
            p = pane(pid, "π > x", tab=tab)
            p.update(left_col=left, top_row=top, size={"cols": 40, "rows": 10})
            return p
        fg = {f"pts/{i}": (i, "omp", 5, "omp") for i in (1, 2, 3, 4)}
        fg["pts/9"] = (9, "python3", 5, "python3 /x/hivegrid-bar --surface #101010")
        bar = at(9, 0, 0)
        rows = self.collect([at(4, 50, 0), at(1, 50, 11), at(2, 8, 0), bar, at(3, 0, 0, tab=1)], fg=fg)
        self.assertEqual({k: (a.num, a.tab_index) for k, a in rows.items()},
                         {2: (1, 1), 4: (2, 1), 1: (3, 1), 3: (1, 2)})

    def test_ack_matches_since_after_a_json_round_trip(self):
        self.write_state(3, state="done", since=NOW - 30.123)
        put(os.path.join(self.dir, "acks.json"), json.dumps({"3": NOW - 30.1229999}))
        self.assertEqual(self.collect([pane(3, "π > x")])[3].state, "idle")

    def test_shell_and_dock_excluded(self):
        fg = {"pts/1": (10, "bash", 5, "-bash"),
              "pts/2": (11, "python3", 5, "python3 /x/hivegrid-bar --surface #101010"),
              "pts/3": (12, "python3", 5, "python3 /x/hivegrid-overview"),
              "pts/4": (13, "omp", 5, "omp --profile beta"),
              "pts/5": (14, "claude", 5, "claude"),
              "pts/6": (15, "bash", 5, "-bash")}
        rows = self.collect([pane(1), pane(2), pane(3), pane(4, "plain"), pane(5, "x"),
                             pane(6, "π > stale title")], fg=fg)
        self.assertEqual(sorted(rows), [4, 5])
        self.assertEqual((rows[4].state, rows[4].profile, rows[4].source), ("idle", "beta", "title"))
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


BAR_ARGS = "python3 /home/u/.local/bin/hivegrid-bar --surface #0b1220 --raised #16213a"


class Tiler:
    """Fake `run` that models the pane tree of a WezTerm mux just far enough for spawn/gather:
    list, list-clients, move-pane-to-new-tab, split-pane (spawn or --move-pane-id with
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
        return {f"pts/{i}": (1, "python3", 1, BAR_ARGS) for i in self.chrome}

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
            if "--url" in a:  # a web pane: no tty, the page URL as its directory
                self.panes[new] = dict(gpane(new, 0, 0, 1, 1, tab=tab, title="Page"), tty_name=None,
                                       cwd=a[a.index("--url") + 1])
                self.url = a[a.index("--url") + 1]
                self.profile = a[a.index("--profile") + 1] if "--profile" in a else None
            else:
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


def _sizes(total, k):
    """k cell sizes (dividers take one cell each) that fill `total`, equal within one cell."""
    usable = total - (k - 1)
    base, extra = divmod(usable, k)
    return [base + (1 if i < extra else 0) for i in range(k)]


class EnvCase(Base):
    """Mux tests: no inherited origin hints and a controllable foreground-process table."""

    def setUp(self):
        super().setUp()
        self.saved = {k: os.environ.pop(k, None) for k in ("WEZTERM_PANE", "HIVEGRID_ORIGIN_PANE", "HIVEGRID_AGENT_CMD")}
        self.fg_orig = A.foreground_by_tty
        self.fg = {}
        A.foreground_by_tty = lambda: self.fg

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
        widths = _sizes(inner, n)
        x = chrome_left + 1
        for k in range(n):
            panes.append(gpane(10 + k, x, 0, widths[k], H, active=(k == 0)))
            x += widths[k] + 1
        t = Tiler(panes, chrome=(1, 2))
        self.fg = t.fg()
        return t


class TestChrome(unittest.TestCase):
    def test_detection(self):
        self.assertTrue(A.is_chrome("python3", BAR_ARGS))
        self.assertTrue(A.is_chrome("python3", "python3 /home/u/.local/bin/hivegrid-overview"))
        self.assertTrue(A.is_chrome("yazi", "/home/u/.local/bin/yazi"))
        self.assertTrue(A.is_chrome("python3", "env PWD=/x /home/u/.local/bin/hivegrid-git"))
        self.assertTrue(A.is_chrome("env", "env YAZI_CONFIG_HOME=/y /home/u/.local/bin/yazi"))
        self.assertTrue(A.is_chrome("lazygit", "lazygit"))
        self.assertFalse(A.is_chrome("omp", "omp --profile alpha"))
        self.assertFalse(A.is_chrome("bash", "bash"))
        self.assertFalse(A.is_chrome("", ""))

    def test_collect_skips_every_chrome_kind(self):
        fg = {"pts/1": (1, "python3", 1, BAR_ARGS), "pts/2": (2, "yazi", 1, "yazi"), "pts/3": (3, "omp", 1, "omp")}
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


    def test_reading_order_tolerates_off_by_one_rows(self):
        panes = [gpane(5, 100, 1, 50, 20), gpane(4, 0, 0, 50, 20), gpane(6, 0, 22, 50, 20)]
        self.assertEqual([p["pane_id"] for p in A._reading_order(panes)], [4, 5, 6])


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


class TestSpawn(EnvCase):
    def test_always_splits_inside_the_origin_tab_and_leaves_everything_else_alone(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(3)
        before = {i: t.geo(i) for i in (1, 2, 10, 11)}
        new = A.spawn_agent("/w", None, run=t)
        self.assertEqual(new, 100)
        self.assertEqual(t.panes[new]["tab_id"], 0)
        self.assertNotIn("spawn", [c[0] for c in t.calls])
        self.assertEqual([c[0] for c in t.non_list()], ["split-pane"])  # one split, nothing else
        split = t.non_list()[0]
        self.assertEqual(split[split.index("--pane-id") + 1], "12")  # the last grid pane
        self.assertNotIn("--move-pane-id", split)
        self.assertEqual({i: t.geo(i) for i in (1, 2, 10, 11)}, before)  # no pane moved or resized

    def test_command_cwd_and_default_agent_command(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(1)
        A.spawn_agent("/w", None, run=t)
        self.assertEqual(t.cmd, ["bash", "-ic", "omp; exec bash"])
        self.assertEqual(t.cwd, "/w")
        os.environ["HIVEGRID_AGENT_CMD"] = "sleep 100000"
        A.spawn_agent("/w", None, run=t)
        self.assertEqual(t.cmd, ["bash", "-ic", "sleep 100000; exec bash"])

    def test_near_only_gives_the_cwd_even_from_another_tab(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(2)
        t.panes[50] = gpane(50, 0, 0, 100, 40, tab=9)
        new = A.spawn_agent(None, agent(50, tab=9, cwd="/near"), run=t)
        self.assertEqual(t.cwd, "/near")
        self.assertEqual(t.panes[new]["tab_id"], 0)
        self.assertEqual(t.panes[50]["tab_id"], 9)

    def test_chrome_is_never_the_split_target(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(2)
        A.spawn_agent("/w", None, run=t)
        first = next(c for c in t.calls if c[0] == "split-pane" and "--move-pane-id" not in c)
        self.assertNotIn(first[first.index("--pane-id") + 1], ("1", "2"))

    def test_too_small_to_split_fails_without_re_tiling_or_retrying(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(2)
        before = {i: t.geo(i) for i in t.panes}
        t.fail_splits = 1
        self.assertIsNone(A.spawn_agent("/w", None, run=t))
        self.assertEqual({i: t.geo(i) for i in t.panes}, before)
        self.assertEqual([c[0] for c in t.non_list()], ["split-pane"])

    def test_failures(self):
        self.assertIsNone(A.spawn_agent("/w", None, run=lambda *a: None))
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(2)
        t.fail_splits = 5
        self.assertIsNone(A.spawn_agent("/w", None, run=t))


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

    def test_moves_agents_of_the_windows_other_tabs_without_re_tiling(self):
        ok, msg = A.gather(0, self.t)
        self.assertEqual((ok, msg), (True, "gathered 3 agents"))
        self.assertEqual({self.t.panes[i]["tab_id"] for i in (30, 31, 32)}, {0})
        self.assertEqual(self.t.panes[33]["tab_id"], 7)   # not an agent
        self.assertEqual(self.t.panes[34]["tab_id"], 7)   # other window
        ids = [i for i in self.t.ids(0) if i not in (1, 2)]
        self.assertEqual(sorted(ids), [10, 11, 30, 31, 32])
        self.assertEqual({i: self.t.geo(i)[2] for i in (1, 2)}, {1: 6, 2: 30})
        names = {c[0] for c in self.t.non_list()}
        self.assertEqual(names, {"split-pane"})  # no re-tile: no move-out, no activate
        self.assertEqual(sum(1 for c in self.t.calls if c[0] == "split-pane"), 3)

    def test_nothing_to_gather(self):
        for i in (30, 31, 32, 34):
            self.fg.pop(f"pts/{i}")
        self.assertEqual(A.gather(0, self.t), (True, "no agents in other tabs"))
        self.assertEqual(self.t.non_list(), [])

    def test_unknown_tab(self):
        self.assertEqual(A.gather(42, self.t), (False, "origin tab not found"))

    def test_partial_failure_is_reported_without_a_retry_or_a_re_tile(self):
        self.t.fail_splits = 1  # the first move fails
        ok, msg = A.gather(0, self.t)
        self.assertFalse(ok)
        self.assertEqual(msg, "moved 2 of 3 agents")
        self.assertNotIn("move-pane-to-new-tab", [c[0] for c in self.t.calls])


class TestDelete(EnvCase):
    def test_closes_on_ctrl_d(self):
        panes = [pane(3)]
        m = FakeMux(panes)
        sleeps = []
        def sleep(s):
            sleeps.append(s)
            panes.clear()  # pane vanished after Ctrl+D
        ok, msg = A.delete_agent(agent(3), run=m, sleep=sleep)
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
        ok, msg = A.delete_agent(agent(3), run=m, sleep=sleeps.append, grace=1.0)
        self.assertEqual((ok, msg), (True, "killed"))
        self.assertAlmostEqual(sum(sleeps), 1.0)
        self.assertEqual([c[0] for c in m.calls if c[0] not in ("list", "list-clients")], ["send-text", "kill-pane"])
        self.assertIn(("kill-pane", "--pane-id", "3"), m.calls)

    def test_kill_early_when_shell_remains(self):
        panes = [pane(3)]
        m = FakeMux(panes, {"kill-pane": lambda a: panes.clear() or ""})
        self.fg = {"pts/3": (1, "bash", 1, "bash")}
        sleeps = []
        ok, _ = A.delete_agent(agent(3), run=m, sleep=sleeps.append, grace=3.0)
        self.assertTrue(ok)
        self.assertEqual(len(sleeps), 1)

    def test_exited_skips_ctrl_d(self):
        panes = [pane(3)]
        m = FakeMux(panes, {"kill-pane": lambda a: panes.clear() or ""})
        self.assertTrue(A.delete_agent(agent(3, state="exited"), run=m, sleep=lambda s: None)[0])
        self.assertEqual([c[0] for c in m.calls if c[0] not in ("list", "list-clients")], ["kill-pane"])

    def test_kill_fails(self):
        m = FakeMux([pane(3)], {"kill-pane": None})
        self.fg = {"pts/3": (1, "omp", 1, "omp")}
        self.assertFalse(A.delete_agent(agent(3), run=m, sleep=lambda s: None, grace=0.5)[0])

    def test_closing_an_agent_never_touches_the_layout_of_its_tab(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(4)
        before = {i: t.geo(i) for i in (10, 11, 12)}
        self.fg["pts/13"] = (13, "bash", 1, "bash")  # omp quit: only the keep-alive shell is left
        ok, msg = A.delete_agent(agent(13, tab=0), run=t, sleep=lambda s: None)
        self.assertEqual((ok, msg), (True, "killed"))
        self.assertEqual([c[0] for c in t.non_list()], ["send-text", "kill-pane"])
        self.assertEqual({i: t.geo(i) for i in (10, 11, 12)}, before)

    def test_other_tab_agent_is_closed_without_touching_any_layout(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(2)
        t.panes[40] = gpane(40, 0, 0, 100, 40, tab=7)
        t.panes[41] = gpane(41, 0, 101, 100, 20, tab=7)
        self.fg["pts/40"] = (40, "bash", 1, "bash")
        ok, _ = A.delete_agent(agent(40, tab=7), run=t, sleep=lambda s: None)
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


class TestSpawnProfile(EnvCase):
    def spawn(self, profile, cwd="/w"):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(1)
        return t, A.spawn_agent(cwd, None, run=t, profile=profile)

    def test_profile_starts_omp_with_the_flag(self):
        t, new = self.spawn("beta")
        self.assertEqual(new, 100)
        self.assertEqual(t.cmd, ["bash", "-ic", "omp --profile=beta; exec bash"])

    def test_profile_with_the_plain_omp_command_set_is_fine(self):
        os.environ["HIVEGRID_AGENT_CMD"] = " omp "
        t, _ = self.spawn("beta")
        self.assertEqual(t.cmd[2], "omp --profile=beta; exec bash")

    def test_profile_is_refused_before_any_pane_exists(self):
        os.environ["HIVEGRID_AGENT_CMD"] = "sleep 100000"
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(1)
        with self.assertRaises(ValueError):
            A.spawn_agent("/w", None, run=t, profile="beta")
        self.assertEqual(t.non_list(), [])
        os.environ.pop("HIVEGRID_AGENT_CMD")
        for bad in ("a b", "a;rm", "-x", "$(id)", "a/b", "x" * 80):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                A.spawn_agent("/w", None, run=t, profile=bad)
        self.assertEqual(t.non_list(), [])

    def test_no_profile_keeps_the_command_line(self):
        t, _ = self.spawn(None)
        self.assertEqual(t.cmd, ["bash", "-ic", "omp; exec bash"])


class TestModelFiles(Base):
    def test_state_file_model_thinking_context_and_session(self):
        self.write_state(3, model={"id": "anthropic/claude-x", "name": "X", "provider": "anthropic"}, thinking="high",
                         context={"tokens": 50, "window": 200, "pct": 25}, sessionFile="/h/.omp/agent/sessions/a.jsonl")
        a = self.collect([pane(3, "π > t")])[3]
        self.assertEqual(a.model, {"id": "anthropic/claude-x", "name": "X", "provider": "anthropic"})
        self.assertEqual((a.thinking, a.context, a.session_file),
                         ("high", {"tokens": 50.0, "window": 200.0, "pct": 25.0}, "/h/.omp/agent/sessions/a.jsonl"))

    def test_an_older_extension_or_junk_leaves_them_unset(self):
        self.write_state(3)
        a = self.collect([pane(3, "π > t")])[3]
        self.assertEqual((a.model, a.thinking, a.context, a.session_file), (None, None, None, None))
        self.write_state(3, model={"id": "noslash", "provider": "p"}, thinking="turbo", context={"tokens": -1, "window": 5},
                         sessionFile=7)
        a = self.collect([pane(3, "π > t")])[3]
        self.assertEqual((a.model, a.thinking, a.context, a.session_file), (None, None, None, None))
        self.write_state(3, context={"tokens": 1, "window": 4})
        self.assertEqual(self.collect([pane(3, "π > t")])[3].context["pct"], 25.0, "computed when omp gave none")

    def test_read_models_validates_and_bounds(self):
        put(os.path.join(self.dir, "3.models.json"), json.dumps({
            "v": 1, "thinking_levels": ["off", "high", "bogus", 5],
            "models": [{"id": "a/b", "name": "B", "provider": "a", "reasoning": True}, {"id": "a/b", "provider": "a"},
                       {"id": "nope", "provider": "a"}, "junk", {"id": "c/d", "provider": "c"}]}))
        got = A.read_models(3)
        self.assertEqual(got["levels"], ["off", "high"])
        self.assertEqual(got["models"], [{"id": "a/b", "name": "B", "provider": "a", "reasoning": True},
                                         {"id": "c/d", "name": "d", "provider": "c", "reasoning": False}])
        put(os.path.join(self.dir, "4.models.json"), "{not json")
        self.assertEqual(A.read_models(4), {"levels": [], "models": []})
        self.assertEqual(A.read_models(5), {"levels": [], "models": []})

    def test_send_command_writes_a_command_file_that_is_not_a_state_file(self):
        a = agent(19)
        self.assertTrue(A.send_command(a, {"type": "set_model", "model": "a/b"}))
        (name,) = os.listdir(self.dir)
        self.assertRegex(name, r"^19\.cmd\..+\.json$")
        self.assertEqual(get(os.path.join(self.dir, name)), {"type": "set_model", "model": "a/b"})

    def test_stale_models_files_go_with_their_pane(self):
        self.write_state(3)
        put(os.path.join(self.dir, "3.models.json"), "{}")
        put(os.path.join(self.dir, "9.models.json"), "{}")
        self.collect([pane(3, "π > t")])
        self.assertTrue(os.path.exists(os.path.join(self.dir, "3.models.json")))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "9.models.json")))

    def test_read_state(self):
        self.write_state(3, state="idle")
        self.assertEqual(A.read_state(3)["state"], "idle")
        self.assertIsNone(A.read_state(4))


# Shaped like the entries of a real omp session file (ids, usage and request controls left out).
LIMIT_TEXT = ('429 {"type":"error","error":{"type":"rate_limit_error","message":"This request would exceed '
              'your account\'s rate limit. Please try again later."},"request_id":"req_x"} retry-after-ms=14523000')


def limit_entry(**over):
    msg = {"role": "assistant", "content": [], "stopReason": "error", "errorStatus": 429, "errorMessage": LIMIT_TEXT,
           "timestamp": 1793086676483, "completedAt": 1793086676845}
    msg.update(over)
    return {"type": "message", "timestamp": "2026-10-28T09:41:04.054Z", "message": msg}


class TestLimit(Base):
    def test_limit_state_passes_through_with_its_times(self):
        self.write_state(3, state="limit", reason="usage limit", limit={"hit_at": NOW - 100, "reset_at": NOW + 3600})
        a = self.collect([pane(3, "π > t")])[3]
        self.assertEqual((a.state, a.source, a.reason), ("limit", "ext", "usage limit"))
        self.assertEqual(a.limit, {"hit_at": NOW - 100, "reset_at": NOW + 3600})

    def test_malformed_limit_fields_read_as_unknown(self):
        self.write_state(3, state="limit", limit={"hit_at": "x", "reset_at": -1})
        self.assertEqual(self.collect([pane(3)])[3].limit, {"hit_at": None, "reset_at": None})
        self.write_state(4, state="limit", limit="soon")
        a = self.collect([pane(4)])[4]
        self.assertEqual((a.state, a.limit), ("limit", None))

    def test_limit_is_dropped_when_the_state_is_not_limit(self):
        self.write_state(3, state="done", limit={"hit_at": 1, "reset_at": 2})
        self.assertIsNone(self.collect([pane(3)])[3].limit)
        self.write_state(4, state="limit", updated=NOW - 60, limit={"hit_at": 1, "reset_at": 2})   # heartbeat stopped
        a = self.collect([pane(4, "π > t")])[4]
        self.assertEqual((a.state, a.limit), ("idle", None))

    def test_a_limit_agent_is_not_acknowledged_before_its_reset_but_takes_a_prompt(self):
        self.write_state(3, state="limit", limit={"hit_at": NOW - 60, "reset_at": NOW + 3600})
        a = self.collect([pane(3)])[3]
        A.ack(a, now=NOW)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "acks.json")))
        m = FakeMux([])
        self.assertTrue(A.send_prompt(a, "go on", run=m)[0])

    def test_a_limit_whose_reset_has_passed_can_be_acknowledged_until_it_hits_again(self):
        self.write_state(3, state="limit", since=NOW - 7000, limit={"hit_at": NOW - 7000, "reset_at": NOW - 100})
        a = self.collect([pane(3)])[3]
        self.assertTrue(A.limit_expired(a, NOW))
        self.assertFalse(A.limit_expired(a, NOW - 200))
        for unknown in (None, {"hit_at": 1}, {"reset_at": True}, {"reset_at": float("inf")}):
            self.assertFalse(A.limit_expired(dataclasses.replace(a, limit=unknown), NOW), unknown)
        A.ack(a, now=NOW)
        self.assertEqual(get(os.path.join(self.dir, "acks.json")), {"3": NOW - 7000})
        b = self.collect([pane(3)])[3]
        self.assertEqual((b.state, b.limit), ("idle", None))
        self.write_state(3, state="limit", since=NOW + 50, limit={"hit_at": NOW + 50, "reset_at": NOW + 9000})   # hit again
        self.assertEqual(self.collect([pane(3)], now=NOW + 60)[3].state, "limit")

    def test_a_refused_request_is_a_limit(self):
        self.assertEqual(A.limit_of_entry(limit_entry()), {"hit_at": 1793086676.845, "reset_at": 1793101199.845})

    def test_what_is_not_a_limit(self):
        for entry in (
            {"type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}], "stopReason": "stop"}},
            {"type": "message", "message": {"role": "user", "content": "go on"}},
            limit_entry(stopReason="aborted"),
            limit_entry(errorStatus=500, errorMessage="500 internal error"),
            limit_entry(errorStatus=None, errorMessage="The socket connection was closed unexpectedly before the response completed"),
            limit_entry(errorMessage='429 {"error":{"type":"rate_limit_error"}} retry-after-ms=8000'),   # a burst limit omp retries
            limit_entry(errorMessage="429 too many open files"),
            {"type": "custom", "customType": "session_exit"}, None, "x", [],
        ):
            self.assertIsNone(A.limit_of_entry(entry), entry)

    def test_no_retry_after_is_a_limit_without_a_reset(self):
        e = limit_entry(errorMessage='429 {"error":{"type":"rate_limit_error"}}')
        self.assertEqual(A.limit_of_entry(e), {"hit_at": 1793086676.845, "reset_at": None})
        self.assertEqual(A.limit_of_entry(limit_entry(completedAt=None))["hit_at"], 1793086676.483)
        self.assertEqual(A.limit_of_entry(limit_entry(completedAt=None, timestamp=None))["hit_at"], 1793180464.054)
        gone = limit_entry(completedAt=None, timestamp=None)
        gone["timestamp"] = "not a time"
        self.assertIsNone(A.limit_of_entry(gone))

    def test_an_absurd_retry_after_is_a_limit_without_a_reset_and_never_raises(self):
        for digits in ("9" * 5000, "9" * 13, "1" + "0" * 11):  # beyond int(), beyond 12 digits, beyond the 400-day maximum
            e = limit_entry(errorMessage='429 {"error":{"type":"rate_limit_error"}} retry-after-ms=' + digits)
            self.assertEqual(A.limit_of_entry(e), {"hit_at": 1793086676.845, "reset_at": None})

    def test_which_window_hit(self):
        reset = NOW + 4 * 3600
        limit = {"hit_at": NOW, "reset_at": reset}
        both = {"five_hour": {"pct": 100.0, "reset_at": reset + 1}, "seven_day": {"pct": 30.0, "reset_at": reset + 4 * 86400}}
        self.assertEqual(A.limit_window(limit, both), "five_hour")
        weekly = {"five_hour": {"pct": 2.0, "reset_at": reset + 3 * 3600}, "seven_day": {"pct": 100.0, "reset_at": reset - 2}}
        self.assertEqual(A.limit_window(limit, weekly), "seven_day")
        # nothing known: a reset within five hours could be either window, so it is not guessed
        self.assertIsNone(A.limit_window(limit, None))
        self.assertIsNone(A.limit_window(limit, {"five_hour": None, "seven_day": {"pct": 1, "reset_at": None}}))
        # later than a 5-hour window can reopen: weekly, with or without a cache
        far = {"hit_at": NOW, "reset_at": NOW + 3 * 86400}
        self.assertEqual(A.limit_window(far, None), "seven_day")
        # both windows reopen together: the horizon decides
        same = {"five_hour": {"reset_at": reset}, "seven_day": {"reset_at": reset}}
        self.assertIsNone(A.limit_window(limit, same))
        self.assertIsNone(A.limit_window({"hit_at": NOW, "reset_at": None}, both))
        self.assertIsNone(A.limit_window(None, both))


if __name__ == "__main__":
    unittest.main()
