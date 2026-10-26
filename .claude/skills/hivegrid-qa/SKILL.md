---
name: hivegrid-qa
description: Use for QA of Hivegrid (this repo's WezTerm workspace): testing it end to end in an isolated sandbox, driving it with synthetic keys and clicks, screenshot review, running a parallel QA round with several agents, and verifying any change to config/, bin/ or the wezterm fork before calling it done.
---

# Hivegrid QA

Hivegrid is a running GUI: unit tests cover logic, but layout, focus, colours and timing only show in the real program. A change counts as verified once it was exercised in the sandbox and its screenshots were looked at.

## Safety first

- Your shell may run inside the user's live Hivegrid: `WEZTERM_PANE` and `WEZTERM_UNIX_SOCKET` point at it. A bare `wezterm cli ...` drives the user's window. Only ever use `dev/qa/hgq cli SLOT ...`.
- Never run `install.sh` or `sync.sh`, and never write into `~/.config/wezterm`, `~/.local/bin`, `~/.local/opt/hivegrid-wezterm`, `~/.config/hivegrid` or `~/.omp` during QA. Deploying is the last step, and only with the user's OK (see Finish).
- The sandbox shares the real `~/.omp` and `~/.claude*`: omp sessions started there use the user's quota and land in their omp history. Keep prompts trivial, run at most 3–4 sessions at once, run them only in the slot's `work/` dir, and quit them afterwards. Never trigger an `omp-switch` transfer. Never run mutating git or lazygit actions in real repos; build fixture repos under `work/`.

## Sandbox: `dev/qa/hgq`

`dev/qa/hgq help` prints the full reference. A slot N runs Hivegrid on Xvfb display `:90+N`, with HOME `/tmp/hivegrid-qa/sN/home`. Its `~/.config/wezterm` links to the repo's `config/`, and `~/.local/bin/*` links to `bin/*`, so the sandbox runs the working tree. Lua edits reload on their own; `bin/` edits apply to programs started afterwards (reopen the view, or `hgq restart N`).

```sh
Q=dev/qa/hgq
$Q up 3 1920 1080                       # also 1280 720 and 2560 1440 when layout matters
$Q x 3 key ctrl+space o                 # LEADER is Ctrl+Space
$Q x 3 click 23 106                     # left click; `click X Y 3` right, `click X Y 1 2` double
$Q shot 3 /tmp/hivegrid-qa/s3/shots/a.png        # then `read` the PNG and look at it
$Q zoom 3 /tmp/hivegrid-qa/s3/shots/a-bar.png 60x300+0+0 300%   # pixel detail
$Q cli 3 list --format json             # pane ids, sizes, titles: evidence for layout claims
$Q sh 3 omp-grid 3 3 --tab --cmd bash   # run a command inside the sandbox environment
$Q log 3                                # Lua errors land here
$Q down 3
```

- To test a fork build, build it into `/tmp`, never over the installed one: `cd wezterm && OPT=/tmp/hivegrid-qa/fork WRAPPER=/tmp/hivegrid-qa/fork/hivegrid ./build-hivegrid.sh`. Then run `HG_WEZTERM_DIR=/tmp/hivegrid-qa/fork dev/qa/hgq restart N`. The build script replaces binaries by rename, so running instances survive a rebuild.
- There is no window manager: hgq gives the window X focus at start. If clicks stop registering, run `hgq focus N`.
- The sandbox starts from a clean environment, so `NO_COLOR`, `CI`, pagers and `GIT_*` from an agent shell do not leak into panes.
- A "wezterm Configuration Error" window means the config failed (often a missing font); `hgq log N` shows why.
- **Agents without quota:** for hive, dock and picker scenarios that only need agent panes, replace the sandbox's `omp` link with a fake script. This holds until the next `hgq up/restart`, which re-links it, so do it after that. Use real omp only for real states (working, needs you); the extension's exact states come only from real omp.

  ```sh
  H=/tmp/hivegrid-qa/sN/home; rm -f $H/.local/bin/omp
  printf '#!/bin/bash\nprintf "\\033]0;π fake\\007"\nwhile read -r _; do :; done\n' >$H/.local/bin/omp
  chmod +x $H/.local/bin/omp; dev/qa/hgq sh N omp-grid 2 2 --tab   # Ctrl+D quits a fake
  ```

## Verification ladder

1. `dev/check` (or `dev/check py lua luacheck`): compile and lint, Python, Lua and bun tests, shellcheck, actionlint. CI runs the same steps.
2. `dev/smoke`: end-to-end in slot 11. It opens the bar's views and the dock by key and asserts the panes.
3. The scenario of your change in the sandbox, with before and after screenshots, at the sizes that matter (1280x720, 1920x1080, 2560x1440) and, for anything coloured, under the theme presets.
4. Heavy-user pass: every action reachable by keyboard, focus lands where the user expects, nothing flickers or lags, narrow windows degrade gracefully.

[checklist.md](checklist.md) lists every surface with the scenarios worth running. [design.md](design.md) is the design language every surface follows; check screenshots against it.

## Known traps (learned the hard way)

- **Chrome padding:** WezTerm paints the window padding with the pane's default background. Chrome programs (bar, dock, sidebar views) set it to their own colour with OSC 11, or a grey strip shows above and below them.
- **Split mouse reports:** a mouse report can be split across two reads. TUIs must carry the cut part over (`hivegrid_ui.split_complete`), or fast mouse movement turns into keystrokes.
- **`wezterm cli` from Lua:** calling it inside event handlers can freeze the GUI. Use the Lua API.
- **Theme changes:** running TUIs must pick up a theme Save while they run (`ThemeWatch` in `hivegrid_settings_core`); check every surface after a Save, not only new ones.
- **Agent states:** one vocabulary and glyph/colour table for the dock, hive, picker and header (see design.md). A shell at its prompt is `shell`, not `exited`.
- **Concurrent edits:** a Lua file that someone else is half-way through editing logs errors for a minute. Re-check before blaming code.
- **Freezes in the installed build:** a GUI stuck at 100% CPU on the installed build may be fixed in the fork tree already. Compare with the `/tmp` build before digging.
- **Overlay user vars:** a user var set by an overlay program (panels, picker, hive) right before it exits is lost: WezTerm removes the overlay before reading its last output. The picker waits for an acknowledgement (`config/picker.lua`); do the same for new overlay programs.
- **Overlay panes aren't in the mux:** in a `user-var-changed` handler for an overlay, `pane` methods that look the pane up fail with "pane id N not found in mux". Resolve a real pane (an id the program sent, or `window:active_tab():active_pane()`).
- **Pane numbers:** users see dock numbers (`3`, `tab·number`), never internal pane ids; the git view, hive, picker and agents panel all follow this.

## Running a parallel QA round

- **Plan:** one agent per surface, each with its own slot number and an explicit list of files it owns. Shared files (`README.md`, `config/wezterm.lua`, `bin/hivegrid_ui.py`) get surgical edits only, with a message to the owner. Exactly one agent edits and builds the fork. One read-only design auditor screenshots everything and messages the owners.
- **Contracts first:** when several agents build on a shared component, have its owner publish the API first and the others consume it.
- **Names:** use fresh agent names each round (a reused name reaches the idle agent of an earlier round). Message peers directly; `agent://all` also wakes idle agents.
- **Budget:** ten Opus agents for about 40 minutes used up a weekly Claude window. Size rounds to the remaining quota (header pills), and tell the user before starting a large one.
- **Reports:** each agent writes `local://qa/<Name>.md` with: scenarios tested; bugs (repro, cause, fix or why not); polish done; open issues; redesign proposals; screenshot paths before and after.
- **Redesigns:** major redesigns (new layout, new interaction model, removing features) are proposals until the user approves them. Small and medium fixes are implemented directly.

## Finish

1. Run `dev/check` (all green) and `dev/smoke`, and take a final look at the main surfaces in one sandbox on the build that will ship.
2. Tell the user what changed and what is still open.
3. Deploy only with the user's OK:
   - `./install.sh` copies the config and programs. The config reloads at once; running TUIs pick up `bin/` changes when reopened.
   - `wezterm/build-hivegrid.sh` deploys the fork, which takes effect when Hivegrid restarts.
4. Commit locally. Publishing follows the user's rules: ask "Push to main?" when they have not said. The fork submodule is a separate private repo; commit it on its `hivegrid` branch and push it only when asked.
