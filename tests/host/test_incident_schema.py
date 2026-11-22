"""The incident CSV has no header row, so the readers' column lists ARE the schema: they must equal the monitor's
EV_HEADER, and every verdict check_run.py accepts must be one the monitor can write (VN[] in telltale.c)."""
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import bench_run  # noqa: E402
import check_run  # noqa: E402

SRC = (ROOT / "src" / "telltale" / "telltale.c").read_text()


class IncidentSchema(unittest.TestCase):
    def test_check_run_cols_are_ev_header(self):
        m = re.search(r'#define EV_HEADER "([^"\\]*)\\n"', SRC)
        self.assertIsNotNone(m)
        self.assertEqual(check_run.COLS, m.group(1).split(","))

    def test_bench_run_reads_the_same_columns(self):
        with tempfile.TemporaryDirectory() as d:
            row = ",".join(str(i) for i in range(len(check_run.COLS)))
            Path(d, "incidents.csv").write_text(row + "\n")
            self.assertEqual(list(bench_run.read_incidents(Path(d))[0]), check_run.COLS)

    def test_expected_verdicts_are_monitor_verdicts(self):
        vn = set(re.findall(r'"([^"]*)"', re.search(r'static const char \*VN\[\] = \{([^}]*)\};', SRC).group(1)))
        for cls, (verdicts, _causes, _kill) in check_run.EXPECT.items():
            self.assertLessEqual(verdicts, vn, f"{cls}: {sorted(verdicts - vn)}")

    def test_unknown_width_is_schema_drift(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "incidents.csv").write_text(",".join(["1"] * 17) + "\n")
            with self.assertRaises(ValueError):
                check_run.read_incidents(Path(d))

    def test_missing_incident_log_fails_even_a_clean_run(self):
        with tempfile.TemporaryDirectory() as d:
            res = check_run.check(Path(d), "none")
            self.assertFalse(all(ok for ok, _ in res))


if __name__ == "__main__":
    unittest.main()
