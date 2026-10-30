"""The outbox: files an agent sends to the phone through hivegrid-remote.

`hivegrid-send FILE` (run by an agent, or by you, in a Hivegrid pane) copies the file into a private
directory, `$HIVEGRID_OUTBOX` else `hivegrid-outbox` in `$XDG_RUNTIME_DIR` (a tmpfs, `/run/user/<uid>`), else
`/tmp/hivegrid-outbox-<uid>`: the same place the agents' state files live, so nothing is written to the disk
and a reboot empties it. The
remote server lists and serves only what is in that directory, so the phone can never read a file that
was not sent on purpose. Each item is two files, `<id>.bin` (the bytes) and `<id>.json` (name, size,
time, the sending pane, a note); the data is moved in place first and the metadata last, so a reader
that finds the metadata finds complete data.

Everything read back is untrusted: names are cleaned again, the type is sniffed from the bytes (only a
PNG, JPEG, GIF or WebP is ever shown as an image; the extension and the stored type are not believed),
and an item whose files are not regular files of this user, or whose size differs from its metadata, is
ignored. Standard library only; it must not import hivegrid_remote (that imports this).
"""
import argparse
import json
import os
import re
import secrets
import shutil
import stat
import sys
import threading
import time

MAX_FILE = 20 * 1024 * 1024        # one file
MAX_ITEMS = 50                     # items kept (the oldest go first)
MAX_TOTAL = 100 * 1024 * 1024      # bytes kept (the directory is in memory)
KEEP_SECONDS = 2 * 86400           # and none older than this survives a send
MAX_META = 4096
MAX_NOTE = 300
MAX_NAME = 120
ID_RE = re.compile(r"[0-9a-f]{11}-[0-9a-f]{8}")
WIN_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_BAD = re.compile("[\x00-\x1f\x7f-\x9f\u061c\u200b-\u200f\u2028\u2029\u202a-\u202e\u2066-\u2069\ufeff]")


def outbox_dir(environ=None):
    environ = os.environ if environ is None else environ
    if environ.get("HIVEGRID_OUTBOX"):
        return environ["HIVEGRID_OUTBOX"]
    if environ.get("XDG_RUNTIME_DIR"):
        return os.path.join(environ["XDG_RUNTIME_DIR"], "hivegrid-outbox")
    return f"/tmp/hivegrid-outbox-{os.getuid()}"


def clean_name(name):
    """A file name safe to show and to offer as a download: the last path component, no control or bidi
    characters, at most MAX_NAME characters; `file` when nothing is left."""
    name = _BAD.sub("", str(name or "")).replace("\\", "/").rsplit("/", 1)[-1].strip().strip(".")
    if len(name) > MAX_NAME:
        stem, dot, ext = name.rpartition(".")
        keep = max(MAX_NAME - len(ext) - 1, 1) if dot and len(ext) < 16 else MAX_NAME
        name = (stem[:keep] + "." + ext) if dot and len(ext) < 16 else name[:MAX_NAME]
    return name or "file"


def clean_note(note):
    return _BAD.sub(" ", str(note or "")).strip()[:MAX_NOTE]


def sniff_image(head):
    """The image type of the first bytes of a file, else None. Only these four are ever drawn by the page."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def human_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} MB"


class OutboxError(Exception):
    pass


def _private_dir(path, create, uid):
    """`path` as a directory only we can write; created (mode 0700) when `create`. OutboxError otherwise."""
    if create:
        os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise OutboxError(f"cannot use {path}: {exc.strerror}") from None
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid:
        raise OutboxError(f"{path} is not a directory of this user")
    if st.st_mode & 0o022:
        if not create:
            raise OutboxError(f"{path} is writable by others")
        os.chmod(path, 0o700)
    return st


def _read_meta(path, uid):
    """The metadata dict stored at `path`, validated, else None."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != uid or st.st_size > MAX_META:
            return None
        raw = os.read(fd, MAX_META + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    try:
        meta = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(meta, dict):
        return None
    ident, pane, size, ts = meta.get("id"), meta.get("pane"), meta.get("size"), meta.get("ts")
    ok = (isinstance(ident, str) and ID_RE.fullmatch(ident) and os.path.basename(path) == ident + ".json"
          and type(pane) is int and pane >= 0 and type(size) is int and 0 <= size <= MAX_FILE
          and isinstance(ts, (int, float)) and not isinstance(ts, bool) and 0 <= ts < 1e11)
    if not ok:
        return None
    win = meta.get("win")
    return {"id": ident, "pane": pane, "size": size, "ts": float(ts), "name": clean_name(meta.get("name")),
            "win": win if isinstance(win, str) and WIN_RE.fullmatch(win) else "",
            "note": clean_note(meta.get("note")), "mime": meta.get("mime") if meta.get("mime") in
            ("image/png", "image/jpeg", "image/gif", "image/webp") else "application/octet-stream"}


def _scan(root, uid):
    """Every valid item of the outbox, newest first."""
    try:
        names = os.listdir(root)
    except OSError:
        return []
    items = []
    for n in names:
        if not n.endswith(".json") or not ID_RE.fullmatch(n[:-5]):
            continue
        meta = _read_meta(os.path.join(root, n), uid)
        if meta is None:
            continue
        try:
            st = os.lstat(os.path.join(root, meta["id"] + ".bin"))
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode) and st.st_uid == uid and st.st_size == meta["size"]:
            items.append(meta)
    items.sort(key=lambda m: (m["ts"], m["id"]), reverse=True)
    return items


def _remove(root, ident):
    for ext in (".json", ".bin"):          # the metadata first: an item without it is invisible
        try:
            os.unlink(os.path.join(root, ident + ext))
        except OSError:
            pass


def collect(root, uid, now):
    """Deletes what is too old or too much (see the limits above) and leftovers of interrupted sends."""
    items = _scan(root, uid)
    keep = []
    for m in items:
        if now - m["ts"] > KEEP_SECONDS:
            _remove(root, m["id"])
        else:
            keep.append(m)
    total = sum(m["size"] for m in keep)
    while keep and (len(keep) > MAX_ITEMS or total > MAX_TOTAL):
        old = keep.pop()
        total -= old["size"]
        _remove(root, old["id"])
    live = {m["id"] for m in keep}
    try:
        names = os.listdir(root)
    except OSError:
        return
    for n in names:
        path = os.path.join(root, n)
        ident = n.rsplit(".", 1)[0]
        stale = n.startswith(".tmp-") or (ID_RE.fullmatch(ident) and ident not in live)
        try:
            if stale and now - os.lstat(path).st_mtime > 3600:
                os.unlink(path)
        except OSError:
            pass


def window_key(socket_path):
    """The name of a Hivegrid window's socket (`gui-sock-<pid>`) or "": pane numbers start again when the
    window restarts, so the window tells apart the panes of two runs that carry the same number."""
    name = os.path.basename(str(socket_path or ""))
    return name if WIN_RE.fullmatch(name) else ""


def send(path, pane, note=None, environ=None, now=None, uid=None):
    """Copies the file `path` into the outbox for the agent in pane `pane` of the window named by
    $WEZTERM_UNIX_SOCKET; returns its metadata."""
    uid = os.getuid() if uid is None else uid
    now = time.time() if now is None else now
    root = outbox_dir(environ)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as exc:
        raise OutboxError(f"cannot read {path}: {exc.strerror}") from None
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise OutboxError(f"{path} is not a regular file")
    with os.fdopen(fd, "rb") as src:
        if st.st_size > MAX_FILE:
            raise OutboxError(f"{path} is {human_size(st.st_size)}; the limit is {human_size(MAX_FILE)}")
        _private_dir(root, True, uid)
        ident = f"{int(now * 1000):011x}-{secrets.token_hex(4)}"
        tmp = os.path.join(root, f".tmp-{ident}")
        size, head = 0, b""
        try:
            with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as dst:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_FILE:
                        raise OutboxError(f"{path} is larger than {human_size(MAX_FILE)}")
                    if len(head) < 16:
                        head += chunk[:16 - len(head)]
                    dst.write(chunk)
            os.rename(tmp, os.path.join(root, ident + ".bin"))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    meta = {"id": ident, "pane": int(pane), "win": window_key((os.environ if environ is None else environ).get("WEZTERM_UNIX_SOCKET")), "name": clean_name(os.path.basename(path)), "size": size, "ts": now,
            "mime": sniff_image(head) or "application/octet-stream", "note": clean_note(note)}
    tmp = os.path.join(root, f".tmp-{ident}.json")
    try:
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "w", encoding="utf-8") as f:
            json.dump(meta, f)
        os.rename(tmp, os.path.join(root, ident + ".json"))
    except BaseException:
        for p in (tmp, os.path.join(root, ident + ".bin")):
            try:
                os.unlink(p)
            except OSError:
                pass
        raise
    collect(root, uid, now)
    return meta


class Outbox:
    """Read side, for the server: the items of an outbox directory. The listing is rescanned only when the
    directory changed (its modification time), so asking for it every second costs one `stat`."""

    def __init__(self, environ=None, uid=None):
        self.root = outbox_dir(environ)
        self.uid = os.getuid() if uid is None else uid
        self._lock = threading.Lock()
        self._stamp = None
        self._items = []

    def items(self):
        """Every valid item, newest first; empty while the directory is missing or not private."""
        try:
            st = _private_dir(self.root, False, self.uid)
        except OutboxError:
            return []
        stamp = (st.st_ino, st.st_mtime_ns)
        with self._lock:
            if stamp != self._stamp:
                self._items = _scan(self.root, self.uid)
                self._stamp = stamp
            return list(self._items)

    def for_pane(self, pane, win=""):
        """The items sent from pane `pane`, newest first. `win` is the running window's key: an item that
        names another window is from an earlier run of Hivegrid and is not shown (an item or a server
        that does not know its window shows it)."""
        return [m for m in self.items() if m["pane"] == pane and (not win or not m["win"] or m["win"] == win)]

    def read(self, ident):
        """(metadata, bytes) of item `ident`, or None when it is not there, not valid or changed since the listing."""
        if not isinstance(ident, str) or not ID_RE.fullmatch(ident):
            return None
        meta = next((m for m in self.items() if m["id"] == ident), None)
        if meta is None:
            return None
        try:
            fd = os.open(os.path.join(self.root, ident + ".bin"), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            return None
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != self.uid or st.st_size != meta["size"]:
                return None
            chunks, left = [], meta["size"]
            while left > 0:
                chunk = os.read(fd, min(left, 1 << 20))
                if not chunk:
                    return None
                chunks.append(chunk)
                left -= len(chunk)
        except OSError:
            return None
        finally:
            os.close(fd)
        return meta, b"".join(chunks)


def main(argv=None, environ=None, out=sys.stdout, err=sys.stderr):
    """`hivegrid-send [--note TEXT] [--pane N] FILE...`"""
    environ = os.environ if environ is None else environ
    p = argparse.ArgumentParser(prog="hivegrid-send", description=(
        "Send files to the phone page of hivegrid-remote. Run it in a Hivegrid pane (an agent runs it from its shell); "
        "the files show on that agent's page. Images appear as pictures, anything else as a download. "
        f"At most {human_size(MAX_FILE)} per file; the newest {MAX_ITEMS} files are kept for {KEEP_SECONDS // 86400} days."))
    p.add_argument("files", nargs="+", metavar="FILE")
    p.add_argument("--note", help="a line of text shown with the first file (the rest of the files carry none)")
    p.add_argument("--pane", help="the pane's number (default: this pane, from $WEZTERM_PANE)")
    args = p.parse_args(argv)
    pane = args.pane if args.pane is not None else environ.get("WEZTERM_PANE", "")
    if not re.fullmatch(r"\d{1,9}", str(pane)):
        print("hivegrid-send: not inside a Hivegrid pane ($WEZTERM_PANE is not set): pass --pane N", file=err)
        return 2
    status = 0
    for n, path in enumerate(args.files):
        try:
            meta = send(path, int(pane), args.note if n == 0 else None, environ)
        except OutboxError as exc:
            print(f"hivegrid-send: {exc}", file=err)
            status = 1
            continue
        except OSError as exc:
            print(f"hivegrid-send: {path}: {exc.strerror}", file=err)
            status = 1
            continue
        print(f"sent {meta['name']} ({human_size(meta['size'])}) to the phone", file=out)
    return status
