"""Unit tests for tools/gen_scenario.py (the scripted lamp/gear scenario for the hero drive)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import gen_scenario as G  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


class ScenarioTest(unittest.TestCase):
    def setUp(self):
        self.rows = G.scenario_rows(12.0, 16.848)

    def at(self, ts):
        bits = 0
        for t, b, _ in self.rows:
            if t <= ts:
                bits = b
        return bits

    def test_belt_first_two_seconds(self):
        self.assertEqual(self.at(0.0), G.BELT)
        self.assertEqual(self.at(1.999), G.BELT)
        self.assertEqual(self.at(2.0) & G.BELT, 0)

    def test_turn_blinks_at_90_per_minute_while_slowing(self):
        edges = [t for t, b, _ in self.rows if 12.0 <= t < 15.8]
        gaps = {round(b - a, 3) for a, b in zip(edges, edges[1:])}
        self.assertTrue(gaps <= {0.333, 0.334}, gaps)
        self.assertTrue(self.at(12.1) & G.TURN_L)
        self.assertFalse(self.at(12.4) & G.TURN_L)
        self.assertFalse(self.at(20.0) & G.TURN_L)

    def test_brake_lamp_last_second_and_after(self):
        self.assertFalse(self.at(15.8) & G.BRAKE)
        self.assertTrue(self.at(15.848) & G.BRAKE)
        self.assertTrue(self.at(40.0) & G.BRAKE)

    def test_no_duplicate_rows_and_sorted(self):
        ts = [t for t, _, _ in self.rows]
        self.assertEqual(ts, sorted(ts))
        self.assertTrue(all(a[1] != b[1] for a, b in zip(self.rows, self.rows[1:])))
        self.assertTrue(all(g == "D" for _, _, g in self.rows))

    def test_committed_file_is_up_to_date(self):
        meta = os.path.join(ROOT, "data", "trace-hero.meta")
        out = os.path.join(ROOT, "data", "scenario-hero.csv")
        self.assertEqual(G.main(["--meta", meta, "--out", out, "--check"]), 0)


if __name__ == "__main__":
    unittest.main()
