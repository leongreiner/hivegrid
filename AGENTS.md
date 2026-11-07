# AGENTS.md

Guide for AI coding agents working in this repository. Humans: see [README.md](README.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

Hivegrid is a WezTerm workspace for running many `omp` coding agents in a pane grid: a Lua config (`config/`), Python terminal programs for the panels, bar, dock and git view (`bin/`), an omp extension (`ext/`), and a WezTerm fork as a submodule (`wezterm/`). It is a GUI program: unit tests cover logic, but layout, focus, colours and timing only show in the running window.

## Safety rules (read first)

- **Your shell may run inside the user's live Hivegrid.** `WEZTERM_PANE` and `WEZTERM_UNIX_SOCKET` then point at it, and a bare `wezterm cli ...` drives the user's window. Drive only a sandbox: `dev/qa/hgq cli SLOT ...`.
- **Never run `install.sh` or `sync.sh`, and never write to `~/.config/wezterm`, `~/.local/bin`, `~/.config/hivegrid`, `~/.local/opt/hivegrid-wezterm` or `~/.claude*`.** Deploying Hivegrid is the user's decision.
- **OMP configuration:** Agents may install, update, or configure OMP plugins and MCP servers under `~/.omp` when the user requests it. Limit changes to the requested profile(s), use OMP's supported setup/configuration flows, and preserve unrelated settings and credentials. The user must complete OAuth and account authorization.
- The sandbox shares the real `~/.omp` and `~/.claude*`: omp sessions started there use the user's quota. Keep prompts trivial, run at most 3 to 4 sessions, and quit them afterwards. Never trigger an `omp-switch` transfer there.
- Tests and fixtures use temp directories and fake data, never a real home, account name, token or path. Do not commit secrets, account names or personal paths.
- Do not push, tag, open pull requests or change repository visibility unless asked.

## Commands

```sh
dev/check                  # everything CI runs; `dev/check py lua luacheck bun shell actions` selects steps
dev/qa/hgq help            # isolated Hivegrid on its own Xvfb display: up, x (synthetic input), shot, cli, log, down
dev/smoke                  # end-to-end smoke test in sandbox slot 11 (needs Xvfb and the fork build)
dev/qa/web-smoke           # web pane end to end in slot 12 (needs Chrome and a fork build with the web pane)
dev/qa/remote-smoke        # hivegrid-remote against fake agents in slot 14 (needs a fork build; a Chromium-family browser is optional)
dev/qa/remote-term-smoke   # the phone terminal through a real hivegrid-remote in a throwaway HOME (no fork build, no sandbox slot)
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
| `config/autocontinue.lua` | Types `continue` into an omp pane whose usage limit has reset (once per episode, capped, ledger `autocontinue.json`). See `docs/accounts-and-quota.md`. |
| `bin/hivegrid_ui.py` | Terminal UI toolkit (cell canvas, click regions, design components); every TUI uses it. |
| `bin/hivegrid_panels.py`, `hivegrid_hive.py`, `hivegrid_picker.py`, `hivegrid-bar`, `hivegrid-overview`, `hivegrid-git` | The panels, hive, picker, bar, dock and git view programs. |
| `bin/hivegrid_agents.py`, `hivegrid_repos.py`, `hivegrid_settings_core.py`, `hivegrid_hex.py` | Logic without UI: agent discovery and states, git scanning and CI, settings and accounts, hex geometry. |
| `bin/hivegrid-remote`, `hivegrid_remote.py`, `hivegrid_remote_data.py`, `hivegrid_remote_ui.py`, `hivegrid_remote_term.py`, `hivegrid_remote_attach.py`, `hivegrid_remote_git.py`, `hivegrid_remote_termjs.py`, `hivegrid_remote_notify.py`, `hivegrid_remote_push.py` | Phone remote over Tailscale: HTTP API and token handling (`hivegrid_remote.py`), session-file chat parser, usage-limit detection, accounts, system metrics, new-session options and account switching (`hivegrid_remote_data.py`), the web page and its service worker as Python string constants (`hivegrid_remote_ui.py`), the opt-in phone terminal: pty shells (`hivegrid_remote_term.py`) and the in-page emulator and view (`hivegrid_remote_termjs.py`), phone alerts: ntfy messages and the agent-state watcher (`hivegrid_remote_notify.py`), Web Push with the optional `cryptography` package, the VAPID key and `push.json` (`hivegrid_remote_push.py`), the entry shim. See `docs/remote.md`. |
| `bin/hivegrid-autostart`, `hivegrid_autostart.py` | Crash and reboot recovery: user services for Hivegrid and hivegrid-remote, and the sudo-only kernel panic reboot, hardware watchdog and auto-login. Changes `/etc` only when the user passes the flag. See `docs/autostart.md`. |
| `bin/hivegrid-schedule`, `hivegrid_schedule.py` | Scheduled `omp` agents: schedule store, cron and plain-text `when` parser, catch-up, overlap, retry and breaker policies, run records and results inbox, prompt composition, summary file and control inbox, CLI. No UI. See `docs/schedules.md`. |
| `bin/hivegrid-scheduler`, `hivegrid_scheduler.py` | The scheduler daemon (`hivegrid-scheduler.service`, installed by `hivegrid-autostart`): fires due bots, runs headless `omp` or, for `visible` and `hybrid` bots, an interactive `omp` in a tab of the Bots window of the running Hivegrid (headless when there is no window), quota guard, retries, account failover, push notifications. See `docs/schedules.md`. |
| `config/pages.lua`, `bin/hivegrid-host-page`, `hivegrid_hostpage.py` | The remote host pages: another machine's remote page as a page of the window (`[Canvas] [Bots] [host]` in the header), the hosts file `remote-hosts.json`, per-host token files and the one-shot page. See `docs/remote.md` ("Remote host page"). |
| `bin/hivegrid_remote_bots.py` | The phone's Bots API (`/api/bots`, limits for phone-made bots), read and written through `hivegrid_schedule`. See `docs/remote.md`. |
| `config/pages.lua`, `bin/hivegrid-bots-page`, `hivegrid_botspage.py` | The header's page switcher `[Canvas] [Bots]` and the Bots page: the phone's page in a web pane over the active tab, with the local `hivegrid-remote` started on demand. See `docs/schedules.md` ("Host page"). |
| `bin/hivegrid_panels.py` (`BotsPage`), `config/bots.lua` | The Bots page in the terminal (`hivegrid-panel bots`, the fallback without a web pane), and the header chip and toasts that read the scheduler's runtime summary. |
| `bin/claude-quota`, `omp-switch`, `omp-grid` | Usage fetcher and history, chat transfer between omp profiles, grid launcher. |
| `ext/hivegrid-agent-state.ts` | omp extension that writes exact agent states for the hive and dock. |
| `tests/` | Python `unittest` in `tests/test_*.py`; Lua in `tests/lua/` (own runner, fake `wezterm` and mux); bun tests in `tests/ext/`. |
| `wezterm/` | Submodule: the WezTerm fork (clickable header, overlays, hover, web pane). `wezterm/HIVEGRID.md` documents it. |
| `dev/` | `check`, `smoke`, the QA sandbox `qa/`, the demo recorder `demo/`. |
| `docs/` | Detail pages linked from the README. |

## Conventions

- Follow the neighbouring code before adding a pattern. Python is standard library only (PIL is optional); Lua targets 5.4 and WezTerm's API; shell passes `shellcheck --severity=warning`.
- One vocabulary for agent states (`working`, `busy`, `needs you`, `limit`, `done`, `idle`, `exited`, plus `shell` for a plain shell and `web` for a web pane), glyphs and colours, shared by the dock, hive, picker and header.
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

`install.sh` downloads the pre-built fork from the latest GitHub release (`.github/workflows/release.yml`, started by hand only; `dev/package-wezterm` makes the same archive from a local build). Test installer changes in a throwaway `HOME` with `HIVEGRID_WEZTERM_URL=file://...` and `HIVEGRID_SRC_URL=file://...` pointing at local archives; never run it against the real home (see the safety rules).
