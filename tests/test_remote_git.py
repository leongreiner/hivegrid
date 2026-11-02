"""hivegrid-remote: the git overview of an agent's folder (bin/hivegrid_remote_git.py, GET /api/agents/<id>/git).
Real git in temp repositories with fake data; no network, no real home."""
import os
import subprocess
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import hivegrid_remote as R  # noqa: E402
import hivegrid_remote_git as G  # noqa: E402
import test_remote as TR  # noqa: E402

ENV = dict(os.environ, GIT_AUTHOR_NAME="Ann Author", GIT_AUTHOR_EMAIL="a@example.test", GIT_COMMITTER_NAME="Ann Author",
           GIT_COMMITTER_EMAIL="a@example.test", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")


def git(path, *args):
    subprocess.run(["git", "-C", path, *args], check=True, capture_output=True, env=ENV)


class GitCase(TR.AppCase):
    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.home, "proj")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        self.commit("first.txt", "one", "first commit")
        self.commit("second.txt", "two", "second \x1b[31mcommit\x1b[0m")
        self.write_state(3, state="idle", cwd=self.repo)
        self.clock.t += 5

    def commit(self, name, text, message):
        with open(os.path.join(self.repo, name), "w") as f:
            f.write(text)
        git(self.repo, "add", name)
        git(self.repo, "commit", "-q", "-m", message)

    def overview(self, query="", ident="3"):
        return self.call("GET", f"/api/agents/{ident}/git{query}")


class TestOverview(GitCase):
    def test_branch_changes_and_commits_of_the_agents_folder(self):
        with open(os.path.join(self.repo, "first.txt"), "w") as f:
            f.write("changed")
        with open(os.path.join(self.repo, "new.txt"), "w") as f:
            f.write("x")
        r, data = self.overview()
        self.assertEqual(r.status, 200)
        repo = data["repos"][0]
        self.assertEqual((repo["name"], repo["branch"], repo["changed"], repo["untracked"], repo["staged"], repo["dot"]),
                         ("proj", "main", 1, 1, 0, "amber"))
        self.assertIsNone(repo["ahead"], "no upstream")
        files = {f["path"]: f["kind"] for f in data["detail"]["files"]}
        self.assertEqual(files, {"first.txt": "modified", "new.txt": "untracked"})
        self.assertEqual([c["subject"] for c in data["detail"]["commits"]], ["second commit", "first commit"],
                         "newest first, escape sequences gone")
        self.assertEqual(data["detail"]["commits"][0]["author"], "Ann Author")

    def test_a_clean_repository_has_no_files(self):
        _, data = self.overview()
        self.assertEqual((data["repos"][0]["dot"], data["detail"]["files"], data["detail"]["files_more"]), ("green", [], False))

    def test_a_folder_outside_any_repository_says_so(self):
        plain = os.path.join(self.home, "plain")
        os.makedirs(plain)
        self.write_state(3, state="idle", cwd=plain)
        self.clock.t += 5
        r, data = self.overview()
        self.assertEqual((r.status, data["repos"], data["detail"]), (200, [], None))

    def test_a_folder_that_is_gone_is_not_an_error(self):
        self.write_state(3, state="idle", cwd=os.path.join(self.home, "gone"))
        self.clock.t += 5
        self.assertEqual(self.overview()[1]["repos"], [])

    def test_nested_repositories_are_listed_and_picked_by_number(self):
        inner = os.path.join(self.repo, "inner")
        os.makedirs(inner)
        git(inner, "init", "-q", "-b", "dev")
        with open(os.path.join(inner, "a.txt"), "w") as f:
            f.write("a")
        git(inner, "add", "a.txt")
        git(inner, "commit", "-q", "-m", "inner commit")
        _, data = self.overview()
        self.assertEqual([r["name"] for r in data["repos"]], ["proj", "inner"])
        _, picked = self.overview("?repo=1")
        self.assertEqual((picked["open"], picked["detail"]["commits"][0]["subject"]), (1, "inner commit"))

    def test_the_phone_names_no_path(self):
        for q in ("?repo=../../etc", "?repo=-1", "?repo=x"):
            self.assertEqual(self.overview(q)[0].status, 400, q)
        self.assertEqual(self.overview("?repo=99")[1]["open"], 0, "a number past the list opens the first")

    def test_the_file_list_is_capped(self):
        for i in range(G.MAX_FILES + 5):
            with open(os.path.join(self.repo, f"f{i:03}.txt"), "w") as f:
                f.write("x")
        _, data = self.overview()
        d = data["detail"]
        self.assertEqual((len(d["files"]), d["files_total"], d["files_more"]), (G.MAX_FILES, G.MAX_FILES + 5, True))

    def test_a_file_name_with_control_characters_is_cleaned(self):
        with open(os.path.join(self.repo, "bad\x1b[2Jname.txt"), "w") as f:
            f.write("x")
        r, data = self.overview()
        self.assertNotIn(b"\\u001b", r.body)
        self.assertEqual(len(data["detail"]["files"]), 1)
        self.assertNotIn("\x1b", data["detail"]["files"][0]["path"])

    def test_the_answer_is_cached_for_a_moment_and_has_an_etag(self):
        r, _ = self.overview()
        etag = r.headers["ETag"]
        self.assertEqual(self.call("GET", "/api/agents/3/git", headers={"if-none-match": etag})[0].status, 304)
        self.commit("third.txt", "3", "third")
        self.assertEqual(len(self.overview()[1]["detail"]["commits"]), 2, "served from the short cache")
        self.clock.t += G.TTL + 1
        self.assertEqual(len(self.overview()[1]["detail"]["commits"]), 3)


class TestAccess(GitCase):
    def test_unknown_agent_method_and_token(self):
        self.assertEqual(self.overview(ident="9")[0].status, 404)
        self.assertEqual(self.call("POST", "/api/agents/3/git", {})[0].status, 405)
        self.assertEqual(self.call("GET", "/api/agents/3/git", token=None)[0].status, 401)

    def test_a_read_only_server_shows_it_too(self):
        app = self.make_app(readonly=True)
        r, data = self.call("GET", "/api/agents/3/git", app=app)
        self.assertEqual((r.status, data["repos"][0]["branch"]), (200, "main"))


class TestParsers(unittest.TestCase):
    def test_status_lines(self):
        text = ("1 .M N... 100644 100644 100644 a b src/x.py\n"
                "1 A. N... 000000 100644 100644 a b new.py\n"
                "2 R. N... 100644 100644 100644 a b R100 renamed.py\told.py\n"
                "u UU N... 1 2 3 4 a b c conflict.py\n"
                "? loose.txt\n")
        got = [(f["path"], f["staged"], f["worktree"], f["kind"]) for f in G.parse_files(text)]
        self.assertEqual(got, [("src/x.py", ".", "M", "modified"), ("new.py", "A", ".", "added"),
                               ("renamed.py", "R", ".", "renamed"), ("conflict.py", "U", "U", "conflict"),
                               ("loose.txt", ".", "?", "untracked")])

    def test_log_lines_that_are_not_commits_are_skipped(self):
        us = "\x1f"
        got = G.parse_log(f"abc123{us}Ann{us}1700000000{us}subject with {us} inside\nnoise\nx{us}y{us}notanumber{us}z\n")
        self.assertEqual([(c["sha"], c["subject"]) for c in got], [("abc123", f"subject with {us} inside")])


if __name__ == "__main__":
    unittest.main()
