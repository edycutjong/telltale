"""scripts/check_run.py against the committed on-target runs in evidence/runs (real QEMU (TCG) runs, not fixtures)."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import check_run as C  # noqa: E402

RUNS = Path(__file__).resolve().parents[2] / "evidence" / "runs"
GOOD = {"p1b-125318-stale": "stale", "p1b-125357-freeze": "freeze", "p1b-125428-blank": "blank",
        "p1b-125459-misdraw": "misdraw", "p1b-125530-crash": "crash", "p1b-130644-starve": "starve",
        "p1b-131213-none": "none", "p1b-131446-lampdrop": "lampdrop"}
# engineering-mode run from the on-target suite: banner + incident card logged and read back by ttshot
DEBUG_RUN = Path(__file__).resolve().parents[2] / "evidence" / "test-target" / "20261115T142358Z" / "t-debug"
try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


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

    @unittest.skipUnless(HAVE_PIL, "Pillow not installed")
    def test_debug_run_passes(self):
        res = C.check(DEBUG_RUN, "debug")
        self.assertTrue(res and all(ok for ok, _ in res), [m for ok, m in res if not ok])

    def _tampered(self, edit):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        run = d / "run"
        shutil.copytree(DEBUG_RUN, run)
        edit(run)
        return C.check(run, "debug")

    @unittest.skipUnless(HAVE_PIL, "Pillow not installed")
    def test_debug_banner_with_wrong_ms_fails(self):
        def edit(run):
            log = run / "telltale.log"
            log.write_text(log.read_text().replace("VALUE_MISMATCH \u00b7 ", "VALUE_MISMATCH \u00b7 9", 1))
        self.assertFalse(all(ok for ok, _ in self._tampered(edit)))

    @unittest.skipUnless(HAVE_PIL, "Pillow not installed")
    def test_debug_card_missing_from_frame_fails(self):
        from PIL import Image

        def edit(run):
            img = Image.open(run / "card.png").convert("RGB")
            img.paste((14, 13, 11), (868, 144, 868 + 404, 144 + 188))   # glass where the card should be
            img.save(run / "card.png")
        self.assertFalse(all(ok for ok, _ in self._tampered(edit)))

    @unittest.skipUnless(HAVE_PIL, "Pillow not installed")
    def test_debug_without_frames_fails(self):
        self.assertFalse(all(ok for ok, _ in self._tampered(lambda run: (run / "banner.png").unlink())))

    def test_missing_dir_fails(self):
        res = C.check(RUNS / "does-not-exist", "freeze")
        self.assertFalse(all(ok for ok, _ in res))


if __name__ == "__main__":
    unittest.main()
