# AGENTS.md

Guide for AI coding agents working in this repository. Humans: see [README.md](README.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

Hivegrid is a WezTerm workspace for running many `omp` coding agents in a pane grid: a Lua config (`config/`), Python terminal programs for the panels, bar, dock and git view (`bin/`), an omp extension (`ext/`), and a WezTerm fork as a submodule (`wezterm/`). It is a GUI program: unit tests cover logic, but layout, focus, colours and timing only show in the running window.

## Safety rules (read first)

- **Your shell may run inside the user's live Hivegrid.** `WEZTERM_PANE` and `WEZTERM_UNIX_SOCKET` then point at it, and a bare `wezterm cli ...` drives the user's window. Drive only a sandbox: `dev/qa/hgq cli SLOT ...`.
- **Never run `install.sh` or `sync.sh`, and never write to `~/.config/wezterm`, `~/.local/bin`, `~/.config/hivegrid`, `~/.local/opt/hivegrid-wezterm`, `~/.omp` or `~/.claude*`.** Deploying is the user's decision.
- The sandbox shares the real `~/.omp` and `~/.claude*`: omp sessions started there use the user's quota. Keep prompts trivial, run at most 3 to 4 sessions, and quit them afterwards. Never trigger an `omp-switch` transfer there.
- Tests and fixtures use temp directories and fake data, never a real home, account name, token or path. Do not commit secrets, account names or personal paths.
- Do not push, tag, open pull requests or change repository visibility unless asked.

## Commands

```sh
dev/check                  # everything CI runs; `dev/check py lua luacheck bun shell actions` selects steps
dev/qa/hgq help            # isolated Hivegrid on its own Xvfb display: up, x (synthetic input), shot, cli, log, down
dev/smoke                  # end-to-end smoke test in sandbox slot 11 (needs Xvfb and the fork build)
dev/qa/web-smoke           # web pane end to end in slot 12 (needs Chrome and a fork build with the web pane)
```

A missing tool skips its `dev/check` step locally and fails it in CI. Run `dev/check` before calling a change done. The QA workflow (scenarios, screenshots, parallel rounds) is in [.claude/skills/hivegrid-qa/SKILL.md](.claude/skills/hivegrid-qa/SKILL.md) with a surface checklist (`checklist.md`) and the design language (`design.md`); a screenshot that breaks `design.md` is a bug.

## Layout

| Path | What lives there |
| --- | --- |
| `config/wezterm.lua` | Entry point: loads the modules below, header, key bindings, `+` menu. Modules are `return function(config, wezterm, ...)` and register into `config`. |
| `config/theme.lua` | Palette, drawing helpers, feature detection (`has_click`, `has_web`) shared by every module. |
| `config/layouts.lua` | Pane layout, only on request: equalize, arrange, rotate, swap, broadcast, key tables. Nothing re-tiles the grid by itself. |
| `config/overview.lua`, `sidebar.lua`, `picker.lua`, `agentstatus.lua` | Dock, left bar and its views, picker, per-pane agent state. |
| `config/claudequota.lua`, `sysstats.lua`, `settings.lua`, `web.lua` | Header chips, settings reader, web pane wiring. |
| `bin/hivegrid_ui.py` | Terminal UI toolkit (cell canvas, click regions, design components); every TUI uses it. |
| `bin/hivegrid_panels.py`, `hivegrid_hive.py`, `hivegrid_picker.py`, `hivegrid-bar`, `hivegrid-overview`, `hivegrid-git` | The panels, hive, picker, bar, dock and git view programs. |
| `bin/hivegrid_agents.py`, `hivegrid_repos.py`, `hivegrid_settings_core.py`, `hivegrid_hex.py` | Logic without UI: agent discovery and states, git scanning and CI, settings and accounts, hex geometry. |
| `bin/claude-quota`, `omp-switch`, `omp-grid` | Usage fetcher and history, chat transfer between omp profiles, grid launcher. |
| `ext/hivegrid-agent-state.ts` | omp extension that writes exact agent states for the hive and dock. |
| `tests/` | Python `unittest` in `tests/test_*.py`; Lua in `tests/lua/` (own runner, fake `wezterm` and mux); bun tests in `tests/ext/`. |
| `wezterm/` | Submodule: the WezTerm fork (clickable header, overlays, hover, web pane). `wezterm/HIVEGRID.md` documents it. |
| `dev/` | `check`, `smoke`, the QA sandbox `qa/`, the demo recorder `demo/`. |
| `docs/` | Detail pages linked from the README. |

## Conventions

- Follow the neighbouring code before adding a pattern. Python is standard library only (PIL is optional); Lua targets 5.4 and WezTerm's API; shell passes `shellcheck --severity=warning`.
- One vocabulary for agent states (`working`, `busy`, `needs you`, `done`, `idle`, `exited`, plus `shell` for a plain shell and `web` for a web pane), glyphs and colours, shared by the dock, hive, picker and header.
- Users see dock numbers (`3`, `tab·number`), never internal pane ids.
- Every feature degrades on stock WezTerm: gate on `theme.has_click` / `theme.has_web`, register nothing and raise no Lua error when the fork is missing.
- Settings live in `~/.config/hivegrid/settings.json` (a missing file means the defaults). Lua and Python read the same file and must agree (account pairing, the web URL allowlist); change both sides and their tests together.
- Chrome programs (bar, dock, views) set the padding colour with OSC 11; mouse reports can be split across reads (`hivegrid_ui.split_complete`); a user var set by an overlay program right before it exits is lost, so wait for an acknowledgement (see `config/picker.lua`).
- Do not call `wezterm cli` from Lua event handlers (it can freeze the GUI); use the Lua API. Page-controlled text (a web page title) must never reach a terminal unfiltered.
- A user-visible change updates `README.md` or the matching `docs/` page in the same commit. Docs describe the current behaviour, not history.

## Tests

- Add a test for every bug you fix and behaviour you add, next to the existing ones, deterministic and independent of the machine (no real home, network, display or installed Chrome). Do not test wiring, mock echoes or source text.
- Lua: `lua5.4 tests/lua/run.lua [file...]`; the helpers are documented at the top of `tests/lua/t.lua` (`T.test`, `T.fresh`, `T.setenv`, `T.tmpdir`).
- Python: `python3 -m unittest discover -s tests -p 'test_*.py'`.
- Visual or timing changes need a sandbox run and a screenshot look at 1280x720, 1920x1080 and 2560x1440; `dev/check` alone is not proof.

## The WezTerm fork

`wezterm/` is a separate repository (branch `hivegrid`) and a Rust build (`wezterm/build-hivegrid.sh`). CI does not build it. Build a test fork only into `/tmp` (`OPT=/tmp/x WRAPPER=/tmp/x/hivegrid wezterm/build-hivegrid.sh`) and run the sandbox against it with `HG_WEZTERM_DIR=/tmp/x dev/qa/hgq restart N`. Commit fork changes in the fork first; the main repository only records the submodule pointer.
