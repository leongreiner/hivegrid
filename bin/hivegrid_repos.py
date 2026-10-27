"""Git repository discovery and status for the Hivegrid git view. Standard library only.

find_repos() lists the repositories of a workspace directory the way VS Code's Source Control
view does: the repository around the directory plus every repository below it. status() reads one
repository's branch, upstream distance and change counts with a single `git status` call, plus the
operation in progress (merge, rebase, ...) from marker files. ci() reads the GitHub Actions result
of the checked-out branch through the `gh` CLI, for repositories that have workflows. Where answers
"which branch, and which worktree, is the checkout around this directory on" for the overview dock,
cached, with the status read off the calling thread.
"""
import json
import os
import subprocess
import threading
import time

# Directories never worth descending into: dependency trees, build output and caches.
SKIP_DIRS = frozenset({
    "node_modules", "target", "__pycache__", "site-packages", "venv", "env", "build", "dist",
    "vendor", "bower_components", "Pods", "DerivedData",
})
DEFAULT_DEPTH = 3
DEFAULT_BUDGET = 2.0  # seconds the directory walk may take before it gives up


class Repo:
    """One repository: its work tree, the name shown for it and its latest status and CI state."""

    __slots__ = ("path", "name", "status", "ci")

    def __init__(self, path, name):
        self.path, self.name, self.status, self.ci = path, name, None, None


def _git(path, *args, timeout=20, here=False):
    """Output of `git -C path ARGS`, None on failure. here=True confines git to the repository at
    `path` itself: without it, a broken `.git` there (an interrupted clone) makes git walk up and
    answer for an outer repository."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", LC_ALL="C", GIT_TERMINAL_PROMPT="0")
    if here:
        env["GIT_CEILING_DIRECTORIES"] = os.path.dirname(os.path.abspath(path))
    try:
        r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=timeout,
                           env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def toplevel(path):
    """Work tree root of the repository around `path`, or None outside any repository."""
    out = _git(path, "rev-parse", "--show-toplevel")
    return os.path.realpath(out.strip()) if out and out.strip() else None


def find_repos(root, max_depth=DEFAULT_DEPTH, budget=DEFAULT_BUDGET):
    """-> (repos, truncated). `repos` is sorted by path; `truncated` is True when the time budget
    ended the walk early. A directory holding `.git` (a directory, or a file for worktrees and
    submodules) is a repository; the walk continues inside it to find nested ones. Symlinks and
    hidden directories are not followed."""
    root = os.path.realpath(root)
    found = {}
    top = toplevel(root)
    if top:
        found[top] = Repo(top, os.path.basename(top) or top)
    deadline = time.monotonic() + budget
    truncated = False
    stack = [(root, 0)]
    while stack:
        if time.monotonic() > deadline:
            truncated = True
            break
        path, depth = stack.pop()
        try:
            with os.scandir(path) as it:
                entries = list(it)
        except OSError:
            continue
        if any(e.name == ".git" for e in entries) and path not in found:
            rel = os.path.relpath(path, root)
            found[path] = Repo(path, os.path.basename(path) if rel == "." else rel)
        if depth >= max_depth:
            continue
        for e in entries:
            if e.name.startswith(".") or e.name in SKIP_DIRS:
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    stack.append((e.path, depth + 1))
            except OSError:
                pass
    return sorted(found.values(), key=lambda r: r.path), truncated


def parse_status(text):
    """Parses `git status --porcelain=v2 --branch` output into a dict: branch, detached, ahead,
    behind (None without an upstream), staged, changed (unstaged), untracked, conflicts."""
    s = {"branch": "", "detached": False, "ahead": None, "behind": None,
         "staged": 0, "changed": 0, "untracked": 0, "conflicts": 0}
    oid = ""
    for line in text.splitlines():
        if line.startswith("# branch.head "):
            s["branch"] = line[14:]
        elif line.startswith("# branch.oid "):
            oid = line[13:]
        elif line.startswith("# branch.ab "):
            a, b = line[12:].split()
            s["ahead"], s["behind"] = int(a[1:]), int(b[1:])
        elif line[:2] in ("1 ", "2 "):
            xy = line[2:4]
            s["staged"] += xy[0] != "."
            s["changed"] += xy[1] != "."
        elif line.startswith("u "):
            s["conflicts"] += 1
        elif line.startswith("? "):
            s["untracked"] += 1
    if s["branch"] == "(detached)":
        s["detached"] = True
        s["branch"] = "@" + oid[:7] if oid and oid != "(initial)" else "(detached)"
    elif oid == "(initial)":
        s["branch"] += " (no commits)"
    return s


def git_dir(path):
    """The repository's git directory: `.git` itself, or where a `.git` file (submodule,
    worktree) points. None when neither is readable."""
    dot = os.path.join(path, ".git")
    if os.path.isdir(dot):
        return dot
    try:
        with open(dot, encoding="utf-8") as f:
            line = f.readline().strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    return os.path.normpath(os.path.join(path, line[7:].strip()))


# Marker in the git directory -> the operation it means, checked in this order (a rebase stops on
# conflicts the same way a merge does, so the rebase markers come first).
OPERATIONS = (("rebase-merge", "rebasing"), ("rebase-apply", "rebasing"), ("MERGE_HEAD", "merging"),
              ("CHERRY_PICK_HEAD", "cherry-picking"), ("REVERT_HEAD", "reverting"),
              ("BISECT_LOG", "bisecting"))


def operation(path):
    """The unfinished operation of the repository at `path` ("merging", "rebasing",
    "cherry-picking", "reverting", "bisecting") or None."""
    gd = git_dir(path)
    if gd:
        for marker, name in OPERATIONS:
            if os.path.exists(os.path.join(gd, marker)):
                return name
    return None


def status(repo):
    """Reads the repository's status into repo.status: the parse_status dict plus "op" (see
    operation()), or {"error": True} when git failed (e.g. the directory vanished). Safe to call
    from threads; it takes no index lock, so it never blocks a running lazygit."""
    out = _git(repo.path, "status", "--porcelain=v2", "--branch", here=True)
    if out is None:
        repo.status = {"error": True}
    else:
        s = parse_status(out)
        s["op"] = operation(repo.path)
        repo.status = s
    return repo.status


def dot(st):
    """(glyph, palette key) of the status dot: reading, unreadable, conflicts, changes, clean.
    Only uncommitted work makes it amber; commits not yet pushed leave it green."""
    if st is None:
        return "…", "muted"
    if st.get("error"):
        return "✕", "red"
    if st["conflicts"]:
        return "●", "red"
    if st["staged"] or st["changed"] or st["untracked"]:
        return "●", "amber"
    return "●", "green"


def checkout(cwd):
    """(work tree root, git directory) of the checkout around `cwd` (`~` allowed), or None for a
    relative path (a web pane's host), a missing directory, a directory outside any repository
    and a broken `.git`. Walks up looking for `.git`; runs no git."""
    path = os.path.expanduser(cwd or "")
    if not os.path.isabs(path) or not os.path.isdir(path):
        return None
    path = os.path.normpath(path)
    while True:
        if os.path.lexists(os.path.join(path, ".git")):
            gd = git_dir(path)
            return (path, gd) if gd and os.path.isdir(gd) else None
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def head(gd):
    """(branch, detached) from the HEAD file of git directory `gd`: a detached HEAD reads as
    "@" and the first 7 characters of the commit, as parse_status shows it. None when unreadable."""
    try:
        with open(os.path.join(gd, "HEAD"), encoding="utf-8") as f:
            text = f.readline().strip()
    except OSError:
        return None
    if text.startswith("ref:"):
        ref = text[4:].strip()
        return (ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref), False
    return ("@" + text[:7], True) if text else None


class Where:
    """The branch and worktree of the checkout around a directory, for the overview dock, which
    asks again on every redraw: call the instance with a directory. The branch comes from reading
    HEAD (no git process), cached for `ttl` seconds per directory; the changes dot needs one
    `git status` per repository, read at most every `status_ttl` seconds on a daemon thread (the
    dot is missing until the first read lands; background=False reads it on the calling thread).
    The answer is None outside a repository, else a dict:
      branch    the branch, or "@abc1234" on a detached HEAD
      detached  True on a detached HEAD
      worktree  the directory name of a linked worktree (`git worktree add`), None otherwise
      dot       "green" clean, "amber" uncommitted changes, "red" conflicts, None while unread"""

    CACHE = 64  # directories remembered before the stale ones are dropped

    def __init__(self, ttl=2.0, status_ttl=5.0, background=True, clock=time.monotonic):
        self.ttl, self.status_ttl, self.background, self.clock = ttl, status_ttl, background, clock
        self.places = {}  # cwd -> (read at, (root, branch, detached, linked) or None)
        self.states = {}  # root -> {"at", "dot", "busy", "seen"}
        self.lock = threading.Lock()

    def __call__(self, cwd):
        now = self.clock()
        with self.lock:
            hit = self.places.get(cwd)
            if hit is None or now - hit[0] >= self.ttl:
                hit = self.places[cwd] = (now, self.read(cwd))
                if len(self.places) > self.CACHE:
                    self.places = {k: v for k, v in self.places.items() if now - v[0] < self.ttl}
                    self.states = {k: v for k, v in self.states.items() if now - v["seen"] < 300 or v["busy"]}
            place = hit[1]
            if place is None:
                return None
            root, branch, detached, linked = place
            state = self.states.setdefault(root, {"at": None, "dot": None, "busy": False, "seen": now})
            state["seen"] = now
            stale = state["at"] is None or now - state["at"] >= self.status_ttl
            start = stale and not state["busy"]
            if start:
                state["busy"] = True
            dot_key = state["dot"]
        if start:
            if self.background:
                threading.Thread(target=self.refresh, args=(root,), daemon=True).start()
            else:
                self.refresh(root)
                dot_key = self.states[root]["dot"]
        return {"branch": branch, "detached": detached, "worktree": os.path.basename(root) if linked else None,
                "dot": dot_key}

    @staticmethod
    def read(cwd):
        found = checkout(cwd)
        h = head(found[1]) if found else None
        if h is None:
            return None
        # A linked worktree's git directory (<repo>/.git/worktrees/<name>) points at the shared one.
        return found[0], h[0], h[1], os.path.isfile(os.path.join(found[1], "commondir"))

    def refresh(self, root):
        st = status(Repo(root, ""))
        key = None if st.get("error") else dot(st)[1]
        with self.lock:
            state = self.states.get(root)
            if state is not None:
                state.update(at=self.clock(), dot=key, busy=False)


def has_ci(path):
    """True when the work tree defines GitHub Actions workflows."""
    try:
        return any(n.endswith((".yml", ".yaml")) for n in os.listdir(os.path.join(path, ".github", "workflows")))
    except OSError:
        return False


def parse_ci(text):
    """Reduces `gh run list --json status,conclusion,workflowName` output (newest run first) to
    one state from the latest run of each workflow: "fail" if any failed, else "running" if any is
    unfinished, else "pass" if any passed, else "other" (cancelled/skipped only), "none" without
    runs. None when the text is not a run list."""
    try:
        runs = json.loads(text)
    except ValueError:
        return None
    if not isinstance(runs, list):
        return None
    latest = {}
    for r in runs:
        if isinstance(r, dict):
            latest.setdefault(r.get("workflowName"), r)
    if not latest:
        return "none"
    states = set()
    for r in latest.values():
        if r.get("status") != "completed":
            states.add("running")
        elif r.get("conclusion") in ("failure", "timed_out", "startup_failure"):
            states.add("fail")
        elif r.get("conclusion") == "success":
            states.add("pass")
        else:
            states.add("other")
    return next(s for s in ("fail", "running", "pass", "other") if s in states)


def _gh(path, *args, timeout=20):
    env = dict(os.environ, GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1", NO_COLOR="1")
    try:
        r = subprocess.run(["gh", *args], cwd=path, capture_output=True, text=True, timeout=timeout,
                           env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def ci(repo):
    """Reads the CI state of the checked-out branch (or commit when detached) into repo.ci: None
    without workflows, else "pass" / "fail" / "running" / "other" / "none" (no runs yet) or
    "unknown" when gh failed (missing, logged out, offline, not a GitHub remote); a failed read
    keeps the previous state. Network call: run it off the UI thread and rarely."""
    if not has_ci(repo.path):
        repo.ci = None
        return None
    branch = (_git(repo.path, "symbolic-ref", "--short", "-q", "HEAD", here=True) or "").strip()
    sha = "" if branch else (_git(repo.path, "rev-parse", "HEAD", here=True) or "").strip()
    state = None
    if branch or sha:
        out = _gh(repo.path, "run", "list", "--branch" if branch else "--commit", branch or sha,
                  "--limit", "30", "--json", "status,conclusion,workflowName")
        state = parse_ci(out) if out is not None else None
    repo.ci = state or repo.ci or "unknown"
    return repo.ci
