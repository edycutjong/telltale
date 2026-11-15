"""scripts/check_run.py against the committed on-target runs in evidence/runs (real QEMU (TCG) runs, not fixtures)."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import check_run as C  # noqa: E402

RUNS = Path(__file__).resolve().parents[2] / "evidence" / "runs"
GOOD = {"p1b-125318-stale": "stale", "p1b-125357-freeze": "freeze", "p1b-125428-blank": "blank",
        "p1b-125459-misdraw": "misdraw", "p1b-125530-crash": "crash", "p1b-130644-starve": "starve",
        "p1b-131213-none": "none", "p1b-131446-lampdrop": "lampdrop"}


class CheckRunTest(unittest.TestCase):
    def test_recorded_runs_pass(self):
        for run, cls in GOOD.items():
            with self.subTest(run=run):
                res = C.check(RUNS / run, cls)
                self.assertTrue(res and all(ok for ok, _ in res), [m for ok, m in res if not ok])

    def test_one_cpu_starve_run_fails_the_starve_bar(self):
        # whole-CPU starvation: Screen starved too, the monitor flaps -> must not pass as "STARVE recovered"
        res = C.check(RUNS / "p1b-130521-starve", "starve")
        self.assertFalse(all(ok for ok, _ in res))

    def test_wrong_class_fails(self):
        res = C.check(RUNS / "p1b-125357-freeze", "starve")
        self.assertFalse(all(ok for ok, _ in res))

    def test_missing_dir_fails(self):
        res = C.check(RUNS / "does-not-exist", "freeze")
        self.assertFalse(all(ok for ok, _ in res))


if __name__ == "__main__":
    unittest.main()
