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
        "p1b-131213-none": "none", "p1b-131446-lampdrop": "lampdrop",
        "p1b-144540-blank-straddle": "blank"}   # first read straddled the cleared frame: VALUE_MISMATCH, cause BLANK
# engineering-mode run from the on-target suite: banner + incident card logged and read back by ttshot
DEBUG_RUN = Path(__file__).resolve().parents[2] / "evidence" / "runs" / "p1b-151033-debug"
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
    def test_debug_card_row_not_from_incident_fails(self):
        def edit(run):
            log = run / "telltale.log"
            log.write_text(log.read_text().replace("|shown ", "|shown 1", 1))   # card row 2 no longer matches incidents.csv
        self.assertFalse(all(ok for ok, _ in self._tampered(edit)))

    @staticmethod
    def _failed(res, prefix):
        return [m for ok, m in res if not ok and m.startswith(prefix)]

    @unittest.skipUnless(HAVE_PIL, "Pillow not installed")
    def test_debug_text_replaced_by_solid_bars_fails(self):
        # the text's extent and colours survive (amber band, lit pixels, stripe): only the rendered-text check can see it
        from PIL import Image

        def edit(run):
            img = Image.open(run / "banner.png").convert("RGB")
            img.paste(C.GLASS, (150, C.BANNER[1] + 22, 1130, C.BANNER[1] + 44))     # a bar where the letters were
            img.save(run / "banner.png")
            img = Image.open(run / "card.png").convert("RGB")
            for i in range(6):
                y = C.CARD[1] + C.CARD_PAD + 4 + i * C.CARD_PITCH
                img.paste(C.LIT, (C.CARD[0] + C.CARD_PAD, y, C.CARD[0] + 300, y + 12))
            img.save(run / "card.png")
        res = self._tampered(edit)
        self.assertTrue(self._failed(res, "banner text in the frame"), res)
        self.assertTrue(self._failed(res, "card text in the frame"), res)
        self.assertFalse(self._failed(res, "card read back:"))   # the old colour/geometry check alone would have passed

    @unittest.skipUnless(HAVE_PIL, "Pillow not installed")
    def test_debug_frame_showing_another_value_fails(self):
        # the frames show a different restored time than incidents.csv (the log still agrees): the frame check catches it
        from PIL import Image

        def edit(run):
            img = Image.open(run / "card.png").convert("RGB")
            rows = ["INCIDENT TT-VAL-0001", "VALUE_MISMATCH", "shown 48 \u00b7 truth 47 km/h", "caught 20 ms \u00b7 restored 1678 ms",
                    "handed back, no restart", "service: cat /dev/telltale"]          # the run's card says 1679 ms
            want = C.expected_card(rows)
            for y, line in enumerate(want):
                for x, rgb in enumerate(line):
                    img.putpixel((C.CARD[0] + x, C.CARD[1] + y), rgb)
            img.save(run / "card.png")
        res = self._tampered(edit)
        self.assertTrue(self._failed(res, "card text in the frame"), res)
        self.assertFalse(self._failed(res, "incident card logged"))

    @unittest.skipUnless(HAVE_PIL, "Pillow not installed")
    def test_debug_rendering_reproduces_the_capture_exactly(self):
        from PIL import Image
        img = Image.open(DEBUG_RUN / "card.png").convert("RGB")
        rows = ["INCIDENT TT-VAL-0001", "VALUE_MISMATCH", "shown 48 \u00b7 truth 47 km/h", "caught 20 ms \u00b7 restored 1679 ms",
                "handed back, no restart", "service: cat /dev/telltale"]
        self.assertEqual(C.frame_diff(img, C.CARD, C.expected_card(rows)), 0)
        img = Image.open(DEBUG_RUN / "banner.png").convert("RGB")
        text = "ENGINEERING VIEW \u00b7 DISPLAY FAULT CAUGHT \u00b7 VALUE_MISMATCH \u00b7 20 ms"
        self.assertEqual(C.frame_diff(img, C.BANNER, C.expected_banner(text)), 0)
        self.assertGreater(C.frame_diff(img, C.BANNER, C.expected_banner(text.replace("20 ms", "26 ms"))), 0)

    @unittest.skipUnless(HAVE_PIL, "Pillow not installed")
    def test_debug_card_never_hidden_in_frame_fails(self):
        def edit(run):
            shutil.copy(run / "card.png", run / "card-hidden.png")   # the card still on screen after card_ms
        self.assertFalse(all(ok for ok, _ in self._tampered(edit)))

    @unittest.skipUnless(HAVE_PIL, "Pillow not installed")
    def test_debug_without_frames_fails(self):
        self.assertFalse(all(ok for ok, _ in self._tampered(lambda run: (run / "banner.png").unlink())))

    def _clean(self, edit):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        run = d / "run"
        shutil.copytree(RUNS / "p1b-131213-none", run)
        edit(run)
        return C.check(run, "none")

    def test_clean_run_with_a_failing_check_after_arming_fails(self):
        # >= 1000 passing checks, then an armed VALUE_MISMATCH whose incident never closed (no record written yet)
        def edit(run):
            lines = (run / "checks.csv").read_text().splitlines()
            f = lines[-1].split(",")
            f[5] = "VALUE_MISMATCH"
            (run / "checks.csv").write_text("\n".join(lines + [",".join(f)]) + "\n")
        res = self._clean(edit)
        self.assertEqual([m for ok, m in res if not ok][0][:43], "clean drive after arming: 1 checks other th")

    def test_clean_run_with_an_open_incident_at_collection_fails(self):
        def edit(run):
            st = run / "status.txt"
            st.write_text(st.read_text().replace("state=NORMAL", "state=SAFE-HOLD").replace("faults=0", "faults=1"))
        self.assertFalse(all(ok for ok, _ in self._clean(edit)))

    def test_clean_run_without_status_fails(self):
        self.assertFalse(all(ok for ok, _ in self._clean(lambda run: (run / "status.txt").unlink())))

    def test_clean_run_wait_after_arming_fails(self):
        def edit(run):
            lines = (run / "checks.csv").read_text().splitlines()
            f = lines[-1].split(",")
            f[3] = "WAIT"
            (run / "checks.csv").write_text("\n".join(lines + [",".join(f)]) + "\n")
        self.assertFalse(all(ok for ok, _ in self._clean(edit)))

    def test_missing_dir_fails(self):
        res = C.check(RUNS / "does-not-exist", "freeze")
        self.assertFalse(all(ok for ok, _ in res))


if __name__ == "__main__":
    unittest.main()
