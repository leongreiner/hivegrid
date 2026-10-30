"""Files the phone uploads through hivegrid-remote: where they are kept and how old ones go.

A file lands in `<cache>/hivegrid/uploads/<time>-<random>/<name>` (directories mode 0700, the file 0600), so the
agent on this machine can read it by its absolute path. Nothing here reads a path the phone names: the phone
gives a file name, which is reduced to safe characters and used only as the last path component.
"""
import os
import re
import secrets
import shutil
import stat
import time

import hivegrid_outbox as O

MAX_UPLOAD = 25 * 1024 * 1024      # one file
MAX_TOTAL = 500 * 1024 * 1024      # bytes kept on disk (the oldest go first)
MAX_ITEMS = 200
KEEP_SECONDS = 7 * 86400
CHUNK = 256 * 1024
DIR_RE = re.compile(r"[0-9]{10}-[0-9a-f]{8}")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class UploadError(Exception):
    pass


def upload_root(environ=None, home=None):
    """`$XDG_CACHE_HOME/hivegrid/uploads`, else `<home>/.cache/hivegrid/uploads`."""
    environ = os.environ if environ is None else environ
    base = environ.get("XDG_CACHE_HOME")
    if not base or not os.path.isabs(base):
        base = os.path.join(home or environ.get("HOME") or os.path.expanduser("~"), ".cache")
    return os.path.join(base, "hivegrid", "uploads")


def safe_name(name):
    """A file name made of letters, digits and `._-` only (so the path can be typed into a prompt as it is),
    at most 100 characters for the stem, never hidden, keeping a short extension."""
    stem, ext = os.path.splitext(O.clean_name(name))
    stem = _UNSAFE.sub("_", stem).strip("._-") or "file"
    ext = _UNSAFE.sub("", ext.lstrip("."))[:12]
    return stem[:100] + ("." + ext if ext else "")


def _entries(root):
    """[(mtime, size, dir path)] of the upload directories of `root`, oldest first."""
    out = []
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for n in names:
        if not DIR_RE.fullmatch(n):
            continue
        d = os.path.join(root, n)
        try:
            st = os.lstat(d)
            if not stat.S_ISDIR(st.st_mode):
                continue
            size = sum(os.lstat(os.path.join(d, f)).st_size for f in os.listdir(d))
        except OSError:
            continue
        out.append((st.st_mtime, size, d))
    return sorted(out)


def collect(root, now):
    """Deletes upload directories older than KEEP_SECONDS, and the oldest ones beyond MAX_ITEMS or MAX_TOTAL."""
    items = _entries(root)
    total = sum(e[1] for e in items)
    while items:
        mtime, size, d = items[0]
        if now - mtime <= KEEP_SECONDS and len(items) <= MAX_ITEMS and total <= MAX_TOTAL:
            break
        shutil.rmtree(d, ignore_errors=True)
        total -= size
        items.pop(0)


def save(root, name, length, read, now=None, uid=None):
    """Stores `length` bytes from `read(n)` as the file `name`; returns `{"path", "name", "size"}`.
    UploadError when it cannot (too big, short body, the directory is not ours); nothing is left behind then."""
    now = time.time() if now is None else now
    uid = os.getuid() if uid is None else uid
    if not 0 < length <= MAX_UPLOAD:
        raise UploadError("empty file" if length <= 0 else f"file is larger than {MAX_UPLOAD // (1024 * 1024)} MB")
    try:
        O._private_dir(root, True, uid)
    except (O.OutboxError, OSError) as exc:
        raise UploadError(str(exc)) from None
    collect(root, now)
    clean = safe_name(name)
    folder = os.path.join(root, f"{int(now):010d}-{secrets.token_hex(4)}")
    path = os.path.join(folder, clean)
    try:
        os.mkdir(folder, 0o700)
        left = length
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as f:
            while left > 0:
                chunk = read(min(CHUNK, left))
                if not chunk:
                    raise UploadError("the upload stopped before the end of the file")
                f.write(chunk)
                left -= len(chunk)
    except BaseException as exc:
        shutil.rmtree(folder, ignore_errors=True)
        if isinstance(exc, OSError) and not isinstance(exc, TimeoutError):   # a timeout is the server's to answer
            raise UploadError(f"cannot store the file: {exc.strerror}") from None
        raise
    return {"path": path, "name": clean, "size": length}
