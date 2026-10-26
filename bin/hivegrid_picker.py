"""Hivegrid picker: the one themed chooser over a tab (agents, accounts, the + menu, pane swap).

Started by config/picker.lua as `hivegrid-panel picker` in a ShowOverlayCommand overlay (the
Hivegrid WezTerm fork). The request comes in the environment variable HIVEGRID_PICKER_SPEC
(or a file named by --spec) as JSON:

  request      string echoed back with the answer
  title        "Go to agent";  crumb: muted context after it ("2 tabs · 6 agents")
  description  optional muted text under the title (wrapped)
  palette      {name: "#rrggbb"} of the active theme (theme.lua P); colours below are palette
               names or "#rrggbb"
  rows         [{id, key?, glyph?, color?, cols: [{text, color?, bold?}], meta?, meta_color?,
                 desc?}] -- `key` is the row's digit (1-9); when no row has one, digits pick
               the n-th shown row. `cols` are left-aligned columns, the last one takes the
               room that is left; `meta` sits right-aligned; `desc` is a second, muted line.
  selected     id of the row selected at start
  verb         what Enter does ("go", "open", "swap"), for the footer
  empty        text shown when there are no rows
  confirm      {text, yes, no, danger} instead of rows: a Yes / No question, No focused

The answer goes back as the user var hivegrid_pick = base64(JSON {request, id}); id is null
when the picker was closed without a choice. Keys: type to filter (or / first), arrows or j/k
(Ctrl+N/P) move, digits choose, Enter chooses, Esc clears the filter then closes; the mouse
hovers, clicks and scrolls. Kept to the standard library and hivegrid_ui so it opens fast.
"""
import base64
import json
import os
import select
import signal
import sys
import termios
import textwrap
import time
import tty

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import hivegrid_ui as U  # noqa: E402

MIN_W, MAX_W = 56, 110  # card width bounds in cells (spacing rule 8: content capped ~110)
DIGITS = "123456789"


def _color(pal, c, default):
    if isinstance(c, str) and c.startswith("#") and len(c) == 7:
        return c
    return pal.get(c or "", default)


def _cols(row):
    """A row's columns as [{text, color, bold, segs}]; strings are accepted for plain columns.
    A column with `segs` ([{text, color}], e.g. a gauge) is drawn piece by piece; its text is
    the pieces joined."""
    out = []
    for c in row.get("cols") or []:
        c = {"text": c} if isinstance(c, str) else dict(c)
        if c.get("segs"):
            c["segs"] = [{"text": s} if isinstance(s, str) else s for s in c["segs"]]
            c["text"] = "".join(s.get("text", "") for s in c["segs"])
        out.append(c)
    return out


def put_segs(cv, pal, x, y, segs, width, default, bg, attrs=0):
    """Draws coloured pieces from x on, cut (with an ellipsis) at `width` cells; -> cells used."""
    used = 0
    for s in segs:
        room = width - used
        if room <= 0:
            break
        used += cv.put(x + used, y, U.fit_cells(s.get("text", ""), room), _color(pal, s.get("color"), default), bg, attrs)
    return used


def matches(row, terms):
    """True when every whitespace-separated term of the filter occurs in the row's text
    (columns, meta, description, key), case-insensitively."""
    if not terms:
        return True
    hay = " ".join([c.get("text", "") for c in _cols(row)] + [row.get("meta") or "", row.get("desc") or "",
                                                             str(row.get("key") or "")]).lower()
    return all(t in hay for t in terms)


def column_widths(rows, room):
    """Widths of the left columns of `rows` within `room` cells (2 cells between columns):
    every column but the last at its widest text (at most 28), the last takes what is left.
    When even that does not fit, the earlier columns shrink from the widest down."""
    n = max((len(_cols(r)) for r in rows), default=0)
    if n == 0:
        return []
    widths = [0] * n
    for r in rows:
        for i, c in enumerate(_cols(r)):
            widths[i] = max(widths[i], U.text_width(c.get("text", "")))
    fixed = [min(w, 28) for w in widths[:-1]]
    last_min = min(widths[-1], 12)
    while fixed and sum(fixed) + 2 * len(fixed) + last_min > room and max(fixed) > 4:
        i = fixed.index(max(fixed))
        fixed[i] -= 1
    return fixed + [max(room - sum(fixed) - 2 * len(fixed), 0)]


class Picker:
    """State and drawing of one picker; `done` and `result` (an id, "yes"/"no" for a confirm,
    or None) are set when it is finished."""

    def __init__(self, spec):
        self.spec = spec
        self.pal = dict(U.DEFAULT_PAL, **(spec.get("palette") or _saved_palette()))
        self.rows = list(spec.get("rows") or [])
        self.confirm = spec.get("confirm")
        self.filter = ""
        self.editing = False  # True while typed letters and digits go to the filter
        self.top = 0
        self.hover = U.Hover()
        self.done, self.result = False, None
        self.keyed = any(r.get("key") for r in self.rows)
        self.shown = self.rows
        self.sel = 0
        want = spec.get("selected")
        for i, r in enumerate(self.rows):
            if want is not None and str(r.get("id")) == str(want):
                self.sel = i
        self.button = "no"  # confirm: the focused button; No by default
        self.cv = None
        self.list_h = 1

    # ---- model ----------------------------------------------------------------------
    def refilter(self):
        terms = self.filter.lower().split()
        cur = self.shown[self.sel] if 0 <= self.sel < len(self.shown) else None
        self.shown = [r for r in self.rows if matches(r, terms)]
        self.sel = self.shown.index(cur) if cur in self.shown else 0
        self.top = 0

    def move(self, d):
        if self.shown:
            self.sel = max(0, min(len(self.shown) - 1, self.sel + d))

    def finish(self, value):
        self.done, self.result = True, value

    def choose(self, i):
        if 0 <= i < len(self.shown):
            self.finish(str(self.shown[i].get("id")))

    def digit(self, d):
        """Index of the shown row digit `d` picks: the row with that key, else the d-th shown
        row; None when there is none."""
        if self.keyed:
            for i, r in enumerate(self.shown):
                if str(r.get("key") or "") == d:
                    return i
            return None
        i = int(d) - 1
        return i if i < len(self.shown) else None

    def label_of(self, i, r):
        """The number drawn in front of shown row i."""
        if self.keyed:
            return str(r.get("key") or "")
        return str(i + 1) if i < 9 else ""

    # ---- input ----------------------------------------------------------------------
    def key(self, mods, key):
        mods = set(mods)
        if self.confirm:
            return self._confirm_key(mods, key)
        if mods == {"CTRL"} and key == "c":
            return self.finish(None)
        if key == "Escape":
            if self.filter or self.editing:
                self.filter, self.editing = "", False
                return self.refilter()
            return self.finish(None)
        if key == "Enter":
            return self.choose(self.sel)
        page = max(self.list_h - 1, 1)
        nav = {"UpArrow": -1, "DownArrow": 1, "PageUp": -page, "PageDown": page, "Home": -10 ** 6, "End": 10 ** 6}
        if key in nav and not mods:
            return self.move(nav[key])
        if mods == {"CTRL"} and key in ("n", "p", "j", "k"):
            return self.move(1 if key in ("n", "j") else -1)
        if mods == {"CTRL"} and key == "u":
            self.filter = ""
            return self.refilter()
        if mods == {"CTRL"} and key == "w":
            words = self.filter.rstrip()
            self.filter = words[:words.rfind(" ") + 1] if " " in words else ""
            return self.refilter()
        if key == "Backspace":
            if self.filter:
                self.filter = self.filter[:-1]
                return self.refilter()
            self.editing = False
            return None
        if key == "Tab":
            return self.move(-1 if mods == {"SHIFT"} else 1)
        if not self.editing and not mods:
            if key == "/":
                self.editing = True
                return None
            if key in ("j", "k"):
                return self.move(1 if key == "j" else -1)
            if key in DIGITS:
                i = self.digit(key)
                if i is not None:
                    self.choose(i)
                return None
        ch = " " if key == "Space" else key
        if len(ch) == 1 and mods <= {"SHIFT"} and ch.isprintable():
            self.editing = True
            self.filter += ch.upper() if "SHIFT" in mods else ch
            self.refilter()
        return None

    def _confirm_key(self, mods, key):
        if key in ("Escape", "n", "q") or mods == {"CTRL"} and key == "c":
            return self.finish("no")
        if key == "y":
            return self.finish("yes")
        if key in ("Enter", "Space"):
            return self.finish(self.button)
        if key in ("LeftArrow", "RightArrow", "Tab", "h", "l"):
            self.button = "yes" if self.button == "no" else "no"
        return None

    def mouse(self, action, button, x, y):
        """A pointer report; -> True when the pointer shape changes (hand over a target)."""
        if action == "wheel":
            if not self.confirm:
                self.move(-1 if button == "up" else 1)
            return False
        inside = x >= 0 and y >= 0 and self.cv is not None
        shape = self.hover.set(self.cv.key_at(x, y) if inside else None)
        if action == "press" and button == "left" and inside:
            fn = self.cv.hit_at(x, y)
            if fn:
                fn("left")
        return shape

    # ---- drawing --------------------------------------------------------------------
    def card_width(self, cols):
        """Card width: what the rows need, between MIN_W and MAX_W, 2 cells from each side."""
        if cols < MIN_W + 4:
            return max(cols - 2, 10)
        need = U.text_width(self.spec.get("title", "")) + U.text_width(self.spec.get("crumb", "")) + 12
        if self.confirm:
            need = max(need, min(U.text_width(self.confirm.get("text", "")) + 6, 72))
        # The list's own layout (see _list) at natural column widths: glyph, number, columns
        # (all but the last capped at 28, as in column_widths), right-aligned meta, frame.
        kw = max([len(self.label_of(i, r)) for i, r in enumerate(self.rows)] + [1])
        n = max((len(_cols(r)) for r in self.rows), default=0)
        widths = [0] * n
        for r in self.rows:
            for i, c in enumerate(_cols(r)):
                widths[i] = max(widths[i], U.text_width(c.get("text", "")))
        cols_w = sum(min(w, 28) for w in widths[:-1]) + (widths[-1] if widths else 0) + 2 * max(n - 1, 0)
        meta_w = min(max([U.text_width(r.get("meta") or "") for r in self.rows] + [0]), 24)
        glyph = 2 if any(r.get("glyph") for r in self.rows) else 0
        if self.rows:
            need = max(need, 2 + glyph + kw + 1 + cols_w + (meta_w + 1 if meta_w else 0) + 1 + 2)
        return max(MIN_W, min(need, MAX_W, cols - 4))

    def frame(self, cols, rows, now=None):
        """Draws the picker on a cols x rows canvas: a surface card with an edge_hi frame
        (DesignAudit §4.2 rule 11) holding the title row, filter field, list and footer."""
        p = self.pal
        surface = p["surface"]
        cv = U.Canvas(cols, rows, p["text"], p["deep"])
        levels = self.hover.levels()
        cv.hit(0, 0, cols, rows, lambda b: self.finish("no" if self.confirm else None))  # beside the card: close
        w = self.card_width(cols)
        iw = w - 2  # inside the frame
        if self.confirm:
            body = textwrap.wrap(self.confirm.get("text", ""), max(iw - 4, 10)) or [""]
            h = len(body) + 9  # frame, title, rule, gap | body | gap, buttons, gap, footer, frame
        else:
            desc = textwrap.wrap(self.spec.get("description") or "", max(iw - 4, 10))
            per = 2 if any(r.get("desc") for r in self.rows) else 1
            gap = 1 if desc else 0
            fixed = 3 + len(desc) + gap + 2 + 3  # frame, title, rule | description, gap | filter, gap | gap, footer, frame
            h = min(fixed + max(len(self.rows), 1) * per, rows - 2)
            self.list_h = max(h - fixed, 1)
        h = max(min(h, rows), 6)
        x = (cols - w) // 2
        y = max(min((rows - h) // 3, rows - h), 0)
        cv.fill(x, y, w, h, bg=surface)
        cv.hit(x, y, w, h, lambda b: None)
        edge = p["edge_hi"]
        cv.put(x, y, "╭" + "─" * (w - 2) + "╮", edge, surface)
        for yy in range(y + 1, y + h - 1):
            cv.put(x, yy, "│", edge, surface)
            cv.put(x + w - 1, yy, "│", edge, surface)
        cv.put(x, y + h - 1, "╰" + "─" * (w - 2) + "╯", edge, surface)
        ix = x + 1
        right = [(f"{len(self.shown)}/{len(self.rows)}", p["muted"])] if self.filter and not self.confirm else []
        t = U.title_row(cv, p, self.spec.get("title", ""), self.spec.get("crumb", ""), right, x=ix, y=y + 1, w=iw,
                        bg=surface, crumb_fit="end")
        cy = t.body
        if self.confirm:
            self._confirm(cv, ix, cy + 1, iw, body, levels)
        else:
            for line in desc:
                cv.put(ix + 2, cy, line, p["muted"], surface)
                cy += 1
            cy += 1 if desc else 0
            self._filter(cv, ix, cy, iw)
            self._list(cv, ix, cy + 2, iw, self.list_h, levels)
        U.footer(cv, p, self.hints(), x=ix + 1, y=y + h - 2, w=iw - 2, bg=surface)
        self.cv = cv
        return cv

    def _filter(self, cv, x, y, w):
        p = self.pal
        field = U.mix(p["surface"], p["deep"], 0.6)
        cv.fill(x + 1, y, w - 2, 1, bg=field)
        cv.put(x + 2, y, "›", p["accent"] if self.editing else p["muted"], field, U.BOLD)
        n = cv.put(x + 4, y, U.fit_tail(self.filter, w - 8), p["bright"], field) if self.filter else 0
        if self.editing:
            n += cv.put(x + 4 + n, y, "▏", p["accent"], field)
        if not self.filter:
            cv.put(x + 4 + n, y, "type to filter" if self.editing else "type or / to filter", p["dim"], field)

    def _list(self, cv, x, y, w, h, levels):
        p, surface = self.pal, self.pal["surface"]
        if not self.shown:
            if self.filter:
                U.empty_state(cv, p, x + 2, y, w - 4, f'Nothing matches "{self.filter}"',
                              "Backspace edits the filter, Esc clears it.", bg=surface)
            else:
                U.empty_state(cv, p, x + 2, y, w - 4, self.spec.get("empty") or "Nothing to choose", bg=surface)
            return
        per = 2 if any(r.get("desc") for r in self.shown) else 1
        fit = max(h // per, 1)
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + fit:
            self.top = self.sel - fit + 1
        self.top = max(0, min(self.top, max(len(self.shown) - fit, 0)))
        has_glyph = any(r.get("glyph") for r in self.shown)
        kw = max([len(self.label_of(i, r)) for i, r in enumerate(self.shown)] + [1])
        meta_w = min(max([U.text_width(r.get("meta") or "") for r in self.shown] + [0]), 24)
        name_x = x + 2 + (2 if has_glyph else 0) + kw + 1
        room = x + w - 1 - name_x - (meta_w + 1 if meta_w else 0)
        widths = column_widths(self.shown, room)
        for k in range(min(fit, len(self.shown) - self.top)):
            i = self.top + k
            r = self.shown[i]
            sel = i == self.sel
            cols = _cols(r)
            first = cols[0] if cols else {"text": ""}
            row = U.list_row(
                cv, p, y + k * per, x=x, w=w, lines=per, selected=sel, hover=levels.get(("row", i), 0.0), bg=surface,
                glyph=(r.get("glyph") or " ") if has_glyph else None, glyph_color=_color(p, r.get("color"), p["muted"]),
                num=self.label_of(i, r).rjust(kw), num_w=kw,
                name=U.fit_cells(first.get("text", ""), widths[0] if widths else 0),
                name_color=_color(p, first.get("color"), p["text"]), name_attrs=U.BOLD if sel or first.get("bold") else 0,
                right=U.fit_cells(r.get("meta") or "", meta_w), right_color=_color(p, r.get("meta_color"), p["dim"]),
                sub=r.get("desc") or "", hit=lambda b, i=i: self.choose(i), key=("row", i))
            cx = row.name_x + (widths[0] + 2 if widths else 0)
            for j, c in enumerate(cols[1:], 1):
                if j >= len(widths) or widths[j] <= 0:
                    break
                fg = _color(p, c.get("color"), p["text"])
                if sel and fg == p["text"]:
                    fg = p["bright"]
                put_segs(cv, p, cx, y + k * per, c.get("segs") or [{"text": c.get("text", "")}], widths[j], fg,
                         row.bg, U.BOLD if c.get("bold") else 0)
                cx += widths[j] + 2
        if len(self.shown) > fit:
            # A scroll thumb on the right frame line shows where the visible rows sit.
            span = fit * per
            th = max(span * fit // len(self.shown), 1)
            ty = y + (span - th) * self.top // max(len(self.shown) - fit, 1)
            for j in range(th):
                cv.put(x + w, ty + j, "┃", p["accent"], surface)

    def _confirm(self, cv, x, y, w, body, levels):
        p, surface = self.pal, self.pal["surface"]
        for k, line in enumerate(body):
            cv.put(x + 2, y + k, line, p["text"], surface)
        by = y + len(body) + 1
        bx = x + 2
        for name in ("no", "yes"):
            label = self.confirm.get(name) or name.capitalize()
            focused = self.button == name
            kind = "primary" if focused else "button"
            hit = (lambda b, name=name: self.finish(name))
            if focused and name == "yes" and self.confirm.get("danger"):
                # A destructive Yes reads red when it has the focus.
                text = f" {label} "
                cv.put(bx, by, text, p["deep"], U.mix(p["red"], p["bright"], 0.25 * levels.get(("button", name), 0.0)),
                       U.BOLD)
                cv.hit(bx, by, U.text_width(text), 1, hit, key=("button", name))
                bx += U.text_width(text) + 1
                continue
            bx += U.chip(cv, p, bx, by, label, kind=kind, hover=levels.get(("button", name), 0.0), bg=surface,
                         hit=hit, key=("button", name)) + 1

    def hints(self):
        if self.confirm:
            return [("←→", "choose"), ("Enter", "confirm"), ("y", "yes"), ("n/Esc", "no")]
        pairs = []
        digits = sorted({k for k in (self.label_of(i, r) for i, r in enumerate(self.shown)) if len(k) == 1 and k in DIGITS})
        if digits and not self.editing:
            pairs.append((digits[0] if len(digits) == 1 else f"{digits[0]}-{digits[-1]}", "jump"))
        pairs += [("↑↓", "move"), ("Enter", self.spec.get("verb") or "choose"),
                  ("type" if self.editing else "/", "filter"), ("Esc", "clear" if self.filter else "close")]
        return pairs


def _saved_palette():
    """The saved theme's palette, for a spec without one (manual runs); Lua always sends it."""
    try:
        import hivegrid_settings_core as SC
        return SC.Model(SC.Settings(SC.settings_path())).current_theme()["palette"]
    except Exception:  # noqa: BLE001 - any settings problem: the default palette
        return {}


ACK = b"hivegrid-pick-ack"  # config/picker.lua types "\x1b_hivegrid-pick-ack\x1b\\" once it has the answer
ACK_WAIT = 0.5  # seconds to wait for it (an older config never sends one)


def answer(out, request, value, fd=None):
    """Reports the choice to WezTerm (config/picker.lua listens for hivegrid_pick) and, with
    `fd`, waits until the config acknowledges it: a user var set by a program that exits at
    once can be lost with its overlay pane."""
    data = base64.b64encode(json.dumps({"request": request, "id": value}).encode()).decode()
    out.write(f"\x1b]1337;SetUserVar=hivegrid_pick={data}\x07")
    out.flush()
    if fd is None:
        return False
    seen, deadline = b"", time.monotonic() + ACK_WAIT
    while ACK not in seen:
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        try:
            r, _, _ = select.select([fd], [], [], left)
        except InterruptedError:
            continue
        if r:
            chunk = os.read(fd, 4096)
            if not chunk:
                return False
            seen = (seen + chunk)[-256:]
    return True


def run(picker, fd=None, out=None):
    """The event loop: draws, reads keys and pointer reports until the picker is done, then
    reports the answer and restores the terminal."""
    fd = sys.stdin.fileno() if fd is None else fd
    out = out or sys.stdout
    old = termios.tcgetattr(fd)
    size, pending = None, b""
    try:
        tty.setraw(fd)
        # 1003: pointer motion for the hover; 8800 (Hivegrid build): a report when the pointer
        # leaves the pane, so the hover ends.
        out.write("\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1003h\x1b[?1006h\x1b[?8800h")
        signal.signal(signal.SIGWINCH, lambda *_: None)
        dirty = True
        while not picker.done:
            try:
                ts = os.get_terminal_size(out.fileno())
                now_size = (ts.columns, ts.lines)
            except OSError:
                now_size = (100, 30)
            if now_size != size:
                size, dirty = now_size, True
            fading = picker.hover.advance(time.monotonic())
            if dirty or fading:
                out.write("\x1b[?25l" + picker.frame(*size).render())
                out.flush()
                dirty = False
            try:
                r, _, _ = select.select([fd], [], [], 0.05 if pending else U.FRAME if fading else 1.0)
            except InterruptedError:
                continue
            if r:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                data, pending = U.split_complete(pending + chunk)
            elif pending:
                data, pending = pending, b""  # a lone Escape: nothing followed it
            else:
                continue
            for ev in U.decode(data):
                if ev[0] == "mouse":
                    _, action, button, x, y = ev
                    if picker.mouse(action, button, x, y):
                        out.write(U.pointer_shape(picker.hover.key is not None))
                    dirty = dirty or action in ("press", "wheel")
                else:
                    picker.key(ev[1], ev[2])
                    dirty = True
                if picker.done:
                    break
    except KeyboardInterrupt:
        pass
    finally:
        answer(out, picker.spec.get("request"), picker.result if picker.done else None, fd)
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        out.write(U.pointer_shape(False) + "\x1b[?8800l\x1b[?1006l\x1b[?1003l\x1b[?1000l\x1b[?25h\x1b[?1049l")
        out.flush()
    return picker.result


def load_spec(path=None):
    if path:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return json.loads(os.environ.get("HIVEGRID_PICKER_SPEC") or "{}")


def main(argv):
    """`hivegrid-panel picker [--spec FILE] [--dump COLSxROWS]`."""
    path, dump = None, None
    while argv:
        a = argv.pop(0)
        if a == "--spec" and argv:
            path = argv.pop(0)
        elif a == "--dump" and argv:
            c, _, r = argv.pop(0).lower().partition("x")
            dump = (int(c), int(r))
        elif a == "--origin" and argv:
            argv.pop(0)
        else:
            print(f"hivegrid-panel picker: unknown argument {a!r}", file=sys.stderr)
            return 2
    try:
        spec = load_spec(path)
    except (OSError, ValueError) as e:
        print(f"hivegrid-panel picker: bad spec: {e}", file=sys.stderr)
        return 2
    picker = Picker(spec)
    if dump:
        cv = picker.frame(*dump)
        print("\n".join("".join(c[0] for c in row).rstrip() for row in cv.cells))
        return 0
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("hivegrid-panel picker: needs a terminal", file=sys.stderr)
        return 2
    run(picker)
    return 0
