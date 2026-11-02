"""`hivegrid-remote attach`: shows one of the phone's terminals (a shell of hivegrid-remote) in a pane of the host.

The server opens a pane in a window of its own for every terminal the phone opens and runs this program in it.
It speaks the server's own terminal API over HTTP with the token file, like the page does, so it gets no power
the phone has not: it mirrors the shell's output, types what is typed in the pane and sets the shell's size to
the pane's. It ends (and the pane closes) when the shell ends; closing the pane only detaches: the shell goes on
for the phone. Nothing it reads or types is logged.
"""
import base64
import http.client
import json
import os
import select
import signal
import struct
import sys
import termios
import threading
import tty
import fcntl

POLL = 15          # seconds an output request is held
RETRY = 1.0


class Gone(Exception):
    """The terminal does not exist (any more): the pane may close."""


class Client:
    """The terminal API of one session, the token read from the token file at every call (a rotation ends the shell)."""

    def __init__(self, host, port, token_file, sid, read_token, conn=http.client.HTTPConnection):
        self.host, self.port, self.token_file, self.sid = host, port, token_file, sid
        self.read_token, self.conn = read_token, conn

    def call(self, method, path, body=None, timeout=POLL + 10):
        headers = {"Authorization": "Bearer " + self.read_token(self.token_file)}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers.update({"Content-Type": "application/json", "X-Hivegrid-Remote": "1"})
        c = self.conn(self.host, self.port, timeout=timeout)
        try:
            c.request(method, path, body=data, headers=headers)
            r = c.getresponse()
            raw = r.read()
        finally:
            c.close()
        try:
            parsed = json.loads(raw) if raw else {}
        except ValueError:
            parsed = {}
        if r.status in (401, 403, 404):
            raise Gone(r.status)
        return r.status, parsed

    def out(self, pos):
        return self.call("GET", f"/api/terminals/{self.sid}/out?from={pos}&wait={POLL}")

    def send(self, seq, text):
        return self.call("POST", f"/api/terminals/{self.sid}/in", {"seq": seq, "data": text})

    def resize(self, cols, rows):
        return self.call("POST", f"/api/terminals/{self.sid}/resize", {"cols": cols, "rows": rows})


class Typist:
    """Numbered input: the next request number is the one the server says (the phone types into the same shell)."""

    def __init__(self, client):
        self.client, self.seq = client, 0

    def note(self, expected):
        if isinstance(expected, int) and not isinstance(expected, bool):
            self.seq = expected

    def type(self, text):
        for _ in range(3):
            status, data = self.client.send(self.seq, text)
            if status == 200:
                self.note(data.get("in", self.seq + 1))
                return True
            if status == 409 and isinstance(data.get("in"), int):
                self.note(data["in"])
                continue
            return False
        return False


def clamp(cols, rows):
    return max(10, min(400, cols)), max(3, min(200, rows))


def size_of(fd):
    try:
        rows, cols = struct.unpack("HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8))[:2]
    except OSError:
        return None
    return clamp(cols, rows) if cols and rows else None


def mirror(client, typist, write, stop, reset=b"\x1bc"):
    """Output of the shell to `write` until it ends (True) or `stop` is set (False). A connection that fails is
    retried; a terminal that is gone ends the loop."""
    pos = 0
    while not stop.is_set():
        try:
            status, d = client.out(pos)
        except Gone:
            return True
        except (OSError, http.client.HTTPException):
            stop.wait(RETRY)
            continue
        if status != 200:
            stop.wait(RETRY)
            continue
        typist.note(d.get("in"))
        if d.get("reset"):
            write(reset)
        data = d.get("data")
        if data:
            write(base64.b64decode(data))
        pos = d.get("next", pos)
        if d.get("alive") is False:
            return True
    return False


def run(client, stdin=0, stdout=1):
    """Attach the terminal this process runs in to the session; the exit status for the program."""
    stop = threading.Event()
    typist = Typist(client)
    old = None
    try:
        old = termios.tcgetattr(stdin)
        tty.setraw(stdin)
    except termios.error:
        pass

    def write(data):
        while data:
            data = data[os.write(stdout, data):]

    def fit(*_):
        size = size_of(stdout)
        if size:
            try:
                client.resize(*size)
            except (Gone, OSError, http.client.HTTPException):
                pass

    def keys():
        while not stop.is_set():
            try:
                ready, _, _ = select.select([stdin], [], [], 0.25)
                if not ready:
                    continue
                raw = os.read(stdin, 1024)
            except OSError:
                break
            if not raw:
                break
            try:
                typist.type(raw.decode("utf-8", "replace"))
            except Gone:
                break
            except (OSError, http.client.HTTPException):
                continue
        stop.set()

    signal.signal(signal.SIGWINCH, lambda *_: threading.Thread(target=fit, daemon=True).start())
    fit()
    threading.Thread(target=keys, daemon=True).start()
    try:
        ended = mirror(client, typist, write, stop)
    finally:
        stop.set()
        if old is not None:
            termios.tcsetattr(stdin, termios.TCSADRAIN, old)
    return 0 if ended else 1


def main(host, port, token_file, sid, read_token):
    return run(Client(host, port, token_file, sid, read_token))
