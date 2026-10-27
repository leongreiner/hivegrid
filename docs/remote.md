# Remote control from a phone

`hivegrid-remote` is a small web server that shows every Hivegrid agent on your phone and lets you answer them: read the tail of an agent's screen, send a prompt, press a key (`Enter`, `Esc`, arrows, `y`, `n`, `1`..`9`), answer a plan review and acknowledge a finished agent. It is made for reaching your agents over [Tailscale](https://tailscale.com); it listens on the local machine only and is published to your tailnet by `tailscale serve`. Python standard library only; the web page is a single page served by the same program, with no build step and no remote assets.

It lists agents only (the panes the dock calls working, busy, needs you, done, idle and exited). It cannot type into plain shells, open or close panes, jump the window's focus or read files.

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

   The page stores the token in the phone's `localStorage` and removes it from the address. A fragment is never sent to a server, so the token is in no log. "Add to Home Screen" makes a shortcut that opens the page full screen; the page ships no icon of its own, so the browser's default icon is used. After a rotation (or a 401) the page shows a token field again.

An alternative without `tailscale serve` is `hivegrid-remote serve --bind $(tailscale ip -4)`: plain HTTP inside the WireGuard tunnel. It works, but the browser calls it an insecure context and the server needs tailscaled up first. The bound address is allowed as a `Host` automatically, so the phone URL may use that IP or the machine's MagicDNS name; any other name needs `--allow-host`.

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

Every `/api/...` request needs `Authorization: Bearer <token>`; the token is accepted nowhere else (no query string, no cookie). Answers are JSON; errors are `{"ok": false, "error": "<code>", "message": "..."}`.

| Request | What it does |
| --- | --- |
| `GET /api/meta` | Limits, the state vocabulary (glyph, label, colour name), the key names, whether the server is read-only. |
| `GET /api/agents` | Every agent: `id`, `label` (the dock's number, `tab·number` with several tabs), `state`, `title`, `cwd`, `age`, `reason`, a `pending` question, usage, and what can be done to it (`can`). `ETag` / `If-None-Match` answer `304` while nothing changed. |
| `GET /api/agents/{id}/tail?lines=60` | The last 5 to 400 lines of the agent's screen and scrollback, without escape sequences. |
| `POST /api/agents/{id}/prompt` | `{"text": "..."}`: the text and then Enter, as the hive's prompt line does. Refused while the agent waits for an answer or has exited. |
| `POST /api/agents/{id}/keys` | `{"keys": ["y", "enter"], "text": "...", "confirm": false}`: up to 8 keys, each sent on its own; a key is one of the names from `/api/meta` (`enter`, `esc`, `tab`, `shift-tab`, `backspace`, `space`, the four arrows, `ctrl-c`, `ctrl-d`) or any single `[A-Za-z0-9]` character. `ctrl-c` and `ctrl-d` need `"confirm": true`. Refused for an exited agent, so nothing reaches the shell behind it. |
| `POST /api/agents/{id}/plan` | `{"choice": "execute", "feedback": "..."}`: answers a pending plan review (`refine` needs feedback). |
| `POST /api/agents/{id}/ack` | Acknowledges an agent that is `done` or `idle`; `409` for any other state. |

`id` is an internal handle the page never shows; people see the dock's number. Other statuses: `401` bad token, `403` wrong host or origin, or read-only, `404` not an agent, `405` a method other than `GET` and `POST`, `409` the agent's state does not allow it (or a confirmation is missing), `400`, `411` (no `Content-Length`), `413` and `415` malformed requests, `429` too many requests (`Retry-After`), `500` an internal error, `502` `wezterm cli` failed, `503 no_mux` no Hivegrid window. The page's quick keys offer `y`, `n` and `1` to `3`; the API takes any single letter or digit.

## Limits

- Request body at most 16 KiB; a prompt at most 8000 characters; at most 8 keys per request; a tail at most 400 lines (64 KiB, lines cut at 500 characters).
- Prompts, key text and feedback may contain newlines and tabs but no other control character, so no escape sequence can reach a pane.
- Writes: bursts of 10, then 2 per second; reads: bursts of 30, then 15 per second. Five wrong tokens in a minute throttle that client for a minute, doubling while it keeps failing; a request with the valid token always passes, so a stranger's guesses cannot lock you out. A global cap on failures per minute backstops clients that cannot be told apart.
- At most 32 connections at once (more are closed at once), a socket timeout of 10 seconds per connection, and 4 simultaneous `wezterm cli` calls; each call gives up after 4 seconds.
- The page polls (the list every 2 s, the open tail every 1 s) and pauses while the tab is hidden. There are no push notifications; the page title carries the needs-you count.

## Threat model

**The token is control of your agents.** Whoever holds it can send any prompt to any agent, answer approvals and interrupt or quit agents, and an agent that is told to run a command does. Treat it like an SSH key: it is remote code execution as your user through the agents, though not a shell (the API cannot type into plain shells, open panes or read files).

- It sits in plaintext at mode `0600` (the server refuses a file that others can read, a symlink or a file of another owner), and in the phone's `localStorage`. A lost phone: `hivegrid-remote token --rotate` (the running server picks it up at once, no restart) and remove the device in the Tailscale admin console.
- Anyone on your tailnet can reach the port: limit it with a Tailscale ACL that allows port 443 of this machine to your phone's user or tag only.
- Pane text is untrusted. The server strips escape sequences, control and bidi characters and cuts lengths, and the page sets every agent-derived text with `textContent`; its content security policy forbids inline script and any other origin.
- A page on another site cannot drive the server from your phone's browser: the token is no cookie, a write needs `Content-Type: application/json` and an `X-Hivegrid-Remote` header (no CORS answer is ever sent), and the `Host` and `Origin` headers are checked (`*.ts.net`, loopback and `--allow-host` names only).
- Nothing logs a token, a header, a query string or a prompt; a write logs the action and the agent's label.
- There is no idle expiry. The real controls are rotation, `--read-only` and removing the device.

Details about each defence are in the module `bin/hivegrid_remote.py`; `dev/qa/remote-smoke` exercises the server against a sandbox Hivegrid (local only, QA slot 14).
