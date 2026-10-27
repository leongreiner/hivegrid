# Security policy

Hivegrid is alpha software. Only the latest release and `main` are supported.

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private reporting instead: the repository's **Security** tab, **Report a vulnerability**. Include what you found, how to reproduce it and what an attacker gains. You will get an answer within a week.

## Scope worth knowing

Hivegrid runs on your machine with your user's permissions and touches sensitive material, so reports in these areas are especially useful:

- `claude-quota` reads OAuth tokens of omp profiles and Claude Code accounts (read-only, in process). Tokens must never be stored, logged or shown.
- `omp-switch` copies omp session files between profiles. It must never overwrite or delete a session.
- The settings panel and the installer write files under `~/.config`, `~/.local` and `~/.cache`. The installer never uses sudo.
- `hivegrid-remote` (phone control over Tailscale, off until you start it) is guarded by one bearer token in `~/.config/hivegrid/remote-token`. The token is equivalent to control of every agent, so it must never be logged, put in a URL that reaches a server, or readable by other users. See [docs/remote.md](docs/remote.md).
- Programs started in panes, and text sent to them (`send-text`, broadcast), run with your privileges.
