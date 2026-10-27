"""Settings core: settings.json normalisation, account discovery/merge/pairing, binding checks."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin")
sys.dont_write_bytecode = True  # bin/ is copied wholesale by install.sh: no __pycache__ there
sys.path.insert(0, BIN)
import hivegrid_settings_core as S  # noqa: E402


class TempHome(unittest.TestCase):
    """A fake HOME; nothing of the real ~/.claude* or ~/.omp is seen."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("HIVEGRID_SETTINGS", None)

    def claude_dir(self, name, creds=True):
        d = self.home / name
        d.mkdir()
        if creds:
            (d / ".credentials.json").write_text("{}")
        return d

    def profile(self, name):
        (self.home / ".omp" / "profiles" / name).mkdir(parents=True)

    def settings_file(self, data):
        path = self.home / ".config" / "hivegrid" / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        return path


class SettingsFileNormalisation(TempHome):
    def test_missing_file_gives_defaults_without_error(self):
        s = S.Settings(self.home / "nope.json")
        self.assertFalse(s.exists)
        self.assertIsNone(s.error)
        self.assertEqual(s.theme()["preset"], S.DEFAULT_PRESET)
        self.assertEqual(s.keys(), {n: S.normalize(d) for n, (_, d, _) in S.BINDINGS.items()})
        self.assertIsNone(s.stored_accounts())

    def test_invalid_json_is_reported_and_defaults_used(self):
        s = S.Settings(self.settings_file("{not json"))
        self.assertTrue(s.exists)
        self.assertIsNotNone(s.error)
        self.assertEqual(s.data, {})
        self.assertEqual(s.theme()["preset"], S.DEFAULT_PRESET)

    def test_non_object_top_level_is_an_error(self):
        s = S.Settings(self.settings_file("[1, 2]"))
        self.assertIsNotNone(s.error)
        self.assertEqual(s.data, {})

    def test_unknown_preset_and_bad_accent_fall_back(self):
        s = S.Settings(self.settings_file({"theme": {"preset": "neon", "accent": "#12"}}))
        t = s.theme()
        self.assertEqual(t["preset"], S.DEFAULT_PRESET)
        self.assertEqual(t["accent"], S.PRESETS[S.DEFAULT_PRESET]["palette"]["accent"])
        s = S.Settings(self.settings_file({"theme": "slate"}))
        self.assertEqual(s.theme()["preset"], S.DEFAULT_PRESET)

    def test_valid_accent_overrides_preset_and_is_lowercased(self):
        s = S.Settings(self.settings_file({"theme": {"preset": "forest", "accent": "#ABCDEF"}}))
        t = s.theme()
        self.assertEqual((t["preset"], t["accent"], t["palette"]["accent"]), ("forest", "#abcdef", "#abcdef"))
        # the preset's own palette is not mutated by the override
        self.assertNotEqual(S.PRESETS["forest"]["palette"]["accent"], "#abcdef")

    def test_invalid_key_strings_fall_back_to_their_default(self):
        s = S.Settings(self.settings_file({"keys": {"equalize": "HYPER+e", "arrange": "", "sidebar_files": "ctrl+x"}}))
        k = s.keys()
        self.assertEqual(k["equalize"], "CTRL+SHIFT+e")
        self.assertEqual(k["arrange"], "CTRL+SHIFT+a")
        self.assertEqual(k["sidebar_files"], "CTRL+x")  # valid: canonical spelling
        self.assertEqual(set(k), set(S.BINDINGS))
        s = S.Settings(self.settings_file({"keys": ["CTRL+x"]}))
        self.assertEqual(s.keys()["sidebar_files"], "CTRL+f")

    def test_stored_accounts_drop_malformed_entries_and_coerce_fields(self):
        s = S.Settings(self.settings_file({"accounts": [
            {"label": "main", "dir": "~/.claude", "profile": "alpha", "auto": 1},
            {"label": "x"},  # no dir
            {"dir": "~/.claude-y"},  # no label
            "garbage",
            {"label": "w", "dir": "~/w", "profile": "", "hidden": "yes"},
        ]}))
        self.assertEqual(s.stored_accounts(), [
            {"label": "main", "profile": "alpha", "dir": "~/.claude", "auto": True, "hidden": False},
            {"label": "w", "profile": None, "dir": "~/w", "auto": False, "hidden": True},
        ])
        self.assertIsNone(S.Settings(self.settings_file({"accounts": {"a": 1}})).stored_accounts())

    def test_write_keeps_unknown_fields_and_omits_default_keys(self):
        path = self.settings_file({"custom": {"x": 1}, "keys": {"equalize": "CTRL+ALT+e"}})
        s = S.Settings(path)
        keys = s.keys()
        s.write(s.theme(), keys, [], s.web())
        data = json.loads(path.read_text())
        self.assertEqual(data["custom"], {"x": 1})
        self.assertEqual(data["keys"], {"equalize": "CTRL+ALT+e"})
        self.assertEqual(data["version"], S.SCHEMA)
        self.assertEqual(S.Settings(path).keys(), keys)
        self.assertEqual([p.name for p in path.parent.iterdir()], ["settings.json"])  # no temp left

    def test_web_settings_default_and_stored_values(self):
        self.assertEqual(S.Settings(self.settings_file({})).web(),
                         {"links": "pane", "search": "", "profile": "main", "home": "about:blank", "browser": "auto"})
        s = S.Settings(self.settings_file({"web": {"links": "system", "search": "https://x.test/?q=%s", "profile": "work.2",
                                                    "home": "https://start.test/", "browser": "/usr/bin/brave-browser"}}))
        self.assertEqual(s.web(), {"links": "system", "search": "https://x.test/?q=%s", "profile": "work.2",
                                   "home": "https://start.test/", "browser": "/usr/bin/brave-browser"})
        self.assertEqual(s.effective()["web"], s.web())

    def test_invalid_web_settings_fall_back_per_field(self):
        s = S.Settings(self.settings_file({"web": {"links": "both", "search": "https://x.test/", "profile": "../evil"}}))
        self.assertEqual(s.web(), S.WEB_DEFAULTS)
        for stored in ("text", 5, ["pane"], None):
            self.assertEqual(S.Settings(self.settings_file({"web": stored})).web(), S.WEB_DEFAULTS, repr(stored))
        s = S.Settings(self.settings_file({"web": {"links": "system", "profile": 7, "search": ["x"]}}))
        self.assertEqual(s.web(), dict(S.WEB_DEFAULTS, links="system"))

    def test_a_stored_home_must_be_an_address_and_a_stored_browser_a_path(self):
        for home in ("javascript:alert(1)", "two words", "", 5, ["https://x.test"]):
            with self.subTest(home=home):
                self.assertEqual(S.Settings(self.settings_file({"web": {"home": home}})).web()["home"], "about:blank")
        for browser in ("chromium", "~/chrome", "", 7, None, ["/usr/bin/chromium"]):
            with self.subTest(browser=browser):
                self.assertEqual(S.Settings(self.settings_file({"web": {"browser": browser}})).web()["browser"], "auto")
        self.assertEqual(S.Settings(self.settings_file({"web": {"browser": " /usr/bin/chromium "}})).web()["browser"],
                         "/usr/bin/chromium")

    def test_the_browser_and_home_are_written_only_when_they_differ_from_the_defaults(self):
        path = self.settings_file({})
        s = S.Settings(path)
        web = dict(s.web(), browser="/usr/bin/vivaldi", home="https://start.test/")
        s.write(s.theme(), s.keys(), [], web)
        self.assertEqual(json.loads(path.read_text())["web"], {"browser": "/usr/bin/vivaldi", "home": "https://start.test/"})
        s.write(s.theme(), s.keys(), [], dict(web, browser="auto", home="about:blank"))
        self.assertNotIn("web", json.loads(path.read_text()))

    def test_a_stored_search_template_must_pass_valid_search(self):
        for bad in ("javascript:alert(%s)", "chrome://settings/?q=%s", "ftp://x.test/%s", "x.test/?q=%s"):
            with self.subTest(bad=bad):
                self.assertIsNotNone(S.valid_search(bad))
                self.assertEqual(S.Settings(self.settings_file({"web": {"search": bad}})).web()["search"], "")

    def test_web_validators(self):
        self.assertIsNone(S.valid_profile("main"))
        self.assertIsNone(S.valid_profile("a" * 32))
        for bad in ("", "a" * 33, "-x", ".x", "a b", "a/b", "ä"):
            self.assertTrue(S.valid_profile(bad), repr(bad))
        self.assertIsNone(S.valid_search(""))
        self.assertIsNone(S.valid_search("https://duck.example/?q=%s"))
        self.assertIsNone(S.valid_search("http://localhost:8080/s?q=%s"))
        for bad in ("https://x.test/", "x.test/?q=%s", "ftp://x/%s"):
            self.assertTrue(S.valid_search(bad), bad)

    def make_exe(self, relative, text="#!/bin/sh\necho 'Chromium 120.0.1'\n"):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o755)
        return str(path)

    def setUp(self):
        super().setUp()
        # The /opt/google/chrome/chrome of the machine running the tests is not part of these.
        families = [(label, tuple(p for p in places if not os.path.isabs(p))) for label, places in S.BROWSER_FAMILIES]
        patcher = mock.patch.object(S, "BROWSER_FAMILIES", tuple(families))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_detected_browsers_are_one_per_product_in_the_order_of_the_families(self):
        bin1, bin2 = self.home / "bin1", self.home / "bin2"
        brave = self.make_exe("bin1/brave-browser")
        self.make_exe("bin1/chromium-browser")
        chromium = self.make_exe("bin2/chromium")
        self.make_exe("bin2/vivaldi-stable")
        self.make_exe("bin2/firefox")
        self.make_exe("bin2/notes.txt")
        (bin2 / "microsoft-edge").write_text("not executable")
        found, skipped = S.detect_browsers(os.pathsep.join(["", str(bin1), str(bin2)]))
        paths = dict(found)
        self.assertEqual(paths["Brave"], brave)
        self.assertEqual(paths["Chromium"], chromium, "names are tried in order, `chromium` before `chromium-browser`")
        self.assertEqual(paths["Vivaldi"], str(bin2 / "vivaldi-stable"))
        self.assertNotIn("Microsoft Edge", paths, "a file that is not executable is not a browser")
        self.assertFalse([p for _, p in found if "firefox" in p], "Firefox is never offered")
        self.assertEqual(skipped, [])
        self.assertEqual([label for label, _ in found], [l for l, _ in S.BROWSER_FAMILIES if l in paths], "family order")

    def test_snap_and_flatpak_browsers_are_skipped_with_a_reason(self):
        snap_stub = self.make_exe("bin/chromium-browser", "#!/bin/sh\necho 'Command requires the chromium snap to be installed' >&2\nexec snap install chromium\n")
        flatpak = self.make_exe("bin/brave-browser", "#!/bin/sh\nexec flatpak run --branch=stable com.brave.Browser \"$@\"\n")
        real = self.make_exe("bin/vivaldi", "#!/bin/sh\nexec /opt/vivaldi/vivaldi \"$@\"\n")
        link = self.home / "bin" / "microsoft-edge"
        link.symlink_to("/usr/bin/snap")
        found, skipped = S.detect_browsers(str(self.home / "bin"))
        self.assertEqual([p for _, p in found], [real])
        self.assertEqual(sorted(p for p, _ in skipped), sorted([snap_stub, flatpak, str(link)]))
        self.assertTrue(all(why == S.CONFINED_WHY for _, why in skipped))
        self.assertEqual(S.confined_browser("/snap/chromium/current/usr/lib/chromium-browser/chrome"), S.CONFINED_WHY)
        self.assertEqual(S.confined_browser(str(self.home / ".local/share/flatpak/exports/bin/org.x")), S.CONFINED_WHY)
        self.assertIsNone(S.confined_browser(real))

    def test_a_custom_browser_must_exist_be_executable_and_answer_version(self):
        ok = self.make_exe("my/chrome")
        error, path, version = S.check_browser(ok)
        self.assertEqual((error, path, version), (None, ok, "Chromium 120.0.1"))
        self.assertEqual(S.check_browser("~/my/chrome")[1], ok, "a leading ~ is the home directory")
        cases = {
            "nothing typed": "",
            "relative": "my/chrome",
            "missing": str(self.home / "gone"),
            "a directory": str(self.home / "my"),
            "not executable": self.make_exe("my/plain") and (self.home / "my" / "plain").chmod(0o644) or str(self.home / "my" / "plain"),
            "version fails": self.make_exe("my/broken", "#!/bin/sh\nexit 3\n"),
            "version prints nothing": self.make_exe("my/silent", "#!/bin/sh\nexit 0\n"),
            "not runnable": self.make_exe("my/nobin", "no shebang, not a binary"),
            "firefox": self.make_exe("my/ff", "#!/bin/sh\necho 'Mozilla Firefox 130.0'\n"),
            "firefox by name": self.make_exe("my/firefox"),
            "snap": self.make_exe("my/snapped", "#!/bin/sh\nexec snap run chromium \"$@\"\n"),
        }
        for label, text in cases.items():
            with self.subTest(case=label):
                error, path, version = S.check_browser(text)
                self.assertTrue(error, "an error text")
                self.assertEqual((path, version), (None, None))
        self.assertIn("Firefox", S.check_browser(cases["firefox"])[0])
        self.assertIn("snap", S.check_browser(cases["snap"])[0])

    def test_a_browser_that_hangs_on_version_is_refused_not_waited_for(self):
        def hang(*args, **kwargs):
            raise S.subprocess.TimeoutExpired(args[0], kwargs["timeout"])
        error, path, _ = S.check_browser(self.make_exe("my/slow"), run=hang)
        self.assertEqual(path, None)
        self.assertIn("did not answer", error)

    def test_web_leader_is_a_leader_binding_with_the_documented_default(self):
        self.assertEqual(S.Settings(self.settings_file({})).keys()["web_leader"], "LEADER+w")
        keys = S.Settings(self.settings_file({"keys": {"web_leader": "LEADER+x"}})).keys()
        self.assertEqual(keys["web_leader"], "LEADER+x")
        self.assertTrue([e for e in S.check_binding("web_leader", "LEADER+o", keys) if e[0] == "error"], "overview's key")
        self.assertEqual(S.check_binding("web_leader", "LEADER+x", keys), [])

    def test_non_string_key_values_fall_back_instead_of_crashing(self):
        s = S.Settings(self.settings_file({"keys": {"equalize": 5, "arrange": True, "settings": ["x"],
                                                    "sidebar_git": None}}))
        k = s.keys()
        self.assertEqual((k["equalize"], k["arrange"], k["settings"], k["sidebar_git"]),
                         ("CTRL+SHIFT+e", "CTRL+SHIFT+a", "LEADER+,", "CTRL+g"))

    def test_theme_watch_reports_only_palette_changes(self):
        path = self.settings_file({"theme": {"preset": "slate"}})
        w = S.ThemeWatch(path)
        first = w.poll()
        self.assertEqual(first["base"], S.PRESETS["slate"]["palette"]["base"])
        self.assertIsNone(w.poll())
        self.settings_file({"theme": {"preset": "slate"}, "keys": {"equalize": "CTRL+ALT+e"}})
        self.assertIsNone(w.poll())  # file changed, palette did not
        self.settings_file({"theme": {"preset": "forest", "accent": "#123456"}})
        changed = w.poll()
        self.assertEqual((changed["base"], changed["accent"]), (S.PRESETS["forest"]["palette"]["base"], "#123456"))
        path.unlink()
        self.assertEqual(w.poll(), S.PRESETS[S.DEFAULT_PRESET]["palette"])  # deleted file: the default

    def test_newer_schema_is_never_overwritten(self):
        path = self.settings_file({"version": S.SCHEMA + 1, "keys": {"equalize": "CTRL+ALT+e"}})
        before = path.read_bytes()
        m = S.Model(S.Settings(path))
        m.theme["preset"] = "slate"
        ok, _ = m.save()
        self.assertFalse(ok)
        self.assertEqual(path.read_bytes(), before)

    def test_model_dirty_tracks_edits_and_save_clears_it(self):
        path = self.home / "s.json"
        m = S.Model(S.Settings(path))
        self.assertFalse(m.dirty)
        m.keys["equalize"] = "CTRL+ALT+e"
        self.assertTrue(m.dirty)
        ok, _ = m.save()
        self.assertTrue(ok)
        self.assertFalse(m.dirty)
        self.assertEqual(S.Settings(path).keys()["equalize"], "CTRL+ALT+e")


class AccountDiscovery(TempHome):
    def test_discovery_labels_pairs_and_orders(self):
        for name in (".claude-zeta", ".claude-private", ".claude", ".claude-2", ".claude-work"):
            self.claude_dir(name)
        self.claude_dir(".claude-nocreds", creds=False)
        self.claude_dir(".claude-")  # no name after the dash
        (self.home / ".claude-file").write_text("not a dir")
        self.profile("work")
        self.profile("main")
        self.profile("alpha")  # no account of that name
        found = S.discover_accounts()
        self.assertEqual([(a["label"], a["profile"], a["dir"]) for a in found], [
            ("main", "main", "~/.claude"),  # main first, then by name
            ("2", None, "~/.claude-2"),
            ("private", None, "~/.claude-private"),
            ("work", "work", "~/.claude-work"),  # the profile of the same name exists
            ("zeta", None, "~/.claude-zeta"),  # no such profile: unpaired
        ])
        self.assertTrue(all(a["auto"] and not a["hidden"] for a in found))

    def test_without_profile_directories_nothing_pairs(self):
        self.claude_dir(".claude")
        self.claude_dir(".claude-2")
        self.assertEqual([a["profile"] for a in S.discover_accounts()], [None, None])

    def test_pairing_needs_a_profile_of_the_same_name(self):
        self.profile("2")
        self.assertEqual(S.pair_profile("2", S.omp_profiles()), "2")
        self.assertIsNone(S.pair_profile("main", S.omp_profiles()))


class MergeAccounts(TempHome):
    def test_rescan_keeps_manual_entries_and_drops_vanished_auto_ones(self):
        self.claude_dir(".claude")
        existing = [
            {"label": "main", "profile": "alpha", "dir": "~/.claude", "auto": True, "hidden": False},
            {"label": "gone", "profile": None, "dir": "~/.claude-gone", "auto": True, "hidden": False},
            {"label": "mine", "profile": "alpha", "dir": "~/elsewhere", "auto": False, "hidden": False},
        ]
        merged = S.merge_accounts(existing, S.discover_accounts())
        self.assertEqual([a["label"] for a in merged], ["main", "mine"])

    def test_edits_to_auto_entries_survive_and_are_not_duplicated(self):
        self.claude_dir(".claude-2")
        existing = [{"label": "work2", "profile": "custom", "dir": "~/.claude-2", "auto": True, "hidden": True}]
        merged = S.merge_accounts(existing, S.discover_accounts())
        self.assertEqual(merged, existing)
        self.assertIsNot(merged[0], existing[0])  # copies: the caller's list is not aliased

    def test_newly_found_directories_are_appended(self):
        self.claude_dir(".claude")
        self.claude_dir(".claude-new")
        existing = [{"label": "main", "profile": "alpha", "dir": "~/.claude", "auto": True, "hidden": False}]
        merged = S.merge_accounts(existing, S.discover_accounts())
        self.assertEqual([(a["label"], a["auto"]) for a in merged], [("main", True), ("new", True)])

    def test_no_stored_list_means_the_discovered_one(self):
        self.claude_dir(".claude")
        self.assertEqual(S.merge_accounts(None, S.discover_accounts()), S.discover_accounts())

    def test_settings_accounts_merges_file_with_discovery(self):
        self.claude_dir(".claude")
        self.claude_dir(".claude-x")
        path = self.settings_file({"accounts": [
            {"label": "boss", "profile": "alpha", "dir": "~/.claude", "auto": True},
            {"label": "manual", "dir": "/opt/claude", "auto": False},
        ]})
        self.assertEqual([a["label"] for a in S.Settings(path).accounts()], ["boss", "manual", "x"])


class RemoveAccount(TempHome):
    def test_auto_entry_is_hidden_not_deleted_and_stays_hidden_after_rescan(self):
        self.claude_dir(".claude")
        accts = S.merge_accounts(None, S.discover_accounts())
        S.remove_account(accts, 0)
        self.assertEqual(len(accts), 1)
        self.assertTrue(accts[0]["hidden"])
        merged = S.merge_accounts(accts, S.discover_accounts())
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0]["hidden"])

    def test_manual_entry_on_a_directory_with_credentials_is_hidden(self):
        self.claude_dir(".claude-m")
        accts = [{"label": "m", "profile": None, "dir": "~/.claude-m", "auto": False, "hidden": False}]
        S.remove_account(accts, 0)
        self.assertEqual(len(accts), 1)
        self.assertTrue(accts[0]["hidden"])
        self.assertEqual(len(S.merge_accounts(accts, S.discover_accounts())), 1)

    def test_manual_entry_without_credentials_is_deleted(self):
        accts = [{"label": "a", "profile": None, "dir": "~/a", "auto": False, "hidden": False},
                 {"label": "b", "profile": None, "dir": "~/b", "auto": False, "hidden": False}]
        S.remove_account(accts, 0)
        self.assertEqual([a["label"] for a in accts], ["b"])


class Bindings(unittest.TestCase):
    KEYS = S.effective_keys({})

    def errors(self, name, combo, keys=None, wez=None):
        return [t for kind, t in S.check_binding(name, combo, keys or self.KEYS, wez) if kind == "error"]

    def test_own_current_binding_is_no_conflict(self):
        for name, value in self.KEYS.items():
            self.assertEqual(S.check_binding(name, value, self.KEYS), [], name)

    def test_same_chord_as_another_setting_refuses(self):
        self.assertTrue(self.errors("arrange", "CTRL+SHIFT+e"))
        # letter case: CTRL+E is CTRL+SHIFT+e
        self.assertTrue(self.errors("arrange", "CTRL+E"))
        # shifted symbol: CTRL+| is the same chord as CTRL+SHIFT+|
        self.assertTrue(self.errors("equalize", "CTRL+|"))
        self.assertTrue(self.errors("quota_toggle", "LEADER+o"))
        # a fresh chord is fine
        self.assertEqual(self.errors("arrange", "CTRL+ALT+a"), [])

    def test_conflict_is_against_current_values_not_defaults(self):
        keys = dict(self.KEYS, equalize="CTRL+ALT+e")
        self.assertEqual(self.errors("arrange", "CTRL+SHIFT+e", keys), [])
        self.assertTrue(self.errors("arrange", "CTRL+ALT+e", keys))

    def test_fixed_bindings_refuse(self):
        self.assertTrue(self.errors("quota_popup", "ALT+h"))
        self.assertTrue(self.errors("quota_popup", "CTRL+v"))
        self.assertTrue(self.errors("hive_panel", "LEADER+n"))
        self.assertTrue(self.errors("equalize", "CTRL+RightArrow"))

    def test_kind_rules(self):
        self.assertTrue(self.errors("sidebar_toggle", "CTRL+x"))  # leader binding needs LEADER+
        self.assertTrue(self.errors("sidebar_toggle", "LEADER+CTRL+x"))
        self.assertTrue(self.errors("equalize", "LEADER+x"))  # combo binding must not use LEADER
        self.assertTrue(self.errors("equalize", "x"))  # bare key swallows typing
        self.assertTrue(self.errors("leader", "SHIFT+x"))
        self.assertEqual(self.errors("leader", "ALT+x"), [])
        self.assertTrue(self.errors("equalize", "HYPER+x"))
        self.assertTrue(self.errors("equalize", "CTRL+"))
        self.assertTrue(self.errors("equalize", ""))

    SHOW_KEYS = (
        "Default key table\n-----------------\n\n"
        "\tALT                  Enter              ->   ToggleFullScreen\n"
        "\tSHIFT | CTRL         t                  ->   SpawnTab(CurrentPaneDomain)\n"
        "\tSUPER                c                  ->   CopyTo(Clipboard)\n"
        "\n"
        "Key Table: copy_mode\n---------------------\n\n"
        "\tCTRL                 b                  ->   CopyMode(PageUp)\n"
    )

    def test_wezterm_defaults_only_warn(self):
        wez = S.parse_show_keys(self.SHOW_KEYS)
        self.assertIn(S.combo_id({"CTRL", "SHIFT"}, "t"), wez)
        self.assertNotIn(S.combo_id({"CTRL"}, "b"), wez)  # later key tables are not defaults
        out = S.check_binding("quota_popup", "ALT+Enter", self.KEYS, wez)
        self.assertEqual([k for k, _ in out], ["warn"])
        out = S.check_binding("arrange", "CTRL+T", self.KEYS, wez)  # CTRL+T == CTRL+SHIFT+t
        self.assertEqual([k for k, _ in out], ["warn"])
        # a fixed binding that is also a WezTerm default still refuses
        out = S.check_binding("quota_popup", "ALT+h", self.KEYS, {S.combo_id({"ALT"}, "h"): "X"})
        self.assertIn("error", [k for k, _ in out])


class ComboRoundTrip(unittest.TestCase):
    def test_defaults_round_trip(self):
        for name, (_, default, _) in S.BINDINGS.items():
            mods, key = S.parse_combo(default)
            text = S.format_combo(mods, key)
            self.assertEqual(S.parse_combo(text), (mods, key), name)
            self.assertEqual(S.normalize(text), text, name)

    def test_canonical_spelling(self):
        cases = {
            "shift+ctrl+a": "CTRL+SHIFT+a",
            "control+opt+x": "CTRL+ALT+x",
            "cmd+k": "SUPER+k",
            "ctrl+space": "CTRL+Space",
            "alt+pageup": "ALT+PageUp",
            "ctrl+f5": "CTRL+F5",
            "CTRL++": "CTRL++",
            "leader+,": "LEADER+,",
            " CTRL+SHIFT+_ ": "CTRL+SHIFT+_",
        }
        for raw, want in cases.items():
            self.assertEqual(S.normalize(raw), want, raw)
            self.assertEqual(S.normalize(want), want, raw)

    def test_rejects_unknown_keys_and_modifiers(self):
        for bad in ("", "   ", "CTRL+foo", "HYPER+a", "CTRL+F123", "CTRL+ "):
            with self.assertRaises(ValueError, msg=bad):
                S.parse_combo(bad)


if __name__ == "__main__":
    unittest.main()
