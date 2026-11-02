"""The Hivegrid panels: settings, usage history, agent overview, system monitor, bots."""
import bisect
import copy
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import textwrap
import time
from collections import deque
from pathlib import Path

import hivegrid_remote_data as RD
import hivegrid_schedule as HS
import hivegrid_ui as U
from hivegrid_agents import (AGENT_PROCS, SHELLS, format_cost, format_tokens, is_chrome, is_web, parse_usage,
                             state_dir, totals_text, usage_totals, web_build_cached, web_label)
from hivegrid_settings_core import (
    AGENT_DEFAULT, AGENT_NAMES, AGENTS, BINDINGS, BROWSER_AUTO, PRESETS, VERSION, WEB_DEFAULTS, WEB_THEMES, about_info, find_wezterm,
    cache_dir, check_binding,
    check_browser, current_entry, detect_browsers, discover_accounts, effective_keys, format_combo, merge_accounts,
    normalize, normalize_home, omp_profiles, pair_profile, quota_state, remove_account, tilde, untilde,
    SCHEDULE_DEFAULTS, valid_label, valid_profile, valid_search, wezterm_defaults,
)

ACCENTS = ["#4f9dff", "#38bdf8", "#34d399", "#fbbf24", "#a78bfa", "#f87171", "#f472b6", "#e5e7eb"]
BOLD, DIM = U.BOLD, U.DIM


def fit(text, n):
    text = str(text)
    return text if len(text) <= n else text[: max(n - 1, 0)] + "…"


def chat_text(text, limit):
    """Display text for the shared chat parser: no control characters except newline, at most `limit` characters."""
    if not isinstance(text, str):
        return ""
    text = text.replace("\r\n", "\n").replace("\t", "    ")
    text = "".join(c for c in text if c == "\n" or c.isprintable())
    return text if len(text) <= limit else text[: max(limit - 1, 0)] + "…"


def chat_line(text, limit):
    return chat_text(" ".join(str(text or "").split()), limit)


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


WEB_ROWS = ("links", "theme", "browser", "home", "search", "profile")
WEB_LABELS = {"links": "Open links in", "theme": "Page colours", "browser": "Browser", "home": "Home page", "search": "Search engine",
              "profile": "Default profile"}
AUTO_BROWSER = "Auto (Google Chrome, then Chromium)"
WEB_THEME_TEXT = {"dark": "dark (pages that follow the system scheme turn dark)", "light": "light",
                  "system": "system (the browser's own, no emulation)"}
CONFINED_SHORT = "their sandbox hides the profile directory and their launcher does not reliably pass on the DevTools pipe"


class SettingsPage(U.Page):
    title = "Settings"
    SUBS = ["Theme", "Keys", "Accounts", "Web", "Agent", "About"]

    def __init__(self, model):
        self.model = model
        self.sub = 0
        self.cursor = [list(PRESETS).index(model.theme["preset"]), 0, 0, 0, AGENTS.index(model.agent)]
        self.profiles = omp_profiles()
        self.wez_defaults = None
        self.about = None
        stored = model.settings.stored_accounts()
        model.accounts = stored if stored is not None else merge_accounts(None, discover_accounts())
        model.baseline = model.snapshot()
        self.found = []
        self.two_pane = False  # set by render: the wide layout (section list, list + detail)
        self.web_built = None  # does this WezTerm have the web pane? (looked up when the Web section draws)
        self.browsers = None  # (installed browsers, skipped confined ones), scanned when the Web section first draws
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
            4: "↑↓ choose  Enter use  r reset  s save  ←→ section  q close",
            5: "↑↓/←→ section  F1-F5 panels  q close",
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
        elif self.sub == 4:
            self.agent_key(ui, key)
        elif key in ("UpArrow", "DownArrow"):
            # About has no list of its own: ↑↓ walk the sections, so the keyboard never dead-ends.
            self.sub = max(0, min(len(self.SUBS) - 1, self.sub + (1 if key == "DownArrow" else -1)))

    def wheel(self, ui, dy):
        if self.sub in (0, 1, 2, 3, 4):
            limit = {0: len(PRESETS), 1: len(BINDINGS), 2: len(self.model.accounts), 3: len(WEB_ROWS),
                     4: len(AGENTS)}[self.sub] - 1
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
        elif name == "Agent":
            text, dirty = AGENT_NAMES[self.model.agent], self.model.agent != saved[4]
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
        step = 4 if h >= 7 + 4 * len(PRESETS) else 3  # short windows drop the gap between the cards
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
        if name == "browser":
            self.choose_browser(ui)
            return
        if name == "theme":
            order = list(WEB_THEMES)
            ui.open_modal(U.ChoiceModal("Web: page colours", [WEB_THEME_TEXT[t] for t in order],
                                        order.index(self.model.web["theme"]),
                                        lambda idx: self.set_web(ui, "theme", order[idx])))
            return

        def ok(text):
            text = text.strip()
            if name == "home":
                value, err = normalize_home(text)
            else:
                value, err = text, valid_profile(text) if name == "profile" else valid_search(text)
            if err:
                return err
            self.set_web(ui, name, value)
            return None
        hint = {"profile": "e.g. work, private-research", "home": "e.g. https://duckduckgo.com or about:blank",
                "search": "e.g. https://duckduckgo.com/?q=%s ; empty = no search"}[name]
        ui.open_modal(U.PromptModal(f"Web: {WEB_LABELS[name].lower()}", self.model.web[name], ok, hint))

    def installed_browsers(self):
        if self.browsers is None:
            self.browsers = detect_browsers()
        return self.browsers

    def choose_browser(self, ui):
        """Auto, one of the installed browsers, or a path of one's own (checked: exists, executable,
        answers --version). Firefox is not offered (no screencast API), nor are snap or Flatpak ones."""
        found = self.installed_browsers()[0]
        current = self.model.web["browser"]
        options = [AUTO_BROWSER] + [f"{label}  {tilde(path)}" for label, path in found] + ["Custom path…"]
        paths = [path for _, path in found]
        cur = 0 if current == BROWSER_AUTO else (1 + paths.index(current) if current in paths else len(options) - 1)

        def pick(idx):
            if idx == 0:
                self.set_web(ui, "browser", BROWSER_AUTO)
            elif idx <= len(found):
                self.set_web(ui, "browser", paths[idx - 1])
            else:
                self.custom_browser(ui)
        ui.open_modal(U.ChoiceModal("Web: browser", options, cur, pick))

    def custom_browser(self, ui):
        current = self.model.web["browser"]

        def ok(text):
            err, path, _version = check_browser(text)
            if err:
                return err
            self.set_web(ui, "browser", path)
            return None
        ui.open_modal(U.PromptModal("Web: browser path", "" if current == BROWSER_AUTO else current, ok,
                                    "full path of a Chrome, Chromium, Brave, Edge or Vivaldi binary"))

    def web_value(self, name):
        value = self.model.web[name]
        if name == "links":
            return "a web pane beside the terminal" if value == "pane" else "the system browser"
        if name == "theme":
            return WEB_THEME_TEXT[value]
        if name == "browser":
            if value == BROWSER_AUTO:
                return AUTO_BROWSER
            named = {path: label for label, path in self.installed_browsers()[0]}
            return f"{named[value]}  {tilde(value)}" if value in named else f"Custom  {tilde(value)}"
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
            bx += ui.button(cv, bx, row, {"links": "Switch", "theme": "Choose…", "browser": "Choose…"}.get(name, "Edit…"),
                            lambda b, n=name: self.edit_web(ui, n), accent=sel) + 1
            if changed:
                ui.button(cv, bx, row, "Reset", lambda b, n=name: self.reset_web(ui, n))
        skipped = [os.path.basename(path) for path, _ in self.installed_browsers()[1]]
        notes = [
            "Chromium-family browsers only (Chrome, Chromium, Brave, Edge, Vivaldi): the page is shown from the "
            "browser's screencast, which Firefox does not have. Snap and Flatpak browsers are not offered: "
            + CONFINED_SHORT + (f" (skipped here: {', '.join(skipped)})." if skipped else "."),
            "HIVEGRID_CHROME overrides the choice. A browser already running for a profile stays until its last web "
            "pane closes; the next one starts with the new choice.",
            "Cookies and logins of a profile live in ~/.local/share/hivegrid/web/profile-<name>. Save writes "
            "settings.json and WezTerm reloads it.",
        ]
        ny = y + 3 + len(WEB_ROWS)
        for note in notes:
            for line in textwrap.wrap(note, max(tw - 2, 20)):
                cv.put(x + 2, ny, line, p["muted"])
                ny += 1
            ny += 1
        if self.web_built is None:
            self.web_built = web_build_cached()  # None while a thread scans the binaries: no hint yet
        if self.web_built is False:
            cv.put(x + 2, ny,
                   fit("This WezTerm has no web pane (the Hivegrid build with the web feature adds it): these settings "
                       "apply once it does.", max(w - 4, 20)), p["amber"])

    # agent ----------------------------------------------------------------------
    AGENT_NOTES = [
        "omp: accounts are omp profiles (Accounts, p), agent states are exact through the omp extension, and a "
        "running chat can move to another account from the header.",
        "Claude Code: starts claude; an account pill starts it with that account's CLAUDE_CONFIG_DIR. Its states "
        "are read from the screen and the process, not exact, and a chat does not move between accounts or come "
        "back after a restart (the pane returns as a shell in its directory).",
        "The choice applies to new tabs (LEADER+1, 2, 3), the + menu and the account pills in the header; panes "
        "that already run an agent are not touched. Save writes settings.json and WezTerm reloads it.",
    ]

    def agent_key(self, ui, key):
        c = self.cursor[4]
        if key == "UpArrow":
            self.cursor[4] = max(0, c - 1)
        elif key == "DownArrow":
            self.cursor[4] = min(len(AGENTS) - 1, c + 1)
        elif key in ("Enter", "Space"):
            self.set_agent(ui, AGENTS[c])
        elif key == "r":
            self.set_agent(ui, AGENT_DEFAULT)

    def set_agent(self, ui, name):
        if self.model.agent == name:
            ui.say(f"{AGENT_NAMES[name]} is already the agent")
            return
        self.model.agent = name
        self.changed(ui, f"agent: {AGENT_NAMES[name]}")

    def draw_agent(self, ui, cv, x, y, w, h):
        p = ui.pal
        cur = self.cursor[4] = max(0, min(self.cursor[4], len(AGENTS) - 1))
        tw = self.WEB_TW
        self.table_head(ui, cv, x, y, tw, ((2, "Agent for new panes"), (36, "Command")))
        for i, name in enumerate(AGENTS):
            row = y + 2 + i
            sel = i == cur
            used = self.model.agent == name
            r = U.list_row(cv, p, row, x=x, w=tw, selected=sel, bg=p["base"], name=AGENT_NAMES[name],
                           hit=lambda b, i=i, n=name: (self.cursor.__setitem__(4, i), self.set_agent(ui, n)))
            cv.put(x + 36, row, "omp" if name == "omp" else "claude", p["bright"] if sel else p["text"], r.bg)
            bx = x + tw + 2
            if used:
                cv.put(bx, row, "● in use", p["green"], None)
            else:
                ui.button(cv, bx, row, "Use", lambda b, n=name: self.set_agent(ui, n), accent=sel)
        ny = y + 3 + len(AGENTS)
        for note in self.AGENT_NOTES:
            for line in textwrap.wrap(note, max(tw - 2, 20)):
                cv.put(x + 2, ny, line, p["muted"])
                ny += 1
            ny += 1

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
        usage = None
        if comm == "omp":
            # the hive extension's state file names the profile omp chose when it started without
            # --profile, and carries the tokens and cost omp reported
            try:
                with open(os.path.join(state_dir(), f"{pane.get('pane_id')}.json")) as f:
                    state = json.load(f)
            except (OSError, ValueError):
                state = None
            if isinstance(state, dict):
                if not profile and isinstance(state.get("profile"), str):
                    profile = state["profile"]
                usage = parse_usage(state.get("usage"))
        rows.append({
            "pane": pane.get("pane_id"), "tab": tabs[win][pane.get("tab_id")], "window": windows[win],
            "proc": comm or "?", "profile": profile, "profile_set": bool(m), "cwd": cwd,
            "title": str(pane.get("title", "")), "age": etimes, "active": bool(pane.get("is_active")),
            "agent": comm in AGENT_PROCS, "idle": comm in SHELLS, "usage": usage,
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

    @staticmethod
    def usage_cells(r, p):
        """The tokens and cost cells of a row: what omp reported (an estimated cost leads with `~`),
        a dim `—` for an agent that reported nothing, blank for a pane that is no agent."""
        if not r["agent"]:
            return [("", p["dim"], 0, "end")] * 2
        u = r.get("usage")
        tokens = (format_tokens(u.tokens), p["muted"]) if u and u.tokens is not None else ("—", p["dim"])
        cost = (format_cost(u.cost, u.cost_kind), p["muted"]) if u and u.cost is not None else ("—", p["dim"])
        return [(tokens[0], tokens[1], 0, "end"), (cost[0], cost[1], 0, "end")]

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
        rest = max(w - (tw + 7 + 11 + 11 + 9 + 8 + 10) - 4, 20)
        dw = min(max(rest * 2 // 5, 16), 60)  # directory column: 40% of the free room
        cols = [("tab", tw, ">"), ("pane", 7, ">"), ("process", 11, "<"), ("profile", 11, "<"), ("running", 9, "<"),
                ("tokens", 8, ">"), ("cost", 10, ">"), ("directory", dw + 2, "<"), ("title", min(rest - dw - 2, 62), "<")]
        table = min(w, 2 + sum(c[1] for c in cols))  # the selection slab ends with the table, as in Settings
        # The table is centred on a wide window (its width is capped by the column caps).
        if w >= U.WIDE_COLS:
            x += (w - table) // 2
        pos = x + 2
        for name, width, align in cols:
            cv.put(pos, y, f"{name:{align}{width - 2}}", p["muted"], None, BOLD)
            pos += width
        cv.fill(x, y + 1, table, 1, fg=U.rule_color(p, p["base"]), ch="─")
        totals = usage_totals(r.get("usage") for r in self.rows)
        est = totals["estimated"] is not None
        self.page_rows = body = max(h - (4 + (1 + est if totals["agents"] else 0)), 1)
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
                     *self.usage_cells(r, p),
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
        if totals["agents"]:
            n = totals["agents"]
            cv.put(x + 2, y + 4 + shown, U.fit_text(
                f"Total of {n} agent{'s' if n != 1 else ''} reporting: {totals_text(totals)}", table - 4, "end"), p["muted"])
            if est:
                cv.put(x + 2, y + 5 + shown, U.fit_text("~ estimated from list prices, not a provider's charge", table - 4, "end"), p["dim"])
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


# ---- bots ---------------------------------------------------------------------------
# The Bots panel: scheduled omp agents (bin/hivegrid_schedule.py is the library, bin/hivegrid_scheduler.py
# the daemon that runs them). The page reads the Store, the state directories and the runtime summary,
# and changes things only through the library (Store, update_state, send_command).

BOT_KIND_ICON = {"task": "▪", "agent": "◆"}
BOT_SORT = {"attention": 0, "limit": 0, "working": 1, "done": 2, "schedule": 3, "idle": 4}
# Result words by run status: the one state vocabulary (U.STATES) names how much a result wants the eye.
RUN_STATE = {"ok": "done", "limit": "limit", "failed": "attention", "timeout": "attention", "cost": "attention",
             "missed": "attention", "skipped": "idle", "deferred": "idle", "cancelled": "idle"}
RESULT_COLOR = {"ok": "green", "failed": "red", "timeout": "red", "limit": "red", "cost": "amber", "missed": "amber"}
EDIT_MARK = "--- task.prompt: everything below this line is the prompt ---"
SCHEDULER_UNIT = "hivegrid-scheduler.service"


def span_text(secs):
    """A span as short text: `45m`, `2h05m`, `2h`, `3d`; `now` when it is over."""
    secs = int(secs)
    if secs <= 0:
        return "now"
    if secs < 60:
        return "<1m"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 36000:
        h, m = divmod(secs // 60, 60)
        return f"{h}h{m:02d}m" if m else f"{h}h"
    if secs < 2 * 86400:
        return f"{secs // 3600}h"
    d, h = divmod(secs // 3600, 24)
    return f"{d}d{h}h" if d < 7 and h else f"{d}d"


def clock_text(epoch, tz="local", now=None):
    """A time for a preview: `09:00` today, `Mon 09:00` within a week, else `Nov 3 09:00`."""
    try:
        import zoneinfo
        zone = None if tz in (None, "local") else zoneinfo.ZoneInfo(tz)
    except Exception:  # noqa: BLE001 - an unknown zone reads as local
        zone = None
    t = dt.datetime.fromtimestamp(epoch, zone)
    n = dt.datetime.fromtimestamp(now if now is not None else time.time(), zone)
    days = (t.date() - n.date()).days
    if days == 0:
        return t.strftime("%H:%M")
    if 0 < days < 7:
        return t.strftime("%a %H:%M")
    return f"{t.strftime('%b')} {t.day} {t.strftime('%H:%M')}"


def bot_state(sched, st, runs):
    """The state kind a bot shows (see hivegrid_schedule.build_summary): working while a run is live,
    attention or limit for an unseen bad result or a pause, done for an unseen ok result, idle when
    disabled or finished, else schedule (waiting for its next run)."""
    unseen = [r for r in runs if r.get("seen") is False]
    bad = [r for r in unseen if r.get("status") in HS.ATTENTION_STATUS]
    if st.get("running"):
        return "attention" if isinstance(st["running"], dict) and st["running"].get("waiting") else "working"
    if st.get("paused_reason") or bad:
        return "limit" if not st.get("paused_reason") and bad[-1].get("status") == "limit" else "attention"
    if any(r.get("status") == "ok" for r in unseen):
        return "done"
    if not sched["enabled"] or st.get("done"):
        return "idle"
    return "schedule"


def load_bots(store, env, now):
    """The rows of the list: one per valid schedule, sorted like the summary (needs you, running,
    done, waiting, idle, then by next run and name)."""
    rows = []
    for s in store.read():
        sdir = HS.state_dir(s["id"], env)
        st = HS.load_state(sdir)
        runs = HS.read_runs(sdir)
        rows.append({
            "id": s["id"], "name": s["name"], "kind": s["kind"], "sched": s, "state": bot_state(s, st, runs),
            "enabled": s["enabled"], "when_text": HS.describe(s["when"], now), "next_at": HS.next_run(s, st, now),
            "last": runs[-1] if runs else None, "unseen": sum(1 for r in runs if r.get("seen") is False),
            "running": st.get("running") or None, "paused_reason": st.get("paused_reason"),
            "done": bool(st.get("done")), "run_count": len(runs), "skipped_at": HS.skipped_slot(s, st, now),
        })
    rows.sort(key=lambda r: (BOT_SORT[r["state"]], r["next_at"] or 1 << 60, r["name"].lower()))
    return rows


def session_run(runs):
    """The newest run record that has a session file, or None."""
    return next((r for r in reversed(runs) if r.get("session_file")), None)


def edit_text(rec):
    """The text the editor opens for a bot: its record as JSON, with the (often long) prompt as plain
    text under a marker line."""
    doc = copy.deepcopy(rec)
    prompt = doc["task"]["prompt"]
    doc["task"]["prompt"] = "(see below)"
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n\n" + EDIT_MARK + "\n" + prompt + "\n"


def parse_edit_text(text):
    """The record dict of edit_text()'s format; ValueError with the reason (JSON errors name line and column)."""
    head, sep, tail = text.partition("\n" + EDIT_MARK + "\n")
    try:
        doc, end = json.JSONDecoder().raw_decode(head.lstrip())
    except ValueError as exc:
        raise ValueError(f"not valid JSON: {exc}") from None
    if not isinstance(doc, dict):
        raise ValueError("the record must be a JSON object")
    task = doc.get("task")
    if sep:
        if not isinstance(task, dict):
            raise ValueError("task must be an object")
        task["prompt"] = tail.strip("\n")
    elif head.lstrip()[end:].strip() or (isinstance(task, dict) and task.get("prompt") == "(see below)"):
        raise ValueError(f"the marker line `{EDIT_MARK}` is missing: the prompt would be lost")
    return doc


def scheduler_command(env=None, exists=os.path.exists):
    """(argv, text) that starts the scheduler: `systemctl --user start hivegrid-scheduler` when its user
    unit is installed, else the hivegrid-scheduler program run directly."""
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME")
    base = base if base and os.path.isabs(base) else os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    if exists(os.path.join(base, "systemd", "user", SCHEDULER_UNIT)):
        return ["systemctl", "--user", "start", SCHEDULER_UNIT], "systemctl --user start hivegrid-scheduler"
    here = os.path.join(os.path.dirname(os.path.realpath(__file__)), "hivegrid-scheduler")
    return [here if exists(here) else (shutil.which("hivegrid-scheduler") or "hivegrid-scheduler")], "hivegrid-scheduler"


def start_scheduler(env=None, popen=subprocess.Popen, run=subprocess.run, exists=os.path.exists):
    """Starts the scheduler (see scheduler_command); -> (ok, message). The program form runs detached
    in its own session, so it outlives the panel."""
    argv, text = scheduler_command(env, exists)
    try:
        if argv[0] == "systemctl":
            r = run(argv, capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                return False, f"`{text}` failed: {(r.stderr or r.stdout or 'exit ' + str(r.returncode)).strip()[:120]}"
        else:
            popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  start_new_session=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not start the scheduler (`{text}`): {getattr(exc, 'strerror', None) or exc}"
    return True, f"starting the scheduler (`{text}`): this line turns green when it reports"


def origin_context():
    """(directory with ~, omp profile) of the pane the panel was opened over (--origin), or ("", "")."""
    text = wezterm_cli("list", "--format", "json")
    fid = focused_pane()
    if text is None or fid is None:
        return "", ""
    row = next((r for r in parse_panes(text, foreground_by_tty()) if r["pane"] == fid), None)
    return (row["cwd"], row["profile"]) if row and not row.get("proc") == "web" else ("", "")


class WhenModal(U.PromptModal):
    """The prompt for a `when` text with a live reading under it: what it means and the next 3 times."""

    def __init__(self, title, text, on_ok, tz="local", now=time.time, hint=""):
        super().__init__(title, text, on_ok, hint)
        self.tz, self.now = tz, now

    def reading(self):
        """-> (lines [(text, palette key)]): the parse of the text typed so far."""
        if not self.text.strip():
            return [("weekdays 9am · every 30m · 0 9 * * 1-5 · tomorrow 8:30 · 2026-11-03 09:00", "dim")]
        try:
            now = self.now()
            got = HS.parse_when(self.text, now=now, tz=self.tz)
        except ValueError as exc:
            return [(str(exc), "amber")]
        when = got["when"]
        lines = [(f"→ {HS.describe(when, now)}", "text")]
        lines.append(("next  " + " · ".join(clock_text(e, when.get("tz", "local"), now) for e in got["next"]), "muted"))
        return lines

    def render(self, ui, cv):
        p = ui.pal
        x, y, w, h = U.box(ui, cv, 74, 12, self.title)
        cv.fill(x + 2, y + 2, w - 4, 1, bg=p["deep"])
        cv.put(x + 3, y + 2, self.text[-(w - 8):], p["bright"], p["deep"])
        cv.put(x + 3 + min(len(self.text), w - 8), y + 2, "▏", p["accent"], p["deep"])
        if self.hint:
            cv.put(x + 2, y + 4, U.fit_cells(self.hint, w - 4), p["muted"], p["surface"])
        for k, (line, color) in enumerate(self.reading()):
            cv.put(x + 2, y + 6 + k, U.fit_cells(line, w - 4), p[color], p["surface"])
        if self.error:
            cv.put(x + 2, y + 9, U.fit_cells(self.error, w - 4), p["red"], p["surface"])
        U.dialog_footer(ui, cv, x, y + h - 2, w, [("OK", lambda b: ui.finish(self, self.key(ui, set(), "Enter")), True),
                                                  ("Cancel", lambda b: ui.close_modal(), False)], "Enter OK  Esc cancel")


class HelpModal(U.Modal):
    def __init__(self, title, lines):
        self.title, self.lines = title, lines

    def render(self, ui, cv):
        p = ui.pal
        width = min(max(U.text_width(f"{k}  {t}") for k, t in self.lines) + 8, cv.w - 4)
        x, y, w, h = U.box(ui, cv, width, len(self.lines) + 5, self.title)
        kw = max(U.text_width(k) for k, _ in self.lines)
        for i, (key, text) in enumerate(self.lines):
            cv.put(x + 3, y + 2 + i, key, p["text"], p["surface"], BOLD)
            cv.put(x + 3 + kw + 2, y + 2 + i, U.fit_cells(text, w - kw - 8), p["muted"], p["surface"])
        U.dialog_footer(ui, cv, x, y + h - 2, w, [("Close", lambda b: ui.close_modal(), True)], "any key")

    def key(self, ui, mods, key):
        return True


BOT_HELP = [
    ("n", "new bot (name, kind, task, when, folder, account, permissions, mode)"),
    ("b", "Briefing: the important reports of all bots (Enter reads one, a marks all seen)"),
    ("t", "Talk: the bot's conversation, live; type and Enter to send, Esc back"),
    ("Enter", "details: the run history; marks the results seen"),
    ("e", "edit the bot's record and prompt in $EDITOR (checked before it is saved)"),
    ("m", "show / edit the bot's memory.md in $EDITOR"),
    ("space", "pause or resume (a breaker pause is cleared by resume)"),
    ("r", "run now"),
    ("s", "skip the next scheduled run; pressed again while it is still ahead, bring it back"),
    ("c", "cancel the live run"),
    ("d", "delete the bot and its history"),
    ("↑↓", "choose;  Esc goes back, q closes"),
    ("", "▪ task  ◆ agent;  ● an unseen result"),
]


class BotWizard:
    """The new-bot dialogs, chained: name, kind, task, when, folder, account, permissions, mode. Esc in
    any of them drops the bot; nothing is written before the last answer."""

    KINDS = ["task – runs its prompt on schedule", "agent – standing task, identity, memory"]
    PERMS = [("edit", "edit – read and write files (default)"), ("readonly", "readonly – read and search only"),
             ("full", "full – also shell commands (opt-in)")]
    MODES = [("headless", "headless – in the background (default)"),
             ("visible", "visible – an interactive agent in the Bots window, you can watch and answer"),
             ("hybrid", "hybrid – headless; if it fails, the session opens in the Bots window for you")]

    def __init__(self, page, ui, cwd="", profile=""):
        self.page, self.ui = page, ui
        self.cwd, self.profile = cwd, profile
        self.spec = {"enabled": True, "task": {}, "run": {}}
        self.accounts = []

    def ask(self, modal):
        self.ui.open_modal(modal)

    def start(self):
        self.ask(U.PromptModal("New bot · name", "", self.on_name, hint="A short label, for example: morning digest"))

    def on_name(self, text):
        name = text.strip()
        if not name:
            return "give the bot a name"
        if len(name) > HS.MAX_NAME:
            return f"at most {HS.MAX_NAME} characters"
        self.spec["name"] = name
        self.ask(U.ChoiceModal("New bot · kind", self.KINDS, 0, self.on_kind))

    def on_kind(self, i):
        self.spec["kind"] = ("task", "agent")[i]
        agent = i == 1
        self.ask(U.PromptModal("New bot · " + ("standing task" if agent else "task"), "", self.on_prompt,
                               hint="What it does on every run (long prompts: edit later with e)"))

    def on_prompt(self, text):
        if not text.strip():
            return "write what the bot should do"
        if len(text) > HS.MAX_PROMPT:
            return f"at most {HS.MAX_PROMPT} characters"
        self.spec["task"]["prompt"] = text.strip()
        if self.spec["kind"] == "agent":
            self.ask(U.PromptModal("New bot · identity", "", self.on_identity,
                                   hint="Who is this agent? Optional: Enter to skip"))
        else:
            self.ask_when()

    def on_identity(self, text):
        if len(text) > 8000:
            return "at most 8000 characters"
        if text.strip():
            self.spec["identity"] = {"system": text.strip()}
        self.ask_when()

    def ask_when(self):
        self.ask(WhenModal("New bot · when", "", self.on_when, tz=self.page.cfg()["tz"], now=self.page.now,
                           hint="A phrase, a cron expression or a date"))

    def on_when(self, text):
        try:
            got = HS.parse_when(text, now=self.page.now(), tz=self.page.cfg()["tz"])
        except ValueError as exc:
            return str(exc)
        self.spec["when"] = got["when"]
        self.ask(U.PromptModal("New bot · folder", self.cwd or "~", self.on_cwd,
                               hint="The folder it works in (the focused pane's by default)"))

    def on_cwd(self, text):
        path = text.strip()
        full = os.path.expanduser(path)
        if not path or not os.path.isabs(full):
            return "an absolute path, or one starting with ~"
        if not os.path.isdir(full):
            return "that folder does not exist"
        self.spec["run"]["cwd"] = path
        try:
            self.accounts = [a["label"] for a in self.page.model.settings.accounts()]
        except Exception:  # noqa: BLE001 - accounts are optional context
            self.accounts = []
        current = next((i + 1 for i, label in enumerate(self.accounts) if label == self.profile), 0)
        self.ask(U.ChoiceModal("New bot · account", ["default account"] + [fit(a, 36) for a in self.accounts],
                               current, self.on_account))

    def on_account(self, i):
        if i > 0:
            self.spec["run"]["account"] = self.accounts[i - 1]
        self.ask_permissions()

    def ask_permissions(self):
        self.ask(U.ChoiceModal("New bot · permissions", [t for _, t in self.PERMS], 0, self.on_permissions))

    def on_permissions(self, i):
        perm = self.PERMS[i][0]
        if perm == "full":
            if not self.page.cfg()["allow_full"]:
                self.ui.say("full needs \"schedule\": {\"allow_full\": true} in settings.json", True)
                self.ask_permissions()
                return
            self.ask(U.ConfirmModal("Full permissions let the bot run any shell command without asking, with no one "
                                    "watching. Continue?", self.ask_time, "Full permissions", yes="Allow full",
                                    no="Back", on_no=self.ask_permissions))
            return
        self.spec["run"]["permissions"] = perm
        self.ask_mode()

    def ask_time(self):
        self.spec["run"]["permissions"] = "full"
        self.ask(U.PromptModal("New bot · longest run", "30m", self.on_time,
                               hint="Full needs a limit: 30m, 2h (1 minute to 24 hours)"))

    def on_time(self, text):
        try:
            secs = HS.parse_duration(text)
        except ValueError as exc:
            return str(exc)
        if not 60 <= secs <= 86400:
            return "between 1 minute and 24 hours"
        self.spec["run"]["max_time_s"] = secs
        self.ask(U.PromptModal("New bot · most it may spend", "2", self.on_cost,
                               hint="US dollars per run, above 0 and at most 100"))

    def on_cost(self, text):
        try:
            cost = float(text.strip().lstrip("$"))
        except ValueError:
            return "a number of dollars"
        if not 0 < cost <= 100:
            return "above 0 and at most 100"
        self.spec["run"]["max_cost_usd"] = cost
        self.ask_mode()

    def ask_mode(self):
        self.ask(U.ChoiceModal("New bot · mode", [t for _, t in self.MODES], 0, self.on_mode))

    def on_mode(self, i):
        self.spec["run"]["mode"] = self.MODES[i][0]
        self.page.create(self.ui, self.spec)


class BotsPage(U.Page):
    title = "Bots"

    def __init__(self, model, env=None, now=time.time, select=None, new=False, cwd=None, profile=None,
                 editor=None, start=None):
        self.model, self.env, self.now = model, env, now
        self.store = HS.Store(env=env)
        self.select, self.new, self.cwd, self.profile = select, new, cwd, profile
        self.editor = editor or (lambda argv: subprocess.run(argv))  # tests replace both
        self.start = start or (lambda: start_scheduler(env))
        self.rows, self.cursor, self.top, self.page_rows = [], 0, 0, 10
        self.error, self.errors = "", []
        self.alive, self.doc = False, None
        self.detail = None  # id of the bot whose runs are shown
        self.runs, self.run_cursor, self.fresh = [], 0, set()
        self.mode = "list"  # "list" (and detail), "briefing" or "talk"
        self.brief, self.brief_cursor, self.brief_open = [], 0, False
        self.talk_id, self.draft, self.scroll, self.talk_note = None, "", 0, ("", False)
        self.chat = RD.ChatCache(chat_text, chat_line)
        self._stamp, self._talk_at = None, 0.0
        self._at = 0.0
        self._shown = False

    # data ---------------------------------------------------------------------
    def cfg(self):
        try:
            return self.model.settings.schedule()
        except Exception:  # noqa: BLE001 - settings are context: defaults when unreadable
            return dict(SCHEDULE_DEFAULTS)

    def refresh(self):
        now = self.now()
        current = self.current()["id"] if self.current() else None
        try:
            self.rows, self.error = load_bots(self.store, self.env, now), ""
        except (HS.ScheduleError, OSError) as exc:
            self.rows, self.error = [], f"The bots could not be read: {exc}"
        self.errors = list(self.store.errors)
        self.doc = HS.read_summary(HS.summary_path(self.env))
        self.alive = HS.daemon_alive(self.env, now)
        self.cursor = next((i for i, r in enumerate(self.rows) if r["id"] == current), self.cursor)
        self.cursor = max(0, min(self.cursor, len(self.rows) - 1))
        if self.detail:
            self.load_runs()
        try:
            self.brief = HS.briefing(self.env)
        except (HS.ScheduleError, OSError):
            self.brief = []
        self.brief_cursor = max(0, min(self.brief_cursor, len(self.brief) - 1))
        if self.mode == "talk" and not self.row(self.talk_id):
            self.mode = "list"

    def load_runs(self):
        row = self.row(self.detail)
        if row is None:
            self.detail, self.runs = None, []
            return
        self.runs = list(reversed(HS.read_runs(HS.state_dir(self.detail, self.env))))
        self.run_cursor = max(0, min(self.run_cursor, len(self.runs) - 1))

    def current(self):
        return self.rows[self.cursor] if 0 <= self.cursor < len(self.rows) else None

    def row(self, sid):
        return next((r for r in self.rows if r["id"] == sid), None)

    def on_show(self, ui):
        self._at = self.now()
        self.refresh()
        if self.select:
            i = next((i for i, r in enumerate(self.rows) if r["id"] == self.select), None)
            if i is not None:
                self.cursor = i
            self.select = None
        if self.new and not self._shown:
            self._shown = True
            cwd, profile = (self.cwd or "", self.profile or "")
            if not cwd:
                cwd, found = origin_context()
                profile = profile or found
            BotWizard(self, ui, cwd, profile).start()
        self._shown = True

    def tick(self, ui):
        now = self.now()
        if now - self._at >= 2:
            self._at = now
            self.refresh()
            return True
        if self.mode == "talk":
            row = self.row(self.talk_id)
            live = bool(row and row["state"] == "working")
            if live and now - self._talk_at >= 1 or (not live and self.talk_stamp() != self._stamp):
                self._talk_at = now
                return True
            return False
        return any(r["state"] == "working" for r in self.rows)

    def hints(self):
        if self.mode == "talk":
            return "↵ send  PgUp/PgDn scroll  Esc back"
        if self.mode == "briefing":
            return "↑↓ report  ↵ read  a all seen  Esc back  ? help  q close"
        if self.detail:
            return "↑↓ run  v report  r run now  Esc back  ? help  q close"
        return "n new  ↵ details  space pause  r run  e edit  m memory  s skip/undo  d delete  ? help  q close"

    def captures(self, mods, key):
        if mods and mods != {"SHIFT"}:
            return False
        if self.mode == "talk":
            return True
        if self.mode == "briefing" and key in ("Escape", "Backspace", "a", "Enter", "b"):
            return True
        if self.detail and key in ("Escape", "Backspace"):
            return True
        return key in ("n", "e", "r", "s", "d", "m", "c", "v", "b", "t", "?", "Enter", "Space")

    # commands -----------------------------------------------------------------
    def tell(self, op, sid):
        """Sends `op` for bot `sid` to a live scheduler; False when none is running."""
        if not self.alive:
            return False
        try:
            HS.send_command(HS.command_dir(self.env), {"op": op} if sid is None else {"op": op, "id": sid})
        except (OSError, ValueError):
            return False
        return True

    def state_dir(self, row):
        return HS.init_state_dir(row["sched"], self.now(), self.env)

    def create(self, ui, spec):
        try:
            rec = self.store.add(spec, now=int(self.now()), origin="panel")
            HS.init_state_dir(rec, self.now(), self.env)
        except (ValueError, OSError) as exc:
            ui.say(f"could not create the bot: {exc}", True)
            return
        self.tell("reload", None)
        self.refresh()
        self.cursor = next((i for i, r in enumerate(self.rows) if r["id"] == rec["id"]), self.cursor)
        ui.say(f"created {rec['name']}: {HS.describe(rec['when'], self.now())}")

    def toggle_pause(self, ui, row):
        sid = row["id"]
        resume = not row["enabled"] or bool(row["paused_reason"]) or row["done"]
        try:
            new = self.store.change(sid, {"enabled": resume}, now=int(self.now()))
            if resume:
                sdir = HS.init_state_dir(new, self.now(), self.env)

                def fn(st):
                    st["paused_reason"], st["consec_failures"], st["done"] = None, 0, False
                    st["baseline"] = int(self.now())
                HS.update_state(sdir, fn)
        except (ValueError, KeyError, OSError) as exc:
            ui.say(f"could not change {row['name']}: {exc}", True)
            return
        self.tell("reload", None)
        self.refresh()
        ui.say(f"{'resumed' if resume else 'paused'} {row['name']}")

    def run_now(self, ui, row):
        if self.tell("run-now", row["id"]):
            ui.say(f"asked the scheduler to run {row['name']} now")
            return

        def mark():
            HS.update_state(self.state_dir(row), lambda st: st.update(run_now=True))

        def mark_only():
            mark()
            ui.say(f"{row['name']} runs as soon as the scheduler starts (not running now)")

        def start_and_mark():
            mark()
            ok, message = self.start()
            ui.say(message if ok else f"{message}; {row['name']} will run when the scheduler starts", not ok)

        ui.open_modal(U.ConfirmModal(f"The scheduler is not running. Start it now and run {row['name']}?",
                                     start_and_mark, "Run now", yes="Start scheduler", no="Run when it starts",
                                     on_no=mark_only))

    def skip_next(self, ui, row):
        """`s`: skips the next run; pressed again while that skip is still ahead it brings the run back."""
        if row.get("skipped_at"):
            if self.tell("unskip", row["id"]):
                ui.say(f"asked the scheduler to bring back the skipped run of {row['name']}")
                return
            sched = row["sched"]
            st = HS.update_state(self.state_dir(row), lambda s: HS.unskip(sched, s, self.now()))
            self.refresh()
            nxt = HS.next_run(sched, st, self.now())
            ui.say(f"skip undone; next {clock_text(nxt, sched['when'].get('tz', 'local'), self.now()) if nxt else 'none'}")
            return
        if self.tell("skip", row["id"]):
            ui.say(f"asked the scheduler to skip the next run of {row['name']} (s again undoes it)")
            return
        sched = row["sched"]
        st = HS.update_state(self.state_dir(row), lambda s: HS.skip_next(sched, s, self.now()))
        self.refresh()
        nxt = HS.next_run(sched, st, self.now())
        ui.say(f"skipped; next {clock_text(nxt, sched['when'].get('tz', 'local'), self.now()) if nxt else 'none'} (s again undoes it)")

    def cancel_run(self, ui, row):
        if not row["running"]:
            ui.say(f"{row['name']} is not running", True)
        elif self.tell("cancel", row["id"]):
            ui.say(f"asked the scheduler to cancel the run of {row['name']}")
        else:
            ui.say("the scheduler is not running: it can not cancel a run", True)

    def delete(self, ui, row):
        def go():
            try:
                self.store.remove(row["id"])
                sdir = HS.state_dir(row["id"], self.env)
                if os.path.isdir(sdir) and not os.path.islink(sdir):
                    shutil.rmtree(sdir, ignore_errors=True)
            except (OSError, ValueError) as exc:
                ui.say(f"could not delete {row['name']}: {exc}", True)
                return
            self.tell("reload", None)
            self.detail = None
            self.refresh()
            ui.say(f"deleted {row['name']}")
        ui.open_modal(U.ConfirmModal(f"Delete {row['name']} and its run history?", go, "Delete bot"))

    def view_report(self, ui, run):
        path = run.get("report_path") if run else None
        if not path or not os.path.isfile(path):
            ui.say("that run left no report", True)
            return
        pager = os.environ.get("PAGER") or "less"
        ui.suspend()
        try:
            self.editor(shlex.split(pager) + [path])
        except (OSError, ValueError) as exc:
            ui.say(f"could not run the pager: {exc}", True)
        finally:
            ui.resume()

    # editing ------------------------------------------------------------------
    def run_editor(self, ui, path):
        """Runs $VISUAL / $EDITOR on `path` with the terminal handed over; -> an error text or None."""
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or next((c for c in ("nano", "vi") if shutil.which(c)), "")
        if not editor:
            return "No editor found: set $EDITOR."
        try:
            argv = shlex.split(editor) + [path]
        except ValueError as exc:
            return f"$EDITOR does not parse: {exc}"
        ui.suspend()
        try:
            r = self.editor(argv)
        except OSError as exc:
            return f"could not run {argv[0]}: {exc.strerror or exc}"
        finally:
            ui.resume()
        code = getattr(r, "returncode", 0)
        return f"{argv[0]} exited with status {code}: nothing was changed" if code else None

    def edit(self, ui, row):
        tmp = tempfile.mkdtemp(prefix="hivegrid-bot-")
        path = os.path.join(tmp, f"{row['id']}.json.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(edit_text(row["sched"]))
        self.edit_again(ui, row, tmp, path)

    def edit_again(self, ui, row, tmp, path):
        done = lambda: shutil.rmtree(tmp, ignore_errors=True)  # noqa: E731
        err = self.run_editor(ui, path)
        if err:
            done()
            ui.say(err, True)
            return
        try:
            with open(path, encoding="utf-8") as f:
                new = f.read()
            if new == edit_text(row["sched"]):
                done()
                ui.say("no changes")
                return
            self.apply(row, parse_edit_text(new))
        except (ValueError, KeyError, OSError) as exc:
            ui.open_modal(U.ConfirmModal(f"Not saved: {exc.args[0] if isinstance(exc, KeyError) else exc}", lambda: self.edit_again(ui, row, tmp, path),
                                         "Invalid bot", yes="Edit again", no="Discard", on_no=done))
            return
        done()
        self.refresh()
        ui.say(f"saved {row['name']}")

    def apply(self, row, doc):
        """Replaces the record of `row` by `doc` (ValueError with the reason when it does not validate)."""
        sid, now = row["id"], int(self.now())
        perms = doc.get("run", {}).get("permissions") if isinstance(doc.get("run"), dict) else None
        if perms == "full" and row["sched"]["run"]["permissions"] != "full" and not self.cfg()["allow_full"]:
            raise ValueError("permissions full needs \"schedule\": {\"allow_full\": true} in settings.json")
        if doc.get("id") != sid:
            raise ValueError("the id cannot change")

        def fn(recs):
            for i, r in enumerate(recs):
                if r["id"] == sid:
                    recs[i] = HS.validate(dict(doc, created=r["created"], updated=now))
                    return copy.deepcopy(recs[i])
            raise KeyError(f"{row['name']} is gone")
        new = self.store.mutate(fn)
        HS.init_state_dir(new, now, self.env)
        HS.sync_identity(new, self.env)
        self.tell("reload", None)

    def edit_memory(self, ui, row):
        sched = row["sched"]
        path = HS.memory_path(row["id"], self.env)
        if not sched["identity"]["memory"] and not os.path.exists(path):
            ui.say(f"{row['name']} has no memory: agents always have one, a task only with identity.memory", True)
            return
        self.state_dir(row)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                before = f.read()
        except FileNotFoundError:
            before = ""
        except OSError as exc:
            ui.say(f"could not read memory.md: {exc.strerror or exc}", True)
            return
        tmp = tempfile.mkdtemp(prefix="hivegrid-bot-")
        try:
            copy_path = os.path.join(tmp, "memory.md")
            with open(copy_path, "w", encoding="utf-8") as f:
                f.write(before)
            err = self.run_editor(ui, copy_path)
            if err:
                ui.say(err, True)
                return
            with open(copy_path, encoding="utf-8", errors="replace") as f:
                after = f.read()
            if after == before:
                ui.say("memory unchanged")
                return
            HS.atomic_write(path, after)
            cut = HS.enforce_memory_cap(sched, self.env)
            cap = sched["identity"]["memory_max_bytes"]
            ui.say(f"memory saved" + (f"; cut to its cap of {cap} bytes (the oldest lines went)" if cut else ""))
        except OSError as exc:
            ui.say(f"could not save memory.md: {exc.strerror or exc}", True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # events -------------------------------------------------------------------
    def open_detail(self, ui, row=None):
        row = row or self.current()
        if row is None:
            return
        self.detail, self.run_cursor = row["id"], 0
        self.load_runs()
        self.fresh = {r.get("run") for r in self.runs if r.get("seen") is False}
        if self.fresh:
            HS.mark_seen(row["id"], env=self.env)
            self.refresh()

    def move(self, step):
        if self.mode == "briefing":
            self.brief_cursor = max(0, min(len(self.brief) - 1, self.brief_cursor + step))
        elif self.mode == "talk":
            self.scroll = max(0, self.scroll - step * 3)
        elif self.detail:
            self.run_cursor = max(0, min(len(self.runs) - 1, self.run_cursor + step))
        else:
            self.cursor = max(0, min(len(self.rows) - 1, self.cursor + step))

    def wheel(self, ui, dy):
        self.move(dy)

    def key(self, ui, mods, key):
        steps = {"UpArrow": -1, "DownArrow": 1, "PageUp": -self.page_rows, "PageDown": self.page_rows,
                 "Home": -10 ** 6, "End": 10 ** 6}
        row = self.row(self.detail) if self.detail else self.current()
        if self.mode == "talk":
            return self.talk_key(ui, mods, key)
        if self.mode == "briefing" and self.briefing_key(ui, key):
            return
        if key == "b":
            self.open_briefing()
            return
        if key == "t" and row:
            self.open_talk(row["id"])
            return
        if key in steps:
            self.move(steps[key])
        elif key == "n":
            cwd, profile = (self.cwd or "", self.profile or "")
            if not cwd:
                cwd, found = origin_context()
                profile = profile or found
            BotWizard(self, ui, cwd, profile).start()
        elif key == "?":
            ui.open_modal(HelpModal("Bots", BOT_HELP))
        elif key in ("Escape", "Backspace") and self.detail:
            self.detail = None
        elif row is None:
            return
        elif key == "Enter" and not self.detail:
            self.open_detail(ui)
        elif key == "e":
            self.edit(ui, row)
        elif key == "m":
            self.edit_memory(ui, row)
        elif key == "Space":
            self.toggle_pause(ui, row)
        elif key == "r":
            self.run_now(ui, row)
        elif key == "s":
            self.skip_next(ui, row)
        elif key == "c":
            self.cancel_run(ui, row)
        elif key == "d":
            self.delete(ui, row)
        elif key == "v" and self.detail and self.runs:
            self.view_report(ui, self.runs[self.run_cursor])

    # drawing ------------------------------------------------------------------
    def status_cells(self, p, now):
        """The scheduler status as [(text, colour)] segments."""
        if self.alive:
            hb = (self.doc or {}).get("scheduler", {}).get("heartbeat")
            age = f" · heartbeat {span_text(now - hb)} ago" if isinstance(hb, (int, float)) and now - hb >= 60 else ""
            return [("● Scheduler running", p["green"]), (age, p["dim"])]
        return [("○ Scheduler not running", p["amber"]), (" · start it: ", p["muted"]),
                (scheduler_command(self.env)[1], p["text"])]

    def columns(self, inner):
        """[(key, width)] of the columns beside the name that fit `inner` cells: the least useful go first."""
        cols = [("kind", 9), ("when", 19), ("next", 16), ("last", 17), ("account", 10), ("access", 10), ("mode", 10)]
        for drop in ("mode", "access", "account", "kind", "when"):
            if sum(w for _, w in cols) + 18 <= inner:
                break
            cols = [c for c in cols if c[0] != drop]
        return cols

    def cells(self, ui, r, now):
        """The text cell of every column of row r: {key: (text, colour, attrs)}."""
        p = ui.pal
        sched, last = r["sched"], r["last"]
        if r["running"]:
            nxt = (f"running {human_age(now - (r['running'].get('started') or now))}", p["accent"])
        elif r["paused_reason"]:
            nxt = ("paused", p["amber"])
        elif not r["enabled"] or r["done"] or r["next_at"] is None:
            nxt = ("finished" if r["done"] else "off" if not r["enabled"] else "—", p["dim"])
        else:
            nxt = ("in " + span_text(r["next_at"] - now), p["text"])
        if last:
            status = last.get("status") or "?"
            age = span_text(now - (last.get("ended") or last.get("started") or now))
            color = p[RESULT_COLOR.get(status, "dim")]
            unseen = last.get("seen") is False
            res = (("● " if unseen else "") + f"{status} {age}", color if unseen else U.mix(color, p["dim"], 0.55), BOLD if unseen else 0)
        else:
            res = ("no run yet", p["dim"], 0)
        perm = sched["run"]["permissions"]
        return {
            "kind": (f"{BOT_KIND_ICON[r['kind']]} {r['kind']}", p["text"] if r["kind"] == "agent" else p["muted"], 0),
            "when": (r["when_text"], p["muted"], 0), "next": (*nxt, 0), "last": res,
            "account": (sched["run"]["account"] or "default", p["muted"] if sched["run"]["account"] else p["dim"], 0),
            "access": (perm, p["red"] if perm == "full" else p["muted"], BOLD if perm == "full" else 0),
            "mode": (sched["run"]["mode"], p["dim"], 0),
        }

    def render(self, ui, cv, x, y, w, h):
        p = ui.pal
        now = self.now()
        col = None
        if w >= U.WIDE_COLS:
            ox, tw = U.content_box(w, 130)
            x, w, col = x + ox, tw, (x + ox, tw)
        # scheduler status and the buttons on the first row
        right = x + w - 1
        unseen = sum(1 for e in self.brief if not e["seen"])
        chips = [("Briefing" + (f" ● {unseen}" if unseen else ""), lambda b: self.open_briefing(), "button"),
                 ("New", lambda b: self.key(ui, set(), "n"), "primary")]
        if not self.alive:
            chips.insert(0, ("Start", lambda b: self.start_now(ui), "button"))
        for label, fn, kind in reversed(chips):
            right -= U.chip_width(label)
            U.chip(cv, p, right, y, label, kind=kind, bg=p["base"], hit=fn)
            right -= 1
        pos = x + 2
        for text, colour in self.status_cells(p, now):
            room = right - pos - 1
            if room <= 0:
                break
            pos += cv.put(pos, y, U.fit_cells(text, room), colour)
        if self.mode == "talk" and self.row(self.talk_id):
            self.render_talk(ui, cv, x, y + 2, w, h - 2, now)
            return col
        if self.mode == "briefing":
            self.render_briefing(ui, cv, x, y + 2, w, h - 2, now)
            return col
        if self.detail and self.row(self.detail):
            self.render_detail(ui, cv, x, y + 2, w, h - 2, now)
            return col
        if self.error:
            U.empty_state(cv, p, x + 2, y + 2, w - 4, self.error)
            return col
        if not self.rows:
            U.empty_state(cv, p, x + 2, y + 2, w - 4, "No bots yet.",
                          "A bot runs an omp agent on a schedule in the background and reports here. Press n to "
                          "create one, or use hivegrid-schedule add on the command line.")
            self.render_notes(cv, p, x, y + 6, w)
            return col
        inner = w - 4 - 1
        cols = self.columns(inner)
        name_w = inner - sum(wd for _, wd in cols)
        hy = y + 2
        pos = x + 4
        cv.put(pos, hy, "name".ljust(name_w - 2), p["muted"], None, BOLD)
        pos += name_w
        heads = {"kind": "kind", "when": "when", "next": "next", "last": "last result", "account": "account",
                 "access": "access", "mode": "mode"}
        for key, wd in cols:
            cv.put(pos, hy, heads[key], p["muted"], None, BOLD)
            pos += wd
        cv.fill(x, hy + 1, w, 1, fg=U.rule_color(p, p["base"]), ch="─")
        self.page_rows = body = max(h - 7, 1)
        self.cursor = max(0, min(self.cursor, len(self.rows) - 1))
        self.top = min(max(self.top, self.cursor - body + 1), self.cursor, max(len(self.rows) - body, 0))
        for i in range(self.top, min(self.top + body, len(self.rows))):
            r = self.rows[i]
            row = hy + 2 + i - self.top
            sel = i == self.cursor
            glyph, _, gcolor = U.state_style(p, r["state"], now if r["state"] == "working" else None)
            bg = U.list_row(cv, p, row, x=x, w=w, selected=sel, bg=p["base"], glyph=glyph, glyph_color=gcolor,
                            hit=lambda b, i=i: (setattr(self, "cursor", i), self.open_detail(ui))).bg
            name_color = p["bright"] if sel or r["unseen"] else p["text"]
            cv.put(x + 4, row, U.fit_text(r["name"], name_w - 2), name_color, bg, BOLD if sel or r["unseen"] else 0)
            cells = self.cells(ui, r, now)
            pos = x + 4 + name_w
            for key, wd in cols:
                text, colour, attrs = cells[key]
                cv.put(pos, row, U.fit_text(text, wd - 2), colour, bg, attrs)
                pos += wd
        shown = min(body, len(self.rows) - self.top)
        self.render_notes(cv, p, x, hy + 2 + shown + 1, w)
        return col

    def render_notes(self, cv, p, x, y, w):
        n, running = len(self.rows), sum(1 for r in self.rows if r["state"] == "working")
        unseen = sum(r["unseen"] for r in self.rows)
        line = f"{n} bot{'s' if n != 1 else ''} · {running} running" + (f" · {unseen} unseen result{'s' if unseen != 1 else ''}" if unseen else "")
        cv.put(x + 2, y, U.fit_cells(line, w - 4), p["dim"])
        if self.errors:
            sid, msg = self.errors[0]
            more = f" (+{len(self.errors) - 1} more)" if len(self.errors) > 1 else ""
            cv.put(x + 2, y + 1, U.fit_cells(f"{len(self.errors)} schedule{'s' if len(self.errors) != 1 else ''} skipped: {sid}: {msg}{more}", w - 4), p["amber"])

    # briefing -----------------------------------------------------------------
    def open_briefing(self):
        self.mode, self.brief_open = "briefing", False
        self.refresh()

    def briefing_key(self, ui, key):
        """Keys of the Briefing page; True when handled."""
        entry = self.brief[self.brief_cursor] if self.brief else None
        if key in ("Escape", "Backspace", "b"):
            self.mode = "list"
        elif key == "a":
            n = HS.mark_briefing_seen(self.env)
            self.refresh()
            ui.say(f"marked {n} report{'s' if n != 1 else ''} seen" if n else "nothing new")
        elif key == "Enter" and entry:
            self.brief_open = not self.brief_open
            if self.brief_open and not entry["seen"]:
                HS.mark_seen(entry["bot_id"], entry["run"], env=self.env)
                self.refresh()
        else:
            return False
        return True

    def report_text(self, entry):
        """The report file of a Briefing entry (the first 4000 characters), else its headline."""
        try:
            with open(HS.report_path(entry["bot_id"], entry["run"], self.env), encoding="utf-8", errors="replace") as f:
                text = f.read(4000)
        except OSError:
            return entry["headline"]
        return chat_text(text, 4000).strip() or entry["headline"]

    def render_briefing(self, ui, cv, x, y, w, h, now):
        p = ui.pal
        U.chip(cv, p, x + 1, y, "‹ Back", kind="button", bg=p["base"], hit=lambda b: setattr(self, "mode", "list"))
        unseen = sum(1 for e in self.brief if not e["seen"])
        pos = x + 1 + U.chip_width("‹ Back") + 2
        pos += cv.put(pos, y, "Briefing", p["bright"], None, BOLD)
        cv.put(pos + 2, y, f"{unseen} new" if unseen else "nothing new", p["accent"] if unseen else p["dim"])
        if unseen:
            U.chip(cv, p, x + w - 1 - U.chip_width("Mark all seen"), y, "Mark all seen", kind="button", bg=p["base"],
                   hit=lambda b: self.briefing_key(ui, "a"))
        cv.fill(x, y + 1, w, 1, fg=U.rule_color(p, p["base"]), ch="─")
        if not self.brief:
            U.empty_state(cv, p, x + 2, y + 3, w - 4, "Nothing important.",
                          "Only what you should know or decide shows here. Quiet runs stay in each bot's own history.")
            return
        open_h = min(7, max(h - 8, 0)) if self.brief_open else 0
        rows = max((h - 3 - open_h) // 2, 1)
        self.page_rows = rows
        top = min(max(self.brief_cursor - rows + 1, 0), max(len(self.brief) - rows, 0))
        for k in range(top, min(top + rows, len(self.brief))):
            e = self.brief[k]
            fresh = not e["seen"]
            glyph, _, color = U.state_style(p, RUN_STATE.get(e["status"], "done"))
            age = span_text(now - (e.get("ended") or now))
            U.list_row(cv, p, y + 2 + (k - top) * 2, x=x, w=w, lines=2, selected=k == self.brief_cursor, bg=p["base"],
                       glyph="●" if fresh else glyph, glyph_color=p["accent"] if fresh else color,
                       name=e["headline"], name_color=p["bright"] if fresh else p["text"], name_attrs=BOLD if fresh else None,
                       sub=f"{e['bot_name']} · {e['status']} · {age} ago",
                       hit=lambda b, k=k: (setattr(self, "brief_cursor", k), self.briefing_key(ui, "Enter")))
        if open_h:
            by = y + 2 + min(rows, len(self.brief)) * 2
            cv.fill(x, by, w, 1, fg=U.rule_color(p, p["base"]), ch="─")
            lines = []
            for para in self.report_text(self.brief[self.brief_cursor]).splitlines():
                lines += textwrap.wrap(para, max(w - 4, 10)) or [""]
            for j, line in enumerate(lines[:open_h - 1]):
                cv.put(x + 2, by + 1 + j, line, p["text"])

    # talk ---------------------------------------------------------------------
    def open_talk(self, sid):
        self.mode, self.talk_id, self.draft, self.scroll, self.talk_note = "talk", sid, "", 0, ("", False)
        self.detail = None
        self._stamp = None

    def talk_stamp(self):
        sf = HS.conversation(self.talk_id, self.env)["session_file"] if self.talk_id else None
        return self.chat.stamp(sf) if sf else None

    def talk_key(self, ui, mods, key):
        if key == "Escape":
            self.mode = "list"
        elif key == "Enter":
            self.send(ui)
        elif key == "Backspace":
            self.draft = self.draft[:-1]
        elif key == "PageUp":
            self.scroll += max(self.page_rows, 5)
        elif key == "PageDown":
            self.scroll = max(self.scroll - max(self.page_rows, 5), 0)
        elif key == "Space" and not mods:
            self.draft += " "
        elif len(key) == 1 and mods <= {"SHIFT"} and len(self.draft) < HS.CHAT_MAX:
            self.draft += key.upper() if "SHIFT" in mods else key

    def send(self, ui):
        text = self.draft.strip()
        if not text:
            return
        try:
            got = HS.say(self.talk_id, text, env=self.env, now=self.now())
        except HS.ScheduleError as exc:
            code = getattr(exc, "code", None)
            if code == "no_daemon":
                self.talk_note = (f"scheduler not running: {scheduler_command(self.env)[1]}", True)
            elif code == "queue_full":
                self.talk_note = ("queue_full: 5 messages already wait for this bot", True)
            else:
                self.talk_note = (str(exc), True)
            return
        except ValueError as exc:
            self.talk_note = (str(exc), True)
            return
        self.draft, self.scroll = "", 0
        self.talk_note = (f"queued #{got['position']}: it runs when the bot is free" if got["queued"] else "sent", False)

    def transcript(self, path, width):
        """[(text, palette key, attrs)] lines of the session's chat (the shared parser), tool calls one line each."""
        got = self.chat.window(path, 200) if path else None
        out = []
        for it in got[0] if got else []:
            kind, role, text = it["kind"], it["role"], it["text"] or ""
            if kind == "tool":
                tool = it["tool"] or {}
                mark = {"error": "red", "running": "accent"}.get(tool.get("status"), "dim")
                out.append((U.fit_cells(f"▸ {tool.get('name', 'tool')} {text}".rstrip(), width), mark, 0))
            elif kind == "text" and role in ("user", "assistant"):
                lead = "› " if role == "user" else "  "
                for para in text.splitlines() or [""]:
                    for j, line in enumerate(textwrap.wrap(para, max(width - 2, 10)) or [""]):
                        out.append(((lead if j == 0 else "  ") + line, "accent" if role == "user" else "text",
                                    BOLD if role == "user" else 0))
                out.append(("", "text", 0))
            elif kind in ("limit", "note"):
                out.append((U.fit_cells(f"· {text}", width), "amber" if kind == "limit" else "dim", 0))
        return out

    def render_talk(self, ui, cv, x, y, w, h, now):
        p = ui.pal
        r = self.row(self.talk_id)
        info = HS.conversation(self.talk_id, self.env)
        U.chip(cv, p, x + 1, y, "‹ Back", kind="button", bg=p["base"], hit=lambda b: setattr(self, "mode", "list"))
        glyph, label, gcolor = U.state_style(p, r["state"], now if r["state"] == "working" else None)
        pos = x + 1 + U.chip_width("‹ Back") + 2
        pos += cv.put(pos, y, f"{BOT_KIND_ICON[r['kind']]} ", p["muted"])
        pos += cv.put(pos, y, U.fit_cells(r["name"], max(w - pos - 24, 8)), p["bright"], None, BOLD)
        cv.put(pos + 2, y, f"{glyph} {label}", gcolor)
        live = f"a {info['activity']} is live" if info["activity"] else "idle"
        cv.put(x + 2, y + 1, U.fit_cells(f"Talk · {live}" + (f" · {info['queue']} waiting" if info["queue"] else ""), w - 4), p["muted"])
        cv.fill(x, y + 2, w, 1, fg=U.rule_color(p, p["base"]), ch="─")
        body = max(h - 6, 1)
        self.page_rows = body
        sf = info["session_file"]
        lines = self.transcript(sf, w - 4)
        self._stamp = self.chat.stamp(sf) if sf else None
        if not lines:
            U.empty_state(cv, p, x + 2, y + 3, w - 4, "No messages yet.",
                          "The session has no messages yet." if sf else "Write below: the bot answers in its own session.")
        self.scroll = max(0, min(self.scroll, max(len(lines) - body, 0)))
        end = len(lines) - self.scroll
        for j, (text, key, attrs) in enumerate(lines[max(end - body, 0):end]):
            cv.put(x + 2, y + 3 + j, U.fit_cells(text, w - 4), p[key], None, attrs)
        if self.scroll:
            cv.put(x + w - 14, y + 2, f"↓ {self.scroll} more", p["dim"], p["base"])
        note, bad = self.talk_note
        if info["error"] and not note:
            note, bad = f"last turn failed: {info['error']}", True
        if note:
            cv.put(x + 2, y + h - 3, U.fit_cells(note, w - 4), p["red"] if bad else p["muted"])
        cv.fill(x + 1, y + h - 2, w - 2, 1, bg=p["deep"])
        cv.hit(x + 1, y + h - 2, w - 2, 1, lambda b: None)
        shown = self.draft[-(w - 8):]
        cv.put(x + 3, y + h - 2, "▏" + shown, p["accent"], p["deep"])
        cv.put(x + 4, y + h - 2, shown, p["bright"], p["deep"])
        if not shown:
            cv.put(x + 5, y + h - 2, "Talk to the bot", p["dim"], p["deep"])

    def start_now(self, ui):
        ok, message = self.start()
        ui.say(message, not ok)

    def render_detail(self, ui, cv, x, y, w, h, now):
        p = ui.pal
        r = self.row(self.detail)
        sched = r["sched"]
        U.chip(cv, p, x + 1, y, "‹ Back", kind="button", bg=p["base"], hit=lambda b: setattr(self, "detail", None))
        U.chip(cv, p, x + w - 1 - U.chip_width("Talk"), y, "Talk", kind="primary", bg=p["base"],
               hit=lambda b, sid=self.detail: self.open_talk(sid))
        glyph, label, gcolor = U.state_style(p, r["state"], now if r["state"] == "working" else None)
        pos = x + 1 + U.chip_width("‹ Back") + 2
        pos += cv.put(pos, y, f"{BOT_KIND_ICON[r['kind']]} ", p["muted"])
        pos += cv.put(pos, y, U.fit_cells(r["name"], max(w - pos - 24, 8)), p["bright"], None, BOLD)
        cv.put(pos + 2, y, f"{glyph} {label}", gcolor)
        run = sched["run"]
        nxt = f"next in {span_text(r['next_at'] - now)}" if r["next_at"] and r["enabled"] and not r["paused_reason"] else "no next run"
        if r.get("skipped_at"):
            nxt += f" (the run at {clock_text(r['skipped_at'], sched['when'].get('tz', 'local'), now)} is skipped, s undoes it)"
        facts = [f"{r['when_text']}", nxt, U.home_path(run["cwd"]), f"account {run['account'] or 'default'}",
                 f"{run['permissions']} · {run['mode']}"]
        cv.put(x + 2, y + 1, U.fit_cells(" · ".join(facts), w - 4), p["muted"])
        note = ""
        if r["paused_reason"]:
            cv.put(x + 2, y + 2, U.fit_cells(f"Paused: {r['paused_reason']} · space resumes it", w - 4), p["amber"])
        else:
            prompt = " ".join(sched["task"]["prompt"].split())
            cv.put(x + 2, y + 2, U.fit_cells(prompt, w - 4), p["dim"])
        cv.put(x + 2, y + 3, "runs", p["muted"], None, BOLD)
        cv.fill(x, y + 4, w, 1, fg=U.rule_color(p, p["base"]), ch="─")
        if not self.runs:
            U.empty_state(cv, p, x + 2, y + 5, w - 4, "No runs yet.", "The first one appears here after the schedule fires" + (f"; press r to run it now." if self.row(self.detail) else "."))
            return
        block = min(5, max(h - 10, 0))  # lines for the selected run's full text
        per = 2
        body = max((h - 5 - (block + 1 if block else 0)) // per, 1)
        self.page_rows = body
        self.run_cursor = max(0, min(self.run_cursor, len(self.runs) - 1))
        top = min(max(self.run_cursor - body + 1, 0), max(len(self.runs) - body, 0))
        for k in range(top, min(top + body, len(self.runs))):
            rec = self.runs[k]
            ry = y + 5 + (k - top) * per
            status = rec.get("status") or "?"
            sglyph, _, scolor = U.state_style(p, RUN_STATE.get(status, "idle"))
            took = span_text((rec["ended"] - rec["started"])) if rec.get("ended") and rec.get("started") else "—"
            cost = f"${rec['cost_usd']:.2f}" if isinstance(rec.get("cost_usd"), (int, float)) else "—"
            fresh = rec.get("run") in self.fresh
            name = f"{'● ' if fresh else ''}{status} · {span_text(now - (rec.get('ended') or rec.get('started') or now))} ago · took {took}"
            sub = (rec.get("error") if status not in ("ok",) and rec.get("error") else rec.get("summary")) or ""
            U.list_row(cv, p, ry, x=x, w=w, lines=per, selected=k == self.run_cursor, bg=p["base"], glyph=sglyph, glyph_color=scolor,
                       num=f"#{rec.get('run')}", num_w=4, name=name, name_color=p["bright"] if fresh else p["text"],
                       name_attrs=BOLD if fresh else None, right=f"{rec.get('trigger') or ''} · {cost}", sub=" ".join(sub.split()),
                       hit=lambda b, k=k: setattr(self, "run_cursor", k))
        if block:
            rec = self.runs[self.run_cursor]
            by = y + 5 + min(body, len(self.runs)) * per + 1
            cv.fill(x, by, w, 1, fg=U.rule_color(p, p["base"]), ch="─")
            text = rec.get("summary") or rec.get("error") or "No summary."
            if rec.get("error") and rec.get("status") != "ok" and rec.get("summary"):
                text = f"{rec['summary']} — {rec['error']}"
            lines = textwrap.wrap(" ".join(text.split()), max(w - 4, 10))[:block]
            for j, line in enumerate(lines):
                cv.put(x + 2, by + 1 + j, line, p["text"] if rec.get("status") == "ok" else p["amber"])
