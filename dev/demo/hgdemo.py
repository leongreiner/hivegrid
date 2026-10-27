"""Building blocks of the demo recording: smooth synthetic input through XTest (ctypes, no
dependencies), a captioned ffmpeg screen recording of the QA sandbox's Xvfb display, and helpers
for `dev/qa/hgq`. Used by dev/demo/record.py.
"""
import ctypes as C
import json
import os
import shutil
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HGQ = os.path.join(REPO, "dev", "qa", "hgq")

X = C.cdll.LoadLibrary("libX11.so.6")
T = C.cdll.LoadLibrary("libXtst.so.6")
X.XOpenDisplay.restype = C.c_void_p
X.XOpenDisplay.argtypes = [C.c_char_p]
X.XFlush.argtypes = [C.c_void_p]
X.XSync.argtypes = [C.c_void_p, C.c_int]
X.XStringToKeysym.restype = C.c_ulong
X.XStringToKeysym.argtypes = [C.c_char_p]
X.XKeysymToKeycode.restype = C.c_ubyte
X.XKeysymToKeycode.argtypes = [C.c_void_p, C.c_ulong]
X.XkbKeycodeToKeysym.restype = C.c_ulong
X.XkbKeycodeToKeysym.argtypes = [C.c_void_p, C.c_ubyte, C.c_int, C.c_int]
X.XQueryPointer.argtypes = [C.c_void_p, C.c_ulong] + [C.c_void_p] * 7
X.XDefaultRootWindow.restype = C.c_ulong
X.XDefaultRootWindow.argtypes = [C.c_void_p]
T.XTestFakeMotionEvent.argtypes = [C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_ulong]
T.XTestFakeButtonEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]
T.XTestFakeKeyEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]

ALIASES = {
    "ctrl": "Control_L", "shift": "Shift_L", "alt": "Alt_L", "enter": "Return", "esc": "Escape",
    "tab": "Tab", "up": "Up", "down": "Down", "left": "Left", "right": "Right", "space": "space",
    "|": "bar", "_": "underscore", ",": "comma", ".": "period", "[": "bracketleft",
    "]": "bracketright", "/": "slash", "bs": "BackSpace",
}


class Demo:
    """One sandbox slot: input, screenshots, captions, recording."""

    def __init__(self, slot, env=None):
        self.slot = slot
        self.display = f":{90 + slot}"
        self.env = dict(os.environ, **(env or {}))
        self.d = X.XOpenDisplay(self.display.encode())
        if not self.d:
            raise SystemExit(f"cannot open display {self.display}")
        self.pos = self.pointer()
        self.t0 = None
        self.captions = []  # (start, end, text), seconds from the recording start
        self.ff = None

    # ---- input -------------------------------------------------------------------------
    def flush(self):
        X.XFlush(self.d)
        X.XSync(self.d, 0)

    def pointer(self):
        root = X.XDefaultRootWindow(self.d)
        r, c = C.c_ulong(), C.c_ulong()
        rx, ry, wx, wy = C.c_int(), C.c_int(), C.c_int(), C.c_int()
        m = C.c_uint()
        X.XQueryPointer(self.d, root, C.byref(r), C.byref(c), C.byref(rx), C.byref(ry),
                        C.byref(wx), C.byref(wy), C.byref(m))
        return (rx.value, ry.value)

    def _warp(self, x, y):
        T.XTestFakeMotionEvent(self.d, -1, int(x), int(y), 0)
        self.flush()
        self.pos = (int(x), int(y))

    def move(self, x, y, dur=0.55):
        """Glide the pointer to (x, y) with an ease in/out, never teleport."""
        x0, y0 = self.pos
        steps = max(8, int(dur * 60))
        for i in range(1, steps + 1):
            t = i / steps
            e = t * t * (3 - 2 * t)
            self._warp(x0 + (x - x0) * e, y0 + (y - y0) * e)
            time.sleep(dur / steps)

    def _button(self, b, down):
        T.XTestFakeButtonEvent(self.d, int(b), 1 if down else 0, 0)
        self.flush()

    def click(self, x, y, button=1, count=1, dur=0.55, settle=0.25):
        self.move(x, y, dur)
        time.sleep(settle)
        for _ in range(count):
            self._button(button, True)
            time.sleep(0.05)
            self._button(button, False)
            time.sleep(0.09)

    def drag(self, x1, y1, x2, y2, dur=0.9):
        self.move(x1, y1)
        time.sleep(0.2)
        self._button(1, True)
        time.sleep(0.1)
        self.move(x2, y2, dur)
        time.sleep(0.1)
        self._button(1, False)

    def scroll(self, x, y, direction="down", n=3, gap=0.12):
        self.move(x, y, 0.4)
        for _ in range(n):
            self._button(4 if direction == "up" else 5, True)
            self._button(4 if direction == "up" else 5, False)
            time.sleep(gap)

    def _keycode(self, name):
        name = ALIASES.get(name.lower(), name) if len(name) > 1 or name in ALIASES else name
        ks = X.XStringToKeysym(name.encode())
        kc = X.XKeysymToKeycode(self.d, ks) if ks else 0
        if not kc:
            raise ValueError(f"unknown key {name!r}")
        return kc

    def key(self, *chords, gap=0.35):
        """key("ctrl+space", "o"): each chord is pressed and released in turn."""
        for spec in chords:
            parts = ["+"] if spec == "+" else (spec[:-2].split("+") + ["+"] if spec.endswith("++") else spec.split("+"))
            codes = [self._keycode(p) for p in parts if p]
            for kc in codes:
                T.XTestFakeKeyEvent(self.d, kc, 1, 0)
                self.flush()
                time.sleep(0.02)
            for kc in reversed(codes):
                T.XTestFakeKeyEvent(self.d, kc, 0, 0)
                self.flush()
                time.sleep(0.02)
            time.sleep(gap)

    def type(self, text, delay=0.045):
        shift = self._keycode("Shift_L")
        for ch in text:
            if ch == "\n":
                self.key("Return", gap=0.05)
                continue
            ks = ord(ch)
            kc = X.XKeysymToKeycode(self.d, ks)
            if not kc:
                continue
            need = X.XkbKeycodeToKeysym(self.d, kc, 0, 0) != ks
            if need:
                T.XTestFakeKeyEvent(self.d, shift, 1, 0)
            T.XTestFakeKeyEvent(self.d, kc, 1, 0)
            T.XTestFakeKeyEvent(self.d, kc, 0, 0)
            if need:
                T.XTestFakeKeyEvent(self.d, shift, 0, 0)
            self.flush()
            time.sleep(delay)

    def leader(self, *keys, gap=0.5):
        """LEADER (Ctrl+Space) followed by keys."""
        self.key("ctrl+space", gap=0.25)
        self.key(*keys, gap=gap)

    def focus(self):
        subprocess.run([HGQ, "focus", str(self.slot)], env=self.env, check=False, capture_output=True)

    # ---- sandbox -----------------------------------------------------------------------
    def hgq(self, *args, check=True):
        r = subprocess.run([HGQ, *args], env=self.env, capture_output=True, text=True)
        if check and r.returncode:
            raise RuntimeError(f"hgq {args}: {r.stderr or r.stdout}")
        return r.stdout

    def cli(self, *args):
        return self.hgq("cli", str(self.slot), *args)

    def panes(self):
        return json.loads(self.cli("list", "--format", "json"))

    def sh(self, *cmd):
        return self.hgq("sh", str(self.slot), *cmd)

    def shot(self, path, crop=None):
        self.hgq("shot", str(self.slot), path, *([crop] if crop else []))
        return path

    def text(self, pane_id):
        return self.cli("get-text", "--pane-id", str(pane_id))

    def wait_text(self, pane_id, needle, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            if needle in self.text(pane_id):
                return True
            time.sleep(0.5)
        return False

    # ---- recording ---------------------------------------------------------------------
    def now(self):
        return time.monotonic() - self.t0 if self.t0 else 0.0

    def start(self, out, size="1920x1080", fps=30):
        self.ff = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "x11grab", "-framerate", str(fps),
             "-draw_mouse", "1", "-video_size", size, "-i", self.display, "-c:v", "libx264",
             "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p", out],
            env=dict(self.env, DISPLAY=self.display), stdin=subprocess.PIPE)
        time.sleep(0.6)
        self.t0 = time.monotonic()

    def stop(self):
        if self.ff:
            self.ff.stdin.write(b"q")
            self.ff.stdin.flush()
            self.ff.wait(timeout=60)
            self.ff = None

    def caption(self, text, hold=3.0):
        """Show `text` as a subtitle for `hold` seconds from now (the script keeps acting)."""
        now = self.now()
        if self.captions and self.captions[-1][1] > now:  # one caption at a time
            self.captions[-1] = (self.captions[-1][0], now, self.captions[-1][2])
        self.captions.append((now, now + hold, text))

    def say(self, text, hold=3.0):
        """Caption and wait for it to be read."""
        self.caption(text, hold)
        time.sleep(hold)

    def write_ass(self, path, width=1920, height=1144):
        """Captions as ASS subtitles, centred in the strip below the picture (the last 64 px)."""
        def ts(s):
            return f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{s % 60:05.2f}"
        head = (
            "[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Cap,DejaVu Sans,31,&H00F4E8D6,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,40,40,15,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n") % (width, height)
        with open(path, "w") as f:
            f.write(head)
            for a, b, t in self.captions:
                f.write(f"Dialogue: 0,{ts(a)},{ts(b)},Cap,,0,0,0,,{t}\n")


class Phone:
    """The phone remote inside the recording: `hivegrid-remote serve` in the sandbox (loopback, own port)
    and a headed Chrome app window shaped like a phone on the same Xvfb display, beside the Hivegrid
    window, so the display capture records both. The page is driven with the Demo's XTest input."""

    CHROME = "/opt/google/chrome/chrome"

    def __init__(self, demo, port=18720, tmp=None):
        self.demo = demo
        self.port = port
        self.tmp = tmp or f"/tmp/hivegrid-demo-phone-{demo.slot}"
        self.server = None
        self.chrome = None
        self.win = None

    def _sandbox(self, *cmd):
        """Popen of `hgq sh SLOT cmd...`: the sandbox's HOME, XDG_RUNTIME_DIR and mux socket."""
        return subprocess.Popen([HGQ, "sh", str(self.demo.slot), *cmd], env=self.demo.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def start_server(self):
        remote = os.path.join(REPO, "bin", "hivegrid-remote")
        token = self.demo.hgq("sh", str(self.demo.slot), remote, "token", "--show", check=False).strip()
        if not token:
            token = self.demo.hgq("sh", str(self.demo.slot), remote, "token").strip().splitlines()[-1].strip()
        self.token = token
        self.server = self._sandbox(remote, "serve", "--bind", "127.0.0.1", "--port", str(self.port))
        for _ in range(60):
            r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"http://127.0.0.1:{self.port}/"],
                               capture_output=True, text=True)
            if r.stdout.strip() == "200":
                return
            time.sleep(0.5)
        raise RuntimeError("hivegrid-remote did not come up")

    def open(self, x, y, w, h, scale):
        """Chrome app window of w x h CSS px at x, y (screen pixels) scaled by `scale`: it takes its position
        in CSS px too, hence the division."""
        os.makedirs(self.tmp, exist_ok=True)
        env = {k: v for k, v in self.demo.env.items() if not k.startswith(("WAYLAND", "WEZTERM", "DBUS"))}
        env.update(DISPLAY=self.demo.display, XDG_SESSION_TYPE="x11")
        self.chrome = subprocess.Popen(
            [self.CHROME, f"--app=http://127.0.0.1:{self.port}/#t={self.token}", f"--user-data-dir={self.tmp}",
             f"--window-size={w},{h}", f"--window-position={round(x / scale)},{round(y / scale)}", f"--force-device-scale-factor={scale}",
             "--ozone-platform=x11", "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-sync",
             "--password-store=basic", "--use-mock-keychain", "--disable-features=Translate,InfiniteSessionRestore"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        time.sleep(3.0)

    def agents(self):
        """The agent list the server sends (the order the page shows it in), each with `label` and `state`."""
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/agents",
                                     headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)["agents"]

    def agents_by_label(self):
        return {a["label"]: a for a in self.agents()}

    def focus(self):
        """No window manager here: give the Chrome window the keyboard focus by hand."""
        out = subprocess.run(["xwininfo", "-root", "-tree"], env=dict(self.demo.env, DISPLAY=self.demo.display),
                             capture_output=True, text=True).stdout
        ids = [ln.split()[0] for ln in out.splitlines() if "Google-chrome" in ln]
        if ids:
            subprocess.run([sys.executable, os.path.join(REPO, "dev", "qa", "xin.py"), "focus", ids[0]],
                           env=dict(self.demo.env, DISPLAY=self.demo.display), capture_output=True)

    def close(self):
        for p in (self.chrome, self.server):
            if p and p.poll() is None:
                try:
                    os.killpg(p.pid, 15)
                except ProcessLookupError:
                    pass
        for p in (self.chrome, self.server):
            if p:
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, 9)
        self.chrome = self.server = None
        shutil.rmtree(self.tmp, ignore_errors=True)
