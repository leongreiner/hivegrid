# Remote control from a phone

`hivegrid-remote` is a small web server that shows every Hivegrid agent on your phone and lets you answer them: follow an agent's chat live (built from its omp session file) or the tail of its screen, send a prompt, press a key (`Enter`, `Esc`, arrows, `y`, `n`, `1`..`9`), answer a plan review, acknowledge a finished agent, change its model and thinking level, and start a new omp session on one of your accounts. It also reports each account's quota and token usage and the machine's CPU, memory, disk and GPU. It is made for reaching your agents over [Tailscale](https://tailscale.com); it listens on the local machine only and is published to your tailnet by `tailscale serve`. Python standard library only; the web page is a single page served by the same program, with no build step and no remote assets.

It lists agents only (the panes the dock calls working, busy, needs you, done, idle and exited). It cannot type into plain shells, close panes, jump the window's focus or read arbitrary files: the only files it reads for the phone are an agent's own omp session file (see below), the quota cache numbers, the monitor log and the terminal's font. The one pane it can open is a new omp session from `POST /api/sessions`, in a directory and on an account you offered it.

## Set up

1. **Create the token.** It is a 256-bit random secret, kept in `~/.config/hivegrid/remote-token` (mode `0600`; next to `settings.json`, so `HIVEGRID_SETTINGS` moves it too):

   ```sh
   hivegrid-remote token            # creates it and prints it; with a file already there it prints the path
   hivegrid-remote token --show     # prints it again
   hivegrid-remote token --rotate   # replaces it; the running server accepts only the new one at once
   ```

2. **Start the server** (from a shell on the machine that runs Hivegrid):

   ```sh
   hivegrid-remote serve            # http://127.0.0.1:8787/
   ```

   It exits with status 1 when the token file is missing or unsafe (run `hivegrid-remote token` first), never creates a token itself and never prints it. It finds the Hivegrid window's socket by itself, so it also works from a service that was not started inside Hivegrid, starts without a window (the API answers `503 no_mux` until one exists) and follows a Hivegrid restart without a restart of its own. When a stock WezTerm runs besides Hivegrid, pass the Hivegrid window's socket with `--socket` (and `--wezterm` for its `wezterm` binary) so the server does not pick the wrong one.

3. **Publish it to your tailnet** (HTTPS needs MagicDNS and HTTPS certificates enabled in the Tailscale admin console):

   ```sh
   tailscale serve --bg --https=443 http://127.0.0.1:8787
   tailscale serve status
   tailscale serve --https=443 off      # remove it again
   ```

   The `tailscale serve` flags differ between Tailscale versions: check `tailscale serve --help` on your machine before you run the line above. Never use `tailscale funnel`: it publishes the page to the whole internet.

4. **Open it on the phone** (the phone is signed in to the same tailnet), once, with the token in the URL fragment:

   ```text
   https://<machine>.<tailnet>.ts.net/#t=<token>
   ```

   The page stores the token in the phone's `localStorage` and removes it from the address. A fragment is never sent to a server, so the token is in no log. "Add to Home Screen" makes a shortcut that opens the page full screen with the Hivegrid icon (`/icon.png`, `/logo.svg`). After a rotation (or a 401) the page shows a token field again.

An alternative without `tailscale serve` is `hivegrid-remote serve --bind $(tailscale ip -4)`: plain HTTP inside the WireGuard tunnel. It works, but the browser calls it an insecure context and the server needs tailscaled up first. The bound address is allowed as a `Host` automatically, so the phone URL may use that IP or the machine's MagicDNS name; any other name needs `--allow-host`.

**Tailscale ACL.** A tailnet with a custom policy can drop the phone's traffic even though ping works (`tailscale ping` is ICMP; the page is TCP 443). If the phone is a tagged device, or your policy has no rule from it to this machine, add a grant that allows only this machine's HTTPS port, for example:

```json
"hosts":  { "my-workstation": "100.x.y.z" },
"grants": [ { "src": ["tag:trusted"], "dst": ["my-workstation"], "ip": ["tcp:443"] } ]
```

(`src` is your phone's tag or user; `100.x.y.z` is `tailscale ip -4` on the workstation.) This is the only network barrier besides the token, so keep it narrow.

## Run it as a user service

A systemd user unit keeps the server running after a login. Run `hivegrid-remote token` once first: without a token file the server exits with status 1, and `RestartPreventExitStatus=1 2` (1: no or unsafe token file, bad setup; 2: bad arguments) stops systemd from restarting it in a loop. Save it as `~/.config/systemd/user/hivegrid-remote.service` (Hivegrid does not install it):

```ini
[Unit]
Description=Hivegrid remote control (phone UI over Tailscale)

[Service]
ExecStart=%h/.local/bin/hivegrid-remote serve
Restart=on-failure
RestartPreventExitStatus=1 2
RestartSec=3
UMask=0077
NoNewPrivileges=yes
LockPersonality=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6

[Install]
WantedBy=default.target
```

```sh
systemctl --user daemon-reload
systemctl --user enable --now hivegrid-remote
journalctl --user -u hivegrid-remote -f
```

The unit has no `ProtectHome` or `ProtectSystem` on purpose: the server writes `acks.json` and `seen.json` below `$XDG_RUNTIME_DIR/hivegrid-agents`, and `wezterm cli` writes logs of its own. It has no `PrivateTmp` either: in a user service that runs the server in a user namespace, where it can no longer read `/proc/<pid>/exe` of the Hivegrid window, so it finds no window and answers `503 no_mux`.

Use the path of the program in `ExecStart`: `%h/.local/bin/hivegrid-remote` after `install.sh`, or the repo's `bin/hivegrid-remote` when you run it from a checkout. The server finds the Hivegrid window by itself, so it needs no environment from a Hivegrid pane.

## If the page does not work

| Symptom | Cause and fix |
| --- | --- |
| The phone times out, while `curl https://<machine>.<tailnet>.ts.net/healthz` on the workstation prints `ok` | The Tailscale ACL blocks TCP 443 from the phone (see above), or the phone cannot resolve the name (Android Private DNS). |
| `502` from the tailnet URL | `hivegrid-remote` is not running: `systemctl --user status hivegrid-remote`. |
| The page loads but says "no Hivegrid window" (`503 no_mux`) | No Hivegrid window is open, a stock WezTerm was picked instead (pass `--socket`), or the unit has `PrivateTmp` (remove it). |
| The token field appears again | The token was rotated or the site data cleared: open the `#t=` link once more (`hivegrid-remote token --show`). |

## Read-only mode

`hivegrid-remote serve --read-only` (or `HIVEGRID_REMOTE_READ_ONLY=1`) removes every write: all `POST` requests answer `403 readonly`, the page hides its controls and shows an amber `read-only` chip. Agents, states and tails stay visible. Use it for a phone you only want to watch with.

## Options

| Flag | Environment variable | Default |
| --- | --- | --- |
| `--bind ADDR` | `HIVEGRID_REMOTE_BIND` | `127.0.0.1` |
| `--port N` | `HIVEGRID_REMOTE_PORT` | `8787` |
| `--token-file PATH` | `HIVEGRID_REMOTE_TOKEN_FILE` | `~/.config/hivegrid/remote-token` |
| `--read-only` | `HIVEGRID_REMOTE_READ_ONLY=1` | off |
| `--allow-host NAME` (repeatable) | `HIVEGRID_REMOTE_ALLOW_HOSTS` (comma separated) | loopback, `*.ts.net` and the bound address |
| `--socket PATH` | `HIVEGRID_REMOTE_SOCKET` | found from the running Hivegrid window |
| `--wezterm PATH` | `HIVEGRID_REMOTE_WEZTERM` | the `wezterm` next to that window's `wezterm-gui` (a `wezterm-gui` path is swapped for its sibling `wezterm`) |
| `--allow-any-bind` | | off |

`--bind` accepts only loopback, a Tailscale address (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`) or, with `--allow-any-bind`, anything else (`0.0.0.0`, a LAN address). The settings file has no keys for it, and the Lua config never reads this.

## API

Every `/api/...` request needs `Authorization: Bearer <token>`; the token is accepted nowhere else (no query string, no cookie). Answers are JSON; errors are `{"ok": false, "error": "<code>", "message": "..."}`. Without a token the server serves only the page (`/`, `/app.js`, `/app.css`, `/theme.css`, `/manifest.webmanifest`), `/logo.svg`, `/icon.png`, the two font routes and `/healthz`.

| Request | What it does |
| --- | --- |
| `GET /api/meta` | Limits, the state vocabulary (glyph, label, colour name), the key names, whether the server is read-only, and `capabilities` (`chat`, `accounts`, `system`, `sessions`, `model`, `longpoll`: what this server version offers). |
| `GET /api/agents?wait=S` | Every agent: `id`, `label` (the dock's number, `tab·number` with several tabs), `state`, `title`, `cwd`, `since` and `age`, `reason`, a `pending` question, usage, `model` (`{id, name, provider}`), `thinking`, `context` (`{tokens, window, pct}`), `session` (a chat is available) and what can be done to it (`can`, with `model`). `ETag` / `If-None-Match` answer `304` while nothing changed; with `wait=S` (0 to 20 seconds) the server holds a request whose ETag still matches and answers as soon as something changed, else `304` at the timeout. The revision holds no age: the page computes ages from `since`. |
| `GET /api/agents/{id}/tail?lines=60&wait=S` | The last 5 to 400 lines of the agent's screen and scrollback, without escape sequences; `ETag` and `wait` as above. |
| `GET /api/agents/{id}/chat?limit=60&before=N&rev=R&wait=S` | The chat of the agent, parsed from its omp session file: the last `limit` (1 to 200) items whole, each `{n, ts, role (user, assistant, tool, note), kind (text, thinking, tool, image, note), text, tool}` with `tool` = `{name, summary, status (ok, error, running), output}` for a tool call and its result. `before=N` (a positive item number, `400` otherwise) returns the older items with `n < N` (`older: true`). With `rev=R` equal to the current revision the request is held up to `wait` seconds and answers `{"unchanged": true, "rev": R}` when nothing happened. `source` is `screen` (and `items` empty) when no session file is available: the page then shows the tail. |
| `GET /api/agents/{id}/models` | `{current, thinking, levels, models: [{id, name, provider, reasoning}]}`: what the agent's omp can switch to (empty for an omp that was started before the extension was updated). |
| `GET /api/accounts` | The header's accounts (hidden ones excluded) with `state` (`ok`, `expired`, `login`, `error`, `unknown`), the five-hour and seven-day quota windows (`pct`, `reset_at`) from the claude-quota cache, and the tokens and cost of the live agents running on each; plus totals. No credentials, no directories. |
| `GET /api/system` | CPU %, cores, load, memory, disk of `$HOME`, GPU (`nvidia-smi`, when there is one), uptime and up to 120 points of the last 6 hours of the monitor log (`~/.cache/hivegrid/sysstats.jsonl`). |
| `GET /api/sessions/options` | `{profiles: [{label, profile, default?, note?}], dirs: [{path, label}]}`: the accounts and the directories a new session may start in (`~`, the working directories of live agents, up to 60 subdirectories of `~/Projects`). An account without a paired omp profile can only start omp's default configuration, so all of them collapse into one entry (the `main` account when it is one of them) with `profile: null`, `default: true` and a `note` that says so. Needs no Hivegrid window. |
| `POST /api/sessions` | `{"profile": "<name>" or null, "dir": "~/Projects/x"}`: starts `omp` (`omp --profile=<name>` with a profile) in a new pane of the Hivegrid window, like the hive's "new agent". Only a `dir` from the options and a `profile` of a listed account are accepted (`400` otherwise); a profile needs the plain `omp` command (`HIVEGRID_AGENT_CMD` unset). Answers `{"ok": true, "id": N}`. Starting sessions has its own limit (3 at once, then one every 5 seconds: `429`), and the same directory and account is not started twice within 10 seconds (`409 conflict`, so a retried tap cannot start two). |
| `POST /api/agents/{id}/model` | `{"model": "provider/id"}`: switches the agent's model (a value from its `models` list; `400` otherwise). `409 agent_not_ready` while the agent is working, busy, waiting for an answer or exited, or when its omp does not publish its model. Answers `{"ok": true, "confirmed": true}` once the agent's state file shows the change (up to 3 seconds), `confirmed: false` otherwise. |
| `POST /api/agents/{id}/thinking` | `{"level": "high"}`: the same for the thinking level (one of the levels from `models`). |
| `POST /api/agents/{id}/prompt` | `{"text": "..."}`: the text and then Enter, as the hive's prompt line does. Refused while the agent waits for an answer or has exited. |
| `POST /api/agents/{id}/keys` | `{"keys": ["y", "enter"], "text": "...", "confirm": false}`: up to 8 keys, each sent on its own; a key is one of the names from `/api/meta` (`enter`, `esc`, `tab`, `shift-tab`, `backspace`, `space`, the four arrows, `ctrl-c`, `ctrl-d`) or any single `[A-Za-z0-9]` character. `ctrl-c` and `ctrl-d` need `"confirm": true`. Refused for an exited agent, so nothing reaches the shell behind it. |
| `POST /api/agents/{id}/plan` | `{"choice": "execute", "feedback": "..."}`: answers a pending plan review (`refine` needs feedback). |
| `POST /api/agents/{id}/ack` | Acknowledges an agent that is `done` or `idle`; `409` for any other state. |

Static routes: `/logo.svg` and `/icon.png` (the Hivegrid icon, `no-cache` with an `ETag`), and `/fonts/mono.ttf` and `/fonts/mono-bold.ttf`, the terminal's font read from the machine that runs the server (`~/.local/share/fonts/DejaVuSansMNerdFont/DejaVuSansMNerdFontMono-{Regular,Bold}.ttf`, else DejaVu Sans Mono below `/usr/share/fonts/truetype/dejavu/`; `404` when neither exists, then the page uses the phone's monospace font). The repository ships no font.

`id` is an internal handle the page never shows; people see the dock's number. Other statuses: `401` bad token, `403` wrong host or origin, or read-only, `404` not an agent, `405` a method other than `GET` and `POST`, `409` the agent's state does not allow it (or a confirmation is missing, or the same session was just started: `conflict`), `400`, `411` (no `Content-Length`), `413` and `415` malformed requests, `429` too many requests (`Retry-After`), `500` an internal error, `502` `wezterm cli` failed, `503 no_mux` no Hivegrid window.

**The chat.** The chat reads the agent's omp session file (`.jsonl`), found from the state file's `sessionFile`, else from the file the agent's process holds open (when it holds several: the one named after the state file's session id, then the terminal record's, then a top-level session file rather than a subagent's under the session's artifact directory, then the newest), else from omp's `terminal-sessions` record. Only a regular file of your user with the suffix `.jsonl` whose real path lies in `~/.omp/profiles/<profile>/agent/sessions/` or `~/.omp/agent/sessions/` is read; anything else (another path in the state file, a symlink that leads out, another owner) is ignored and the page falls back to the screen. The first look reads the last 4 MiB of the file, later looks only what was appended, older history is read on request (at most 32 MiB back, and never more than 5000 items per session: past that `older` is false). omp's session file is a tree (`id` and `parentId`; going back to an earlier message and continuing leaves the old path in the file): the chat shows the path that ends at the last message. A file that is rewritten (a new inode, or in place with other first or last bytes) is read afresh. A line over 16 MiB is not parsed; a `[line too large]` note stands in its place. Lines that are not JSON are skipped; text is stripped of escape and control characters and cut (12000 characters per item, 1500 for a tool output); images show as `[image]`.

**Model and thinking.** The omp extension `ext/hivegrid-agent-state.ts` publishes the model, thinking level and context fill in the agent's state file, the switchable models in `<pane>.models.json` next to it, and executes `set_model` and `set_thinking` command files like the plan-review answers. An omp process that started before the extension was updated has the old extension: it shows no model and the page hides the controls until it is restarted.

## Limits

- Request body at most 16 KiB; a prompt at most 8000 characters; at most 8 keys per request; a tail at most 400 lines (64 KiB, lines cut at 500 characters).
- Prompts, key text and feedback may contain newlines and tabs but no other control character, so no escape sequence can reach a pane.
- Writes: bursts of 10, then 2 per second; reads: bursts of 30, then 15 per second; starting a session: 3 at once, then one every 5 seconds. A held request (long poll) pays one read token when it arrives, not one per look. Five wrong tokens in a minute throttle that client for a minute, doubling while it keeps failing; a request with the valid token always passes, so a stranger's guesses cannot lock you out. A global cap on failures per minute backstops clients that cannot be told apart.
- At most 32 connections at once (more are closed at once) and 8 per client, a socket timeout of 10 seconds per connection, and 4 simultaneous `wezterm cli` calls (also `nvidia-smi`); each call gives up after 4 seconds (2 for `nvidia-smi`). A GPU answer is cached for 3 seconds, and the lookup repeats only every 60 seconds while there is no GPU (no `nvidia-smi`, or it failed).
- A long poll (`wait=` up to 20 seconds) holds one connection, looks about once a second (the chat: twice a second, a file stat) and holds no lock while it waits. Held tails of one pane share one `wezterm cli` read per second. A phone keeps about three of them (list, chat, tail), well inside the per-client cap; a held request ends at once when the server shuts down, and within a look when the client goes away (the page aborts its polls all the time), so its connection slot is free again.
- A chat answer carries at most 200 items; the parser keeps at most 5000 parsed items of each of up to 8 session files in memory (the oldest go first).
- A chat answer carries `gen`, a number that changes when the session file is parsed afresh (it was replaced or rewritten, so item numbers `n` no longer match earlier answers); the page then drops the items and nodes it holds for that chat. It is `0` when `source` is `screen`.
- The page long-polls (the list, the open chat or tail) with timeouts and restarts when the tab becomes visible or the network returns. There are no push notifications; the page title carries the needs-you count.

## Threat model

**The token is control of your agents.** Whoever holds it can send any prompt to any agent, answer approvals and interrupt or quit agents, and an agent that is told to run a command does. Treat it like an SSH key: it is remote code execution as your user through the agents, though not a shell (the API cannot type into plain shells or close panes, and reads no file but the ones named above). `POST /api/sessions` does start a new agent, so it is one more way to run work on your accounts' quota: it accepts only the directories and accounts it listed itself (a `dir` outside `~` and `~/Projects/*` and the live agents' directories is `400`; the profile is validated as a plain name and shell-quoted) and `--read-only` refuses it.

- It sits in plaintext at mode `0600` (the server refuses a file that others can read, a symlink or a file of another owner), and in the phone's `localStorage`. A lost phone: `hivegrid-remote token --rotate` (the running server picks it up at once, no restart) and remove the device in the Tailscale admin console.
- Anyone on your tailnet can reach the port: limit it with a Tailscale ACL that allows port 443 of this machine to your phone's user or tag only.
- The chat shows conversation text, tool calls and tool output of your agents (code, file contents, command output): treat the token as read access to that. Session files are opened only below `~/.omp/` as described above, so a state file or a symlink cannot make the server read another file; `/api/accounts` returns quota numbers and usage sums only (never a credential, token, directory or the cache file's other fields).
- Pane text and session text are untrusted. The server strips escape sequences, control and bidi characters and cuts lengths, and the page sets every agent-derived text with `textContent`; its content security policy forbids inline script and any other origin.
- The directory of the agents' state files (`$XDG_RUNTIME_DIR/hivegrid-agents`, else `/tmp/hivegrid-agents`) drives prompts, model switches and which session file is read, so the server refuses to start when it is not a real directory of your user that others cannot write to.
- A page on another site cannot drive the server from your phone's browser: the token is no cookie, a write needs `Content-Type: application/json` and an `X-Hivegrid-Remote` header (no CORS answer is ever sent), and the `Host` and `Origin` headers are checked (`*.ts.net`, loopback and `--allow-host` names only).
- The fonts are public system files of the machine and are served without the token (the `Host` check still applies); they reveal nothing the browser could not get from any machine with the same package. The content security policy allows fonts, images and everything else from the same origin only.
- Nothing logs a token, a header, a query string, a prompt, chat text or a path; a write logs the action and the agent's label (a new session logs the account name only).
- There is no idle expiry. The real controls are rotation, `--read-only` and removing the device.

Details about each defence are in the module `bin/hivegrid_remote.py`; `dev/qa/remote-smoke` exercises the server against a sandbox Hivegrid (local only, QA slot 14).
