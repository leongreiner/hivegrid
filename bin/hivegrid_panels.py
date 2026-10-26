"""The four Hivegrid panels: settings, usage history, agent overview, system monitor."""
import json
import os
import re
import shutil
import subprocess
import time
from collections import deque
from pathlib import Path

import hivegrid_ui as U
from hivegrid_settings_core import (
    BINDINGS, PRESETS, about_info, find_wezterm, cache_dir, check_binding, discover_accounts, effective_keys, format_combo,
    merge_accounts, normalize, omp_profiles, pair_profile, quota_state, remove_account, tilde, untilde,
    valid_label, wezterm_defaults,
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
        label, default, kind = BINDINGS[self.name]
        x, y, w, h = U.box(ui, cv, 66, 11, label)
        what = "the key to press after the leader" if kind == "leader" else "the new key combination"
        cv.put(x + 2, y + 2, f"Press {what}.", p["text"], p["surface"])
        cv.put(x + 2, y + 3, f"now: {self.page.model.keys[self.name]}", p["muted"], p["surface"])
        if self.error:
            cv.put(x + 2, y + 5, fit(self.error, w - 4), p["red"], p["surface"])
        cv.put(x + 2, y + 6, "A terminal reports Ctrl+letter, Alt+key and plain keys; type others.", p["dim"], p["surface"])
        ui.button(cv, x + 2, y + h - 2, "Type it", lambda b: self._type(ui))
        ui.button(cv, x + 13, y + h - 2, "Cancel", lambda b: ui.close_modal())
        cv.put(x + 24, y + h - 2, "Esc cancels, Tab types it", p["muted"], p["surface"])

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


class SettingsPage(U.Page):
    title = "Settings"
    SUBS = ["Theme", "Keys", "Accounts", "About"]

    def __init__(self, model):
        self.model = model
        self.sub = 0
        self.cursor = [0, 0, 0]
        self.profiles = omp_profiles()
        self.wez_defaults = None
        self.about = None
        stored = model.settings.stored_accounts()
        model.accounts = stored if stored is not None else merge_accounts(None, discover_accounts())
        model.baseline = model.snapshot()
        self.found = []
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
            0: "↑↓ choose  Enter use  a accent  s save  Tab section  q close",
            1: "↑↓ choose  Enter change  r reset  R reset all  s save  Tab section  q close",
            2: "↑↓ choose  Space show/hide  e rename  p profile  d remove  a add  c rescan  s save  q close",
            3: "Tab section  q close",
        }[self.sub]

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
        m = self.model
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
                m.keys.update(effective_keys({}))
                ui.say("all bindings reset to their defaults")
        elif self.sub == 2:
            self.accounts_key(ui, key)

    def wheel(self, ui, dy):
        if self.sub in (0, 1, 2):
            limit = {0: len(PRESETS), 1: len(BINDINGS), 2: len(self.model.accounts)}[self.sub] - 1
            self.cursor[self.sub] = max(0, min(max(limit, 0), self.cursor[self.sub] + dy))

    # actions ------------------------------------------------------------------
    def choose_preset(self, ui, pid):
        self.model.theme = {"preset": pid, "accent": PRESETS[pid]["palette"]["accent"]}
        ui.say(f"theme: {PRESETS[pid]['name']}  (Save applies it)")

    def set_accent(self, ui, color):
        self.model.theme["accent"] = color
        ui.say(f"accent {color}  (Save applies it)")

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
        ui.say(f"{BINDINGS[name][0]} reset to {self.model.keys[name]}")

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
        ui.say(f"{a['label']} {'hidden' if a['hidden'] else 'shown'} in the header  (Save applies it)")

    def rename(self, ui, i):
        accts = self.model.accounts

        def ok(text):
            err = valid_label(text, accts, own=i)
            if err:
                return err
            accts[i]["label"] = text
            ui.say("renamed  (Save applies it)")
            return None
        ui.open_modal(U.PromptModal(f"Rename {accts[i]['label']}", accts[i]["label"], ok, "label shown in the header"))

    def pick_profile(self, ui, i):
        a = self.model.accounts[i]
        options = ["(none)"] + self.profiles
        cur = options.index(a["profile"]) if a.get("profile") in options else 0

        def pick(idx):
            a["profile"] = None if idx == 0 else options[idx]
            ui.say("profile set  (Save applies it)")
        ui.open_modal(U.ChoiceModal(f"omp profile of {a['label']}", options, cur, pick))

    def remove(self, ui, i):
        a = self.model.accounts[i]

        def yes():
            ui.say(remove_account(self.model.accounts, i) + "  (Save applies it)")
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
                profile = pair_profile(label, self.profiles)
                entry = {"label": label, "profile": profile, "dir": tilde(untilde(d.strip())), "auto": False, "hidden": False}
                accts.append(entry)
                self.cursor[2] = len(accts) - 1
                ui.say(f"added {label}  (Save applies it)")
                if not profile:
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
        ui.say(f"added {a['label']}  (Save applies it)")

    # drawing ------------------------------------------------------------------
    def render(self, ui, cv, x, y, w, h):
        p = ui.pal
        pos = x + 2
        for i, name in enumerate(self.SUBS):
            label = f" {name} "
            sel = i == self.sub
            cv.put(pos, y, label, p["bright"] if sel else p["muted"], p["raised"] if sel else p["base"], BOLD if sel else 0)
            cv.hit(pos, y, len(label), 1, lambda b, i=i: setattr(self, "sub", i))
            pos += len(label) + 1
        getattr(self, "draw_" + self.SUBS[self.sub].lower())(ui, cv, x, y + 2, w, h - 2)

    def draw_theme(self, ui, cv, x, y, w, h):
        p = ui.pal
        cur = self.cursor[0] = max(0, min(self.cursor[0], len(PRESETS) - 1))
        active = self.model.theme["preset"]
        cv.put(x + 2, y, "Colour theme", p["bright"], None, BOLD)
        for i, pid in enumerate(PRESETS):
            row = y + 2 + i
            pal = PRESETS[pid]["palette"]
            sel = i == cur
            bg = p["raised"] if sel else p["base"]
            cv.fill(x + 1, row, 54, 1, bg=bg)
            cv.put(x + 2, row, "●" if pid == active else " ", p["accent"], bg)
            cv.put(x + 4, row, f"{PRESETS[pid]['name']:<16}", p["bright"] if sel else p["text"], bg)
            for k, key in enumerate(("deep", "base", "surface", "pill", "raised", "accent", "green", "amber", "red")):
                ui.swatch(cv, x + 22 + k * 3, row, pal[key])
            cv.hit(x + 1, row, 54, 1, lambda b, pid=pid, i=i: (self.cursor.__setitem__(0, i), self.choose_preset(ui, pid)))
        ay = y + 2 + len(PRESETS) + 1
        cv.put(x + 2, ay, "Accent", p["bright"], None, BOLD)
        ui.swatch(cv, x + 10, ay, self.model.theme["accent"], 4)
        cv.put(x + 15, ay, self.model.theme["accent"], p["text"])
        ui.button(cv, x + 24, ay, "Edit", lambda b: self.edit_accent(ui))
        for k, c in enumerate(ACCENTS):
            sx = x + 33 + k * 5
            ui.swatch(cv, sx, ay, c, 4)
            cv.hit(sx, ay, 4, 1, lambda b, c=c: self.set_accent(ui, c))
        py = ay + 2
        cv.put(x + 2, py, "Preview", p["bright"], None, BOLD)
        t = self.model.current_theme()["palette"]
        bar = py + 1
        cv.fill(x + 2, bar, 62, 1, bg=t["deep"])
        cv.put(x + 3, bar, "⬡", t["accent"], t["deep"])
        cv.put(x + 5, bar, " ~/proj · 3 ", t["bright"], t["pill"])
        cv.put(x + 18, bar, " main ", t["bright"], t["raised"], BOLD)
        cv.put(x + 24, bar, " 12% ", t["green"], t["pill"])
        cv.put(x + 29, bar, "57% ", t["amber"], t["pill"])
        cv.put(x + 33, bar, "91% ", t["red"], t["pill"])
        cv.fill(x + 2, bar + 1, 62, 3, bg=t["base"])
        cv.put(x + 3, bar + 1, "$ omp --profile=work", t["text"], t["base"])
        cv.put(x + 3, bar + 2, "selected text", t["bright"], self.model.current_theme()["terminal"]["selection_bg"])
        cv.put(x + 3, bar + 3, "plain  muted  dim", t["muted"], t["base"])
        term = self.model.current_theme()["terminal"]
        for k, c in enumerate(term["ansi"] + term["brights"]):
            ui.swatch(cv, x + 2 + k * 3, bar + 5, c)

    def draw_keys(self, ui, cv, x, y, w, h):
        p = ui.pal
        names = list(BINDINGS)
        cur = self.cursor[1] = max(0, min(self.cursor[1], len(names) - 1))
        cv.put(x + 2, y, f"{'Action':<36}{'Binding':<24}Default", p["muted"], None, BOLD)
        ui.button(cv, x + w - 16, y, "Reset all", lambda b: (self.model.keys.update(effective_keys({})), ui.say("all bindings reset")))
        for i, name in enumerate(names):
            row = y + 2 + i
            label, default, _ = BINDINGS[name]
            value = self.model.keys[name]
            changed = value != normalize(default)
            sel = i == cur
            bg = p["raised"] if sel else p["base"]
            cv.fill(x + 1, row, w - 2, 1, bg=bg)
            cv.put(x + 2, row, f"{label:<36}", p["bright"] if sel else p["text"], bg)
            cv.put(x + 38, row, f"{value:<22}", p["accent"] if changed else p["text"], bg, BOLD if changed else 0)
            cv.put(x + 62, row, normalize(default) if changed else "default", p["dim"], bg)
            cv.hit(x + 1, row, 60, 1, lambda b, i=i: self.cursor.__setitem__(1, i))
            bx = x + 38 + 24 + 14
            if bx + 20 < x + w:
                ui.button(cv, bx, row, "Change", lambda b, n=name, i=i: (self.cursor.__setitem__(1, i), self.change_binding(ui, n)))
                if changed:
                    ui.button(cv, bx + 10, row, "Reset", lambda b, n=name: self.reset_binding(ui, n))
            else:
                cv.hit(x + 38, row, 22, 1, lambda b, n=name, i=i: (self.cursor.__setitem__(1, i), self.change_binding(ui, n)))
        cv.put(x + 2, y + 3 + len(names), "Click a binding (or Enter) to record new keys. Save writes settings.json; WezTerm reloads it.", p["muted"])

    def draw_accounts(self, ui, cv, x, y, w, h):
        p = ui.pal
        accts = self.model.accounts
        cur = self.cursor[2] = max(0, min(self.cursor[2], max(len(accts) - 1, 0)))
        bx = x + 2
        for label, fn in (("Add", lambda b: self.add_account(ui)), ("Rescan", lambda b: self.rescan(ui))):
            bx += ui.button(cv, bx, y, label, fn) + 1
        have = bool(accts)
        for label, fn in (("Rename", lambda b: self.rename(ui, cur)), ("Profile", lambda b: self.pick_profile(ui, cur)),
                          ("Hide/Show", lambda b: self.toggle_hidden(ui, cur)), ("Remove", lambda b: self.remove(ui, cur))):
            bx += ui.button(cv, bx, y, label, fn, enabled=have) + 1
        cv.put(x + 2, y + 2, f"{'':<4}{'Label':<10}{'omp profile':<14}{'Config dir':<26}{'Source':<8}Quota", p["muted"], None, BOLD)
        for i, a in enumerate(accts):
            row = y + 3 + i
            sel = i == cur
            hidden = bool(a.get("hidden"))
            bg = p["raised"] if sel else p["base"]
            fgc = p["dim"] if hidden else (p["bright"] if sel else p["text"])
            cv.fill(x + 1, row, w - 2, 1, bg=bg)
            cv.put(x + 2, row, "[ ]" if hidden else "[x]", p["accent"] if not hidden else p["dim"], bg)
            cv.hit(x + 2, row, 3, 1, lambda b, i=i: (self.cursor.__setitem__(2, i), self.toggle_hidden(ui, i)))
            cv.put(x + 6, row, f"{fit(a['label'], 9):<10}{fit(a.get('profile') or '(none)', 13):<14}{fit(a['dir'], 25):<26}"
                   f"{'auto' if a.get('auto') else 'manual':<8}{'hidden' if hidden else quota_state(a['label'])}", fgc, bg)
            cv.hit(x + 6, row, w - 8, 1, lambda b, i=i: self.cursor.__setitem__(2, i))
        row = y + 4 + len(accts)
        cv.put(x + 2, row, "Found on this machine, not in your list yet (the header shows them anyway):" if self.found
               else "Every credentials directory found is in the list.", p["muted"])
        for k, a in enumerate(self.found):
            ry = row + 1 + k
            cv.put(x + 2, ry, f"{a['label']:<10}{a.get('profile') or '(no profile)':<14}{a['dir']}", p["text"])
            ui.button(cv, x + 50, ry, "Add", lambda b, k=k: self.add_found(ui, k))
        cv.put(x + 2, row + 3 + len(self.found), "Profiles: " + (", ".join(self.profiles) or "none"), p["dim"])

    def draw_about(self, ui, cv, x, y, w, h):
        p = ui.pal
        if self.about is None:
            self.about = about_info()
        a = self.about
        rows = [
            ("Settings file", tilde(self.model.settings.path) + ("" if self.model.settings.exists else "  (not written yet)"), None),
            ("WezTerm binary", tilde(a["executable"]), None),
            ("Version", a["version"], None),
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


def series_for(samples, label, since):
    """Points and reset times of both windows for `label` from `since`."""
    out = {"five_hour": [], "seven_day": []}
    resets = {"five_hour": set(), "seven_day": set()}
    first = None
    for s in samples:
        if s["label"] != label:
            continue
        first = s["ts"] if first is None else first
        if s["ts"] < since:
            continue
        for key in out:
            w = s.get(key)
            if isinstance(w, dict) and isinstance(w.get("pct"), (int, float)):
                out[key].append((s["ts"], float(w["pct"])))
                if isinstance(w.get("reset_at"), (int, float)):
                    resets[key].add(round(w["reset_at"]))
    return out, resets, first


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

    def labels(self):
        return [a["label"] for a in self.model.accounts if not a.get("hidden")]

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
        return "←→ account  ↑↓ range  click the buttons  Tab next panel  q close"

    def key(self, ui, mods, key):
        labels = self.labels()
        if key in ("LeftArrow", "RightArrow") and labels:
            i = labels.index(self.account) if self.account in labels else 0
            self.account = labels[(i + (1 if key == "RightArrow" else -1)) % len(labels)]
        elif key == "UpArrow":
            self.range = min(self.range + 1, len(RANGES) - 1)
        elif key == "DownArrow":
            self.range = max(self.range - 1, 0)

    def render(self, ui, cv, x, y, w, h):
        p = ui.pal
        labels = self.labels()
        if self.account not in labels and labels:
            self.account = labels[0]
        pos = x + 2
        cv.put(pos, y, "Account", p["muted"])
        pos += 9
        for label in labels:
            text = f" {label} "
            sel = label == self.account
            cv.put(pos, y, text, p["deep"] if sel else p["text"], p["accent"] if sel else p["raised"], BOLD if sel else 0)
            cv.hit(pos, y, len(text), 1, lambda b, label=label: setattr(self, "account", label))
            pos += len(text) + 1
        pos = max(pos + 2, x + 2)
        cv.put(pos, y, "Range", p["muted"])
        pos += 7
        for i, (name, _) in enumerate(RANGES):
            text = f" {name} "
            sel = i == self.range
            cv.put(pos, y, text, p["deep"] if sel else p["text"], p["accent"] if sel else p["raised"], BOLD if sel else 0)
            cv.hit(pos, y, len(text), 1, lambda b, i=i: setattr(self, "range", i))
            pos += len(text) + 1
        if not self.account:
            cv.put(x + 2, y + 3, "No accounts configured.", p["muted"])
            return
        now = self.now()
        span = RANGES[self.range][1]
        series, resets, first = series_for(self.samples, self.account, now - span)
        last5 = series["five_hour"][-1] if series["five_hour"] else None
        last7 = series["seven_day"][-1] if series["seven_day"] else None
        info = f"{self.account}: " + (f"5h {last5[1]:.0f}%  " if last5 else "5h --  ") + (f"7d {last7[1]:.0f}%  " if last7 else "7d --  ") + quota_state(self.account)
        cv.put(x + 2, y + 2, fit(info, w - 4), p["bright"], None, BOLD)
        note = ""
        if first is None:
            note = "No history for this account yet: samples are written by `claude-quota refresh`."
        elif first > now - span * 0.98:
            note = "history starts " + time.strftime("%Y-%m-%d %H:%M", time.localtime(first))
        cv.put(x + 2, y + 3, fit(note, w - 4), p["amber"])
        top = y + 5
        ch_h = max(h - 5, 4)
        ch_w = w - 4
        pts5, pts7 = series["five_hour"], series["seven_day"]
        if not (pts5 or pts7):
            return
        x0, x1 = now - span, now
        markers5 = [(t, p["accent"]) for t in resets["five_hour"]] if span <= 86400 else []
        markers7 = [(t, p["amber"]) for t in resets["seven_day"]]
        markers = markers5 + markers7
        if U.images_enabled():
            cw, chh = U.cell_pixels()
            png = U.line_chart_png(
                ch_w * cw, ch_h * chh,
                [{"pts": pts7, "color": p["amber"], "label": "7d"}, {"pts": pts5, "color": p["accent"], "label": "5h"}],
                x0, x1, 100.0, p, markers=markers, title=f"{self.account}  utilisation")
            cv.image(x + 2, top, ch_w, ch_h, png)
        else:
            self.braille(ui, cv, x + 2, top, ch_w, ch_h, (pts5, markers5), (pts7, markers7), x0, x1)

    def braille(self, ui, cv, x, y, w, h, five, seven, x0, x1):
        p = ui.pal
        half = max(h // 2, 3)
        for k, (name, (pts, markers), color) in enumerate((("5h", five, p["accent"]), ("7d", seven, p["amber"]))):
            top = y + k * half
            cv.put(x, top, f"{name}", color, None, BOLD)
            gx, gw, gh = x + 8, max(w - 9, 10), half - 1
            rows = U.braille_chart(gw, gh, [{"pts": pts, "color": color}], x0, x1, 100.0)
            for r, row in enumerate(rows):
                for c, (ch, col) in enumerate(row):
                    if ch != " ":
                        cv.put(gx + c, top + r, ch, col, p["base"])
                pct = 100 - round(r / max(gh - 1, 1) * 100)
                if r in (0, gh // 2, gh - 1):
                    cv.put(x + 3, top + r, f"{pct:>3}%"[:4], p["dim"])
            for t, col in markers:
                if x0 <= t <= x1:
                    mx = gx + round((t - x0) / max(x1 - x0, 1) * (gw - 1))
                    cv.put(mx, top + gh, "▲", col, p["base"])
        left = U._fmt_time(x0, x1 - x0)
        right = U._fmt_time(x1, x1 - x0)
        ty = min(y + 2 * half, y + h - 1)
        cv.put(x + 8, ty, left, p["dim"])
        cv.put(x + w - len(right), ty, right, p["dim"])


# ---- agents -------------------------------------------------------------------------

SHELLS = {"bash", "zsh", "fish", "sh", "dash"}


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
    """`wezterm cli list --format json` -> rows for the table."""
    try:
        panes = json.loads(text)
    except ValueError:
        return []
    home = home or os.environ.get("HOME", "")
    rows = []
    for n, pane in enumerate(sorted(panes, key=lambda p: (p.get("window_id", 0), p.get("tab_id", 0), p.get("pane_id", 0))), 1):
        tty = str(pane.get("tty_name", "")).replace("/dev/", "")
        pid, comm, etimes, args = fg.get(tty, (None, "", 0, ""))
        cwd = str(pane.get("cwd", ""))
        cwd = re.sub(r"^file://[^/]*", "", cwd)
        if home and cwd.startswith(home):
            cwd = "~" + cwd[len(home):]
        profile = ""
        m = re.search(r"--profile[= ](\S+)", args)
        if comm == "omp":
            profile = m.group(1) if m else ""
        rows.append({
            "n": n, "pane": pane.get("pane_id"), "tab": pane.get("tab_id"), "window": pane.get("window_id"),
            "proc": comm or "?", "profile": profile, "cwd": cwd, "title": str(pane.get("title", "")),
            "age": etimes, "active": bool(pane.get("is_active")), "agent": comm == "omp" or comm in ("claude", "codex", "gemini"),
            "idle": comm in SHELLS,
        })
    return rows


class AgentsPage(U.Page):
    title = "Agents"

    def __init__(self, model):
        self.model = model
        self.rows = []
        self.cursor = 0
        self.error = ""
        self._at = 0

    def on_show(self, ui):
        self._at = 0
        self.tick(ui)

    def hints(self):
        return "↑↓ choose  Enter or click: jump to the pane  Tab next panel  q close"

    def refresh(self):
        text = wezterm_cli("list", "--format", "json")
        if text is None:
            self.error = "no WezTerm mux in this terminal (run the panel inside Hivegrid)"
            self.rows = []
            return
        self.error = ""
        self.rows = parse_panes(text, foreground_by_tty())

    def tick(self, ui):
        t = time.time()
        if t - self._at < 2:
            return False
        self._at = t
        self.refresh()
        return True

    def wheel(self, ui, dy):
        self.cursor = max(0, min(len(self.rows) - 1, self.cursor + dy))

    def key(self, ui, mods, key):
        if key == "UpArrow":
            self.cursor = max(0, self.cursor - 1)
        elif key == "DownArrow":
            self.cursor = min(max(len(self.rows) - 1, 0), self.cursor + 1)
        elif key == "Enter" and self.rows:
            self.jump(ui, self.rows[self.cursor])

    def jump(self, ui, row):
        if wezterm_cli("activate-pane", "--pane-id", str(row["pane"])) is None:
            ui.say("could not activate the pane", True)
            return
        ui.running = False

    def render(self, ui, cv, x, y, w, h):
        p = ui.pal
        if self.error:
            cv.put(x + 2, y + 1, self.error, p["amber"])
            return
        head = f"{'#':>3} {'pane':>5} {'tab':>4} {'process':<10}{'profile':<10}{'running':<9}{'cwd':<34}title"
        cv.put(x + 1, y, head, p["muted"], None, BOLD)
        self.cursor = max(0, min(self.cursor, max(len(self.rows) - 1, 0)))
        for i, r in enumerate(self.rows[: max(h - 3, 1)]):
            row = y + 1 + i
            sel = i == self.cursor
            bg = p["raised"] if sel else p["base"]
            colour = p["green"] if r["agent"] else (p["dim"] if r["idle"] else p["text"])
            cv.fill(x, row, w, 1, bg=bg)
            cv.put(x + 1, row, f"{r['n']:>3} {r['pane']:>5} {r['tab']:>4} ", p["bright"] if sel else p["text"], bg)
            cv.put(x + 16, row, f"{fit(r['proc'], 9):<10}", colour, bg, BOLD if r["agent"] else 0)
            cv.put(x + 26, row, f"{fit(r['profile'], 9):<10}{human_age(r['age']) if r['age'] else '':<9}{fit(r['cwd'], 33):<34}"
                   f"{fit(r['title'], max(w - 80, 10))}", p["bright"] if sel else p["text"], bg)
            if r["active"]:
                cv.put(x, row, "▎", p["accent"], bg)
            cv.hit(x, row, w, 1, lambda b, r=r, i=i: (setattr(self, "cursor", i), self.jump(ui, r)))
        cv.put(x + 1, y + 2 + min(len(self.rows), max(h - 3, 1)), f"{len(self.rows)} pane(s); ▎ marks the active pane of its tab", p["dim"])


# ---- monitor ------------------------------------------------------------------------

MON_RANGES = [("1m", 60), ("5m", 300), ("15m", 900)]


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
    """CPU, RAM, GPU and disk of `/` every second; the last 15 minutes are kept."""

    JETSON = ("/sys/devices/platform/gpu.0/load", "/sys/devices/gpu.0/load")

    def __init__(self, keep=900):
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
                self.gpu_text = f"{self.gpu:.0f}%"
            except ValueError:
                self.gpu = None
            return
        try:
            r = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
                                "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=2)
            u, used, total = [float(v) for v in r.stdout.splitlines()[0].split(",")]
            self.gpu = u
            self.gpu_text = f"{u:.0f}%  {used / 1024:.1f}/{total / 1024:.0f}G"
        except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
            self.gpu = None

    def tick(self):
        now = time.time()
        if now - self.t < 1.0:
            return False
        self.t = now
        cpu = parse_cpu_line(self.read("/proc/stat"))
        pct = None
        if cpu and self.prev:
            dt, di = cpu[0] - self.prev[0], cpu[1] - self.prev[1]
            pct = 100.0 * (dt - di) / dt if dt > 0 else None
        self.prev = cpu
        ram, total, avail = parse_meminfo(self.read("/proc/meminfo"))
        if ram is not None:
            self.ram_text = f"{ram:.0f}%  {(total - avail) / 1048576:.1f}/{total / 1048576:.0f}G"
        try:
            du = shutil.disk_usage("/")
            dsk = 100.0 * du.used / du.total
            self.dsk_text = f"{dsk:.0f}%  {du.used / 2**30:.0f}/{du.total / 2**30:.0f}G"
        except OSError:
            dsk = None
        self.sample_gpu(now)
        self.times.append(now)
        for key, val in (("CPU", pct), ("RAM", ram), ("GPU", self.gpu), ("DSK", dsk)):
            self.hist[key].append(val)
        return True

    def values(self, key, seconds):
        return [v for v in list(self.hist[key])[-seconds:]]

    def current(self, key):
        h = self.hist[key]
        return h[-1] if h and h[-1] is not None else None

    def text(self, key):
        v = self.current(key)
        if v is None:
            return "n/a"
        return {"RAM": self.ram_text, "GPU": self.gpu_text, "DSK": self.dsk_text}.get(key, f"{v:.0f}%")


class MonitorPage(U.Page):
    title = "Monitor"

    def __init__(self, model, sampler=None):
        self.model = model
        self.sampler = sampler or Sampler()
        self.range = 1

    def hints(self):
        return "←→ range  Tab next panel  q close"

    def key(self, ui, mods, key):
        if key == "RightArrow":
            self.range = min(self.range + 1, len(MON_RANGES) - 1)
        elif key == "LeftArrow":
            self.range = max(self.range - 1, 0)

    def tick(self, ui):
        return self.sampler.tick()

    def render(self, ui, cv, x, y, w, h):
        p = ui.pal
        cv.put(x + 2, y, "Range", p["muted"])
        pos = x + 9
        for i, (name, _) in enumerate(MON_RANGES):
            text = f" {name} "
            sel = i == self.range
            cv.put(pos, y, text, p["deep"] if sel else p["text"], p["accent"] if sel else p["raised"], BOLD if sel else 0)
            cv.hit(pos, y, len(text), 1, lambda b, i=i: setattr(self, "range", i))
            pos += len(text) + 1
        seconds = MON_RANGES[self.range][1]
        metrics = [("CPU", p["accent"]), ("RAM", p["green"]), ("DSK", p["amber"])]
        if self.sampler.gpu_kind:
            metrics.append(("GPU", p["violet"]))
        top = y + 2
        avail = h - 2
        each = max(avail // len(metrics), 4)
        for k, (key, color) in enumerate(metrics):
            gy = top + k * each
            gh = each - 1
            vals = self.sampler.values(key, seconds)
            if U.images_enabled():
                cw, chh = U.cell_pixels()
                png = U.sparkline_png((w - 4) * cw, gh * chh, [v if v is not None else 0 for v in vals], color, p, key,
                                      self.sampler.text(key))
                cv.image(x + 2, gy, w - 4, gh, png)
            else:
                cv.put(x + 2, gy, key, color, None, BOLD)
                cv.put(x + w - 2 - len(self.sampler.text(key)), gy, self.sampler.text(key), color, None, BOLD)
                if gh > 1:
                    n = len(vals)
                    pts = [(i, v if v is not None else 0) for i, v in enumerate(vals)]
                    rows = U.braille_chart(w - 6, gh - 1, [{"pts": pts, "color": color}], 0, max(n - 1, 1), 100.0)
                    for r, row in enumerate(rows):
                        for c, (ch, col) in enumerate(row):
                            if ch != " ":
                                cv.put(x + 3 + c, gy + 1 + r, ch, col, p["base"])
