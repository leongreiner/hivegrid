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
        s.write(s.theme(), keys, [])
        data = json.loads(path.read_text())
        self.assertEqual(data["custom"], {"x": 1})
        self.assertEqual(data["keys"], {"equalize": "CTRL+ALT+e"})
        self.assertEqual(data["version"], S.SCHEMA)
        self.assertEqual(S.Settings(path).keys(), keys)
        self.assertEqual([p.name for p in path.parent.iterdir()], ["settings.json"])  # no temp left

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
