import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

BIN = os.path.join(os.path.dirname(__file__), "..", "bin")
sys.path.insert(0, BIN)

import hivegrid_repos as R  # noqa: E402


def load_git_view():
    loader = importlib.machinery.SourceFileLoader("hivegrid_git", os.path.join(BIN, "hivegrid-git"))
    spec = importlib.util.spec_from_loader("hivegrid_git", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


G = load_git_view()


def runs(*items):
    return json.dumps([{"workflowName": w, "status": s, "conclusion": c} for w, s, c in items])


class ParseCi(unittest.TestCase):
    def test_only_latest_run_of_each_workflow_counts(self):
        # newest first: the old failure of "test" was fixed by a newer green run
        self.assertEqual(R.parse_ci(runs(("test", "completed", "success"), ("test", "completed", "failure"))), "pass")

    def test_any_failing_workflow_fails_the_repo(self):
        self.assertEqual(R.parse_ci(runs(("lint", "completed", "success"), ("test", "completed", "timed_out"))), "fail")

    def test_failure_beats_running_beats_pass(self):
        self.assertEqual(R.parse_ci(runs(("a", "in_progress", ""), ("b", "completed", "success"))), "running")
        self.assertEqual(R.parse_ci(runs(("a", "in_progress", ""), ("b", "completed", "failure"))), "fail")

    def test_skipped_does_not_mask_a_pass(self):
        self.assertEqual(R.parse_ci(runs(("a", "completed", "skipped"), ("b", "completed", "success"))), "pass")
        self.assertEqual(R.parse_ci(runs(("a", "completed", "cancelled"))), "other")

    def test_no_runs_and_garbage(self):
        self.assertEqual(R.parse_ci("[]"), "none")
        self.assertIsNone(R.parse_ci("not json"))
        self.assertIsNone(R.parse_ci("{}"))


class ParseStatus(unittest.TestCase):
    def test_counts_and_upstream(self):
        s = R.parse_status("# branch.oid abc\n# branch.head main\n# branch.upstream origin/main\n# branch.ab +2 -3\n"
                           "1 M. N... 100644 100644 100644 a b f1\n1 .M N... 100644 100644 100644 a b f2\n"
                           "u UU N... 1 2 3 4 a b c f3\n? new\n")
        self.assertEqual((s["branch"], s["ahead"], s["behind"]), ("main", 2, 3))
        self.assertEqual((s["staged"], s["changed"], s["conflicts"], s["untracked"]), (1, 1, 1, 1))

    def test_detached_and_unborn(self):
        self.assertEqual(R.parse_status("# branch.oid 1234567890\n# branch.head (detached)\n")["branch"], "@1234567")
        s = R.parse_status("# branch.oid (initial)\n# branch.head main\n")
        self.assertEqual((s["branch"], s["ahead"]), ("main (no commits)", None))


class Operation(unittest.TestCase):
    def test_markers_in_git_dir_and_gitdir_file(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "a", ".git"))
            self.assertIsNone(R.operation(os.path.join(d, "a")))
            open(os.path.join(d, "a", ".git", "MERGE_HEAD"), "w").close()
            self.assertEqual(R.operation(os.path.join(d, "a")), "merging")
            os.makedirs(os.path.join(d, "a", ".git", "rebase-merge"))
            self.assertEqual(R.operation(os.path.join(d, "a")), "rebasing")
            # a submodule or worktree: .git is a file pointing at the real git directory
            os.makedirs(os.path.join(d, "real"))
            open(os.path.join(d, "real", "CHERRY_PICK_HEAD"), "w").close()
            os.makedirs(os.path.join(d, "b"))
            with open(os.path.join(d, "b", ".git"), "w") as f:
                f.write("gitdir: ../real\n")
            self.assertEqual(R.operation(os.path.join(d, "b")), "cherry-picking")

    def test_status_of_a_real_merge_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                       GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")

            def commit(text):
                with open(os.path.join(d, "f"), "w") as f:
                    f.write(text)
                git("add", "f")
                git("commit", "-q", "-m", text)

            def git(*args):
                subprocess.run(["git", "-C", d, *args], env=env, capture_output=True, check=args[0] != "merge")

            git("init", "-q", "-b", "main")
            commit("base")
            git("checkout", "-q", "-b", "other")
            commit("theirs")
            git("checkout", "-q", "main")
            commit("ours")
            git("merge", "-q", "other")
            st = R.status(R.Repo(d, "x"))
            self.assertEqual((st["op"], st["conflicts"]), ("merging", 1))


class Layout(unittest.TestCase):
    """Rows of the git view: two lines and a blank when every repository fits, else two lines."""

    def view(self, n, rows, truncated=False):
        v = G.View.__new__(G.View)
        v.repos = [R.Repo(f"/r{i}", f"r{i}") for i in range(n)]
        v.truncated, v.size = truncated, (40, rows)
        return v

    def test_blank_line_between_rows_only_when_all_fit(self):
        self.assertEqual((self.view(4, 14).pitch(), self.view(4, 14).page()), (3, 4))  # 11 rows = 4 * 3 - 1
        self.assertEqual((self.view(4, 13).pitch(), self.view(4, 13).page()), (2, 5))
        self.assertEqual(self.view(4, 14, truncated=True).pitch(), 2)  # the scan note takes a row
        self.assertEqual(self.view(30, 13).page(), 5)


class CiCadence(unittest.TestCase):
    def test_only_rows_in_view_and_running_runs_are_read_often(self):
        r = R.Repo("/x", "x")
        self.assertEqual(G.ci_interval(r, True), G.CI_EVERY)
        self.assertEqual(G.ci_interval(r, False), G.CI_HIDDEN_EVERY)
        r.ci = "running"
        self.assertEqual(G.ci_interval(r, False), G.CI_RUNNING_EVERY)

    def test_refresher_reads_due_repositories_rows_in_view_first(self):
        seen = []
        rf = G.Refresher(seen.append, lambda repo, in_view: 60.0)
        repos = [R.Repo(f"/{n}", n) for n in "abc"]
        rf.at["/b"] = 100.0  # read 10 s ago: not due
        rf.kick(repos, {"/c"}, 110.0)
        while rf.busy:
            pass
        self.assertEqual(sorted(r.path for r in seen), ["/a", "/c"])


class LazygitTheme(unittest.TestCase):
    def test_theme_is_yaml_with_quoted_hex_colours(self):
        pal = {k: "#123456" for k in ("accent", "edge_hi", "amber", "muted", "raised", "red", "text")}
        text = G.lazygit_theme(pal)
        self.assertIn('activeBorderColor: ["#123456", "bold"]', text)
        self.assertIn("showRandomTip: false", text)


if __name__ == "__main__":
    unittest.main()
