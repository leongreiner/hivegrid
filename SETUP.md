# Setting up Hivegrid (for an AI coding agent)

You are an AI coding agent and a user asked you to install Hivegrid for them. Read this file completely, then follow it. Hivegrid is a WezTerm workspace that runs many coding agents in a grid of panes ([README](README.md)).

## Rules

- Install into the user's home directory. The installer never uses sudo; you do not use it either unless the user agrees to a specific command.
- Never read, print, copy or ask for tokens or credential files (`~/.claude*/.credentials.json`, anything under `~/.omp`). Logging in is something the user does in their own terminal.
- Four things are the user's choice: **the colour theme**, **which agent Hivegrid starts** (omp or Claude Code), **their accounts** (each must log in once) and **a browser for web panes**. Ask them in step 3. Do not pick for them; say what the default is.
- Hivegrid is a GUI program. You cannot see it from a shell: say what you changed and let the user look.

## 1. Check the machine

```sh
uname -sm                                    # Linux x86_64 is needed for the pre-built WezTerm
command -v curl tar python3 unzip            # curl and tar are required; python3 runs the panels; unzip unpacks the font
```

If `curl` or `tar` is missing, the installer stops and prints the command that installs it. On another architecture it still installs the config and uses a stock `wezterm` you already have (reduced features: no clickable header, no web pane); building the fork is in the README under "Custom WezTerm build".

## 2. Install

```sh
curl -fsSL https://raw.githubusercontent.com/leongreiner/hivegrid/main/install.sh | bash
```

Add `-s -- --tools` after `bash` to also download `yazi`, `ya` and `lazygit` (the file explorer and git view in the left bar need them). The installer puts everything under `~/.config/wezterm`, `~/.local/bin`, `~/.local/opt/hivegrid-wezterm` and `~/.local/share`, saves a differing existing file once as `*.bak.<timestamp>`, and prints a warning for anything that is missing. Read its output:

- `downloading the Hivegrid WezTerm build` then `installed ...`: the full build is in place and `hivegrid` is the command to start it.
- `download failed` or `there is no pre-built Hivegrid WezTerm for ...`: it carries on with the stock `wezterm` if there is one, and stops if there is none. Tell the user; do not hide it.
- `warning: web panes need a Chromium-family browser`: see "Browser" below.
- `note ~/.local/bin is not on PATH`: tell the user to add it (`export PATH="$HOME/.local/bin:$PATH"` in their shell profile).

## 3. Ask the user, then apply

Ask all four questions in one message and wait for the answers.

### Theme

Hivegrid ships five themes: **Hivegrid Graphite** (the default), **Hivegrid Navy**, **Midnight Black**, **Slate** and **Forest**. The theme sets the header, tab, bar, dock and terminal colours.

### Agent

Hivegrid starts either **omp** (the default) or **Claude Code** in new tabs (`LEADER+1`, `2`, `3`, where `LEADER` is `Ctrl+Space`), in the `+` menu and for the account pills in the header. The program must be installed and on `PATH`: check `command -v omp` or `command -v claude`, and if it is missing, tell the user to install it from the agent's own documentation (do not guess a download).

With omp the agent states in the dock are exact and a running chat can move to another account; with Claude Code the states are read from the screen and a chat does not move between accounts or come back after a restart.

### Accounts

The header shows each Claude account's usage next to a `login` chip until the account is logged in. The user logs in once per account, in a terminal, themselves:

```sh
claude                                       # the default account (~/.claude)
CLAUDE_CONFIG_DIR=~/.claude-work claude      # another account; the directory name after "claude-" is its label
omp --profile=work                           # then /login: pairs the omp profile "work" with the account "work"
```

An account is every `~/.claude` or `~/.claude-<name>` directory that holds a `.credentials.json` (Hivegrid only checks that it exists). It pairs with the omp profile of the same name. Ask how many accounts they have and what to call them. Do not log in for them.

### Browser

Web panes (a page as a pane of the grid, next to the agents) need the Hivegrid WezTerm build and a Chromium-family browser: Google Chrome, Chromium, Brave, Microsoft Edge or Vivaldi. Firefox does not work, and snap or Flatpak browsers are not offered (their sandbox hides the profile directory); on Ubuntu the `chromium` package is a snap, so Google Chrome's `.deb` or a distro Chromium that is not a snap is the way. Check with `command -v google-chrome-stable google-chrome chromium chromium-browser`. With a browser installed nothing needs activating: the browser button appears in the left bar and `LEADER+w` then `n` opens a web pane. Another browser than the first one found is chosen in the settings (Web, Browser) or with `HIVEGRID_CHROME=/path/to/binary`.

### Applying the answers

The user can do all of it in Hivegrid: `LEADER+,` opens the settings (Theme, Keys, Accounts, Web, Agent), `s` saves and WezTerm reloads at once. You can write the same file yourself. Do not edit `~/.config/hivegrid/settings.json` by hand for the theme: the file stores the whole palette, which only the model writes. Use the installed model instead (set `THEME` to one of `hivegrid-graphite`, `hivegrid-navy`, `midnight-black`, `slate`, `forest` and `AGENT` to `omp` or `claude`):

```sh
THEME=slate AGENT=claude python3 - <<'PY'
import os, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.expanduser("~/.local/bin"))
from hivegrid_settings_core import AGENTS, PRESETS, Model, Settings, settings_path

model = Model(Settings(settings_path()))
if model.settings.newer:
    sys.exit("settings.json was written by a newer Hivegrid; not touching it")
theme, agent = os.environ["THEME"], os.environ["AGENT"]
if theme not in PRESETS:
    sys.exit(f"THEME must be one of: {', '.join(PRESETS)}")
if agent not in AGENTS:
    sys.exit(f"AGENT must be one of: {', '.join(AGENTS)}")
model.theme = {"preset": theme, "accent": PRESETS[theme]["palette"]["accent"]}
model.agent = agent
print(model.save()[1])
PY
```

A wrong `THEME` or `AGENT` stops with a message that lists the valid names and writes nothing. The browser needs no settings entry unless it is not the first one found.

## 4. Verify and hand over

```sh
command -v hivegrid                           # ~/.local/bin/hivegrid
ls ~/.config/wezterm/wezterm.lua              # the config is in place
cat ~/.config/hivegrid/settings.json          # only if you applied settings: it holds "agent" and the theme
```

Then tell the user, in this order: Hivegrid is installed; start it from the application menu or with `hivegrid` (restart it, or press `Ctrl+Shift+R`, if it was already running); `Ctrl+Space` then `1` opens a tab with their agent; each account must log in once (give them the exact commands for their accounts); the theme and agent they chose, and that `Ctrl+Space` then `,` changes them; the browser button or `Ctrl+Space`, `w`, `n` for web panes, or which browser is still missing. Keys: [docs/keys.md](docs/keys.md). Everything the installer does: [docs/install.md](docs/install.md).

## If something is wrong

| Symptom | Cause and fix |
| --- | --- |
| `hivegrid: command not found` | `~/.local/bin` is not on `PATH`, or the WezTerm download failed (then `wezterm` is the command, with reduced features). Re-run the installer with `--wezterm` once the network works. |
| The window shows no clickable header or no web pane | A stock WezTerm is running, not the Hivegrid build: start `hivegrid`, not `wezterm`. |
| Icons show as boxes | The Nerd Font download failed (no `unzip` or no network): install `unzip` and run the installer again. |
| A `login` chip stays | That account is not logged in yet (see Accounts). |
| New tabs start the wrong agent | The Agent setting; `LEADER+,`, Agent. Panes that already run an agent are not touched. |
| The web pane says no browser | See Browser; the settings (Web, Browser) list what Hivegrid found. |
