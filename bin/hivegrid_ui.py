"""Small terminal UI toolkit for the Hivegrid panels: a cell canvas with click regions, raw ANSI
output with truecolor, SGR mouse input, modal dialogs, inline images (iTerm2 protocol, rendered with
PIL when available) and braille charts as the image-free fallback. Standard library only."""
import base64
import collections
import datetime
import fcntl
import io
import json
import os
import re
import select
import signal
import struct
import sys
import termios
import textwrap
import time
import tty
import unicodedata

BOLD, DIM, REV, UL = 1, 2, 4, 8

TRUECOLOR = os.environ.get("COLORTERM", "") in ("truecolor", "24bit") or os.environ.get("TERM", "").endswith("-direct")


def rgb(h):
    """'#rrggbb' -> (r, g, b)."""
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def _to256(r, g, b):
    q = lambda v: 0 if v < 48 else 1 if v < 115 else (v - 35) // 40
    return 16 + 36 * q(r) + 6 * q(g) + q(b)


def sgr_color(h, bgnd):
    r, g, b = rgb(h)
    if TRUECOLOR:
        return f"{48 if bgnd else 38};2;{r};{g};{b}"
    return f"{48 if bgnd else 38};5;{_to256(r, g, b)}"


def mix(a, b, t):
    ra, ga, ba = rgb(a)
    rb, gb, bb = rgb(b)
    return "#%02x%02x%02x" % (round(ra + (rb - ra) * t), round(ga + (gb - ga) * t), round(ba + (bb - ba) * t))


# Never drawn: control characters (category Cc: ESC, BEL, C1 ...), the line/paragraph separators and
# the bidi controls. Page titles and URLs reach the panels from the outside; a drawn ESC would
# become an escape sequence in the panel's pty (OSC 52 clipboard writes, OSC 1337 user vars).
_UNSAFE = {c: None for c in (*range(0x20), *range(0x7F, 0xA0), 0x61C, 0x200E, 0x200F, 0x2028, 0x2029,
                             *range(0x202A, 0x202F), *range(0x2066, 0x206A))}


def printable_text(text):
    """`text` without the characters the canvas never draws (see _UNSAFE)."""
    return text.translate(_UNSAFE)


def char_width(ch):
    """Terminal cells of `ch`: 0 for combining marks, joiners and variation selectors, 2 for East
    Asian wide/fullwidth, else 1. Mirrors WezTerm's default layout (unicode_version 9, where
    U+FE0F does not widen a char) of the titles and screen text the panels show; omp titles
    carry emoji."""
    o = ord(ch)
    if o < 0x300:
        return 0 if o < 0x20 or 0x7F <= o < 0xA0 else 1  # control characters are never drawn
    if unicodedata.combining(ch) or o in (0x200B, 0x200C, 0x200D, 0xFE0E, 0xFE0F) or unicodedata.category(ch) == "Mn":
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def text_width(text):
    """Terminal cells `text` takes."""
    return sum(char_width(c) for c in text)


def fit_cells(text, w, ellipsis="…"):
    """`text` cut to at most `w` cells, ending in `ellipsis` when cut."""
    if text_width(text) <= w:
        return text
    room = w - text_width(ellipsis)
    out, n = [], 0
    for c in text:
        cw = char_width(c)
        if n + cw > room:
            break
        out.append(c)
        n += cw
    return "".join(out) + ellipsis if w > 0 else ""


# ---- canvas -------------------------------------------------------------------------


class Canvas:
    """A grid of (char, fg, bg, attrs) cells, click regions and image placements."""

    def __init__(self, w, h, fg, bg):
        self.w, self.h, self.fg, self.bg = w, h, fg, bg
        self.cells = [[(" ", fg, bg, 0)] * w for _ in range(h)]
        self.hits = []
        self.keys = []
        self.images = []

    def _clear_wide(self, row, cx):
        """Before cell cx is overwritten: blank the other half of a wide char it belongs to."""
        if row[cx][0] == "" and cx > 0:
            old = row[cx - 1]
            row[cx - 1] = (" ", old[1], old[2], old[3])
        if cx + 1 < self.w and row[cx + 1][0] == "":
            old = row[cx + 1]
            row[cx + 1] = (" ", old[1], old[2], old[3])

    def put(self, x, y, text, fg=None, bg=None, attrs=0):
        """Writes `text` at (x, y), clipped to the canvas; returns the cells written. A wide char
        takes two cells (the second holds "" and is skipped by render); zero-width chars join the
        char before them."""
        if y < 0 or y >= self.h:
            return 0
        row = self.cells[y]
        n, base = 0, None  # base: cell of the last char written, for zero-width chars to join
        for ch in printable_text(text):
            cw = char_width(ch)
            if cw == 0:
                if base is not None:
                    old = row[base]
                    row[base] = (old[0] + ch, old[1], old[2], old[3])
                continue
            cx = x + n
            n += cw
            base = None
            if not 0 <= cx < self.w:
                continue
            self._clear_wide(row, cx)
            old = row[cx]
            if cw == 2 and cx + 1 >= self.w:
                row[cx] = (" ", fg or old[1], bg or old[2], attrs)
                continue
            row[cx] = (ch, fg or old[1], bg or old[2], attrs)
            base = cx
            if cw == 2:
                self._clear_wide(row, cx + 1)
                nxt = row[cx + 1]
                row[cx + 1] = ("", fg or nxt[1], bg or nxt[2], attrs)
        return n

    def fill(self, x, y, w, h, bg=None, fg=None, ch=" "):
        for yy in range(max(y, 0), min(y + h, self.h)):
            row = self.cells[yy]
            for xx in range(max(x, 0), min(x + w, self.w)):
                self._clear_wide(row, xx)
                old = row[xx]
                row[xx] = (ch, fg or old[1], bg or old[2], 0)

    def hit(self, x, y, w, h, fn, key=None):
        """Registers a click region; the last one registered wins. fn(button) with 'left' or 'right'.
        A `key` names the region for hover effects (see key_at)."""
        self.hits.append((x, y, w, h, fn))
        if key is not None:
            self.keys.append((x, y, w, h, key))

    def hit_at(self, x, y):
        for rx, ry, rw, rh, fn in reversed(self.hits):
            if rx <= x < rx + rw and ry <= y < ry + rh:
                return fn
        return None

    def key_at(self, x, y):
        """The key of the last registered keyed region under (x, y), or None."""
        for rx, ry, rw, rh, key in reversed(self.keys):
            if rx <= x < rx + rw and ry <= y < ry + rh:
                return key
        return None

    def image(self, x, y, w, h, png):
        self.fill(x, y, w, h)
        self.images.append((x, y, w, h, png))

    def render(self):
        out = []
        for y, row in enumerate(self.cells):
            out.append(f"\x1b[{y + 1};1H")
            last = None
            run = []
            for ch, fg, bg, attrs in row:
                style = (fg, bg, attrs)
                if style != last:
                    if run:
                        out.append(printable_text("".join(run)))
                        run = []
                    codes = ["0"]
                    if attrs & BOLD:
                        codes.append("1")
                    if attrs & DIM:
                        codes.append("2")
                    if attrs & UL:
                        codes.append("4")
                    if attrs & REV:
                        codes.append("7")
                    codes.append(sgr_color(fg, False))
                    codes.append(sgr_color(bg, True))
                    out.append("\x1b[" + ";".join(codes) + "m")
                    last = style
                run.append(ch)
            out.append(printable_text("".join(run)))
        out.append("\x1b[0m")
        return "".join(out)


# ---- input --------------------------------------------------------------------------

ARROWS = {"A": "UpArrow", "B": "DownArrow", "C": "RightArrow", "D": "LeftArrow", "H": "Home", "F": "End"}
TILDE = {"1": "Home", "2": "Insert", "3": "Delete", "4": "End", "5": "PageUp", "6": "PageDown", "7": "Home",
         "8": "End", "11": "F1", "12": "F2", "13": "F3", "14": "F4", "15": "F5", "17": "F6", "18": "F7",
         "19": "F8", "20": "F9", "21": "F10", "23": "F11", "24": "F12"}
SS3 = {"P": "F1", "Q": "F2", "R": "F3", "S": "F4", "A": "UpArrow", "B": "DownArrow", "C": "RightArrow",
       "D": "LeftArrow", "H": "Home", "F": "End"}


def _mods_from_param(n):
    n -= 1
    return {name for bit, name in ((1, "SHIFT"), (2, "ALT"), (4, "CTRL"), (8, "SUPER")) if n & bit}


def _plain(c):
    o = ord(c)
    if o == 0:
        return {"CTRL"}, "Space"
    if c == "\t":
        return set(), "Tab"
    if c in "\r\n":
        return set(), "Enter"
    if o in (8, 127):
        return set(), "Backspace"
    if 1 <= o <= 26:
        return {"CTRL"}, chr(o + 96)
    if 28 <= o <= 31:
        return {"CTRL"}, "\\]^_"[o - 28]
    if c == " ":
        return set(), "Space"
    if c.isalpha() and c.isupper():
        return {"SHIFT"}, c.lower()
    return set(), c


MOUSE_RE = re.compile(r"\[<(\d+);(\d+);(\d+)([Mm])")
CSI_RE = re.compile(r"\[(\d*)(?:;(\d+))?([A-Za-z~])")
CSI_U_RE = re.compile(r"\[(\d+)(?:;(\d+))?u")


def decode(data):
    """Bytes from the tty -> events: ('key', mods, key) or ('mouse', action, button, x, y).
    action: press | release | drag | wheel; button: left | middle | right | up | down; x, y 0-based."""
    out = []
    s = data.decode("utf-8", "replace")
    i = 0
    while i < len(s):
        c = s[i]
        if c != "\x1b":
            mods, key = _plain(c)
            out.append(("key", frozenset(mods), key))
            i += 1
            continue
        rest = s[i + 1:]
        m = MOUSE_RE.match(rest)
        if m:
            b, x, y, kind = int(m.group(1)), int(m.group(2)) - 1, int(m.group(3)) - 1, m.group(4)
            if b & 64:
                out.append(("mouse", "wheel", "up" if b & 1 == 0 else "down", x, y))
            else:
                button = {0: "left", 1: "middle", 2: "right"}.get(b & 3, "left")
                action = "release" if kind == "m" else ("drag" if b & 32 else "press")
                out.append(("mouse", action, button, x, y))
            i += 1 + m.end()
            continue
        m_u = CSI_U_RE.match(rest)
        m = CSI_RE.match(rest)
        if m_u:
            code = int(m_u.group(1))
            mods = _mods_from_param(int(m_u.group(2) or 1))
            key = {9: "Tab", 13: "Enter", 27: "Escape", 32: "Space", 127: "Backspace"}.get(code, chr(code))
            out.append(("key", frozenset(mods), key))
            i += 1 + m_u.end()
        elif m:
            num, mod, final = m.group(1), m.group(2), m.group(3)
            mods = _mods_from_param(int(mod)) if mod else set()
            if final == "Z":
                out.append(("key", frozenset({"SHIFT"}), "Tab"))
            elif final == "~":
                out.append(("key", frozenset(mods), TILDE.get(num, "?")))
            else:
                out.append(("key", frozenset(mods), ARROWS.get(final, "?")))
            i += 1 + m.end()
        elif rest[:1] == "O" and len(rest) > 1:
            out.append(("key", frozenset(), SS3.get(rest[1], "?")))
            i += 3
        elif rest:
            mods, key = _plain(rest[0])
            out.append(("key", frozenset(mods | {"ALT"}), key))
            i += 2
        else:
            out.append(("key", frozenset(), "Escape"))
            i += 1
    return out


def split_complete(data):
    """-> (head, tail): `tail` is an escape sequence or UTF-8 character that the read boundary
    cut off (motion reports arrive fast enough to be split); prepend it to the next read. A lone
    ESC also waits in `tail`: the caller decodes it as Escape when nothing follows soon."""
    i = data.rfind(b"\x1b")
    if i >= 0:
        rest = data[i + 1:]
        if not rest or rest == b"O" or (rest[:1] == b"[" and not re.search(rb"[\x40-\x7e]", rest[1:])):
            return data[:i], data[i:]
    for k in range(1, min(4, len(data)) + 1):
        b = data[-k]
        if b & 0xC0 == 0x80:  # continuation byte: look further back for the lead byte
            continue
        if b >= 0xC0 and (2 if b < 0xE0 else 3 if b < 0xF0 else 4) > k:
            return data[:-k], data[-k:]
        break
    return data, b""


# ---- images and charts --------------------------------------------------------------

try:
    from PIL import Image, ImageDraw, ImageFont  # noqa: F401
    HAVE_PIL = True
except Exception:  # noqa: BLE001 - any import problem means no images
    HAVE_PIL = False


def images_enabled():
    """Inline images need PIL and a terminal that speaks the iTerm2 protocol (WezTerm does)."""
    if os.environ.get("HIVEGRID_NO_IMAGES") == "1" or not HAVE_PIL:
        return False
    if os.environ.get("HIVEGRID_IMAGES") == "1":
        return True
    return os.environ.get("TERM_PROGRAM") == "WezTerm" or "WEZTERM_PANE" in os.environ or os.environ.get("LC_TERMINAL") == "iTerm2"


def cell_pixels(fd=1):
    """Pixel size of one cell from the tty's window size; a typical size when unreported."""
    try:
        rows, cols, xpix, ypix = struct.unpack("HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8))
        if xpix and ypix and cols and rows:
            return max(xpix // cols, 4), max(ypix // rows, 8)
    except OSError:
        pass
    return 9, 18


def iterm_image(png, cols, rows):
    """iTerm2 inline image escape for `png` stretched over cols x rows cells."""
    data = base64.b64encode(png).decode()
    return f"\x1b]1337;File=inline=1;size={len(png)};width={cols};height={rows};preserveAspectRatio=0:{data}\x07"


_FONT_CACHE = {}


def font(size):
    if size not in _FONT_CACHE:
        for path in (os.path.expanduser("~/.local/share/fonts/DejaVuSansMNerdFont/DejaVuSansMNerdFontMono-Regular.ttf"),
                     "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                     "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"):
            try:
                _FONT_CACHE[size] = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
        else:
            _FONT_CACHE[size] = ImageFont.load_default()
    return _FONT_CACHE[size]


_TICK_STEPS = (900, 1800, 3600, 2 * 3600, 3 * 3600, 4 * 3600, 6 * 3600, 12 * 3600, 86400, 2 * 86400, 7 * 86400)


def time_ticks(x0, x1, n=6):
    """At most about n ticks in [x0, x1] on whole local wall-clock times (quarter hours, hours,
    midnights; DST-safe) -> [(t, label)]. A tick on a midnight shows the date, so day changes stand
    out; multi-day steps start on even days or Mondays."""
    span = max(x1 - x0, 1)
    step = next((s for s in _TICK_STEPS if span / s <= max(n, 1)), _TICK_STEPS[-1])
    start = datetime.datetime.fromtimestamp(x0)
    midnight = start.replace(hour=0, minute=0, second=0, microsecond=0)
    if step >= 86400:
        days = step // 86400
        wall = midnight if midnight.timestamp() >= x0 else midnight + datetime.timedelta(days=1)
        while wall.toordinal() % days != (1 if days == 7 else 0) % days:  # ordinal 1 is a Monday
            wall += datetime.timedelta(days=1)
        delta = datetime.timedelta(days=days)
    else:
        sec = (start - midnight).total_seconds()
        wall = midnight + datetime.timedelta(seconds=-(-sec // step) * step)
        delta = datetime.timedelta(seconds=step)
    out = []
    while (t := wall.timestamp()) <= x1:
        if t >= x0:
            day = step >= 86400 or (wall.hour == 0 and wall.minute == 0)
            out.append((t, wall.strftime("%d %b" if day else "%H:%M")))
        wall += delta
    return out


def runs_of(pts, gap=None):
    """[(t, v)] split where two neighbours are more than `gap` apart (no samples in between), so a
    chart does not draw a straight line across a stretch nobody recorded."""
    runs = []
    for i, pt in enumerate(pts):
        if not runs or (gap is not None and pt[0] - pts[i - 1][0] > gap):
            runs.append([])
        runs[-1].append(pt)
    return runs


def line_chart_png(width, height, series, x0, x1, ymax, pal, markers=(), unit="%", ymin=0.0, title=None,
                   since=None, marker_label=None, text_px=11, gap=None, since_label=None):
    """series: [{'pts': [(t, v)], 'color', 'label'}]; markers: [(t, color)] vertical dotted lines,
    named `marker_label` in the legend; the plot left of `since` is shaded as having no data
    (named `since_label` inside the shaded span when it fits there, never over the data) and a
    line breaks where samples are more than `gap` apart.
    Text is `text_px` high (pass ~0.7 of the cell height so it matches the terminal font) and
    the margins follow it. Drawn at 2x and scaled down for smooth lines. -> PNG bytes."""
    S = 2
    K = S * text_px / 11  # layout unit: 1 px at the 11 px design size, supersampled
    W, H = width * S, height * S
    img = Image.new("RGB", (W, H), pal["base"])
    d = ImageDraw.Draw(img)
    f = font(round(text_px * S))
    left, right, top, bottom = 44 * K, 10 * K, 22 * K, 20 * K
    pw, ph = W - left - right, H - top - bottom
    if pw < 10 or ph < 10:
        return _png(img.resize((width, height)))
    d.rectangle([left, top, left + pw, top + ph], fill=pal["deep"])
    span = max(x1 - x0, 1)
    ysp = max(ymax - ymin, 1e-9)

    def px(t):
        return left + (t - x0) / span * pw

    def py(v):
        return top + ph - (v - ymin) / ysp * ph

    if since is not None and since > x0:
        nx = px(min(since, x1))
        d.rectangle([left, top, nx, top + ph], fill=pal["base"])
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        y = top + ph - frac * ph
        d.line([left, y, left + pw, y], fill=pal["border"], width=S)
        label = f"{ymin + ysp * frac:.0f}{unit}"
        d.text((left - 4 * K, y), label, fill=pal["muted"], font=f, anchor="rm")
    if since_label and since is not None and since > x0:
        nx = px(min(since, x1))
        tw = d.textlength(since_label, font=f)
        if nx - left >= tw + 16 * K:
            cx, cy = (left + nx) / 2, top + ph * 0.375  # between two grid lines
            d.text((cx, cy), since_label, fill=pal["dim"], font=f, anchor="mm")
    for t, label in time_ticks(x0, x1, min(8, int(pw // (110 * K)))):
        x = px(t)
        d.line([x, top + ph, x, top + ph + 3 * K], fill=pal["muted"], width=S)
        half = d.textlength(label, font=f) / 2
        d.text((min(max(x, left + half), W - half - 2 * S), top + ph + 5 * K), label, fill=pal["muted"], font=f, anchor="ma")
    for t, color in markers:
        if x0 <= t <= x1:
            x = px(t)
            for yy in range(int(top), int(top + ph), 6 * S):
                d.line([x, yy, x, min(yy + 3 * S, top + ph)], fill=color, width=S)
    for s in series:
        for run in runs_of([(t, v) for t, v in s["pts"] if x0 <= t <= x1], gap):
            pts = [(px(t), py(v)) for t, v in run]
            if len(pts) >= 2:
                d.line(pts, fill=s["color"], width=2 * S, joint="curve")
            else:
                x, y = pts[0]
                d.ellipse([x - 2 * S, y - 2 * S, x + 2 * S, y + 2 * S], fill=s["color"])
    if title:
        d.text((left, 3 * K), title, fill=pal["bright"], font=f, anchor="la")
    lx = left + pw
    for s in reversed(series):
        w = d.textlength(s["label"], font=f)
        lx -= w + 22 * K
        d.line([lx, 10 * K, lx + 12 * K, 10 * K], fill=s["color"], width=3 * S)
        d.text((lx + 16 * K, 10 * K), s["label"], fill=pal["text"], font=f, anchor="lm")
    if marker_label and any(x0 <= t <= x1 for t, _ in markers):
        lx -= d.textlength(marker_label, font=f) + 22 * K
        for yy in (3 * K, 9 * K, 15 * K):
            d.line([lx + 6 * K, yy, lx + 6 * K, yy + 3 * K], fill=pal["muted"], width=S)
        d.text((lx + 16 * K, 10 * K), marker_label, fill=pal["text"], font=f, anchor="lm")
    return _png(img.resize((width, height), Image.LANCZOS))


def sparkline_png(width, height, values, color, pal, label, value_text, ymax=100.0, value_color=None, text_px=12):
    """A filled mini graph with its label and current value, for the monitor. `values` are evenly
    spaced over the width, oldest first; None leaves a gap (not sampled yet). Text is `text_px`
    high and the margins follow it."""
    S = 2
    K = S * text_px / 12
    W, H = width * S, height * S
    img = Image.new("RGB", (W, H), pal["deep"])
    d = ImageDraw.Draw(img)
    f = font(round(text_px * S))
    d.rectangle([0, 0, W - 1, H - 1], outline=pal["border"], width=S)
    d.text((8 * K, 5 * K), label, fill=pal["muted"], font=f, anchor="la")
    d.text((W - 8 * K, 5 * K), value_text, fill=value_color or color, font=f, anchor="ra")
    top, bottom = 22 * K, H - 6 * K
    n = len(values)
    if n >= 2 and bottom - top > 4:
        mid = (top + bottom) / 2  # dashed 50% line
        for xx in range(int(8 * K), int(W - 8 * K), 8 * S):
            d.line([xx, mid, xx + 3 * S, mid], fill=pal["border"], width=S)
        runs = []
        prev = None
        for i, v in enumerate(values):
            if v is not None:
                if prev is None:
                    runs.append([])
                runs[-1].append((8 * K + i / (n - 1) * (W - 16 * K), bottom - min(max(v, 0), ymax) / ymax * (bottom - top)))
            prev = v
        fill = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        fd = ImageDraw.Draw(fill)
        r, g, b = rgb(color)
        for pts in runs:
            if len(pts) >= 2:
                fd.polygon(pts + [(pts[-1][0], bottom), (pts[0][0], bottom)], fill=(r, g, b, 70))
        img.paste(fill, (0, 0), fill)
        for pts in runs:
            if len(pts) >= 2:
                d.line(pts, fill=color, width=2 * S, joint="curve")
        d.line([8 * K, bottom, W - 8 * K, bottom], fill=pal["border"], width=S)
    return _png(img.resize((width, height), Image.LANCZOS))


def _png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


BRAILLE_BITS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))


def braille_chart(cols, rows, series, x0, x1, ymax, ymin=0.0, gap=None):
    """Line chart in braille dots. series: [{'pts': [(t, v)], 'color'}]. -> rows of [(char, color)]
    with each series drawn in its own colour (a cell keeps the colour of the last series that set it);
    a line breaks where samples are more than `gap` apart."""
    dw, dh = cols * 2, rows * 4
    dots = [[0] * cols for _ in range(rows)]
    owner = [[None] * cols for _ in range(rows)]
    span = max(x1 - x0, 1)
    ysp = max(ymax - ymin, 1e-9)

    def to_dot(t, v):
        x = round((t - x0) / span * (dw - 1))
        y = round((1 - (min(max(v, ymin), ymax) - ymin) / ysp) * (dh - 1))
        return x, y

    def plot(x, y, color):
        if 0 <= x < dw and 0 <= y < dh:
            cx, cy = x // 2, y // 4
            dots[cy][cx] |= BRAILLE_BITS[y % 4][x % 2]
            owner[cy][cx] = color

    for s in series:
        for run in runs_of([(t, v) for t, v in s["pts"] if x0 <= t <= x1], gap):
            pts = [to_dot(t, v) for t, v in run]
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                steps = max(abs(bx - ax), abs(by - ay), 1)
                for k in range(steps + 1):
                    plot(round(ax + (bx - ax) * k / steps), round(ay + (by - ay) * k / steps), s["color"])
            if len(pts) == 1:
                plot(pts[0][0], pts[0][1], s["color"])
    return [[(chr(0x2800 + dots[r][c]) if dots[r][c] else " ", owner[r][c]) for c in range(cols)] for r in range(rows)]


# ---- design components ----------------------------------------------------------------
# The one look of every Hivegrid TUI (DesignAudit §4): the full-tab panels (UI / Page), the
# docked programs (overview dock, git view) and the picker all draw these into a Canvas, so a
# title row, a footer, a list row or a chip looks and behaves the same everywhere.


class State(collections.namedtuple("State", "glyph label color rank")):
    """An agent state: glyph, the word shown for it, palette key of its colour, sort rank
    (1 = needs the eye most)."""


# The state table, shared with config/theme.lua (theme.STATES): keep both in step.
STATES = {
    "attention": State("?", "needs you", "violet", 1),
    "limit": State("⊘", "limit", "red", 2),
    "bell": State("!", "bell", "red", 3),
    "done": State("✓", "done", "green", 4),
    "busy": State("◔", "busy", "amber", 5),
    "working": State("↻", "working", "accent", 6),
    "idle": State("·", "idle", "grey", 7),
    "exited": State("○", "exited", "muted", 8),
    "web": State("◍", "web", "cyan", 9),
    "other": State("›", "running", "dim", 10),
    "shell": State("›", "shell", "dim", 11),
}
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"  # drawn instead of ↻ while something runs, at 8 frames a second

# The Graphite palette (config/theme.lua M.P): what a program draws with before it knows the theme.
DEFAULT_PAL = {
    "deep": "#070a10", "base": "#0b0f16", "surface": "#111722", "pill": "#162030", "raised": "#222f45",
    "track": "#1c2535", "border": "#293345", "edge_hi": "#4a5d7d", "edge_lo": "#0a0e15", "text": "#e2e8f2",
    "bright": "#f5f8fc", "muted": "#8d9ab0", "dim": "#7a879d", "accent": "#78a9ff", "cyan": "#5cc8f0",
    "green": "#4ade80", "amber": "#f5a524", "red": "#f87171", "violet": "#a78bfa", "grey": "#8590a5",
}

# Hover: what the pointer is over fades HOVER_BLEND of the way toward `raised` in FADE_IN seconds
# and back in FADE_OUT, drawn in STEPS shades, a frame every FRAME seconds while a fade runs.
HOVER_BLEND, FADE_IN, FADE_OUT, STEPS, FRAME = 0.45, 0.15, 0.20, 8, 1 / 30
TITLE_GLYPH = "⬡"
MARKER = "▎"  # the selection marker at the left edge of a selected row
CAP_L, CAP_R = "\ue0b6", "\ue0b4"  # rounded chip ends (WezTerm draws these itself)
CONTENT_MAX = 110  # columns of content on a wide panel; the rest is margin
STATUS_COLORS = {"ok": "green", "warn": "amber", "error": "red"}
STRIPE = 0.16  # dense lists (no blank between rows): every second row this share from bg to raised


def state_style(pal, kind, now=None):
    """(glyph, label, colour) of state `kind` (unknown kinds read as idle). With `now` a
    working state shows the spinner frame of that moment instead of ↻."""
    s = STATES.get(kind) or STATES["idle"]
    glyph = SPINNER[int(now * 8) % len(SPINNER)] if now is not None and kind == "working" else s.glyph
    return glyph, s.label, pal.get(s.color) or DEFAULT_PAL[s.color]


def rule_color(pal, bg):
    """Colour of title-row and table rules on background `bg` (T3): readable on every surface."""
    return mix(bg, pal["edge_hi"], 0.45)


def hover_bg(pal, bg, level):
    """`bg` faded toward `raised` by a hover at level 0..1."""
    return mix(bg, pal["raised"], HOVER_BLEND * level) if level else bg


def fit_tail(text, w, ellipsis="…"):
    """`text` cut from the left to at most `w` cells: "…end of it"."""
    if text_width(text) <= w:
        return text
    if w <= text_width(ellipsis):
        return fit_cells(text, w, ellipsis)
    out, n = [], text_width(ellipsis)
    for c in reversed(text):
        if n + char_width(c) > w:
            break
        out.append(c)
        n += char_width(c)
    return ellipsis + "".join(reversed(out))


def fit_path(text, w):
    """A path shortened from the left by whole components so its last part stays readable:
    g1-workspace/reference/unitree_ros2 -> …/reference/unitree_ros2; the last part alone is cut
    at its end."""
    if text_width(text) <= w:
        return text
    parts = text.split("/")
    for k in range(1, len(parts)):
        short = "…/" + "/".join(parts[k:])
        if text_width(short) <= w:
            return short
    return fit_cells(parts[-1], w)


def fit_text(text, w, how="end"):
    """`text` fitted into `w` cells: how='end' cuts the end, 'start' the start, 'path' whole path
    components from the start."""
    if w <= 0:
        return ""
    return fit_path(text, w) if how == "path" else fit_tail(text, w) if how == "start" else fit_cells(text, w)


def home_path(path):
    """`path` with the home directory as ~ and without a trailing slash (but "/" stays)."""
    path = str(path or "")
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    home = os.path.expanduser("~").rstrip("/")
    if home and (path == home or path.startswith(home + "/")):
        return "~" + path[len(home):]
    return path


Title = collections.namedtuple("Title", "left right body")


class Chip(collections.namedtuple("Chip", "label hit key hover kind selected round")):
    """A chip for title_row(chips=...); fields as chip()'s arguments."""


Chip.__new__.__defaults__ = (None, None, 0.0, "button", False, False)


def title_row(cv, pal, title, crumb="", right=(), *, chips=(), x=0, y=0, w=None, bg=None, rule_bg=None,
              reserve=0, crumb_fit="path", rule=True):
    """The title row (§4.2 rule 1) at row y over x..x+w: " ⬡ Title" in accent bold, " · crumb" in
    muted, then `right` segments [(text, colour)] right-aligned 1 cell apart, then `chips`
    [Chip] (left to right, 1 cell apart) at the right edge, dropped from the left when they would
    come within 2 cells of the title; 1 cell of padding is always kept, and
    `reserve` more cells are left free for anything the caller draws there. Row y+1 is the rule in
    rule_color on `rule_bg` (default `bg`). When narrow the crumb shrinks down to 8 cells
    (crumb_fit: 'path', 'start' or 'end'), then right segments drop from the end (list them
    most important first). -> Title(
    left=column after the crumb, right=column where the right segments start, body=first row
    under the title)."""
    w = cv.w - x if w is None else w
    bg = bg or pal["surface"]
    end = x + w
    cv.fill(x, y, w, 1, bg=bg)
    title_text = f"{TITLE_GLYPH} {title}"
    lx = x + 1 + cv.put(x + 1, y, fit_cells(title_text, max(end - 1 - reserve - (x + 1), 0)), pal["accent"], bg, BOLD)
    sep = " · "
    right = [(t, c) for t, c in right if t]
    limit = end - 1 - reserve
    # The chips never overdraw the title: when they do not fit beside it (2 cells apart) they drop
    # from the left, so the first chip listed goes first (list them least important first).
    chips = list(chips)
    while chips and limit - (sum(chip_width(c.label, c.round) + 1 for c in chips) - 1) - 2 < lx:
        chips = chips[1:]
    if chips:
        limit -= sum(chip_width(c.label, c.round) + 1 for c in chips) - 1
        cx = limit
        for c in chips:
            cx += chip(cv, pal, cx, y, c.label, kind=c.kind, selected=c.selected, hover=c.hover, bg=bg,
                       hit=c.hit, key=c.key, round=c.round) + 1
        limit -= 1

    def right_w(segs):
        return sum(text_width(t) + 1 for t, _ in segs) - 1 if segs else 0
    # Two cells between the title (or crumb) and the right group (§4.2 rule 8).
    while right and (limit - right_w(right) - 2) - lx - (len(sep) + min(8, text_width(crumb)) if crumb else 0) < 0:
        right = right[:-1]
    rx = limit - right_w(right)
    if crumb:
        room = (rx - 2 if right else limit) - lx - len(sep)
        if room > 0:
            lx += cv.put(lx, y, sep, pal["muted"], bg)
            lx += cv.put(lx, y, fit_text(crumb, room, crumb_fit), pal["muted"], bg)
    cx = rx
    for t, c in right:
        cx += cv.put(cx, y, t, c, bg) + 1
    if not rule:
        return Title(lx, rx if right else limit, y + 1)
    rb = rule_bg or bg
    cv.fill(x, y + 1, w, 1, bg=rb, fg=rule_color(pal, rb), ch="─")
    return Title(lx, rx if right else limit, y + 2)


def hint_pairs(hints):
    """Key hints as [(key, label)] from a list of pairs or the string form "key label  key label"."""
    if isinstance(hints, str):
        return [tuple(pair.partition(" ")[::2]) for pair in hints.split("  ") if pair.strip()]
    return [(k, label) for k, label in hints]


def footer_fit(hints, width):
    """The leading hint pairs that fit `width` cells: two cells between pairs, a pair is
    "key label"; pairs drop from the end."""
    out, used = [], 0
    for key, label in hint_pairs(hints):
        need = text_width(key) + (1 + text_width(label) if label else 0) + (2 if out else 0)
        if used + need > width:
            break
        out.append((key, label))
        used += need
    return out


def footer(cv, pal, hints, *, x=1, y=None, w=None, bg=None, right=None):
    """The footer (§4.2 rule 6) on row y (default the last): key in `text`, label in `muted`, two
    cells between pairs, pairs dropped from the end when narrow. `right` = (text, colour) sits
    right-aligned (a position, a note) and keeps 2 cells from the hints; it goes when it does not
    fit at all. Draws over x..x+w (default 1 cell of padding each side); -> column after the hints."""
    y = cv.h - 1 if y is None else y
    w = cv.w - 2 * x if w is None else w
    bg = bg or pal["surface"]
    end = x + w
    limit = w
    if right and right[0] and text_width(right[0]) <= w:
        rw = text_width(right[0])
        cv.put(end - rw, y, right[0], right[1], bg)
        limit = w - rw - 2
    for k, (key, label) in enumerate(footer_fit(hints, limit)):
        x += 2 if k else 0
        x += cv.put(x, y, key, pal["text"], bg)
        if label:
            x += cv.put(x, y, " " + label, pal["muted"], bg)
    return x


def status_line(cv, pal, y, text, level="ok", *, x=1, w=None, bg=None):
    """A status message (the row above the footer): green ok, amber warn, red error."""
    w = cv.w - 2 * x if w is None else w
    cv.put(x, y, fit_cells(text, w), pal[STATUS_COLORS.get(level, level)], bg)


Row = collections.namedtuple("Row", "bg name_x end")


class Action(collections.namedtuple("Action", "label hit key hover color")):
    """A row action for list_row(action=...): `label` (a glyph like ✕), hit(button), hover key,
    hover level 0..1, and the palette colour it lights up in (default accent)."""


Action.__new__.__defaults__ = (None, None, 0.0, None)


def list_row(cv, pal, y, *, x=0, w=None, lines=1, selected=False, hover=0.0, bg=None,
             glyph=None, glyph_color=None, num=None, num_w=1, num_color=None,
             name="", name_color=None, name_attrs=None, name_fit="end",
             right="", right_color=None, sub="", sub_color=None, sub_fit="end", hit=None, key=None, action=None):
    """A list row (§4.2 rules 3-4) of `lines` rows at y over x..x+w. Selected: `raised` slab, the
    ▎ marker at x in accent, bright bold name, the number in accent (num_color, when given, wins
    always: a mark such as a CI result keeps its colour); hover (0..1): the slab
    fades toward `raised`. Columns: glyph at x+2 (bold; glyph=None leaves the column out), number
    at x+4 (`num_w` cells; num=None leaves it out; any short mark fits there), the name after
    them, `right` right-aligned with 1 cell of padding (dropped when the name would get fewer
    than 4 cells), `sub` on the second line at the name column. hit(button) and `key` register
    the whole row as a click/hover region. `action` (an Action; pass it only on the selected or
    hovered row, §4.2 rule 5) is " label " at the right end of the first line with its own
    region, `right` moves left of it. -> Row(bg, name_x, end): end is the column the content
    stops before, for callers that draw their own columns."""
    w = cv.w - x if w is None else w
    bg = bg or pal["surface"]
    slab = pal["raised"] if selected else hover_bg(pal, bg, hover)
    end = x + w - 1
    cv.fill(x, y, w, lines, bg=slab)
    if selected:
        cv.fill(x, y, 1, lines, fg=pal["accent"], ch=MARKER)
    if hit is not None or key is not None:
        cv.hit(x, y, w, lines, hit or (lambda button: None), key=key)
    if action is not None:
        aw = text_width(action.label) + 2
        ax = x + w - aw
        lit = pal.get(action.color or "accent", pal["accent"])
        cv.put(ax, y, f" {action.label} ", mix(pal["muted"], pal["bright"], action.hover),
               mix(slab, lit, 0.45 * action.hover), BOLD)
        if action.hit is not None or action.key is not None:
            cv.hit(ax, y, aw, 1, action.hit or (lambda button: None), key=action.key)
        end = ax
    nx = x + 2
    if glyph is not None:
        cv.put(nx, y, glyph, glyph_color or pal["muted"], slab, BOLD)
        nx += 2
    if num is not None:
        cv.put(nx, y, str(num), num_color or (pal["accent"] if selected else pal["muted"]), slab, BOLD)
        nx += num_w + 1
    rw = text_width(right) if right else 0
    if rw and end - nx - rw - 1 < 4:
        rw = 0
    if rw:
        cv.put(end - rw, y, right, right_color or pal["dim"], slab)
    attrs = (BOLD if selected else 0) if name_attrs is None else name_attrs
    room = end - nx - (rw + 1 if rw else 0)
    cv.put(nx, y, fit_text(name, room, name_fit), pal["bright"] if selected else name_color or pal["text"], slab, attrs)
    if sub and lines > 1:
        cv.put(nx, y + 1, fit_text(sub, x + w - 1 - nx, sub_fit), sub_color or pal["muted"], slab)
    return Row(slab, nx, end)


def chip_width(label, round=False):
    return text_width(label) + 2 + (2 if round else 0)


def chip(cv, pal, x, y, label, *, kind="tab", selected=False, hover=0.0, bg=None, hit=None, key=None, round=False):
    """A chip " label " (§4.2 rules 2 and 5) on background `bg`; -> its width. kind 'tab':
    selected = raised + bright bold, else muted with a hover fade toward raised. 'button':
    neutral, bright on raised (hover toward accent). 'primary': deep bold on accent. 'disabled':
    dim on pill, not clickable. round=True adds rounded ends (one cell each). hit(button) and
    `key` make the whole chip a click/hover region."""
    bg = bg or pal["surface"]
    attrs = 0
    if kind == "primary":
        body, fg, attrs = mix(pal["accent"], pal["bright"], 0.25 * hover), pal["deep"], BOLD
    elif kind == "button":
        body, fg = mix(pal["raised"], pal["accent"], HOVER_BLEND * hover), pal["bright"]
    elif kind == "disabled":
        body, fg, hit, key = pal["pill"], pal["dim"], None, None
    elif selected:
        body, fg, attrs = pal["raised"], pal["bright"], BOLD
    else:
        body, fg = hover_bg(pal, bg, hover), pal["muted"]
    width = chip_width(label, round)
    cx = x
    if round:
        cx += cv.put(cx, y, CAP_L, body, bg)
    cx += cv.put(cx, y, f" {label} ", fg, body, attrs)
    if round:
        cv.put(cx, y, CAP_R, body, bg)
    if hit is not None or key is not None:
        cv.hit(x, y, width, 1, hit or (lambda button: None), key=key)
    return width


def empty_state(cv, pal, x, y, w, headline, text="", *, bg=None):
    """An empty or error state (§4.2 rule 10): headline in `text`, then the explanation in muted,
    wrapped (never cut). -> the row after it."""
    cv.put(x, y, fit_cells(headline, w), pal["text"], bg)
    y += 1
    for line in textwrap.wrap(text, max(w, 1)) if text else ():
        cv.put(x, y, line, pal["muted"], bg)
        y += 1
    return y


def content_box(w, cap=CONTENT_MAX, pad=1):
    """(x, width) of content capped at `cap` columns and centred in `w`, at least `pad` from
    each side (§4.2 rule 8)."""
    width = max(min(cap, w - 2 * pad), 0)
    return (w - width) // 2, width


def pointer_shape(on):
    """OSC 22: the hand pointer over something clickable, the default elsewhere."""
    return "\x1b]22;" + ("pointer" if on else "") + "\x07"


class Hover:
    """The hover fades of a program: what the pointer is over (`key`) fades in over FADE_IN, what
    it left fades out over FADE_OUT. set() on every pointer report, advance() before each
    frame; levels() gives the fade per key in STEPS shades for the components' `hover`."""

    def __init__(self):
        self.key = None
        self.fades = {}  # key -> level 0..1 of everything hovered or still fading out
        self.clock = None  # time of the last step; None while no fade runs

    def set(self, key, now=None):
        """The pointer is over `key` (None: nothing). -> True when the pointer shape changes
        between hand and default (write pointer_shape(key is not None))."""
        if key == self.key:
            return False
        prior, self.key = self.key, key
        if self.clock is None:
            self.clock = time.monotonic() if now is None else now
        return (prior is None) != (key is None)

    def advance(self, now):
        """Moves every fade on by the time since the last step; -> True while one still runs."""
        dt = 0.0 if self.clock is None else max(now - self.clock, 0.0)
        busy = False
        for key in set(self.fades) | ({self.key} - {None}):
            target = 1.0 if key == self.key else 0.0
            level = self.fades.get(key, 0.0)
            if level < target:
                level = min(target, level + dt / FADE_IN)
            elif level > target:
                level = max(target, level - dt / FADE_OUT)
            if level:
                self.fades[key] = level
            else:
                self.fades.pop(key, None)
            busy = busy or level != target
        self.clock = now if busy else None
        return busy

    def levels(self):
        return {k: round(v * STEPS) / STEPS for k, v in self.fades.items()}

    def reset(self):
        self.key, self.fades, self.clock = None, {}, None


# ---- UI -----------------------------------------------------------------------------


class Modal:
    """Base of the dialogs drawn over the page. They take every key and click while open."""

    def render(self, ui, cv):
        raise NotImplementedError

    def key(self, ui, mods, key):
        """-> True to close."""
        return False


def box(ui, cv, w, h, title):
    """Centred dialog frame over a dimmed page; returns its (x, y, w, h). Clicks outside are
    swallowed."""
    p = ui.pal
    w, h = min(w, cv.w - 2), min(h, cv.h - 2)
    x, y = (cv.w - w) // 2, (cv.h - h) // 2
    _scrim(cv, p["deep"])
    cv.hit(0, 0, cv.w, cv.h, lambda b: None)
    cv.fill(x, y, w, h, bg=p["surface"])
    cv.put(x, y, "┌" + "─" * (w - 2) + "┐", p["edge_hi"], p["surface"])
    for yy in range(y + 1, y + h - 1):
        cv.put(x, yy, "│", p["edge_hi"], p["surface"])
        cv.put(x + w - 1, yy, "│", p["edge_hi"], p["surface"])
    cv.put(x, y + h - 1, "└" + "─" * (w - 2) + "┘", p["edge_hi"], p["surface"])
    cv.put(x + 2, y, f" {title} ", p["bright"], p["surface"], BOLD)
    return x, y, w, h


def _scrim(cv, deep):
    """Fades the page behind a dialog towards `deep`, so the dialog is what the eye finds."""
    memo = {}

    def fade(c, t):
        if (c, t) not in memo:
            memo[c, t] = mix(c, deep, t)
        return memo[c, t]
    for row in cv.cells:
        for i, (ch, fg, bg, attrs) in enumerate(row):
            row[i] = (ch, fade(fg, 0.55), fade(bg, 0.45), attrs & ~BOLD)


def dialog_footer(ui, cv, x, y, w, buttons, hint):
    """The bottom row every dialog shares: buttons [(label, fn, accent)] then a muted key hint."""
    pos = x + 2
    for label, fn, accent in buttons:
        pos += ui.button(cv, pos, y, label, fn, accent=accent) + 1
    if hint:
        cv.put(pos + 1, y, hint[:max(x + w - 2 - pos - 1, 0)], ui.pal["muted"], ui.pal["surface"])


class PromptModal(Modal):
    def __init__(self, title, text, on_ok, hint=""):
        self.title, self.text, self.on_ok, self.hint, self.error = title, text, on_ok, hint, ""

    def render(self, ui, cv):
        p = ui.pal
        x, y, w, h = box(ui, cv, 60, 9, self.title)
        cv.fill(x + 2, y + 2, w - 4, 1, bg=p["deep"])
        cv.put(x + 3, y + 2, self.text[-(w - 8):], p["bright"], p["deep"])
        cv.put(x + 3 + min(len(self.text), w - 8), y + 2, "▏", p["accent"], p["deep"])
        if self.hint:
            cv.put(x + 2, y + 4, self.hint[:w - 4], p["muted"], p["surface"])
        if self.error:
            cv.put(x + 2, y + 5, self.error[:w - 4], p["red"], p["surface"])
        dialog_footer(ui, cv, x, y + h - 2, w, [("OK", lambda b: ui.finish(self, self.key(ui, set(), "Enter")), True),
                                                ("Cancel", lambda b: ui.close_modal(), False)], "Enter OK  Esc cancel")

    def key(self, ui, mods, key):
        if key == "Enter":
            try:
                err = self.on_ok(self.text)
            except Exception as exc:  # noqa: BLE001 - a callback bug must not take the panel down
                err = f"error: {exc}"
            if err:
                self.error = err
                return False
            return True
        if key == "Escape":
            return True
        if key == "Backspace":
            self.text = self.text[:-1]
        elif key == "Space" and not mods:
            self.text += " "
        elif len(key) == 1 and mods <= {"SHIFT"}:
            self.text += key.upper() if "SHIFT" in mods else key
        return False


class ChoiceModal(Modal):
    def __init__(self, title, options, current, on_pick):
        self.title, self.options, self.idx, self.on_pick = title, options, current, on_pick

    def render(self, ui, cv):
        p = ui.pal
        n = len(self.options)
        shown = min(n, max(cv.h - 9, 3))
        top = max(0, min(self.idx - shown // 2, n - shown))
        x, y, w, h = box(ui, cv, 46, shown + 5, self.title)
        for row in range(shown):
            i = top + row
            sel = i == self.idx
            cv.fill(x + 2, y + 2 + row, w - 4, 1, bg=p["raised"] if sel else p["surface"])
            cv.put(x + 3, y + 2 + row, self.options[i][:w - 6], p["bright"] if sel else p["text"],
                   p["raised"] if sel else p["surface"])
            cv.hit(x + 2, y + 2 + row, w - 4, 1, lambda b, i=i: self._pick(ui, i))
        cv.put(x + 2, y + h - 2, "↑↓ choose  Enter OK  Esc cancel", p["muted"], p["surface"])

    def _pick(self, ui, i):
        self.on_pick(i)
        ui.finish(self, True)  # unless the pick opened another dialog (a prompt for a custom value)

    def key(self, ui, mods, key):
        if key == "UpArrow":
            self.idx = (self.idx - 1) % len(self.options)
        elif key == "DownArrow":
            self.idx = (self.idx + 1) % len(self.options)
        elif key == "Enter":
            self.on_pick(self.idx)
            return True
        elif key == "Escape":
            return True
        return False


class ConfirmModal(Modal):
    """A question with Yes / No. With `on_no` the No button and `n` do something too (e.g.
    discard), and Esc alone goes back; the labels name what each button does."""

    def __init__(self, text, on_yes, title="Confirm", yes="Yes", no="No", on_no=None):
        self.text, self.on_yes, self.title = text, on_yes, title
        self.yes, self.no, self.on_no = yes, no, on_no

    def render(self, ui, cv):
        p = ui.pal
        buttons = [(self.yes, lambda b: self._yes(ui), True), (self.no, lambda b: self._no(ui), False)]
        if self.on_no:
            buttons.append(("Back", lambda b: ui.close_modal(), False))
        hint = "y  n  Esc back" if self.on_no else "y  n"
        footer = sum(len(label) + 3 for label, _, _ in buttons) + len(hint) + 5
        width = min(max(len(self.text) + 6, footer, 44), 64)
        lines = textwrap.wrap(self.text, width - 6) or [""]
        x, y, w, h = box(ui, cv, width, len(lines) + 5, self.title)
        for k, line in enumerate(lines):
            cv.put(x + 3, y + 2 + k, line, p["text"], p["surface"])
        dialog_footer(ui, cv, x, y + h - 2, w, buttons, hint)

    def _yes(self, ui):
        ui.close_modal()
        self.on_yes()

    def _no(self, ui):
        ui.close_modal()
        if self.on_no:
            self.on_no()

    def key(self, ui, mods, key):
        if key in ("y", "Enter"):
            self.on_yes()
            return True
        if key == "n" and self.on_no:
            self.on_no()
            return True
        return key in ("n", "Escape")


class UI:
    """Owns the terminal: raw mode, mouse, alternate screen, the event loop and the shared frame
    (title, page tabs, save button, status line)."""

    def __init__(self, model, pages, active=0):
        self.model, self.pages, self.active = model, pages, active
        self.modal = None
        self.message, self.message_error = "", False
        self.fd = None  # the tty, from run(): drawing (--dump, tests) needs none
        self.out = sys.stdout
        self.cv = None
        self.dirty = True
        self.running = True
        self.size = (0, 0)
        self.tickers = []
        self.hover = None  # (x, y, w, h) of the click region under the pointer, or None
        self.pointer = None  # last pointer cell, None once it left the pane

    # theme ------------------------------------------------------------------
    @property
    def pal(self):
        return self.model.current_theme()["palette"]

    def say(self, text, error=False):
        self.message, self.message_error = text, error
        self.dirty = True

    # modal helpers ----------------------------------------------------------
    def open_modal(self, modal):
        self.modal = modal
        self.dirty = True

    def close_modal(self):
        m, self.modal = self.modal, None
        self.dirty = True
        if m is not None and hasattr(m, "closed"):
            m.closed(self)

    def finish(self, modal, done):
        """Closes `modal` when `done`, unless its handler already opened another dialog."""
        if done and self.modal is modal:
            self.close_modal()

    def raw_keys(self, on):
        """Asks WezTerm (user var hivegrid_capture) to hand every chord to this program."""
        value = base64.b64encode(b"1" if on else b"0").decode()
        self.out.write(f"\x1b]1337;SetUserVar=hivegrid_capture={value}\x07")
        self.out.flush()
        if on:
            time.sleep(0.15)

    # widgets ----------------------------------------------------------------
    def button(self, cv, x, y, label, fn, accent=False, enabled=True, bg=None):
        """A chip button (§4.2 rule 5): primary with `accent`, neutral, or disabled. -> its width."""
        kind = "disabled" if not enabled else "primary" if accent else "button"
        return chip(cv, self.pal, x, y, label, kind=kind, bg=bg or self.pal["base"], hit=fn if enabled else None)

    def swatch(self, cv, x, y, color, w=3):
        cv.put(x, y, " " * w, None, color)

    # frame ------------------------------------------------------------------
    def page(self):
        return self.pages[self.active]

    def switch(self, i, step=None):
        if 0 <= i < len(self.pages):
            self.active = i
            if step is not None and hasattr(self.page(), "entered"):
                self.page().entered(step)
            self.page().on_show(self)
            self.dirty = True

    def draw(self):
        cols, rows = self.size
        p = self.pal
        cv = Canvas(cols, rows, p["text"], p["base"])
        # Title row (§4.2 rule 1) on deep with the page tabs as chips after the brand, then
        # "● unsaved" + the primary Save + ✕ at the right; the rule sits on the body background.
        close = " ✕ "
        right = len(close) + (19 if self.model.dirty else 0)
        t = title_row(cv, p, "Hivegrid", x=0, y=0, w=cols, bg=p["deep"], rule_bg=p["base"], reserve=right)
        x = t.left + 2
        for i, pg in enumerate(self.pages):
            x += chip(cv, p, x, 0, pg.title, kind="tab", selected=i == self.active, bg=p["deep"],
                      hit=lambda b, i=i: self.switch(i)) + 1
        cv.put(cols - len(close), 0, close, p["muted"], p["deep"])
        cv.hit(cols - len(close), 0, len(close), 1, lambda b: self.quit())
        if self.model.dirty:
            sx = cols - len(close) - 1 - chip_width("Save")
            self.button(cv, sx, 0, "Save", lambda b: self.save(), accent=True, bg=p["deep"])
            cv.put(sx - 11, 0, "● unsaved", p["amber"], p["deep"])
        # A page may return the (x, w) of its content column: the status and footer rows then
        # line up with the content (§4.2 rule 8) instead of sitting at the far left.
        col = self.page().render(self, cv, 0, t.body, cols, rows - 2 - t.body)
        fx, fw = (col[0] + 2, col[1] - 4) if col else (1, cols - 2)
        status_line(cv, p, rows - 2, self.message, "error" if self.message_error else "ok", x=fx, w=max(fw, 1), bg=p["base"])
        footer(cv, p, self.page().hints(), x=fx, y=rows - 1, w=max(fw, 1), bg=p["base"])
        if self.modal:
            self.modal.render(self, cv)
        self.cv = cv
        if self.pointer:
            # Regions move when the page changes under a still pointer: re-find the hover.
            self.set_hover(self.region_at(*self.pointer))
        hx, hy, hw, hh = self.hover or (0, 0, 0, 0)
        if hh == 1:  # one-row regions (chips, buttons, rows) fade toward raised (§4.2 rule 4)
            row = cv.cells[hy] if 0 <= hy < cv.h else []
            for i in range(max(hx, 0), min(hx + hw, cv.w)):
                ch, fg, bg, attrs = row[i]
                row[i] = (ch, fg, self.hover_shade(bg), attrs)
        probe = os.environ.get("HIVEGRID_PANEL_DEBUG")
        if probe:
            self._probe(probe, cv)
        # Synchronized output (2026): the terminal shows the frame whole, images included.
        buf = ["\x1b[?2026h\x1b[?25l", cv.render()]
        if not self.modal:
            for x, y, w, h, png in cv.images:
                buf.append(f"\x1b[{y + 1};{x + 1}H" + iterm_image(png, w, h))
        buf.append("\x1b[?2026l")
        self.out.write("".join(buf))
        self.out.flush()
        self.dirty = False

    def _probe(self, path, cv):
        """Test aid: HIVEGRID_PANEL_DEBUG=<file> writes the screen text and size, and marks the
        corner cells magenta so a screenshot can be mapped back to cells."""
        for x, y in ((0, 0), (cv.w - 1, cv.h - 1)):
            cv.cells[y][x] = ("█", "#ff00ff", "#ff00ff", 0)
        try:
            with open(path, "w") as f:
                json.dump({"cols": cv.w, "rows": cv.h, "text": ["".join(c[0] for c in row) for row in cv.cells]}, f)
        except OSError:
            pass

    def save(self):
        ok, message = self.model.save()
        self.say(message, not ok)

    def hover_shade(self, bg):
        """The hovered look of a cell with background `bg`, as the components draw a hover at
        full level: the primary button lightens, raised (buttons, the selection) leans toward
        the accent, anything else fades toward raised."""
        p = self.pal
        if bg == p["accent"]:
            return mix(bg, p["bright"], 0.25)
        if bg == p["raised"]:
            return mix(bg, p["accent"], 0.25)
        return hover_bg(p, bg, 1.0)

    def quit(self):
        if self.model.dirty and not isinstance(self.modal, ConfirmModal):
            self.open_modal(ConfirmModal("There are unsaved changes.", self._save_and_quit, "Close",
                                         yes="Save and close", no="Discard", on_no=self._discard))
            return
        self.running = False

    def _save_and_quit(self):
        self.save()
        if not self.model.dirty:
            self.running = False

    def _discard(self):
        self.running = False

    def region_at(self, x, y):
        """The click region under (x, y) as (x, y, w, h); None over plain cells and over a
        dialog's backdrop (the region that swallows clicks around it spans the whole canvas)."""
        if not self.cv:
            return None
        for rx, ry, rw, rh, _ in reversed(self.cv.hits):
            if rx <= x < rx + rw and ry <= y < ry + rh:
                return None if (rw, rh) == (self.cv.w, self.cv.h) else (rx, ry, rw, rh)
        return None

    def set_hover(self, rect):
        """Hand pointer over anything clickable (OSC 22, like the bar and the dock), and a
        lighter shade on the region under it."""
        if rect == self.hover:
            return
        if (rect is None) != (self.hover is None):
            self.out.write("\x1b]22;" + ("pointer" if rect else "") + "\x07")
            self.out.flush()
        self.hover = rect
        self.dirty = True

    # events -----------------------------------------------------------------
    def handle(self, ev):
        if ev[0] == "mouse":
            _, action, button, x, y = ev
            if action == "wheel":
                if not self.modal:
                    self.page().wheel(self, -1 if button == "up" else 1)
                return
            # Motion without a button arrives as a drag (see decode); 8800 reports the pointer
            # leaving the pane at column 0, row 0 (-1 here).
            self.pointer = (x, y) if x >= 0 and y >= 0 else None
            self.set_hover(self.region_at(x, y) if self.pointer else None)
            if action == "press" and self.cv:
                fn = self.cv.hit_at(x, y)
                if fn:
                    fn("right" if button == "right" else "left")
                self.dirty = True
            return
        _, mods, key = ev
        if self.modal:
            m = self.modal
            if m.key(self, mods, key) and self.modal is m:
                self.close_modal()
            self.dirty = True
            return
        if key == "c" and mods == {"CTRL"}:
            self.running = False
            return
        if self.page().captures(mods, key):
            self.page().key(self, mods, key)
        elif not mods and key in ("F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9"):
            self.switch(int(key[1]) - 1)
        elif not mods and key == "Tab" or key == "Tab" and mods == {"SHIFT"}:
            self.page().cycle(self, -1 if mods else 1)
        elif key == "s" and not mods and self.model.dirty:
            self.save()
        elif key in ("q", "Escape") and not mods:
            self.quit()
        else:
            self.page().key(self, mods, key)
        self.dirty = True

    def next_events(self, timeout):
        pending = getattr(self, "_pending", b"")
        r, _, _ = select.select([self.fd], [], [], 0.05 if pending else timeout)
        if not r:
            self._pending = b""
            return decode(pending) if pending else []
        data = os.read(self.fd, 4096)
        if not data:
            raise EOFError
        data, self._pending = split_complete(pending + data)
        return decode(data)

    def run(self):
        self.fd = sys.stdin.fileno()
        old = termios.tcgetattr(self.fd)
        try:
            tty.setraw(self.fd)
            # 1003: pointer motion for the hover; 8800 (Hivegrid build): a report when the
            # pointer leaves the pane, so the hover ends.
            self.out.write("\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1003h\x1b[?1006h\x1b[?8800h")
            self.out.flush()
            signal.signal(signal.SIGWINCH, lambda *_: None)
            self.page().on_show(self)
            while self.running:
                try:
                    size = os.get_terminal_size(self.out.fileno())
                    size = (size.columns, size.lines)
                except OSError:
                    size = (100, 30)
                if size != self.size:
                    self.size = size
                    self.dirty = True
                if self.dirty:
                    self.draw()
                try:
                    events = self.next_events(0.2)
                except EOFError:
                    break
                for ev in events:
                    self.handle(ev)
                if self.page().tick(self):
                    self.dirty = True
                for t in self.tickers:
                    t()
        except KeyboardInterrupt:
            pass
        finally:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, old)
            self.raw_keys(False)
            self.out.write("\x1b]22;\x07\x1b[?8800l\x1b[?1006l\x1b[?1003l\x1b[?1000l\x1b[?25h\x1b[?1049l")
            self.out.flush()


# From this many columns a panel uses its wide layout (two panes, a side column, centred
# content): a 1920 px window and up at the default font. Narrower windows get one column.
WIDE_COLS = 170


class Page:
    """A panel. Subclasses draw into the canvas region they are given and register click regions."""

    title = "Page"

    def render(self, ui, cv, x, y, w, h):
        """Draws the page into x, y, w, h. May return (x, w) of its content column, which the
        frame lines the status and footer rows up with; None keeps them at the left edge."""
        pass

    def key(self, ui, mods, key):
        pass

    def wheel(self, ui, dy):
        pass

    def tick(self, ui):
        """Called every ~0.2 s; True asks for a redraw."""
        return False

    def on_show(self, ui):
        pass

    def hints(self):
        return "Tab next section  q close"

    def captures(self, mods, key):
        """True when the page wants this key itself, before the frame's own q / Esc / s / Tab."""
        return False

    def cycle(self, ui, step):
        ui.switch((ui.active + step) % len(ui.pages), step)
