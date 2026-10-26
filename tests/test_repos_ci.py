import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import hivegrid_repos as R  # noqa: E402


def runs(*items):
    return json.dumps([{"workflowName": w, "status": s, "conclusion": c} for w, s, c in items])


class ParseCi(unittest.TestCase):
    def test_only_latest_run_of_each_workflow_counts(self):
        # newest first: the old failure of "test" was fixed by a newer green run
        self.assertEqual(R.parse_ci(runs(("test", "completed", "success"), ("test", "completed", "failure"))), "pass")

    def test_any_failing_workflow_fails_the_repo(self):
        self.assertEqual(R.parse_ci(runs(("lint", "completed", "success"), ("test", "completed", "timed_out"))), "fail")

    def test_failure_beats_running_beats_pass(self):
        self.assertEqual(R.parse_ci(runs(("a", "in_progress", ""), ("b", "completed", "success"))), "running")
        self.assertEqual(R.parse_ci(runs(("a", "in_progress", ""), ("b", "completed", "failure"))), "fail")

    def test_skipped_does_not_mask_a_pass(self):
        self.assertEqual(R.parse_ci(runs(("a", "completed", "skipped"), ("b", "completed", "success"))), "pass")
        self.assertEqual(R.parse_ci(runs(("a", "completed", "cancelled"))), "other")

    def test_no_runs_and_garbage(self):
        self.assertEqual(R.parse_ci("[]"), "none")
        self.assertIsNone(R.parse_ci("not json"))
        self.assertIsNone(R.parse_ci("{}"))


if __name__ == "__main__":
    unittest.main()
