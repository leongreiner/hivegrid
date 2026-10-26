"""bin/omp-grid builds its layouts through `wezterm cli`; a fake `wezterm` first on PATH records
every call, so the split sequence, the pane sizes it produces and the text sent to each pane are
checked without a terminal."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SCRIPT = os.path.join(ROOT, "bin", "omp-grid")
sys.dont_write_bytecode = True

# Fake wezterm: appends argv (fields separated by \x1f) plus, for send-text, \x1e and its stdin;
# records end in \x1d. split-pane / spawn print fresh pane ids 101, 102, ...; FAKE_WEZ_FAIL names a
# subcommand that fails instead.
FAKE = r"""#!/bin/sh
log=$FAKE_WEZ_LOG
for a; do printf '%s\037' "$a"; done >> "$log"
if [ "$2" = send-text ]; then printf '\036' >> "$log"; cat >> "$log"; fi
printf '\035' >> "$log"
case $2 in
  split-pane|spawn)
    [ "$2" = "$FAKE_WEZ_FAIL" ] && exit 1
    n=$(cat "$log.n" 2>/dev/null || echo 100); n=$((n + 1)); echo $n > "$log.n"; echo $n ;;
esac
"""

BASE = "7"  # the WEZTERM_PANE the script runs in


class Call:
    def __init__(self, rec):
        head, _, self.stdin = rec.partition("\x1e")
        self.argv = head.split("\x1f")[:-1]

    def opt(self, name):
        return self.argv[self.argv.index(name) + 1]

    @property
    def sub(self):
        return self.argv[1]


class OmpGridTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        fake = os.path.join(self.tmp, "wezterm")
        with open(fake, "w") as f:
            f.write(FAKE)
        os.chmod(fake, 0o755)
        self.log = os.path.join(self.tmp, "log")
        self.work = os.path.join(self.tmp, "work")
        os.mkdir(self.work)

    def run_grid(self, *args, pane=BASE, fail=None):
        env = {"PATH": self.tmp + os.pathsep + "/usr/bin:/bin", "HOME": self.tmp, "FAKE_WEZ_LOG": self.log}
        if pane is not None:
            env["WEZTERM_PANE"] = pane
        if fail:
            env["FAKE_WEZ_FAIL"] = fail
        r = subprocess.run(["bash", SCRIPT, *args], env=env, cwd=self.work, capture_output=True, text=True, timeout=10)
        try:
            with open(self.log) as f:
                recs = f.read().split("\x1d")[:-1]
        except FileNotFoundError:
            recs = []
        self.assertTrue(all(Call(rec).argv[0] == "cli" for rec in recs))
        return r, [Call(rec) for rec in recs]

    @staticmethod
    def sizes(calls, base):
        """Simulates the splits: {pane: (width, height)} as fractions of the starting pane."""
        size = {base: (1.0, 1.0)}
        made = 0  # panes created so far; the fake numbers them 101, 102, ...
        for c in calls:
            if c.sub == "spawn":
                made += 1
            if c.sub != "split-pane":
                continue
            made += 1
            parent, pct = c.opt("--pane-id"), int(c.opt("--percent")) / 100
            w, h = size[parent]
            new = str(100 + made)
            if "--right" in c.argv:
                size[parent], size[new] = (w * (1 - pct), h), (w * pct, h)
            else:
                size[parent], size[new] = (w, h * (1 - pct)), (w, h * pct)
        return size

    def sent(self, calls):
        return [(c.opt("--pane-id"), c.stdin) for c in calls if c.sub == "send-text"]

    def landed(self, calls):
        """[(pane, directory its command runs in)] in send order: a pane starts in the --cwd it was
        created with (the current pane in the script's own directory), then the shell runs the
        sent line, with the command stubbed to print the directory."""
        start, made = {}, 0
        for c in calls:
            if c.sub in ("spawn", "split-pane"):
                made += 1
                start[str(100 + made)] = c.opt("--cwd")
        out = []
        for pane, text in self.sent(calls):
            r = subprocess.run(["bash", "-c", 'omp() { pwd; }; claude() { pwd; }; cd "$1" && eval "$2"', "_",
                                start.get(pane, self.work), text], capture_output=True, text=True, timeout=5)
            out.append((pane, r.stdout.strip().rsplit("\n", 1)[-1]))
        return out

    def assertAllNear(self, values, target, tol=0.02):
        for v in values:
            self.assertAlmostEqual(v, target, delta=tol)

    # ---- layouts ------------------------------------------------------------------------

    def test_positional_grid_makes_equal_cells_in_row_order(self):
        d = os.path.join(self.tmp, "proj")
        os.mkdir(d)
        r, calls = self.run_grid("2", "3", d)
        self.assertEqual(r.returncode, 0, r.stderr)
        splits = [c for c in calls if c.sub == "split-pane"]
        self.assertEqual(len(splits), 5)  # 6 panes from the current one
        size = self.sizes(calls, BASE)
        self.assertEqual(len(size), 6)
        self.assertAllNear([w for w, _ in size.values()], 1 / 3)
        self.assertAllNear([h for _, h in size.values()], 1 / 2)
        # every pane gets the command once, row by row: the row head first, then its columns
        sent = self.sent(calls)
        self.assertEqual([p for p, _ in sent], [BASE, "102", "103", "101", "104", "105"])
        self.assertTrue(all(text.endswith("omp\n") for _, text in sent))
        self.assertEqual({dd for _, dd in self.landed(calls)}, {d})
        # text is sent only after the layout is complete
        self.assertTrue(all(c.sub == "split-pane" for c in calls[:5]))

    def test_layout_grid_takes_rows_and_cols(self):
        r, calls = self.run_grid("--layout", "grid", "2", "3")
        self.assertEqual(r.returncode, 0, r.stderr)
        size = self.sizes(calls, BASE)
        self.assertEqual(len(size), 6)
        self.assertAllNear([w for w, _ in size.values()], 1 / 3)

    def test_main_side_gives_main_60_percent_and_stacks_n_equal_side_panes(self):
        r, calls = self.run_grid("--layout", "main-side", "3")
        self.assertEqual(r.returncode, 0, r.stderr)
        size = self.sizes(calls, BASE)
        self.assertEqual(len(size), 4)
        self.assertAlmostEqual(size[BASE][0], 0.6, delta=0.01)
        self.assertEqual(size[BASE][1], 1.0)
        side = [s for p, s in size.items() if p != BASE]
        self.assertAllNear([w for w, _ in side], 0.4, 0.01)
        self.assertAllNear([h for _, h in side], 1 / 3)
        self.assertEqual([p for p, _ in self.sent(calls)], [BASE, "101", "102", "103"])

    def test_main_side_defaults_to_two_side_panes(self):
        r, calls = self.run_grid("--layout", "main-side")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.sent(calls)), 3)

    def test_columns_in_new_tab_cycles_dirs(self):
        for name in ("a", "b"):
            os.mkdir(os.path.join(self.work, name))
        r, calls = self.run_grid("--layout", "columns", "4", "--tab", "--dirs", "a,b", pane=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls[0].sub, "spawn")
        splits = [c for c in calls if c.sub == "split-pane"]
        self.assertFalse(any("--bottom" in c.argv for c in splits), "columns must not split rows")
        size = self.sizes(calls, "101")
        self.assertEqual(len(size), 4)
        self.assertAllNear([w for w, _ in size.values()], 1 / 4)
        a, b = (os.path.join(self.work, n) for n in ("a", "b"))
        self.assertEqual(self.landed(calls), [("101", a), ("102", b), ("103", a), ("104", b)])

    def test_missing_dir_is_refused_before_any_pane_is_made(self):
        r, calls = self.run_grid("--dirs", "nope", "1", "2")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(calls, [])

    def test_tab_does_not_touch_the_current_pane(self):
        r, calls = self.run_grid("--tab", "1", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(BASE, [c.opt("--pane-id") for c in calls if "--pane-id" in c.argv])

    def test_cmd_replaces_omp_and_dir_with_spaces_is_quoted(self):
        d = os.path.join(self.tmp, "my dir")
        os.mkdir(d)
        r, calls = self.run_grid("--cmd", "claude --resume", "--dir", d, "1", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c for c in calls if c.sub == "split-pane"], [])
        sent = self.sent(calls)
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0][1].endswith("claude --resume\n"), sent[0][1])
        self.assertNotIn("omp", sent[0][1])
        # the current pane's shell must end up in the directory despite the space
        self.assertEqual(self.landed(calls), [(BASE, d)])

    def test_name_panes_titles_each_pane_in_order(self):
        r, calls = self.run_grid("--name-panes", "1", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        sent = self.sent(calls)
        self.assertEqual(len(sent), 2)
        for i, (_, text) in enumerate(sent, 1):
            title = subprocess.run(["bash", "-c", text.split("; ")[0]], capture_output=True).stdout
            self.assertEqual(title, f"\x1b]2;agent-{i}\x07".encode())

    # ---- errors -------------------------------------------------------------------------

    def assertRefused(self, r, calls, code=2):
        self.assertEqual(r.returncode, code, r.stderr)
        self.assertEqual(calls, [], "no pane may be split or typed into after a usage error")

    def test_zero_or_non_numeric_dimensions_are_refused(self):
        for args in (["0", "2"], ["2", "x"], ["--layout", "columns", "0"], ["--rows", "-1"]):
            with self.subTest(args=args):
                r, calls = self.run_grid(*args)
                self.assertRefused(r, calls)
                self.assertIn("positive integer", r.stderr)

    def test_unknown_option_prints_usage_to_stderr(self):
        r, calls = self.run_grid("--bogus")
        self.assertRefused(r, calls)
        self.assertIn("Usage:", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_unknown_layout_and_missing_values_are_refused(self):
        for args in (["--layout", "spiral"], ["--layout"], ["--cmd"]):
            with self.subTest(args=args):
                r, calls = self.run_grid(*args)
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(calls, [])

    def test_outside_wezterm_without_tab_fails(self):
        r, calls = self.run_grid("2", "2", pane=None)
        self.assertRefused(r, calls, code=1)

    def test_help_exits_zero_without_calling_wezterm(self):
        for flag in ("-h", "--help"):
            r, calls = self.run_grid(flag, "--bogus")
            self.assertEqual(r.returncode, 0)
            self.assertIn("Usage:", r.stdout)
            self.assertEqual(calls, [])

    def test_failed_split_stops_before_sending_commands(self):
        r, calls = self.run_grid("2", "2", fail="split-pane")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.sent(calls), [])


if __name__ == "__main__":
    unittest.main()
