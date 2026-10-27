"""The overview dock's git tag: hivegrid_repos.Where resolves branch, worktree and the changes dot of a
pane's directory on real repositories in a temp tree; hivegrid-overview draws it on the second line."""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin")
sys.dont_write_bytecode = True  # bin/ is copied wholesale by install.sh: no __pycache__ there
sys.path.insert(0, BIN)
import hivegrid_repos as R  # noqa: E402
import hivegrid_ui as U  # noqa: E402


def load(name):
    spec = importlib.util.spec_from_loader(name, SourceFileLoader(name, os.path.join(BIN, name)))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


D = load("hivegrid-overview")
NOW = 1_000_000.0
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_AUTHOR_DATE": "2020-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2020-01-01T00:00:00Z",
}
_saved = {}


def setUpModule():
    for k, v in GIT_ENV.items():
        _saved[k] = os.environ.get(k)
        os.environ[k] = v


def tearDownModule():
    for k, v in _saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr}")
    return r.stdout.strip()


def write(path, text="x\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class WhereTest(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        write(os.path.join(self.repo, "README"))
        git(self.repo, "add", "README")
        git(self.repo, "commit", "-q", "-m", "init")
        self.clock = Clock()
        self.where = R.Where(background=False, clock=self.clock)

    def test_plain_repo_reports_branch_and_a_clean_dot(self):
        self.assertEqual(self.where(self.repo), {"branch": "main", "detached": False, "worktree": None, "dot": "green"})

    def test_a_subdirectory_resolves_to_its_repository(self):
        os.makedirs(os.path.join(self.repo, "src", "deep"))
        self.assertEqual(self.where(os.path.join(self.repo, "src", "deep"))["branch"], "main")

    def test_uncommitted_changes_are_amber_and_committed_ones_are_not(self):
        write(os.path.join(self.repo, "new"))
        self.clock.now += 100
        self.assertEqual(self.where(self.repo)["dot"], "amber")
        git(self.repo, "add", "new")
        git(self.repo, "commit", "-q", "-m", "more")
        self.clock.now += 100
        self.assertEqual(self.where(self.repo)["dot"], "green")

    def test_linked_worktree_names_the_worktree_directory_not_the_repository(self):
        wt = os.path.join(self.tmp, "feature-tree")
        git(self.repo, "worktree", "add", "-q", "-b", "feature/x", wt)
        os.makedirs(os.path.join(wt, "sub"))
        for cwd in (wt, os.path.join(wt, "sub")):
            got = self.where(cwd)
            self.assertEqual((got["branch"], got["worktree"], got["detached"]), ("feature/x", "feature-tree", False))
        self.assertIsNone(self.where(self.repo)["worktree"], "the main work tree is not a linked worktree")
        self.assertEqual(self.where(self.repo)["branch"], "main")

    def test_worktree_dot_is_its_own(self):
        wt = os.path.join(self.tmp, "wt")
        git(self.repo, "worktree", "add", "-q", "-b", "b", wt)
        write(os.path.join(wt, "scratch"))
        self.assertEqual(self.where(wt)["dot"], "amber")
        self.assertEqual(self.where(self.repo)["dot"], "green")

    def test_detached_head_shows_the_short_commit(self):
        sha = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "checkout", "-q", "--detach")
        got = self.where(self.repo)
        self.assertEqual((got["branch"], got["detached"]), ("@" + sha[:7], True))

    def test_unborn_branch(self):
        fresh = os.path.join(self.tmp, "fresh")
        os.makedirs(fresh)
        git(fresh, "init", "-q", "-b", "trunk")
        self.assertEqual(self.where(fresh)["branch"], "trunk")

    def test_conflicts_are_red(self):
        git(self.repo, "checkout", "-q", "-b", "other")
        write(os.path.join(self.repo, "README"), "theirs\n")
        git(self.repo, "commit", "-qam", "theirs")
        git(self.repo, "checkout", "-q", "main")
        write(os.path.join(self.repo, "README"), "ours\n")
        git(self.repo, "commit", "-qam", "ours")
        subprocess.run(["git", "merge", "other"], cwd=self.repo, capture_output=True)
        self.assertEqual(self.where(self.repo)["dot"], "red")

    def test_outside_a_repository_and_unusable_directories_give_none(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        for cwd in (plain, "", "relative/dir", "example.com", os.path.join(self.tmp, "missing")):
            self.assertIsNone(self.where(cwd), cwd)

    def test_a_broken_git_file_gives_none(self):
        broken = os.path.join(self.tmp, "broken")
        write(os.path.join(broken, ".git"), "gitdir: /nonexistent/place\n")
        self.assertIsNone(self.where(broken))

    def test_home_is_expanded(self):
        old = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp
        self.addCleanup(lambda: os.environ.__setitem__("HOME", old) if old else os.environ.pop("HOME", None))
        self.assertEqual(self.where("~/repo")["branch"], "main")

    def test_answers_are_cached_between_redraws(self):
        self.where(self.repo)
        git(self.repo, "checkout", "-q", "-b", "next")
        self.clock.now += 1.0
        self.assertEqual(self.where(self.repo)["branch"], "main", "within the ttl nothing is read again")
        self.clock.now += 5.0
        self.assertEqual(self.where(self.repo)["branch"], "next")

    def test_no_git_process_for_a_cached_answer(self):
        self.where(self.repo)
        calls = []
        real = R.subprocess.run
        R.subprocess.run = lambda *a, **k: calls.append(a) or real(*a, **k)
        self.addCleanup(setattr, R.subprocess, "run", real)
        for _ in range(20):
            self.clock.now += 0.05
            self.where(self.repo)
        self.assertEqual(calls, [])

    def test_the_dot_is_read_off_the_calling_thread(self):
        where = R.Where(clock=self.clock)
        first = where(self.repo)
        self.assertEqual(first["branch"], "main")
        for _ in range(200):  # the daemon thread lands its answer
            if where(self.repo)["dot"]:
                break
            import time
            time.sleep(0.02)
        self.assertEqual(where(self.repo)["dot"], "green")

    def test_a_failing_status_read_does_not_leave_the_root_busy(self):
        where = R.Where(background=False, clock=self.clock)
        real = R.status
        R.status = lambda repo: (_ for _ in ()).throw(RuntimeError("boom"))
        self.addCleanup(setattr, R, "status", real)
        with self.assertRaises(RuntimeError):
            where(self.repo)
        R.status = real
        self.clock.now += 100
        self.assertEqual(where(self.repo)["dot"], "green")


class TagTest(unittest.TestCase):
    PAL = dict(U.DEFAULT_PAL)

    def tag(self, g, room):
        segs, width = D.git_tag(self.PAL, g, room)
        self.assertEqual(width, sum(U.text_width(t) for t, _ in segs))
        self.assertLessEqual(width, max(room, 0))
        return "".join(t for t, _ in segs), [c for _, c in segs]

    def test_branch_dot_and_worktree_fit(self):
        g = {"branch": "feature/x", "worktree": "wt", "dot": "amber"}
        text, colors = self.tag(g, 40)
        self.assertEqual(text, "● feature/x  ◇ wt")
        self.assertEqual(colors, [self.PAL["amber"], self.PAL["cyan"], self.PAL["muted"]])

    def test_narrowing_drops_from_the_end(self):
        g = {"branch": "feature/x", "worktree": "wt", "dot": "green"}
        self.assertEqual(self.tag(g, 16)[0], "● feature/x ◇")  # the name goes first, the marker stays
        self.assertEqual(self.tag(g, 11)[0], "● feature/x")  # then the marker
        self.assertEqual(self.tag(g, 8)[0], "● featu…")  # then the branch is cut
        self.assertEqual(self.tag(g, 5)[0], "●")  # a branch under 4 cells is not worth showing
        self.assertEqual(self.tag(g, 0)[0], "")

    def test_a_long_branch_is_cut_before_the_worktree_marker_goes(self):
        g = {"branch": "feature/login-flow-redesign", "worktree": "wt", "dot": "amber"}
        self.assertEqual(self.tag(g, 16)[0], "● feature/log… ◇")  # the branch is cut beside the marker
        self.assertEqual(self.tag(g, 12)[0], "● feature/l…")  # under 10 cells left for the branch: the marker goes
        self.assertEqual(self.tag(g, 9)[0], "● featur…")

    def test_no_dot_until_read_and_none_outside_a_repository(self):
        self.assertEqual(self.tag({"branch": "main", "worktree": None, "dot": None}, 20)[0], "main")
        self.assertEqual(self.tag(None, 20)[0], "")


def pane(i, cwd, git_info=None, **kw):
    return dict({"id": i, "num": i, "active": i == 1, "kind": "idle", "name": os.path.basename(cwd), "cwd": cwd,
                 "proc": "omp", "since": NOW - 90, "text": "idle", "title": "", "reason": "", "git": git_info}, **kw)


class DockRenderTest(unittest.TestCase):
    def render(self, panes, w, h=14):
        return D.dump(D.render({"panes": panes}, w, h, NOW, lambda i: 0, lambda: 0, lambda: 0))

    def test_tag_sits_on_the_second_line_and_never_on_the_title_or_buttons(self):
        panes = [pane(1, "/p/app", {"branch": "main", "worktree": None, "dot": "green"}, title="Fix the build"),
                 pane(2, "/p/app-wt", {"branch": "feature/x", "worktree": "app-wt", "dot": "amber"}, title="Other"),
                 pane(3, "/tmp/plain", None, title="Notes")]
        rows = self.render(panes, 40)
        body = [r for r in rows if r.strip()]
        text = "\n".join(rows)
        self.assertIn("● main", text)
        self.assertIn("● feature/x  ◇ app-wt", text)
        first = next(r for r in rows if "Fix the build" in r)
        self.assertTrue(first.rstrip().endswith("idle 1m"), first)
        self.assertNotIn("main", first)
        plain = rows[rows.index(next(r for r in rows if "Notes" in r)) + 1]
        self.assertNotIn("●", plain, "a pane outside a repository has no tag")
        self.assertTrue(body)

    def test_tag_keeps_part_of_the_second_line_text_and_cuts_from_the_end(self):
        g = {"branch": "feature/very-long-branch-name", "worktree": "wt", "dot": "green"}
        panes = [pane(1, "/p/app", g, kind="working", text="working", reason="bash: npm test (12s)", title="Fix")]
        for w in (60, 44, 36, 30, 24, 20):
            rows = self.render(panes, w)
            sub = rows[rows.index(next(r for r in rows if "Fix" in r)) + 1]
            self.assertLessEqual(U.text_width(sub), w - 1, (w, sub))
            self.assertIn("bash:", sub, w)  # the second line's own text is never pushed out
            self.assertEqual("●" in sub, w >= 24, (w, sub))
        wide = self.render(panes, 60)
        self.assertIn("◇ wt", "\n".join(wide))
        self.assertNotIn("◇", "\n".join(self.render(panes, 24)))

    def test_one_line_rows_carry_no_tag(self):
        panes = [pane(i, f"/p/{i}", {"branch": "main", "worktree": None, "dot": "green"}) for i in range(1, 11)]
        self.assertNotIn("main", "\n".join(self.render(panes, 40, 14)))

    def test_with_git_skips_web_panes_and_asks_once_per_pane(self):
        asked = []
        doc = {"panes": [pane(1, "/p/a"), pane(2, "example.com", kind="web")]}
        out = D.with_git(doc, lambda cwd: asked.append(cwd) or {"branch": "b", "worktree": None, "dot": None})
        self.assertEqual(asked, ["/p/a"])
        self.assertEqual(out["panes"][0]["git"]["branch"], "b")
        self.assertEqual(out["panes"][1]["git"], None)
        self.assertIsNone(D.with_git(None, lambda cwd: None))


if __name__ == "__main__":
    unittest.main()
