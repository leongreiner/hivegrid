"""The Hivegrid hive: every agent pane as a hex cell, coloured by state. A cell shows only its
status (glyph, pane number, folder); selecting one opens a larger popover with the details, the
last screen lines and a prompt line. The agent data comes from hivegrid_agents (imported lazily).

Where the terminal shows inline images (WezTerm, PIL installed, HIVEGRID_NO_IMAGES unset) the
cells are anti-aliased images (CellArt) with the cell text written as terminal text into holes
of the image; elsewhere they are drawn with block characters."""
import collections
import io
import math
import os
import textwrap
import threading
import time
import unicodedata

import hivegrid_hex as X
import hivegrid_ui as U
from hivegrid_ui import BOLD, DIM

try:
    from PIL import Image, ImageDraw
except Exception:  # noqa: BLE001 - no PIL: block characters
    Image = ImageDraw = None

# The hive's states in legend order; glyphs, words and colours come from the shared table.
STATES = ("working", "busy", "attention", "done", "idle", "exited")
GLYPH = {s: U.STATES[s].glyph for s in STATES}
WORD = {s: U.STATES[s].label for s in STATES}
BUSY_STATES = ("working", "busy", "attention")
ADD = "add"
PEEK_LINES = 10
HOVER_LIGHT = 0.16  # a hovered cell's fill moves this far toward `bright`
POP_MIN_W, POP_MAX_W = 44, 64  # popover width: 42% of the area within these


def state_color(p, state):
    """Legend colour of a state, from the shared state table (unknown states read as idle)."""
    return U.state_style(p, state)[2]


def state_fill(p, state):
    """(background, text colour) of a cell: strong fills for live states, soft greys for idle/exited."""
    if state == "idle":
        return U.mix(p["base"], p["grey"], 0.5), p["bright"]
    if state == "exited":
        return U.mix(p["base"], p["grey"], 0.3), p["bright"]
    return state_color(p, state), p["deep"]


# ---- image cells ---------------------------------------------------------------------

SS = 4  # supersampling of the sprite masks: PIL does not smooth polygon edges


def art_metrics(cw, ch):
    """(gap, ring, sep, margin) in pixels for cells of cw x ch px: `gap` is how far the fill sits
    inside the lattice cell (half the space between two hexes); the selection ring runs in that
    space, `sep` px off the fill, so selecting moves no text; text cells keep `margin` px from
    the lattice edge, on flat fill."""
    gap = max(2.5, cw * 0.55)
    ring = max(2.0, round(cw * 0.35))
    sep = max(1.0, cw / 8)
    return gap, ring, sep, gap + max(1.5, cw * 0.2)


def outline(form, cw, ch):
    """(polygon in px relative to the cell's box top-left, corner radius) of a cell form:
    ('hex', ry) or ('tile', tw, th)."""
    if form[0] == "hex":
        return X.hex_polygon(form[1], cw, ch), max(2.0, min(X.hex_extent(form[1], cw, ch)) * 0.12)
    return X.tile_polygon(form[1], form[2], cw, ch), max(2.0, cw * 0.6)


class CellArt:
    """Renders hive cells (hexes or tiles) as inline images. A sprite (one cell in one look) is
    cached per form, size and colours; a frame is cut into strips of cell rows, and a strip's
    PNG is cached by what is in it, so a hover or a state change re-encodes only the strips that
    cell touches, and resending an unchanged strip hits WezTerm's image cache."""

    MAX_PNGS = 256

    def __init__(self):
        self.sprites = {}
        self.pngs = collections.OrderedDict()
        self.encoded = 0  # strips encoded so far (tests watch the caching with it)

    def sprite(self, form, cw, ch, fill, ring=None, edge=None):
        """-> (RGBA image, left, top): the cell filled with `fill`, with a selection `ring`
        around it and/or a 1 px `edge` inside it; (left, top) is the image's corner relative to
        the cell's box top-left in pixels (the shape reaches a little beyond the box)."""
        key = (form, cw, ch, fill, ring, edge)
        hit = self.sprites.get(key)
        if hit is not None:
            return hit
        poly, r = outline(form, cw, ch)
        xs, ys = [x for x, _ in poly], [y for _, y in poly]
        left, top = math.floor(min(xs)), math.floor(min(ys))
        w, h = math.ceil(max(xs)) - left, math.ceil(max(ys)) - top
        poly = [(x - left, y - top) for x, y in poly]
        gap, rw, sep, _ = art_metrics(cw, ch)

        def shape(d):
            return X.rounded(X.inset(poly, d), max(r - d + gap, 1.0))

        def mask(*layers):
            m = Image.new("L", (w * SS, h * SS), 0)
            draw = ImageDraw.Draw(m)
            for pts, value in layers:
                draw.polygon([(x * SS, y * SS) for x, y in pts], fill=value)
            return m.resize((w, h), Image.BOX)  # an exact average: no ringing at the edges

        def layer(color, m):
            lay = Image.new("RGBA", (w, h), U.rgb(color) + (255,))
            lay.putalpha(m)
            return lay

        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if ring:
            outer = max(gap - sep - rw, 0.5)
            img.alpha_composite(layer(ring, mask((shape(outer), 255), (shape(outer + rw), 0))))
        img.alpha_composite(layer(fill, mask((shape(gap), 255))))
        if edge:
            img.alpha_composite(layer(edge, mask((shape(gap), 255), (shape(gap + 1), 0))))
        self.sprites[key] = (img, left, top)
        return img, left, top

    def strips(self, items, region, cw, ch, holes=(), cut=None, band=4):
        """The images of a frame: items [(form, x, y, fill, ring, edge)] (box top-left in cells),
        drawn over the cell rect `region` (x, y, w, h) cut into strips of `band` rows, with the
        cell rects `holes` and `cut` left transparent (the text and the popover drawn as cells
        show there). -> [(x, y, w, h, png)], strips without a cell left out."""
        rx, ry0, rw, rh = region
        placed = []
        for it in items:
            img, left, top = self.sprite(it[0], cw, ch, *it[3:])
            placed.append((it, img, (it[1] - rx) * cw + left, (it[2] - ry0) * ch + top))
        out = []
        for sy in range(0, rh, band):
            sh = min(band, rh - sy)
            py0, py1 = sy * ch, (sy + sh) * ch
            members = [(it, img, px, py) for it, img, px, py in placed if py < py1 and py + img.height > py0]
            if not members:
                continue
            inside = lambda r: r[1] < ry0 + sy + sh and r[1] + r[3] > ry0 + sy
            hs = tuple(r for r in holes if inside(r))
            c = cut if cut and inside(cut) else None
            key = (cw, ch, rx, rw, ry0 + sy, sh, tuple((it, px, py) for it, _, px, py in members), hs, c)
            png = self.pngs.get(key)
            if png is None:
                png = self._strip(members, rw * cw, sh * ch, py0, rx, ry0 + sy, cw, ch, hs, c)
                self.pngs[key] = png
                if len(self.pngs) > self.MAX_PNGS:
                    self.pngs.popitem(last=False)
            else:
                self.pngs.move_to_end(key)
            out.append((rx, ry0 + sy, rw, sh, png))
        return out

    def _strip(self, members, w, h, py0, cx0, cy0, cw, ch, holes, cut):
        self.encoded += 1
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        for _, spr, px, py in members:
            py -= py0
            sx, sy = max(-px, 0), max(-py, 0)
            ex, ey = min(spr.width, w - px), min(spr.height, h - py)
            if ex > sx and ey > sy:
                img.alpha_composite(spr, dest=(px + sx, py + sy), source=(sx, sy, ex, ey))
        for x, y, cw_, ch_ in holes + ((cut,) if cut else ()):
            box = ((x - cx0) * cw, (y - cy0) * ch, (x - cx0 + cw_) * cw, (y - cy0 + ch_) * ch)
            img.paste((0, 0, 0, 0), tuple(max(v, 0) for v in box))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()


def human_age(seconds):
    s = max(int(seconds), 0)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h{s % 3600 // 60:02d}m"
    return f"{s // 86400}d"


def short(text, w):
    """One line of at most w terminal cells (whitespace collapsed), with an ellipsis when cut."""
    return U.fit_cells(" ".join(str(text).split()), max(w, 0))


def clip(text, w):
    """Truncates to w terminal cells with an ellipsis, keeping the text as it is."""
    return U.fit_cells(text, max(w, 0))


RULES = set("─━═-_ ")


def screen_line(text, w):
    """A peeked screen line cut to w cells: rules (omp's ──── separators) are cut clean; text keeps
    its indent, inner runs of blanks shrink to one (omp pads its boxes to the pane width) and it
    gets an ellipsis when cut."""
    text = text.rstrip()
    if text and set(text) <= RULES:
        return text[:w]
    indent = text[: len(text) - len(text.lstrip())]
    return clip(indent + " ".join(text.split()), w)


def calm_title(title):
    """omp's session title without its leading emoji (📂, 🙋, ⏱️); mirrors agentstatus.lua."""
    t = (title or "").lstrip()
    while t and (U.char_width(t[0]) == 0 or unicodedata.category(t[0]) == "So"):
        t = t[1:]
    return t.lstrip()


def folder(cwd):
    if not cwd:
        return ""
    home = os.path.expanduser("~")
    if cwd.rstrip("/") == home.rstrip("/"):
        return "~"
    return os.path.basename(cwd.rstrip("/")) or "/"


def place_popover(area, anchor, widths, h):
    """(x, y, w, h) of a popover inside area (x, y, w, h), next to the anchor rect: right of it when
    it fits, else left of it, trying the widths in order (widest first); when none fits it takes
    the widest and is pinned to the area's right edge, covering the anchor."""
    ax, ay, aw, ah = area
    h = min(h, ah)
    nx, ny, nw, nh = anchor
    widths = [min(w, aw) for w in widths]
    x, w = ax + aw - widths[0], widths[0]
    for cand in widths:
        if nx + nw + 1 + cand <= ax + aw:
            x, w = nx + nw + 1, cand
            break
        if nx - 1 - cand >= ax:
            x, w = nx - 1 - cand, cand
            break
    y = max(ay, min(ny + nh // 2 - h // 2, ay + ah - h))
    return x, y, w, h


def flow(items, width):
    """Splits button labels into rows of at most `width` columns (each button is len + 2 and a gap)."""
    rows, cur, used = [], [], 0
    for it in items:
        need = len(it[0]) + 2 + (1 if cur else 0)
        if cur and used + need > width:
            rows.append(cur)
            cur, used, need = [], 0, len(it[0]) + 2
        cur.append(it)
        used += need
    if cur:
        rows.append(cur)
    return rows


def _threaded(fn):
    threading.Thread(target=fn, daemon=True).start()


class HivePage(U.Page):
    title = "Hive"

    def __init__(self, model, core=None, now=time.time, background=_threaded, images=None, cell_px=None):
        self.model = model
        self._core = core
        self.now = now
        self.background = background
        self.agents = []
        self.origin = None         # (pane, tab, window) the hive was opened over
        self.by_pane = {}
        self.slots = {}
        self.sel = None            # pane id, ADD or None
        self.open = False          # detail popover open for the selection
        self.focus = False         # prompt line has the keyboard
        self.drafts = {}
        self.mode = "hex"
        self.fallback = False      # hex mode asked for but too small: tiles drawn instead
        self.error = ""
        self.peeked = (None, [])
        self.cells = []            # last layout: dicts with kind, pane, slot, x, y, w, h, cx, cy
        self.area = (0, 0, 0, 0)
        self.high = 1              # most slots seen; keeps the layout still while agents come and go
        self.hidden = 0
        self.pop_rect = None
        self.want = None           # pane id to select once it shows up
        self.want_until = 0.0
        self._collected = -1e9
        self._peeked_at = -1e9
        self._sec = None
        self._force = True
        # image cells: on where the terminal shows images (None: ask hivegrid_ui); cell_px fixes
        # the cell size in pixels (tests), else it is read from the tty at every frame
        self.images = (U.images_enabled() if images is None else images) and Image is not None
        self.cell_px = cell_px
        self.art = CellArt() if self.images else None
        self.hover = None          # id of the cell under the pointer (image cells draw it lighter)
        self.ry = None             # hex row pitch of the last layout (None: tiles)
        self.tile = None

    # core -------------------------------------------------------------------
    def core(self):
        if self._core is None:
            import hivegrid_agents as A
            self._core = A
        return self._core

    def refresh(self):
        """Re-reads the agents; True when something visible changed."""
        self._collected = self.now()
        self._force = False
        try:
            agents = list(self.core().collect())
            error = ""
        except Exception as e:  # noqa: BLE001 - the page must survive a broken core
            agents, error = self.agents, f"agents: {e}"
        agents.sort(key=lambda a: a.pane)
        sig = lambda lst: [(a.pane, a.tab, a.state, a.reason, a.cwd, a.title, a.active, a.error, repr(a.pending)) for a in lst]
        changed = sig(agents) != sig(self.agents) or error != self.error
        if self.origin is None:
            try:
                self.origin = self.core().origin()
            except Exception:  # noqa: BLE001 - no origin: nothing is marked, gather is refused
                self.origin = None
            changed = changed or self.origin is not None
        self.agents, self.error = agents, error
        self.by_pane = {a.pane: a for a in agents}
        self._assign_slots()
        if self.want is not None and self.want in self.by_pane:
            self.select(self.want)
            self.want = None
            changed = True
        elif self.want is not None and self.now() > self.want_until:
            self.want = None
        if self.sel not in (None, ADD) and self.sel not in self.by_pane:
            self.sel, self.open, self.focus = None, False, False
            changed = True
        return changed

    def _assign_slots(self):
        for pane in [p for p in self.slots if p not in self.by_pane]:
            del self.slots[pane]
        used = set(self.slots.values())
        for a in self.agents:
            if a.pane not in self.slots:
                s = 0
                while s in used:
                    s += 1
                self.slots[a.pane] = s
                used.add(s)

    def compact(self):
        self.slots = {a.pane: i for i, a in enumerate(self.agents)}
        self.high = 1

    def add_slot(self):
        used = set(self.slots.values())
        s = 0
        while s in used:
            s += 1
        return s

    def total_slots(self):
        used = self.slots.values()
        return max(max(used, default=-1) + 1, self.add_slot() + 1)

    def selected_agent(self):
        return self.by_pane.get(self.sel) if self.sel not in (None, ADD) else None

    def counts(self):
        out = {s: 0 for s in STATES}
        for a in self.agents:
            out[a.state if a.state in out else "idle"] += 1
        return out

    # selection --------------------------------------------------------------
    def prompt_enabled(self, agent):
        return agent is not None and agent.state not in ("attention", "exited")

    def select(self, pane, open=True):
        self.sel = pane
        agent = self.by_pane.get(pane)
        self.open = bool(open and agent)
        self.focus = self.open and self.prompt_enabled(agent)
        if self.open:
            self.peek(force=True)

    def peek(self, force=False):
        agent = self.selected_agent()
        if agent is None:
            return False
        t = self.now()
        if not force and t - self._peeked_at < 1.0:
            return False
        self._peeked_at = t
        try:
            lines = [str(l) for l in self.core().peek(agent.pane, PEEK_LINES + 2)]
        except Exception:  # noqa: BLE001
            lines = []
        lines = lines[-PEEK_LINES:]
        changed = (agent.pane, lines) != self.peeked
        self.peeked = (agent.pane, lines)
        return changed

    def order(self):
        """Selectable cells in slot order: ['add' placeholder included]."""
        by_slot = {s: p for p, s in self.slots.items()}
        out = []
        for s in range(self.total_slots()):
            if s in by_slot:
                out.append(by_slot[s])
            elif s == self.add_slot():
                out.append(ADD)
        return out

    def step(self, delta):
        order = self.order()
        if not order:
            return
        i = order.index(self.sel) if self.sel in order else (-1 if delta > 0 else 0)
        self.select(order[(i + delta) % len(order)])

    def move(self, dx, dy):
        """Arrow key: the nearest cell in that direction (character metric: a row counts double)."""
        if self.sel is None:
            order = self.order()
            if order:
                self.select(order[0])
            return
        here = next((c for c in self.cells if c["id"] == self.sel), None)
        if here is None:
            return
        best, best_score = None, None
        for c in self.cells:
            if c is here:
                continue
            ddx, ddy = c["cx"] - here["cx"], (c["cy"] - here["cy"]) * 2
            along = ddx * dx + ddy * dy
            if along <= 0:
                continue
            score = along + abs(ddx * dy) + abs(ddy * dx)
            if best_score is None or score < best_score:
                best, best_score = c, score
        if best is not None:
            self.select(best["id"])

    # actions ----------------------------------------------------------------
    def elsewhere(self, agent):
        """True when the agent sits in another tab than the one the hive was opened over."""
        return self.origin is not None and agent.tab != self.origin[1]

    def spawn(self, ui):
        # the new agent starts where the selected agent (else the one under the hive) works
        near = self.selected_agent() or (self.by_pane.get(self.origin[0]) if self.origin else None)

        def work():
            try:
                pane = self.core().spawn_agent(cwd=None, near=near)
                err = "could not start an agent (is this running inside Hivegrid's WezTerm?)"
            except Exception as e:  # noqa: BLE001
                pane, err = None, str(e)
            if pane is None:
                ui.say(err, True)
                return
            self.want, self.want_until = pane, self.now() + 20
            ui.say(f"started pane {pane}; it shows up here once omp runs")
            self._force = True
        ui.say("starting an agent…")
        self.background(work)

    def gather(self, ui):
        if self.origin is None:
            ui.say("cannot tell which tab the hive was opened over", True)
            return
        if not any(self.elsewhere(a) for a in self.agents):
            ui.say("no agents in other tabs")
            return

        def work():
            try:
                ok, msg = self.core().gather(self.origin[1])
            except Exception as e:  # noqa: BLE001
                ok, msg = False, str(e)
            ui.say(msg or ("gathered" if ok else "could not gather"), not ok)
            self._force = True
        ui.say("gathering agents into this tab…")
        self.background(work)

    def jump(self, ui, agent):
        try:
            ok = self.core().jump(agent)
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            ui.running = False
        else:
            ui.say(f"could not activate pane {agent.pane}", True)

    def ack(self, ui, agent):
        if agent.state != "done":
            ui.say("only a done agent can be acknowledged", True)
            return
        self.core().ack(agent)
        self._force = True
        self.refresh()
        ui.say(f"agent {self.number(agent)} acknowledged")

    def delete(self, ui, agent):
        if agent.state in BUSY_STATES:
            ui.open_modal(U.ConfirmModal(f"Agent {self.number(agent)} is {WORD[agent.state]}. Close its pane anyway?",
                                         lambda: self._delete(ui, agent), "Delete agent"))
        else:
            self._delete(ui, agent)

    def _delete(self, ui, agent):
        def work():
            try:
                ok, msg = self.core().delete_agent(agent)
            except Exception as e:  # noqa: BLE001
                ok, msg = False, str(e)
            if ok:  # the core says how ("closed" / "killed"); the user wants to know which agent
                msg = f"agent {self.number(agent)} closed" + (msg[msg.index(" ("):] if " (" in (msg or "") else "")
            ui.say(msg or "could not close the pane", not ok)
            self._force = True
        ui.say(f"closing agent {self.number(agent)}…")
        self.background(work)

    def send(self, ui, agent):
        text = self.drafts.get(agent.pane, "")
        if not text.strip():
            return
        try:
            ok, msg = self.core().send_prompt(agent, text)
        except Exception as e:  # noqa: BLE001
            ok, msg = False, str(e)
        if ok:
            self.drafts[agent.pane] = ""
            ui.say(msg or f"sent to agent {self.number(agent)}")
            self._force = True
            self._peeked_at = -1e9
        else:
            ui.say(msg or "could not send", True)

    def answer(self, ui, agent, choice):
        pend = agent.pending or {}
        if pend.get("kind") != "plan_review":
            return

        def go(feedback=None):
            try:
                ok, msg = self.core().answer_plan_review(agent, choice, feedback)
            except Exception as e:  # noqa: BLE001
                ok, msg = False, str(e)
            ui.say(msg or ("answer sent" if ok else "could not answer"), not ok)
            self._force = True

        if choice == "refine":
            def ok_fn(text):
                if not text.strip():
                    return "feedback is required to refine the plan"
                go(text.strip())
                return None
            ui.open_modal(U.PromptModal("Refine the plan", "", ok_fn, "What should change? Enter sends"))
        else:
            go()

    def toggle_mode(self):
        self.mode = "tile" if self.mode == "hex" else "hex"

    def close_popover(self):
        self.open = self.focus = False

    def plan_choices(self, agent):
        pend = agent.pending or {}
        if pend.get("kind") != "plan_review":
            return []
        choices = [(str(c.get("label") or c.get("id")), str(c.get("id"))) for c in pend.get("choices") or []
                   if isinstance(c, dict) and c.get("id")]
        # omp 18's plan_review_requested carries no choices: its answers are fixed
        return choices or [(c.capitalize(), c) for c in self.core().PLAN_CHOICES]

    # events -----------------------------------------------------------------
    def captures(self, mods, key):
        """True when the page wants the key before the panel frame (q, s, Esc, Tab)."""
        if key == "Tab":
            return True
        if key == "Escape":
            return self.open
        if self.focus and self.open:
            return not (len(key) > 1 and key[0] == "F" and key[1:].isdigit())
        return False

    def hints(self):
        if self.open and self.focus:
            return "Enter send  Alt+j jump  Alt+d del  Alt+n new  Alt+g gather  Esc close"
        if self.open:
            return "j jump  a ack  d delete  1-9 plan answer  n new  g gather  ←↑↓→ Tab  Esc close"
        return "↑↓←→ Tab select  Enter open  n new  g gather  t hex/tile  c compact  q close"

    def wheel(self, ui, dy):
        pass

    def on_show(self, ui):
        self._force = True
        self.tick(ui)

    def tick(self, ui):
        t = self.now()
        changed = False
        if self._force or t - self._collected >= 1.0:
            changed = self.refresh()
        if self.open and self.peek():
            changed = True
        sec = int(t) if self.open else None
        if sec != self._sec:
            self._sec = sec
            changed = changed or self.open
        if self.art_form() is not None and self.hovered(ui) != self.hover:
            changed = True  # image cells shade the hovered hex themselves
        return changed

    def key(self, ui, mods, key):
        agent = self.selected_agent()
        alt = "ALT" in mods
        if key == "Tab":
            self.step(-1 if "SHIFT" in mods else 1)
            return
        if key == "Escape":
            self.close_popover()
            return
        arrows = {"UpArrow": (0, -1), "DownArrow": (0, 1), "LeftArrow": (-1, 0), "RightArrow": (1, 0)}
        if key in arrows:
            self.move(*arrows[key])
            return
        if alt and len(key) == 1:
            self.command(ui, key, agent)
            return
        if self.focus and self.open and agent is not None:
            self.type(ui, agent, mods, key)
            return
        if key == "Enter":
            if self.sel == ADD:
                self.spawn(ui)
            elif agent is not None and not self.open:
                self.select(agent.pane)
            elif agent is not None and self.prompt_enabled(agent):
                self.focus = True
            return
        if not mods or mods == {"SHIFT"}:
            self.command(ui, key, agent)

    def type(self, ui, agent, mods, key):
        text = self.drafts.get(agent.pane, "")
        if key == "Enter":
            self.send(ui, agent)
            return
        if key == "Backspace":
            text = text[:-1]
        elif key == "u" and mods == {"CTRL"}:
            text = ""
        elif key == "Space" and not mods:
            text += " "
        elif len(key) == 1 and mods <= {"SHIFT"}:
            text += key.upper() if "SHIFT" in mods else key
        self.drafts[agent.pane] = text

    def command(self, ui, key, agent):
        if key == "n":
            self.spawn(ui)
        elif key == "t":
            self.toggle_mode()
        elif key == "c":
            self.compact()
        elif key == "g":
            self.gather(ui)
        elif key == "r":
            self.refresh()
        elif agent is None:
            return
        elif key == "j":
            self.jump(ui, agent)
        elif key == "a":
            self.ack(ui, agent)
        elif key in ("d", "Delete"):
            self.delete(ui, agent)
        elif key in ("i", "/"):
            if self.prompt_enabled(agent):
                self.select(agent.pane)
                self.focus = True
        elif len(key) == 1 and key in "123456789":
            choices = self.plan_choices(agent)
            i = int(key) - 1
            if i < len(choices):
                self.answer(ui, agent, choices[i][1])

    # drawing ----------------------------------------------------------------
    def layout(self, x, y, w, h):
        """Computes self.cells for the area; no drawing. Cells: kind agent/add, pane, id, box and
        centre, `rows` (interior spans) and `region` (hit spans) for hexes."""
        self.area = (x, y, w, h)
        by_slot = {s: p for p, s in self.slots.items()}
        add = self.add_slot()
        self.high = total = max(self.high, self.total_slots())
        cells = []

        def entry(slot, bx, by, bw, bh, rows=None, region=None):
            if slot in by_slot:
                kind, pane = "agent", by_slot[slot]
            elif slot == add:
                kind, pane = "add", None
            else:
                return
            cells.append({"kind": kind, "pane": pane, "id": pane if kind == "agent" else ADD, "slot": slot,
                          "x": bx, "y": by, "w": bw, "h": bh, "cx": bx + bw / 2, "cy": by + bh / 2,
                          "rows": rows, "region": region})

        self.hidden = 0
        self.fallback = False
        self.ry = None
        self.tile = None
        if self.mode == "hex":
            # The largest hexes that leave room for the popover on both sides (the band is
            # centred), so opening it never covers a cell; a narrow window takes the largest
            # that fits at all.
            side = POP_MIN_W + 1
            cw, ch, pos = X.fit(total, w - 2 * side, h) if w > 2 * side else (0, 0, [])
            pos = [(q, r, px + side, py) for q, r, px, py in pos]
            if not cw:
                cw, ch, pos = X.fit(total, w, h)
            if cw:
                ry = X.ry_of(cw)
                _, _, rows, region = X.shape(ry)
                self.ry = ry
                for slot, (q, r, px, py) in enumerate(pos):
                    entry(slot, x + px, y + py, cw, ch, rows, region)
            else:
                self.fallback = True
        if self.ry is None:
            tw, th, cols, nrows, pos, fits = X.fit_tiles(total, w, h)
            self.tile = (tw, th)
            if tw:
                px_, py_ = X.tile_pitch(tw, th)
                scroll = oy = 0
                if fits:  # centred like the hexes
                    used = (total - 1) // cols + 1
                    oy = max((h - (used * py_ - (py_ - th))) // 2, 0)
                else:
                    sel_slot = self.slots.get(self.sel, add if self.sel == ADD else 0)
                    scroll = max(0, min(sel_slot // cols - nrows + 1, (total - 1) // cols - nrows + 1))
                for slot, tx, ty in pos:
                    row = ty // py_ - scroll
                    if 0 <= row < nrows:
                        entry(slot, x + tx, y + oy + row * py_, tw, th)
                    elif slot in by_slot:
                        self.hidden += 1
        self.cells = cells
        return cells

    def render(self, ui, cv, x, y, w, h):
        p = ui.pal
        self.pop_rect = None
        self.legend(ui, cv, x, y, w)
        ay, ah = y + 1, max(h - 1, 0)
        cv.hit(x, ay, w, ah, lambda b: self.close_popover())
        cells = self.layout(x, ay, w, ah)
        if not self.agents and not self.error:
            msg = "No agents yet: click + or press n to start an omp agent"
            cv.put(x + max((w - len(msg)) // 2, 0), ay, msg, p["muted"])
        if self.error:
            cv.put(x + 1, ay, short(self.error, w - 2), p["red"])
        if self.art_form() is not None:
            self.draw_art(ui, cv, cells)
        else:
            for c in cells:
                self.draw_cell(ui, cv, c)
        agent = self.selected_agent()
        if self.open and agent is not None:
            self.popover(ui, cv, agent, next((c for c in cells if c["id"] == agent.pane), None))

    def popover_height(self, agent, W):
        """(H, fixed rows) of the popover at width W: borders and a padding row under the title
        and above the bottom border, state+cwd, pending dialog, error, separator,
        note+input+buttons, plus up to PEEK_LINES screen lines."""
        fixed = 2 + 2 + 2 + len(self.pending_block(agent, W - 4)) + (1 if agent.error else 0) + 1 + 3
        return min(fixed + PEEK_LINES, self.area[3]), fixed

    def place(self, agent, cell):
        """(x, y, W, H) of the popover. It covers no cell when it can: beside the whole band of
        cells at full width, else below or above it (a row of tiles); otherwise it sits beside the
        selected cell, covering neighbours but never that cell."""
        ax, ay, aw, ah = self.area
        wide = aw if aw < POP_MIN_W else min(max(POP_MIN_W, aw * 42 // 100), POP_MAX_W)
        if cell is None:
            return place_popover(self.area, (ax + aw // 2, ay + ah // 2, 1, 1), [wide], self.popover_height(agent, wide)[0])
        left = min(d["x"] for d in self.cells)
        right = max(d["x"] + d["w"] for d in self.cells)
        top = min(d["y"] for d in self.cells)
        bottom = max(d["y"] + d["h"] for d in self.cells)
        widths = sorted({wide, min(wide, 40), min(wide, 36)}, reverse=True)
        band = (left, cell["y"], right - left, cell["h"])

        def beside(W):
            return right + 1 + W <= ax + aw or left - 1 - W >= ax
        H = self.popover_height(agent, wide)[0]
        if beside(wide):
            return place_popover(self.area, band, [wide], H)
        x = max(ax, min(cell["x"] + cell["w"] // 2 - wide // 2, ax + aw - wide))
        if bottom + 1 + H <= ay + ah:
            return x, bottom + 1, wide, H
        if top - 1 - H >= ay:
            return x, top - 1 - H, wide, H
        for W in widths[1:]:
            if beside(W):
                return place_popover(self.area, band, [W], self.popover_height(agent, W)[0])
        rect = (cell["x"], cell["y"], cell["w"], cell["h"])
        W = place_popover(self.area, rect, widths, 1)[2]
        return place_popover(self.area, rect, [W], self.popover_height(agent, W)[0])

    def legend(self, ui, cv, x, y, w):
        p = ui.pal
        counts = self.counts()
        cv.fill(x, y, w, 1, bg=p["base"])
        full = "  ".join(f"{GLYPH[s]} {WORD[s]} {counts[s]}" for s in STATES)
        away = sum(1 for a in self.agents if self.elsewhere(a))
        n = len(self.agents)
        right = (f"{n} agent{'' if n == 1 else 's'}" + (f" ({away} in other tabs · g gathers)" if away else "") + " · "
                 + ("tiles" if self.mode == "tile" or self.fallback else "hex"))
        use_full = len(full) + len(right) + 4 <= w
        cx = x + 1
        for s in STATES:
            text = f"{GLYPH[s]} {WORD[s]} {counts[s]}" if use_full else f"{GLYPH[s]}{counts[s]}"
            col = state_color(p, s) if counts[s] else p["dim"]
            cv.put(cx, y, text[0], col, None, BOLD)
            cv.put(cx + 1, y, text[1:], p["text"] if counts[s] else p["dim"])
            cx += len(text) + (2 if use_full else 1)
        if cx + len(right) < x + w:
            cv.put(x + w - len(right) - 1, y, right, p["muted"])

    def number(self, agent):
        """The agent's digit as the overview dock and the pane picker show it; prefixed with its
        tab ("2·3") while the agents sit in several tabs."""
        n = str(agent.num or agent.pane)
        return f"{agent.tab_index}·{n}" if len({a.tab for a in self.agents}) > 1 else n

    def look(self, p, c):
        """(agent, fill, text colour, lines) of a cell before selection and hover: glyph and
        number, then the folder; on tall cells (hexes h >= 8, tiles h >= 5) glyph, number and
        folder each a line."""
        agent = self.by_pane.get(c["pane"]) if c["kind"] == "agent" else None
        tall = c["h"] >= (8 if c["rows"] is not None else 5)
        if agent is not None:
            fill, fg = state_fill(p, agent.state)
            glyph, number, label = GLYPH.get(agent.state, "·"), self.number(agent), folder(agent.cwd)
            lines = [glyph, number, label] if tall else [f"{glyph} {number}", label]
        else:
            fill, fg = p["pill"], p["accent"]
            lines = ["+", "new", ""] if tall else ["+", "new"]
        return agent, fill, fg, lines

    def art_form(self):
        """The form the image cells take in the last layout: ('hex', ry), ('tile', tw, th), or
        None for block characters (no images, or tiles of 1-2 rows: their text rows touch the
        tile's edges, so text holes would show through the gap)."""
        if not self.images:
            return None
        if self.ry is not None:
            return ("hex", self.ry)
        if self.tile and self.tile[0] and self.tile[1] > 2:
            return ("tile",) + tuple(self.tile)
        return None

    def cell_at(self, x, y):
        """The cell whose click area holds the character (x, y), or None."""
        for c in self.cells:
            if c["region"] is not None:
                i = y - c["y"]
                if 0 <= i < len(c["region"]) and c["region"][i][0] <= x - c["x"] < c["region"][i][1]:
                    return c
            elif c["x"] - 1 <= x < c["x"] + c["w"] + 1 and c["y"] <= y < c["y"] + c["h"]:
                return c
        return None

    def hovered(self, ui):
        """Id of the cell under the pointer (not when the popover covers that spot), or None."""
        pt = getattr(ui, "pointer", None)
        if pt is None or ui.modal:
            return None
        r = self.pop_rect
        if r and r[0] <= pt[0] < r[0] + r[2] and r[1] <= pt[1] < r[1] + r[3]:
            return None
        c = self.cell_at(*pt)
        return c["id"] if c else None

    def click_at(self, ui):
        """Click on an image hex: the cell under the pointer, else (a gap) closes the popover."""
        c = self.cell_at(*ui.pointer) if ui.pointer else None
        if c is None:
            self.close_popover()
        else:
            self.click(ui, c)

    def draw_art(self, ui, cv, cells):
        """The cells as one set of images: fill in the state colour, a continuous ring around the
        selected cell, the hovered one lighter; glyph, number and folder stay terminal text,
        written into holes of the image on the same fill colour."""
        if not cells:
            return
        p = ui.pal
        cw, ch = self.cell_px or U.cell_pixels()
        form = self.art_form()
        if form[0] == "hex":
            spans = X.text_spans(form[1], cw, ch, art_metrics(cw, ch)[3])
        else:
            spans = ((1, form[1] - 1),) * form[2]
        agent = self.selected_agent()
        cut = None
        if self.open and agent is not None:
            cut = self.pop_rect = self.place(agent, next((c for c in cells if c["id"] == agent.pane), None))
        self.hover = self.hovered(ui)
        items, holes, texts = [], [], []
        for c in cells:
            agent, fill, fg, lines = self.look(p, c)
            if self.hover == c["id"]:
                fill = U.mix(fill, p["bright"], HOVER_LIGHT)
            ring = p["bright"] if c["id"] == self.sel else None
            edge = U.mix(p["pill"], p["accent"], 0.35) if agent is None else None
            items.append((form, c["x"], c["y"], fill, ring, edge))
            start = (c["h"] - len(lines)) // 2
            if len(lines) == 3 and not all(spans[start + k] for k, t in enumerate(lines) if t):
                lines = [f"{lines[0]} {lines[1]}", lines[2]] if agent is not None else ["+", "new"]
                start = (c["h"] - len(lines)) // 2
            for k, text in enumerate(lines):
                span = spans[start + k] if 0 <= start + k < len(spans) else None
                if not span or not text:
                    continue
                text = short(text, span[1] - span[0])
                tw = U.text_width(text)
                tx, ty = c["x"] + span[0] + (span[1] - span[0] - tw) // 2, c["y"] + start + k
                holes.append((tx, ty, tw, 1))
                texts.append((tx, ty, text, fg, fill, BOLD if k == 0 or len(lines) == 3 and k == 1 else 0))
            # one region per box with a dispatcher: the frame shades one-row regions on hover,
            # which would show through the image's edges; the boxes overlap, click_at decides
            gx = 0 if form[0] == "hex" else 1  # a tile's click area takes the gap columns too
            cv.hit(c["x"] - gx, c["y"], c["w"] + 2 * gx, c["h"], lambda b: self.click_at(ui))
        ax, ay, aw, ah = self.area
        x0 = max(min(c["x"] for c in cells) - 1, ax)
        x1 = min(max(c["x"] + c["w"] for c in cells) + 1, ax + aw)
        y0 = max(min(c["y"] for c in cells) - 1, ay)
        y1 = min(max(c["y"] + c["h"] for c in cells) + 1, ay + ah)
        band = max(form[1] if form[0] == "hex" else X.tile_pitch(form[1], form[2])[1], 2)
        for strip in self.art.strips(items, (x0, y0, x1 - x0, y1 - y0), cw, ch, tuple(holes), cut, band=band):
            cv.image(*strip)
        for tx, ty, text, fg, bg, attrs in texts:
            cv.put(tx, ty, text, fg, bg, attrs)

    def draw_cell(self, ui, cv, c):
        p = ui.pal
        selected = c["id"] == self.sel
        agent, fill, fg, lines = self.look(p, c)
        if agent is not None:
            glyph, number, label = GLYPH.get(agent.state, "·"), self.number(agent), folder(agent.cwd)
        halo = p["bright"]
        if selected:
            fill = U.mix(fill, halo, 0.22)
        click = (lambda b, c=c: self.click(ui, c))
        if c["rows"] is not None:
            rows, region = c["rows"], c["region"]
            for i, span in enumerate(region):
                if selected:
                    cv.fill(c["x"] + span[0], c["y"] + i, span[1] - span[0], 1, bg=halo)
                cv.hit(c["x"] + span[0], c["y"] + i, span[1] - span[0], 1, click)
            for i, span in enumerate(rows):
                if span:
                    cv.fill(c["x"] + span[0], c["y"] + i, span[1] - span[0], 1, bg=fill)
            if selected:
                # The halo columns give the sides; the top and bottom edges (and the stair steps)
                # are half blocks inside the hex, so the outline is closed without touching the
                # neighbouring cells.
                for i, span in enumerate(rows):
                    if not span:
                        continue
                    above = rows[i - 1] if i > 0 else None
                    below = rows[i + 1] if i + 1 < len(rows) else None
                    for xx in range(*span):
                        if not above or not above[0] <= xx < above[1]:
                            cv.put(c["x"] + xx, c["y"] + i, "▀", halo, fill)
                        elif not below or not below[0] <= xx < below[1]:
                            cv.put(c["x"] + xx, c["y"] + i, "▄", halo, fill)
            start = (c["h"] - len(lines)) // 2
            for k, text in enumerate(lines):
                i = start + k
                span = rows[i] if 0 <= i < len(rows) else None
                if not span or not text:
                    continue
                width = span[1] - span[0]
                text = short(text, width)
                cv.put(c["x"] + span[0] + (width - U.text_width(text)) // 2, c["y"] + i, text, fg, fill,
                       BOLD if k == 0 or (c["h"] >= 8 and k == 1) else 0)
        else:
            if selected:
                # sides in the 1-column gaps, top and bottom as half blocks in the gap rows, so the
                # outline is as thin as the hexes' one
                cv.fill(c["x"] - 1, c["y"], c["w"] + 2, c["h"], bg=halo)
                if c["h"] > 2:
                    cv.put(c["x"] - 1, c["y"] - 1, "▄" * (c["w"] + 2), halo)
                    cv.put(c["x"] - 1, c["y"] + c["h"], "▀" * (c["w"] + 2), halo)
            cv.fill(c["x"], c["y"], c["w"], c["h"], bg=fill)
            cv.hit(c["x"] - 1, c["y"], c["w"] + 2, c["h"], click)
            if c["h"] == 1:
                lines = [short(f"{lines[0].replace(' ', '')} {lines[1] if agent else ''}".strip(), c["w"] - 1)]
            elif c["h"] >= 5 and agent is not None:
                lines = [glyph, number, label]
            start = (c["h"] - len(lines)) // 2
            for k, text in enumerate(lines):
                text = short(text, c["w"] - 2)
                cv.put(c["x"] + (c["w"] - U.text_width(text)) // 2, c["y"] + start + k, text, fg, fill, BOLD if k == 0 else 0)

    def click(self, ui, c):
        if c["kind"] == "add":
            self.select(ADD)
            self.spawn(ui)
        elif c["id"] == self.sel and self.open:
            self.close_popover()
        else:
            self.select(c["id"])

    # popover ----------------------------------------------------------------
    def pending_block(self, agent, inner):
        """[('text', str, colour key) | ('buttons', [(label, choice id)])] for the pending dialog."""
        pend = agent.pending or {}
        kind = pend.get("kind")
        out = []
        if kind == "plan_review":
            out.append(("text", short("Plan review: " + str(pend.get("title") or ""), inner), "violet"))
            items = [(f"{i + 1} {label}", cid) for i, (label, cid) in enumerate(self.plan_choices(agent))]
            for row in flow(items, inner):
                out.append(("buttons", row))
        elif kind == "approval":
            out.append(("text", short(f"Approval needed: {pend.get('tool') or 'tool'}", inner), "violet"))
            for line in textwrap.wrap(str(pend.get("summary") or ""), inner)[:2]:
                out.append(("text", line, "text"))
        elif kind == "ask":
            out.append(("text", short("The agent asks:", inner), "violet"))
            for line in textwrap.wrap(str(pend.get("question") or ""), inner)[:3]:
                out.append(("text", line, "text"))
            # bullets, not digits: 1-9 answer plan reviews only, a question is answered in the pane
            for opt in (pend.get("options") or [])[:3]:
                out.append(("text", short(f"  · {opt}", inner), "muted"))
        return out

    def popover(self, ui, cv, agent, cell):
        p = ui.pal
        x, y, W, H = self.place(agent, cell)
        inner = W - 4
        pend = self.pending_block(agent, inner)
        S = max(H - self.popover_height(agent, W)[1], 0)
        self.pop_rect = (x, y, W, H)
        bg, edge = p["surface"], state_color(p, agent.state)
        cv.hit(x, y, W, H, lambda b: None)
        cv.fill(x, y, W, H, bg=bg)
        cv.put(x, y, "╭" + "─" * (W - 2) + "╮", edge, bg)
        for yy in range(y + 1, y + H - 1):
            cv.put(x, yy, "│", edge, bg)
            cv.put(x + W - 1, yy, "│", edge, bg)
        cv.put(x, y + H - 1, "╰" + "─" * (W - 2) + "╯", edge, bg)
        name = calm_title(agent.title) or folder(agent.cwd) or "agent"
        mark = f" ⇥ tab {agent.tab_index} " if self.elsewhere(agent) else ""
        head = clip(f" {GLYPH.get(agent.state, '·')} {self.number(agent)} · {' '.join(name.split())} ", W - 8 - len(mark))
        cv.put(x + 2, y, head, p["bright"], bg, BOLD)
        close = " ✕ "
        if mark:
            cv.put(x + W - 1 - len(close) - len(mark), y, mark, p["amber"], bg)
        cv.put(x + W - 1 - len(close), y, close, p["muted"], bg)
        cv.hit(x + W - 1 - len(close), y, len(close), 1, lambda b: self.close_popover())
        ix, iw = x + 2, W - 4
        row = y + 2  # one row of padding under the title, like the card's 1-cell sides
        age = human_age(self.now() - agent.since) if agent.since else ""
        state_text = f"{GLYPH.get(agent.state, '·')} {WORD.get(agent.state, agent.state)}"
        cv.put(ix, row, state_text, edge, bg, BOLD)
        rest = " · ".join(t for t in (age, agent.reason) if t)
        cv.put(ix + len(state_text), row, clip(" · " + " ".join(rest.split()) if rest else "", iw - len(state_text)), p["text"], bg)
        row += 1
        # The title names the agent by its number; the internal pane id would read as another one.
        meta = U.home_path(agent.cwd)
        tail = agent.profile or ""
        room = iw - len(tail) - 1
        cv.put(ix, row, U.fit_text(meta, room, "path") if room > 4 else "", p["muted"], bg)
        cv.put(ix + iw - len(tail), row, tail, p["dim"], bg)
        row += 1
        for item in pend:
            if item[0] == "text":
                cv.put(ix, row, item[1], p[item[2]], bg, BOLD if item[2] == "violet" else 0)
            else:
                bx = ix
                for label, cid in item[1]:
                    bx += U.chip(cv, p, bx, row, label, kind="primary", bg=bg,
                                 hit=lambda b, cid=cid: self.answer(ui, agent, cid)) + 1
            row += 1
        if agent.error:
            cv.put(ix, row, short("! " + str(agent.error), iw), p["red"], bg)
            row += 1
        cv.put(ix, row, "─" * iw, U.rule_color(p, bg), bg)
        cv.put(ix + 2, row, " screen ", p["dim"], bg)
        row += 1
        lines = self.peeked[1] if self.peeked[0] == agent.pane else []
        for k, line in enumerate(lines[-S:] if S else []):
            cv.put(ix, row + k, screen_line(line, iw), p["text"], bg)
        # bottom block: note, prompt, buttons (anchored to the bottom edge)
        note_y, input_y, btn_y = y + H - 5, y + H - 4, y + H - 3
        enabled = self.prompt_enabled(agent)
        if agent.state == "attention" and (agent.pending or {}).get("kind") == "plan_review":
            note = "Prompt off: answer the plan review above"
        elif agent.state == "attention":
            note = "Answer it in the pane (j jumps there)"
        elif agent.state == "exited":
            note = "Prompt off: the agent has exited"
        elif agent.state in ("working", "busy"):
            note = "Agent is busy: a message may be queued until it is done"
        else:
            note = "Enter sends the line to the agent"
        cv.put(ix, note_y, short(note, iw), p["amber"] if agent.state in ("attention", "working", "busy") else p["dim"], bg)
        field = p["deep"] if enabled else bg
        cv.fill(ix, input_y, iw, 1, bg=field)
        text = self.drafts.get(agent.pane, "")
        if enabled:
            shown = text[-(iw - 4):]
            cv.put(ix, input_y, "› ", p["accent"], field, BOLD)
            if shown:
                cv.put(ix + 2, input_y, shown, p["bright"], field)
            elif not self.focus:
                cv.put(ix + 2, input_y, "message to the agent…", p["dim"], field)
            if self.focus:
                cv.put(ix + 2 + len(shown), input_y, "▏", p["accent"], field)
            cv.hit(ix, input_y, iw, 1, lambda b: setattr(self, "focus", True))
        else:
            cv.put(ix, input_y, "› " + short(text or "prompt disabled", iw - 2), p["dim"], field, DIM)
        bx = ix
        bx += U.chip(cv, p, bx, btn_y, "Jump", kind="primary", bg=bg, hit=lambda b: self.jump(ui, agent)) + 1
        bx += U.chip(cv, p, bx, btn_y, "Ack", kind="button" if agent.state == "done" else "disabled", bg=bg,
                     hit=lambda b: self.ack(ui, agent)) + 1
        U.chip(cv, p, bx, btn_y, "Delete", kind="button", bg=bg, hit=lambda b: self.delete(ui, agent))
