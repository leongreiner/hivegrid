# Hivegrid

A WezTerm workspace for running many [`omp`](https://github.com/can1357/oh-my-pi) AI coding agents side by side in a pane grid, with per-agent state visible at a glance.

## Features

- Agent state per pane (working, waiting, ask, finished, exited) in tab titles and the header status, plus bell toasts for background panes.
- Pane minimap in the header with agent numbers, and a pane picker (`LEADER+g`) listing every agent with its state and directory.
- `omp-grid` launcher: equal grids, main-plus-side and column layouts, per-pane directories, optional new tab.
- Resize, pane and broadcast key tables, and an equalize command that evens out all dividers.
- Broadcast mode that types into every pane of the tab, with a visible `BROADCAST` marker.
- System stats in the header: CPU, RAM, disk, GPU.
- Claude Code usage (5h and 7d windows) for up to three accounts.
- Left sidebar with a yazi file explorer or lazygit, toggled by key or by clicking its handle.
- Smart `Ctrl+C` (copy with a selection, interrupt otherwise), `Ctrl+V` paste, `Ctrl+Arrow` splits, `Alt+Arrow` focus.
- Dark theme, integrated window buttons, dimmed inactive panes.

## Install

Requires WezTerm 20240203 or newer, Python 3 and curl.

```sh
git clone git@github.com:leongreiner/hivegrid.git
cd hivegrid
./install.sh [--tools]
```

The installer copies `config/` to `~/.config/wezterm/`, `bin/` to `~/.local/bin/`, the icon, and a `Hivegrid` launcher that overrides the `org.wezfurlong.wezterm` desktop entry. Differing existing files are saved once as `*.bak.<timestamp>`. `--tools` also downloads the latest `yazi`, `ya` and `lazygit` into `~/.local/bin`. No sudo is used. `./sync.sh` copies the live setup back into the repo for maintenance.

## Keys

`LEADER` is `Ctrl+Shift+Space`.

| Group | Keys | Action |
| --- | --- | --- |
| Panes | `Ctrl+Shift+\|` / `Ctrl+Shift+_` | split right / down |
| | `Ctrl+Arrow` | split in that direction |
| | `Alt+Arrow`, `Alt+h/j/k/l` | focus pane |
| | `Alt+z` | zoom pane |
| | `LEADER+r` | resize mode (arrows or `h/j/k/l`, `Esc`/`Enter` leaves) |
| | `LEADER+p` | pane mode: `r`/`R` rotate, `g` select, `s`/`S` swap, `x` close, `t` to new tab, `z` zoom, `e` equalize, `Esc`/`q` leaves |
| Grid | `LEADER+e` | equalize panes |
| | `LEADER+1` / `2` / `3` | new tab with `omp` / 2x2 grid / 3x3 grid (also in the `+` right-click menu) |
| Agents | `Alt+n`, `LEADER+n` | jump to the next pane needing attention |
| | `Alt+]`, `Alt+[` | next / previous agent pane |
| | `LEADER+g` | pane picker |
| | `LEADER+b` | toggle broadcast typing to all panes of the tab |
| Sidebar | `LEADER+f` / `LEADER+t` | file explorer / git view |
| | `LEADER+Tab` | hide or show, remembering the view |
| Misc | `Ctrl+C` / `Ctrl+V` | smart copy or interrupt / paste |
| | `Alt+s` / `Alt+q` | toggle system stats / Claude quota |
| | `Ctrl+Shift+R` | reload config |

## Grid launcher

`omp-grid` runs inside a WezTerm pane (or anywhere with `--tab`) and starts one command, default `omp`, per pane.

```sh
omp-grid 2 3 ~/work                        # 2 rows x 3 columns in ~/work
omp-grid --layout main-side 3 --name-panes # one main pane, three stacked on the right
omp-grid --layout columns 4 --tab --dirs ~/a,~/b  # new tab, 4 columns, directories cycled
```

`--cmd CMD` swaps the command, `--dir DIR` sets the directory; `omp-grid --help` lists everything.

## Claude quota

`claude-quota` fetches Claude Code usage for each configured account and caches only the percentages and reset times in `~/.cache/wezterm-claude-quota/`. The header reads that cache; the network is never touched from the GUI thread. It reads the OAuth token in-process from the `.credentials.json` of each configured directory (defaults `~/.claude`, `~/.claude-2`, `~/.claude-private`; override with `CLAUDE_QUOTA_DIRS`, a comma-separated list), calls the usage endpoint with caching and backoff, and never stores tokens. An expired token shows as `login` and is not refreshed. Run `claude-quota refresh` or `claude-quota show` by hand to check it.

## Notes and limits

- WezTerm has no custom clickable header buttons or in-pane overlays, so state is shown through tab titles, the right status and the minimap.
- omp state detection is heuristic: it combines the foreground process, screen text markers, the bell and screen activity.
- The sidebar takes the full window height only in a single-pane tab; in a grid it is docked beside the leftmost pane.
- `Ctrl+V` paste is not fanned out in broadcast mode, because the clipboard is not readable from Lua.

## License

MIT
