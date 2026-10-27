"""The four Hivegrid panels: settings, usage history, agent overview, system monitor."""
import bisect
import json
import os
import re
import shutil
import subprocess
import textwrap
import time
from collections import deque
from pathlib import Path

import hivegrid_ui as U
from hivegrid_agents import AGENT_PROCS, SHELLS, is_chrome, is_web, state_dir, web_build_cached, web_label
from hivegrid_settings_core import (
    BINDINGS, PRESETS, VERSION, WEB_DEFAULTS, about_info, find_wezterm, cache_dir, check_binding, current_entry,
    discover_accounts, effective_keys, format_combo, merge_accounts, normalize, omp_profiles, pair_profile, quota_state,
    remove_account, tilde, untilde,
    valid_label, valid_profile, valid_search, wezterm_defaults,
)

ACCENTS = ["#4f9dff", "#38bdf8", "#34d399", "#fbbf24", "#a78bfa", "#f87171", "#f472b6", "#e5e7eb"]
BOLD, DIM = U.BOLD, U.DIM


def fit(text, n):
    text = str(text)
    return text if len(text) <= n else text[: max(n - 1, 0)] + "…"


def human_age(secs):
    secs = int(max(secs, 0))
    if secs >= 86400:
        return f"{secs // 86400}d{secs % 86400 // 3600:02d}h"
    if secs >= 3600:
        return f"{secs // 3600}h{secs % 3600 // 60:02d}m"
    if secs >= 60:
        return f"{secs // 60}m{secs % 60:02d}s"
    return f"{secs}s"


# ---- settings -----------------------------------------------------------------------


class CaptureModal(U.Modal):
    """Records the next key combination; conflicts are shown inline."""

    def __init__(self, page, name):
        self.page, self.name = page, name
        self.error = ""

    def opened(self, ui):
        ui.raw_keys(True)

    def closed(self, ui):
        ui.raw_keys(False)

    def render(self, ui, cv):
        p = ui.pal
        label, _, kind = BINDINGS[self.name]
        x, y, w, h = U.box(ui, cv, 68, 12, label)
        what = "the key to press after the leader" if kind == "leader" else "the new key combination"
        cv.put(x + 3, y + 2, f"Press {what} now…", p["bright"], p["surface"], BOLD)
        cv.put(x + 3, y + 3, "current ", p["muted"], p["surface"])
        cv.put(x + 11, y + 3, self.page.model.keys[self.name], p["accent"], p["surface"])
        if self.error:
            cv.put(x + 3, y + 5, fit(self.error, w - 6), p["red"], p["surface"])
        cv.put(x + 3, y + 7, "A terminal reports only Ctrl+letter, Alt+key and plain keys;", p["muted"], p["surface"])
        cv.put(x + 3, y + 8, "type any other chord (e.g. CTRL+SHIFT+k) with Type it.", p["muted"], p["surface"])
        U.dialog_footer(ui, cv, x, y + h - 2, w, [("Type it", lambda b: self._type(ui), False),
                                                  ("Cancel", lambda b: ui.close_modal(), False)], "Tab type it  Esc cancel")

    def _type(self, ui):
        ui.close_modal()
        name = self.name
        ui.open_modal(U.PromptModal(
            f"{BINDINGS[name][0]}: type the binding", self.page.model.keys[name],
            lambda text: self.page.apply_binding(ui, name, text, typed=True),
            "e.g. CTRL+SHIFT+k, ALT+q or LEADER+u"))

    def key(self, ui, mods, key):
        if key == "Escape" and not mods:
            return True
        if key == "Tab" and not mods:
            self._type(ui)
            return False
        kind = BINDINGS[self.name][2]
        combo = format_combo({"LEADER"}, key) if kind == "leader" else format_combo(set(mods), key)
        err = self.page.apply_binding(ui, self.name, combo)
        if err:
            self.error = err
            return False
        return True


WEB_ROWS = ("links", "search", "profile")
WEB_LABELS = {"links": "Open links in", "search": "Search engine", "profile": "Default profile"}


class SettingsPage(U.Page):
    title = "Settings"
    SUBS = ["Theme", "Keys", "Accounts", "Web", "About"]

    def __init__(self, model):
        self.model = model
        self.sub = 0
        self.cursor = [list(PRESETS).index(model.theme["preset"]), 0, 0, 0]
        self.profiles = omp_profiles()
        self.wez_defaults = None
        self.about = None
        stored = model.settings.stored_accounts()
        model.accounts = stored if stored is not None else merge_accounts(None, discover_accounts())
        model.baseline = model.snapshot()
        self.found = []
        self.two_pane = False  # set by render: the wide layout (section list, list + detail)
        self.web_built = None  # does this WezTerm have the web pane? (looked up when the Web section draws)
        self.rescan(None, quiet=True)

    # data ---------------------------------------------------------------------
    def rescan(self, ui, quiet=False):
        self.profiles = omp_profiles()
        live = {a["dir"]: a for a in discover_accounts()}
        before = len(self.model.accounts)
        self.model.accounts[:] = [a for a in self.model.accounts if not (a.get("auto") and a["dir"] not in live)]
        tracked = {a["dir"] for a in self.model.accounts}
        self.found = [a for d, a in live.items() if d not in tracked]
        if ui and not quiet:
            gone = before - len(self.model.accounts)
            ui.say(f"rescanned: {len(self.found)} new, {gone} gone")

    def apply_binding(self, ui, name, text, typed=False):
        """Validates and applies; returns an error text, or None when done."""
        try:
            combo = normalize(text)
        except ValueError as exc:
            return str(exc)
        if self.wez_defaults is None:
            self.wez_defaults = wezterm_defaults()
        problems = check_binding(name, combo, self.model.keys, self.wez_defaults)
        errors = [t for sev, t in problems if sev == "error"]
        if errors:
            return f"{combo}: {errors[0]}"
        warns = [t for sev, t in problems if sev == "warn"]

        def commit():
            self.model.keys[name] = combo
            ui.say(f"{BINDINGS[name][0]}: {combo}  (Save applies it)")

        if warns:
            ui.close_modal()
            ui.open_modal(U.ConfirmModal(f"{combo} {warns[0]}. Use it anyway?", commit))
            return None
        commit()
        return None

    # input --------------------------------------------------------------------
    def hints(self):
        return {
            0: "↑↓ choose  Enter use  [/] accent  a custom accent  s save  ←→ section  q close",
            1: "↑↓ choose  Enter change  r reset  R reset all  s save  ←→ section  q close",
            2: "↑↓ choose  Space show/hide  e rename  p profile  d remove  a add  c rescan  s save  ←→ section  q close",
            3: "↑↓ choose  Enter change  r reset  s save  ←→ section  q close",
            4: "↑↓/←→ section  F1-F5 panels  q close",
        }[self.sub]

    def changed(self, ui, text):
        """Status line after an edit: says whether Save is still needed (an edit can also undo the last)."""
        ui.say(text + ("  (Save applies it)" if self.model.dirty else "  (as saved)"))

    def cycle(self, ui, step):
        n = self.sub + step
        if 0 <= n < len(self.SUBS):
            self.sub = n
        else:
            ui.switch((ui.active + step) % len(ui.pages), step)

    def entered(self, step):
        self.sub = 0 if step > 0 else len(self.SUBS) - 1

    def key(self, ui, mods, key):
        if mods:
            return
        if key == "LeftArrow" and self.sub > 0:
            self.sub -= 1
        elif key == "RightArrow" and self.sub < len(self.SUBS) - 1:
            self.sub += 1
        elif self.sub == 0:
            ids = list(PRESETS)
            c = self.cursor[0]
            if key == "UpArrow":
                self.cursor[0] = max(0, c - 1)
            elif key == "DownArrow":
                self.cursor[0] = min(len(ids) - 1, c + 1)
            elif key in ("Enter", "Space"):
                self.choose_preset(ui, ids[c])
            elif key in ("[", "]"):
                self.step_accent(ui, -1 if key == "[" else 1)
            elif key == "a":
                self.edit_accent(ui)
        elif self.sub == 1:
            names = list(BINDINGS)
            c = self.cursor[1]
            if key == "UpArrow":
                self.cursor[1] = max(0, c - 1)
            elif key == "DownArrow":
                self.cursor[1] = min(len(names) - 1, c + 1)
            elif key == "Enter":
                self.change_binding(ui, names[c])
            elif key == "r":
                self.reset_binding(ui, names[c])
            elif key == "R":
                self.reset_all(ui)
        elif self.sub == 2:
            self.accounts_key(ui, key)
        elif self.sub == 3:
            self.web_key(ui, key)
        elif key in ("UpArrow", "DownArrow"):
            # About has no list of its own: ↑↓ walk the sections, so the keyboard never dead-ends.
            self.sub = max(0, min(len(self.SUBS) - 1, self.sub + (1 if key == "DownArrow" else -1)))

    def wheel(self, ui, dy):
        if self.sub in (0, 1, 2, 3):
            limit = {0: len(PRESETS), 1: len(BINDINGS), 2: len(self.model.accounts), 3: len(WEB_ROWS)}[self.sub] - 1
            self.cursor[self.sub] = max(0, min(max(limit, 0), self.cursor[self.sub] + dy))

    # actions ------------------------------------------------------------------
    def choose_preset(self, ui, pid):
        if self.model.theme == {"preset": pid, "accent": PRESETS[pid]["palette"]["accent"]}:
            ui.say(f"{PRESETS[pid]['name']} is already the theme")
            return
        # A preset brings its own accent: a custom one chosen for another preset rarely fits.
        self.model.theme = {"preset": pid, "accent": PRESETS[pid]["palette"]["accent"]}
        self.changed(ui, f"theme: {PRESETS[pid]['name']}")

    def set_accent(self, ui, color):
        self.model.theme["accent"] = color
        self.changed(ui, f"accent {color}")

    def step_accent(self, ui, step):
        """[ and ] walk the quick swatches, starting from the current accent (or the preset's own)."""
        choices = self.accent_choices()
        cur = self.model.theme["accent"]
        i = choices.index(cur) + step if cur in choices else 0
        self.set_accent(ui, choices[i % len(choices)])

    def accent_choices(self):
        own = PRESETS[self.model.theme["preset"]]["palette"]["accent"]
        return [own] + [c for c in ACCENTS if c != own]

    def edit_accent(self, ui):
        def ok(text):
            text = text.strip()
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", text):
                return "not a #rrggbb colour"
            self.set_accent(ui, text.lower())
            return None
        ui.open_modal(U.PromptModal("Accent colour", self.model.theme["accent"], ok, "#rrggbb, e.g. #4f9dff"))

    def change_binding(self, ui, name):
        modal = CaptureModal(self, name)
        ui.open_modal(modal)
        modal.opened(ui)

    def reset_binding(self, ui, name):
        self.model.keys[name] = normalize(BINDINGS[name][1])
        self.changed(ui, f"{BINDINGS[name][0]} reset to {self.model.keys[name]}")

    def reset_all(self, ui):
        self.model.keys.update(effective_keys({}))
        self.changed(ui, "all bindings reset to their defaults")

    def accounts_key(self, ui, key):
        accts = self.model.accounts
        c = self.cursor[2]
        if key == "UpArrow":
            self.cursor[2] = max(0, c - 1)
        elif key == "DownArrow":
            self.cursor[2] = min(max(len(accts) - 1, 0), c + 1)
        elif key == "c":
            self.rescan(ui)
        elif key == "a":
            self.add_account(ui)
        elif accts:
            if key in ("Space", "h"):
                self.toggle_hidden(ui, c)
            elif key in ("e", "Enter"):
                self.rename(ui, c)
            elif key == "p":
                self.pick_profile(ui, c)
            elif key == "d":
                self.remove(ui, c)

    def toggle_hidden(self, ui, i):
        a = self.model.accounts[i]
        a["hidden"] = not a.get("hidden")
        self.changed(ui, f"{a['label']} {'hidden' if a['hidden'] else 'shown'} in the header")

    def rename(self, ui, i):
        accts = self.model.accounts

        def ok(text):
            err = valid_label(text, accts, own=i)
            if err:
                return err
            accts[i]["label"] = text
            self.changed(ui, f"renamed to {text}")
            return None
        ui.open_modal(U.PromptModal(f"Rename {accts[i]['label']}", accts[i]["label"], ok, "label shown in the header"))

    def pick_profile(self, ui, i):
        accts = self.model.accounts
        a = accts[i]
        # A profile carries one Anthropic login, so it pairs with one account; the list names
        # the account that already has it.
        owner = {b["profile"]: b["label"] for k, b in enumerate(accts) if k != i and b.get("profile")}
        names = ["(none)"] + self.profiles
        options = [n if n not in owner else f"{n}  (paired with {owner[n]})" for n in names]
        cur = names.index(a["profile"]) if a.get("profile") in names else 0

        def pick(idx):
            if names[idx] in owner:
                ui.say(f"{names[idx]} is already paired with {owner[names[idx]]}; unpair it there first", True)
                return
            a["profile"] = None if idx == 0 else names[idx]
            self.changed(ui, f"{a['label']}: omp profile {a['profile'] or '(none)'}")
        ui.open_modal(U.ChoiceModal(f"omp profile of {a['label']}", options, cur, pick))

    def remove(self, ui, i):
        a = self.model.accounts[i]

        def yes():
            self.changed(ui, remove_account(self.model.accounts, i))
            self.cursor[2] = max(0, min(self.cursor[2], len(self.model.accounts) - 1))
        ui.open_modal(U.ConfirmModal(f"Remove {a['label']}?", yes))

    def add_account(self, ui):
        accts = self.model.accounts

        def label_ok(label):
            err = valid_label(label, accts)
            if err:
                return err
            ui.close_modal()

            def dir_ok(d):
                d = tilde(untilde(d.strip()))
                if not d or d == "~":
                    return "give the account's own Claude config directory"
                if any(untilde(a["dir"]) == untilde(d) for a in accts):
                    return f"{d} is already in the list"
                profile = pair_profile(label, self.profiles)
                accts.append({"label": label, "profile": profile, "dir": d, "auto": False, "hidden": False})
                self.cursor[2] = len(accts) - 1
                login = "" if (Path(untilde(d)) / ".credentials.json").exists() else f"; log in: CLAUDE_CONFIG_DIR={d} claude"
                self.changed(ui, f"added {label}{login}")
                if not profile and self.profiles:
                    ui.close_modal()
                    self.pick_profile(ui, len(accts) - 1)
                return None
            ui.open_modal(U.PromptModal("Claude config directory", f"~/.claude-{label}", dir_ok, "holds .credentials.json"))
            return None
        ui.open_modal(U.PromptModal("New account: label", "", label_ok, "shown in the header"))

    def add_found(self, ui, idx):
        a = self.found.pop(idx)
        self.model.accounts.append(dict(a))
        self.cursor[2] = len(self.model.accounts) - 1
        self.changed(ui, f"added {a['label']}")

    # drawing ------------------------------------------------------------------
    SUB_W = 24  # the section list of the wide layout
    CONTENT_W = (120, 160)  # the content column of the wide layout: from 120 up to 160 columns

    def wide(self, w):
        return w >= U.WIDE_COLS

    def content_w(self, w):
        """Content width of the wide layout: 3/5 of what the section list leaves, within
        CONTENT_W, so a 2560 px window gets a larger preview and detail panes."""
        lo, hi = self.CONTENT_W
        return max(lo, min(hi, (w - self.SUB_W - 5) * 3 // 5))

    def render(self, ui, cv, x, y, w, h):
        p = ui.pal
        draw = getattr(self, "draw_" + self.SUBS[self.sub].lower())
        self.two_pane = self.wide(w)
        if not self.two_pane:
            pos = x + 2  # the section strip (§4.2 rule 2, second level)
            for i, name in enumerate(self.SUBS):
                pos += U.chip(cv, p, pos, y, name, selected=i == self.sub, bg=p["base"],
                              hit=lambda b, i=i: setattr(self, "sub", i)) + 1
            draw(ui, cv, x, y + 2, w, h - 2)
            return None
        # Wide: the section list, a separator, then the content (content_w); the whole
        # composition is centred, and the status and footer rows line up with it. The content's
        # selection marker column sits 1 cell right of the separator.
        cw = self.content_w(w)
        lx, tw = U.content_box(w, self.SUB_W + 5 + cw)
        lx += x
        sep = lx + self.SUB_W + 1
        self.draw_sections(ui, cv, lx, y, h)
        cv.fill(sep, y, 1, h, fg=p["border"], ch="│")
        draw(ui, cv, sep + 2, y, cw + 4, h)
        return lx - 2, tw + 4

    def section_meta(self, i):
        """(text, colour key) at the right of section i in the list: what it holds at a glance,
        amber with a ● while it has unsaved edits."""
        saved = json.loads(self.model.baseline)
        name = self.SUBS[i]
        if name == "Theme":
            text, dirty = PRESETS[self.model.theme["preset"]]["name"].replace("Hivegrid ", ""), self.model.theme != saved[0]
        elif name == "Keys":
            n = sum(self.model.keys[k] != normalize(BINDINGS[k][1]) for k in BINDINGS)
            text, dirty = (f"{n} changed" if n else f"{len(BINDINGS)}"), self.model.keys != saved[1]
        elif name == "Accounts":
            text = str(len(self.model.accounts)) + (f" · {len(self.found)} new" if self.found else "")
            dirty = self.model.accounts != saved[2]
        elif name == "Web":
            text, dirty = f"{self.model.web['links']} · {self.model.web['profile']}", self.model.web != saved[3]
        else:
            text, dirty = f"v{VERSION}", False
        return ("● " + text, "amber") if dirty else (text, "dim")

    def draw_sections(self, ui, cv, x, y, h):
        p = ui.pal
        cv.put(x + 2, y, "Settings", p["muted"], None, BOLD)
        for i, name in enumerate(self.SUBS):
            meta, colour = self.section_meta(i)
            U.list_row(cv, p, y + 2 + i, x=x, w=self.SUB_W, selected=i == self.sub, bg=p["base"], name=name,
                       right=meta, right_color=p[colour], hit=lambda b, i=i: setattr(self, "sub", i))
        cv.put(x + 2, y + 3 + len(self.SUBS), "←→ or Tab", p["dim"])

    CARD_W = 38  # preset cards and the accent block share this width, so their edges line up

    def draw_theme(self, ui, cv, x, y, w, h):
        p = ui.pal
        self.cursor[0] = max(0, min(self.cursor[0], len(PRESETS) - 1))
        chosen = self.model.theme["preset"]
        lx, cw = x + 2, self.CARD_W
        cv.put(lx, y, "Colour theme", p["bright"], None, BOLD)
        step = 4 if h >= 23 else 3  # short windows drop the gap between the cards
        for i, pid in enumerate(PRESETS):
            self.draw_preset_card(ui, cv, lx, y + 2 + i * step, cw, i, pid == chosen)
        ay = y + 2 + len(PRESETS) * step
        self.draw_accent(ui, cv, lx, ay)
        # The preview sits beside the choices when there is room, its colour strip level with
        # the Custom accent row (ay + 4); else it goes below them.
        px = lx + cw + 6
        pw = min(x + w - 2 - px, 120)
        if pw >= 64:
            # Two panes on a tall window: a taller preview (its strip then sits under it).
            ph = max(ay + 5 - y, min(h - 2, 36)) if self.two_pane and h >= 40 else ay + 5 - y
            self.draw_preview(ui, cv, px, y, pw, ph)
        else:
            self.draw_preview(ui, cv, lx, ay + 6, min(w - 4, 120), max(min(h - (ay + 6 - y), 23), 12))

    def draw_preset_card(self, ui, cv, x, y, w, i, chosen):
        """A preset drawn in its own colours (header band, tabs, pills, terminal colours), so the
        card itself is the swatch; the left rule marks the keyboard cursor, ✓ the chosen one."""
        p = ui.pal
        pid = list(PRESETS)[i]
        theme = PRESETS[pid]
        t, term = theme["palette"], theme["terminal"]
        rule = p["accent"] if i == self.cursor[0] else p["base"]
        for k in range(3):
            cv.put(x, y + k, "▌", rule, p["base"])
        cx, inner = x + 1, w - 1
        cv.fill(cx, y, inner, 1, bg=t["deep"])
        cv.put(cx + 1, y, "⬡", t["accent"], t["deep"], BOLD)
        cv.put(cx + 3, y, theme["name"], t["bright"], t["deep"], BOLD)
        if chosen:
            cv.put(cx + inner - 3, y, "✓", t["green"], t["deep"], BOLD)
        cv.fill(cx, y + 1, inner, 2, bg=t["base"])
        pos = cx + 1
        pos += cv.put(pos, y + 1, " 1 omp ", t["bright"], t["raised"], BOLD) + 1
        pos += cv.put(pos, y + 1, " 2 ~ ", t["muted"], t["surface"]) + 1
        for text, fg in ((" 12% ", t["green"]), ("57% ", t["amber"]), ("91% ", t["red"])):
            pos += cv.put(pos, y + 1, text, fg, t["pill"])
        for k, c in enumerate(term["ansi"]):
            cv.put(cx + 1 + k * 3, y + 2, "██", c, t["base"])
        cv.put(cx + inner - 3, y + 2, "Aa", t["text"], t["base"])
        cv.hit(x, y, w, 3, lambda b: (self.cursor.__setitem__(0, i), self.choose_preset(ui, pid)))

    def draw_accent(self, ui, cv, x, y):
        p = ui.pal
        cur = self.model.theme["accent"]
        cv.put(x, y, "Accent", p["bright"], None, BOLD)
        for k, c in enumerate(self.accent_choices()):
            sx = x + 1 + k * 4
            cv.put(sx, y + 2, " ✓ " if c == cur else "   ", p["deep"], c, BOLD)
            cv.hit(sx, y + 2, 3, 1, lambda b, c=c: self.set_accent(ui, c))
        cv.put(x + 1, y + 4, "Custom", p["muted"])
        ui.swatch(cv, x + 9, y + 4, cur, 2)
        cv.put(x + 12, y + 4, cur, p["text"])
        ui.button(cv, x + 21, y + 4, "Edit…", lambda b: self.edit_accent(ui))

    def draw_preview(self, ui, cv, x, y, w, h):
        """A miniature Hivegrid window in the theme being edited: header, bar, two panes (the
        right one inactive and dimmed), the overview dock and the 16 terminal colours."""
        p = ui.pal
        theme = self.model.current_theme()
        t, term = theme["palette"], theme["terminal"]
        ansi = term["ansi"]
        cv.put(x, y, "Preview", p["bright"], None, BOLD)
        note = f"{PRESETS[theme['preset']]['name']}, accent {theme['accent']}"
        cv.put(x + 10, y, note + ("  · not saved yet" if self.model.dirty else ""), p["muted"])
        top = y + 2
        body = h - 5  # rows between the header band and the colour strip
        cv.fill(x, top, w, 1, bg=t["deep"])
        cv.put(x + 1, top, "⬡", t["accent"], t["deep"], BOLD)
        pos = x + 3
        pos += cv.put(pos, top, " 1 omp ", t["bright"], t["raised"], BOLD) + 1
        pos += cv.put(pos, top, " 2 ~ ", t["muted"], t["surface"]) + 2
        for text, fg in ((" work ", t["text"]), ("5h ", t["muted"]), ("12% ", t["green"]), ("7d ", t["muted"]), ("91% ", t["red"])):
            pos += cv.put(pos, top, text, fg, t["pill"])
        stats = " CPU 8%  RAM 35% "
        cv.put(x + w - len(stats) - 1, top, stats, t["text"], t["pill"])
        by = top + 1
        dock_w = 22 if w >= 72 else 0
        cv.fill(x, by, 3, body, bg=t["surface"])
        for k, g in enumerate(("▤", "⑂", "≡")):
            cv.put(x + 1, by + 1 + k * 2, g, t["bright"] if k == 0 else t["dim"], t["raised"] if k == 0 else t["surface"])
        cv.put(x + 1, by + body - 2, "⚙", t["dim"], t["surface"])
        pane_w = (w - 3 - dock_w - 1) // 2
        lx, rx = x + 3, x + 3 + pane_w + 1
        rw = w - 3 - dock_w - pane_w - 1
        cv.fill(lx, by, pane_w, body, bg=t["base"])
        cv.fill(lx + pane_w, by, 1, body, bg=t["base"], fg=term["split"], ch="│")
        dim_bg = U.mix(t["base"], t["deep"], 0.35)  # inactive panes are dimmed
        cv.fill(rx, by, rw, body, bg=dim_bg)
        lines = [
            [("user@hive", ansi[2]), (":", t["text"]), ("~/proj", ansi[4]), ("$ git status", t["text"])],
            [("On branch ", t["text"]), ("main", ansi[6])],
            [("  modified:   src/app.py", ansi[1])],
            [("  new file:   README.md", ansi[2])],
            [("selected text", t["bright"], term["selection_bg"])],
            [("user@hive", ansi[2]), (":", t["text"]), ("~/proj", ansi[4]), ("$ ", t["text"]), ("█", t["accent"])],
        ]
        for k, parts in enumerate(lines[:max(body - 1, 0)]):
            pos = lx + 1
            for part in parts:
                pos += cv.put(pos, by + 1 + k, fit(part[0], max(lx + pane_w - 1 - pos, 0)), part[1],
                              part[2] if len(part) > 2 else t["base"])
        agent = [("●", t["accent"], "Reading 3 files"), ("✓", t["green"], "tests passed"),
                 ("▲", t["amber"], "2 warnings"), ("✗", t["red"], "build failed"), (" ", t["muted"], "a muted note")]
        for k, (g, c, text) in enumerate(agent[:max(body - 1, 0)]):
            cv.put(rx + 1, by + 1 + k, g, U.mix(c, dim_bg, 0.3), dim_bg)
            cv.put(rx + 3, by + 1 + k, fit(text, rw - 4), U.mix(t["muted"] if k == 4 else t["text"], dim_bg, 0.3), dim_bg)
        if dock_w:
            dx = x + w - dock_w
            cv.fill(dx, by, dock_w, body, bg=t["surface"])
            cv.put(dx + 1, by, "⬡ Overview", t["accent"], t["surface"], BOLD)
            dock = [("↻", t["cyan"], "1 omp"), ("✓", t["green"], "2 build"), ("?", t["violet"], "3 docs")]
            for k, (g, c, label) in enumerate(dock[:max(body - 2, 0)]):
                bg = t["raised"] if k == 0 else t["surface"]
                cv.fill(dx, by + 2 + k, dock_w, 1, bg=bg)
                cv.put(dx, by + 2 + k, "▎" if k == 0 else " ", t["accent"], bg)  # selection marker, as in the real dock
                cv.put(dx + 1, by + 2 + k, g, c, bg)
                cv.put(dx + 3, by + 2 + k, label, t["bright"] if k == 0 else t["text"], bg)
        sy = by + body + 1
        stacked = w < 68  # side by side when both strips fit, else one above the other
        for sx, ry, name, cols in ((x, sy, "normal", ansi),
                                   (x if stacked else x + 36, sy + stacked, "bright", term["brights"])):
            cv.put(sx, ry, name, p["muted"])
            for k, c in enumerate(cols):
                ui.swatch(cv, sx + 7 + k * 3, ry, c, 2)

    def detail_head(self, ui, cv, x, y, w, title):
        """Title of a detail pane: the name in bright bold over a rule (T3)."""
        p = ui.pal
        cv.put(x, y, fit(title, w), p["bright"], None, BOLD)
        cv.fill(x, y + 1, w, 1, fg=U.rule_color(p, p["base"]), ch="─")

    def detail_fields(self, ui, cv, x, y, w, rows, label_w=13):
        """Label / value rows of a detail pane: [(label, value, colour)]. -> the row after them."""
        p = ui.pal
        for k, (label, value, colour) in enumerate(rows):
            cv.put(x, y + k, label, p["dim"])
            cv.put(x + label_w, y + k, fit(value, max(w - label_w, 0)), colour)
        return y + len(rows)

    def table_head(self, ui, cv, x, y, tw, cols):
        """Column names [(dx, name)] in muted bold, and the rule under them (T3)."""
        p = ui.pal
        for dx, name in cols:
            cv.put(x + dx, y, name, p["muted"], None, BOLD)
        cv.fill(x, y + 1, tw, 1, fg=U.rule_color(p, p["base"]), ch="─")

    KEYS_TW = 74  # action 34, binding 22, default 16, plus the marker columns

    def draw_keys(self, ui, cv, x, y, w, h):
        p = ui.pal
        names = list(BINDINGS)
        cur = self.cursor[1] = max(0, min(self.cursor[1], len(names) - 1))
        tw = self.KEYS_TW
        bcol, dcol = x + 36, x + 58
        self.table_head(ui, cv, x, y, tw, ((2, "Action"), (36, "Binding"), (58, "Default")))
        for i, name in enumerate(names):
            row = y + 2 + i
            label, default, _ = BINDINGS[name]
            value, dflt = self.model.keys[name], normalize(default)
            changed = value != dflt
            sel = i == cur
            r = U.list_row(cv, p, row, x=x, w=tw, selected=sel, bg=p["base"], name=fit(label, 32),
                           hit=lambda b, i=i: self.cursor.__setitem__(1, i))
            # Changed bindings stand out in amber with their default beside them; unchanged rows
            # show a dim dash rather than repeating the binding.
            cv.put(bcol, row, fit(value, 20), p["amber"] if changed else (p["bright"] if sel else p["text"]), r.bg,
                   BOLD if changed else 0)
            cv.put(dcol, row, fit(dflt, 15) if changed else "—", p["muted"] if changed else p["dim"], r.bg)
            cv.hit(bcol, row, 20, 1, lambda b, n=name, i=i: (self.cursor.__setitem__(1, i), self.change_binding(ui, n)))
            if sel and not self.two_pane:
                # Row actions on the selected row only, beside the table (§4.2 rule 5).
                bw = ui.button(cv, x + tw + 2, row, "Change", lambda b, n=name: self.change_binding(ui, n), accent=True)
                if changed:
                    ui.button(cv, x + tw + 3 + bw, row, "Reset", lambda b, n=name: self.reset_binding(ui, n))
        note = "Changed bindings are amber; — = same as the default."
        if self.two_pane:
            self.draw_key_detail(ui, cv, x + tw + 4, y, w - tw - 6, names[cur])
        else:
            ui.button(cv, x + tw + 2, y, "Reset all", lambda b: self.reset_all(ui))
            note += " Click a binding (or Enter) to record new keys; Save writes settings.json and WezTerm reloads it."
        for k, line in enumerate(textwrap.wrap(note, max(tw - 2 if self.two_pane else w - 4, 20))):
            cv.put(x + 2, y + 3 + len(names) + k, line, p["muted"])

    def draw_key_detail(self, ui, cv, x, y, w, name):
        """The selected binding: what it is, its default, whether it is saved, and its actions."""
        p = ui.pal
        label, default, kind = BINDINGS[name]
        value, dflt = self.model.keys[name], normalize(default)
        changed = value != dflt
        saved = json.loads(self.model.baseline)[1].get(name, dflt)
        if value != saved:
            state, scol = "not saved yet: Save applies it", p["amber"]
        else:
            state, scol = ("changed from the default" if changed else "the default"), p["muted"]
        self.detail_head(ui, cv, x, y, w, label)
        ry = self.detail_fields(ui, cv, x, y + 2, w, [
            ("Binding", value, p["amber"] if changed else p["text"]),
            ("Default", dflt, p["muted"]),
            ("Pressed as", "the leader, then one key" if kind == "leader" else "one key combination", p["muted"]),
            ("State", state, scol),
        ])
        bx = x
        bx += ui.button(cv, bx, ry + 1, "Change", lambda b: self.change_binding(ui, name), accent=True) + 1
        bx += ui.button(cv, bx, ry + 1, "Reset", lambda b: self.reset_binding(ui, name), enabled=changed) + 1
        ui.button(cv, bx, ry + 1, "Reset all", lambda b: self.reset_all(ui))
        text = ("Enter or a click on the binding records new keys; r resets it, R resets all. "
                "Save writes settings.json and WezTerm reloads it.")
        for k, line in enumerate(textwrap.wrap(text, max(w, 20))):
            cv.put(x, ry + 3 + k, line, p["muted"])

    def draw_accounts(self, ui, cv, x, y, w, h):
        p = ui.pal
        accts = self.model.accounts
        cur = self.cursor[2] = max(0, min(self.cursor[2], max(len(accts) - 1, 0)))
        bx = x + 2
        for label, fn in (("Add", lambda b: self.add_account(ui)), ("Rescan", lambda b: self.rescan(ui))):
            bx += ui.button(cv, bx, y, label, fn) + 1
        if not self.two_pane:  # the wide layout has them in the detail pane
            have = bool(accts)
            for label, fn in (("Rename", lambda b: self.rename(ui, cur)), ("Profile", lambda b: self.pick_profile(ui, cur)),
                              ("Hide/Show", lambda b: self.toggle_hidden(ui, cur)), ("Remove", lambda b: self.remove(ui, cur))):
                bx += ui.button(cv, bx, y, label, fn, enabled=have) + 1
        # Fixed columns shared by the list and the "found" rows; the highlight covers the table
        # only. Two panes: label, profile and quota here, the directory in the detail.
        if self.two_pane:
            tw, qx = 68, 38
            cols = "{:<14}{:<16}"
            names = ("Label", "omp profile")
        else:
            tw, qx = 100, 70
            cols = "{:<12}{:<14}{:<28}{:<8}"  # label, profile, dir, source
            names = ("Label", "omp profile", "Config dir", "Source")

        def fields(a):
            f = [fit(a["label"], 11 if not self.two_pane else 13), fit(a.get("profile") or "(none)", 13 if not self.two_pane else 15)]
            if not self.two_pane:
                f += [fit(a["dir"], 27), "auto" if a.get("auto") else "manual"]
            return cols.format(*f)

        self.table_head(ui, cv, x, y + 2, tw, ((2, "Show"), (8, cols.format(*names)), (qx, "Quota")))
        for i, a in enumerate(accts):
            row = y + 4 + i
            sel = i == cur
            hidden = bool(a.get("hidden"))
            fgc = p["dim"] if hidden else (p["bright"] if sel else p["text"])
            r = U.list_row(cv, p, row, x=x, w=tw, selected=sel, bg=p["base"], hit=lambda b, i=i: self.cursor.__setitem__(2, i))
            cv.put(x + 2, row, "[ ]" if hidden else "[x]", p["dim"] if hidden else p["accent"], r.bg)
            cv.hit(x + 2, row, 3, 1, lambda b, i=i: (self.cursor.__setitem__(2, i), self.toggle_hidden(ui, i)))
            cv.put(x + 8, row, fields(a), fgc, r.bg)
            quota = "hidden" if hidden else quota_state(a["label"], time.time())
            cv.put(x + qx, row, fit(quota, tw - qx - 1), p["amber"] if quota == "login needed" else fgc, r.bg)
        if self.two_pane and accts:
            self.draw_account_detail(ui, cv, x + tw + 4, y + 2, w - tw - 6, cur)
        row = y + 5 + len(accts)
        if self.found:
            cv.put(x + 2, row, "Found on this machine", p["bright"], None, BOLD)
            cv.put(x + 24, row, "not in the list yet" + ("" if self.two_pane else "; the header shows them anyway"), p["muted"])
            for k, a in enumerate(self.found):
                ry = row + 2 + k
                cv.put(x + 8, ry, fields(dict(a, auto=True)), p["text"])
                ui.button(cv, x + qx, ry, "Add", lambda b, k=k: self.add_found(ui, k))
            row += 2 + len(self.found)
        else:
            cv.put(x + 2, row, "Every credentials directory on this machine is in the list.", p["muted"])
            row += 1
        cv.put(x + 2, row + 1, fit("omp profiles: " + (", ".join(self.profiles) or "none"), tw - 2), p["dim"])

    def draw_account_detail(self, ui, cv, x, y, w, i):
        """The selected account: where it lives, its profile, header visibility, its quota now,
        and its actions."""
        p = ui.pal
        a = self.model.accounts[i]
        hidden = bool(a.get("hidden"))
        self.detail_head(ui, cv, x, y, w, a["label"])
        ry = self.detail_fields(ui, cv, x, y + 2, w, [
            ("Config dir", a["dir"], p["text"]),
            ("omp profile", a.get("profile") or "(none)", p["text"] if a.get("profile") else p["dim"]),
            ("Source", "found on this machine" if a.get("auto") else "added by hand", p["muted"]),
            ("Header", "hidden" if hidden else "shown", p["dim"] if hidden else p["text"]),
        ])
        cv.put(x, ry, "Quota", p["dim"])
        for line in quota_lines(p, quota_entry(a["label"], time.time()), time.time()):
            pos = x + 13
            for text, colour, attrs in line:
                pos += cv.put(pos, ry, fit(text, max(x + w - pos, 0)), colour, None, attrs)
            ry += 1
        bx = x
        for label, fn in (("Rename", lambda b: self.rename(ui, i)), ("Profile", lambda b: self.pick_profile(ui, i)),
                          ("Show" if hidden else "Hide", lambda b: self.toggle_hidden(ui, i)),
                          ("Remove", lambda b: self.remove(ui, i))):
            bx += ui.button(cv, bx, ry + 1, label, fn) + 1
        text = "Space shows or hides it in the header; e renames, p picks the omp profile, d removes it."
        for k, line in enumerate(textwrap.wrap(text, max(w, 20))):
            cv.put(x, ry + 3 + k, line, p["muted"])

    def web_key(self, ui, key):
        c = self.cursor[3]
        if key == "UpArrow":
            self.cursor[3] = max(0, c - 1)
        elif key == "DownArrow":
            self.cursor[3] = min(len(WEB_ROWS) - 1, c + 1)
        elif key in ("Enter", "Space"):
            self.edit_web(ui, WEB_ROWS[c])
        elif key == "r":
            self.reset_web(ui, WEB_ROWS[c])

    def set_web(self, ui, name, value):
        self.model.web[name] = value
        self.changed(ui, f"web {WEB_LABELS[name].lower()}: {value or '(none)'}")

    def reset_web(self, ui, name):
        self.set_web(ui, name, WEB_DEFAULTS[name])

    def edit_web(self, ui, name):
        if name == "links":  # two choices: a click switches
            self.set_web(ui, name, "system" if self.model.web["links"] == "pane" else "pane")
            return

        def ok(text):
            text = text.strip()
            err = valid_profile(text) if name == "profile" else valid_search(text)
            if err:
                return err
            self.set_web(ui, name, text)
            return None
        hint = "e.g. work, private-research" if name == "profile" else "e.g. https://duckduckgo.com/?q=%s ; empty = no search"
        ui.open_modal(U.PromptModal(f"Web: {WEB_LABELS[name].lower()}", self.model.web[name], ok, hint))

    def web_value(self, name):
        value = self.model.web[name]
        if name == "links":
            return "a web pane beside the terminal" if value == "pane" else "the system browser"
        return value or "(none: a location that is no URL is refused)"

    WEB_TW = 74  # label 34, value 38, plus the marker columns

    def draw_web(self, ui, cv, x, y, w, h):
        p = ui.pal
        cur = self.cursor[3] = max(0, min(self.cursor[3], len(WEB_ROWS) - 1))
        tw = self.WEB_TW
        self.table_head(ui, cv, x, y, tw, ((2, "Setting"), (36, "Value")))
        for i, name in enumerate(WEB_ROWS):
            row = y + 2 + i
            sel = i == cur
            changed = self.model.web[name] != WEB_DEFAULTS[name]
            r = U.list_row(cv, p, row, x=x, w=tw, selected=sel, bg=p["base"], name=WEB_LABELS[name],
                           hit=lambda b, i=i: self.cursor.__setitem__(3, i))
            cv.put(x + 36, row, fit(self.web_value(name), tw - 38), p["amber"] if changed else (p["bright"] if sel else p["text"]), r.bg,
                   BOLD if changed else 0)
            cv.hit(x + 36, row, tw - 38, 1, lambda b, n=name, i=i: (self.cursor.__setitem__(3, i), self.edit_web(ui, n)))
            bx = x + tw + 2
            bx += ui.button(cv, bx, row, "Switch" if name == "links" else "Edit…", lambda b, n=name: self.edit_web(ui, n),
                            accent=sel) + 1
            if changed:
                ui.button(cv, bx, row, "Reset", lambda b, n=name: self.reset_web(ui, n))
        note = ("Web panes need Google Chrome or Chromium (HIVEGRID_CHROME names another). Cookies and logins of a "
                "profile live in ~/.local/share/hivegrid/web/profile-<name>. Save writes settings.json and WezTerm reloads it.")
        lines = textwrap.wrap(note, max(tw - 2, 20))
        for k, line in enumerate(lines):
            cv.put(x + 2, y + 3 + len(WEB_ROWS) + k, line, p["muted"])
        if self.web_built is None:
            self.web_built = web_build_cached()  # None while a thread scans the binaries: no hint yet
        if self.web_built is False:
            cv.put(x + 2, y + 4 + len(WEB_ROWS) + len(lines),
                   fit("This WezTerm has no web pane (the Hivegrid build with the web feature adds it): these settings "
                       "apply once it does.", max(w - 4, 20)), p["amber"])

    def draw_about(self, ui, cv, x, y, w, h):
        p = ui.pal
        if self.about is None:
            self.about = about_info()
        a = self.about
        rows = [
            ("Hivegrid panels", f"version {VERSION}", None),
            ("Settings file", tilde(self.model.settings.path) + ("" if self.model.settings.exists else "  (not written yet)"), None),
            ("WezTerm binary", tilde(a["executable"]), None),
            ("WezTerm version", a["version"], None),
            ("Clickable header segments", "yes" if a["clicks"] else "no", a["clicks"]),
            ("Settings as overlay action", "yes" if a["overlay"] else "no", a["overlay"]),
            ("Custom Hivegrid build", "yes" if a["custom"] else "no", a["custom"]),
            ("Inline images", "yes" if U.images_enabled() else "no (braille charts)", U.images_enabled()),
        ]
        for i, (k, v, ok) in enumerate(rows):
            cv.put(x + 2, y + i, f"{k:<30}", p["muted"])
            cv.put(x + 32, y + i, v, p["text"] if ok is None else (p["green"] if ok else p["amber"]))
        cv.put(x + 2, y + len(rows) + 1, "Stock WezTerm works too: the panel then opens in a tab and the header is not clickable.", p["dim"])


# ---- usage --------------------------------------------------------------------------

RANGES = [("6h", 6 * 3600), ("24h", 24 * 3600), ("7d", 7 * 86400), ("30d", 30 * 86400)]
# claude-quota samples every few minutes while the header refreshes; a longer silence (machine
# off, rate limited) breaks the line instead of drawing a straight ramp across it.
HISTORY_GAP = 20 * 60


def load_history(path):
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict) and isinstance(d.get("ts"), (int, float)) and isinstance(d.get("label"), str):
                    out.append(d)
    except OSError:
        pass
    return out


def conventional_dir(label):
    """The Claude config dir an account label names by convention (see discover_accounts)."""
    return "~/.claude" if label == "main" else f"~/.claude-{label}"


def same_dir(a, b):
    return os.path.normpath(untilde(a)) == os.path.normpath(untilde(b))


def sample_of(s, label, acc_dir):
    """A history sample belongs to the account with its directory. Older samples carry only the
    label they were written under, so they also match through the label convention: history
    written as `2` stays with ~/.claude-2 after the account was renamed `work`."""
    d = s.get("dir")
    if isinstance(d, str):
        return bool(acc_dir) and same_dir(d, acc_dir)
    return s["label"] == label or (bool(acc_dir) and same_dir(conventional_dir(s["label"]), acc_dir))


def series_for(samples, label, since, acc_dir=None):
    """Points and reset times of both windows of the account from `since`, and its first sample."""
    out = {"five_hour": [], "seven_day": []}
    resets = {"five_hour": set(), "seven_day": set()}
    first = None
    for s in samples:
        if not sample_of(s, label, acc_dir):
            continue
        first = s["ts"] if first is None else min(first, s["ts"])
        if s["ts"] < since:
            continue
        for key in out:
            w = s.get(key)
            if isinstance(w, dict) and isinstance(w.get("pct"), (int, float)):
                out[key].append((s["ts"], float(w["pct"])))
                if isinstance(w.get("reset_at"), (int, float)):
                    resets[key].add(round(w["reset_at"] / 60) * 60)
    for key in out:
        out[key].sort()
    return out, resets, first


QUOTA_STATE_WORDS = {"login": "login needed", "expired": "token expired"}


def quota_entry(label, now):
    """The header's cache entry of an account as of `now` (percentages, reset times, state; no
    credentials)."""
    try:
        e = json.loads((cache_dir() / f"{label}.json").read_text())
    except (OSError, ValueError):
        return None
    return current_entry(e, now) if isinstance(e, dict) else None


def quota_color(p, pct):
    """Mirrors config/claudequota.lua color(): the header pill's colour for a percentage."""
    return p["red"] if pct >= 80 else (p["amber"] if pct >= 50 else p["green"])


def chart_text_px(cell_h):
    """Chart text height for a cell height: about the terminal font's size, so the image labels
    read like the panel text around them."""
    return max(round(cell_h * 0.75), 9)


def remaining(secs):
    """Mirrors config/claudequota.lua remaining(): 4d02h, 3h10m, 12m."""
    m = int(max(secs, 0)) // 60
    if m >= 1440:
        return f"{m // 1440}d{m % 1440 // 60:02d}h"
    if m >= 60:
        return f"{m // 60}h{m % 60:02d}m"
    return f"{m}m"


def quota_lines(p, e, now):
    """A cache entry as lines of text segments [(text, colour, attrs)]: `5h 12% resets in 3h10m`,
    `7d 40% resets in 4d02h`, percentages in the header pill colours, then a non-ok state in
    amber."""
    if not e:
        return [[("no data yet", p["muted"], 0)]]
    out = []
    for key, tag in (("five_hour", "5h"), ("seven_day", "7d")):
        wd = e.get(key)
        if not (isinstance(wd, dict) and isinstance(wd.get("pct"), (int, float))):
            continue
        line = [(tag + " ", p["muted"], 0), (f"{wd['pct']:.0f}% ", quota_color(p, wd["pct"]), BOLD)]
        if isinstance(wd.get("reset_at"), (int, float)) and wd["reset_at"] > now:
            line.append(("resets in " + remaining(wd["reset_at"] - now), p["dim"], 0))
        out.append(line)
    state = e.get("state", "?")
    if state != "ok":
        out.append([(QUOTA_STATE_WORDS.get(state, state.replace("_", " ")), p["amber"], 0)])
    return out


def quota_segments(p, e, now):
    """quota_lines on one line, 4 cells between them."""
    out = []
    for k, line in enumerate(quota_lines(p, e, now)):
        out += ([("    ", p["dim"], 0)] if k else []) + line
    return out


class UsagePage(U.Page):
    title = "Usage"

    def __init__(self, model, account=None, range_name="24h", history=None, now=time.time):
        self.model = model
        self.account = account
        self.range = next((i for i, (n, _) in enumerate(RANGES) if n == range_name), 1)
        self.path = Path(history) if history else cache_dir() / "history.jsonl"
        self.now = now
        self._mtime = None
        self.samples = []
        self._tick_at = 0
        self._png_key, self._png = None, None  # the last chart image and what it showed

    def labels(self):
        return [a["label"] for a in self.model.accounts if not a.get("hidden")]

    def account_dir(self):
        return next((a.get("dir") for a in self.model.accounts if a["label"] == self.account), None)

    def on_show(self, ui):
        self.reload()
        labels = self.labels()
        if self.account not in labels and labels:
            self.account = labels[0]

    def reload(self):
        try:
            m = self.path.stat().st_mtime_ns
        except OSError:
            m = None
        if m != self._mtime:
            self._mtime = m
            self.samples = load_history(self.path)
            return True
        return False

    def tick(self, ui):
        t = time.time()
        if t - self._tick_at < 5:
            return False
        self._tick_at = t
        return self.reload()

    def hints(self):
        return "←→ account  ↑↓/1-4 range  Tab next panel  q close"

    def key(self, ui, mods, key):
        labels = self.labels()
        if key in ("LeftArrow", "RightArrow") and labels:
            i = labels.index(self.account) if self.account in labels else 0
            self.account = labels[(i + (1 if key == "RightArrow" else -1)) % len(labels)]
        elif key == "UpArrow":
            self.range = min(self.range + 1, len(RANGES) - 1)
        elif key == "DownArrow":
            self.range = max(self.range - 1, 0)
        elif key in ("1", "2", "3", "4") and not mods:
            self.range = int(key) - 1

    def render(self, ui, cv, x, y, w, h):
        p = ui.pal
        labels = self.labels()
        if self.account not in labels and labels:
            self.account = labels[0]
        wide = w >= U.WIDE_COLS
        col = None
        if wide:
            # Wide: the chart beside a table of every account (its quota now; a click picks it),
            # the two centred within CHART_MAX.
            ux, uw = U.content_box(w, self.CHART_MAX)
            ux += x
            sx = ux + uw - self.SIDE_W
            self.draw_accounts(ui, cv, sx, y, self.SIDE_W, labels)
            x, w = ux - 2, uw - self.SIDE_W - 2
            col = (ux - 2, uw + 4)
        pos = x + 2
        if not wide:
            cv.put(pos, y, "Account", p["muted"])
            pos += 9
            for label in labels:
                pos += U.chip(cv, p, pos, y, label, selected=label == self.account, bg=p["base"],
                              hit=lambda b, label=label: setattr(self, "account", label)) + 1
            pos = max(pos + 2, x + 2)
        cv.put(pos, y, "Range", p["muted"])
        pos += 7
        for i, (name, _) in enumerate(RANGES):
            pos += U.chip(cv, p, pos, y, name, selected=i == self.range, bg=p["base"],
                          hit=lambda b, i=i: setattr(self, "range", i)) + 1
        if not self.account:
            cv.put(x + 2, y + 2, "No accounts configured: add one in Settings, Accounts.", p["muted"])
            return col
        now = self.now()
        span = RANGES[self.range][1]
        series, resets, first = series_for(self.samples, self.account, now - span, self.account_dir())
        self.draw_now(ui, cv, x + 2, y + 2, w - 4, now)
        if first is None:
            cv.put(x + 2, y + 3, fit("No history yet: claude-quota records a sample every few minutes while the header runs.",
                                     w - 4), p["amber"])
        elif first > now - span * 0.98:
            cv.put(x + 2, y + 3, fit("History starts " + time.strftime("%d %b %H:%M", time.localtime(first))
                                     + ": the shaded span before it is not recorded.", w - 4), p["muted"])
        top = y + 5
        ch_h = max(h - 5, 4)
        ch_w = w - 4
        pts5, pts7 = series["five_hour"], series["seven_day"]
        if not (pts5 or pts7):
            if first is not None:
                cv.put(x + 2, top, f"No samples in the last {RANGES[self.range][0]}: pick a longer range.", p["muted"])
            return col
        x0, x1 = now - span, now
        # 5h resets would be a fence at 7d and 30d; 7d resets are drawn at every range.
        markers5 = [(t, p["accent"]) for t in sorted(resets["five_hour"])] if span <= 86400 else []
        markers7 = [(t, p["amber"]) for t in sorted(resets["seven_day"])]
        if U.images_enabled():
            cw, chh = U.cell_pixels()
            # The chart is redrawn only when something it shows changes (not on every hover).
            key = (ch_w, ch_h, cw, chh, self.account, self.range, self._mtime, round(now / 30), json.dumps(p, sort_keys=True))
            if self._png_key != key:
                self._png_key = key
                self._png = U.line_chart_png(
                    ch_w * cw, ch_h * chh,
                    [{"pts": pts7, "color": p["amber"], "label": "7d window"}, {"pts": pts5, "color": p["accent"], "label": "5h window"}],
                    x0, x1, 100.0, p, markers=markers5 + markers7, since=first, marker_label="reset", text_px=chart_text_px(chh),
                    gap=HISTORY_GAP, since_label="not recorded")
            cv.image(x + 2, top, ch_w, ch_h, self._png)
        else:
            self.braille(ui, cv, x + 2, top, ch_w, ch_h, (pts5, markers5), (pts7, markers7), x0, x1, first)
        return col

    CHART_MAX = 220  # chart + account table on a wide window
    SIDE_W = 44  # the account table beside the chart

    def draw_accounts(self, ui, cv, x, y, w, labels):
        """The account table of the wide layout: each account's 5h and 7d use and when its 5h
        window resets (or its state); the shown account is selected, a click shows another."""
        p = ui.pal
        now = self.now()
        for dx, name in ((2, "Account"), (16, "   5h"), (23, "   7d"), (31, "5h resets")):
            cv.put(x + dx, y, name, p["muted"], None, BOLD)
        cv.fill(x, y + 1, w, 1, fg=U.rule_color(p, p["base"]), ch="─")
        for i, label in enumerate(labels):
            row = y + 2 + i
            sel = label == self.account
            r = U.list_row(cv, p, row, x=x, w=w, selected=sel, bg=p["base"], name=fit(label, 13),
                           hit=lambda b, label=label: setattr(self, "account", label))
            e = quota_entry(label, now) or {}
            for dx, key in ((16, "five_hour"), (23, "seven_day")):
                wd = e.get(key)
                if isinstance(wd, dict) and isinstance(wd.get("pct"), (int, float)):
                    cv.put(x + dx, row, f"{wd['pct']:.0f}%".rjust(5), quota_color(p, wd["pct"]), r.bg, BOLD)
            state = e.get("state", "ok") if e else "no data"
            wd = e.get("five_hour")
            if state != "ok":
                text, colour = QUOTA_STATE_WORDS.get(state, state.replace("_", " ")), p["amber"]
            elif isinstance(wd, dict) and isinstance(wd.get("reset_at"), (int, float)) and wd["reset_at"] > now:
                text, colour = remaining(wd["reset_at"] - now), p["dim"]
            else:
                text, colour = "", p["dim"]
            cv.put(x + 31, row, fit(text, w - 32), colour, r.bg)
        ny = y + 3 + len(labels)
        last = max((s["ts"] for s in self.samples if sample_of(s, self.account, self.account_dir())), default=None)
        if last is not None:
            cv.put(x + 2, ny, f"{self.account}: last sample {human_age(now - last)} ago", p["dim"])
            ny += 1
        cv.put(x + 2, ny, "←→ or a click picks the account", p["dim"])

    def draw_now(self, ui, cv, x, y, w, now):
        """`work  5h 12% resets in 3h10m  7d 40% resets in 4d02h`, from the header's cache."""
        p = ui.pal
        pos = x + cv.put(x, y, self.account, p["bright"], None, BOLD) + 3
        for text, colour, attrs in quota_segments(p, quota_entry(self.account, now), now):
            pos += cv.put(pos, y, fit(text, max(x + w - pos, 0)), colour, None, attrs)

    def braille(self, ui, cv, x, y, w, h, five, seven, x0, x1, since):
        """The image-free chart: one braille plot per window (name row, plot on the deep plot
        background, ▲ reset row), then the time axis; the part before `since` has no data."""
        p = ui.pal
        gx, gw = x + 6, max(w - 6, 10)
        half = max((h - 1) // 2, 5)
        gh = half - 3

        def col(t):
            return gx + round((t - x0) / max(x1 - x0, 1) * (gw - 1))

        for k, (name, (pts, markers), color) in enumerate((("5h window", five, p["accent"]), ("7d window", seven, p["amber"]))):
            top = y + k * half
            cv.put(x, top, name, color, None, BOLD)
            if any(x0 <= t <= x1 for t, _ in markers):
                cv.put(gx + gw - 14, top, "▲ window reset", p["dim"])
            cv.fill(gx, top + 1, gw, gh, bg=p["deep"])
            if since is not None and since > x0:
                sw = min(col(since), gx + gw) - gx
                cv.fill(gx, top + 1, sw, gh, bg=p["base"])
                if sw >= 16:  # name the unrecorded span inside it, never over the data
                    cv.put(gx + (sw - 12) // 2, top + 1 + gh // 2, "not recorded", p["dim"])
            rows = U.braille_chart(gw, gh, [{"pts": pts, "color": color}], x0, x1, 100.0, gap=HISTORY_GAP)
            for r, row in enumerate(rows):
                for c, (ch, colour) in enumerate(row):
                    if ch != " ":
                        cv.put(gx + c, top + 1 + r, ch, colour)
                if r in (0, gh // 2, gh - 1):
                    pct = 100 - round(r / max(gh - 1, 1) * 100)
                    cv.put(x, top + 1 + r, f"{pct}%".rjust(5), p["dim"])
            for t, mcol in markers:
                if x0 <= t <= x1:
                    cv.put(col(t), top + 1 + gh, "▲", mcol)
        ty = y + 2 * half
        last = gx - 2
        for t, label in U.time_ticks(x0, x1, max(gw // 14, 2)):
            lx = min(max(col(t) - len(label) // 2, gx), gx + gw - len(label))
            if lx > last + 1:
                cv.put(col(t), ty - 1, "╵", p["dim"])
                cv.put(lx, ty, label, p["dim"])
                last = lx + len(label)


# ---- agents -------------------------------------------------------------------------


def wezterm_cli(*args):
    exe = find_wezterm()
    if not exe or "WEZTERM_UNIX_SOCKET" not in os.environ:
        return None
    try:
        r = subprocess.run([exe, "cli", *args], capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def foreground_by_tty():
    """{ 'pts/3': (pid, comm, etimes, args) } for the foreground process of each terminal."""
    try:
        r = subprocess.run(["ps", "-eo", "pid=,tty=,stat=,etimes=,comm=,args="], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    out = {}
    for line in r.stdout.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 5 or "+" not in parts[2]:
            continue
        pid, tty, _, etimes, comm = parts[:5]
        args = parts[5] if len(parts) > 5 else ""
        # the foreground process group leader is the lowest pid carrying the '+'
        if tty not in out or int(pid) < out[tty][0]:
            out[tty] = (int(pid), comm, int(etimes), args)
    return out


def parse_panes(text, fg, home=None):
    """`wezterm cli list --format json` -> rows for the table, in the mux's order (windows, tabs
    left to right, panes). Hivegrid's own panes (bar, views, dock, this panel) are left out;
    `tab` is the number the tab bar shows."""
    try:
        panes = json.loads(text)
    except ValueError:
        return []
    home = home or os.environ.get("HOME", "")
    tabs, windows = {}, {}
    rows = []
    for pane in panes if isinstance(panes, list) else []:
        win = pane.get("window_id", 0)
        windows.setdefault(win, len(windows) + 1)
        tabs.setdefault(win, {}).setdefault(pane.get("tab_id"), len(tabs[win]) + 1)
        if is_web(pane):  # the fork's web pane: no terminal, its page URL as the directory
            rows.append({
                "pane": pane.get("pane_id"), "tab": tabs[win][pane.get("tab_id")], "window": windows[win],
                "proc": "web", "profile": "", "profile_set": False,
                "cwd": U.printable_text(web_label(pane.get("cwd"))),
                "title": U.printable_text(str(pane.get("title", ""))), "age": 0, "active": bool(pane.get("is_active")),
                "agent": False, "idle": False,
            })
            continue
        tty = str(pane.get("tty_name") or "").replace("/dev/", "")
        pid, comm, etimes, args = fg.get(tty, (None, "", 0, ""))
        if is_chrome(comm, args):
            continue
        cwd = re.sub(r"^file://[^/]*", "", str(pane.get("cwd", "")))
        cwd = cwd.rstrip("/") or cwd
        if home and (cwd == home or cwd.startswith(home.rstrip("/") + "/")):
            cwd = "~" + cwd[len(home.rstrip("/")):]
        m = re.search(r"--profile[= ](\S+)", args)
        profile = m.group(1) if m and comm == "omp" else ""
        if comm == "omp" and not profile:
            # started without --profile: the hive extension's state file names the one omp chose
            try:
                with open(os.path.join(state_dir(), f"{pane.get('pane_id')}.json")) as f:
                    state = json.load(f)
                profile = state.get("profile") if isinstance(state, dict) and isinstance(state.get("profile"), str) else ""
            except (OSError, ValueError):
                pass
        rows.append({
            "pane": pane.get("pane_id"), "tab": tabs[win][pane.get("tab_id")], "window": windows[win],
            "proc": comm or "?", "profile": profile, "profile_set": bool(m), "cwd": cwd,
            "title": str(pane.get("title", "")), "age": etimes, "active": bool(pane.get("is_active")),
            "agent": comm in AGENT_PROCS, "idle": comm in SHELLS,
        })
    return rows


def focused_pane():
    """The pane the panel was opened over: --origin (HIVEGRID_ORIGIN_PANE, the overlay's own pane
    is not in the list), else the GUI client's focused pane (panel in a tab), else None."""
    origin = os.environ.get("HIVEGRID_ORIGIN_PANE", "")
    if origin.isdigit():
        return int(origin)
    try:
        clients = json.loads(wezterm_cli("list-clients", "--format", "json") or "[]")
        return clients[0].get("focused_pane_id") if clients else None
    except (ValueError, AttributeError, IndexError):
        return None


class AgentsPage(U.Page):
    title = "Agents"

    def __init__(self, model):
        self.model = model
        self.rows = []
        self.cursor = 0
        self.top = 0
        self.page_rows = 10
        self.error = ""
        self._at = 0
        self._placed = False

    def on_show(self, ui):
        self._at = 0
        self.tick(ui)
        if not self._placed and self.rows:
            self._placed = True
            focus = focused_pane()
            self.cursor = next((i for i, r in enumerate(self.rows) if r["pane"] == focus), self.cursor)

    def hints(self):
        return "↑↓ choose  Enter/click jump to the pane  Tab next panel  q close"

    def refresh(self):
        text = wezterm_cli("list", "--format", "json")
        if text is None:
            self.error = "No WezTerm mux in this terminal: open the panel inside Hivegrid."
            self.rows = []
            return
        self.error = ""
        current = self.rows[self.cursor]["pane"] if 0 <= self.cursor < len(self.rows) else None
        self.rows = parse_panes(text, foreground_by_tty())
        # keep the cursor on its pane while panes come and go
        self.cursor = next((i for i, r in enumerate(self.rows) if r["pane"] == current), self.cursor)

    def tick(self, ui):
        t = time.time()
        if t - self._at < 2:
            return False
        self._at = t
        self.refresh()
        return True

    def move(self, step):
        self.cursor = max(0, min(len(self.rows) - 1, self.cursor + step))

    def wheel(self, ui, dy):
        self.move(dy)

    def key(self, ui, mods, key):
        steps = {"UpArrow": -1, "DownArrow": 1, "PageUp": -self.page_rows, "PageDown": self.page_rows,
                 "Home": -len(self.rows), "End": len(self.rows)}
        if key in steps:
            self.move(steps[key])
        elif key == "Enter" and self.rows:
            self.jump(ui, self.rows[self.cursor])

    def jump(self, ui, row):
        if wezterm_cli("activate-pane", "--pane-id", str(row["pane"])) is None:
            ui.say(f"Could not activate pane {row['pane']}: it may have closed.", True)
            return
        ui.running = False

    def render(self, ui, cv, x, y, w, h):
        p = ui.pal
        if self.error:
            U.empty_state(cv, p, x + 2, y, w - 4, self.error)
            return None
        if not self.rows:
            U.empty_state(cv, p, x + 2, y, w - 4, "No panes besides Hivegrid's own.",
                          "The bar, the sidebar views, the overview dock and this panel are not listed.")
            return None
        multi = any(r["window"] > 1 for r in self.rows)
        tw = 6 if multi else 4
        rest = max(w - (tw + 7 + 11 + 11 + 9) - 4, 20)
        dw = min(max(rest * 2 // 5, 16), 60)  # directory column: 40% of the free room
        cols = [("tab", tw, ">"), ("pane", 7, ">"), ("process", 11, "<"), ("profile", 11, "<"), ("running", 9, "<"),
                ("directory", dw + 2, "<"), ("title", min(rest - dw - 2, 62), "<")]
        table = min(w, 2 + sum(c[1] for c in cols))  # the selection slab ends with the table, as in Settings
        # The table is centred on a wide window (its width is capped by the column caps).
        if w >= U.WIDE_COLS:
            x += (w - table) // 2
        pos = x + 2
        for name, width, align in cols:
            cv.put(pos, y, f"{name:{align}{width - 2}}", p["muted"], None, BOLD)
            pos += width
        cv.fill(x, y + 1, table, 1, fg=U.rule_color(p, p["base"]), ch="─")
        self.page_rows = body = max(h - 4, 1)
        self.cursor = max(0, min(self.cursor, len(self.rows) - 1))
        self.top = min(max(self.top, self.cursor - body + 1), self.cursor, max(len(self.rows) - body, 0))
        for i in range(self.top, min(self.top + body, len(self.rows))):
            r = self.rows[i]
            row = y + 2 + i - self.top
            sel = i == self.cursor
            text = p["bright"] if sel else p["text"]
            bg = U.list_row(cv, p, row, x=x, w=table, selected=sel, bg=p["base"],
                            hit=lambda b, r=r, i=i: (setattr(self, "cursor", i), self.jump(ui, r))).bg
            if r["active"]:
                cv.put(x + 1, row, "•", p["accent"], bg, BOLD)
            tab = f"{r['window']}:{r['tab']}" if multi else str(r["tab"])
            cells = [(tab, text, 0, "end"), (str(r["pane"]), p["muted"], 0, "end"),
                     (r["proc"], p["green"] if r["agent"] else (p["dim"] if r["idle"] else text), BOLD if r["agent"] else 0, "end"),
                     (r["profile"], text if r["profile_set"] else p["dim"], 0, "end"),
                     (human_age(r["age"]) if r["age"] else "", p["muted"], 0, "end"),
                     (r["cwd"], text, 0, "path"), (r["title"], text, 0, "end")]
            pos = x + 2
            for (value, colour, attrs, how), (_, width, align) in zip(cells, cols):
                value = U.fit_text(value, width - 2, how)
                pad = " " * (width - 2 - U.text_width(value))
                cv.put(pos, row, pad + value if align == ">" else value, colour, bg, attrs)
                pos += width
        shown = min(body, len(self.rows))
        agents = sum(r["agent"] for r in self.rows)
        ntabs = len({(r["window"], r["tab"]) for r in self.rows})
        more = f"  rows {self.top + 1}-{self.top + shown} of {len(self.rows)}" if len(self.rows) > body else ""
        n = len(self.rows)
        cv.put(x + 2, y + 3 + shown, f"{n} pane{'s' if n != 1 else ''} in {ntabs} tab{'s' if ntabs != 1 else ''}, {agents} "
               f"agent{'s' if agents != 1 else ''}{more}   • active pane of its tab", p["dim"])
        return (x, table) if w >= U.WIDE_COLS else None


# ---- monitor ------------------------------------------------------------------------

MON_RANGES = [("1m", 60), ("5m", 300), ("15m", 900), ("1h", 3600), ("6h", 21600), ("24h", 86400), ("7d", 604800)]
LIVE_MAX = 900  # ranges up to this are drawn one slot per second, longer ones from the log alone
LOG_EVERY = 15  # seconds between the lines config/sysstats.lua logs
LOG_SLOTS = 600  # slots of a long range
LOG_KEYS = ("CPU", "RAM", "DSK", "GPU")


def sysstats_log_path():
    """The file the header's sysstats.lua appends one line to every LOG_EVERY seconds, whether
    this panel is open or not (and its rotated predecessor, `.1`)."""
    env = os.environ.get("HIVEGRID_SYSSTATS_LOG")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "hivegrid" / "sysstats.jsonl"


def read_sysstats_log(path, offset=0):
    """-> (rows, end offset): `[ts, cpu, ram, dsk, gpu]` per complete line from `offset` on, a
    value None where the line lacks the source. A line still being written is left for the next
    read, and unreadable lines are skipped."""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return [], offset
    end = data.rfind(b"\n") + 1
    rows = []
    for line in data[:end].splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        num = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        if isinstance(d, dict) and num(d.get("ts")) is not None:
            rows.append([d["ts"]] + [num(d.get(k.lower())) for k in LOG_KEYS])
    return rows, offset + end


def parse_cpu_line(text):
    m = re.match(r"cpu\s+(.+)", text)
    if not m:
        return None
    f = [int(v) for v in m.group(1).split()]
    return sum(f[:8]), f[3] + (f[4] if len(f) > 4 else 0)


def parse_meminfo(text):
    vals = dict(re.findall(r"(\w+):\s+(\d+)", text))
    total, avail = int(vals.get("MemTotal", 0)), int(vals.get("MemAvailable", 0))
    return (100.0 * (total - avail) / total) if total else None, total, avail


class Sampler:
    """CPU, RAM, GPU and disk of `/` every second while the panel is open (the last 15 minutes
    are kept), plus what the header logged every LOG_EVERY seconds, panel open or not."""

    JETSON = ("/sys/devices/platform/gpu.0/load", "/sys/devices/gpu.0/load")

    def __init__(self, keep=900, log=None):
        self.hist = {k: deque(maxlen=keep) for k in ("CPU", "RAM", "GPU", "DSK")}
        self.times = deque(maxlen=keep)
        self.prev = None
        self.t = 0
        self.gpu_t = 0
        self.gpu = None
        self.gpu_kind = None
        self.gpu_text = ""
        self.ram_text = ""
        self.dsk_text = ""
        for p in self.JETSON:
            if os.path.exists(p):
                self.gpu_kind, self.gpu_path = "jetson", p
        if self.gpu_kind is None and shutil.which("nvidia-smi"):
            self.gpu_kind = "nvidia"
        self.log_path = Path(log) if log else sysstats_log_path()
        self.log = []  # [ts, cpu, ram, dsk, gpu] per logged line, oldest first
        self.log_pos = None  # (inode, bytes read) of the current log file
        self.log_t = 0

    def read(self, path):
        try:
            with open(path) as f:
                return f.read()
        except OSError:
            return ""

    def sample_gpu(self, now):
        if self.gpu_kind is None or now - self.gpu_t < 3:
            return
        self.gpu_t = now
        if self.gpu_kind == "jetson":
            try:
                self.gpu = int(self.read(self.gpu_path).strip()) / 10.0
                self.gpu_text = ""  # Jetson reports only the load
            except ValueError:
                self.gpu = None
            return
        try:
            r = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
                                "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=2)
            u, used, total = [float(v) for v in r.stdout.splitlines()[0].split(",")]
            self.gpu = u
            self.gpu_text = f"{used / 1024:.1f}/{gib_text(total / 1024)}G"
        except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
            self.gpu = None

    def refresh_log(self, now):
        """Pick up the lines the header appended since the last look (at most every 5 s); read
        both files again when the current one was rotated away."""
        if now - self.log_t < 5:
            return
        self.log_t = now
        try:
            st = os.stat(self.log_path)
        except OSError:
            st = None
        pos = self.log_pos
        if st and pos and pos[0] == st.st_ino and pos[1] <= st.st_size:
            rows, end = read_sysstats_log(self.log_path, pos[1])
        else:
            self.log, _ = read_sysstats_log(str(self.log_path) + ".1")
            rows, end = read_sysstats_log(self.log_path)
        self.log_pos = (st.st_ino, end) if st else None
        self.log.extend(rows)
        self.log.sort(key=lambda r: r[0])  # sorted already unless two windows' lines interleaved

    def tick(self):
        now = time.time()
        if now - self.t < 1.0:
            return False
        self.t = now
        self.refresh_log(now)
        cpu = parse_cpu_line(self.read("/proc/stat"))
        pct = None
        if cpu and self.prev:
            dt, di = cpu[0] - self.prev[0], cpu[1] - self.prev[1]
            pct = 100.0 * (dt - di) / dt if dt > 0 else None
        self.prev = cpu
        ram, total, avail = parse_meminfo(self.read("/proc/meminfo"))
        if ram is not None:
            self.ram_text = f"{(total - avail) / 1048576:.1f}G/{gib_text(total / 1048576)}G"
        try:
            du = shutil.disk_usage("/")
            # df's Use%: used / (used + available to users), as the header's DSK chip shows it
            dsk = 100.0 * du.used / max(du.used + du.free, 1)
            self.dsk_text = f"{gib_text(du.free / 2**30)}G free"
        except OSError:
            dsk = None
        self.sample_gpu(now)
        self.times.append(now)
        for key, val in (("CPU", pct), ("RAM", ram), ("GPU", self.gpu), ("DSK", dsk)):
            self.hist[key].append(val)
        return True

    def values(self, key, seconds, now=None):
        """Slots of the last `seconds`, oldest first, None where nothing was sampled. Up to
        LIVE_MAX seconds it is one slot per second, the panel's own samples over the log's;
        longer ranges take LOG_SLOTS slots from the log alone, a slot's value the mean of its
        lines. A slot with no sample keeps the value before it for up to three log intervals
        (timing jitter, a skipped line), so the graph has no false gaps but a closed Hivegrid
        shows as one."""
        now = (self.t or time.time()) if now is None else now
        width = 1.0 if seconds <= LIVE_MAX else seconds / LOG_SLOTS
        n = round(seconds / width)
        out = [None] * (n + 1)
        k = LOG_KEYS.index(key) + 1
        acc = {}
        for row in self.log[bisect.bisect_left(self.log, [now - seconds - width]):]:
            i = n - round((now - row[0]) / width)
            if row[k] is not None and 0 <= i <= n:
                s = acc.setdefault(i, [0.0, 0])
                s[0] += row[k]
                s[1] += 1
        for i, (total, count) in acc.items():
            out[i] = total / count
        if seconds <= LIVE_MAX:
            for t, v in zip(self.times, self.hist[key]):
                i = n - round((now - t) / width)
                if v is not None and 0 <= i <= n:
                    out[i] = v
        hold = int(3 * LOG_EVERY / width) + 1
        last = None
        for i in range(n + 1):
            if out[i] is not None:
                last = i
            elif last is not None and i - last <= hold:
                out[i] = out[last]
        return out

    def current(self, key):
        h = self.hist[key]
        return h[-1] if h and h[-1] is not None else None

    def amount(self, key):
        """The header chip's amount after the percentage: 9.8G/31G, 140G free, 0.0/16G."""
        return {"RAM": self.ram_text, "GPU": self.gpu_text, "DSK": self.dsk_text}.get(key, "")


def gib_text(g):
    """Mirrors config/sysstats.lua size(): whole GiB from 10 up, one decimal below."""
    return f"{g:.0f}" if g >= 10 else f"{g:.1f}"


def load_color(p, pct):
    """Mirrors the thresholds of config/sysstats.lua level(): above 85 critical, from 60 a warning."""
    return p["red"] if pct > 85 else (p["amber"] if pct >= 60 else p["green"])


class MonitorPage(U.Page):
    title = "Monitor"

    def __init__(self, model, sampler=None):
        self.model = model
        self.sampler = sampler or Sampler()
        self.range = 1

    def hints(self):
        return "←→/1-7 range  Tab next panel  q close"

    def key(self, ui, mods, key):
        if key == "RightArrow":
            self.range = min(self.range + 1, len(MON_RANGES) - 1)
        elif key == "LeftArrow":
            self.range = max(self.range - 1, 0)
        elif key in ("1", "2", "3", "4", "5", "6", "7") and not mods:
            self.range = int(key) - 1

    def tick(self, ui):
        return self.sampler.tick()

    MAX_W = 200  # graph width cap on a wide window; the graphs are centred

    def render(self, ui, cv, x, y, w, h):
        p = ui.pal
        col = None
        if w >= U.WIDE_COLS:
            ux, uw = U.content_box(w, self.MAX_W)
            x, w = x + ux - 2, uw + 4
            col = (x, w)
        cv.put(x + 2, y, "Range", p["muted"])
        pos = x + 9
        for i, (name, _) in enumerate(MON_RANGES):
            pos += U.chip(cv, p, pos, y, name, selected=i == self.range, bg=p["base"],
                          hit=lambda b, i=i: setattr(self, "range", i)) + 1
        gpu = {"nvidia": "nvidia-smi", "jetson": "Jetson gpu.0/load"}.get(self.sampler.gpu_kind)
        live = "live 1 s, header log 15 s"
        extra = f"; GPU from {gpu}" if gpu else "; no GPU found"
        if pos + 2 + len(live) + len(extra) + 2 <= x + w:
            live += extra
        cv.put(pos + 2, y, live, p["dim"])
        seconds = MON_RANGES[self.range][1]
        metrics = [("CPU", p["accent"]), ("RAM", p["green"]), ("DSK", p["amber"])]
        if self.sampler.gpu_kind:
            metrics.append(("GPU", p["violet"]))
        top = y + 2
        avail = h - 2
        each, extra = divmod(avail, len(metrics))
        gy = top
        for k, (key, color) in enumerate(metrics):
            gh = max(each + (1 if k < extra else 0) - 1, 3)  # the last row of each slot is the gap
            vals = self.sampler.values(key, seconds)
            cur = self.sampler.current(key)
            value = "n/a" if cur is None else f"{cur:.0f}% {self.sampler.amount(key)}".rstrip()
            vcol = p["muted"] if cur is None else load_color(p, cur)
            if U.images_enabled():
                cw, chh = U.cell_pixels()
                png = U.sparkline_png((w - 4) * cw, gh * chh, vals, color, p, key, value, value_color=vcol,
                                      text_px=chart_text_px(chh))
                cv.image(x + 2, gy, w - 4, gh, png)
            else:
                cv.fill(x + 2, gy, w - 4, gh, bg=p["deep"])
                cv.put(x + 3, gy, key, p["muted"], None, BOLD)
                cv.put(x + w - 3 - len(value), gy, value, vcol, None, BOLD)
                if gh > 1:
                    pts = [(i, v) for i, v in enumerate(vals) if v is not None]
                    rows = U.braille_chart(w - 6, gh - 1, [{"pts": pts, "color": color}], 0, len(vals) - 1, 100.0)
                    for r, row in enumerate(rows):
                        for c, (ch, colour) in enumerate(row):
                            if ch != " ":
                                cv.put(x + 3 + c, gy + 1 + r, ch, colour)
            gy += gh + 1
        return col
