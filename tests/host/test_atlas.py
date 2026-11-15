"""The committed glyph + text atlases are exactly what the generators produce from the vendored font."""
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HAVE_PIL = importlib.util.find_spec("PIL") is not None


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class AtlasTest(unittest.TestCase):
    def check(self, tool):
        r = subprocess.run([sys.executable, str(ROOT / "tools" / tool), "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_digit_atlas_up_to_date(self):
        self.check("gen_glyphs.py")

    def test_text_atlas_up_to_date(self):
        self.check("gen_text.py")


if __name__ == "__main__":
    unittest.main()
