"""The git overview of one agent's working directory for the phone page (`GET /api/agents/<id>/git`).

The data is the host git view's (hivegrid_repos.py: the repository around the directory and those below it, each with
its branch, distance to its upstream and change counts, the operation in progress and the CI result of the checked-out
branch), plus for the repository the page opens its changed files and recent commits. Read only: git runs with a
timeout, no prompt and no index lock, never fetches, never writes. The phone names no path: the directory is the
agent's own, and a repository is picked by its number in the list the server made. Text is sanitized by the caller's
`clean(text, limit)`; every list is capped.
"""
import os
import threading
import time

import hivegrid_repos as R

MAX_REPOS = 12
MAX_FILES = 60
MAX_COMMITS = 10
FIND_DEPTH = 2
FIND_BUDGET = 1.0              # seconds the walk for nested repositories may take
GIT_TIMEOUT = 6                # seconds one git call may take
TTL = 4.0                      # an overview is reused this long (a refresh button cannot hammer git)
CI_EVERY = 120.0               # seconds between two reads of a repository's CI result (a GitHub API call)
CACHE_MAX = 64
NAME_MAX, PATH_MAX, SUBJECT_MAX = 80, 200, 160
KINDS = {"M": "modified", "T": "typechange", "A": "added", "D": "deleted", "R": "renamed", "C": "copied", "U": "conflict"}
_FIELD = "\x1f"


def parse_files(text):
    """[{path, staged, worktree, kind}] from `git status --porcelain=v2`: `staged` and `worktree` are the XY letters
    ('.' = unchanged there); `kind` is `untracked`, `conflict` or the word for the first changed letter. A renamed
    file shows its new path."""
    out = []
    for line in text.splitlines():
        if line.startswith("? "):
            out.append({"path": line[2:], "staged": ".", "worktree": "?", "kind": "untracked"})
        elif line.startswith("u "):
            parts = line.split(" ", 10)
            if len(parts) == 11:
                out.append({"path": parts[10], "staged": "U", "worktree": "U", "kind": "conflict"})
        elif line[:2] in ("1 ", "2 "):
            parts = line.split(" ", 8 if line[0] == "1" else 9)
            if len(parts) < (9 if line[0] == "1" else 10):
                continue
            xy, path = parts[1], parts[-1].split("\t")[0]
            letter = xy[0] if xy[0] != "." else xy[1]
            out.append({"path": path, "staged": xy[0], "worktree": xy[1], "kind": KINDS.get(letter, "modified")})
    return out


def parse_log(text):
    """[{sha, author, ts, subject}] from `git log --format=%h<US>%an<US>%ct<US>%s`."""
    out = []
    for line in text.splitlines():
        parts = line.split(_FIELD, 3)
        if len(parts) == 4 and parts[2].isdigit():
            out.append({"sha": parts[0], "author": parts[1], "ts": int(parts[2]), "subject": parts[3]})
    return out


class Overview:
    """Reads and caches overviews. `clean(text, limit)` is the page's line sanitizer; `slots` bounds the git
    subprocesses (a semaphore shared with the other `wezterm cli` and nvidia-smi calls); `clock` is injectable."""

    def __init__(self, clean, slots=None, clock=time.monotonic, wall=time.time, git=R._git, background=True):
        self.clean, self.slots, self.clock, self.wall, self.git, self.background = clean, slots, clock, wall, git, background
        self._lock = threading.Lock()
        self._cache = {}               # (cwd, repo index) -> (read at, document)
        self._ci = {}                  # repo path -> (read at, state, reading)

    # -- git
    def _run(self, path, *args):
        if self.slots is not None:
            with self.slots:
                return self.git(path, *args, timeout=GIT_TIMEOUT, here=True)
        return self.git(path, *args, timeout=GIT_TIMEOUT, here=True)

    def _repos(self, cwd):
        if self.slots is not None:
            with self.slots:
                repos, _truncated = R.find_repos(cwd, max_depth=FIND_DEPTH, budget=FIND_BUDGET)
        else:
            repos, _truncated = R.find_repos(cwd, max_depth=FIND_DEPTH, budget=FIND_BUDGET)
        return repos[:MAX_REPOS]

    def _ci_state(self, repo):
        """The CI result last read for `repo` (None: no workflows, or not read yet). A stale entry is read again on a
        thread of its own: the request never waits for GitHub."""
        now = self.clock()
        with self._lock:
            if len(self._ci) > CACHE_MAX:
                self._ci.clear()
            at, state, reading = self._ci.get(repo.path, (None, None, False))
            if reading or (at is not None and now - at < CI_EVERY):
                return state
            self._ci[repo.path] = (at, state, True)

        def read():
            try:
                fresh = R.ci(R.Repo(repo.path, repo.name))
            except Exception:  # noqa: BLE001 - a CI read must never take the overview down
                fresh = state
            with self._lock:
                self._ci[repo.path] = (self.clock(), fresh, False)
        if self.background:
            threading.Thread(target=read, name="remote-git-ci", daemon=True).start()
        else:
            read()
            with self._lock:
                return self._ci[repo.path][1]
        return state

    def _summary(self, repo, i):
        R.status(repo)
        st = repo.status or {"error": True}
        ci = self._ci_state(repo)
        if st.get("error"):
            return {"index": i, "name": self.clean(repo.name, NAME_MAX), "error": True}
        return {"index": i, "name": self.clean(repo.name, NAME_MAX), "branch": self.clean(st["branch"], NAME_MAX),
                "detached": st["detached"], "ahead": st["ahead"], "behind": st["behind"], "staged": st["staged"],
                "changed": st["changed"], "untracked": st["untracked"], "conflicts": st["conflicts"],
                "op": self.clean(st.get("op") or "", 40) or None, "ci": ci, "dot": R.dot(st)[1]}

    def _detail(self, repo):
        raw = self._run(repo.path, "status", "--porcelain=v2", "--untracked-files=normal")
        files = parse_files(raw) if raw else []
        total = len(files)
        files = [{**f, "path": self.clean(f["path"], PATH_MAX)} for f in files[:MAX_FILES]]
        log = self._run(repo.path, "log", f"-n{MAX_COMMITS}", "--no-color", "--no-show-signature",
                        f"--format=%h{_FIELD}%an{_FIELD}%ct{_FIELD}%s")
        commits = [{"sha": self.clean(c["sha"], 12), "author": self.clean(c["author"], NAME_MAX), "ts": c["ts"],
                    "subject": self.clean(c["subject"], SUBJECT_MAX)} for c in (parse_log(log) if log else [])]
        return {"files": files, "files_total": total, "files_more": total > len(files), "commits": commits}

    # -- the document
    def overview(self, cwd, index=0):
        """{"repos": [summary...], "open": index or None, "detail": {...} or None, "dir": ...}. `cwd` not a directory
        or outside any repository: no repos (the page says so)."""
        if not isinstance(cwd, str) or not cwd or "\0" in cwd or not os.path.isdir(cwd):
            return {"repos": [], "open": None, "detail": None, "truncated": False}
        key, now = (cwd, index), self.clock()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < TTL:
                return hit[1]
        repos = self._repos(cwd)
        summaries = [self._summary(r, i) for i, r in enumerate(repos)]
        index = index if 0 <= index < len(repos) else 0
        detail = self._detail(repos[index]) if repos and not summaries[index].get("error") else None
        doc = {"repos": summaries, "open": index if repos else None, "detail": detail, "truncated": False}
        with self._lock:
            if len(self._cache) > CACHE_MAX:
                self._cache.clear()
            self._cache[key] = (self.clock(), doc)
        return doc

