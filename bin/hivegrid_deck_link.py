"""The line from the Stream Deck daemon to Hivegrid's Lua config, through the sidebar bars.

Equalize and arrange are Lua (config/layouts.lua) and nothing outside a pane can set a user var, so the
daemon (hivegrid_deck.py) writes "<action>:<pane id>" into a FIFO in the runtime directory, and a
hivegrid-bar, which every tab has and which already waits on its terminal, announces the line as the user
var `hivegrid_deck`. Every bar holds the FIFO open, so a written line is read by exactly one of them.
Standard library only: the bar imports this, and it must stay light."""
import os
import re
import stat

ACTIONS = ("equalize", "arrange")
LINE = re.compile(r"(equalize|arrange):([0-9]{1,9})")


def fifo_path(environ=None):
    """$XDG_RUNTIME_DIR/hivegrid/deck.fifo, None without a runtime directory (nothing is shared then)."""
    base = (os.environ if environ is None else environ).get("XDG_RUNTIME_DIR")
    return os.path.join(base, "hivegrid", "deck.fifo") if base and os.path.isabs(base) else None


def _is_ours(path):
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISFIFO(st.st_mode) and st.st_uid == os.geteuid()


def open_reader(path):
    """The read end of the FIFO as a non-blocking fd (opened read-write: it never sees end of file when the
    daemon closes its end), made first when it does not exist; None when it cannot be had."""
    if not path:
        return None
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        if not os.path.lexists(path):
            os.mkfifo(path, 0o600)
        if not _is_ours(path):
            return None
        return os.open(path, os.O_RDWR | os.O_NONBLOCK)
    except OSError:
        return None


def parse(data):
    """The valid lines of bytes read from the FIFO; anything else another process wrote is dropped."""
    out = []
    for raw in data.split(b"\n"):
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            continue
        if LINE.fullmatch(text):
            out.append(text)
    return out


def send(action, pane, path):
    """Ask for `action` on the window of pane `pane`. False when no bar has the FIFO open (no Hivegrid
    window, or its bars are gone) or the arguments are not valid."""
    line = f"{action}:{pane}"
    if not path or not LINE.fullmatch(line) or not _is_ours(path):
        return False
    try:
        fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)  # ENXIO: nobody reads
    except OSError:
        return False
    try:
        os.write(fd, (line + "\n").encode())
    except OSError:
        return False
    finally:
        os.close(fd)
    return True
