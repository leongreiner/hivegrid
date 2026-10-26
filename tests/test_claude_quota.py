"""claude-quota: account precedence, usage parsing, cache/backoff, history.jsonl, token sources.
The network is never touched: fetch (or urllib's opener) is replaced in every test that would call it."""
import argparse
import email.message
import hashlib
import importlib.util
import io
import json
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
import urllib.error
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin")
sys.dont_write_bytecode = True  # bin/ is copied wholesale by install.sh: no __pycache__ there
sys.path.insert(0, BIN)


def load(name):
    spec = importlib.util.spec_from_loader(name, SourceFileLoader(name, os.path.join(BIN, name)))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


Q = load("claude-quota")
NOW = 1_700_000_000.0
FAR = 4_000_000_000_000  # ms (year 2096): valid both at NOW and at the real time.time() of main()
PAST = (NOW - 60) * 1000  # ms, expired
PAYLOAD = {"five_hour": {"utilization": 42.0, "resets_at": "2023-11-14T23:13:20Z"},
           "seven_day": {"utilization": 7, "resets_at": NOW + 3 * 86400}}


class Env(unittest.TestCase):
    """Temp HOME; the module's path constants point into it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("CLAUDE_QUOTA_DIRS", None)
        for name, value in (("HOME", self.home),
                            ("SETTINGS", self.home / ".config" / "hivegrid" / "settings.json"),
                            ("OMP_PROFILES", self.home / ".omp" / "profiles"),
                            ("CACHE_DIR", self.home / ".cache" / "wezterm-claude-quota")):
            p = mock.patch.object(Q, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.calls = []
        self.reply = ("ok", Q.parse_usage(PAYLOAD), 200, None)
        p = mock.patch.object(Q, "fetch", self.fake_fetch)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(Q, "claude_version", lambda: "1.0.0")
        p.start()
        self.addCleanup(p.stop)

    def fake_fetch(self, token, version):
        self.calls.append(token)
        return self.reply

    def claude_dir(self, name, token="cred-token", expires=FAR):
        d = self.home / name
        d.mkdir()
        if token is not None:
            (d / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": token, "expiresAt": expires}}))
        return d

    def settings(self, data):
        Q.SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        Q.SETTINGS.write_text(json.dumps(data))

    def omp_db(self, profile, rows):
        d = Q.OMP_PROFILES / profile / "agent"
        d.mkdir(parents=True)
        db = d / "agent.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE auth_credentials (id INTEGER PRIMARY KEY, provider TEXT, credential_type TEXT, "
                    "data TEXT, disabled_cause TEXT)")
        con.executemany("INSERT INTO auth_credentials (provider, credential_type, data, disabled_cause) VALUES (?,?,?,?)",
                        rows)
        con.commit()
        con.close()
        return db

    def cache(self):
        Q.ensure_cache_dir()

    def history(self):
        try:
            return [json.loads(line) for line in Q.history_path().read_text().splitlines()]
        except FileNotFoundError:
            return []


def ns(dirs=None, labels=None):
    return argparse.Namespace(dirs=dirs, labels=labels)


class AccountPrecedence(Env):
    def test_env_dirs_win_over_settings(self):
        self.claude_dir(".claude")
        self.settings({"accounts": [{"label": "main", "dir": "~/.claude"}]})
        os.environ["CLAUDE_QUOTA_DIRS"] = "~/.claude-a,~/x/.claude-b"
        got = Q.resolve_accounts(ns())
        self.assertEqual([(d, lb) for d, lb, _ in got],
                         [(self.home / ".claude-a", "a"), (self.home / "x" / ".claude-b", "b")])

    def test_dirs_flag_wins_over_env(self):
        os.environ["CLAUDE_QUOTA_DIRS"] = "~/.claude-a"
        got = Q.resolve_accounts(ns(dirs=f"{self.home}/.claude-z", labels="zed"))
        self.assertEqual([(d, lb) for d, lb, _ in got], [(self.home / ".claude-z", "zed")])

    def test_label_count_mismatch_exits(self):
        with self.assertRaises(SystemExit):
            Q.resolve_accounts(ns(dirs="~/.claude-a,~/.claude-b", labels="one"))

    def test_settings_list_minus_hidden_plus_undiscovered(self):
        for name in (".claude", ".claude-2", ".claude-x"):
            self.claude_dir(name)
        self.claude_dir(".claude-nocreds", token=None)
        self.settings({"accounts": [
            {"label": "boss", "dir": "~/.claude", "profile": "work"},
            {"label": "two", "dir": "~/.claude-2", "hidden": True},
            {"label": "w", "dir": "~/work-claude", "profile": "work"},  # manual, no credentials yet
        ]})
        got = Q.resolve_accounts(ns())
        self.assertEqual(got, [(self.home / ".claude", "boss", "work"),
                               (self.home / "work-claude", "w", "work"),
                               (self.home / ".claude-x", "x", "x")])

    def test_without_settings_list_discovered_dirs_alone_in_order(self):
        for name in (".claude-zz", ".claude-private", ".claude-2", ".claude"):
            self.claude_dir(name)
        self.settings({"theme": {"preset": "slate"}})  # a settings file without accounts
        got = Q.resolve_accounts(ns())
        self.assertEqual([lb for _, lb, _ in got], ["main", "2", "private", "zz"])
        Q.SETTINGS.unlink()
        self.assertEqual([lb for _, lb, _ in Q.resolve_accounts(ns())], ["main", "2", "private", "zz"])

    def test_unlisted_accounts_pair_with_conventional_profiles(self):
        self.claude_dir(".claude")
        self.claude_dir(".claude-2")
        self.claude_dir(".claude-work")
        (Q.OMP_PROFILES / "work").mkdir(parents=True)
        got = {lb: pf for _, lb, pf in Q.resolve_accounts(ns())}
        self.assertEqual((got["main"], got["2"], got["work"]), ("work", "team", "work"))
        self.settings({"accounts": [{"label": "main", "dir": "~/.claude", "profile": None}]})
        self.assertEqual(Q.resolve_accounts(ns())[0][2], "work")

    def test_hidden_account_is_not_refreshed(self):
        self.claude_dir(".claude")
        self.claude_dir(".claude-2", token="other")
        self.settings({"accounts": [{"label": "main", "dir": "~/.claude"},
                                    {"label": "2", "dir": "~/.claude-2", "hidden": True}]})
        Q.main(["refresh"])
        self.assertEqual(self.calls, ["cred-token"])
        self.assertFalse((Q.CACHE_DIR / "2.json").exists())


class UsageParsing(unittest.TestCase):
    def test_both_windows(self):
        w = Q.parse_usage(PAYLOAD)
        self.assertEqual(w["five_hour"], {"pct": 42.0, "reset_at": 1_700_003_600.0})
        self.assertEqual(w["seven_day"], {"pct": 7.0, "reset_at": NOW + 3 * 86400})

    def test_partial_and_invalid_windows(self):
        self.assertEqual(set(Q.parse_usage({"five_hour": {"utilization": 0}})), {"five_hour"})
        bad = [{"utilization": 101}, {"utilization": -1}, {"utilization": True}, {"utilization": "50"},
               {"resets_at": "2023-11-14T23:13:20Z"}, "50", None]
        for w in bad:
            self.assertIsNone(Q.parse_usage({"five_hour": w}), w)
        self.assertEqual(Q.parse_usage({"five_hour": {"utilization": 100}, "seven_day": {"utilization": 200}}),
                         {"five_hour": {"pct": 100.0, "reset_at": None}})

    def test_non_dict_payloads(self):
        for p in (None, [], "x", 3, {}):
            self.assertIsNone(Q.parse_usage(p), p)

    def test_reset_time_formats(self):
        self.assertEqual(Q.parse_ts("2023-11-14T22:13:20.1234567+00:00"), NOW + 0.123456)
        self.assertEqual(Q.parse_ts("2023-11-14T22:13:20Z"), NOW)
        self.assertEqual(Q.parse_ts(NOW), NOW)
        for v in ("soon", "", True, None, {}):
            self.assertIsNone(Q.parse_ts(v), v)
        w = Q.parse_usage({"five_hour": {"utilization": 5, "resets_at": "garbage"}})
        self.assertEqual(w["five_hour"], {"pct": 5.0, "reset_at": None})


class FetchStatusMapping(unittest.TestCase):
    """fetch() against a fake opener: HTTP status -> state."""

    def run_fetch(self, *, raise_exc=None, body=b""):
        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        seen = {}

        class Opener:
            def open(self, req, timeout=None):
                seen["auth"] = req.get_header("Authorization")
                if raise_exc:
                    raise raise_exc
                return Resp(body)

        with mock.patch.object(Q.urllib.request, "build_opener", lambda *h: Opener()):
            out = Q.fetch("tok", "1.0.0")
        self.assertEqual(seen["auth"], "Bearer tok")
        return out

    def http_error(self, code, retry_after=None):
        h = email.message.Message()
        if retry_after is not None:
            h["Retry-After"] = retry_after
        return urllib.error.HTTPError(Q.API_URL, code, "x", h, io.BytesIO(b""))

    def test_ok(self):
        kind, windows, http, _ = self.run_fetch(body=json.dumps(PAYLOAD).encode())
        self.assertEqual((kind, http), ("ok", 200))
        self.assertEqual(windows["five_hour"]["pct"], 42.0)

    def test_ok_status_with_unusable_body_is_an_error(self):
        self.assertEqual(self.run_fetch(body=b"{}")[0], "error")
        self.assertEqual(self.run_fetch(body=b"<html>")[0], "error")

    def test_status_codes(self):
        self.assertEqual(self.run_fetch(raise_exc=self.http_error(429, "120")), ("rate_limited", None, 429, 120.0))
        self.assertEqual(self.run_fetch(raise_exc=self.http_error(429, "Wed, 21 Oct")), ("rate_limited", None, 429, None))
        self.assertEqual(self.run_fetch(raise_exc=self.http_error(401))[0], "login")
        self.assertEqual(self.run_fetch(raise_exc=self.http_error(403))[0], "login")
        self.assertEqual(self.run_fetch(raise_exc=self.http_error(500)), ("error", None, 500, None))
        self.assertEqual(self.run_fetch(raise_exc=OSError("no route")), ("error", None, None, None))


class Backoff(unittest.TestCase):
    def test_schedule(self):
        self.assertEqual(Q.backoff("ok", 0), Q.OK_TTL)
        self.assertEqual(Q.backoff("login", 5), Q.LOGIN_TTL)
        self.assertEqual([Q.backoff("error", n) for n in (1, 2, 3)], [30, 60, 120])
        self.assertEqual([Q.backoff("rate_limited", n) for n in (1, 2)], [300, 600])
        self.assertEqual(Q.backoff("error", 50), Q.MAX_BACKOFF)
        self.assertEqual(Q.backoff("rate_limited", 1, retry_after=900), 900)  # server asks for longer
        self.assertEqual(Q.backoff("rate_limited", 2, retry_after=10), 600)  # never shorter than ours
        self.assertEqual(Q.backoff("error", 1, retry_after=99999), Q.MAX_BACKOFF)

    def test_failures_keep_last_numbers_and_count_up(self):
        ok = Q.next_entry(None, "main", 0, "ok", NOW, Q.parse_usage(PAYLOAD), 200)
        self.assertEqual((ok["fails"], ok["updated_at"], ok["next_at"]), (0, NOW, NOW + Q.OK_TTL))
        e1 = Q.next_entry(ok, "main", 0, "error", NOW + 60, http=500)
        e2 = Q.next_entry(e1, "main", 0, "error", NOW + 120, http=500)
        self.assertEqual((e1["fails"], e2["fails"]), (1, 2))
        self.assertEqual(e2["next_at"], NOW + 120 + 60)
        self.assertEqual((e2["five_hour"], e2["updated_at"]), (ok["five_hour"], NOW))
        back = Q.next_entry(e2, "main", 0, "ok", NOW + 200, {"five_hour": {"pct": 1.0, "reset_at": None}}, 200)
        self.assertEqual((back["fails"], back["five_hour"]["pct"], back["seven_day"]), (0, 1.0, None))

    def test_stale_numbers_are_marked(self):
        ok = Q.next_entry(None, "main", 0, "ok", NOW, Q.parse_usage(PAYLOAD), 200)
        self.assertNotIn("~", Q.render([ok], NOW + 10, compact=True))
        err = Q.next_entry(ok, "main", 0, "error", NOW + 60, http=500)
        self.assertIn("42%~", Q.render([err], NOW + 60, compact=True))
        self.assertIn("~", Q.render([ok], NOW + Q.STALE_AFTER + 1, compact=True))
        login = Q.next_entry(None, "main", 0, "login", NOW)
        self.assertEqual(Q.render([login], NOW), "main login")


class RefreshCache(Env):
    def test_not_due_means_no_request(self):
        d = self.claude_dir(".claude")
        self.cache()
        e, fetched = Q.refresh_one(d, "main", 0, "v", NOW)
        self.assertTrue(fetched)
        e2, fetched2 = Q.refresh_one(d, "main", 0, "v", NOW + Q.OK_TTL - 1)
        self.assertFalse(fetched2)
        self.assertEqual(e2, e)
        self.assertEqual(len(self.calls), 1)
        Q.refresh_one(d, "main", 0, "v", NOW + Q.OK_TTL)
        self.assertEqual(len(self.calls), 2)

    def test_rate_limit_backs_off_and_honours_retry_after(self):
        d = self.claude_dir(".claude")
        self.cache()
        self.reply = ("rate_limited", None, 429, 1000.0)
        e, _ = Q.refresh_one(d, "main", 0, "v", NOW)
        self.assertEqual(e["next_at"], NOW + 1000)
        self.assertFalse(Q.refresh_one(d, "main", 0, "v", NOW + 999)[1])

    def test_cache_holds_no_token_and_is_private(self):
        d = self.claude_dir(".claude", token="SECRET-abc")
        Q.main(["--dirs", str(d), "refresh"])
        self.assertEqual(self.calls, ["SECRET-abc"])
        self.assertEqual(stat.S_IMODE(Q.CACHE_DIR.stat().st_mode), 0o700)
        for f in Q.CACHE_DIR.iterdir():
            if f.is_file():
                self.assertNotIn("SECRET", f.read_text(), f.name)
        self.assertEqual(stat.S_IMODE((Q.CACHE_DIR / "main.json").stat().st_mode), 0o600)

    def test_held_lock_skips_refresh_and_is_left_to_its_owner(self):
        d = self.claude_dir(".claude")
        self.cache()
        lock = Q.CACHE_DIR / "refresh.lock"
        lock.mkdir()
        Q.main(["--dirs", str(d), "refresh"])
        self.assertEqual(self.calls, [])
        self.assertTrue(lock.is_dir())  # another refresh is running: its lock must stay

    def test_stale_lock_is_broken(self):
        d = self.claude_dir(".claude")
        self.cache()
        lock = Q.CACHE_DIR / "refresh.lock"
        lock.mkdir()
        old = lock.stat().st_mtime - Q.LOCK_STALE - 5
        os.utime(lock, (old, old))
        Q.main(["--dirs", str(d), "refresh"])
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(lock.exists())


class History(Env):
    def setUp(self):
        super().setUp()
        self.dir = self.claude_dir(".claude")
        self.cache()

    def at(self, t):
        return Q.refresh_one(self.dir, "main", 0, "v", t)[0]

    def test_one_sample_per_two_minutes_while_numbers_hold(self):
        self.at(NOW)
        self.at(NOW + 60)
        self.at(NOW + 119)
        self.assertEqual([h["ts"] for h in self.history()], [int(NOW)])
        self.at(NOW + 180)
        self.assertEqual([h["ts"] for h in self.history()], [int(NOW), int(NOW + 180)])

    def test_sample_contents_and_mode(self):
        self.at(NOW)
        (h,) = self.history()
        self.assertEqual(set(h), {"ts", "label", "dir", "five_hour", "seven_day"})
        self.assertEqual(h["dir"], "~/.claude")  # the account's dir, home-relative
        self.assertEqual((h["label"], h["five_hour"]["pct"], h["seven_day"]["reset_at"]), ("main", 42.0, NOW + 3 * 86400))
        self.assertEqual(stat.S_IMODE(Q.history_path().stat().st_mode), 0o600)

    def test_failures_write_no_sample_and_keep_the_sampling_clock(self):
        self.at(NOW)
        self.reply = ("error", None, 500, None)
        self.at(NOW + 60)
        self.assertEqual(len(self.history()), 1)
        self.reply = ("ok", Q.parse_usage(PAYLOAD), 200, None)
        self.at(NOW + 100)  # error backoff over; still within 2 min of the last sample
        self.assertEqual(len(self.history()), 1)

    def test_accounts_sample_independently(self):
        other = self.claude_dir(".claude-2")
        self.at(NOW)
        Q.refresh_one(other, "2", 1, "v", NOW + 10)
        self.assertEqual([h["label"] for h in self.history()], ["main", "2"])

    def write_history(self, ages_days):
        lines = [json.dumps({"ts": int(NOW - a * 86400), "label": "main"}) + "\n" for a in ages_days]
        fd = os.open(Q.history_path(), os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, "w") as f:
            f.writelines(lines)

    def test_trim_to_thirty_days_once_a_day_over(self):
        self.write_history([32, 30.5, 29, 1])
        self.at(NOW)
        ages = [round((NOW - h["ts"]) / 86400, 1) for h in self.history()]
        self.assertEqual(ages, [29, 1, 0])
        self.assertEqual(stat.S_IMODE(Q.history_path().stat().st_mode), 0o600)
        self.assertEqual(sorted(p.name for p in Q.CACHE_DIR.iterdir() if p.name.startswith(".")), [])

    def test_no_rewrite_while_oldest_is_within_the_slack(self):
        self.write_history([30.5, 29])
        self.at(NOW)
        self.assertEqual(len(self.history()), 3)


class TokenSources(Env):
    def setUp(self):
        super().setUp()
        self.cache()

    def row(self, token, expires, provider="anthropic", kind="oauth", disabled=None):
        return (provider, kind, json.dumps({"access": token, "expires": expires}), disabled)

    def refresh(self, d, profile, label="main"):
        return Q.refresh_one(d, label, 0, "v", NOW, profile)[0]

    def test_omp_profile_token_wins_over_credentials(self):
        d = self.claude_dir(".claude", token="cred")
        self.omp_db("work", [self.row("omp", FAR)])
        self.assertEqual(self.refresh(d, "work")["state"], "ok")
        self.assertEqual(self.calls, ["omp"])

    def test_expired_or_unusable_omp_rows_fall_back_to_credentials(self):
        d = self.claude_dir(".claude", token="cred")
        self.omp_db("work", [self.row("old", PAST), self.row("dis", FAR, disabled="revoked"),
                             self.row("oa", FAR, provider="openai"), self.row("key", FAR, kind="api_key"),
                             ("anthropic", "oauth", "not json", None), self.row("", FAR)])
        self.refresh(d, "work")
        self.assertEqual(self.calls, ["cred"])

    def test_latest_expiring_omp_row_is_used(self):
        d = self.claude_dir(".claude", token=None)
        self.omp_db("work", [self.row("a", FAR), self.row("b", FAR + 1000), self.row("c", FAR - 1000)])
        self.refresh(d, "work")
        self.assertEqual(self.calls, ["b"])

    def test_no_valid_token_anywhere_is_login_without_request(self):
        d = self.claude_dir(".claude", token="cred", expires=PAST)
        self.omp_db("work", [self.row("omp", PAST)])
        e = self.refresh(d, "work")
        self.assertEqual(e["state"], "login")
        self.assertEqual(self.calls, [])
        self.assertEqual(e["next_at"], NOW + Q.LOGIN_TTL)

    def test_malformed_credentials_mean_login(self):
        for i, content in enumerate(("{", "[]", '{"claudeAiOauth": {"accessToken": "t"}}',
                                     '{"claudeAiOauth": {"accessToken": "", "expiresAt": 9e15}}')):
            d = self.home / f"c{i}"
            d.mkdir()
            (d / ".credentials.json").write_text(content)
            self.assertEqual(Q.read_token(d, NOW), (None, "login"), content)
        self.assertEqual(Q.read_token(self.home / "missing", NOW), (None, "login"))

    def test_profile_names_cannot_escape_the_profiles_dir(self):
        self.omp_db("work", [self.row("omp", FAR)])
        for bad in ("../profiles/work", "work/../work", ".hidden", "", None):
            self.assertIsNone(Q.read_omp_token(bad, NOW), bad)
        self.assertEqual(Q.read_omp_token("work", NOW), "omp")

    def test_agent_db_is_never_modified(self):
        d = self.claude_dir(".claude", token="cred")
        db = self.omp_db("work", [self.row("omp", FAR)])
        before = (hashlib.sha256(db.read_bytes()).hexdigest(), db.stat().st_mtime_ns, sorted(os.listdir(db.parent)))
        self.refresh(d, "work")
        after = (hashlib.sha256(db.read_bytes()).hexdigest(), db.stat().st_mtime_ns, sorted(os.listdir(db.parent)))
        self.assertEqual(after, before)

    def test_corrupt_db_falls_back_to_credentials(self):
        d = self.claude_dir(".claude", token="cred")
        bad = Q.OMP_PROFILES / "work" / "agent"
        bad.mkdir(parents=True)
        (bad / "agent.db").write_bytes(b"not a database at all" * 10)
        self.refresh(d, "work")
        self.assertEqual(self.calls, ["cred"])

    def test_settings_profile_drives_the_token_lookup(self):
        self.claude_dir(".claude-w", token="cred")
        self.omp_db("work", [self.row("omp-work", FAR)])
        self.settings({"accounts": [{"label": "w", "dir": "~/.claude-w", "profile": "work"}]})
        Q.main(["refresh"])
        self.assertEqual(self.calls, ["omp-work"])


if __name__ == "__main__":
    unittest.main()
