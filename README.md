<p align="center"><img src="assets/icon.png" alt="Hivegrid logo" width="128"></p>

# Hivegrid

<p align="center">
  <a href="https://github.com/leongreiner/hivegrid/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/leongreiner/hivegrid/actions/workflows/ci.yml/badge.svg?branch=main"></a>
  <img alt="Status: alpha" src="https://img.shields.io/badge/status-alpha-orange">
  <a href="https://github.com/leongreiner/hivegrid/releases"><img alt="Release" src="https://img.shields.io/github/v/release/leongreiner/hivegrid?include_prereleases&label=release"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue"></a>
  <img alt="Platform: Linux" src="https://img.shields.io/badge/platform-Linux-lightgrey">
  <a href="https://wezterm.org"><img alt="WezTerm 20240203+" src="https://img.shields.io/badge/WezTerm-20240203%2B-4e49ee"></a>
  <a href="CONTRIBUTING.md"><img alt="PRs welcome" src="https://img.shields.io/badge/PRs-welcome-brightgreen"></a>
</p>

> **Alpha software.** Hivegrid works and is used daily, but expect rough edges, changing settings and keys, and bugs. Bug reports and pull requests are very welcome (see [Contributing](#contributing)).

A WezTerm workspace for running many [`omp`](https://github.com/can1357/oh-my-pi) AI coding agents side by side in a pane grid, with per-agent state visible at a glance.

## Demo

<p align="center">
  <a href="assets/demo/hivegrid-demo.mp4"><img src="assets/demo/hivegrid-demo.gif" alt="Hivegrid demo: several omp agents working in a pane grid, the overview dock, account switching, usage and compute history, file explorer, git view, web pane and settings" width="900"></a>
</p>

<p align="center"><em>Highlights, sped up. <a href="assets/demo/hivegrid-demo.mp4">Watch the full demo (3½ minutes, mp4)</a>.</em></p>

The video is recorded by `dev/demo/record.py` from real omp sessions in an isolated sandbox window (fictional accounts and usage, fixture repositories): four agents started with `omp-grid`, the overview dock, auto-tiling and equalize, switching the account of a running chat, usage and system history, the hive, file explorer, git view with CI marks and lazygit, a web pane, and settings with themes.

| | |
| --- | --- |
| ![Four omp agents working in a grid with the overview dock](assets/screenshots/hivegrid-agents.png) | ![Git view listing five repositories with the GitHub CI result](assets/screenshots/hivegrid-git.png) |
| ![Usage history of an account over seven days](assets/screenshots/hivegrid-usage.png) | ![A web page as a pane of the grid](assets/screenshots/hivegrid-web.png) |
| ![Settings panel with colour themes and live preview](assets/screenshots/hivegrid-settings.png) | |


## Features

- Overview dock at the right edge of a tab (`LEADER+o`): every grid pane with its number, state (the hive's states: working, busy, needs you, done, idle, exited, plus shell for a plain shell), directory, time in that state and omp's session title (while the agent works, what it is running); click a row or type its number to jump there (past pane 9 a digit that may start a longer number waits a moment for the next one), or move the selection mark with `j`/`k`, the arrows, `PageUp`/`PageDown` or `g`/`G` and press `Enter` (the mark starts on the active pane, whose number shows in the accent colour; `q`/`Esc` closes the dock), `▦` equalizes the grid and `⊞` arranges it (below). The `✕` at the right end of the row under the pointer (otherwise of the selected row), or `x`/`Delete` for the selected row, closes that pane: at once when nothing is lost (a shell at its prompt, an exited agent, a done or idle omp, whose session is saved), after a question in the row (`close 2 · working?` with *yes* and *no*; `y` closes, any other key or click keeps it) when work would be interrupted (working, busy, needs you, a bell, or a program other than the shell in the foreground). `✕ tab` in the title row, or `X`, closes the whole tab, the bar and the dock with it, after one question that counts the agents at work (`close 6 panes · 1 needs you, 2 working?`). The remaining panes are re-tiled (auto-arrange, below); after `x` the keys stay in the dock with the mark on the next row, after a click they go to the grid pane used last (the closed pane's neighbour if it was that one); closing the last grid pane closes the tab. Rows that do not fit scroll with the wheel; otherwise the list follows the selection. When the panes no longer fit with a blank line between them, every second row is shaded so rows stay apart (the git view does the same). The dock runs from the header to the bottom edge. It opens at 18% of the window, 30 to 48 columns; a width you drag it to is remembered and scales with the window. On the Hivegrid build an open dock follows window resizes, and opening or closing panes never changes its width. A pane picker (`LEADER+g`) lists every agent too, and background panes raise bell toasts. omp panes report their state exactly, through the run-state glyph omp puts in the terminal title.
- Auto-arrange: when a tidy grid gains or loses one pane (a split, a closed pane) the panes are re-tiled into the grid that reads best for their number and the window's shape, e.g. five panes side by side become 3 + 2 in two rows. Panes are moved, never recreated, and keep their numbers. Layouts you built by hand (panes not in aligned rows) and layouts made by `omp-grid` in one go are left alone; `Ctrl+Shift+A` or `⊞` arranges any tab on demand.
- `omp-grid` launcher: equal grids, main-plus-side and column layouts, per-pane directories, optional new tab.
- Resize, pane and broadcast key tables, and an equalize command that evens out all dividers: every split gives its panes equal shares (a column of three beside a column of two: equal columns, equal rows within each). A grid that was even is evened out again when the sidebar view or the dock opens or closes beside it. Rotate and swap move only the grid panes, keeping the layout's geometry; the sidebar and the dock never move.
- Broadcast mode that types into every pane of the tab, with a red `BROADCAST` pill in the header; the resize, pane and account-launch modes show an amber `RESIZE`, `PANE` or `ACCOUNT` pill there while they are active, followed by the mode's keys while there is room, and a `ZOOM` pill shows while the tab has a zoomed pane (a click on it unzooms on the Hivegrid build). The pills stay at any window width; the narrowest header keeps the CPU chip.
- One themed Hivegrid picker for every list to choose from (`LEADER+g`, `Alt+q`, the `+` menu, pane mode `g`/`s`/`S`) and for the account-switch question (No is the default): type to filter (or `/` first), `j`/`k` or the arrows move, a digit picks, `Enter` chooses, `Esc` clears the filter then closes, and the mouse hovers, clicks and scrolls. On stock WezTerm the same lists open in WezTerm's own selector.
- System stats in the header: CPU, RAM, disk, GPU, each with its percentage, a slim bar and, when the window is wide enough, the absolute amount (`11.7G/31G`, `165G free`).
- Claude Code usage (5h and 7d windows) for every account as a header chip with numbers and slim bars (an account that needs a login shows only an amber `login` chip); accounts are discovered, so a new one appears on its own. The account whose omp runs in the pane you are working in is raised and bold, and the highlight follows focus from pane to pane within a second (nothing is highlighted in a pane without omp). Clicking an account starts omp with it, or moves the omp already running in that pane to it with the same chat.
- Panels inside the window (the gear button of the left bar, `LEADER+,`, `+` menu): settings (themes, key bindings, accounts), usage history charts, agent overview and a system monitor.
- Web pane (the Hivegrid WezTerm build and Google Chrome or Chromium): a page in a pane of the grid, next to the agents. `LEADER+w`, then `n` asks for a URL (or search words) and opens it beside the last grid pane; clicking an `http(s)` link in a terminal pane opens it in the tab's web pane (Settings, Web: or leave links to the system browser). The page is a pane like any other: it is numbered, split, zoomed (`Alt+z`), arranged, rotated and swapped with the terminals, it shows in the overview dock and the pickers as `◍ web` with its host and page title, and broadcast never types into it. Mouse, wheel, keys and paste go to the page; Hivegrid's own keys (`Alt+h/j/k/l`, the leader, `Ctrl+V`) win. `wezterm cli split-pane --url URL [--profile NAME] [--private]` and `+ web` in the agent hive open one from a script or the hive. Each profile name is one headless Chrome (cookies and logins in `~/.local/share/hivegrid/web/profile-<name>`; a private pane forgets them when it closes).
- Permanent narrow bar at the left of every tab with buttons for the file explorer, the git view, the overview dock and the settings panel. The explorer is a yazi sidebar; the git view lists every repository of the working directory (like VS Code's Source Control repositories) with branch, ahead/behind, change counts and a merge, rebase, cherry-pick, revert or bisect in progress, and opens lazygit (in Hivegrid colours; a theme in your own lazygit config wins) in the one you pick. A coloured dot gives the working-tree state (green clean, amber changes, red conflicts), the title line the scanned directory and how many repositories are in each state, and repositories with GitHub Actions workflows show a `✓` / `✗` right after the dot for the latest runs on the checked-out branch, a spinning amber `⠋` while one is unfinished (read through `gh` every 2 minutes for the rows in view, every 10 minutes for the others and every 15 s while a run is unfinished, which keeps a workspace of a hundred repositories at about a fifth of GitHub's hourly API limit; `–` when the branch has no runs or only cancelled or skipped ones, `?` when `gh` cannot tell, nothing without workflows). It follows the active pane: focusing a pane in another directory (or a `cd` there) restarts it in that directory. `Ctrl+F` / `Ctrl+G` do the same as the buttons.
- Hover feedback on the bar, the overview dock and the git view: the pointer turns into a hand over the bar's buttons and the git view's rows, and a hovered button (or dock or git view row, or `⊞` / `▦`) fades to a lighter shade in about 150 ms and back in about 200 ms (only while it fades does anything redraw). The hover also ends when the pointer leaves the bar, dock or git view by any route, because the Hivegrid build tells the program (below). Stock WezTerm sends no hover to a pane that has no focus, so there the bar and dock stay plain.
- Smart `Ctrl+C` (copy with a selection, interrupt otherwise), `Ctrl+V` paste, `Ctrl+Arrow` splits, `Alt+Arrow` focus.
- Dark-blue theme with rounded tabs level with the header pills, the app icon's honeycomb of seven hexagons drawn at the left of the header (click it to open the agent hive), wide padding, and dimmed inactive panes.

## Install

Requires WezTerm 20240203 or newer, Python 3, curl and unzip; the optional web pane also needs the Hivegrid WezTerm build and Google Chrome or Chromium.

```sh
git clone https://github.com/leongreiner/hivegrid.git
cd hivegrid
./install.sh [--tools]
```

The installer copies `config/*.lua` to `~/.config/wezterm/`, `config/yazi/` to `~/.config/hivegrid/yazi/` (the explorer's own yazi config; a global `~/.config/yazi` is never read or changed; the explorer runs from `~/.cache/hivegrid/yazi/`, which links that config and adds a theme generated from the current Hivegrid colour theme), `bin/` to `~/.local/bin/`, the icon, and a `Hivegrid` launcher that overrides the `org.wezfurlong.wezterm` desktop entry. Differing existing files are saved once as `*.bak.<timestamp>`. `--tools` also downloads the latest `yazi`, `ya` and `lazygit` into `~/.local/bin`. No sudo is used. `./sync.sh` copies the live setup back into the repo for maintenance.

The installer also downloads [DejaVu Sans Mono Nerd Font](https://github.com/ryanoasis/nerd-fonts) (the `DejaVuSansM Nerd Font Mono` family, DejaVu Sans Mono patched with Nerd Font icons and with the same cell width) into `~/.local/share/fonts/DejaVuSansMNerdFont/` and refreshes the font cache. WezTerm uses it for the terminal text and the header, and the charts draw with it; without it, plain DejaVu Sans Mono is used and Nerd Font icons show as boxes. To have omp draw its icons with Nerd Font glyphs, set `symbolPreset: nerd` in omp's settings.

The usage chip in the header needs each account logged in at least once: an omp profile (`omp --profile=<name>`, then `/login` with Anthropic) and/or Claude Code (`CLAUDE_CONFIG_DIR=~/.claude-<name> claude`, which creates `~/.claude-<name>/.credentials.json`; that file is also how an account is discovered). An account paired with an omp profile (see Settings, Accounts) stays logged in as long as omp runs, because the chip reads omp's own token; without a profile it follows the Claude Code token, which only `claude` refreshes: once it expires the last numbers stay, marked `~`, and each Claude Code session of that account reports fresh ones through its status line hook; `login` appears only after a day without either, or when the API rejects the token (see Claude quota). The state is only read, never changed.

## Keys

`LEADER` is `Ctrl+Space`.

| Group | Keys | Action |
| --- | --- | --- |
| Panes | `Ctrl+Shift+\|` / `Ctrl+Shift+_` | split right / down |
| | `Ctrl+Arrow` | split in that direction |
| | `Alt+Arrow`, `Alt+h/j/k/l` | focus pane |
| | `Alt+z` | zoom pane |
| | `LEADER+r` | resize mode (arrows or `h/j/k/l`, `Esc`/`Enter` leaves) |
| | `LEADER+p` | pane mode: `r`/`R` rotate, `g` go to a pane, `s`/`S` swap with a pane (focus follows the pane / stays in the slot), `x` close, `t` to a new tab (and follow it), `z` zoom, `e` equalize, `Esc`/`q` leaves |
| Grid | `LEADER+e`, `Ctrl+Shift+E` | equalize panes (also the `▦` button of the overview dock) |
| | `Ctrl+Shift+A` | arrange the panes into the best grid (also the `⊞` button) |
| | `LEADER+1` / `2` / `3` | new tab with `omp` / 2x2 grid / 3x3 grid (also in the `+` right-click menu, which lists these, the panels and their keys; Enter, a click or the row number opens an entry) |
| Agents | `Alt+n`, `LEADER+n` | jump to the next pane needing attention |
| | `LEADER+o` | show or hide the overview dock (right edge) |
| | `Alt+]`, `Alt+[` | next / previous agent pane |
| | `LEADER+g` | agent picker (every pane of the window; the most urgent one starts selected) |
| | `LEADER+b` | toggle broadcast typing to all panes of the tab |
| Sidebar | `Ctrl+F` / `Ctrl+G` | file explorer / git view |
| | `LEADER+Tab` | hide or show, remembering the view |
| | `Enter` / click, `r`, `q` in the git view | open lazygit in the selected repository (quit lazygit to return to the list), rescan, close; `j`/`k` or arrows move, `PageUp`/`PageDown`, `g`/`G` or `Home`/`End` jump |
| | `Ctrl+C` in the explorer | copy the absolute path of the hovered file or folder, or of all selected entries one per line |
| | double click or `T` in the explorer | open a terminal in that folder (a file's folder), split right of the last active grid pane, or below it when that pane is narrow; it takes focus |
| | `Enter` / `o` in the explorer | enter a folder; open text files (the hovered one, or all selected) in `$EDITOR` (else `nano`, else `vi`) in a new grid pane placed like a double click's terminal, which takes focus; other files go to yazi's opener |
| | `q` in the explorer | close it; the keyboard returns to the grid pane (yazi's other keys are unchanged: `.` shows hidden files, `F1` or `~` help) |
| | drag a column border in the explorer | resize the parent / current / preview columns (the pointer turns into a resize cursor over a border; Hivegrid build only). Until a border is dragged the columns follow the sidebar width: below 32 columns only the file list, below 64 file list and preview, then the parent column too |
| Web | `LEADER+w`, then `l` | location: type a URL or search words (opens a web pane when the tab has none) |
| | `LEADER+w`, then `n` | new web pane beside the last grid pane (asks for the URL) |
| | `LEADER+w`, then `b` / `f` | back / forward |
| | `LEADER+w`, then `r` / `R` | reload / hard reload (`s` stops loading) |
| | `LEADER+w`, then `y` / `o` | copy the page URL / open it in the system browser |
| | click on an `http(s)` link in a terminal pane | open it in the tab's web pane (a web pane's own links stay in the page) |
| Panels | `LEADER+,` | settings panel |
| | `LEADER+h` | usage history panel |
| | `LEADER+a` | agent hive panel |
| | `LEADER+u`, then `1`-`9` | start (or switch) omp with that account |
| Misc | `Ctrl+C` / `Ctrl+V` | smart copy or interrupt / paste |
| | `Alt+s` | toggle system stats |
| | `Alt+q` | Claude quota picker with all accounts (digit = the `LEADER+u` number); `LEADER+q` hides or shows the header chip |
| | `Ctrl+Shift+R` | reload config |

Leader, sidebar toggles, the overview dock toggle, the web key table, `Ctrl+Shift+E` (equalize), `Ctrl+Shift+A` (arrange), splits, the account launcher and the panel keys can be rebound in the settings panel (Keys); everything else is fixed.

## Panels

`bin/hivegrid-panel [settings|usage|agents|monitor|hive]` is a terminal UI with mouse support: click the tabs, buttons and rows, or use the keys shown at the bottom (`F1`-`F5` and `Tab` switch panels, `q` or `Esc` closes; in the hive `Tab` selects agents, so use the F-keys or click the tabs). On the Hivegrid build it opens as an overlay over the active tab (the panes keep running underneath and come back when it closes); on stock WezTerm it opens in a new tab. From about 170 columns (a 1920 px window) the panels use a wide layout: settings in two panes, usage with an account table beside the chart, and the agents table and monitor graphs centred with a capped width; narrower windows keep one column.

| Open it with | Panel |
| --- | --- |
| the gear button of the left bar, `LEADER+,`, `+` menu `Hivegrid settings` | settings |
| right-click an account pill, `LEADER+h`, `+` menu `Usage history` | usage |
| `+` menu `Agent overview` | agents |
| click the header logo, `LEADER+a`, `+` menu `Agent hive` | hive |
| click the system group (CPU, RAM, DSK, GPU), `+` menu `System monitor` | monitor |

- **Settings** writes `~/.config/hivegrid/settings.json`; the WezTerm config watches it, so *Save* applies at once to the header, tabs and terminal colours, and the bar, dock and git view repaint in the new theme while they run (a missing file means today's defaults). Closing with unsaved changes asks: *Save and close*, *Discard* or *Back*.
  - *Theme*: Hivegrid Navy (the default), Midnight Black, Slate and Forest, each shown as a card in its own colours, a live preview (a miniature window with header, bar, two panes, the dock and the 16 terminal colours) and an accent colour (quick swatches, `[` / `]` to step through them, or any `#rrggbb` with *Edit…*). A preset sets the header and tab palette and the terminal colours, and brings its own accent.
  - *Keys*: click a binding (or `Enter`) and press the new keys. Conflicts with another setting, with the fixed Hivegrid bindings and with WezTerm defaults (`wezterm --skip-config show-keys`) are shown inline; the first two refuse, the last asks. While recording, the config's own bindings are suspended (user var `hivegrid_capture`), so bound chords such as `Ctrl+F` reach the panel. A terminal reports only Ctrl+letter, Alt+key and plain keys; other chords (`CTRL+SHIFT+k`) are typed with *Type it*. *Reset* restores a default.
  - *Accounts*: add, rename, pick the omp profile, hide or remove, rescan. Every `~/.claude` and `~/.claude-<name>` directory that contains a `.credentials.json` is an account (existence only; the file is never read), labelled `main` for `~/.claude` and `<name>` otherwise, `main` first, then by name. An account pairs with the omp profile of the same name when `~/.omp/profiles/<label>` exists; otherwise it has no profile and omp starts with its default config (the panel offers a profile only when some exist). Entries carry `auto: true/false`, so manual ones survive rescans; removing an entry whose directory still holds credentials hides it instead. A directory the list does not mention yet is shown in the header anyway and offered under *Found on this machine*. Hidden or removed accounts leave the header, the quota refresh and the `LEADER+u` keys at once.
  - *Web*: where `http(s)` links clicked in a terminal open (*pane*: in the tab's web pane, the default; *system*: the system browser), the search engine a location that is no URL turns into (a URL template with `%s`, e.g. `https://duckduckgo.com/?q=%s`; empty, the default, refuses such a location), and the default browser profile (letters, digits, `.`, `_`, `-`, at most 32; default `main`). Only values that differ from the default are written (`web` in `settings.json`).
  - *About*: panel version, settings file, WezTerm version and whether the Hivegrid build (clickable segments, overlay action) is running.
  - On a wide window the sections are a list on the left (with a summary of each, amber `●` while it has unsaved edits; `←→`, `Tab` or a click switch), and Keys and Accounts show the selected binding or account in a detail pane with its actions (*Change*, *Reset*, *Reset all*; *Rename*, *Profile*, *Hide*/*Show*, *Remove*, plus the account's quota and reset times).
- **Usage** shows the account's current 5h and 7d numbers with their reset countdowns (as the header does) and charts both windows over 6 h, 24 h, 7 d or 30 d (`←→` account, `↑↓` or `1`-`4` range) with dotted window-reset markers, from `~/.cache/wezterm-claude-quota/history.jsonl`, which `claude-quota refresh` appends to (percentages, reset times and the account's directory only, one sample per account every two minutes, sooner when a number moved by a point or more, trimmed to about 30 days, mode 0600). Samples belong to the account directory, so renaming an account keeps its history (older label-only samples follow the `main` = `~/.claude`, `<name>` = `~/.claude-<name>` convention). The span before the first sample is shaded, labelled "not recorded" inside it and dated above the chart, and a line breaks where nothing was recorded for 20 minutes or more. On a wide window a table of every account (5h and 7d use, when the 5h window resets, the age of the last sample) sits beside the chart; click a row to show that account.
- **Agents** lists the panes of the window (`wezterm cli list`, the multiplexer of the terminal it runs in) in tab-bar order, without Hivegrid's own panes (bar, views, dock, panels): tab number, pane id, process, omp profile, how long the foreground process has run, directory and title. The cursor starts on the pane the panel was opened over; `↑↓`, `PgUp`/`PgDn`, `Home`/`End` and the wheel move it, and a click or `Enter` jumps to the pane and closes the panel.
  - Web panes are listed too, as process `web` with the page's host and path as directory and its title; they are no agents.
- **Monitor** graphs CPU, RAM, disk and GPU (`nvidia-smi` or Jetson `gpu.0/load`) over 1, 5, 15 minutes, 1, 6, 24 hours or 7 days (`←→` or `1`-`7`); time runs to the right edge. While the panel is open it samples every second. Independently of the panel the header (`config/sysstats.lua`, from its status tick, also while the system group is hidden with `Alt+s`) appends one line every 15 s (percentages only, CPU the mean of the readings since the last line) to `~/.cache/hivegrid/sysstats.jsonl`, rotated to `sysstats.jsonl.1` at 2 MiB, so 4 to 8 days remain; the 1 h and longer ranges (and the part of the shorter ones before the panel opened) are drawn from it, 600 slots each, a slot the mean of its lines, and a stretch without lines (Hivegrid closed) shows as a gap. The cost is a few reads of `/proc` per second (the header already does them), one line written per 15 s, and, with the group hidden, one `nvidia-smi` per 15 s instead of per 3 s. Values and amounts read as on the header's system group, coloured by the same load levels.
- **Hive** shows every agent pane of the window (all tabs) as a hex cell, auto-arranged in a spiral that keeps each agent's slot when another is deleted (`c` compacts, `t` toggles hex/tile). Colour and glyph give the state, the same in the overview dock and the pane picker: blue ↻ working, amber ◔ busy (the turn ended but background jobs run, or a tool such as a build or test has run for 8 s or more), green ✓ done, violet ? needs you (approval, question or plan review), grey · idle, ○ exited (the agent quit and left its shell). A cell shows nothing more than the state, the agent's number (the overview dock's digit; `tab·number` while agents sit in several tabs) and its folder. Where inline images work (as for the charts, below) the cells are smooth anti-aliased images: rounded hexes or tiles with an even gap, a continuous ring around the selected cell and a lighter shade under the pointer, while the glyph, number and folder stay terminal text; otherwise they are drawn with block characters. A few agents in a large window get bigger cells (up to 26 x 16 characters), sized so the popover still fits beside them. Selecting one opens a larger popover with the directory, state, age and reason, the pending question, the last screen lines and a prompt line (Enter sends it to the pane); it sits beside the cells, or below them, so it covers none when there is room. The prompt is disabled while the agent waits for an answer, since typed text would land in that dialog. Plan reviews can be answered from the popover (buttons or `1`-`5`); approvals and questions are shown and answered in the pane (`j` jumps there). `n` adds an agent in the tab the hive was opened over (in the selected agent's directory, else the one of the pane under the hive) and re-grids that tab, and `d` removes one (Ctrl+D, then the pane is closed; confirmation while the agent is working). `done` stays green until you acknowledge it (`a`) or jump to, look at or prompt that agent.
  - The exact states come from the omp extension `ext/hivegrid-agent-state.ts`, which `install.sh` copies into `~/.omp/agent/extensions/` and every profile's `extensions/`. It writes `$XDG_RUNTIME_DIR/hivegrid-agents/<pane id>.json` (marked `exited` when omp quits); the hive and the overview dock read those files and the hive hands plan-review answers over as `<pane id>.cmd.*.json`. Without the extension, or for a pane with a stale file, the state comes from omp's title glyph (working, idle, needs you only), and for other agents (claude, codex, ...) from the foreground process.
  - `w`, or the `+ web` chip in the legend, asks for a URL (or search words) and opens it in a new web pane of the tab the hive was opened over, with the profile and search engine of Settings, Web. A page is no agent, so it gets no cell.

Charts are drawn with PIL as inline images (the iTerm2 image protocol, which WezTerm speaks) when `python3-pil` is installed and the terminal is WezTerm; otherwise, or with `HIVEGRID_NO_IMAGES=1`, they are braille plots. The panels need only Python 3.

## Grid launcher

`omp-grid` runs inside a WezTerm pane (or anywhere with `--tab`) and starts one command, default `omp`, per pane.

```sh
omp-grid 2 3 ~/work                        # 2 rows x 3 columns in ~/work
omp-grid --layout main-side 3 --name-panes # one main pane, three stacked on the right
omp-grid --layout columns 4 --tab --dirs ~/a,~/b  # new tab, 4 columns, directories cycled
```

`--cmd CMD` swaps the command, `--dir DIR` sets the directory (relative directories are resolved where `omp-grid` runs, and every pane starts in its own one, in reading order); `omp-grid --help` lists everything.

## Claude quota

`claude-quota` fetches Claude Code usage for each account and caches only the percentages and reset times in `~/.cache/wezterm-claude-quota/` (plus `history.jsonl`, see Panels). The header reads that cache; the network is never touched from the GUI thread. It reads the OAuth token in-process, first from the account's paired omp profile (the `anthropic` OAuth row in `~/.omp/profiles/<profile>/agent/agent.db`, opened read-only; omp keeps it fresh while it runs) and otherwise from the `.credentials.json` of the account directory (which only the `claude` CLI refreshes). It calls the usage endpoint with caching and backoff, never refreshes, stores or logs tokens, and shows `login` only when neither source holds a valid token and nothing fresher came in for a day. A window whose reset time has passed reads 0% instead of stale. `claude-quota install-statusline` adds Claude Code's documented `statusLine` hook to each account's `settings.json` (an existing status line command is kept and chained, a rerun changes nothing): `claude-quota statusline` stores the `rate_limits` of the session JSON as that account's numbers, with no token involved, and prints the account's usage as the status line unless `--chain` names another command. Re-run it after adding an account. Run `claude-quota refresh` or `claude-quota show` by hand to check it.

The accounts are, in order of precedence: `CLAUDE_QUOTA_DIRS` (or `--dirs`), a comma-separated list; the list in `~/.config/hivegrid/settings.json` (hidden entries skipped) plus every `~/.claude-*` directory holding a `.credentials.json` that the list does not mention; or, without a settings file, those discovered directories alone.

### Switching accounts without losing the chat

Clicking an account pill (or `Alt+q`, or `LEADER+u <n>`) in a pane that runs a shell starts `omp --profile=<profile>` (a bare `omp` for an account without a profile). In a pane that already runs omp under another profile it moves the chat over (a chat moves only between omp profiles: an account without a profile starts a bare `omp`, but a running omp is not moved to or from it):

1. `omp-switch locate` finds the session (omp records each terminal's session in `<profile>/agent/terminal-sessions/pts-N`; only paths are read, never the conversation);
2. omp is asked to quit with `Ctrl+D` and has five seconds; with unsent text in its prompt it stays, the switch is abandoned and a toast says so (never `kill`);
3. `omp-switch transfer` copies the finished session file, its artifact directory and the blobs it references into the other profile under the same names, without overwriting anything (a copy of an older part of the same chat is brought up to date; a different chat under that name aborts) and without touching the first profile's files;
4. `omp --profile=<new> --resume '<copy>'` continues the same chat (a fresh `omp --profile=<new>` when no message was written yet; a `cd` first when the shell is not where omp ran).

If the pane looks busy (agent working or asking), a dialog asks "switch anyway?" first, default No; Yes sends `Esc` (omp's interrupt) before quitting.

## Custom WezTerm build

`wezterm/` is a submodule pointing at the [WezTerm fork](https://github.com/leongreiner/wezterm) (branch `hivegrid`) that adds clickable header segments (`status-click`), the `ShowOverlayCommand` key assignment, which runs a program in an overlay over the active tab until it exits (the panels), and the drawn header mark. The mark is the option `tab_bar_brand = { enabled, size, margin_left, margin_right, colors = { center, center_outline, glow, ring, ring_outline } }` (`theme.BRAND_MARK` in `config/theme.lua` sets the icon's colours; the centre cell is blue and the six outer cells show agent-state colours), and `tab_bar_floating_tabs` puts tabs and `+` on the pills' line. The config also runs on stock WezTerm 20240203: clicks are ignored, the panels open in a new tab, the header shows a row of `⬢` glyphs instead of the drawn mark and the tabs rise from the bar's lower edge. To build it without sudo, run `git submodule update --init` and then `wezterm/build-hivegrid.sh`; `wezterm/HIVEGRID.md` has the details.

The fork also tells a program that grabs the mouse when the pointer leaves its pane (private mode 8800, `wezterm/HIVEGRID.md`), which is what lets the bar and dock end their hover; stock WezTerm ignores the mode. It keeps the bar and the dock at their widths when the window is resized (the grid's columns shrink and grow in proportion), never dims them or gives the bar the keyboard focus on a click, and runs their background and divider from the header to the bottom border.

The fork also hosts the web pane (`wezterm/HIVEGRID.md`): a pane kind that shows a page of a headless Chrome driven over the DevTools pipe, drawn as a texture in the pane's rectangle, started by `wezterm cli split-pane --url` or by the program name `hivegrid:web <url>` and controlled by `ESC _ hgweb;<command> ESC \` written to the pane (`wezterm cli send-text --no-paste`). In `wezterm cli list` it has `tty_name: null`, `is_web: true`, the page URL as `cwd` and the page title as `title`; Hivegrid recognises it that way. The WezTerm config finds out whether this WezTerm has the web pane by running `wezterm cli split-pane --help` and looking for `--url`; the answer is cached in `~/.cache/hivegrid/web-probe` (keyed by the binaries' sizes and version), so it costs no process while the build is unchanged. Without the web pane (stock WezTerm, or a fork built before it) the web key table, the link routing and the `+` menu rows are not registered. The hive's `+ web` chip and `w` key look for the web pane's user var name in the `wezterm` and `wezterm-gui` binaries next to the one the panel runs under and stay away without it, and the settings panel says so under Web; both checks are only as good as they can be without asking the running GUI, so a build moved away from its siblings reads as having no web pane.

## Notes and limits

- Agent state is shown in the overview dock, the pane picker and bell toasts, not in the header or the tab titles; a background tab's state is only visible after switching to it. On stock WezTerm the header is not clickable.
- The overlay covers the whole active tab; there is no centred modal mode.
- Moving an omp chat to another account needs omp's per-terminal session record (omp 18); with several omp processes in one terminal it refuses rather than guess. The busy check uses the same heuristic as the agent states.
- omp's state comes from the run-state glyph in its terminal title (`tui.titleState`, on by default in omp); when that is off, and for other agents, detection is heuristic: it combines the foreground process, screen text markers, the bell and screen activity.
- The sidebar bar spans the full window height on the Hivegrid build (a top-level split); on stock WezTerm 20240203, which mis-sizes top-level splits, it and the overview dock sit beside the leftmost / top-right pane only. On the Hivegrid build the explorer and git view form a full-height column right of the bar, and the grid shrinks as a whole (an even grid stays even); switching the view, or the git view following another directory, keeps the column in place and the grid still. The view is 22% of the window (28 to 60 columns) and follows window resizes until you drag its divider: that width, scaled with the window, then stays for the tab and is taken by tabs without their own. The git view never restarts while lazygit runs in it; it follows the grid once lazygit quits. On stock WezTerm the view is docked beside the leftmost pane.
- The sidebar views start in the working directory of the pane they were opened from (the git view scans it for repositories: the one around it and those below it, `HIVEGRID_GIT_DEPTH` levels deep, default 3; with a single repository it opens lazygit there directly, with none it says so). A pane whose shell reports a path from another host, such as an ssh session, uses the directory of its local foreground process instead.
- The explorer copies paths with the terminal clipboard escape (OSC 52), which WezTerm accepts, and also through `wl-copy`, `xclip` or `xsel` when present.
- `Ctrl+V` paste is not fanned out in broadcast mode, because the clipboard is not readable from Lua.
- Web panes need the Hivegrid WezTerm build and Chrome or Chromium (`install.sh` warns when none is found; `HIVEGRID_CHROME` names another binary). There is no address bar in the pane: locations come from `LEADER+w l`, links and the CLI. Mouse wheel scrolling moves a notch at a time (the compositor reports wheel steps); IME text is committed to the page but the candidate window sits at the pane's top-left corner; drag and drop is not forwarded; a page's downloads, permission prompts and JS dialogs are answered by Hivegrid (refused or dismissed), and pages do not survive a restart of Hivegrid (cookies and logins of a profile do). They work in the local domain only, not through a mux server, an SSH domain or a mux client.
- A web pane is never part of broadcast (starting broadcast from one is refused, and focusing one while it runs ends it, so typing into a page never reaches the other panes); in a layout it counts like a terminal pane, so auto-arrange re-tiles around it. The overview dock lets it close without asking (state `web`).
- Web profiles keep cookies and logins on disk in your data directory (`~/.local/share/hivegrid/web/profile-<name>`, mode 0700) with Chrome's basic password store, so they are not encrypted by the OS keyring: anything running as your user can read them; use a private web pane (ephemeral profile) for sessions you do not want kept.
- Web panes launch Chrome or Chromium, and without one the web key table says `Chrome or Chromium not found` and link clicks stay with the system browser; Snap Chromium may not work, because the snap cannot write the hidden profile directory under your home, so install Google Chrome or a non-snap Chromium (or point `HIVEGRID_CHROME` at one).
- `HIVEGRID_WEB_DEVTOOLS=1` is a debug option: it makes the browser listen on an unauthenticated DevTools port on localhost, which any local process can use to control the browser and read its cookies, so leave it unset.

## Development

`dev/check` runs every check locally, the same ones CI runs: `py_compile` of all Python files and the unittest suite (`tests/*.py`, standard library only; PIL cases skip without PIL), `luac -p` of `config/` and the Lua tests (`tests/lua/`, a stdlib-only runner with a fake `wezterm` module, Lua 5.4), luacheck (`.luacheckrc`), the bun tests of the omp extension (`tests/ext`), shellcheck and actionlint. `dev/check py lua` runs only those steps; a tool that is not installed is skipped with a note.

CI (`.github/workflows/ci.yml`, GitHub Actions on pushes and pull requests to `main`) runs one job per group of steps (Python, Lua, bun, shellcheck + actionlint), each calling `dev/check`; there a missing tool fails. It checks out without submodules, since the checks need only `config/`, `bin/`, `ext/` and `tests/`.

`dev/qa/hgq` runs an isolated Hivegrid (the fork build and this repo's `config/` and `bin/`) on its own Xvfb display for manual QA, screenshots and synthetic input (`dev/qa/hgq help`). `dev/smoke` uses it (slot 11, local only) to open the bar's views and the overview dock with their keys, check the panes, save screenshots and look for Lua errors.

`dev/qa/web-smoke` (slot 12, local only; needs Chrome and a fork build with the web pane) drives the web pane in the sandbox: pixels in the pane rectangle, input, resizing, zoom, moving, dialogs and popups, a killed Chrome, and that no Chrome outlives a closed pane or a killed GUI.

`dev/demo/record.py` records the demo video of the README (`assets/demo/`) and its stills (`assets/screenshots/`): `dev/demo/prepare.sh` sets up QA slot 20 with fixture repositories, fictional accounts and usage (`dev/demo/fake-claude-quota`) and omp profiles of their own, then the script drives the window with synthetic input and records the display with ffmpeg. It runs real omp sessions with a few trivial prompts, so it needs a logged-in omp (`DEMO_OMP_SRC`: `~/.omp`, the default profile, or `~/.omp/profiles/NAME`) and Chrome, and it uses a few percent of that account's quota.

## Contributing

Hivegrid is open to pull requests: bug fixes, new features, themes, docs and tests. Small focused changes are merged fastest; for a bigger feature or a redesign, open an issue first so the direction can be agreed. [CONTRIBUTING.md](CONTRIBUTING.md) explains how to run the checks (`dev/check`) and how to try a change in an isolated sandbox window (`dev/qa/hgq`). Found a bug or have an idea? Use the [issue templates](https://github.com/leongreiner/hivegrid/issues/new/choose). Security problems go through [SECURITY.md](SECURITY.md); everyone is expected to follow the [code of conduct](CODE_OF_CONDUCT.md).

## License

MIT
