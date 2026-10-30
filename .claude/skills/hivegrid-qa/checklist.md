# Hivegrid QA checklist

Every surface with the scenarios that found real bugs before. Run each at 1280x720, 1920x1080 and 2560x1440 unless noted, take screenshots before and after, and look at them. "Fixture" means something you build under `/tmp/hivegrid-qa/sN/work`.

## Whole window

- Window resizes in steps from 2560 down to about 800 px wide and back: no overlap, clipping, jitter, or a GUI stuck at 100% CPU.
- All five theme presets (Graphite, Navy, Midnight Black, Slate, Forest) plus a custom accent. After a Save, every surface that is already open restyles: header, tabs, bar, dock, explorer, git view, lazygit, panels, hive.
- Chrome (bar, dock, sidebar view) runs edge to edge, from under the header to the bottom border, with no strip of window background.
- `hgq log N` shows no Lua errors after any of the scenarios below.

## Header

- Brand mark: clicking it opens the hive. Its colours follow the agent states.
- Tabs, 1 to 10 of them: truncation with an ellipsis; active, inactive and hover styles; the close button; level with the pills.
- `+` button: left click opens a new tab. Right-click opens the menu; try every entry.
- Quota chips: numbers, bars, countdowns, the `login` chip and the stale marker. Clicking a chip in a plain shell starts omp with that account (quit it afterwards). Right-click opens the usage panel.
- System group: CPU, RAM, DSK, GPU; clicking it opens the monitor.
- Mode pills: BROADCAST, RESIZE, PANE, ZOOM.
- Width plan: groups drop to narrower tiers in order, the CPU chip survives, and no separator is left orphaned.

## Left bar

- All four buttons are present after: start, `omp-grid --tab`, LEADER+1/2/3, window resizes and theme changes.
- Hover fades in and out, the pointer becomes a hand, and the hover ends when the pointer leaves the bar (fork build).
- A click never leaves keyboard focus on the bar.
- The active view's button carries the accent marker.

## Sidebar views (explorer, git view)

- Open, close and switch with `Ctrl+F`, `Ctrl+G`, the bar buttons and LEADER+Tab. The column keeps its place and width, and switching views does not resize the grid.
- With 1, 4, 9 and 12 panes, the dock open, several tabs, a zoomed pane, and after closing panes.
- The view follows the active pane's directory, and never restarts a running lazygit.
- Explorer:
  - hidden files, very long names, a 1000+ file directory, symlinks (working and broken), unicode names, preview;
  - double click (opens a terminal in that folder) and Enter on a file (opens an editor pane);
  - `Ctrl+C` copies the paths (check by pasting into a pane);
  - dragging a column border, with the resize cursor over it.
- Git view: fixture repos that are clean, dirty, staged, untracked-only, in conflict, ahead and behind a local bare remote, without upstream, with a detached HEAD, nested, a submodule, 30+ repos, long names, a non-repo directory, an empty directory, and repos deeper than `HIVEGRID_GIT_DEPTH`.
  - CI marks: read-only `gh` on real repos only.
  - Keys `j`, `k`, Enter, `r`, `q`; mouse click and scroll.
  - Watch the `gh` call rate: the API limit is shared with every other `gh` user.

## Overview dock

- 1, 4, 9, 16 and 20 panes: scrolling, keeping the active row visible, two-digit numbers.
- States, with real omp sessions: working, busy, needs you, limit, done, idle, exited. A shell at its prompt shows as `shell`. Timers update live and survive a config reload.
- Jump by click, digits, and the keyboard selection with Enter; `▦` equalize and `⊞` arrange.
- Closing panes and tabs from the dock: the confirmation rule, re-tiling afterwards, where focus lands.
- Hover, pointer shape, footer hints at narrow widths.

## Grid

- Splits, focus moves, zoom (bar and dock stay visible, ZOOM pill shows), resize mode, pane mode (`r`/`R`, `g`, `s`/`S`, `x`, `t`, `z`, `e`).
- Equalize with 2 to 20 panes, in rows, columns and irregular layouts. Check the widths with `hgq cli N list` and make sure the bar, dock and sidebar are never counted or moved.
- Auto-arrange when a tidy grid gains or loses a pane. Hand-built layouts are left alone. Panes are moved, never recreated: the pane ttys stay the same.
- `omp-grid`: every layout, `--tab`, `--dir`/`--dirs` (relative ones too), `--cmd`, `--help`; and LEADER+1/2/3.
- Broadcast reaches every grid pane, and nothing else.

## Panels (settings, usage, agents, monitor) and the picker

- Settings:
  - Theme: presets, accent, preview, Save applies at once.
  - Keys: record, the three kinds of conflict, Type it, Reset; a bound chord reaches the panel while recording.
  - Accounts: work only on the sandbox `settings.json`; never create or delete `~/.claude-*` or omp profiles.
  - About.
  - Closing with unsaved changes.
- Usage: every account and range, reset markers, gaps, no-history spans, braille fallback (`HIVEGRID_NO_IMAGES=1`).
- Agents: only grid panes are listed; tab numbers; jumping.
- Monitor: live graphs, ranges, resizing while open.
- Panel chrome: F1–F5, Tab, `q`, Esc, mouse, wide layouts at 2560.
- Picker overlay: pane picker LEADER+g, quota Alt+q, the `+` menu, pane-mode swap/select; filtering, keys, mouse; the stock InputSelector fallback.

## Hive

- 1, 6 and 20 agents; hex and tile (`t`); compact (`c`); selection; popover placement; prompt line; plan-review answers.
- Smooth image cells and both fallbacks (block characters, and no images).
- The pane picker, `Alt+n` / LEADER+n, `Alt+]` / `Alt+[` and bell toasts agree with the dock.

## Remote (hivegrid-remote)

- `dev/qa/remote-smoke` passes (slot 14); the token lives in the sandbox HOME, never the real one. Never run `tailscale` in QA.
- The phone page (390x844) and a desktop window: state colours and words from the palette, 44 px tap targets, no pane ids shown, a read-only chip with `--read-only`, the confirm dialog for Ctrl-C and Quit.
- Hostile pane text (`<img src=x onerror=...>`) shows as plain text in the list and the tail.

## Fork (Hivegrid WezTerm build)

- After a fork change: run `cargo test` for the touched crates, rebuild into `/tmp`, restart one sandbox, and repeat the bar, dock, sidebar, zoom and resize scenarios.
- Stock WezTerm 20240203 must keep working: every fork-only option and action is guarded.
