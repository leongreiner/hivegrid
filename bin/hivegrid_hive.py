"""The Hivegrid hive: every agent pane as a hex cell, coloured by state. A cell shows only its
status (glyph, pane number, folder); selecting one opens a larger popover with the details, the
last screen lines and a prompt line. The agent data comes from hivegrid_agents (imported lazily)."""
import os
import textwrap
import threading
import time

import hivegrid_hex as X
import hivegrid_ui as U
from hivegrid_ui import BOLD, DIM

STATES = ("working", "busy", "attention", "done", "idle", "exited")
GLYPH = {"working": "↻", "busy": "◔", "attention": "?", "done": "✓", "idle": "·", "exited": "○"}
WORD = {"working": "working", "busy": "busy", "attention": "needs you", "done": "done", "idle": "idle",
        "exited": "exited"}
BUSY_STATES = ("working", "busy", "attention")
ADD = "add"
PEEK_LINES = 10


def state_color(p, state):
    """Stable legend colour of a state: working blue, busy amber, attention violet, done green."""
    return {"working": p.get("blue", p["accent"]), "busy": p["amber"], "attention": p["violet"],
            "done": p["green"], "idle": p["grey"], "exited": p["muted"]}.get(state, p["grey"])


def state_fill(p, state):
    """(background, text colour) of a cell: strong fills for live states, soft greys for idle/exited."""
    if state == "idle":
        return U.mix(p["base"], p["grey"], 0.5), p["bright"]
    if state == "exited":
        return U.mix(p["base"], p["grey"], 0.3), p["bright"]
    return state_color(p, state), p["deep"]


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


def folder(cwd):
    if not cwd:
        return ""
    home = os.path.expanduser("~")
    if cwd.rstrip("/") == home.rstrip("/"):
        return "~"
    return os.path.basename(cwd.rstrip("/")) or "/"


def tilde(path):
    home = os.path.expanduser("~")
    if path and (path == home or path.startswith(home + "/")):
        return "~" + path[len(home):]
    return path or ""


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

    def __init__(self, model, core=None, now=time.time, background=_threaded):
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
        for c in cells:
            self.draw_cell(ui, cv, c)
        agent = self.selected_agent()
        if self.open and agent is not None:
            self.popover(ui, cv, agent, next((c for c in cells if c["id"] == agent.pane), None))

    def popover_height(self, agent, W):
        """(H, fixed rows) of the popover at width W: borders, state+cwd, pending dialog, error,
        separator, note+input+buttons, plus up to PEEK_LINES screen lines."""
        fixed = 2 + 2 + len(self.pending_block(agent, W - 4)) + (1 if agent.error else 0) + 1 + 3
        return min(fixed + PEEK_LINES, self.area[3]), fixed

    def place(self, agent, cell):
        """(x, y, W, H) of the popover. It covers no cell when it can: beside the whole band of
        cells at full width, else below or above it (a row of tiles); otherwise it sits beside the
        selected cell, covering neighbours but never that cell."""
        ax, ay, aw, ah = self.area
        wide = aw if aw < 44 else min(max(44, aw * 42 // 100), 64)
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
        right = (f"{len(self.agents)} agents" + (f" ({away} in other tabs · g gathers)" if away else "") + " · "
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

    def draw_cell(self, ui, cv, c):
        p = ui.pal
        selected = c["id"] == self.sel
        agent = self.by_pane.get(c["pane"]) if c["kind"] == "agent" else None
        if agent is not None:
            fill, fg = state_fill(p, agent.state)
            glyph, number, label = GLYPH.get(agent.state, "·"), self.number(agent), folder(agent.cwd)
            lines = [f"{glyph} {number}", label]
        else:
            fill, fg = p["pill"], p["accent"]
            lines = ["+", "new"]
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
            if agent is not None and c["h"] >= 8:
                lines = [glyph, number, label]
            elif agent is None and c["h"] >= 8:
                lines = ["+", "new", ""]
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
        cv.put(x, y, "┌" + "─" * (W - 2) + "┐", edge, bg)
        for yy in range(y + 1, y + H - 1):
            cv.put(x, yy, "│", edge, bg)
            cv.put(x + W - 1, yy, "│", edge, bg)
        cv.put(x, y + H - 1, "└" + "─" * (W - 2) + "┘", edge, bg)
        name = agent.title or folder(agent.cwd) or "agent"
        mark = f" ⇥ tab {agent.tab_index} " if self.elsewhere(agent) else ""
        head = clip(f" {GLYPH.get(agent.state, '·')} {self.number(agent)} · {' '.join(name.split())} ", W - 8 - len(mark))
        cv.put(x + 2, y, head, p["bright"], bg, BOLD)
        close = " ✕ "
        if mark:
            cv.put(x + W - 1 - len(close) - len(mark), y, mark, p["amber"], bg)
        cv.put(x + W - 1 - len(close), y, close, p["muted"], bg)
        cv.hit(x + W - 1 - len(close), y, len(close), 1, lambda b: self.close_popover())
        ix, iw = x + 2, W - 4
        row = y + 1
        age = human_age(self.now() - agent.since) if agent.since else ""
        state_text = f"{GLYPH.get(agent.state, '·')} {WORD.get(agent.state, agent.state)}"
        cv.put(ix, row, state_text, edge, bg, BOLD)
        rest = " · ".join(t for t in (age, agent.reason) if t)
        cv.put(ix + len(state_text), row, clip(" · " + " ".join(rest.split()) if rest else "", iw - len(state_text)), p["text"], bg)
        row += 1
        meta = tilde(agent.cwd)
        tail = " ".join(t for t in (agent.profile, f"pane {agent.pane}") if t)
        room = iw - len(tail) - 1
        cv.put(ix, row, (meta if len(meta) <= room else "…" + meta[-(room - 1):]) if room > 4 else "", p["muted"], bg)
        cv.put(ix + iw - len(tail), row, tail, p["dim"], bg)
        row += 1
        for item in pend:
            if item[0] == "text":
                cv.put(ix, row, item[1], p[item[2]], bg, BOLD if item[2] == "violet" else 0)
            else:
                bx = ix
                for label, cid in item[1]:
                    bx += ui.button(cv, bx, row, label, lambda b, cid=cid: self.answer(ui, agent, cid), accent=True) + 1
            row += 1
        if agent.error:
            cv.put(ix, row, short("! " + str(agent.error), iw), p["red"], bg)
            row += 1
        cv.put(ix, row, "─" * iw, p["border"], bg)
        cv.put(ix + 2, row, " screen ", p["dim"], bg)
        row += 1
        lines = self.peeked[1] if self.peeked[0] == agent.pane else []
        for k, line in enumerate(lines[-S:] if S else []):
            cv.put(ix, row + k, screen_line(line, iw), p["text"], bg)
        # bottom block: note, prompt, buttons (anchored to the bottom edge)
        note_y, input_y, btn_y = y + H - 4, y + H - 3, y + H - 2
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
        bx += ui.button(cv, bx, btn_y, "Jump", lambda b: self.jump(ui, agent), accent=True) + 1
        bx += ui.button(cv, bx, btn_y, "Ack", lambda b: self.ack(ui, agent), enabled=agent.state == "done") + 1
        ui.button(cv, bx, btn_y, "Delete", lambda b: self.delete(ui, agent))
