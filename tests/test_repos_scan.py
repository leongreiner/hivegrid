"""Repository discovery and git status of the git view (bin/hivegrid_repos.py, bin/hivegrid-git),
on real repositories in a temp tree. parse_ci is covered by test_repos_ci.py."""
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


def load(name):
    spec = importlib.util.spec_from_loader(name, SourceFileLoader(name, os.path.join(BIN, name)))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GV = load("hivegrid-git")

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_AUTHOR_DATE": "2020-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2020-01-01T00:00:00Z",
}
_saved_env = {}
_home = None


def setUpModule():
    global _home
    _home = tempfile.mkdtemp()
    for k, v in {**GIT_ENV, "HOME": _home}.items():
        _saved_env[k] = os.environ.get(k)
        os.environ[k] = v


def tearDownModule():
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    shutil.rmtree(_home, ignore_errors=True)


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode not in (0, 1):
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr}")
    return r.stdout


def write(path, text="x\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def init(path, commit=True):
    os.makedirs(path, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    if commit:
        write(os.path.join(path, "README"), "readme\n")
        git(path, "add", "README")
        git(path, "commit", "-q", "-m", "init")
    return path


def tmpdir(test):
    d = os.path.realpath(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, d, True)
    return d


class FindReposTest(unittest.TestCase):
    """One workspace for all discovery tests:
    ws/                      repo (depth 0)
    ws/app, ws/app/sub       repo with a nested repo
    ws/wt                    worktree of app (`.git` is a file)
    ws/libs/a                depth 2
    ws/one/two/three         depth 3 (the default limit)
    ws/one/two/three/four    depth 4, nested in the depth-3 repo
    ws/node_modules/pkg, ws/build/out, ws/.cache/r   skipped directories
    ws/bare.git              bare repository (no work tree)
    ws/link -> libs          symlink, not followed
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = os.path.realpath(tempfile.mkdtemp())
        ws = cls.ws = os.path.join(cls.tmp, "ws")
        init(ws)
        init(os.path.join(ws, "app"))
        init(os.path.join(ws, "app", "sub"))
        git(os.path.join(ws, "app"), "worktree", "add", "-q", "-b", "wt", os.path.join(ws, "wt"))
        init(os.path.join(ws, "libs", "a"))
        init(os.path.join(ws, "one", "two", "three"), commit=False)
        init(os.path.join(ws, "one", "two", "three", "four"), commit=False)
        for skipped in ("node_modules/pkg", "build/out", ".cache/r"):
            init(os.path.join(ws, skipped), commit=False)
        git(ws, "init", "-q", "--bare", os.path.join(ws, "bare.git"))
        os.symlink(os.path.join(ws, "libs"), os.path.join(ws, "link"))
        os.makedirs(os.path.join(ws, "docs"))
        os.makedirs(os.path.join(ws, "libs", "a", "src"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def rel(self, repos):
        return [os.path.relpath(r.path, self.ws) for r in repos]

    def test_default_depth_finds_repos_up_to_three_levels_sorted(self):
        repos, truncated = R.find_repos(self.ws)
        self.assertFalse(truncated)
        self.assertEqual(self.rel(repos), [".", "app", "app/sub", "libs/a", "one/two/three", "wt"])

    def test_names_are_paths_relative_to_the_scanned_dir(self):
        repos, _ = R.find_repos(self.ws)
        self.assertEqual([r.name for r in repos], ["ws", "app", "app/sub", "libs/a", "one/two/three", "wt"])

    def test_depth_limit_boundaries(self):
        repos, _ = R.find_repos(self.ws, max_depth=4)
        self.assertIn("one/two/three/four", self.rel(repos))
        repos, _ = R.find_repos(self.ws, max_depth=1)
        self.assertEqual(self.rel(repos), [".", "app", "wt"])
        repos, _ = R.find_repos(self.ws, max_depth=0)
        self.assertEqual(self.rel(repos), ["."])

    def test_skipped_hidden_symlinked_and_bare_dirs_are_not_listed(self):
        repos, _ = R.find_repos(self.ws, max_depth=5)
        rel = self.rel(repos)
        for absent in ("node_modules/pkg", "build/out", ".cache/r", "bare.git", "link/a"):
            self.assertNotIn(absent, rel)
        self.assertEqual(len(rel), len(set(r.path for r in repos)), "a repo is listed twice")

    def test_scan_inside_a_repo_lists_the_repo_around_it(self):
        repos, _ = R.find_repos(os.path.join(self.ws, "libs", "a", "src"))
        self.assertEqual([(r.path, r.name) for r in repos], [(os.path.join(self.ws, "libs", "a"), "a")])
        repos, _ = R.find_repos(os.path.join(self.ws, "docs"))
        self.assertEqual([(r.path, r.name) for r in repos], [(self.ws, "ws")])

    def test_symlinked_root_is_resolved_without_duplicates(self):
        alias = os.path.join(self.tmp, "alias")
        os.symlink(self.ws, alias)
        self.addCleanup(os.remove, alias)
        repos, _ = R.find_repos(alias, max_depth=1)
        self.assertEqual([r.path for r in repos], [self.ws, os.path.join(self.ws, "app"), os.path.join(self.ws, "wt")])

    def test_outside_any_repo_finds_nothing(self):
        empty = tmpdir(self)
        self.assertEqual(R.find_repos(empty), ([], False))
        self.assertEqual(R.find_repos(os.path.join(empty, "missing")), ([], False))

    def test_exhausted_time_budget_reports_truncation_but_keeps_the_repo_around(self):
        repos, truncated = R.find_repos(os.path.join(self.ws, "docs"), budget=-1)
        self.assertTrue(truncated)
        self.assertEqual([r.path for r in repos], [self.ws])

    @unittest.skipIf(os.geteuid() == 0, "root reads unreadable directories")
    def test_unreadable_directory_is_skipped(self):
        root = tmpdir(self)
        init(os.path.join(root, "ok"), commit=False)
        locked = os.path.join(root, "locked")
        init(os.path.join(locked, "hidden"), commit=False)
        os.chmod(locked, 0)
        self.addCleanup(os.chmod, locked, 0o755)
        repos, _ = R.find_repos(root)
        self.assertEqual([r.name for r in repos], ["ok"])

    # ---- the git view program: HIVEGRID_GIT_DEPTH and the single-repo shortcut ----------

    def run_view(self, root, depth=None):
        lazygit = os.path.join(self.tmp, "lazygit")
        if not os.path.exists(lazygit):
            write(lazygit, '#!/bin/sh\necho "LAZYGIT $(pwd)"\n')
            os.chmod(lazygit, 0o755)
        env = {k: v for k, v in os.environ.items() if not k.startswith("WEZTERM")}
        env.update(HIVEGRID_LAZYGIT=lazygit, TMPDIR=self.tmp, XDG_CONFIG_HOME=os.path.join(_home, ".config"),
                   PYTHONDONTWRITEBYTECODE="1")
        env.pop("HIVEGRID_GIT_DEPTH", None)
        if depth is not None:
            env["HIVEGRID_GIT_DEPTH"] = depth
        r = subprocess.run([sys.executable, os.path.join(BIN, "hivegrid-git"), root], env=env, capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

    def test_view_depth_comes_from_environment(self):
        ws = self.ws
        self.assertEqual(self.run_view(ws, "1"), [ws, os.path.join(ws, "app"), os.path.join(ws, "wt")])
        default = self.run_view(ws)
        self.assertIn(os.path.join(ws, "one", "two", "three"), default)
        self.assertNotIn(os.path.join(ws, "one", "two", "three", "four"), default)
        self.assertEqual(self.run_view(ws, "not-a-number"), default)

    def test_view_with_one_repo_opens_lazygit_there(self):
        self.assertEqual(self.run_view(os.path.join(self.ws, "libs", "a", "src")),
                         ["LAZYGIT " + os.path.join(self.ws, "libs", "a")])


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.root = tmpdir(self)
        self.repo = init(os.path.join(self.root, "repo"))

    def status(self, path=None):
        return R.status(R.Repo(path or self.repo, "repo"))

    def dot(self, st):
        return R.dot(st)[1]

    def conflict(self, path):
        """Two branches change README differently; returns with `git merge other` stopped on it."""
        git(path, "checkout", "-q", "-b", "other")
        write(os.path.join(path, "README"), "theirs\n")
        git(path, "commit", "-qam", "theirs")
        git(path, "checkout", "-q", "main")
        write(os.path.join(path, "README"), "ours\n")
        git(path, "commit", "-qam", "ours")

    def test_clean_repo_without_upstream_is_green_with_no_sync_counts(self):
        st = self.status()
        self.assertEqual(st["branch"], "main")
        self.assertFalse(st["detached"])
        self.assertIsNone(st["ahead"])
        self.assertIsNone(st["behind"])
        self.assertEqual((st["staged"], st["changed"], st["untracked"], st["conflicts"]), (0, 0, 0, 0))
        self.assertIsNone(st["op"])
        self.assertEqual(self.dot(st), "green")

    def test_repo_without_commits(self):
        repo = init(os.path.join(self.root, "fresh"), commit=False)
        write(os.path.join(repo, "new"))
        st = self.status(repo)
        self.assertEqual(st["branch"], "main (no commits)")
        self.assertEqual(st["untracked"], 1)
        self.assertEqual(self.dot(st), "amber")

    def test_change_counts(self):
        write(os.path.join(self.repo, "staged"))
        write(os.path.join(self.repo, "both"))
        git(self.repo, "add", "staged", "both")
        write(os.path.join(self.repo, "both"), "changed again\n")  # staged and modified: counts twice
        write(os.path.join(self.repo, "README"), "edited\n")
        write(os.path.join(self.repo, "untracked"))
        write(os.path.join(self.repo, "dir", "also_untracked"))
        st = self.status()
        self.assertEqual((st["staged"], st["changed"], st["untracked"], st["conflicts"]), (2, 2, 2, 0))
        self.assertEqual(self.dot(st), "amber")

    def test_staged_rename_counts_as_one_staged_change(self):
        git(self.repo, "mv", "README", "README.md")
        st = self.status()
        self.assertEqual((st["staged"], st["changed"]), (1, 0))

    def test_ahead_and_behind_upstream(self):
        origin = os.path.join(self.root, "origin.git")
        git(self.root, "clone", "-q", "--bare", self.repo, origin)
        git(self.repo, "remote", "add", "origin", origin)
        git(self.repo, "fetch", "-q", "origin")
        git(self.repo, "branch", "-q", "-u", "origin/main")
        st = self.status()
        self.assertEqual((st["ahead"], st["behind"]), (0, 0))
        other = os.path.join(self.root, "other")
        git(self.root, "clone", "-q", origin, other)
        write(os.path.join(other, "theirs"))
        git(other, "add", "theirs")
        git(other, "commit", "-qm", "theirs")
        git(other, "push", "-q", "origin", "main")
        for n in (1, 2):
            write(os.path.join(self.repo, f"mine{n}"))
            git(self.repo, "add", f"mine{n}")
            git(self.repo, "commit", "-qm", f"mine {n}")
        git(self.repo, "fetch", "-q", "origin")
        st = self.status()
        self.assertEqual((st["ahead"], st["behind"]), (2, 1))
        self.assertEqual(self.dot(st), "green", "commits to push are not working-tree changes")

    def test_detached_head_shows_short_commit(self):
        sha = git(self.repo, "rev-parse", "HEAD").strip()
        git(self.repo, "checkout", "-q", "--detach")
        st = self.status()
        self.assertTrue(st["detached"])
        self.assertEqual(st["branch"], "@" + sha[:7])
        self.assertIsNone(st["ahead"])

    def test_merge_conflict_is_red_and_merging(self):
        self.conflict(self.repo)
        git(self.repo, "merge", "-q", "other")
        write(os.path.join(self.repo, "untracked"))
        st = self.status()
        self.assertEqual(st["conflicts"], 1)
        self.assertEqual(st["op"], "merging")
        self.assertEqual(self.dot(st), "red", "conflicts outrank other changes")

    def test_rebase_conflict_is_rebasing_not_merging(self):
        self.conflict(self.repo)
        git(self.repo, "rebase", "-q", "other")
        st = self.status()
        self.assertEqual(st["conflicts"], 1)
        self.assertEqual(st["op"], "rebasing")

    def test_operation_in_a_worktree_follows_the_git_file(self):
        wt = os.path.join(self.root, "wt")
        git(self.repo, "worktree", "add", "-q", "-b", "side", wt)
        self.assertTrue(os.path.isfile(os.path.join(wt, ".git")))
        self.conflict(self.repo)  # makes branch `other` changing README
        write(os.path.join(wt, "README"), "side\n")
        git(wt, "commit", "-qam", "side")
        git(wt, "merge", "-q", "other")
        st = self.status(wt)
        self.assertEqual(st["op"], "merging")
        self.assertIsNone(self.status()["op"], "the main work tree is not merging")

    def test_vanished_repo_is_an_error_not_a_crash(self):
        gone = os.path.join(self.root, "gone")
        init(gone, commit=False)
        repo = R.Repo(gone, "gone")
        shutil.rmtree(gone)
        self.assertEqual(R.status(repo), {"error": True})
        self.assertEqual(R.dot(repo.status), ("✕", "red"))

    def test_broken_nested_repo_is_an_error_not_the_outer_repos_state(self):
        # e.g. an interrupted clone: listed (it holds `.git`) but unreadable; git must not walk
        # up and report the repository around it instead
        for broken in ("empty", "garbage"):
            path = os.path.join(self.repo, broken)
            if broken == "empty":
                os.makedirs(os.path.join(path, ".git"))
            else:
                write(os.path.join(path, ".git"), "not a gitdir line\n")
            with self.subTest(broken=broken):
                self.assertIn(path, [r.path for r in R.find_repos(self.repo)[0]])
                self.assertEqual(self.status(path), {"error": True})

    def test_status_not_read_yet_is_neutral(self):
        self.assertEqual(R.dot(None)[1], "muted")


class ParseStatusTest(unittest.TestCase):
    def test_detached_without_commits(self):
        st = R.parse_status("# branch.oid (initial)\n# branch.head (detached)\n")
        self.assertTrue(st["detached"])
        self.assertEqual(st["branch"], "(detached)")

    def test_gone_upstream_has_no_sync_counts(self):
        # an upstream that was deleted on the remote: git prints branch.upstream but no branch.ab
        st = R.parse_status("# branch.oid abc\n# branch.head main\n# branch.upstream origin/main\n")
        self.assertIsNone(st["ahead"])


if __name__ == "__main__":
    unittest.main()
