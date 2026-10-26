"""Runs the bun tests of ext/hivegrid-agent-state.ts (skipped when bun is unavailable)."""
import os
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipUnless(shutil.which("bun"), "bun not installed")
class ExtStateTests(unittest.TestCase):
    def test_bun_suite(self):
        p = subprocess.run(
            ["bun", "test", "tests/ext/hivegrid-agent-state.test.ts"],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
