"""Web panes (the Hivegrid fork's browser pane) in the agent core: how `wezterm cli list` shows
one (tty_name null, the page URL as cwd), that it is never an agent but counts in the numbering and
the grid, spawn_web's command line, and the typed-location rules shared with config/web.lua."""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import hivegrid_agents as A  # noqa: E402
from test_agents import BAR_ARGS, NOW, Base, EnvCase, Tiler, gpane, pane  # noqa: E402


def web(pid, url="https://example.com/a", tab=0, left=0, top=0, cols=40, rows=20, title="Example"):
    """A `wezterm cli list` entry of a web pane: no tty, the page URL as the directory."""
    return dict(gpane(pid, left, top, cols, rows, tab=tab, title=title), tty_name=None, cwd=url)


class TestIsWeb(unittest.TestCase):
    def test_a_listed_pane_is_web_when_it_has_no_tty_and_a_non_file_url(self):
        for url in ("https://example.com/", "http://localhost:3000/x", "about:blank", "data:text/html,hi",
                    "view-source:https://a.test/"):
            with self.subTest(url=url):
                self.assertTrue(A.is_web(web(5, url)))
        self.assertTrue(A.is_web({"pane_id": 5, "tty_name": "", "cwd": "https://a.test/"}))
        self.assertTrue(A.is_web({"pane_id": 5, "cwd": "https://a.test/"}), "tty_name missing")

    def test_terminals_and_other_panes_are_not(self):
        self.assertFalse(A.is_web(pane(5)), "a terminal: tty and a file: cwd")
        self.assertFalse(A.is_web(pane(5, cwd="https://a.test/")), "a pane with a tty is a terminal")
        self.assertFalse(A.is_web(dict(pane(5, cwd="file:///home/u/page.html"), tty_name=None)),
                         "without the marker a file: directory is never taken for a page")
        self.assertFalse(A.is_web({"pane_id": 5, "tty_name": None}), "no cwd at all (an overlay)")
        self.assertFalse(A.is_web({"pane_id": 5, "tty_name": None, "cwd": None}))
        self.assertFalse(A.is_web({"pane_id": 5, "tty_name": None, "cwd": "/home/u"}), "a bare path is no URL")
        self.assertFalse(A.is_web({"pane_id": 5, "tty_name": None, "cwd": "HTTPS://a.test"}), "schemes are lower case")


class TestCollect(Base):
    def test_a_web_pane_is_never_an_agent(self):
        omp = pane(1, title="π > chat", tab=0)
        # a page titled like an omp session must not pass for one
        page = web(2, title="π ⠋ looks like omp")
        shell = pane(3, tab=0)
        fg = {"pts/1": (11, "omp", 5, "omp"), "pts/3": (13, "bash", 5, "bash")}
        got = self.collect([omp, page, shell], fg=fg)
        self.assertEqual(sorted(got), [1])

    def test_a_web_pane_beside_agents_leaves_their_numbers_and_state_alone(self):
        panes = [dict(gpane(1, 0, 0, 40, 20, title="π > a"), tty_name="/dev/pts/1"), web(2, left=41),
                 dict(gpane(3, 82, 0, 40, 20, title="π ⠋ b"), tty_name="/dev/pts/3")]
        fg = {"pts/1": (11, "omp", 5, "omp"), "pts/3": (13, "omp", 5, "omp")}
        got = self.collect(panes, fg=fg)
        self.assertEqual({p: (a.num, a.state) for p, a in got.items()}, {1: (1, "idle"), 3: (3, "working")})

    def test_a_listing_with_null_tty_names_does_not_break(self):
        self.assertEqual(self.collect([web(2), web(3, left=41)]), {})

    def test_a_local_file_page_titled_like_an_agent_is_still_no_agent(self):
        page = dict(web(2, title="π ⠋ looks like omp"), cwd="file:///home/u/slides.html", is_web=True)
        self.assertEqual(self.collect([page]), {})


class TestNumbering(unittest.TestCase):
    def test_web_panes_count_in_reading_order_like_any_grid_pane(self):
        bar = dict(gpane(9, 0, 0, 6, 40), cwd="file://h/")
        panes = [bar, gpane(1, 7, 0, 40, 20), web(2, left=48), gpane(3, 7, 21, 40, 20), web(4, left=48, top=21)]
        fg = {"pts/9": (1, "python3", 1, BAR_ARGS)}
        nums, tab_index = A._numbering(panes, fg)
        self.assertEqual(nums, {1: 1, 2: 2, 3: 3, 4: 4}, "the bar is chrome, the pages are grid panes")
        self.assertEqual(tab_index, {0: 1})

    def test_grid_panes_keep_web_panes(self):
        panes = [web(2), gpane(1, 41, 0, 40, 20)]
        self.assertEqual([p["pane_id"] for p in A._grid_panes(panes, {})], [2, 1])


class TestSpawnWeb(EnvCase):
    def test_command_line_direction_and_new_pane(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(1)
        new = A.spawn_web("https://example.com/x", run=t)
        self.assertEqual(new, 100)
        split = next(c for c in t.calls if c[0] == "split-pane" and "--move-pane-id" not in c)
        self.assertEqual(split, ("split-pane", "--pane-id", "10", "--right", "--url", "https://example.com/x"))
        self.assertEqual((t.url, t.profile), ("https://example.com/x", None))
        self.assertTrue(A.is_web(t.panes[new]))
        self.assertEqual(t.panes[new]["tab_id"], 0)

    def test_profile_flag_only_when_given(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(1)
        A.spawn_web("https://example.com", profile="work", run=t)
        split = next(c for c in t.calls if c[0] == "split-pane" and "--move-pane-id" not in c)
        self.assertEqual(split[-4:], ("--url", "https://example.com", "--profile", "work"))
        self.assertEqual(t.profile, "work")

    def test_a_tall_pane_is_split_below(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = Tiler([gpane(10, 0, 0, 60, 60, active=True)])
        A.spawn_web("https://example.com", run=t)
        split = next(c for c in t.calls if c[0] == "split-pane" and "--move-pane-id" not in c)
        self.assertEqual(split[3], "--bottom")

    def test_the_new_page_splits_the_last_pane_and_nothing_else_moves(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(4)
        before = {i: t.geo(i) for i in (1, 2, 10, 11, 12)}
        new = A.spawn_web("https://example.com", run=t)
        ids = [i for i in t.ids(0) if i not in (1, 2)]
        self.assertEqual(sorted(ids), [10, 11, 12, 13, new])
        self.assertEqual([c[0] for c in t.non_list()], ["split-pane"], "one split: no re-tile")
        first = t.non_list()[0]
        self.assertEqual(first[first.index("--pane-id") + 1], "13", "the last grid pane is split")
        self.assertEqual({i: t.geo(i) for i in before}, before)

    def test_a_web_pane_can_be_the_split_target(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(2)
        t.panes[11] = dict(t.panes[11], tty_name=None, cwd="https://example.com/")  # the second pane is a page
        self.fg = t.fg()
        new = A.spawn_web("https://other.test", run=t)
        self.assertIsNotNone(new)
        self.assertEqual(sorted(i for i in t.ids(0) if i not in (1, 2)), [10, 11, new])
        self.assertEqual(t.panes[11]["tab_id"], 0)

    def test_an_agent_started_over_a_web_pane_does_not_take_its_url_as_directory(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(1)
        t.panes[10] = dict(t.panes[10], tty_name=None, cwd="https://example.com/a/b")
        self.fg = t.fg()
        with unittest.mock.patch.dict(os.environ, {"HOME": "/home/u"}):
            A.spawn_agent(None, None, run=t)
        self.assertEqual(t.cwd, "/home/u")

    def test_failures_return_none(self):
        self.assertIsNone(A.spawn_web("https://example.com", run=lambda *a: None))
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(2)
        t.fail_splits = 5
        self.assertIsNone(A.spawn_web("https://example.com", run=t))

    def test_spawn_agent_still_sends_its_old_command_line(self):
        os.environ["HIVEGRID_ORIGIN_PANE"] = "10"
        t = self.strip(1)
        A.spawn_agent("/w", None, run=t)
        split = next(c for c in t.calls if c[0] == "split-pane" and "--move-pane-id" not in c)
        self.assertEqual(split, ("split-pane", "--pane-id", "10", "--right", "--cwd", "/w", "--",
                                 "bash", "-ic", "omp; exec bash"))


class TestWebLabel(unittest.TestCase):
    def test_host_and_path(self):
        self.assertEqual(A.web_label("https://docs.example.com/guide/intro"), "docs.example.com/guide/intro")
        self.assertEqual(A.web_label("https://example.org/"), "example.org")
        self.assertEqual(A.web_label("http://localhost:3000"), "localhost:3000")
        self.assertEqual(A.web_label("https://a.test/x?q=1#top"), "a.test/x")

    def test_hostless_urls_show_themselves_cut_to_40(self):
        self.assertEqual(A.web_label("about:blank"), "about:blank")
        self.assertEqual(len(A.web_label("data:text/html," + "x" * 60)), 40)
        self.assertEqual(A.web_label(None), "")


class TestWebBuild(unittest.TestCase):
    """web_build finds the web feature in the binaries next to the wezterm of this terminal."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        A._web_builds.clear()
        A._web_scans.clear()

    def install(self, gui=b"", cli=b""):
        for name, body in (("wezterm-gui", gui), ("wezterm", cli)):
            with open(os.path.join(self.tmp, name), "wb") as f:
                f.write(b"\x00some binary\x00" + body + b"\x00")
        return os.path.join(self.tmp, "wezterm")

    def test_the_web_build_carries_the_user_var_name(self):
        self.assertTrue(A.web_build(self.install(gui=b"hivegrid_web")))

    def test_a_build_without_it_or_without_wezterm_is_not(self):
        self.assertFalse(A.web_build(self.install(gui=b"wezterm-status-click:", cli=b"split-pane")))
        self.assertFalse(A.web_build(os.path.join(self.tmp, "missing", "wezterm")))
        with unittest.mock.patch.object(A, "find_wezterm", lambda: None):
            self.assertFalse(A.web_build())

    def test_the_cached_answer_never_blocks_and_arrives_from_a_thread(self):
        exe = self.install(gui=b"hivegrid_web")
        gate = threading.Event()
        real = A._scan_web_build

        def slow(path):
            gate.wait(5)
            return real(path)
        with unittest.mock.patch.object(A, "_scan_web_build", slow):
            self.assertIsNone(A.web_build_cached(exe), "the scan has not finished: no answer, no wait")
            self.assertIsNone(A.web_build_cached(exe), "and no second scan")
            self.assertEqual(len(A._web_scans), 1)
            gate.set()
            A._web_scans[exe].join(5)
        self.assertIs(A.web_build_cached(exe), True)
        self.assertFalse(A.web_build_cached(os.path.join(self.tmp, "missing", "wezterm")) is True)
        with unittest.mock.patch.object(A, "find_wezterm", lambda: None):
            self.assertIs(A.web_build_cached(), False)

    def test_the_answer_is_remembered(self):
        exe = self.install(gui=b"hivegrid_web")
        self.assertTrue(A.web_build(exe))
        os.unlink(os.path.join(self.tmp, "wezterm-gui"))
        self.assertTrue(A.web_build(exe))

    def test_the_fork_marker_decides_when_listed(self):
        self.assertTrue(A.is_web({"pane_id": 5, "tty_name": None, "cwd": "file:///tmp/a.html", "is_web": True}),
                        "a local page of a build with the marker")
        self.assertTrue(A.is_web({"pane_id": 5, "tty_name": None, "cwd": None, "is_web": True}))
        self.assertFalse(A.is_web({"pane_id": 5, "tty_name": "/dev/pts/3", "cwd": "file:///home/u", "is_web": False}))
        self.assertFalse(A.is_web({"pane_id": 5, "tty_name": None, "cwd": "https://a.test/", "is_web": False}),
                         "the marker wins over the guess")

    def test_ssh_and_mux_client_panes_are_no_web_panes(self):
        for pane_ in (
            {"pane_id": 7, "tty_name": None, "cwd": "file://remote-host/home/u/proj", "title": "omp"},
            {"pane_id": 7, "tty_name": None, "cwd": "file://remote-host/", "is_web": False},
            {"pane_id": 7, "cwd": "file:///srv/app"},
        ):
            with self.subTest(pane=pane_):
                self.assertFalse(A.is_web(pane_))

    def test_a_plain_shell_is_none(self):
        self.assertFalse(A.is_web({"pane_id": 1, "tty_name": "/dev/pts/1", "cwd": "file:///home/u", "is_web": False}))
        self.assertFalse(A.is_web({"pane_id": 1, "tty_name": "/dev/pts/1", "cwd": "file:///home/u"}))


# tests/data/web_urls.json is shared with tests/lua/test_web.lua: both implementations must agree.
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "web_urls.json"), encoding="utf-8") as _f:
    URL_DOC = json.load(_f)


class TestNormalizeUrl(unittest.TestCase):
    def test_the_shared_cases(self):
        self.assertGreater(len(URL_DOC["cases"]), 20)
        for case in URL_DOC["cases"]:
            search = URL_DOC["search"] if case.get("search") else ""
            with self.subTest(text=case["text"], search=bool(search)):
                url, why = A.normalize_url(case["text"], search)
                self.assertEqual(url, case["url"])
                self.assertEqual(why is None, case["url"] is not None, "a reason exactly when refused")

    def test_a_template_needs_the_placeholder(self):
        self.assertIsNone(A.normalize_url("word", "https://no-placeholder.example/")[0])


if __name__ == "__main__":
    unittest.main()
