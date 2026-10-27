"""Shared logic of the Hivegrid settings panel: colour presets, key bindings and their conflict
checks, omp/Claude account discovery and pairing, and the settings.json file. No UI here."""
import json
import mmap
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

VERSION = "1.1"
SCHEMA = 1

# ---- themes ------------------------------------------------------------------------

PALETTE_KEYS = ["deep", "base", "surface", "pill", "raised", "track", "border", "edge_hi", "edge_lo",
                "text", "bright", "muted", "dim", "accent", "cyan", "green", "amber", "red", "violet", "grey"]
TERMINAL_KEYS = ["selection_bg", "split", "scrollbar_thumb", "button_hover", "tab_hover", "tab_edge"]
SWATCH_KEYS = ["deep", "base", "surface", "pill", "raised", "accent", "green", "amber", "red"]

_SEMANTIC = dict(cyan="#38bdf8", green="#34d399", amber="#fbbf24", red="#f87171", violet="#a78bfa")


def _preset(name, palette, terminal, ansi, brights):
    p = dict(palette)
    for k, v in _SEMANTIC.items():
        p.setdefault(k, v)
    return {"name": name, "palette": p, "terminal": {**terminal, "ansi": ansi, "brights": brights}}


PRESETS = {
    # The default: neutral graphite and one blue accent; the meaning colours keep their hues.
    "hivegrid-graphite": _preset(
        "Hivegrid Graphite",
        dict(deep="#070a10", base="#0b0f16", surface="#111722", pill="#162030", raised="#222f45", track="#1c2535",
             border="#293345", edge_hi="#4a5d7d", edge_lo="#0a0e15", text="#e2e8f2", bright="#f5f8fc",
             muted="#8d9ab0", dim="#7a879d", accent="#78a9ff", cyan="#5cc8f0", green="#4ade80", amber="#f5a524",
             red="#f87171", violet="#a78bfa", grey="#8590a5"),
        dict(selection_bg="#26395c", split="#1c2433", scrollbar_thumb="#263143", button_hover="#1c2638",
             tab_hover="#172030", tab_edge="#05070b"),
        ["#243044", "#f87171", "#4ade80", "#f5a524", "#78a9ff", "#c4a1f7", "#5cc8f0", "#e2e8f2"],
        ["#5b677d", "#fca5a5", "#86efac", "#fcd34d", "#a5c6ff", "#dac4fa", "#8fdcf5", "#f5f8fc"]),
    # The former default.
    "hivegrid-navy": _preset(
        "Hivegrid Navy",
        dict(deep="#070d19", base="#0b1220", surface="#111b30", pill="#15244a", raised="#1d3a70", track="#1f2d4d",
             border="#1e2a45", edge_hi="#4a6fb0", edge_lo="#0c1630", text="#c9d6ee", bright="#eaf1ff",
             muted="#8a9cc2", dim="#7487ad", accent="#4f9dff", cyan="#38bdf8", green="#34d399", amber="#fbbf24",
             red="#f87171", violet="#a78bfa", grey="#8190ad"),
        dict(selection_bg="#1f3f73", split="#1a2b4d", scrollbar_thumb="#22324f", button_hover="#1b2f55",
             tab_hover="#18274a", tab_edge="#04080f"),
        ["#243252", "#f87171", "#34d399", "#fbbf24", "#4f9dff", "#c084fc", "#38bdf8", "#c9d6ee"],
        ["#5b6b8f", "#fca5a5", "#6ee7b7", "#fcd34d", "#7dbaff", "#d8b4fe", "#7dd3fc", "#eaf1ff"]),
    "midnight-black": _preset(
        "Midnight Black",
        dict(deep="#050506", base="#0b0b0d", surface="#141417", pill="#1c1c22", raised="#2e2e3a", track="#26262e",
             border="#23232b", edge_hi="#5a5a70", edge_lo="#0d0d10", text="#d4d4dc", bright="#f4f4f8",
             muted="#9a9aa8", dim="#8888a0", accent="#7aa2f7", grey="#8a8a98"),
        dict(selection_bg="#2f3a5c", split="#26262e", scrollbar_thumb="#2e2e38", button_hover="#25252e",
             tab_hover="#1d1d24", tab_edge="#000000"),
        ["#2a2a33", "#f87171", "#34d399", "#fbbf24", "#7aa2f7", "#c084fc", "#38bdf8", "#d4d4dc"],
        ["#5a5a6e", "#fca5a5", "#6ee7b7", "#fcd34d", "#a5c0fa", "#d8b4fe", "#7dd3fc", "#f4f4f8"]),
    "slate": _preset(
        "Slate",
        dict(deep="#0f141a", base="#1a2129", surface="#232c36", pill="#2c3743", raised="#3d5368", track="#34414f",
             border="#2f3b48", edge_hi="#6b8aa8", edge_lo="#12181e", text="#d3dde8", bright="#f2f6fa",
             muted="#9fb0c2", dim="#90a3b8", accent="#6cb6ff", grey="#92a3b5"),
        dict(selection_bg="#3a5a7a", split="#2f3b48", scrollbar_thumb="#3a4756", button_hover="#34465a",
             tab_hover="#2a3644", tab_edge="#0a0e12"),
        ["#2f3b48", "#f87171", "#4ade80", "#fbbf24", "#6cb6ff", "#c4a1f7", "#5ed3e8", "#d3dde8"],
        ["#66788c", "#fca5a5", "#86efac", "#fcd34d", "#9ccdff", "#dac4fa", "#8fe2f0", "#f2f6fa"]),
    "forest": _preset(
        "Forest",
        dict(deep="#08120d", base="#0e1a14", surface="#15251d", pill="#1b3025", raised="#2a5238", track="#25392d",
             border="#223a2c", edge_hi="#5f9f78", edge_lo="#0a1610", text="#cfe3d6", bright="#eefaf2",
             muted="#8fb09c", dim="#80a38d", accent="#4fc9a4", grey="#86a393"),
        dict(selection_bg="#2a5a40", split="#22382b", scrollbar_thumb="#2b4436", button_hover="#244434",
             tab_hover="#1c3226", tab_edge="#050c08"),
        ["#1f3328", "#f87171", "#4ade80", "#fbbf24", "#5aa9e6", "#c084fc", "#4fc9a4", "#cfe3d6"],
        ["#52705f", "#fca5a5", "#86efac", "#fcd34d", "#8cc4f0", "#d8b4fe", "#7fe0c2", "#eefaf2"]),
}
DEFAULT_PRESET = "hivegrid-graphite"
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def theme_for(preset_id, accent=None):
    """Resolved theme record: { preset, accent, palette, terminal }."""
    base = PRESETS[preset_id]
    palette = dict(base["palette"])
    terminal = {k: (list(v) if isinstance(v, list) else v) for k, v in base["terminal"].items()}
    if accent:
        if not HEX_RE.match(accent):
            raise ValueError(f"not a #rrggbb colour: {accent!r}")
        palette["accent"] = accent.lower()
    return {"preset": preset_id, "accent": palette["accent"], "palette": palette, "terminal": terminal}


# ---- key bindings ------------------------------------------------------------------

# name -> (label, default, kind). kind "leader": stored as LEADER+<key>, only the key is chosen.
BINDINGS = {
    "leader": ("Leader key", "CTRL+Space", "combo"),
    "sidebar_files": ("Sidebar: files", "CTRL+f", "combo"),
    "sidebar_git": ("Sidebar: git", "CTRL+g", "combo"),
    "sidebar_toggle": ("Sidebar: toggle", "LEADER+Tab", "leader"),
    "overview_toggle": ("Overview dock: toggle", "LEADER+o", "leader"),
    "equalize": ("Equalize all panes", "CTRL+SHIFT+e", "combo"),
    "arrange": ("Arrange panes into a grid", "CTRL+SHIFT+a", "combo"),
    "split_horizontal": ("Split side by side", "CTRL+SHIFT+|", "combo"),
    "split_vertical": ("Split top / bottom", "CTRL+SHIFT+_", "combo"),
    "quota_popup": ("Account launcher popup", "ALT+q", "combo"),
    "quota_launch": ("Account launcher keys (then 1-9)", "LEADER+u", "leader"),
    "quota_toggle": ("Quota chip on/off", "LEADER+q", "leader"),
    "settings": ("Settings panel", "LEADER+,", "leader"),
    "usage_panel": ("Usage history panel", "LEADER+h", "leader"),
    "hive_panel": ("Agent hive panel", "LEADER+a", "leader"),
    "web_leader": ("Web pane: commands", "LEADER+w", "leader"),
}

# Bindings of the Hivegrid config that this panel does not change.
FIXED_BINDINGS = [
    ("CTRL+c", "copy / interrupt"), ("CTRL+v", "paste"),
    ("ALT+h", "focus pane left"), ("ALT+l", "focus pane right"), ("ALT+k", "focus pane up"),
    ("ALT+j", "focus pane down"), ("ALT+z", "zoom pane"),
    ("CTRL+RightArrow", "split right"), ("CTRL+LeftArrow", "split left"),
    ("CTRL+DownArrow", "split down"), ("CTRL+UpArrow", "split up"),
    ("ALT+RightArrow", "focus right"), ("ALT+LeftArrow", "focus left"),
    ("ALT+DownArrow", "focus down"), ("ALT+UpArrow", "focus up"),
    ("ALT+n", "next agent needing you"), ("ALT+]", "next pane group"), ("ALT+[", "previous pane group"),
    ("ALT+s", "system stats on/off"),
    ("LEADER+1", "omp menu 1"), ("LEADER+2", "omp menu 2"), ("LEADER+3", "omp menu 3"),
    ("LEADER+n", "next agent needing you"), ("LEADER+g", "agent picker"), ("LEADER+r", "resize mode"),
    ("LEADER+e", "equalize panes"), ("LEADER+p", "pane mode"), ("LEADER+b", "broadcast"),
]

# ---- web pane ----------------------------------------------------------------------

# settings.json "web": links "pane" (an http(s) link clicked in a terminal opens in a web pane)
# or "system"; search is the URL template (with %s) a location that is no URL becomes, "" = none;
# profile names the browser profile (cookies, logins) new web panes use. Mirrors config/settings.lua.
WEB_DEFAULTS = {"links": "pane", "search": "", "profile": "main"}
WEB_LINKS = ("pane", "system")


def valid_profile(name):
    """None when `name` can name a browser profile (it becomes a directory name), else the reason."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}", name or ""):
        return "letters, digits, . _ - only, starting with a letter or digit, at most 32"
    return None


def valid_search(template):
    """None when `template` is empty or a search URL template, else the reason."""
    if not template:
        return None
    if "%s" not in template:
        return "must contain %s, where the search words go"
    if not re.match(r"https?://", template):
        return "must start with http:// or https://"
    return None


def web_settings(stored):
    """The effective web settings of the stored `web` value: defaults for anything missing or invalid."""
    stored = stored if isinstance(stored, dict) else {}
    out = dict(WEB_DEFAULTS)
    if stored.get("links") in WEB_LINKS:
        out["links"] = stored["links"]
    if isinstance(stored.get("search"), str) and valid_search(stored["search"]) is None:
        out["search"] = stored["search"]
    if isinstance(stored.get("profile"), str) and valid_profile(stored["profile"]) is None:
        out["profile"] = stored["profile"]
    return out


MOD_ORDER = ["CTRL", "ALT", "SHIFT", "SUPER", "LEADER"]
MOD_ALIASES = {"CTRL": "CTRL", "CONTROL": "CTRL", "ALT": "ALT", "OPT": "ALT", "META": "ALT",
               "SHIFT": "SHIFT", "SUPER": "SUPER", "CMD": "SUPER", "WIN": "SUPER", "LEADER": "LEADER"}
SPECIAL_KEYS = {"Space", "Tab", "Enter", "Escape", "Backspace", "Delete", "Insert", "Home", "End", "PageUp",
                "PageDown", "UpArrow", "DownArrow", "LeftArrow", "RightArrow"}


def parse_combo(text):
    """'CTRL+SHIFT+|' -> (frozenset({'CTRL','SHIFT'}), '|'). Raises ValueError."""
    if text is not None and not isinstance(text, str):
        raise ValueError(f"not a binding: {text!r}")  # a hand-edited settings.json may hold anything
    text = (text or "").strip()
    if not text:
        raise ValueError("empty binding")
    m = re.match(r"^(.*)\+(.+)$", text)
    mods_text, key = (m.group(1), m.group(2)) if m else ("", text)
    mods = set()
    for part in filter(None, (p.strip() for p in mods_text.split("+"))):
        mod = MOD_ALIASES.get(part.upper())
        if not mod:
            raise ValueError(f"unknown modifier {part!r}")
        mods.add(mod)
    if key.lower() in {k.lower() for k in SPECIAL_KEYS}:
        key = next(k for k in SPECIAL_KEYS if k.lower() == key.lower())
    elif re.fullmatch(r"[Ff]\d{1,2}", key):
        key = key.upper()
    elif len(key) == 1 and ord(key) > 32:
        pass
    else:
        raise ValueError(f"unknown key {key!r}")
    return frozenset(mods), key


def format_combo(mods, key):
    return "+".join([m for m in MOD_ORDER if m in mods] + [key])


def normalize(text):
    """Canonical spelling of a binding string."""
    return format_combo(*parse_combo(text))


def combo_id(mods, key):
    """Identity of a binding for conflict tests: shifted symbols and letter case do not matter."""
    mods = set(mods)
    if len(key) == 1:
        if key.isalpha():
            if key.isupper():
                mods.add("SHIFT")
            key = key.lower()
        elif not key.isalnum():
            mods.discard("SHIFT")
    return frozenset(mods), key


def parse_show_keys(text):
    """The default key table of `wezterm show-keys` -> { combo_id: action }."""
    out = {}
    for line in text.splitlines():
        if not line.startswith("\t"):
            if out and line.strip() and not line.startswith("-"):
                break  # a later key table
            continue
        m = re.match(r"^\t([A-Z| ]*?)\s{2,}(\S+)\s+->\s+(.*)$", line)
        if not m:
            continue
        mods = {MOD_ALIASES[p.strip()] for p in m.group(1).split("|") if p.strip() in MOD_ALIASES}
        out.setdefault(combo_id(mods, m.group(2)), m.group(3).strip())
    return out


def find_wezterm():
    """The `wezterm` binary of the terminal this runs in: the executable of the nearest wezterm-gui
    ancestor (its sibling `wezterm`), else WEZTERM_EXECUTABLE, else PATH. Matters because the
    `wezterm cli` of another build refuses to talk to the running GUI."""
    pid = os.getpid()
    for _ in range(40):
        try:
            with open(f"/proc/{pid}/stat") as f:
                stat = f.read()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
            exe = os.readlink(f"/proc/{ppid}/exe")
        except (OSError, ValueError, IndexError):
            break
        name = os.path.basename(exe)
        if name.startswith("wezterm-gui") or name == "wezterm":
            sibling = os.path.join(os.path.dirname(exe), "wezterm")
            return sibling if os.path.exists(sibling) else exe
        if ppid <= 1:
            break
        pid = ppid
    return os.environ.get("WEZTERM_EXECUTABLE") or shutil.which("wezterm")


def wezterm_defaults(exe=None):
    exe = exe or find_wezterm()
    if not exe:
        return {}
    try:
        r = subprocess.run([exe, "--skip-config", "show-keys"], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    return parse_show_keys(r.stdout) if r.returncode == 0 else {}


def effective_keys(stored):
    keys = {}
    for name, (_, default, _kind) in BINDINGS.items():
        value = (stored or {}).get(name, default)
        try:
            keys[name] = normalize(value)
        except ValueError:
            keys[name] = default
    return keys


def check_binding(name, combo, keys, wez_defaults=None):
    """Conflicts of `combo` for binding `name`: [('error'|'warn', text)].
    error: same chord as another setting or a fixed Hivegrid binding; warn: replaces a WezTerm default."""
    out = []
    kind = BINDINGS[name][2]
    try:
        mods, key = parse_combo(combo)
    except ValueError as exc:
        return [("error", str(exc))]
    if kind == "leader" and mods != {"LEADER"}:
        return [("error", "must be LEADER+<key>")]
    if kind == "combo" and "LEADER" in mods:
        return [("error", "LEADER+ is for the other bindings; choose a modifier combination")]
    if kind == "combo" and not mods:
        out.append(("error", "a bare key would swallow typing; add CTRL, ALT or SHIFT"))
    cid = combo_id(mods, key)
    for other, value in keys.items():
        if other != name and combo_id(*parse_combo(value)) == cid:
            out.append(("error", f"already used by: {BINDINGS[other][0]}"))
    for fixed, what in FIXED_BINDINGS:
        if combo_id(*parse_combo(fixed)) == cid:
            out.append(("error", f"fixed Hivegrid binding: {what}"))
    if name == "leader" and mods <= {"SHIFT"}:
        out.append(("error", "the leader needs CTRL or ALT"))
    if wez_defaults and cid in wez_defaults:
        out.append(("warn", f"replaces the WezTerm default: {wez_defaults[cid]}"))
    return out


# ---- accounts ----------------------------------------------------------------------


def home():
    return Path(os.environ.get("HOME") or str(Path.home()))


def tilde(path):
    p, h = str(path), str(home())
    return "~" + p[len(h):] if p == h or p.startswith(h + "/") else p


def untilde(path):
    return str(home()) + path[1:] if path.startswith("~/") or path == "~" else path


def omp_profiles():
    root = home() / ".omp" / "profiles"
    try:
        return sorted(p.name for p in root.iterdir() if p.is_dir())
    except OSError:
        return []


def pair_profile(label, profiles):
    """The omp profile of the same name as the account label, when ~/.omp/profiles/<label> exists."""
    return label if label in profiles else None


def discover_accounts():
    """Claude config dirs holding a .credentials.json (existence only, never read)."""
    profiles = omp_profiles()
    found = []
    try:
        names = sorted(p.name for p in home().iterdir())
    except OSError:
        names = []
    for name in names:
        if name == ".claude":
            label = "main"
        elif name.startswith(".claude-") and len(name) > len(".claude-"):
            label = name[len(".claude-"):]
        else:
            continue
        d = home() / name
        if d.is_dir() and (d / ".credentials.json").exists():
            found.append({"label": label, "profile": pair_profile(label, profiles), "dir": tilde(d),
                          "auto": True, "hidden": False})
    found.sort(key=lambda a: (a["label"] != "main", a["label"]))
    return found


def merge_accounts(existing, discovered):
    """Keep manual entries and the edits made to auto ones; add newly found directories; drop auto
    entries whose directory no longer holds credentials."""
    found = {a["dir"]: a for a in discovered}
    out, seen = [], set()
    for a in existing or []:
        if a.get("auto") and a["dir"] not in found:
            continue
        out.append(dict(a))
        seen.add(a["dir"])
    for a in discovered:
        if a["dir"] not in seen:
            out.append(dict(a))
    return out


def remove_account(accounts, index):
    """Delete a manual entry; an entry whose directory is still discoverable is hidden instead, so a
    rescan does not bring it back. -> message."""
    a = accounts[index]
    if a.get("auto") or any(d["dir"] == a["dir"] for d in discover_accounts()):
        a["hidden"] = True
        return f"{a['label']} hidden (its directory still holds credentials)"
    del accounts[index]
    return f"{a['label']} removed"


def valid_label(label, accounts, own=None):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,15}", label or ""):
        return "label: letters, digits, . _ - (1-16 chars)"
    for i, a in enumerate(accounts):
        if a["label"] == label and i != own:
            return "label already used"
    return None


def cache_dir():
    return Path(os.environ.get("CLAUDE_QUOTA_CACHE_DIR") or home() / ".cache" / "wezterm-claude-quota")


def current_entry(e, now):
    """A cache entry as of `now`: a window whose reset time has passed has reset, so it reads 0 %
    with no reset time until a fetch says otherwise (the token may be unusable, so none comes)."""
    out = dict(e)
    for key in ("five_hour", "seven_day"):
        w = e.get(key)
        if isinstance(w, dict) and isinstance(w.get("reset_at"), (int, float)) and w["reset_at"] < now:
            out[key] = {"pct": 0.0, "reset_at": None}
    return out


def quota_state(label, now):
    """One word / numbers from the helper's cache; the cache holds no credentials."""
    try:
        e = json.loads((cache_dir() / f"{label}.json").read_text())
    except (OSError, ValueError):
        return "no data"
    e = current_entry(e, now)
    bits = []
    for key, tag in (("five_hour", "5h"), ("seven_day", "7d")):
        w = e.get(key)
        if isinstance(w, dict) and isinstance(w.get("pct"), (int, float)):
            bits.append(f"{tag} {w['pct']:.0f}%")
    state = str(e.get("state", "?"))
    word = {"login": "login needed", "expired": "token expired"}.get(state, state.replace("_", " "))
    if bits:
        return " ".join(bits) + ("" if state == "ok" else f" · {word}")
    return word


# ---- settings file -----------------------------------------------------------------


def settings_path(override=None):
    return Path(override or os.environ.get("HIVEGRID_SETTINGS") or home() / ".config" / "hivegrid" / "settings.json")


class Settings:
    def __init__(self, path):
        self.path = Path(path)
        self.data = {}
        self.exists = False
        self.newer = False
        self.error = None
        try:
            text = self.path.read_text(encoding="utf-8")
            self.exists = True
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("top level is not an object")
            self.data = data
            self.newer = isinstance(data.get("version"), int) and data["version"] > SCHEMA
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            self.error = f"{self.path.name} is unreadable ({exc}); saving would replace it"

    def theme(self):
        t = self.data.get("theme")
        preset = t.get("preset") if isinstance(t, dict) else None
        if preset not in PRESETS:
            preset = DEFAULT_PRESET
        accent = t.get("accent") if isinstance(t, dict) and isinstance(t.get("accent"), str) else None
        try:
            return theme_for(preset, accent if accent and HEX_RE.match(accent) else None)
        except ValueError:
            return theme_for(preset)

    def keys(self):
        k = self.data.get("keys")
        return effective_keys(k if isinstance(k, dict) else {})

    def stored_accounts(self):
        """The account list as saved, or None when the file has none."""
        stored = self.data.get("accounts")
        if not isinstance(stored, list):
            return None
        return [{"label": str(a["label"]), "profile": a.get("profile") or None, "dir": str(a["dir"]),
                 "auto": bool(a.get("auto")), "hidden": bool(a.get("hidden"))}
                for a in stored if isinstance(a, dict) and "label" in a and "dir" in a]

    def accounts(self):
        return merge_accounts(self.stored_accounts(), discover_accounts())

    def web(self):
        return web_settings(self.data.get("web"))

    def effective(self):
        return {"version": SCHEMA, "theme": self.theme(), "keys": self.keys(), "accounts": self.accounts(),
                "web": self.web()}

    def write(self, theme, keys, accounts, web):
        data = dict(self.data)
        data["version"] = SCHEMA
        data["theme"] = theme
        data["keys"] = {n: v for n, v in keys.items() if v != normalize(BINDINGS[n][1])}
        data["accounts"] = accounts
        # Only non-default web values are written (like keys); entries this version does not know stay.
        web_data = dict(data["web"]) if isinstance(data.get("web"), dict) else {}
        for k, v in web.items():
            if v == WEB_DEFAULTS[k]:
                web_data.pop(k, None)
            else:
                web_data[k] = v
        if web_data:
            data["web"] = web_data
        else:
            data.pop("web", None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".settings.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o644)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self.data, self.exists, self.error = data, True, None


class ThemeWatch:
    """The saved palette, re-read when settings.json changes. Long-running views (the bar, the git
    view) poll it, so a Save in the settings panel restyles them without a restart."""

    def __init__(self, path=None):
        self.path = settings_path(path)
        self.stamp = False  # never a real stamp: the first poll always reads
        self.palette = None

    def poll(self):
        """-> the palette when it differs from the last one returned (the first call always
        returns it), else None. Costs one stat() while the file is unchanged."""
        try:
            st = self.path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = None
        if stamp == self.stamp:
            return None
        self.stamp = stamp
        palette = Settings(self.path).theme()["palette"]
        if palette == self.palette:
            return None
        self.palette = palette
        return palette


# ---- about -------------------------------------------------------------------------


def about_info():
    exe = find_wezterm() or ""
    info = {"executable": exe or "(not found)", "version": "?", "clicks": False, "overlay": False}
    if exe:
        try:
            r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5)
            info["version"] = r.stdout.strip() or "?"
        except (OSError, subprocess.TimeoutExpired):
            pass
        gui = Path(exe).parent / "wezterm-gui"
        for path in (gui, Path(exe)):
            try:
                with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    info["clicks"] = info["clicks"] or mm.find(b"wezterm-status-click:") >= 0
                    info["overlay"] = info["overlay"] or mm.find(b"ShowOverlayCommand") >= 0
            except (OSError, ValueError):
                continue
    info["custom"] = info["clicks"] and info["overlay"]
    return info


# ---- editing state shared by both front ends -----------------------------------------


class Model:
    """What the panel edits: theme choice, bindings and accounts, with dirty tracking and save."""

    def __init__(self, settings):
        self.settings = settings
        t = settings.theme()
        self.theme = {"preset": t["preset"], "accent": t["accent"]}
        self.keys = settings.keys()
        self.accounts = settings.accounts()
        self.web = settings.web()
        self.baseline = self.snapshot()

    def snapshot(self):
        return json.dumps([self.theme, self.keys, self.accounts, self.web], sort_keys=True)

    @property
    def dirty(self):
        return self.snapshot() != self.baseline

    def current_theme(self):
        return theme_for(self.theme["preset"], self.theme["accent"])

    def save(self):
        """-> (ok, message)."""
        if self.settings.newer:
            return False, "settings.json was written by a newer version; not overwriting it"
        try:
            self.settings.write(self.current_theme(), self.keys, self.accounts, self.web)
        except OSError as exc:
            return False, f"save failed: {exc.strerror or exc}"
        self.baseline = self.snapshot()
        return True, f"saved {tilde(self.settings.path)}; WezTerm reloads it"
