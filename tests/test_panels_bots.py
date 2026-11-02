"""The Bots panel (hivegrid_panels.BotsPage) over a temp HOME: list rendering from the store, state
directories and runtime summary, the new-bot wizard, the key actions and what they change on disk. The
scheduler is never started; commands to it are read from its inbox."""
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin")
sys.dont_write_bytecode = True  # bin/ is copied wholesale by install.sh: no __pycache__ there
sys.path.insert(0, BIN)
import hivegrid_panels as P  # noqa: E402
import hivegrid_schedule as H  # noqa: E402
import hivegrid_settings_core as SC  # noqa: E402
import hivegrid_ui as U  # noqa: E402

NOW = 1_793_600_400.0  # Monday 2026-11-02 06:20 UTC
NOKEY = frozenset()


def put(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def sub(*pairs):
    """An editor action that replaces text in the file it is handed: sub((old, new), ...)."""
    def do(path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for old, new in pairs:
            assert old in text, old
            text = text.replace(old, new, 1)
        put(path, text)
    return do


class BotsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        t = self.tmp
        for d in ("config", "state", "run", "work"):
            os.makedirs(f"{t}/{d}")
        os.chmod(f"{t}/run", 0o700)
        env = mock.patch.dict(os.environ, {"HOME": t, "XDG_CONFIG_HOME": f"{t}/config", "XDG_STATE_HOME": f"{t}/state",
                                           "XDG_RUNTIME_DIR": f"{t}/run", "HIVEGRID_SETTINGS": f"{t}/settings.json",
                                           "TZ": "UTC", "EDITOR": "fake-editor", "VISUAL": ""})
        env.start()
        self.addCleanup(env.stop)
        time.tzset()
        self.addCleanup(time.tzset)
        self.now = NOW
        self.store = H.Store()
        self.edits = []  # (argv, contents before)
        self.editor_do = lambda path: None
        self.started = []
        self.model = SC.Model(SC.Settings(SC.settings_path()))
        self.page = P.BotsPage(self.model, now=lambda: self.now, editor=self.fake_editor,
                               start=lambda: (self.started.append(1), (True, "starting"))[1], cwd=f"{t}/work", profile="")
        self.ui = U.UI(self.model, [self.page])
        self.ui.size, self.ui.out = (130, 32), io.StringIO()

    # helpers ------------------------------------------------------------------
    def fake_editor(self, argv):
        with open(argv[-1], encoding="utf-8") as f:
            self.edits.append((argv, f.read()))
        self.editor_do(argv[-1])
        return SimpleNamespace(returncode=0)

    def write_settings(self, data):
        with open(SC.settings_path(), "w") as f:
            json.dump(data, f)
        self.model.settings = SC.Settings(SC.settings_path())

    def add(self, name, kind="task", when="weekdays 9am", prompt="do the thing", **run):
        spec = {"name": name, "kind": kind, "when": H.parse_when(when, now=self.now)["when"],
                "task": {"prompt": prompt}, "run": dict({"cwd": f"{self.tmp}/work"}, **run)}
        rec = self.store.add(spec, now=int(self.now - 86400 * 3))
        H.init_state_dir(rec, self.now - 86400 * 3)
        return rec

    def run_rec(self, rec, n, status, ago, seen, **kw):
        H.append_run(H.state_dir(rec["id"]), dict({
            "run": n, "schedule": rec["id"], "trigger": "cron", "mode": "headless", "started": self.now - ago - 90,
            "ended": self.now - ago, "status": status, "seen": seen, "summary": "", "cost_usd": 0.25}, **kw))

    def alive(self, on=True):
        if on:
            H.write_summary(H.summary_path(), H.build_summary(self.now, {"heartbeat": self.now - 3}))
        else:
            try:
                os.unlink(H.summary_path())
            except FileNotFoundError:
                pass

    def show(self):
        self.page.on_show(self.ui)
        return self.screen()

    def screen(self):
        self.ui.draw()
        return "\n".join("".join(c[0] for c in row).rstrip() for row in self.ui.cv.cells)

    def press(self, *keys):
        for k in keys:
            mods = frozenset({"SHIFT"}) if len(k) == 1 and k.isupper() else NOKEY
            self.ui.handle(("key", mods, k.lower() if mods else k))

    def type(self, text):
        for ch in text:
            self.press("Space" if ch == " " else ch)

    def commands(self):
        return H.read_commands(H.command_dir())


class ListTest(BotsCase):
    def fixture(self):
        a = self.add("morning digest")
        self.run_rec(a, 1, "ok", 7200, False, summary="9 unread")
        b = self.add("repo watcher", "agent", "every 30m", account="work")
        self.run_rec(b, 1, "failed", 600, False, error="network")
        H.update_state(H.state_dir(b["id"]), lambda s: s.update(running={"run": 2, "pid": 1, "started": self.now - 40}))
        c = self.add("backup check", "task", "daily at 3", permissions="readonly")
        self.run_rec(c, 1, "limit", 3600, False)
        d = self.add("weekly report", "task", "monday 8am")
        H.update_state(H.state_dir(d["id"]), lambda s: s.update(paused_reason="5 runs in a row did not succeed"))
        e = self.add("release notes", "task", "tomorrow 10am")
        self.run_rec(e, 1, "ok", 86400, True)
        return a, b, c, d, e

    def test_rows_columns_and_order(self):
        self.fixture()
        self.alive()
        text = self.show()
        lines = text.splitlines()
        names = [n for n in ("backup check", "weekly report", "repo watcher", "morning digest", "release notes")
                 if n in text]
        self.assertEqual(len(names), 5)
        order = [next(i for i, l in enumerate(lines) if n in l) for n in names]
        self.assertEqual(set(order[:2]), {min(order), min(order) + 1}, "the two bots that need you come first")
        self.assertEqual(order[2:], sorted(order[2:]), "then running, done, waiting")
        row = {n: next(l for l in lines if n in l) for n in names}
        self.assertIn("⊘", row["backup check"])
        self.assertIn("● limit 1h", row["backup check"])
        self.assertIn("readonly", row["backup check"])
        self.assertIn("paused", row["weekly report"])
        self.assertIn("running 40s", row["repo watcher"])
        self.assertIn("◆ agent", row["repo watcher"])
        self.assertIn("every 30m", row["repo watcher"])
        self.assertIn("work", row["repo watcher"])
        self.assertIn("✓", row["morning digest"])
        self.assertIn("weekdays 09:00", row["morning digest"])
        self.assertIn("● ok 2h", row["morning digest"])
        self.assertIn("◷", row["release notes"])
        self.assertNotIn("●", row["release notes"], "a seen result is not highlighted")
        self.assertIn("5 bots · 1 running · 3 unseen results", text)
        self.assertIn("▪ task", row["morning digest"])

    def test_unseen_result_is_bold_and_coloured_seen_is_faded(self):
        a = self.add("one")
        self.run_rec(a, 1, "failed", 60, False, error="x")
        self.show()
        self.screen()
        cells = self.ui.cv.cells
        y = next(i for i, r in enumerate(cells) if "".join(c[0] for c in r).count("one"))
        x = "".join(c[0] for c in cells[y]).index("● failed")
        self.assertEqual((cells[y][x][1], cells[y][x][3] & U.BOLD), (self.ui.pal["red"], U.BOLD))
        H.mark_seen(a["id"])
        self.page.refresh()
        self.screen()
        self.assertNotEqual(self.ui.cv.cells[y][x + 2][1], self.ui.pal["red"])
        self.assertFalse(self.ui.cv.cells[y][x + 2][3] & U.BOLD)

    def test_scheduler_status_line(self):
        self.add("one")
        self.alive()
        self.assertIn("● Scheduler running", self.show())
        self.alive(False)
        self.page.refresh()
        text = self.screen()
        self.assertIn("○ Scheduler not running", text)
        self.assertIn("start it: hivegrid-scheduler", text)
        self.assertIn("Start", text)  # the button

    def test_start_command_names_the_unit_when_installed(self):
        none = P.scheduler_command({"HOME": self.tmp}, exists=lambda p: False)
        self.assertEqual(none[1], "hivegrid-scheduler")
        unit = os.path.join(self.tmp, ".config", "systemd", "user", P.SCHEDULER_UNIT)
        argv, text = P.scheduler_command({"HOME": self.tmp}, exists=lambda p: p == unit)
        self.assertEqual((argv, text), (["systemctl", "--user", "start", P.SCHEDULER_UNIT],
                                        "systemctl --user start hivegrid-scheduler"))

    def test_start_scheduler_runs_the_unit_or_detaches_the_program(self):
        ran = mock.Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
        ok, _ = P.start_scheduler({"HOME": self.tmp}, run=ran, exists=lambda p: p.endswith(P.SCHEDULER_UNIT))
        self.assertTrue(ok)
        self.assertEqual(ran.call_args[0][0][:3], ["systemctl", "--user", "start"])
        failed = mock.Mock(return_value=SimpleNamespace(returncode=5, stdout="", stderr="Unit not found.\n"))
        ok, msg = P.start_scheduler({"HOME": self.tmp}, run=failed, exists=lambda p: p.endswith(P.SCHEDULER_UNIT))
        self.assertFalse(ok)
        self.assertIn("Unit not found", msg)
        popen = mock.Mock()
        ok, _ = P.start_scheduler({"HOME": self.tmp}, popen=popen, exists=lambda p: False)
        self.assertTrue(ok)
        self.assertTrue(popen.call_args[1]["start_new_session"])

    def test_empty_state_explains_and_offers_new(self):
        text = self.show()
        self.assertIn("No bots yet.", text)
        self.assertIn("Press n to create one", text)

    def test_unreadable_store_is_an_error_not_a_crash(self):
        os.makedirs(os.path.dirname(self.store.path), exist_ok=True)
        with open(self.store.path, "w") as f:
            f.write("{nope")
        self.assertIn("The bots could not be read", self.show())

    def test_invalid_records_are_counted(self):
        self.add("good")
        with open(self.store.path) as f:
            doc = json.load(f)
        doc["schedules"].append({"id": "bad-one", "name": "bad", "when": {"type": "nope"}, "task": {"prompt": "x"}})
        with open(self.store.path, "w") as f:
            json.dump(doc, f)
        text = self.show()
        self.assertIn("1 schedule skipped: bad-one", text)

    def test_narrow_panel_drops_columns_not_the_name(self):
        self.add("a rather long bot name here")
        self.ui.size = (70, 14)
        text = self.show()
        row = next(l for l in text.splitlines() if "rather long" in l)
        self.assertIn("last result", text)
        self.assertNotIn("mode", text.splitlines()[4])
        self.assertIn("a rather long bot", row)

    def test_select_puts_the_cursor_on_the_bot(self):
        self.add("aaa")
        b = self.add("bbb", when="every 2h")
        self.page.select = b["id"]
        self.show()
        self.assertEqual(self.page.current()["id"], b["id"])


class WhenPreviewTest(BotsCase):
    def test_reading_shows_the_parse_and_next_three_times(self):
        m = P.WhenModal("t", "weekdays 9am", lambda t: None, now=lambda: NOW)
        lines = m.reading()
        self.assertEqual(lines[0], ("→ weekdays 09:00", "text"))
        self.assertEqual(lines[1][0], "next  09:00 · Tue 09:00 · Wed 09:00")

    def test_reading_accepts_cron_and_iso_and_reports_errors(self):
        m = P.WhenModal("t", "0 9 * * 1-5", lambda t: None, now=lambda: NOW)
        self.assertEqual(m.reading()[0][0], "→ weekdays 09:00")
        m.text = "2026-11-03T09:00"
        self.assertEqual(m.reading()[0][0], "→ once Nov 3 09:00")
        self.assertEqual(m.reading()[1][0], "next  Tue 09:00")
        m.text = "whenever"
        line, color = m.reading()[0]
        self.assertEqual(color, "amber")
        self.assertIn("try", line)
        m.text = ""
        self.assertEqual(m.reading()[0][1], "dim")

    def test_modal_renders_the_reading_inside_the_dialog(self):
        self.show()
        self.ui.open_modal(P.WhenModal("New bot · when", "every 30m", lambda t: None, now=lambda: NOW))
        text = self.screen()
        self.assertIn("→ every 30m", text)
        self.assertIn("next  ", text)


class WizardTest(BotsCase):
    def answers(self, name="nightly", kind=0, prompt="check the logs", when="weekdays 9am", cwd=None,
                account=0, perms=0, mode=0):
        self.press("n")
        self.type(name), self.press("Enter")
        for _ in range(kind):
            self.press("DownArrow")
        self.press("Enter")
        self.type(prompt), self.press("Enter")
        if kind == 1:
            self.press("Enter")  # identity: skipped
        self.type(when), self.press("Enter")
        if cwd is not None:
            for _ in range(40):
                self.press("Backspace")
            self.type(cwd)
        self.press("Enter")
        for _ in range(account):
            self.press("DownArrow")
        self.press("Enter")
        for _ in range(perms):
            self.press("DownArrow")
        self.press("Enter")

    def test_task_with_defaults(self):
        self.show()
        self.answers()
        self.assertEqual(len(self.store.read()), 0)
        self.press("Enter")  # mode: headless (default)
        (rec,) = self.store.read()
        self.assertEqual((rec["name"], rec["kind"], rec["task"]["prompt"]), ("nightly", "task", "check the logs"))
        self.assertEqual(rec["when"], {"type": "cron", "expr": "0 9 * * 1-5", "tz": "local"})
        self.assertEqual((rec["run"]["cwd"], rec["run"]["permissions"], rec["run"]["mode"], rec["run"]["account"]),
                         (f"{self.tmp}/work", "edit", "headless", None))
        self.assertEqual(rec["origin"], "panel")
        self.assertTrue(os.path.isdir(H.state_dir(rec["id"])), "the state directory exists for the scheduler")
        self.assertIsNone(self.ui.modal)
        self.assertEqual(self.page.current()["id"], rec["id"])

    def test_agent_has_identity_and_memory(self):
        self.show()
        self.press("n")
        self.type("Scout"), self.press("Enter")
        self.press("DownArrow", "Enter")
        self.type("watch CI"), self.press("Enter")
        self.type("You are Scout."), self.press("Enter")
        self.type("every 30m"), self.press("Enter")
        self.press("Enter", "Enter", "Enter", "Enter")  # folder, account, permissions, mode
        (rec,) = self.store.read()
        self.assertEqual((rec["kind"], rec["identity"]["system"], rec["identity"]["memory"]), ("agent", "You are Scout.", True))
        self.assertTrue(os.path.exists(H.memory_path(rec["id"])))

    def test_validation_errors_stay_in_the_dialog(self):
        self.show()
        self.press("n")
        self.press("Enter")
        self.assertIn("give the bot a name", self.ui.modal.error)
        self.type("x"), self.press("Enter")
        self.press("Enter")
        self.press("Enter")
        self.assertIn("write what the bot should do", self.ui.modal.error)
        self.type("go"), self.press("Enter")
        self.type("whenever"), self.press("Enter")
        self.assertIn("try", self.ui.modal.error)
        self.assertIsInstance(self.ui.modal, P.WhenModal)
        for _ in range(8):
            self.press("Backspace")
        self.type("in 5m"), self.press("Enter")
        self.assertIsInstance(self.ui.modal, U.PromptModal)
        # the folder must exist
        for _ in range(60):
            self.press("Backspace")
        self.type("/no/such/folder"), self.press("Enter")
        self.assertEqual(self.ui.modal.error, "that folder does not exist")
        for _ in range(30):
            self.press("Backspace")
        self.type("relative"), self.press("Enter")
        self.assertIn("absolute", self.ui.modal.error)
        self.assertEqual(self.store.read(), [], "nothing is written before the last answer")

    def test_escape_drops_the_bot(self):
        self.show()
        self.press("n")
        self.type("half"), self.press("Enter")
        self.press("Escape")
        self.assertIsNone(self.ui.modal)
        self.assertEqual(self.store.read(), [])

    def test_full_is_refused_without_the_setting(self):
        self.show()
        self.answers(perms=2)
        self.assertIsInstance(self.ui.modal, U.ChoiceModal, "asked again")
        self.assertIn("allow_full", self.ui.message)
        self.assertTrue(self.ui.message_error)
        self.assertEqual(self.store.read(), [])

    def test_full_needs_a_confirmation_a_time_and_a_cost(self):
        self.write_settings({"schedule": {"allow_full": True}})
        self.show()
        self.answers(perms=2)
        self.assertIsInstance(self.ui.modal, U.ConfirmModal)
        self.press("Enter")  # yes
        self.assertIn("Full needs a limit", self.ui.modal.hint)
        for _ in range(5):
            self.press("Backspace")
        self.type("5h"), self.press("Enter")
        self.assertEqual(self.ui.modal.text, "2")  # the cost keeps its default
        self.assertEqual(self.store.read(), [])
        for _ in range(3):
            self.press("Backspace")
        self.type("3.5"), self.press("Enter")
        self.press("Enter")  # mode
        (rec,) = self.store.read()
        self.assertEqual((rec["run"]["permissions"], rec["run"]["max_time_s"], rec["run"]["max_cost_usd"]),
                         ("full", 5 * 3600, 3.5))
        self.assertEqual(rec["origin"], "panel")

    def test_full_time_and_cost_are_checked(self):
        self.write_settings({"schedule": {"allow_full": True}})
        self.show()
        self.answers(perms=2)
        self.press("Enter")
        for _ in range(5):
            self.press("Backspace")
        self.type("2d"), self.press("Enter")
        self.assertIn("between 1 minute and 24 hours", self.ui.modal.error)
        for _ in range(3):
            self.press("Backspace")
        self.type("30m"), self.press("Enter")
        for _ in range(3):
            self.press("Backspace")
        self.type("500"), self.press("Enter")
        self.assertIn("at most 100", self.ui.modal.error)
        self.assertEqual(self.store.read(), [])

    def test_declining_full_goes_back_to_the_choice(self):
        self.write_settings({"schedule": {"allow_full": True}})
        self.show()
        self.answers(perms=2)
        self.press("Escape")
        self.assertIsNone(self.ui.modal)
        self.assertEqual(self.store.read(), [])

    def test_account_and_readonly_and_mode_are_stored(self):
        self.write_settings({"accounts": [{"label": "work", "dir": f"{self.tmp}/.claude-work", "profile": "work"}]})
        self.show()
        self.answers(account=1, perms=1)
        self.press("DownArrow", "Enter")  # visible
        (rec,) = self.store.read()
        self.assertEqual((rec["run"]["account"], rec["run"]["permissions"], rec["run"]["mode"]),
                         ("work", "readonly", "visible"))

    def test_folder_defaults_to_the_focused_panes_directory_and_profile(self):
        self.page.cwd, self.page.profile = f"{self.tmp}/work", "work"
        self.write_settings({"accounts": [{"label": "work", "dir": f"{self.tmp}/.claude-work", "profile": "work"}]})
        self.show()
        self.press("n")
        self.type("n"), self.press("Enter", "Enter")
        self.type("p"), self.press("Enter")
        self.type("daily 9"), self.press("Enter")
        self.assertEqual(self.ui.modal.text, f"{self.tmp}/work")
        self.press("Enter")
        self.assertEqual(self.ui.modal.options[self.ui.modal.idx], "work", "the account of the pane is preselected")

    def test_new_flag_opens_the_wizard_on_show(self):
        self.page.new = True
        self.show()
        self.assertIsInstance(self.ui.modal, U.PromptModal)
        self.assertEqual(self.ui.modal.title, "New bot · name")


class ActionTest(BotsCase):
    def setUp(self):
        super().setUp()
        self.rec = self.add("digest", when="weekdays 9am")
        self.sdir = H.state_dir(self.rec["id"])
        self.show()

    def test_space_pauses_and_resumes_and_clears_a_breaker_pause(self):
        self.press("Space")
        self.assertFalse(self.store.get(self.rec["id"])["enabled"])
        self.assertEqual(self.page.current()["state"], "idle")
        self.assertIn("paused digest", self.ui.message)
        H.update_state(self.sdir, lambda s: s.update(paused_reason="5 runs in a row did not succeed", consec_failures=5))
        self.now += 60
        self.press("Space")
        st = H.load_state(self.sdir)
        self.assertTrue(self.store.get(self.rec["id"])["enabled"])
        self.assertEqual((st["paused_reason"], st["consec_failures"], st["baseline"]), (None, 0, int(self.now)))
        # a breaker-paused (still enabled) bot resumes too
        H.update_state(self.sdir, lambda s: s.update(paused_reason="x"))
        self.page.refresh()
        self.press("Space")
        self.assertIsNone(H.load_state(self.sdir)["paused_reason"])

    def test_run_now_with_a_live_scheduler_sends_the_command(self):
        self.alive()
        self.page.refresh()
        self.press("r")
        self.assertEqual(self.commands(), [{"op": "run-now", "id": self.rec["id"]}])
        self.assertIn("asked the scheduler", self.ui.message)

    def test_run_now_without_a_scheduler_offers_to_start_it(self):
        self.press("r")
        self.assertIsInstance(self.ui.modal, U.ConfirmModal)
        self.assertIn("scheduler is not running", self.ui.modal.text)
        self.press("y")
        self.assertEqual(self.started, [1])
        self.assertTrue(H.load_state(self.sdir)["run_now"])
        self.assertEqual(self.commands(), [])

    def test_run_now_without_a_scheduler_can_wait_for_it(self):
        self.press("r")
        self.press("n")
        self.assertEqual(self.started, [])
        self.assertTrue(H.load_state(self.sdir)["run_now"])
        self.assertIn("as soon as the scheduler starts", self.ui.message)

    def test_skip_without_a_scheduler_marks_the_next_slot_handled(self):
        before = H.next_run(self.rec, H.load_state(self.sdir), self.now)
        self.press("s")
        st = H.load_state(self.sdir)
        self.assertEqual(st["last_slot"], before)
        self.assertGreater(H.next_run(self.rec, st, self.now), before)
        self.assertIn("skipped; next", self.ui.message)

    def test_skip_with_a_scheduler_sends_the_command(self):
        self.alive()
        self.page.refresh()
        self.press("s")
        self.assertEqual(self.commands(), [{"op": "skip", "id": self.rec["id"]}])
        self.assertIsNone(H.load_state(self.sdir)["last_slot"])

    def test_s_again_while_the_skip_is_ahead_brings_the_run_back(self):
        before = H.next_run(self.rec, H.load_state(self.sdir), self.now)
        self.press("s")
        self.assertEqual(H.load_state(self.sdir)["last_slot"], before)
        self.press("s")
        st = H.load_state(self.sdir)
        self.assertIsNone(st["last_slot"])
        self.assertEqual(H.next_run(self.rec, st, self.now), before)
        self.assertIn("skip undone", self.ui.message)

    def test_undoing_a_skip_with_a_scheduler_sends_the_command(self):
        H.update_state(self.sdir, lambda s: H.skip_next(self.rec, s, self.now))
        self.alive()
        self.page.refresh()
        self.press("s")
        self.assertEqual(self.commands(), [{"op": "unskip", "id": self.rec["id"]}])

    def test_cancel_needs_a_live_run_and_a_scheduler(self):
        self.press("c")
        self.assertIn("is not running", self.ui.message)
        H.update_state(self.sdir, lambda s: s.update(running={"run": 1, "pid": 1, "started": self.now - 5}))
        self.page.refresh()
        self.press("c")
        self.assertIn("scheduler is not running", self.ui.message)
        self.alive()
        self.page.refresh()
        self.press("c")
        self.assertEqual(self.commands(), [{"op": "cancel", "id": self.rec["id"]}])

    def test_delete_asks_then_removes_the_bot_and_its_state(self):
        self.press("d")
        self.assertIsInstance(self.ui.modal, U.ConfirmModal)
        self.assertIn("digest", self.ui.modal.text)
        self.press("n")
        self.assertEqual(len(self.store.read()), 1)
        self.press("d", "y")
        self.assertEqual(self.store.read(), [])
        self.assertFalse(os.path.exists(self.sdir))
        self.assertIn("deleted digest", self.ui.message)

    def test_changes_tell_a_live_scheduler_to_reload(self):
        self.alive()
        self.page.refresh()
        self.press("Space")
        self.assertIn({"op": "reload"}, self.commands())

    def test_detail_lists_runs_and_marks_them_seen(self):
        self.run_rec(self.rec, 1, "ok", 86400, True, summary="old one")
        self.run_rec(self.rec, 2, "failed", 3600, False, error="omp exited 1", summary="")
        self.run_rec(self.rec, 3, "ok", 600, False, summary="Found 3 mails that need an answer today.", cost_usd=1.5)
        self.page.refresh()
        self.assertEqual(self.page.current()["unseen"], 2)
        self.press("Enter")
        text = self.screen()
        self.assertEqual(self.page.detail, self.rec["id"])
        self.assertIn("digest", text)
        self.assertIn("#3", text)
        self.assertIn("● ok · 10m ago · took 1m", text)
        self.assertIn("$1.50", text)
        self.assertIn("Found 3 mails that need an answer today.", text)
        self.assertIn("● failed", text)
        self.assertIn("omp exited 1", text)
        self.assertEqual([r["seen"] for r in H.read_runs(self.sdir)], [True, True, True], "viewing marks them seen")
        self.assertEqual(H.unseen(self.sdir), [])
        self.press("Escape")
        self.assertIsNone(self.page.detail)
        self.assertIn("digest", self.screen())
        self.assertNotIn("●", [l for l in self.screen().splitlines() if "digest" in l][0])

    def test_detail_without_runs_says_so(self):
        self.press("Enter")
        self.assertIn("No runs yet.", self.screen())

    def test_detail_selects_runs_with_the_arrows(self):
        self.run_rec(self.rec, 1, "ok", 3600, True, summary="first summary")
        self.run_rec(self.rec, 2, "ok", 600, True, summary="second summary")
        self.page.refresh()
        self.press("Enter")
        def block():
            lines = self.screen().splitlines()
            rule = max(k for k, l in enumerate(lines) if l.strip().startswith("────"))
            return lines[rule + 1]
        self.assertIn("second summary", block())
        self.press("DownArrow")
        self.assertEqual(self.page.run_cursor, 1)
        self.assertEqual(self.page.runs[self.page.run_cursor]["run"], 1)
        self.assertIn("first summary", block())

    def test_help_lists_the_keys(self):
        self.press("?")
        text = self.screen()
        for key in ("new bot", "run now", "skip the next", "delete", "memory.md"):
            self.assertIn(key, text)
        self.press("x")
        self.assertIsNone(self.ui.modal)


class EditTest(BotsCase):
    def setUp(self):
        super().setUp()
        self.rec = self.add("digest", prompt="line one\nline two")
        self.show()

    def test_the_editor_gets_the_record_with_the_prompt_below_a_marker(self):
        self.press("e")
        argv, text = self.edits[0]
        self.assertEqual(argv[0], "fake-editor")
        head, _, tail = text.partition("\n" + P.EDIT_MARK + "\n")
        self.assertEqual(json.loads(head)["task"]["prompt"], "(see below)")
        self.assertEqual(tail, "line one\nline two\n")
        self.assertFalse(os.path.exists(os.path.dirname(argv[-1])), "the temp directory is removed")
        self.assertIn("no changes", self.ui.message)

    def test_a_valid_edit_is_saved_through_the_store(self):
        self.editor_do = sub(('"name": "digest"', '"name": "morning mail"'), ("line two", "line two\nline three"),
                             ('"permissions": "edit"', '"permissions": "readonly"'))
        self.now += 100
        self.press("e")
        rec = self.store.get(self.rec["id"])
        self.assertEqual((rec["name"], rec["run"]["permissions"], rec["task"]["prompt"]),
                         ("morning mail", "readonly", "line one\nline two\nline three"))
        self.assertEqual((rec["created"], rec["updated"]), (self.rec["created"], int(self.now)))
        self.assertEqual(self.page.current()["name"], "morning mail")
        self.assertIn("saved", self.ui.message)

    def test_invalid_json_is_not_saved_and_the_error_is_clear(self):
        self.editor_do = sub(('"kind": "task",', '"kind": "task"'))
        self.press("e")
        self.assertIsInstance(self.ui.modal, U.ConfirmModal)
        self.assertIn("not valid JSON", self.ui.modal.text)
        self.assertIn("line", self.ui.modal.text)
        self.assertEqual(self.store.get(self.rec["id"])["name"], "digest")

    def test_a_value_the_store_rejects_is_named(self):
        self.editor_do = sub(('"permissions": "edit"', '"permissions": "root"'))
        self.press("e")
        self.assertIn("run.permissions must be one of", self.ui.modal.text)
        self.assertEqual(self.store.get(self.rec["id"])["run"]["permissions"], "edit")

    def test_edit_again_reopens_the_same_text_and_discard_changes_nothing(self):
        self.editor_do = lambda path: put(path, "{broken")
        self.press("e")
        self.editor_do = lambda path: put(path, "{broken 2")
        self.press("Enter")  # Edit again
        self.assertEqual(len(self.edits), 2)
        self.assertEqual(self.edits[1][1], "{broken", "the second round starts from what was typed")
        self.assertIsInstance(self.ui.modal, U.ConfirmModal)
        self.press("n")
        self.assertIsNone(self.ui.modal)
        self.assertEqual(self.store.get(self.rec["id"])["name"], "digest")

    def test_losing_the_prompt_marker_is_refused(self):
        self.editor_do = sub((P.EDIT_MARK, ""))
        self.press("e")
        self.assertIn("marker line", self.ui.modal.text)
        self.assertEqual(self.store.get(self.rec["id"])["task"]["prompt"], "line one\nline two")

    def test_the_id_cannot_change(self):
        self.editor_do = sub((self.rec["id"], "other-id"))
        self.press("e")
        self.assertIn("the id cannot change", self.ui.modal.text)
        self.assertEqual([r["id"] for r in self.store.read()], [self.rec["id"]])

    def test_full_cannot_be_reached_through_the_editor_without_the_setting(self):
        self.editor_do = sub(('"permissions": "edit"', '"permissions": "full"'))
        self.press("e")
        self.assertIn("allow_full", self.ui.modal.text)
        self.assertEqual(self.store.get(self.rec["id"])["run"]["permissions"], "edit")

    def test_no_editor_is_said(self):
        with mock.patch.dict(os.environ, {"EDITOR": "", "VISUAL": ""}), mock.patch.object(P.shutil, "which", return_value=None):
            self.press("e")
        self.assertIn("No editor found", self.ui.message)

    def test_editor_failure_changes_nothing(self):
        self.page.editor = lambda argv: SimpleNamespace(returncode=2)
        self.press("e")
        self.assertIn("exited with status 2", self.ui.message)
        self.assertEqual(self.store.get(self.rec["id"])["name"], "digest")


class MemoryTest(BotsCase):
    def test_a_task_without_memory_says_so(self):
        self.add("plain")
        self.show()
        self.press("m")
        self.assertIn("has no memory", self.ui.message)
        self.assertEqual(self.edits, [])

    def test_an_agents_memory_is_edited_and_capped(self):
        rec = self.add("scout", "agent", "every 30m", prompt="watch")
        sched = rec
        put(H.memory_path(rec["id"]), "old fact\n")
        self.show()
        self.editor_do = lambda path: put(path, "old fact\n" + ("line of facts\n" * 1000))
        self.press("m")
        self.assertEqual(self.edits[0][1], "old fact\n")
        size = os.path.getsize(H.memory_path(rec["id"]))
        self.assertLessEqual(size, sched["identity"]["memory_max_bytes"])
        self.assertIn("cut to its cap", self.ui.message)

    def test_unchanged_memory_is_left_alone(self):
        rec = self.add("scout", "agent", "every 30m", prompt="watch")
        path = H.memory_path(rec["id"])
        put(path, "facts\n")
        stamp = os.stat(path).st_mtime_ns
        self.show()
        self.press("m")
        self.assertIn("memory unchanged", self.ui.message)
        self.assertEqual(os.stat(path).st_mtime_ns, stamp)


class TextTest(unittest.TestCase):
    def test_edit_text_round_trips(self):
        rec = H.validate({"id": "abc", "name": "n", "when": {"type": "cron", "expr": "0 9 * * *", "tz": "local"},
                          "task": {"prompt": "multi\nline\n\nprompt"}, "run": {"cwd": "/tmp"}})
        self.assertEqual(P.parse_edit_text(P.edit_text(rec)), rec)

    def test_span_and_clock_text(self):
        self.assertEqual([P.span_text(s) for s in (-5, 0, 30, 120, 3600, 7500, 40000, 90000, 3 * 86400 + 7200, 9 * 86400)],
                         ["now", "now", "<1m", "2m", "1h", "2h05m", "11h", "25h", "3d2h", "9d"])
        with mock.patch.dict(os.environ, {"TZ": "UTC"}):
            time.tzset()
            self.assertEqual(P.clock_text(NOW + 3600, now=NOW), "07:20")
            self.assertEqual(P.clock_text(NOW + 86400, now=NOW), "Tue 06:20")
            self.assertEqual(P.clock_text(NOW + 20 * 86400, now=NOW), "Nov 22 06:20")


class TwinTest(BotsCase):
    def test_new_panel_is_registered_last_and_the_dump_path_works(self):
        import subprocess
        out = subprocess.run([sys.executable, os.path.join(BIN, "hivegrid-panel"), "bots", "--dump", "100x12"],
                             capture_output=True, text=True, env=dict(os.environ), timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Bots", out.stdout.splitlines()[0])
        self.assertIn("No bots yet.", out.stdout)
        order = out.stdout.splitlines()[0].split()
        self.assertEqual(order[order.index("Settings"):][:6], ["Settings", "Usage", "Agents", "Monitor", "Hive", "Bots"])


if __name__ == "__main__":
    unittest.main()
