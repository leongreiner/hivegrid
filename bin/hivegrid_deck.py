"""Stream Deck Mini (6 keys) as a control panel for Hivegrid: what each key shows and does. See docs/deck.md.

Keys, left to right, top row then bottom row:
  1 needs you   the number of agents that wait for you; press: focus the next one
  2 enter       the focused agent (number, state); press: Enter (approve, continue, send what was dictated)
  3 mic         press: start dictating, press again: the text is typed into the focused pane (no Enter)
  4 new         press: a new omp session in the focused pane's folder; in a new window when the focused
                pane already runs omp, else split off in its tab
  5 grid        one press: equalize the panes, two presses: arrange them (config/layouts.lua)
  6 zoom        press: zoom the focused pane to the whole window and back (Alt+z)

Every action runs against the Hivegrid window whatever has the keyboard, through `wezterm cli`; equalize and
arrange are Lua and go through the sidebar bars (hivegrid_deck_link). The device layer speaks the Mini's
HID protocol (the 0fd9:0063 Stream Deck Mini) over hidraw. Standard library plus PIL for the key images."""
import fcntl
import glob
import io
import json
import os
import select
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

import hivegrid_agents as A
import hivegrid_deck_link as L

VENDOR_PRODUCT = "HID_ID=0003:00000FD9:00000063"
KEYS = 6
ICON = 80                  # the Mini's keys are 80 x 80 pixels
DOUBLE = 0.35              # seconds a second press of the grid key may follow the first
POLL = 1.5                 # seconds between looks at the agents
BRIGHTNESS = 70
TRANSCRIBE_MODEL = "gemini-2.5-flash"
MAX_RECORD = 120           # seconds; a dictation that long is cut

ACTIONS = ("next", "enter", "mic", "new", "grid", "zoom", "equalize", "arrange")
KEY_ACTIONS = ("next", "enter", "mic", "new", "grid", "zoom")  # key 1 .. 6

PAL = {"bg": "#0b0f16", "raised": "#222f45", "text": "#f5f8fc", "muted": "#8d9ab0", "accent": "#78a9ff",
       "violet": "#a78bfa", "red": "#f87171", "green": "#4ade80", "amber": "#f5a524", "cyan": "#5cc8f0"}
STATE_COLOR = {"attention": "violet", "limit": "red", "done": "green", "busy": "amber", "working": "accent"}
WAITING = ("attention", "limit")


# ---------------------------------------------------------------- the mux

class Mux:
    """`wezterm cli` of the running Hivegrid window, found again whenever it is gone."""

    def __init__(self, discover=None, runner=None):
        self._discover = discover or _discover
        self._runner = runner
        self._run = None

    def run(self, *args):
        if self._run is None:
            found = self._discover()
            if not found:
                return None
            sock, exe = found
            if self._runner:
                self._run = self._runner(exe, sock)
            else:
                import hivegrid_remote as R
                self._run = R.cli_runner(exe, sock)
        out = self._run(*args)
        if out is None and not self._alive():
            self._run = None
        return out

    def _alive(self):
        return self._run is not None and self._run("list-clients", "--format", "json") is not None

    def focused(self):
        """The pane that has the keyboard in the Hivegrid window, None when there is none."""
        try:
            clients = json.loads(self.run("list-clients", "--format", "json") or "[]")
        except ValueError:
            return None
        for c in clients if isinstance(clients, list) else []:
            if isinstance(c, dict) and isinstance(c.get("focused_pane_id"), int):
                return c["focused_pane_id"]
        return None


def _discover():
    import hivegrid_remote as R
    return R.discover_gui_socket()


# ---------------------------------------------------------------- state and actions

class View:
    """What the keys show: the agents that wait, the focused agent."""

    def __init__(self, waiting=0, num=None, state=None, recording=False, busy=False, alive=True):
        self.waiting, self.num, self.state = waiting, num, state
        self.recording, self.busy, self.alive = recording, busy, alive

    def key(self):
        return (self.waiting, self.num, self.state, self.recording, self.busy, self.alive)


def read_view(mux, agents=None, focus=None):
    agents = A.collect(mux.run) if agents is None else agents
    focus = mux.focused() if focus is None else focus
    here = next((a for a in agents if a.pane == focus), None)
    return View(waiting=sum(1 for a in agents if a.state in WAITING),
                num=here.num if here else None, state=here.state if here else None), agents, focus


def next_waiting(agents, focus):
    """The agent after the focused pane (pane order, wrapping) that waits for you, None when none does."""
    ordered = sorted(agents, key=lambda a: (a.window, a.tab, a.pane))
    waits = [a for a in ordered if a.state in WAITING and a.pane != focus]
    if not waits:
        return None
    after = [a for a in waits if (a.window, a.tab, a.pane) > _pos(ordered, focus)]
    return (after or waits)[0]


def _pos(ordered, focus):
    here = next((a for a in ordered if a.pane == focus), None)
    return (here.window, here.tab, here.pane) if here else (-1, -1, -1)


def act_next(mux, agents, focus):
    target = next_waiting(agents, focus)
    return bool(target) and A.jump(target, mux.run)


def act_enter(mux, focus):
    return focus is not None and mux.run("send-text", "--pane-id", str(focus), "--no-paste", "\r") is not None


def act_zoom(mux, focus):
    return focus is not None and mux.run("zoom-pane", "--pane-id", str(focus), "--toggle") is not None


def act_layout(mux, action, focus, path=None):
    return focus is not None and L.send(action, focus, path or L.fifo_path())


def act_new(mux, agents, focus):
    """A new omp session in the folder of the focused pane. When that pane runs an agent (not exited) it gets a
    window of its own, else it is split off in the focused pane's tab (hivegrid_agents.spawn_agent)."""
    if focus is None:
        return False
    here = next((a for a in agents if a.pane == focus), None)
    os.environ["HIVEGRID_ORIGIN_PANE"] = str(focus)
    if here is None or here.state == "exited":
        return A.spawn_agent(near=here, run=mux.run) is not None
    cwd = here.cwd or os.environ.get("HOME", "/")
    argv = A._agent_command()
    return mux.run("spawn", "--new-window", "--pane-id", str(focus), "--cwd", cwd, "--", *argv) is not None


class DoubleTap:
    """One press or two on one key: `press(now)` -> "arrange" on the second press inside DOUBLE seconds,
    else None; `due(now)` -> "equalize" once DOUBLE seconds passed since a lone press."""

    def __init__(self, window=DOUBLE):
        self.window, self.first = window, None

    def press(self, now):
        if self.first is not None and now - self.first <= self.window:
            self.first = None
            return "arrange"
        self.first = now
        return None

    def due(self, now):
        if self.first is not None and now - self.first > self.window:
            self.first = None
            return "equalize"
        return None

    def wait(self, now):
        return None if self.first is None else max(0.0, self.window - (now - self.first))


def raise_window(runner=subprocess.run):
    """Bring the Hivegrid window to the front through the GNOME Shell extension hivegrid-raise@hivegrid
    (Wayland lets no outside program do it). False when the extension does not answer or found no window."""
    try:
        r = runner(["gdbus", "call", "--session", "--dest", "org.hivegrid.Raise", "--object-path", "/org/hivegrid/Raise",
                    "--method", "org.hivegrid.Raise.Raise"], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and "true" in r.stdout


# ---------------------------------------------------------------- dictation

class Dictation:
    """Record with arecord, transcribe with Gemini (the key of the phone's voice mode), type into a pane."""

    def __init__(self, mux, say=print):
        self.mux, self.say = mux, say
        self.proc = None
        self.wav = None
        self.target = None
        self.busy = False
        self.started = 0.0

    @property
    def recording(self):
        return self.proc is not None

    def toggle(self, focus):
        if self.busy:
            return
        if self.proc is None:
            self._start(focus)
        else:
            self._stop()

    def _start(self, focus):
        if focus is None:
            return
        self.wav = tempfile.NamedTemporaryFile(prefix="hivegrid-deck-", suffix=".wav", delete=False).name
        try:
            self.proc = subprocess.Popen(["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "wav",
                                          "-d", str(MAX_RECORD), self.wav], stderr=subprocess.DEVNULL)
        except OSError as e:
            self.say(f"mic: cannot record: {e}")
            self._cleanup()
            return
        self.target, self.started = focus, time.monotonic()

    def expired(self):
        return self.proc is not None and time.monotonic() - self.started > MAX_RECORD + 1

    def _stop(self):
        proc, wav, target = self.proc, self.wav, self.target
        self.proc = None
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        self.busy = True
        threading.Thread(target=self._finish, args=(wav, target), daemon=True).start()

    def _finish(self, wav, target):
        try:
            text = transcribe(wav)
            if text:
                self.mux.run("send-text", "--pane-id", str(target), "--", text)
        except Exception as e:  # noqa: BLE001 - a failed dictation is reported, never fatal
            self.say(f"mic: {e}")
        finally:
            self.wav = wav
            self._cleanup()
            self.busy = False

    def _cleanup(self):
        if self.wav:
            try:
                os.remove(self.wav)
            except OSError:
                pass
        self.wav = None


def gemini_key():
    import hivegrid_remote_voice as V
    import hivegrid_settings_core as S
    return V.read_key(V.key_path(S.settings_path()))


def transcribe(path, key=None, opener=urllib.request.urlopen):
    """The words spoken in the WAV file `path`, via the Gemini API; "" when there are none."""
    import base64
    key = key or gemini_key()
    if not key:
        raise RuntimeError("no Gemini key (docs/remote.md, Voice)")
    with open(path, "rb") as f:
        audio = base64.b64encode(f.read()).decode()
    body = {"contents": [{"parts": [
        {"text": "Transcribe this audio exactly as spoken, in the language spoken (German or English). "
                 "Output only the transcript, no quotes, no commentary. If there is no speech, output nothing."},
        {"inline_data": {"mime_type": "audio/wav", "data": audio}}]}]}
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{TRANSCRIBE_MODEL}:generateContent",
        data=json.dumps(body).encode(), headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    try:
        with opener(req, timeout=30) as r:
            doc = json.load(r)
    except urllib.error.URLError as e:
        raise RuntimeError(f"transcription failed: {e}") from e
    parts = (((doc.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
    return " ".join(p.get("text", "") for p in parts).strip()


# ---------------------------------------------------------------- the device

def find_device():
    """/dev/hidraw node of a Stream Deck Mini, None when none is connected."""
    for d in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(d + "/device/uevent") as f:
                if VENDOR_PRODUCT in f.read():
                    return "/dev/" + os.path.basename(d)
        except OSError:
            continue
    return None


class Deck:
    """The Mini over hidraw: key states in, 80 x 80 images and brightness out."""

    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)

    def close(self):
        os.close(self.fd)

    def _feature(self, data):
        data = bytes(data).ljust(17, b"\0")
        fcntl.ioctl(self.fd, 0xC0000000 | (len(data) << 16) | (ord("H") << 8) | 0x06, data)

    def brightness(self, percent):
        self._feature([5, 0x55, 0xAA, 0xD1, 1, max(0, min(100, percent))])

    def reset(self):
        self._feature([0x0B, 0x63])


def _bmp(im):
    out = io.BytesIO()
    im.save(out, "BMP")
    return out.getvalue()


def prepare(im):
    """The bytes the Mini takes for a key picture: rotated a quarter turn, flipped top to bottom, as a BMP."""
    from PIL import Image
    im = im.convert("RGB").resize((ICON, ICON)).rotate(90).transpose(Image.FLIP_TOP_BOTTOM)
    return _bmp(im)


def send_image(fd, key, bmp):
    """Write one key picture in 1008-byte pages."""
    pages = (len(bmp) + 1007) // 1008
    for p in range(pages):
        head = bytes([2, 1, p, 0, 1 if p == pages - 1 else 0, key + 1]) + bytes(10)
        os.write(fd, (head + bmp[p * 1008:(p + 1) * 1008]).ljust(1024, b"\0"))


def parse_report(data):
    """The 6 key states of an input report, None for a report that is not the Mini's key state."""
    if len(data) < 7 or data[0] != 1:
        return None
    return [bool(b) for b in data[1:7]]


# ---------------------------------------------------------------- key pictures

def _font(size):
    from PIL import ImageFont
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _centered(draw, text, size, color, y=None, cx=ICON // 2):
    font = _font(size)
    box = draw.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    draw.text((cx - w / 2 - box[0], (ICON / 2 - h / 2 - box[1]) if y is None else y), text, font=font, fill=color)


def render(index, view, now=0.0):
    """The picture of key `index` (0..5) for `view`."""
    from PIL import Image, ImageDraw
    P = PAL
    im = Image.new("RGB", (ICON, ICON), P["bg"])
    d = ImageDraw.Draw(im)
    if not view.alive:
        _centered(d, "no", 22, P["muted"], y=18)
        _centered(d, "window", 16, P["muted"], y=46)
        return im
    if index == 0:
        hot = view.waiting > 0
        color = P["violet"] if hot else P["muted"]
        if hot:
            d.rounded_rectangle((4, 4, 75, 75), 10, fill=P["raised"], outline=color, width=3)
        _centered(d, str(view.waiting), 40, color, y=12 if hot else None)
        _centered(d, "waiting", 11, color, y=60)
    elif index == 1:
        color = P[STATE_COLOR.get(view.state, "muted")]
        _centered(d, str(view.num) if view.num else "-", 38, P["text"], y=8)
        _centered(d, (view.state or "no agent").replace("attention", "needs you"), 12, color, y=56)
        d.rectangle((0, 0, ICON, 4), fill=color)
    elif index == 2:
        color = P["red"] if view.recording else (P["amber"] if view.busy else P["accent"])
        if view.recording or view.busy:
            d.rounded_rectangle((2, 2, 77, 77), 10, outline=color, width=3)
        d.rounded_rectangle((31, 14, 49, 46), 9, fill=color)
        d.arc((22, 26, 58, 56), 0, 180, fill=color, width=3)
        d.line((40, 56, 40, 64), fill=color, width=3)
        d.line((30, 65, 50, 65), fill=color, width=3)
        _centered(d, "rec" if view.recording else ("..." if view.busy else "dictate"), 10, color, y=68)
    elif index == 3:
        c = P["green"]
        d.line((40, 18, 40, 52), fill=c, width=7)
        d.line((23, 35, 57, 35), fill=c, width=7)
        _centered(d, "new omp", 11, c, y=62)
    elif index == 4:
        c = P["cyan"]
        for r in range(2):
            for k in range(2):
                x, y = 18 + k * 24, 12 + r * 24
                d.rounded_rectangle((x, y, x + 20, y + 20), 3, outline=c, width=3)
        _centered(d, "1 equalize", 10, c, y=60)
        _centered(d, "2 arrange", 10, c, y=70)
    elif index == 5:
        c = P["accent"]
        for pts in (((16, 28), (16, 16), (28, 16)), ((64, 28), (64, 16), (52, 16)),
                    ((16, 42), (16, 54), (28, 54)), ((64, 42), (64, 54), (52, 54))):
            d.line(pts, fill=c, width=5)
        _centered(d, "zoom", 12, c, y=62)
    return im


# ---------------------------------------------------------------- the daemon

class Daemon:
    def __init__(self, mux=None, say=None):
        self.mux = mux or Mux()
        self.say = say or (lambda m: print(m, file=sys.stderr, flush=True))
        self.mic = Dictation(self.mux, self.say)
        self.tap = DoubleTap()
        self.shown = [None] * KEYS
        self.view = View(alive=False)
        self.agents, self.focus = [], None

    def refresh(self):
        view, self.agents, self.focus = read_view(self.mux)
        if self.focus is None and not self.agents:
            view.alive = False
        self.view = view

    def press(self, index, now):
        """Key `index` (0..5) went down."""
        name = KEY_ACTIONS[index]
        if name == "grid":
            fired = self.tap.press(now)
            if fired:
                self.do(fired)
            return
        self.do(name)

    def do(self, name):
        if name in ("equalize", "arrange"):
            ok = act_layout(self.mux, name, self.mux.focused())
        else:
            focus = self.mux.focused()
            agents = A.collect(self.mux.run)
            ok = {"next": lambda: act_next(self.mux, agents, focus),
                  "enter": lambda: act_enter(self.mux, focus),
                  "zoom": lambda: act_zoom(self.mux, focus),
                  "new": lambda: act_new(self.mux, agents, focus),
                  "mic": lambda: (self.mic.toggle(focus), True)[1]}[name]()
        if not ok:
            self.say(f"{name}: nothing done")
        elif not raise_window():
            self.say("raise: the GNOME extension hivegrid-raise@hivegrid is not loaded (docs/deck.md)")
        return ok

    def draw(self, deck, force=False):
        v = self.view
        v.recording, v.busy = self.mic.recording, self.mic.busy
        for i in range(KEYS):
            sig = v.key() if i < 3 else v.alive
            if force or self.shown[i] != sig:
                send_image(deck.fd, i, prepare(render(i, v)))
                self.shown[i] = sig

    def serve(self, deck):
        """Run on one connected deck until it goes away (OSError)."""
        deck.reset()
        deck.brightness(BRIGHTNESS)
        self.shown = [None] * KEYS
        last_poll, down = 0.0, [False] * KEYS
        while True:
            now = time.monotonic()
            if now - last_poll >= POLL:
                self.refresh()
                last_poll = now
            if self.mic.expired():
                self.mic.toggle(self.focus)
            self.draw(deck)
            wait = self.tap.wait(now)
            ready, _, _ = select.select([deck.fd], [], [], min(POLL, wait) if wait is not None else 0.25)
            if ready:
                state = parse_report(os.read(deck.fd, 64))
                if state is not None:
                    for i, (was, is_) in enumerate(zip(down, state)):
                        if is_ and not was:
                            self.press(i, time.monotonic())
                    down = state
            fired = self.tap.due(time.monotonic())
            if fired:
                self.do(fired)

    def run(self):
        while True:
            path = find_device()
            if path is None:
                time.sleep(2)
                continue
            try:
                deck = Deck(path)
            except OSError as e:
                self.say(f"deck: cannot open {path}: {e}")
                time.sleep(5)
                continue
            self.say(f"deck: {path}")
            try:
                self.serve(deck)
            except OSError:
                self.say("deck: disconnected")
            finally:
                try:
                    deck.close()
                except OSError:
                    pass
                self.shown = [None] * KEYS
            time.sleep(1)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["do"]:
        if len(argv) != 2 or argv[1] not in ACTIONS:
            print("usage: hivegrid-deck [do " + "|".join(ACTIONS) + "]", file=sys.stderr)
            return 2
        return 0 if Daemon().do(argv[1]) else 1
    if argv:
        print("usage: hivegrid-deck [do ACTION]", file=sys.stderr)
        return 2
    try:
        Daemon().run()
    except KeyboardInterrupt:
        pass
    return 0
