"""Git repository discovery and status for the Hivegrid git view. Standard library only.

find_repos() lists the repositories of a workspace directory the way VS Code's Source Control
view does: the repository around the directory plus every repository below it. status() reads one
repository's branch, upstream distance and change counts with a single `git status` call.
"""
import os
import subprocess
import time

# Directories never worth descending into: dependency trees, build output and caches.
SKIP_DIRS = frozenset({
    "node_modules", "target", "__pycache__", "site-packages", "venv", "env", "build", "dist",
    "vendor", "bower_components", "Pods", "DerivedData",
})
DEFAULT_DEPTH = 3
DEFAULT_BUDGET = 2.0  # seconds the directory walk may take before it gives up


class Repo:
    """One repository: its work tree, the name shown for it and its latest status."""

    __slots__ = ("path", "name", "status")

    def __init__(self, path, name):
        self.path, self.name, self.status = path, name, None


def _git(path, *args, timeout=20):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", LC_ALL="C", GIT_TERMINAL_PROMPT="0")
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


def status(repo):
    """Reads the repository's status into repo.status: the parse_status dict, or {"error": True}
    when git failed (e.g. the directory vanished). Safe to call from threads; it takes no index
    lock, so it never blocks a running lazygit."""
    out = _git(repo.path, "status", "--porcelain=v2", "--branch")
    repo.status = parse_status(out) if out is not None else {"error": True}
    return repo.status
