"""Unit tests for scripts/bench.py over SYNTHETIC fixture rows (tests/fixtures/synthetic-*.csv).
These rows are hand-written test inputs, not measurements."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import bench  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


class Percentile(unittest.TestCase):
    def test_nearest_rank(self):
        vals = list(range(1, 101))  # 1..100
        self.assertEqual(bench.percentile(vals, 50), 50)
        self.assertEqual(bench.percentile(vals, 95), 95)
        self.assertEqual(bench.percentile(vals, 100), 100)

    def test_small_n_rounds_up(self):
        self.assertEqual(bench.percentile([10, 20, 30], 95), 30)
        self.assertEqual(bench.percentile([10, 20, 30], 50), 20)
        self.assertEqual(bench.percentile([7], 95), 7)

    def test_empty_and_bad_p(self):
        self.assertIsNone(bench.percentile([], 95))
        with self.assertRaises(ValueError):
            bench.percentile([1], 0)


class Faults(unittest.TestCase):
    def setUp(self):
        t = bench.load([str(FIX / "synthetic-bench-faults.csv")], bench.FAULT_COLUMNS)
        self.synthetic = t.synthetic
        self.res = bench.analyze_faults(t.rows)

    def test_marked_synthetic(self):
        self.assertTrue(self.synthetic)

    def test_detect_is_from_effective_inject_time(self):
        f = self.res["classes"]["FREEZE"]["detect_ms"]
        self.assertEqual(f["n"], 2)
        self.assertAlmostEqual(f["p50"], 60.0)
        self.assertAlmostEqual(f["p95"], 80.0)
        self.assertAlmostEqual(f["max"], 80.0)

    def test_recovery_metrics(self):
        c = self.res["classes"]["FREEZE"]
        self.assertAlmostEqual(c["safe_layer_recovery_ms"]["p95"], 20.0)
        self.assertAlmostEqual(c["full_recovery_ms"]["p50"], 419.0)  # nearest rank of [419, 439]
        self.assertAlmostEqual(c["full_recovery_ms"]["max"], 439.0)

    def test_stale_diverge_and_miss(self):
        c = self.res["classes"]["STALE"]
        self.assertEqual(c["misses"], 1)
        self.assertAlmostEqual(c["detect_ms"]["p50"], 150.0)
        self.assertAlmostEqual(c["detect_from_diverge_ms"]["p50"], 100.0)

    def test_missing_inject_stamp_is_failed_not_estimated(self):
        c = self.res["classes"]["BLANK"]
        self.assertEqual(c["failed"], 1)
        self.assertEqual(c["detect_ms"]["n"], 0)

    def test_starve_has_no_restart_but_counts(self):
        self.assertEqual(self.res["classes"]["STARVE"]["detect_ms"]["n"], 1)

    def test_qemu_label(self):
        self.assertIn("QEMU timing (accel=tcg), not hardware timing", self.res["timing"])

    def test_render_has_banner(self):
        text = bench.render_faults(self.res, True)
        self.assertTrue(text.startswith("**SYNTHETIC"))
        self.assertIn("| FREEZE | 2 | 0 | 0 | 0 | 2 · 60.0 / 80.0 / 80.0 | 2 · 20.0 / 20.0 |", text)


def _row(**kw):
    base = dict.fromkeys(bench.FAULT_COLUMNS, "")
    base.update(accel="tcg", tier="G1-FULL", smp="8", runmask="0x1", period_ms="20", **{"class": "FREEZE"})
    base.update(kw)
    return base


class FaultRules(unittest.TestCase):
    def test_late_detection_is_a_miss(self):
        r = _row(t_inject_ns="0", t_detect_ns="2500000000", missed="1")
        c = bench.analyze_faults([r])["classes"]["FREEZE"]
        self.assertEqual((c["misses"], c["inconsistent"]), (1, 0))

    def test_missed_column_must_agree(self):
        r = _row(t_inject_ns="0", t_detect_ns="50000000", missed="1")
        c = bench.analyze_faults([r])["classes"]["FREEZE"]
        self.assertEqual(c["inconsistent"], 1)
        self.assertEqual(c["detect_ms"]["n"], 0)

    def test_mixed_configuration_refused(self):
        rows = [_row(t_inject_ns="0", t_detect_ns="1", missed="0"),
                _row(t_inject_ns="0", t_detect_ns="1", missed="0", period_ms="10")]
        with self.assertRaises(bench.BenchError):
            bench.analyze_faults(rows)
        self.assertEqual(len(bench.analyze_faults(rows, allow_mixed=True)["configs"]), 2)

    def test_time_running_backwards_is_inconsistent(self):
        rows = [_row(t_inject_ns="100", t_detect_ns="50", missed="0"),
                _row(t_inject_ns="0", t_detect_ns="100", t_safe_visible_ns="90", missed="0")]
        c = bench.analyze_faults(rows)["classes"]["FREEZE"]
        self.assertEqual((c["inconsistent"], c["detect_ms"]["n"]), (2, 0))

    def test_missing_recovery_stamps_counted(self):
        rows = [_row(t_inject_ns="0", t_detect_ns="1000000", missed="0"),
                _row(**{"class": "STARVE"}, t_inject_ns="0", t_detect_ns="1000000",
                     t_safe_visible_ns="2000000", missed="0")]
        res = bench.analyze_faults(rows)
        f, st = res["classes"]["FREEZE"], res["classes"]["STARVE"]
        self.assertEqual((f["missing_safe_stamp"], f["missing_true_stamp"]), (1, 1))
        self.assertEqual((st["missing_safe_stamp"], st["missing_true_stamp"]), (0, 0))  # STARVE never restarts
        self.assertEqual(res["problem_rows"], 2)

    def test_detect_only_class_exempt_from_recovery_stamps(self):
        r = _row(**{"class": "B7d"}, t_inject_ns="0", t_detect_ns="1000000", missed="0")
        self.assertEqual(bench.analyze_faults([r])["problem_rows"], 2)
        self.assertEqual(bench.analyze_faults([r], detect_only=("B7d",))["problem_rows"], 0)

    def test_restart_before_detect_is_inconsistent(self):
        r = _row(t_inject_ns="0", t_detect_ns="100", t_restart_ns="50", missed="0")
        self.assertEqual(bench.analyze_faults([r])["classes"]["FREEZE"]["inconsistent"], 1)

    def test_non_numeric_stamp_is_bench_error(self):
        with self.assertRaises(bench.BenchError):
            bench.analyze_faults([_row(t_inject_ns="abc", missed="0")])

    def test_hardware_accel_not_labelled_qemu(self):
        r = _row(accel="pi5", t_inject_ns="0", t_detect_ns="1", missed="0")
        self.assertEqual(bench.analyze_faults([r])["timing"], "accel=pi5")


class Schema(unittest.TestCase):
    def test_missing_column_rejected(self):
        with self.assertRaises(bench.BenchError):
            bench.read_table("run_id,class\nr1,FREEZE\n", bench.FAULT_COLUMNS)

    def test_header_only_rejected(self):
        with self.assertRaises(bench.BenchError):
            bench.read_table(",".join(bench.CHECK_COLUMNS) + "\n", bench.CHECK_COLUMNS)

    def test_empty_rejected(self):
        with self.assertRaises(bench.BenchError):
            bench.read_table("# only a comment\n", bench.CHECK_COLUMNS)


class Clean(unittest.TestCase):
    def test_false_positive_is_any_non_pass_verdict(self):
        t = bench.load([str(FIX / "synthetic-bench-clean.csv")], bench.CHECK_COLUMNS)
        self.assertEqual(t.meta["accel"], "tcg")
        res = bench.analyze_clean(t.rows, t.meta["accel"])
        self.assertIn("QEMU timing (accel=tcg)", res["timing"])
        self.assertEqual(res["checks"], 4)
        self.assertEqual(res["armed_checks"], 3)
        self.assertEqual(res["false_positives"], 2)        # armed VALUE_MISMATCH + unarmed UNKNOWN
        self.assertEqual(res["false_positives_armed"], 1)
        self.assertAlmostEqual(res["duration_s"], 0.06)
        self.assertAlmostEqual(res["false_positives_per_30min"], 60000.0)
        self.assertEqual(res["readback_check_us"]["p95"], 6000.0)


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "bench.py"), *args],
                              capture_output=True, text=True)

    def test_json_output(self):
        p = self.run_cli("faults", "--json", str(FIX / "synthetic-bench-faults.csv"))
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertTrue(data["synthetic"])
        self.assertEqual(data["classes"]["STALE"]["misses"], 1)

    def test_strict_fails_on_problem_rows(self):
        p = self.run_cli("faults", "--strict", str(FIX / "synthetic-bench-faults.csv"))
        self.assertEqual(p.returncode, 3)  # the fixture's BLANK row has no t_inject
        self.assertIn("problem rows", p.stderr)

    def test_clean_needs_accel_and_one_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            no_accel = Path(d) / "c.csv"
            no_accel.write_text("\n".join(ln for ln in (FIX / "synthetic-bench-clean.csv").read_text().splitlines()
                                          if "accel=" not in ln) + "\n")
            self.assertEqual(self.run_cli("clean", str(no_accel)).returncode, 2)
            self.assertEqual(self.run_cli("clean", "--accel", "tcg", str(no_accel)).returncode, 0)
        two = self.run_cli("clean", str(FIX / "synthetic-bench-clean.csv"), str(FIX / "synthetic-bench-clean.csv"))
        self.assertEqual(two.returncode, 2)
        self.assertIn("one run per report", two.stderr)

    def test_bad_schema_exit_2(self):
        p = self.run_cli("faults", str(FIX / "synthetic-bench-clean.csv"))
        self.assertEqual(p.returncode, 2)
        self.assertIn("missing columns", p.stderr)



class Ablation(unittest.TestCase):
    ROWS = [{"class": "FREEZE", "seconds_wrong": "29.5", "wrong_at_end": "1", "monitor_acted": "0", "window_s": "30"},
            {"class": "FREEZE", "seconds_wrong": "20.0", "wrong_at_end": "0", "monitor_acted": "0", "window_s": "30"},
            {"class": "BLANK", "seconds_wrong": "", "wrong_at_end": "", "monitor_acted": "", "window_s": "30"}]

    def test_counts_and_missing(self):
        res = bench.analyze_ablation(self.ROWS)
        self.assertEqual((res["runs"], res["missing_rows"], res["runs_below_bar"], res["wrong_at_end"]), (2, 1, 1, 1))
        self.assertEqual(res["classes"]["FREEZE"]["median_s"], 20.0)
        self.assertIn("rows without a measurement: 1", bench.render_ablation(res))


if __name__ == "__main__":
    unittest.main()
