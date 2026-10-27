# Claude usage and switching accounts

`claude-quota` fetches Claude Code usage for each account and caches only the percentages and reset times in `~/.cache/wezterm-claude-quota/` (plus `history.jsonl`, see Panels). The header reads that cache; the network is never touched from the GUI thread. It reads the OAuth token in-process, first from the account's paired omp profile (the `anthropic` OAuth row in `~/.omp/profiles/<profile>/agent/agent.db`, opened read-only; omp keeps it fresh while it runs) and otherwise from the `.credentials.json` of the account directory (which only the `claude` CLI refreshes). It calls the usage endpoint with caching and backoff, never refreshes, stores or logs tokens, and shows `login` only when neither source holds a valid token and nothing fresher came in for a day. A window whose reset time has passed reads 0% instead of stale. `claude-quota install-statusline` adds Claude Code's documented `statusLine` hook to each account's `settings.json` (an existing status line command is kept and chained, a rerun changes nothing): `claude-quota statusline` stores the `rate_limits` of the session JSON as that account's numbers, with no token involved, and prints the account's usage as the status line unless `--chain` names another command. Re-run it after adding an account. Run `claude-quota refresh` or `claude-quota show` by hand to check it.

The accounts are, in order of precedence: `CLAUDE_QUOTA_DIRS` (or `--dirs`), a comma-separated list; the list in `~/.config/hivegrid/settings.json` (hidden entries skipped) plus every `~/.claude-*` directory holding a `.credentials.json` that the list does not mention; or, without a settings file, those discovered directories alone.

## Switching accounts without losing the chat

Clicking an account pill (or `Alt+q`, or `LEADER+u <n>`) in a pane that runs a shell starts `omp --profile=<profile>` (a bare `omp` for an account without a profile). In a pane that already runs omp under another profile it moves the chat over (a chat moves only between omp profiles: an account without a profile starts a bare `omp`, but a running omp is not moved to or from it):

1. `omp-switch locate` finds the session (omp records each terminal's session in `<profile>/agent/terminal-sessions/pts-N`; only paths are read, never the conversation);
2. omp is asked to quit with `Ctrl+D` and has five seconds; with unsent text in its prompt it stays, the switch is abandoned and a toast says so (never `kill`);
3. `omp-switch transfer` copies the finished session file, its artifact directory and the blobs it references into the other profile under the same names, without overwriting anything (a copy of an older part of the same chat is brought up to date; a different chat under that name aborts) and without touching the first profile's files;
4. `omp --profile=<new> --resume '<copy>'` continues the same chat (a fresh `omp --profile=<new>` when no message was written yet; a `cd` first when the shell is not where omp ran).

If the pane looks busy (agent working or asking), a dialog asks "switch anyway?" first, default No; Yes sends `Esc` (omp's interrupt) before quitting.
