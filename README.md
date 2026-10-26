<p align="center"><img src="assets/icon.png" alt="Hivegrid logo" width="128"></p>

# Hivegrid

A WezTerm workspace for running many [`omp`](https://github.com/can1357/oh-my-pi) AI coding agents side by side in a pane grid, with per-agent state visible at a glance.

![Hivegrid UI with sidebar and main-side layout](assets/screenshots/hivegrid-ui.png)

![Nine omp sessions in a 3x3 grid](assets/screenshots/hivegrid-agents.png)

## Features

- Overview dock at the right edge of a tab (`LEADER+o`): every grid pane with its number, state (working, waiting, ask, finished, exited), directory, time in that state and omp's session title; click a row or press its digit to jump there, `▦` equalizes the grid. A pane picker (`LEADER+g`) lists every agent too, and background panes raise bell toasts. omp panes report their state exactly, through the run-state glyph omp puts in the terminal title.
- `omp-grid` launcher: equal grids, main-plus-side and column layouts, per-pane directories, optional new tab.
- Resize, pane and broadcast key tables, and an equalize command that evens out all dividers.
- Broadcast mode that types into every pane of the tab, with a visible `BROADCAST` marker.
- System stats in the header: CPU, RAM, disk, GPU, each with its percentage, a slim bar and, when the window is wide enough, the absolute amount (`11.7G/31G`, `165G free`).
- Claude Code usage (5h and 7d windows) for every account as a header chip with numbers and slim bars (an account that needs a login shows only an amber `login` chip); accounts are discovered, so a new one appears on its own. Clicking an account starts omp with it, or moves the omp already running in that pane to it with the same chat.
- Panels inside the window (the gear button of the left bar, `LEADER+,`, `+` menu): settings (themes, key bindings, accounts), usage history charts, agent overview and a system monitor.
- Permanent narrow bar at the left of every tab with buttons for the file explorer, the git view and the settings panel. The explorer is a yazi sidebar; the git view lists every repository of the working directory (like VS Code's Source Control repositories) with branch, ahead/behind and change counts, and opens lazygit in the one you pick. A coloured dot gives the working-tree state (green clean, amber changes, red conflicts) and repositories with GitHub Actions workflows show `CI ✓` / `CI ✗` / `CI ◔` for the latest runs on the checked-out branch (read through `gh` every minute; `?` when `gh` cannot tell, nothing without workflows). It follows the active pane: focusing a pane in another directory (or a `cd` there) restarts it in that directory. `Ctrl+F` / `Ctrl+G` do the same as the buttons.
- Smart `Ctrl+C` (copy with a selection, interrupt otherwise), `Ctrl+V` paste, `Ctrl+Arrow` splits, `Alt+Arrow` focus.
- Dark-blue theme with rounded tabs level with the header pills, the app icon's honeycomb of seven hexagons drawn at the left of the header (click it to open the agent hive), wide padding, and dimmed inactive panes.

## Install

Requires WezTerm 20240203 or newer, Python 3 and curl.

```sh
git clone git@github.com:leongreiner/hivegrid.git
cd hivegrid
./install.sh [--tools]
```

The installer copies `config/*.lua` to `~/.config/wezterm/`, `config/yazi/` to `~/.config/hivegrid/yazi/` (the explorer's own yazi config; a global `~/.config/yazi` is never read or changed), `bin/` to `~/.local/bin/`, the icon, and a `Hivegrid` launcher that overrides the `org.wezfurlong.wezterm` desktop entry. Differing existing files are saved once as `*.bak.<timestamp>`. `--tools` also downloads the latest `yazi`, `ya` and `lazygit` into `~/.local/bin`. No sudo is used. `./sync.sh` copies the live setup back into the repo for maintenance.

The usage chip in the header needs each account logged in at least once: an omp profile (`omp --profile=<name>`, then `/login` with Anthropic) and/or Claude Code (`CLAUDE_CONFIG_DIR=~/.claude-<name> claude`, which creates `~/.claude-<name>/.credentials.json`; that file is also how an account is discovered). An account paired with an omp profile (see Settings, Accounts) stays logged in as long as omp runs, because the chip reads omp's own token; without a profile it follows the Claude Code token, which only `claude` refreshes and which shows `login` once it expires (see Claude quota). The state is only read, never changed.

## Keys

`LEADER` is `Ctrl+Space`.

| Group | Keys | Action |
| --- | --- | --- |
| Panes | `Ctrl+Shift+\|` / `Ctrl+Shift+_` | split right / down |
| | `Ctrl+Arrow` | split in that direction |
| | `Alt+Arrow`, `Alt+h/j/k/l` | focus pane |
| | `Alt+z` | zoom pane |
| | `LEADER+r` | resize mode (arrows or `h/j/k/l`, `Esc`/`Enter` leaves) |
| | `LEADER+p` | pane mode: `r`/`R` rotate, `g` select, `s`/`S` swap, `x` close, `t` to new tab, `z` zoom, `e` equalize, `Esc`/`q` leaves |
| Grid | `LEADER+e`, `Ctrl+Shift+E` | equalize panes (also the `▦` button of the overview dock) |
| | `LEADER+1` / `2` / `3` | new tab with `omp` / 2x2 grid / 3x3 grid (also in the `+` right-click menu) |
| Agents | `Alt+n`, `LEADER+n` | jump to the next pane needing attention |
| | `LEADER+o` | show or hide the overview dock (right edge) |
| | `Alt+]`, `Alt+[` | next / previous agent pane |
| | `LEADER+g` | pane picker |
| | `LEADER+b` | toggle broadcast typing to all panes of the tab |
| Sidebar | `Ctrl+F` / `Ctrl+G` | file explorer / git view |
| | `LEADER+Tab` | hide or show, remembering the view |
| | `Enter` / click, `r`, `q` in the git view | open lazygit in the selected repository (quit lazygit to return to the list), rescan, close; `j`/`k` or arrows move |
| | `Ctrl+C` in the explorer | copy the absolute path of the hovered file or folder, or of all selected entries one per line |
| | double click in the explorer | open a terminal in that folder (a file's folder), split right of the last active grid pane, or below it when that pane is narrow; it takes focus |
| | drag a column border in the explorer | resize the parent / current / preview columns (the pointer turns into a resize cursor over a border; Hivegrid build only) |
| Panels | `LEADER+,` | settings panel |
| | `LEADER+h` | usage history panel |
| | `LEADER+a` | agent hive panel |
| | `LEADER+u`, then `1`-`9` | start (or switch) omp with that account |
| Misc | `Ctrl+C` / `Ctrl+V` | smart copy or interrupt / paste |
| | `Alt+s` | toggle system stats |
| | `Alt+q` | Claude quota selector with all accounts; `LEADER+q` hides or shows the header chip |
| | `Ctrl+Shift+R` | reload config |

Leader, sidebar toggles, the overview dock toggle, `Ctrl+Shift+E` (equalize), splits, the account launcher and the panel keys can be rebound in the settings panel (Keys); everything else is fixed.

## Panels

`bin/hivegrid-panel [settings|usage|agents|monitor|hive]` is a terminal UI with mouse support: click the tabs, buttons and rows, or use the keys shown at the bottom (`F1`-`F5` and `Tab` switch panels, `q` or `Esc` closes; in the hive `Tab` selects agents, so use the F-keys or click the tabs). On the Hivegrid build it opens as an overlay over the active tab (the panes keep running underneath and come back when it closes); on stock WezTerm it opens in a new tab.

| Open it with | Panel |
| --- | --- |
| the gear button of the left bar, `LEADER+,`, `+` menu `Hivegrid settings` | settings |
| right-click an account pill, `LEADER+h`, `+` menu `Usage history` | usage |
| `+` menu `Agent overview` | agents |
| click the header logo, `LEADER+a` | hive |
| click the system group (CPU, RAM, DSK, GPU), `+` menu `System monitor` | monitor |

- **Settings** writes `~/.config/hivegrid/settings.json`; the WezTerm config watches it, so *Save* applies at once (a missing file means today's defaults).
  - *Theme*: Hivegrid Navy (the default), Midnight Black, Slate and Forest, with colour swatches, a live preview and an accent colour (quick swatches or any `#rrggbb`). A preset sets the header and tab palette and the terminal colours.
  - *Keys*: click a binding (or `Enter`) and press the new keys. Conflicts with another setting, with the fixed Hivegrid bindings and with WezTerm defaults (`wezterm --skip-config show-keys`) are shown inline; the first two refuse, the last asks. While recording, the config's own bindings are suspended (user var `hivegrid_capture`), so bound chords such as `Ctrl+F` reach the panel. A terminal reports only Ctrl+letter, Alt+key and plain keys; other chords (`CTRL+SHIFT+k`) are typed with *Type it*. *Reset* restores a default.
  - *Accounts*: add, rename, pick the omp profile, hide or remove, rescan. Every `~/.claude` and `~/.claude-<name>` directory that contains a `.credentials.json` is an account (existence only; the file is never read), labelled `main` for `~/.claude` and `<name>` otherwise. omp profiles pair by convention: `main` with `work`, `2` with `team`, `private` with `private`; any other label pairs with `~/.omp/profiles/<label>` when it exists, and otherwise the panel asks for a profile. Entries carry `auto: true/false`, so manual ones survive rescans; removing an entry whose directory still holds credentials hides it instead. A directory the list does not mention yet is shown in the header anyway and offered under *Found on this machine*. Hidden or removed accounts leave the header, the quota refresh and the `LEADER+u` keys at once.
  - *About*: WezTerm version and whether the Hivegrid build (clickable segments, overlay action) is running.
- **Usage** charts the 5h and 7d utilisation of the chosen account over 6 h, 24 h, 7 d or 30 d with window-reset markers, from `~/.cache/wezterm-claude-quota/history.jsonl`, which `claude-quota refresh` appends to (percentages and reset times only, at most one sample per account every two minutes, trimmed to about 30 days, mode 0600). It says when the history starts later than the range.
- **Agents** lists the panes of the window (`wezterm cli list`, the multiplexer of the terminal it runs in): process, omp profile, how long the foreground process has run, directory and title. A click or `Enter` jumps to the pane and closes the panel.
- **Monitor** graphs CPU, RAM, disk and GPU (`nvidia-smi` or Jetson `gpu.0/load`) sampled every second while the panel is open, over 1, 5 or 15 minutes.
- **Hive** shows every agent pane of the window (all tabs) as a hex cell, auto-arranged in a spiral that keeps each agent's slot when another is deleted (`c` compacts, `t` toggles hex/tile). Colour and glyph give the state: blue ↻ working, amber ◔ busy (the turn ended but background jobs run, or a tool such as a build or test has run for 8 s or more), green ✓ done, violet ? needs you (approval, question or plan review), grey · idle, ○ exited. A cell shows nothing more. Selecting one opens a larger popover with the directory, state, age and reason, the pending question, the last screen lines and a prompt line (Enter sends it to the pane). The prompt is disabled while the agent waits for an answer, since typed text would land in that dialog. Plan reviews can be answered from the popover; approvals and questions are shown and answered in the pane (`j` jumps there). `n` adds an agent (a split of the selected agent's tab while it holds fewer than 4 agents, otherwise a new tab) and `d` removes one (Ctrl+D, then the pane is closed; confirmation while the agent is working). `done` stays green until you acknowledge it (`a`) or jump to or prompt that agent.
  - The exact states come from the omp extension `ext/hivegrid-agent-state.ts`, which `install.sh` copies into `~/.omp/agent/extensions/` and every profile's `extensions/`. It writes `$XDG_RUNTIME_DIR/hivegrid-agents/<pane id>.json`; the hive reads those files and accepts plan-review answers as `<pane id>.cmd.*.json`. Without the extension, or for a pane with a stale file, the state comes from omp's title glyph (working, idle, needs you only), and for other agents (claude, codex, ...) from the foreground process.

Charts are drawn with PIL as inline images (the iTerm2 image protocol, which WezTerm speaks) when `python3-pil` is installed and the terminal is WezTerm; otherwise, or with `HIVEGRID_NO_IMAGES=1`, they are braille plots. The panels need only Python 3.

## Grid launcher

`omp-grid` runs inside a WezTerm pane (or anywhere with `--tab`) and starts one command, default `omp`, per pane.

```sh
omp-grid 2 3 ~/work                        # 2 rows x 3 columns in ~/work
omp-grid --layout main-side 3 --name-panes # one main pane, three stacked on the right
omp-grid --layout columns 4 --tab --dirs ~/a,~/b  # new tab, 4 columns, directories cycled
```

`--cmd CMD` swaps the command, `--dir DIR` sets the directory; `omp-grid --help` lists everything.

## Claude quota

`claude-quota` fetches Claude Code usage for each account and caches only the percentages and reset times in `~/.cache/wezterm-claude-quota/` (plus `history.jsonl`, see Panels). The header reads that cache; the network is never touched from the GUI thread. It reads the OAuth token in-process, first from the account's paired omp profile (the `anthropic` OAuth row in `~/.omp/profiles/<profile>/agent/agent.db`, opened read-only; omp keeps it fresh while it runs) and otherwise from the `.credentials.json` of the account directory (which only the `claude` CLI refreshes). It calls the usage endpoint with caching and backoff, never refreshes, stores or logs tokens, and shows `login` only when neither source holds a valid token. Run `claude-quota refresh` or `claude-quota show` by hand to check it.

The accounts are, in order of precedence: `CLAUDE_QUOTA_DIRS` (or `--dirs`), a comma-separated list; the list in `~/.config/hivegrid/settings.json` (hidden entries skipped) plus every `~/.claude-*` directory holding a `.credentials.json` that the list does not mention; or, without a settings file, those discovered directories alone.

### Switching accounts without losing the chat

Clicking an account pill (or `Alt+q`, or `LEADER+u <n>`) in a pane that runs a shell starts `omp --profile=<profile>`. In a pane that already runs omp under another profile it moves the chat over:

1. `omp-switch locate` finds the session (omp records each terminal's session in `<profile>/agent/terminal-sessions/pts-N`; only paths are read, never the conversation);
2. omp is asked to quit with `Ctrl+D` and has five seconds; with unsent text in its prompt it stays, the switch is abandoned and a toast says so (never `kill`);
3. `omp-switch transfer` copies the finished session file, its artifact directory and the blobs it references into the other profile under the same names, without overwriting anything (a copy of an older part of the same chat is brought up to date; a different chat under that name aborts) and without touching the first profile's files;
4. `omp --profile=<new> --resume '<copy>'` continues the same chat (a fresh `omp --profile=<new>` when no message was written yet; a `cd` first when the shell is not where omp ran).

If the pane looks busy (agent working or asking), a dialog asks "switch anyway?" first, default No; Yes sends `Esc` (omp's interrupt) before quitting.

## Custom WezTerm build

`wezterm/` is a submodule pointing at a private WezTerm fork (branch `hivegrid`) that adds clickable header segments (`status-click`), the `ShowOverlayCommand` key assignment, which runs a program in an overlay over the active tab until it exits (the panels), and the drawn header mark. The mark is the option `tab_bar_brand = { enabled, size, margin_left, margin_right, colors = { center, center_outline, glow, ring, ring_outline } }` (`theme.BRAND_MARK` in `config/theme.lua` sets the icon's colours; the centre cell is blue and the six outer cells show agent-state colours), and `tab_bar_floating_tabs` puts tabs and `+` on the pills' line. The config also runs on stock WezTerm 20240203: clicks are ignored, the panels open in a new tab, the header shows a row of `⬢` glyphs instead of the drawn mark and the tabs rise from the bar's lower edge. To build it without sudo, run `git submodule update --init` and then `wezterm/build-hivegrid.sh`; `wezterm/HIVEGRID.md` has the details.

## Notes and limits

- Agent state is shown in the overview dock, the pane picker and bell toasts, not in the header or the tab titles; a background tab's state is only visible after switching to it. On stock WezTerm the header is not clickable.
- The overlay covers the whole active tab; there is no centred modal mode.
- Moving an omp chat to another account needs omp's per-terminal session record (omp 18); with several omp processes in one terminal it refuses rather than guess. The busy check uses the same heuristic as the agent states.
- omp's state comes from the run-state glyph in its terminal title (`tui.titleState`, on by default in omp); when that is off, and for other agents, detection is heuristic: it combines the foreground process, screen text markers, the bell and screen activity.
- The sidebar bar spans the full window height on the Hivegrid build (a top-level split); on stock WezTerm 20240203, which mis-sizes top-level splits, it and the overview dock sit beside the leftmost / top-right pane only. The sidebar view itself is docked beside the leftmost pane.
- The sidebar views start in the working directory of the pane they were opened from (the git view scans it for repositories: the one around it and those below it, `HIVEGRID_GIT_DEPTH` levels deep, default 3; with a single repository it opens lazygit there directly). A pane whose shell reports a path from another host, such as an ssh session, uses the directory of its local foreground process instead.
- The explorer copies paths with the terminal clipboard escape (OSC 52), which WezTerm accepts, and also through `wl-copy`, `xclip` or `xsel` when present.
- `Ctrl+V` paste is not fanned out in broadcast mode, because the clipboard is not readable from Lua.

## License

MIT
