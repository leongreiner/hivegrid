# Schedules ("Bots")

`bin/hivegrid_schedule.py` keeps **schedules**: one-time and recurring tasks that run an `omp` agent in the background, and persistent single-purpose **agents** that each have a standing task, an identity and a memory file. `bin/hivegrid-schedule` is its command line. The library is standard-library Python with no side effects at import; every function that touches the disk takes explicit paths or reads `HOME` / `XDG_*` when it is called.

```sh
hivegrid-schedule add --name digest --prompt "Summarize my unread mail" --when "weekdays 9am" --cwd ~/notes
hivegrid-schedule list            # table; --json for machines
hivegrid-schedule parse "every monday at 7"
```

## Files

| Path | Content |
| --- | --- |
| `$XDG_CONFIG_HOME/hivegrid/schedules.json` (default `~/.config/hivegrid/`) | The definitions: `{"version": 1, "schedules": [...]}`, mode 0600. |
| `schedules.json.lock` | Sibling lock file. Every change runs under an exclusive `flock`, is validated, and is written through a temp file and `os.replace`. Readers re-read when the file's mtime, size or inode changed. |
| `$XDG_STATE_HOME/hivegrid/schedules/<id>/` (default `~/.local/state/`) | Per-schedule runtime state, mode 0700. |
| `state.json` | Bookkeeping: `last_slot`, `last_slot_local`, `baseline`, `running` (`{run, pid, started}`, plus `chat: true` for a chat turn), `queued`, `run_now`, `run_count`, `consec_failures`, `paused_reason`, `next_override`, `done`, `session_file` (the bot's main session), `chat_queue`, `chat`. |
| `runs.jsonl` | Append-only run records, the newest 200 are kept (older ones, chat turns first, and their reports, are dropped by rewriting the file atomically). |
| `identity.md` | The text for `--append-system-prompt` (see below). |
| `memory.md` | The agent's memory (kind `agent`, or `identity.memory`). |
| `reports/NNNN.md` | The report each run writes. |
| `notes/` | Notes left with `hivegrid-schedule self note` (the newest 50). |
| `$XDG_RUNTIME_DIR/hivegrid-schedule/` (fallback `/tmp/hivegrid-schedule-<uid>/`) | Control inbox: `cmd.<ns>-<pid>.json`, one command each. |
| `$XDG_RUNTIME_DIR/hivegrid-schedule.json` | Runtime summary for panels and the phone (`build_summary`, `write_summary`). |

Every directory the library writes to must be a real directory of the current user that others cannot write to; a symbolic link, a foreign owner or mode `g+w`/`o+w` is refused with an error, as is a symlinked or foreign `schedules.json`. A `schedules.json` that is not valid JSON, or was written by a newer version, is never overwritten. A record that fails validation stays in the file untouched and is skipped (`hivegrid-schedule doctor` lists it).

## A schedule

```json
{"id": "digest-k3x", "name": "digest", "kind": "task", "enabled": true,
 "when": {"type": "cron", "expr": "0 9 * * 1-5", "tz": "local"},
 "task": {"prompt": "Summarize my unread mail"},
 "run": {"cwd": "~/notes", "account": null, "model": null, "thinking": "medium", "mode": "headless",
         "permissions": "edit", "max_time_s": 1800, "max_cost_usd": 2.0, "add_dirs": []},
 "policy": {"catchup": "latest", "grace_s": 7200, "overlap": "skip", "retries": 2, "backoff_s": [300, 1800],
            "quota_guard_pct": 90, "fallback_accounts": [], "pause_after_failures": 5},
 "identity": {"system": "", "memory": false, "continuity": "fresh", "memory_max_bytes": 8192},
 "notify": {"on": ["failed", "limit", "missed"], "done": false},
 "origin": "cli", "created": 1793548499, "updated": 1793548499}
```

Everything but `id`, `name`, `when` and `task.prompt` has the default shown. Unknown keys are rejected with a message. Rules:

- `id`: `[a-z0-9-]{3,32}`, generated as the name's slug plus 3 random characters.
- `name`: 1 to 80 characters, no control characters. `task.prompt`: at most 20000 characters.
- `kind` is `task` or `agent`. An `agent` needs a non-empty `task.prompt` (its standing task) and always has memory (`identity.memory` is forced true).
- `run.cwd` is stored as written; `~` is expanded when used and the result must be absolute. `thinking`: `off minimal low medium high xhigh max auto`. `mode`: `headless`, `visible`, `hybrid`. `permissions`: `readonly`, `edit`, `full`. `max_time_s` 60 to 86400, `max_cost_usd` above 0 and at most 100.
- `permissions: "full"` needs `max_time_s` and `max_cost_usd` written explicitly in `run`, and is refused for `origin: "phone"` (also when a phone edit would make it so).
- `origin` (`cli`, `panel`, `phone`) says who created it.
- `notify.on` lists the events that notify (`failed`, `limit`, `missed`, `done`); `notify.done: true` adds `done`.
- `continuity` (`fresh` or `resume`) says whether a scheduled run starts a new omp session or continues the bot's main session. The default is `resume` for an `agent` (one persistent session: its identity and memory carry on in it) and `fresh` for a `task`; `fresh` written explicitly keeps an agent starting anew. `memory_max_bytes` is 512 to 65536.

## When

`when` is one of:

| Form | Example |
| --- | --- |
| `{"type": "cron", "expr": "0 9 * * 1-5", "tz": "local"}` | 5-field cron in `local` or an IANA zone such as `Europe/Berlin` |
| `{"type": "interval", "every_s": 1800, "anchor": 1793548499}` | fires at `anchor + k * every_s`, `k >= 1` |
| `{"type": "once", "at": "2026-11-03T09:00", "tz": "local"}` | one local time, seconds optional |

The cron grammar is `minute hour day-of-month month weekday` with lists (`1,15`), ranges (`mon-fri`), steps (`*/15`, `10-50/20`, `30/20`), names (`jan`..`dec`, `sun`..`sat`, full names too) and the macros `@hourly @daily @midnight @weekly @monthly @yearly`. Weekday `0` and `7` are Sunday. As in Vixie cron, when both day-of-month and weekday are restricted a day matches if **either** matches (`0 9 13 * fri` is every 13th and every Friday); if one of them starts with `*` both must match. The shortest recurrence and the shortest `interval` is 300 seconds.

Daylight saving time, for cron and `once` in `local` or a named zone: a local time that does not exist (spring forward, `02:30`) fires once at the shifted time; a local time that happens twice (fall back) fires once, the first time. `next_fire` dedupes through `last_slot_local`, the naive local slot string `YYYY-MM-DDTHH:MM` of the slot handled last.

### Plain text

`hivegrid-schedule parse TEXT` (and `--when` of `add` and `edit`) read these, case-insensitively, as a pure function of the current time with no model involved:

```
0 9 * * 1-5              any cron expression, @daily
every 30m   every 2h   every 1d
daily at 9    daily 09:00    every day at 9am
weekdays 9:30    every weekday at 8pm    weekends 10:00
every monday at 7    monday and friday 9am
hourly    every hour    weekly    monthly on the 1st at 9
once 2026-11-03 09:00    2026-11-03T09:00
tomorrow 9am    today 17:30    at 17:00 (the next occurrence)
in 2h    in 45 minutes
```

`--tz` selects the zone of wall-clock words. A bad text reports what to try; a once time in the past, a repeat under 5 minutes and an empty day list are errors.

`describe()` gives the short form shown in lists: `weekdays 09:00`, `daily 09:00`, `weekends 10:00`, `mon,fri 09:00`, `hourly`, `every 30m`, `monthly 1st 09:00`, `once Nov 3 09:00`; other cron expressions read `cron <expr>`, and a zone other than `local` is appended in parentheses.

## Policies

These are pure functions of the schedule, its `state.json` and an explicit `now`.

- **Due slots** (`due`): slots after `last_slot`, after the baseline (set when the state directory is created or the schedule resumed) and after creation, at most 7 days back. A disabled, paused or finished schedule has none. A self-set next run (`next_override`) replaces the regular slots until it fired.
- **Catch-up** (`plan_catchup`): a slot noticed within 120 s of its time is a normal `cron` run. After downtime the missed slots collapse into **one** run of the newest slot (trigger `catchup`) when it is within `grace_s` and `catchup` is `latest`; otherwise they are recorded `missed` and nothing runs. `catchup: "skip"` never runs a missed slot. A `once` schedule is caught up within 24 hours, later it is `missed` and stays enabled for a manual run. A pending `run_now` always wins (trigger `manual`, even when paused).
- **Overlap** (`overlap_action`): while a run is live, `skip` drops the new slot; `queue` keeps one queued run, further slots are dropped.
- **Retries** (`retry_plan`): only `failed`, `limit` and `timeout` are retried, never `cost`, up to `retries` times. A `limit` run with `reset_at` retries at that time plus 0 to 120 s of jitter; otherwise the attempt number picks `backoff_s[attempt - 1]` (the last entry repeats).
- **Breaker** (`breaker`, `record_outcome`): `ok` resets `consec_failures`, `failed`, `limit`, `timeout` and `cost` add one; at `pause_after_failures` (0 disables) the schedule is paused with a `paused_reason` until `hivegrid-schedule resume`.

## Runs and the results inbox

A run record (`runs.jsonl`): `run, schedule, trigger (cron|manual|retry|catchup|self|chat), mode, started, ended, status (ok|failed|limit|timeout|cost|skipped|missed|cancelled|deferred), attempt, exit, profile, model, session_file, session_id, cost_usd, tokens, summary (cut to 1000 characters), report_path, error, reset_at, seen, level, headline`. `seen` is false for a new run, except `skipped`, `deferred` and `cancelled` runs and every run of level `none`, which need no reading. `unseen()` lists the runs not yet seen, `mark_seen()` (`hivegrid-schedule seen`) marks them. `level` and `headline` are described under "Briefing and chat".

## Prompts, identity, memory

`compose_prompt` builds the user prompt of each run. A `task` gets its prompt, a line naming the report path and the report format. An `agent` gets its standing task, `run #n at <time>, trigger <trigger>`, the previous run's summary (or error; chat turns do not count as runs here), and the instructions: read `memory.md` first, at the end rewrite it concisely within `memory_max_bytes` keeping only durable facts, write the report to `reports/NNNN.md` in the report format (see "Briefing and chat"). `identity_text` is the text for `--append-system-prompt`: a fixed preamble (unattended scheduled agent, nobody can answer questions, never ask, finish with a short report), the agent's name, then `identity.system`. `trim_memory` keeps the tail of a too large memory on a line boundary; `enforce_memory_cap` applies it to the file after a run.

## Self scheduling

Inside a run the environment names the schedule (`HIVEGRID_SCHEDULE`); only that schedule can be changed:

```sh
hivegrid-schedule self next --in 2h        # or --at 2026-11-03T09:00 (the schedule's zone)
hivegrid-schedule self done                # no further runs until resumed
hivegrid-schedule self note "build red since Tuesday"
```

A next run is at least 300 seconds away and at most 366 days. It replaces the regular slots until it fired.

## Summary and states

`build_summary(now)` returns `{ts, scheduler, counts: {total, enabled, running, attention, unseen}, items: [{id, name, kind, enabled, when_text, next_at, state, last: {status, ended, summary, seen, level, headline}, running, attention, paused_reason, chat: {active, queue, error}}], briefing: {unseen, newest: [up to 3 briefing entries]}}`; `scheduler` is filled by the caller (heartbeat of whoever runs the schedules). `last` is the newest run that is not a chat turn. Item `state`:

| State | Meaning |
| --- | --- |
| `working` | a run or a chat turn is live |
| `attention` | an unseen `failed`, `limit`, `timeout`, `cost` or `missed` run, or the schedule is paused |
| `done` | an unseen `ok` run |
| `schedule` | waiting for its next run |
| `idle` | disabled, or finished by the agent |

Items sort attention, working, done, schedule, idle. `daemon_alive()` is true when the runtime summary has a `scheduler.heartbeat` less than 30 seconds old.

## Control inbox

`send_command(dir, {"op": "run-now|skip|cancel|reload|chat", "id": ..., "text": ...})` writes `cmd.<ns>-<pid>.json` (0600, atomic) into the inbox; `id` is required except for `reload`, `text` (1 to 4000 characters) is required for `chat` and refused for every other op. `read_commands(dir)` returns the valid commands oldest first and removes them; junk (bad name, JSON, op, a `chat` without a usable text) is removed and ignored, files owned by another user are ignored and left alone, and an untrusted directory raises an error. `cancel` on a live chat turn also empties the bot's chat queue.

## Command line

| Command | |
| --- | --- |
| `add --name N --prompt TEXT\|--prompt-file F\|--prompt - --when TEXT [--tz Z] [--cwd D] [--account A] [--model M] [--thinking L] [--mode M] [--permissions P] [--kind task\|agent] [--max-time S] [--max-cost USD] [--system-file F] [--disabled]` | create; prints the id and the next 3 fire times (`--cwd` defaults to the current directory) |
| `list [--json]` | compact table, or the summary document |
| `show ID [--json]` | record, state and the last runs |
| `edit ID [--name --when --prompt ... ]` | change fields; sections merge key by key |
| `rm ID [--keep-state]` | delete the definition and the state directory |
| `pause ID`, `resume ID` | disable or enable; `resume` also clears a breaker pause and resets the baseline |
| `run ID`, `skip ID` | run now, or skip the next run: a command to the scheduler when its heartbeat is fresh, else a change to the state (`run_now`, `last_slot`) |
| `logs ID [-n N] [--run N] [--json]` | run table (with level and headline), or one run's report |
| `seen [ID] [--run N]` | mark runs seen |
| `briefing [--json] [--all] [--mark-seen]` | the important reports of all bots, newest first; unseen ones unless `--all` |
| `say ID TEXT...` | talk to a bot: the scheduler runs the message as a chat turn; says whether it is queued. Fails with `scheduler not running` when no scheduler is alive |
| `chat ID [--follow]` | print the tail of the conversation (the main session, through the phone's chat parser); `--follow` keeps printing what appears |
| `parse TEXT... [--tz Z] [--json]`, `next ID [-n N]` | preview |
| `doctor` | check accounts (a label without an omp profile), missing `cwd`, bad expressions, stale or foreign state, paused schedules |
| `self next\|done\|note` | for a running agent, see above |

`ID` is an id, a unique id prefix or an exact name. `pause`, `resume`, `rm`, `edit` always change the store directly and tell a live scheduler to `reload`. Exit status: 0 success, 1 failure (an unknown schedule, an unusable file; `doctor`: warnings), 2 bad input (`doctor`: errors).

## Scheduler

`bin/hivegrid_scheduler.py` is the daemon that runs the schedules: it starts `omp`, records what happened and applies the policies above. `bin/hivegrid-scheduler` is its shim.

```sh
hivegrid-scheduler              # = run: the daemon in the foreground (what the user unit starts)
hivegrid-scheduler run-due --once   # start what is due now, wait for those runs, exit (tests, no systemd)
systemctl --user status hivegrid-scheduler   # after `hivegrid-autostart install`, see autostart.md
```

Exit status 1: another scheduler holds the lock (one per user, an exclusive `flock` on `scheduler.lock` in the control inbox directory) or the state directories cannot be used. Nothing about a schedule needs the scheduler to be running to be *defined*; runs only happen while it runs, and what was due while it was down is handled by catch-up when it starts again.

### The loop

Every 2 seconds (and at once when a run ends) one tick: reload `schedules.json` when it changed; read the control inbox (`run-now`, `skip`, `cancel`, `chat`, `reload`); collect finished runs; kill runs that passed their deadline; decide what is due and start it; rewrite the runtime summary (`scheduler: {heartbeat, pid, started, max_parallel, running, waiting}` through `build_summary`) on every change and at least every 10 seconds, so `daemon_alive()` (30 s) holds. On SIGTERM or SIGINT it sends SIGTERM to every run's process group (SIGKILL after 8 s), records those runs `cancelled`, writes a last summary without a heartbeat and exits. On start it closes every `running` record a previous scheduler left: a dead pid is a `failed` run (a chat turn: a `chat` run, shown in the conversation only), a live one that still carries the run's environment is stopped and recorded `cancelled`.

Per schedule a tick works like this: a due slot becomes `fire` or `missed` through `plan_catchup`. A miss records a `missed` run, moves `last_slot` and notifies. A fire while a run is live follows `overlap_action` (`skip` records a `skipped` run, `queue` keeps one queued run that starts when the live one ends). Fires start in order of their slot time, at most `schedule.max_parallel` runs at once (settings.json, default 2); the rest wait for the next tick (a slot that waited longer than 120 s is then a `catchup`, and `missed` after the grace period). `run-now` starts at once even when paused, `skip` marks the next slot handled, `cancel` stops the live run and drops a queued one and a pending retry.

### One run

The run is `omp` started without a shell, in its own session (`start_new_session`), with the prompt on stdin:

```
omp [--profile P] --mode json -p --max-time T --max-cost C <permissions> --thinking L [--model M]
    --append-system-prompt <state dir>/identity.md --add-dir <state dir> [--add-dir D ...] [--resume <session file>]
```

| `permissions` | arguments |
| --- | --- |
| `readonly` | `--tools read,grep,find,glob,web_search,lsp --approval-mode write` |
| `edit` (default) | `--approval-mode write`: read and write approved, commands refused (omp has no one to ask) |
| `full` | `--approval-mode yolo`; only when settings.json has `schedule.allow_full: true`, else the run is a `failed` record that says so |

The state directory is an `--add-dir`, so the agent can write `memory.md` and its report. The working directory is `run.cwd`. The environment is the scheduler's without `WEZTERM_*` (the state extension then stays quiet) and without `PI_SESSION_FILE`, `PI_SESSION_ID`, `OMP_PROFILE`, `PI_PROFILE` and `PI_CODING_AGENT_DIR` (a bot never inherits the profile of the pane the scheduler was started from), plus `HIVEGRID_SCHEDULE=<id>`, `HIVEGRID_RUN=<n>` and `HIVEGRID_RUN_REPORT=<reports/NNNN.md>`. `HIVEGRID_OMP_BIN` replaces the `omp` binary (the tests use a fake one), `HIVEGRID_OMP_SWITCH` replaces `omp-switch`.

`run.account` is an account label of the settings (Settings, Accounts), resolved at start to the account's omp profile; an account without a paired profile runs omp's default configuration, like the account pill does. An unknown label is a `failed` run saying so, never a silent default. `run.mode` `visible` and `hybrid`: **a run is always headless for now**; the record's `mode` reads `headless/visible` or `headless/hybrid` so a panel can tell. There is no visible pane runner yet.

The stdout JSON lines are read while the run goes: the session id (first line), the cost (`message.usage.cost.total` of every `turn_end`, summed), token counts and the final assistant text (the last assistant `message_end`). stderr (MCP warnings and the like) keeps its tail in `run-<n>.log` next to `runs.jsonl` (the newest 50 logs). The session file is found by globbing `*_<session id>.jsonl` below the profile's `sessions/` directory (`~/.omp/agent/` or `~/.omp/profiles/<p>/agent/`); it is where the phone's chat reader and `omp --resume` look. The summary of the run is the headline and body of the report the agent wrote to `HIVEGRID_RUN_REPORT` (at most 64 KB), else the final text, which is then also saved as the report. A `continuity: "resume"` agent (the default of an agent) starts its next run with `--resume <the bot's main session file>`. omp's `--max-cost` limits the spend of the whole session, so a resumed run gets `max_cost_usd` plus what the session already cost.

Status: exit 0 with an assistant message that did not end in `error` or `aborted` is `ok` (tool calls omp refused for lack of approval are counted in `error`); exit 3 is `cost` (`--max-cost`); a run killed by the watchdog (no exit 60 s after `max_time_s`) is `timeout`, and so is a failed run that used its `max_time_s`; a failure whose session ends on a usage-limit refusal (`limit_of_entry` on the session file's last entry) is `limit` with `reset_at`; every other failure is `failed` with the model's error message, else the first stderr line. A stop by `cancel` or by the scheduler going down is `cancelled` (never retried, not counted by the breaker).

### Guards and failure handling

- **Retries**: `retry_plan` after a `failed`, `limit` or `timeout` run; the plan is kept in `state.json` as `retry: {at, attempt, account, resume_file, from_profile}` and runs as trigger `retry`. A retry that will follow keeps the failed attempt quiet (no notification); the final outcome and every `limit` notify. A run that cannot even start (unknown account, missing `cwd`, no `omp`, `full` not allowed) is a `failed` record and is not retried.
- **Quota guard** (not for `run-now`): before a run on an account, `read_quota(account)` of the 5-hour window at or above `quota_guard_pct` tries `fallback_accounts` in order (the first below the guard, or without data, takes the run); with none, the run is recorded `deferred` (already seen) and retried at the window's reset, at most 6 hours away (1 hour without a reset time). No quota data never blocks a run.
- **Limit**: after a `limit` run, a fallback account below the guard takes the retry at once: the session is copied into that account's profile with `omp-switch transfer` and resumed there with `--resume`, so the work goes on; without one the retry waits for `reset_at` plus 0 to 120 s. A failover needs `retries` of 1 or more.
- **Daily budget** (not for `run-now`): when the `cost_usd` of today's runs of all schedules (local day) reaches `schedule.daily_budget_usd`, due runs are `deferred` until midnight and one notification says so per day. 0 turns it off.
- **Breaker**: `record_outcome` after every run; `pause_after_failures` in a row pause the schedule (`paused_reason`), `hivegrid-schedule resume` clears it.
- **Memory**: `enforce_memory_cap` after every run trims `memory.md` to `memory_max_bytes`.

### Notifications

A result is pushed only when it matters: a run of level `important` (see "Briefing and chat"), and a run of level `info` through the `done` opt-in below; a quiet run (`none`) and a chat turn never notify. The push goes with `hivegrid_remote_notify.send` to the topic URL `remote.notify_url` of settings.json and, while `remote.push` is true there, as a Web Push message to the registered phones (`hivegrid_remote_push.send_from_settings`; the scheduler is its own process, so it reads `push.json` beside settings.json itself; tapping the notification opens `#/bots`). Neither set, nothing is sent; a push that fails is dropped and never touches the run, and one way failing does not stop the other. The events are `failed` (also `timeout` and `cost`), `limit`, `missed` and `done` (an `ok` run). A failure, limit or miss is pushed when the bot's `notify.on` lists it and settings.json `schedule.notify_events` allows it (default `failed`, `limit`, `missed`); an `ok` run the agent called `important` is pushed whenever the bot has any notification on; an `ok` run of level `info` only for a bot with `notify.done: true`. Title `Hivegrid bot: <name> <failed|timeout|cost|limit|missed|done>`, body the run's headline reduced to one printable line of at most 300 characters (a limit adds when it retries); priority 4 for failures, 3 otherwise (Web Push urgency high for 4, normal otherwise).

### Settings it reads

`schedule.max_parallel`, `schedule.allow_full`, `schedule.daily_budget_usd`, `schedule.notify_events`, `remote.notify_url` and `remote.push` of `~/.config/hivegrid/settings.json` (or `HIVEGRID_SETTINGS`), re-read on every use. A missing file, key or value, or an unimportable `hivegrid_settings_core`, means the defaults (2 runs at once, `full` not allowed, no budget, `failed,limit,missed`, no push).

## Briefing and chat

**Report levels.** The agent states how much its run matters in the report file (`HIVEGRID_RUN_REPORT`): the first line `level: none|info|important`, the second `headline: <one sentence>`, then a blank line and a free-form body. `compose_prompt` tells it so for both kinds: `important` is something the user should know or decide (a problem found, a fix made, a result they asked for, anything failing), `info` routine work worth keeping in the bot's own history, `none` nothing happened. `parse_report(text, kind)` reads it tolerantly (case-insensitive, markdown emphasis and one code fence around the two lines are stripped, either order) and returns `{level, headline, body, marked}`; a missing or unknown level is `info` for an `agent` and `important` for a `task` (a one-off task's result is what the user wanted), a missing headline the first sentence of the body, cut to 140 characters on one line.

The run record carries `level` and `headline`. The status overrides what the agent wrote: `failed`, `limit`, `timeout`, `cost`, `missed`, `deferred` and a run stopped by the scheduler going down are always `important`, with the automatic headline `<name> failed: <first error line>` (`<name> hit its usage limit`, `<name> timed out: ...`, `<name> missed a run: ...`); `skipped` and a run the user cancelled are `none`, and so is every chat turn. A bot the breaker paused shows through the failed runs that paused it. `none` runs are recorded already seen. A quiet run ("CI green, nothing to do") therefore never reaches the Briefing or a push and stays in that bot's own history (`hivegrid-schedule logs`).

**Briefing.** `briefing(env=None, limit=50, include_seen=True)` lists the `important` runs of all bots, newest first, as `{bot_id, bot_name, kind, run, level, status, headline, ended, seen, session_id?}`; `briefing_counts(env)` is `{unseen, total}`; `mark_briefing_seen(env)` marks every important run seen (and nothing else) and returns how many changed. `build_summary` carries `briefing: {unseen, newest: [up to 3 entries]}` and every item `last.level` and `last.headline`. Records written before levels existed read sensibly (a failed run is important, an ok run `info`, the headline the first sentence of its summary).

**The main session.** Each bot has one persistent session, `state.json` `session_file` (the newest session any run or chat turn of the bot used). A scheduled run of an `agent` resumes it (`continuity` defaults to `resume`; `fresh` written explicitly keeps the old start-anew behaviour; `memory.md` stays). A chat with a `task` continues the session of its latest run; with none, the first chat creates the session as a fresh `omp -p` with the bot's identity, folder, account and permissions.

**Talk.** `say(id, text, env=None, now=None)` sends the inbox command `{"op": "chat", "id", "text"}` (at most 4000 characters) and returns `{queued, position}`: `queued` is true when the bot is busy (a scheduled run or another turn), `position` the place in the queue (0 when it starts at once). It raises `ScheduleError("scheduler not running")` (`.code == "no_daemon"`) when no scheduler heartbeat is fresh, so a message is never queued into the void, `unknown_bot`, or `queue_full` (`.code`) when 5 messages already wait. The daemon runs each message as a headless turn, the same way as a run (`omp -p --resume <main session>` with the bot's permissions mapping, environment scrub, `max_time_s`, `max_cost_usd` (plus what the resumed session already cost, see One run) and watchdog), with the user's text as the whole prompt. The turn is a run record with `trigger: "chat"`, `level: "none"`, its summary the first 200 characters of the reply; it is not counted by the breaker, never retried, never in the Briefing and never notifies. A failed turn is shown in the conversation only, as `chat.error`.

The queue is `state["chat_queue"]` (`[{text, ts}]`, at most 5; a sixth message is refused and leaves `chat.error` = `queue_full: ...`), served first in, first out when the bot is idle; a scheduled run that is due goes before waiting messages. While a chat turn is live the bot is `working` and a due slot is `queue`d (`overlap_action`), never lost. `state["chat"]` is `{active, started, error}`; `cancel` stops the live turn and empties the queue.

`conversation(id, env=None)` returns `{session_file, live, busy, queue, error, activity}`: `live` while a run or turn is going (the session file is growing), `busy` while a new message would have to wait, `activity` `chat`, `run` or None. The transcript itself is read from `session_file` with the shared chat parser (`hivegrid_remote_data.ChatCache`, as the phone does), refreshing while the file grows: tool calls and text appear as they are written. `hivegrid-schedule chat ID [--follow]` is the non-TUI view of the same.

## Host page

The window has a **page switcher** in the header, `[Canvas] [Bots]`: Canvas is the window as it always was (tabs, the pane grid, the bar and the dock), **Bots** is one fixed page over the active tab. It is not a tab and not a pane of the grid: it covers the tab that was in front like a panel does, and a click on Canvas, the same key again (`LEADER+t` toggles), or another tab ends it. Reached from the header buttons, `LEADER+t` (settings key `bots_panel`), the `+` menu rows `Bots` and `New bot…`, the header chip and a row of the dock's Bots section (that bot opens).

The page is the phone's own Bots page ([remote.md](remote.md), "Bots"): the Briefing first, the list of bots, a conversation with each, the new-bot and edit sheets. The host shows it in a **web pane** of the Hivegrid fork (`hivegrid:web`, browser profile `hivegrid-bots`, header row hidden) pointed at the local `hivegrid-remote` on `127.0.0.1` (`/?embed=1#/bots`), so the host and the phone are one page, one code path.

`config/pages.lua` opens it. `hivegrid-bots-page ensure [--route /bots|/bots/<id>] [--new]` (`bin/hivegrid_botspage.py`) makes sure a server answers on `127.0.0.1:<port>` (`HIVEGRID_REMOTE_PORT`, default 8787): the user unit `hivegrid-remote.service` when it is installed, else a loopback-only `hivegrid-remote serve` of its own, started detached. It writes `$XDG_RUNTIME_DIR/hivegrid-bots-page.json` (`ts`, `ok`, `state` `up|started|no_token|down`, `message`, `command`, `open`) and, when ok, the one-shot page `hivegrid-bots-open.html` (mode 0600): a page that sends the web pane to the local server with the access token in the address fragment (`#t=<token>&r=<route>`), which the page stores and removes at once. So the token is read from its 0600 file only, and is in no command line, no log and no address that stays visible; the Lua side never reads it, and the one-shot file is deleted 20 seconds after it opened.

**When it is not available** the Bots page is the terminal page (`hivegrid-panel bots`, below), with the reason in a toast: no access token yet (`hivegrid-remote token`), the local server could not be started (the exact `systemctl --user start hivegrid-remote` or `hivegrid-remote serve` line is named), it did not answer within 15 seconds, no Chrome or Chromium (`web.has_chrome`), or no web pane at all (stock WezTerm: no switcher either, `LEADER+t` and the menu open the terminal page).

### Terminal page

`hivegrid-panel bots` (`--select ID` puts the cursor on a bot, `--new` opens the new-bot dialogs on the folder and account of the pane the panel covers, `--origin`) is the Bots page without a browser, used when the web pane is not available. It reads the store, the state directories and the runtime summary, and changes things only through `hivegrid_schedule`.

The list has one row per bot: state glyph (`◷` waiting, a spinner while a run or chat turn is live, `✓` unseen ok result, `?` needs you, `⊘` limit, `·` off), name, kind (`▪` task, `◆` agent), when, next run, the last result with its age, account, access and mode; an unseen result is marked `●`. The first line says whether the scheduler runs and, when it does not, the command that starts it (`systemctl --user start hivegrid-scheduler` when that user unit is installed, else `hivegrid-scheduler`) with a `Start` button, and carries the `Briefing` and `New` buttons.

| Key | |
| --- | --- |
| `b` | **Briefing**: the important reports of all bots, newest first (headline, bot, status, age). `Enter` reads the report below the list and marks it seen, `a` marks all seen, `Esc` goes back. Quiet runs never appear here |
| `t` | **Talk** to the selected bot: its conversation read from the main session file through the shared chat parser (`hivegrid_remote_data.ChatCache`), tool calls one line each, scrolled to the end (`PgUp` / `PgDn` / wheel scroll back) and redrawn about every second while a run or chat turn is live, otherwise when the file changes. Typing fills the input line at the bottom, `Enter` sends it through `hivegrid_schedule.say` (`queued #n: …` when the bot is busy, `scheduler not running: <start command>` or `queue_full` as errors, the text is kept), `Esc` goes back |
| `n` | new bot: name, kind, task (agents: identity too), when, folder, account, permissions, mode. The `when` dialog shows what the text means and the next three times as you type. Nothing is written before the last answer. `full` needs `schedule.allow_full` in settings.json, a confirmation, and a longest run and a cost limit |
| `Enter` | details: the run history (status, age, duration, trigger, cost, summary or error); viewing marks the bot's results seen. `v` there opens the selected run's report |
| `e` | edit the record in `$VISUAL` / `$EDITOR`: the JSON, with the prompt as plain text below a marker line. The text is validated through the store; an error is shown with `Edit again` / `Discard` and nothing is saved before it passes |
| `m` | edit `memory.md` (an agent's, or a task with `identity.memory`); a result over the cap is cut to it |
| `space` | pause or resume (resume also clears a breaker pause) |
| `r` | run now: a command to the scheduler, or, when it is not running, an offer to start it first (or to run when it starts) |
| `s`, `c` | skip the next run; cancel the live run |
| `d` | delete the bot and its history after a confirmation |
| `?` | key help |

Stepping into a bot is Talk, on this page and on the web page; no key opens a tab or a pane for it. The panel creates bots with origin `panel`.

### Header chip, dock, toasts

- The header shows a quiet chip `◷ 3 · next 09:00 ? 1 ⊘ 1 ↻ 1 ● 2 CI of repo x fixed`: enabled bots waiting, the earliest next run, counts of bots that need you (`?`), hit a usage limit (`⊘`) and are running (`↻`), then the Briefing's unseen count (`●`) with the newest unseen headline. It narrows (headline, then next run, then marks) and is the first thing the header drops when room runs out, and is hidden when the scheduler's heartbeat is older than 60 s or there is nothing enabled, wanting a look or unseen. A click opens the Bots page. The page switcher's Bots button carries the same unseen count (`Bots ● 2`) and is never dropped.
- The dock shows a compact Bots section below the agent rows while the heartbeat is fresh and something runs, waits or is unseen: the newest unseen Briefing headline first (`●`), running bots with a spinner, then up to three waiting ones (`◷`, name, `in 2h` or `09:00`); the title row carries `● <unseen>`. It only uses rows the agents leave free and never moves an agent row. A click on a row opens the Bots page on that bot (user var `hivegrid_overview_bots`).
- A desktop toast ("Hivegrid", `<bot>: <headline>`) appears once for every **new important** Briefing entry. Reports that were already unseen when Hivegrid started, seen ones and quiet runs never toast.

### Settings

`schedule` in settings.json: `max_parallel` (1 to 8, default 2), `tz` (`local` or an IANA name, the zone wall-clock words mean when a bot is created), `allow_full` (default false; only the literal `true` counts), `daily_budget_usd` (0 = off), `notify_events` (a non-empty subset of `failed limit missed done`). `schedule_settings()` in `hivegrid_settings_core.py` and `config/settings.lua` read it the same way; a missing or invalid value is the default. The panel has no Settings page for it yet: edit the file. The key binding `bots_panel` (default `LEADER+t`) is in Settings, Keys.

The state `schedule` (`◷`, `waiting`, cyan, rank 9) is the one addition to the agent state vocabulary (`hivegrid_ui.STATES`, `theme.STATES`); the phone's own vocabulary does not include it.

## Phone

`hivegrid-remote` shows the Bots on the phone and can steer them (docs/remote.md, "Bots"; API under `/api/bots`). It reads and writes only through `hivegrid_schedule` (`Store`, `read_runs`, `unseen`, `mark_seen`, `parse_when`, `send_command`, `build_summary`) in `bin/hivegrid_remote_bots.py`, in the same files the command line and the scheduler use, so a change on the phone is a change of `schedules.json` and is picked up by the scheduler's `reload`, and a result the scheduler records appears on the phone within a long poll.

- **Page.** `#/bots` is the third page of the phone's swipe strip (agents, terminals, bots), `#/bots/<id>` a bot and `#/bots/<id>/<run>` a run. The list is `GET /api/bots` long-polled; its `state` and the labels come from the summary states above (`schedule` is shown as `waiting`, with the clock `◷`). `scheduler.alive` is `daemon_alive`'s rule: a heartbeat of the runtime summary at most 30 seconds old; when it is not, the page says so and a **Run now** or **Skip next** goes to the bot's state (`run_now`, `last_slot`) instead of the control inbox, exactly like `hivegrid-schedule run` and `skip`.
- **What the phone creates.** Bots with `origin: "phone"`, always `readonly` or `edit` (never `full`), with `max_time_s` (at most 7200) and `max_cost_usd` (at most 10) set, at most 50 bots, never repeating more often than every 5 minutes, in a folder `hivegrid-remote` offers or one below the home directory, on an account of the settings or automatic, and only the fields `name`, `kind`, `prompt`, `when`, `dir`, `account`, `mode` (`headless` or `visible`), `notify`, `permissions`, `max_time_s`, `max_cost_usd`, `identity`. Everything else (policy, model, thinking, `add_dirs`, `continuity`) keeps its default; `notify: true` means every event including `done`, `false` none. A bot made on the laptop with `permissions: "full"` is shown but not editable from the phone except to lower its permissions.
- **Results.** Fetching a run (`GET /api/bots/<id>/runs/<n>`) calls `mark_seen` for that run, so the dot on the phone's menu button and the panel's unseen count fall together; `POST /api/bots/<id>/ack` marks all of a bot's runs. A run's report is `reports/NNNN.md` and an agent's memory `memory.md`, both read without following links and shown as text (the first 12000 characters).
- **Talk.** `#/bots/<id>` is the conversation with the bot's main session: `GET /api/bots/<id>/conversation` (the library's `conversation()` plus the session file read through the phone's chat parser, which must pass `session_path_ok`) and `POST /api/bots/<id>/say` (`say()`: a `chat` command to the scheduler, refused with a clear message while it is not running). The Briefing is `GET /api/bots/briefing` (`briefing()`, `briefing_counts()`) and `POST /api/bots/briefing/seen` (`mark_briefing_seen()`). The phone never puts a session into a pane.
- **Push.** `hivegrid_remote_notify.bot_event(url, name, status, summary, base_url=None)` is what the scheduler calls to send one ntfy message for a run: the title `Hivegrid bot: <name> <failed|finished|...>`, the summary as the body, and with `base_url` (settings.json `remote.base_url`, read with `page_url()`) a `Click` link to `<base_url>/#/bots`.
