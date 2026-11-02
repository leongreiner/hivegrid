"""hivegrid-remote: the Bots (scheduled omp agents) behind /api/bots, free of HTTP.

`Bots` is the phone's view of `hivegrid_schedule` (docs/schedules.md): it lists the schedules with their
state, shows one with its memory and runs, creates and edits them under the phone's restrictions, sends
the scheduler its commands, hands it what the user says to a bot (a chat turn of the bot's main session)
and builds the briefing of important reports. It reads and writes
only through the public API of `hivegrid_schedule` (Store, read_runs, unseen, mark_seen, briefing, say,
conversation, parse_when, send_command, read_summary, build_summary) plus the one report and memory file
of a bot, read with the same no-link, own-file rules.

What the phone may not do, whatever its token says (docs/remote.md, "Bots"): create or edit a bot to run
with permissions `full`, create one without a time and a cost cap or with caps above PHONE_MAX_TIME_S and
PHONE_MAX_COST_USD, have more than MAX_BOTS bots, repeat more often than every 5 minutes (parse_when and
the store refuse that too), name a directory that is not offered or not below the home directory, or set
any field outside the whitelists below (`origin` included: it is always `phone`).

Everything that touches the world is injectable (the environment, the clock, the home directory), so the
tests need no real home, scheduler or window."""
import hashlib
import json
import math
import os
import shutil
import stat
import time

import hivegrid_remote_data as D
import hivegrid_schedule as S

MAX_BOTS = 50
MIN_INTERVAL_S = S.MIN_INTERVAL_S
PHONE_MAX_TIME_S = 7200
PHONE_MAX_COST_USD = 10.0
DEFAULT_TIME_S = 1800
DEFAULT_COST_USD = 2.0
PHONE_PERMISSIONS = ("readonly", "edit")
PHONE_MODES = ("headless", "visible")
NOTIFY_ALL = ["failed", "limit", "missed", "done"]
WHEN_MAX = 200
NAME_MAX = S.MAX_NAME
IDENTITY_MAX = 2000
RUN_PAGE_DEFAULT, RUN_PAGE_MAX = 20, 100
MEMORY_VIEW = 12000             # characters of memory.md the phone shows
REPORT_VIEW = 12000             # characters of a run's report
FILE_READ = 4 * MEMORY_VIEW     # bytes read to get them
SAY_MAX = 4000                  # characters of one message to a bot
CHAT_QUEUE_MAX = 5              # messages that may wait for the bot (the scheduler's own limit)
HEADLINE_MAX = 140
BRIEFING_LIMIT = 50
LEVELS = ("none", "info", "important")
CLOCK_SLACK_S = 2

CREATE_FIELDS = ("name", "kind", "prompt", "when", "dir", "account", "mode", "notify", "permissions", "max_time_s",
                 "max_cost_usd", "identity")
EDIT_FIELDS = tuple(f for f in CREATE_FIELDS if f != "kind")
REQUIRED = ("name", "prompt", "when", "dir", "max_time_s", "max_cost_usd")
VERBS = ("run", "pause", "resume", "skip", "cancel", "ack")


class Refused(Exception):
    """The request cannot be done; nothing was changed. `code` is an HttpError code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def limits():
    """What the phone is held to, for /api/meta (`limits.bots`) and the page's forms."""
    return {"max_bots": MAX_BOTS, "min_interval_s": MIN_INTERVAL_S, "max_time_s": PHONE_MAX_TIME_S,
            "max_cost_usd": PHONE_MAX_COST_USD, "default_time_s": DEFAULT_TIME_S, "default_cost_usd": DEFAULT_COST_USD,
            "name": NAME_MAX, "identity": IDENTITY_MAX, "permissions": list(PHONE_PERMISSIONS), "say": SAY_MAX}


def _rev(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]


def _number(value, what, low, high, integer=False):
    """`value` as a number within low..high (an int for `integer`; a float that is whole counts), else Refused."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise Refused("bad_request", f"{what} must be a number")
    if integer:
        if value != int(value):
            raise Refused("bad_request", f"{what} must be a whole number")
        value = int(value)
    if not low <= value <= high:
        raise Refused("bad_request", f"{what} must be between {low} and {high}")
    return value


class Bots:
    """`san_line(text, limit)` / `san_text(text, limit)` are the server's display sanitizers, `check_text(value,
    limit, what, allow_empty)` its input validator (None when fine), `max_prompt` the longest task. `env` is the
    environment hivegrid_schedule reads its paths from (None: the process's); `home` the home directory the
    session files and the offered directories lie below (None: $HOME)."""

    def __init__(self, san_line, san_text, check_text, max_prompt, env=None, wall=time.time, home=None, uid=None):
        self.san_line, self.san_text, self.check_text, self.max_prompt = san_line, san_text, check_text, max_prompt
        self.env, self.wall, self.home = env, wall, home
        self.uid = os.getuid() if uid is None else uid
        self.store = S.Store(env=env)

    # -- reading
    def _recs(self):
        try:
            return self.store.read()
        except S.ScheduleError:
            raise Refused("internal", "the schedules file cannot be used: run `hivegrid-schedule doctor` on the laptop") from None

    def _rec(self, bid):
        rec = next((r for r in self._recs() if r["id"] == bid), None)
        if rec is None:
            raise Refused("not_found", "no such bot")
        return rec

    def _sdir(self, bid):
        return S.state_dir(bid, self.env)

    def scheduler(self, now):
        """`{alive, heartbeat, age_s}` from the runtime summary the scheduler rewrites every few seconds."""
        doc = S.read_summary(S.summary_path(self.env))
        sch = doc.get("scheduler") if doc else None
        hb = sch.get("heartbeat") if isinstance(sch, dict) else None
        if isinstance(hb, bool) or not isinstance(hb, (int, float)) or not math.isfinite(hb):
            return {"alive": False, "heartbeat": None, "age_s": None}
        age = now - hb
        # A heartbeat a moment "ahead" is the whole-second `now` of a write against the daemon's fractional clock: still fresh.
        return {"alive": -CLOCK_SLACK_S <= age <= S.HEARTBEAT_MAX_AGE_S, "heartbeat": hb, "age_s": int(max(age, 0))}

    def _can(self, rec, st, item, alive, readonly):
        keys = ("run", "skip", "pause", "resume", "cancel", "edit", "delete", "ack")
        if readonly:
            return {k: False for k in keys}
        running = item["running"]
        full = rec["run"]["permissions"] == "full"
        return {"run": not running,
                "skip": bool(rec["enabled"]) and item["next_at"] is not None and not st.get("done"),
                "pause": bool(rec["enabled"]),
                "resume": not rec["enabled"] or bool(st.get("paused_reason")) or bool(st.get("done")),
                "cancel": running and alive, "edit": not full, "delete": not running, "ack": item["unseen"] > 0}

    def _item(self, rec, summary_item, st, unseen, alive, readonly):
        san = self.san_line
        last = summary_item["last"]
        running = st.get("running") if isinstance(st.get("running"), dict) else None
        item = {
            "id": rec["id"], "name": san(rec["name"], NAME_MAX), "kind": rec["kind"], "enabled": rec["enabled"],
            "when_text": san(summary_item["when_text"], 80), "next_at": summary_item["next_at"],
            "state": summary_item["state"],
            "last": None if last is None else {"status": last["status"], "ended": last["ended"],
                                               "summary": san(last["summary"], 300), "seen": last["seen"],
                                               "level": last.get("level") if last.get("level") in LEVELS else None,
                                               "headline": san(last["headline"], HEADLINE_MAX) if last.get("headline") else None},
            "running": summary_item["running"], "running_since": running.get("started") if running else None,
            "attention": summary_item["attention"], "unseen": unseen,
            "paused_reason": san(summary_item["paused_reason"], 200) if summary_item["paused_reason"] else None,
            "done": bool(st.get("done")), "permissions": rec["run"]["permissions"], "mode": rec["run"]["mode"],
            "account": san(rec["run"]["account"], 100) if rec["run"]["account"] else None,
            "dir": san(D._shown(rec["run"]["cwd"], self.home), 200), "origin": rec["origin"],
            "notify": bool(rec["notify"]["on"]),
        }
        item["can"] = self._can(rec, st, item, alive, readonly)
        return item

    def items(self, now, readonly=False):
        """(scheduler, counts, [item]) of every bot, the order the scheduler's summary gives (attention first). `unseen`,
        in the counts and per bot, is the number of important reports not yet read (the briefing's)."""
        recs = self._recs()
        sch = self.scheduler(now)
        doc = S.build_summary(now, {}, env=self.env, schedules=recs)
        by = {r["id"]: r for r in recs}
        unread = {}
        for e in S.briefing(self.env, limit=500, include_seen=False):
            unread[e["bot_id"]] = unread.get(e["bot_id"], 0) + 1
        out = []
        for it in doc["items"]:
            rec = by[it["id"]]
            sdir = self._sdir(rec["id"])
            out.append(self._item(rec, it, S.load_state(sdir), unread.get(rec["id"], 0), sch["alive"], readonly))
        counts = dict(doc["counts"])
        counts["unseen"] = sum(unread.values())
        return sch, counts, out

    def listing(self, now, readonly=False):
        """The document of `GET /api/bots`. `rev` covers everything but the clock-dependent ages."""
        sch, counts, items = self.items(now, readonly)
        can = {"create": not readonly and len(items) < MAX_BOTS}
        rev = _rev([sch["alive"], counts, items, can])
        return {"v": 1, "now": now, "rev": rev, "scheduler": sch, "counts": counts, "can": can, "bots": items}

    def summary(self, now):
        """`{attention, unseen, running, alive}` for the agent list's menu dot (`unseen`: unread briefing entries);
        None when there are no bots."""
        try:
            sch, counts, _items = self.items(now, True)
        except (Refused, S.ScheduleError):
            return None
        if not counts["total"]:
            return None
        return {"attention": counts["attention"], "unseen": counts["unseen"], "running": counts["running"],
                "alive": sch["alive"]}

    def _read_head(self, path, limit):
        """(text, whether it was cut) of the start of one of our own regular files (no link), None when there is none."""
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        except OSError:
            return None
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != self.uid:
                return None
            data = b""
            while len(data) <= limit:
                chunk = os.read(fd, limit + 1 - len(data))
                if not chunk:
                    break
                data += chunk
        except OSError:
            return None
        finally:
            os.close(fd)
        cut = len(data) > limit
        text = data[:limit].decode("utf-8", "replace")
        return text, cut

    def _view(self, path, limit):
        got = self._read_head(path, FILE_READ)
        if got is None:
            return None
        text, cut = got
        clean = self.san_text(text, limit)
        return {"text": clean, "truncated": cut or len(clean) >= limit}

    def _run_json(self, rec, run, sdir):
        started, ended = run.get("started"), run.get("ended")
        tokens = run.get("tokens")
        if isinstance(tokens, dict):
            tokens = sum(v for v in tokens.values() if isinstance(v, (int, float)) and not isinstance(v, bool)) or None
        sf = run.get("session_file")
        has_session = bool(sf) and D.session_path_ok(sf, self.home, self.uid) is not None
        n = run.get("run")
        return {
            "run": n, "trigger": run.get("trigger"), "mode": run.get("mode"), "started": started, "ended": ended,
            "duration": int(ended - started) if isinstance(started, (int, float)) and isinstance(ended, (int, float)) and ended >= started else None,
            "status": run.get("status"), "attempt": run.get("attempt"), "exit": run.get("exit"),
            "account": self.san_line(run.get("profile"), 100) if run.get("profile") else None,
            "model": self.san_line(run.get("model"), 100) if run.get("model") else None,
            "cost_usd": run.get("cost_usd"), "tokens": tokens,
            "summary": self.san_text(run.get("summary") or "", S.SUMMARY_MAX),
            "error": self.san_line(run.get("error"), 300) if run.get("error") else None,
            "reset_at": run.get("reset_at"), "seen": bool(run.get("seen")),
            "session": has_session, "level": run.get("level") if run.get("level") in LEVELS else None,
            "headline": self.san_line(run.get("headline"), HEADLINE_MAX) if run.get("headline") else None,
            "report": isinstance(n, int) and os.path.isfile(S.report_path(rec["id"], n, self.env)),
        }

    def _definition(self, rec):
        run, ident = rec["run"], rec["identity"]
        return {
            "name": self.san_line(rec["name"], NAME_MAX), "kind": rec["kind"], "enabled": rec["enabled"],
            "when_text": self.san_line(S.describe(rec["when"], self.wall()), 80), "when_type": rec["when"]["type"],
            "tz": rec["when"].get("tz", "local"), "prompt": self.san_text(rec["task"]["prompt"], S.MAX_PROMPT),
            "identity": self.san_text(ident["system"], 8000), "memory": bool(ident["memory"]),
            "dir": self.san_line(D._shown(run["cwd"], self.home), 200),
            "account": self.san_line(run["account"], 100) if run["account"] else None,
            "model": self.san_line(run["model"], 100) if run["model"] else None, "thinking": run["thinking"],
            "mode": run["mode"], "permissions": run["permissions"], "max_time_s": run["max_time_s"],
            "max_cost_usd": run["max_cost_usd"], "continuity": ident["continuity"],
            "notify": list(rec["notify"]["on"]), "origin": rec["origin"], "created": rec["created"],
            "updated": rec["updated"]}

    def detail(self, bid, now, readonly=False):
        """The document of `GET /api/bots/<id>`: the item, its definition, the memory of an agent, the last runs."""
        rec = self._rec(bid)
        sch, _counts, items = self.items(now, readonly)
        item = next((i for i in items if i["id"] == bid), None)
        if item is None:
            raise Refused("not_found", "no such bot")
        sdir = self._sdir(bid)
        st = S.load_state(sdir)
        runs = S.read_runs(sdir)
        memory = None
        if rec["identity"]["memory"]:
            memory = self._view(S.memory_path(bid, self.env), MEMORY_VIEW) or {"text": "", "truncated": False}
            memory["max_bytes"] = rec["identity"]["memory_max_bytes"]
        state = {"run_count": st.get("run_count"), "consec_failures": st.get("consec_failures"),
                 "queued": bool(st.get("queued")), "run_now": bool(st.get("run_now"))}
        return {"v": 1, "now": now, "scheduler": sch, "bot": item, "definition": self._definition(rec), "memory": memory,
                "state": state, "total_runs": len(runs),
                "runs": [self._run_json(rec, r, sdir) for r in reversed(runs[-5:])]}

    def runs(self, bid, offset, limit, readonly=False):
        """`GET /api/bots/<id>/runs`: newest first, `limit` from `offset`."""
        rec = self._rec(bid)
        sdir = self._sdir(bid)
        runs = list(reversed(S.read_runs(sdir)))
        page = runs[offset:offset + limit]
        return {"v": 1, "offset": offset, "limit": limit, "total": len(runs), "more": offset + limit < len(runs),
                "runs": [self._run_json(rec, r, sdir) for r in page]}

    def run_detail(self, bid, number, readonly=False):
        """`GET /api/bots/<id>/runs/<n>`: the run, its report and, unless the server is read-only, marked seen."""
        rec = self._rec(bid)
        sdir = self._sdir(bid)
        run = next((r for r in S.read_runs(sdir) if r.get("run") == number), None)
        if run is None:
            raise Refused("not_found", "no such run")
        out = self._run_json(rec, run, sdir)
        was_unseen = not run.get("seen")
        if was_unseen and not readonly:
            S.mark_seen(bid, number, env=self.env)
            out["seen"] = True
        out["report_text"] = self._view(S.report_path(bid, number, self.env), REPORT_VIEW)
        return {"v": 1, "bot": bid, "run": out, "was_unseen": was_unseen}

    # -- parsing a when
    def parse(self, text, tz, now):
        """`POST /api/bots/parse`: `{when, describe, next: [epoch x3], error}`; a text that cannot be read is an `error`, not an HTTP error."""
        if not isinstance(text, str):
            raise Refused("bad_request", "text must be a string")
        if len(text) > WHEN_MAX:
            return {"when": None, "describe": None, "next": [], "error": f"too long: at most {WHEN_MAX} characters"}
        try:
            parsed = S.parse_when(text, now=now, tz=tz)
        except ValueError as exc:
            return {"when": None, "describe": None, "next": [], "error": self.san_line(str(exc), 300)}
        return {"when": parsed["when"], "describe": self.san_line(S.describe(parsed["when"], now), 80),
                "next": list(parsed["next"]), "error": None}

    # -- writing
    def _text_field(self, body, key, limit, what, allow_empty=False):
        value = body[key]
        problem = self.check_text(value, limit, what, allow_empty)
        if problem:
            raise Refused("bad_request", problem)
        return value

    def _fields(self, body, allowed):
        extra = sorted(set(body) - set(allowed))
        if extra:
            raise Refused("bad_request", f"unknown field: {self.san_line(str(extra[0]), 40)}")

    def _changes(self, body, labels, resolve_dir, tz, now, rec=None):
        """The validated fields of a create or edit as a partial schedule (`rec` is the bot being edited)."""
        out = {}
        if "name" in body:
            name = self._text_field(body, "name", NAME_MAX, "name")
            if "\n" in name or "\t" in name:
                raise Refused("bad_request", "name must be one line")
            out["name"] = name.strip()
        if "prompt" in body:
            out["task"] = {"prompt": self._text_field(body, "prompt", self.max_prompt, "prompt")}
        if "when" in body:
            if not isinstance(body["when"], str):
                raise Refused("bad_request", "when must be text such as 'weekdays 9am'")
            zone = rec["when"].get("tz", "local") if rec else tz
            parsed = self.parse(body["when"], zone, now)
            if parsed["error"]:
                raise Refused("bad_request", parsed["error"])
            out["when"] = parsed["when"]
            out["_next"] = parsed["next"]
        run = {}
        if "dir" in body:
            real = resolve_dir(body["dir"])
            if real is None:
                raise Refused("bad_request", "dir must be an offered directory or a folder below your home directory")
            run["cwd"] = real
        if "account" in body:
            acct = body["account"]
            if acct is not None and (not isinstance(acct, str) or acct not in labels()):
                raise Refused("bad_request", "account must be one of the offered accounts, or null for automatic")
            run["account"] = acct
        if "mode" in body:
            if body["mode"] not in PHONE_MODES:
                raise Refused("bad_request", f"mode must be one of {', '.join(PHONE_MODES)}")
            run["mode"] = body["mode"]
        if "permissions" in body:
            if body["permissions"] not in PHONE_PERMISSIONS:
                raise Refused("bad_request", f"permissions must be one of {', '.join(PHONE_PERMISSIONS)}: full is set on the laptop")
            run["permissions"] = body["permissions"]
        if "max_time_s" in body:
            run["max_time_s"] = _number(body["max_time_s"], "max_time_s", 60, PHONE_MAX_TIME_S, integer=True)
        if "max_cost_usd" in body:
            run["max_cost_usd"] = float(_number(body["max_cost_usd"], "max_cost_usd", 0.01, PHONE_MAX_COST_USD))
        if run:
            out["run"] = run
        if "notify" in body:
            if not isinstance(body["notify"], bool):
                raise Refused("bad_request", "notify must be true or false")
            out["notify"] = {"on": list(NOTIFY_ALL) if body["notify"] else [], "done": bool(body["notify"])}
        if "identity" in body:
            out["identity"] = {"system": self._text_field(body, "identity", IDENTITY_MAX, "identity", allow_empty=True)}
        return out

    def _poke(self, op, bid=None, now=None):
        """Tells a live scheduler (best effort: the file is saved either way); True when it was told."""
        if not self.scheduler(self.wall() if now is None else now)["alive"]:
            return False
        try:
            S.send_command(S.command_dir(self.env), {"op": op} if bid is None else {"op": op, "id": bid})
        except (OSError, ValueError):
            return False
        return True

    def create(self, body, labels, resolve_dir, tz, now, gate=None):
        """`POST /api/bots`: the whitelisted, bounded bot of a phone. Returns (record, next fire times). `gate()` is called
        once the request is valid and before anything is written (the server's rate limit: a typo costs nothing)."""
        self._fields(body, CREATE_FIELDS)
        for key in REQUIRED:
            if key not in body:
                raise Refused("bad_request", f"{key} is required")
        kind = body.get("kind", "task")
        if kind not in S.KINDS:
            raise Refused("bad_request", f"kind must be one of {', '.join(S.KINDS)}")
        changes = self._changes(body, labels, resolve_dir, tz, now)
        upcoming = changes.pop("_next", [])
        run = changes.setdefault("run", {})
        run.setdefault("permissions", "edit")
        run.setdefault("mode", "headless")
        spec = {"kind": kind, "enabled": True, "origin": "phone", **changes}
        spec.setdefault("notify", {"on": list(NOTIFY_ALL), "done": True})
        if run["permissions"] not in PHONE_PERMISSIONS:                         # unreachable through _changes; the one rule that must hold
            raise Refused("bad_request", "a phone cannot create a bot with full permissions")

        if gate:
            gate()

        def add(recs):
            if len(recs) >= MAX_BOTS:
                raise Refused("conflict", f"at most {MAX_BOTS} bots: delete one first")
            rec = S.new_schedule(spec, now=now, existing_ids={r["id"] for r in recs}, origin="phone")
            recs.append(rec)
            return rec
        try:
            rec = self.store.mutate(add)
        except ValueError as exc:
            raise Refused("bad_request", self.san_line(str(exc), 200)) from None
        except S.ScheduleError:
            raise Refused("internal", "the schedules file cannot be used: run `hivegrid-schedule doctor` on the laptop") from None
        S.init_state_dir(rec, now, self.env)
        self._poke("reload", now=now)
        return rec, upcoming

    def edit(self, bid, body, labels, resolve_dir, tz, now, gate=None):
        """`POST /api/bots/<id>`: some of the fields of a create (never `kind`), under the same limits."""
        self._fields(body, EDIT_FIELDS)
        if not body:
            raise Refused("bad_request", "nothing to change")
        rec = self._rec(bid)
        if rec["run"]["permissions"] == "full" and body.get("permissions") not in PHONE_PERMISSIONS:
            raise Refused("bad_request", "a bot with full permissions is changed on the laptop (the phone can only lower it to edit or readonly)")
        changes = self._changes(body, labels, resolve_dir, tz, now, rec)
        upcoming = changes.pop("_next", None)
        if gate:
            gate()
        try:
            new = self.store.change(bid, changes, now=now, via="phone")
        except KeyError:
            raise Refused("not_found", "no such bot") from None
        except ValueError as exc:
            raise Refused("bad_request", self.san_line(str(exc), 200)) from None
        S.init_state_dir(new, now, self.env)
        S.sync_identity(new, self.env)
        self._poke("reload", now=now)
        return new, upcoming

    def act(self, bid, verb, now, gate=None):
        """`POST /api/bots/<id>/<verb>` for run, pause, resume, skip, cancel and ack. Returns a dict for the answer."""
        rec = self._rec(bid)
        sdir = self._sdir(bid)
        st = S.load_state(sdir)
        alive = self.scheduler(now)["alive"]
        running = bool(st.get("running"))
        if verb == "run":
            if running:
                raise Refused("conflict", "this bot is running already")
            if gate:
                gate()
            if alive:
                self._poke("run-now", bid, now)
            else:
                S.update_state(S.init_state_dir(rec, now, self.env), lambda s: s.__setitem__("run_now", True))
            return {"queued": not alive}
        if verb == "skip":
            if not rec["enabled"] or st.get("done"):
                raise Refused("conflict", "this bot is not scheduled: there is no next run to skip")
            if S.next_run(rec, st, now) is None:
                raise Refused("conflict", "there is no next run to skip")
            if alive:
                self._poke("skip", bid, now)
            else:
                S.update_state(S.init_state_dir(rec, now, self.env), lambda s: S.skip_next(rec, s, now))
            return {"queued": not alive}
        if verb == "cancel":
            if not running:
                raise Refused("conflict", "no run is live")
            if not alive:
                raise Refused("conflict", "the scheduler is not running, so it cannot stop the run")
            self._poke("cancel", bid, now)
            return {}
        if verb == "pause":
            self.store.change(bid, {"enabled": False}, now=now)
            self._poke("reload", now=now)
            return {}
        if verb == "resume":
            new = self.store.change(bid, {"enabled": True}, now=now)
            sd = S.init_state_dir(new, now, self.env)

            def reset(s):
                s["paused_reason"], s["consec_failures"], s["done"] = None, 0, False
                s["baseline"] = int(now)
            S.update_state(sd, reset)
            self._poke("reload", now=now)
            return {}
        if verb == "ack":
            return {"marked": S.mark_seen(bid, env=self.env)}
        raise Refused("not_found", "no such action")

    def delete(self, bid, confirm, now, gate=None):
        """`POST /api/bots/<id>/delete {"confirm": true}`: the definition and the state directory (memory, runs) go."""
        self._rec(bid)
        sdir = self._sdir(bid)
        if S.load_state(sdir).get("running"):
            raise Refused("conflict", "a run is live: stop it first")
        if not confirm:
            raise Refused("confirm_required", "confirm to delete the bot with its memory and run history")
        if gate:
            gate()
        self.store.remove(bid)
        if os.path.isdir(sdir) and not os.path.islink(sdir):
            shutil.rmtree(sdir, ignore_errors=True)
        self._poke("reload", now=now)

    # -- talk (the bot's one main session, see docs/schedules.md "Briefing and chat")
    def say(self, bid, text, now, gate=None):
        """`POST /api/bots/<id>/say`: hands the text to the scheduler as a chat turn of the bot's main session. Returns
        `{queued, position}`. The scheduler must be running (a message nobody reads would sit in a queue nobody serves);
        `gate()` is the server's rate limit, called once the request is valid."""
        problem = self.check_text(text, SAY_MAX, "message", False)
        if problem:
            raise Refused("bad_request", problem)
        self._rec(bid)
        if not self.scheduler(now)["alive"]:
            raise Refused("conflict", "the scheduler is not running on the laptop, so the bot cannot hear you: hivegrid-autostart install starts it")
        if gate:
            gate()
        try:
            got = S.say(bid, text, env=self.env, now=now + CLOCK_SLACK_S)       # the same slack `scheduler()` allows for a whole-second `now`
        except S.ScheduleError as exc:
            code = getattr(exc, "code", None)
            if code == "unknown_bot":
                raise Refused("not_found", "no such bot") from None
            if code == "queue_full":
                raise Refused("conflict", f"the bot has {CHAT_QUEUE_MAX} messages waiting already: wait until it has answered") from None
            if code == "no_daemon":
                raise Refused("conflict", "the scheduler is not running on the laptop, so the bot cannot hear you") from None
            raise Refused("conflict", self.san_line(str(exc), 200)) from None
        return {"queued": bool(got.get("queued")), "position": int(got.get("position") or 0)}

    def conversation(self, bid, now):
        """The state of the conversation page: `{session_file (a path the chat parser may read, or None), live, busy,
        queue, error, pending}`; `pending` are the messages still waiting for their turn."""
        rec = self._rec(bid)
        sdir = self._sdir(bid)
        try:
            conv = S.conversation(bid, env=self.env)
        except S.ScheduleError:
            raise Refused("internal", "the bot's state cannot be read: run `hivegrid-schedule doctor` on the laptop") from None
        sf = conv.get("session_file")
        real = D.session_path_ok(sf, self.home, self.uid) if isinstance(sf, str) and sf else None
        queue = S.load_state(sdir).get("chat_queue")
        pending = []
        if isinstance(queue, list):
            for q in queue[:CHAT_QUEUE_MAX]:
                if isinstance(q, dict) and isinstance(q.get("text"), str):
                    pending.append({"text": self.san_text(q["text"], SAY_MAX),
                                    "ts": q["ts"] if isinstance(q.get("ts"), (int, float)) and not isinstance(q.get("ts"), bool) else None})
        err = conv.get("error")
        return {"name": self.san_line(rec["name"], NAME_MAX), "session_file": real, "live": bool(conv.get("live")),
                "busy": bool(conv.get("busy")), "queue": int(conv.get("queue") or 0),
                "error": self.san_line(err, 300) if err else None, "pending": pending}

    # -- the briefing: the important reports of every bot
    def _entry(self, e):
        return {"bot_id": e["bot_id"], "bot_name": self.san_line(e.get("bot_name") or e["bot_id"], NAME_MAX), "kind": e.get("kind"),
                "run": e.get("run"), "level": e.get("level"), "status": e.get("status"),
                "headline": self.san_line(e.get("headline") or "", HEADLINE_MAX), "ended": e.get("ended"), "seen": bool(e.get("seen"))}

    def briefing(self, now):
        """`GET /api/bots/briefing`: `{entries (newest first), unseen, total, rev}`. `rev` leaves out the clock."""
        try:
            entries = [self._entry(e) for e in S.briefing(self.env, limit=BRIEFING_LIMIT, include_seen=True)]
            counts = S.briefing_counts(self.env)
        except S.ScheduleError:
            raise Refused("internal", "the schedules file cannot be used: run `hivegrid-schedule doctor` on the laptop") from None
        out = {"unseen": counts["unseen"], "total": counts["total"], "entries": entries}
        return {"v": 1, "now": now, "rev": _rev(out), **out}

    def briefing_seen(self):
        """`POST /api/bots/briefing/seen`: every report in the briefing is read."""
        return {"marked": S.mark_briefing_seen(self.env)}
