"""隨程式發布的圖檔：都在清單中、沒有附屬資訊、尺寸正確，總大小不超過 2 MB。"""
import json
from pathlib import Path
import struct
import unittest

from sdm.core.png_chunks import chunks, extra_chunks

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT/"assets"
MAX_TOTAL_BYTES = 2 * 1024 * 1024
EXPECTED = {**{f"app/{color}-{size}.png": (size, size) for color in ("mint", "peach", "night", "sky")
               for size in (128, 512)},
            "companion/companion-charging.png": (152, 160),
            "companion/companion-resting.png": (152, 160),
            "companion/pet-cat.png": (320, 212),
            "companion/cat-headshot.png": (224, 224)}


def size_of(path):
    return struct.unpack(">II", chunks(path.read_bytes())[0][1][:8])


class Assets(unittest.TestCase):
    def setUp(self):
        manifest = json.loads((ROOT/"package-files.json").read_text())
        self.runtime, self.source = set(manifest["runtime"]), set(manifest["source"])
        self.files = sorted(path for path in ASSETS.rglob("*") if path.is_file())

    def test_expected_images_exist_with_their_sizes(self):
        found = {str(path.relative_to(ASSETS)): size_of(path) for path in self.files}
        self.assertEqual(found, EXPECTED)

    def test_every_asset_is_listed_and_shipped(self):
        names = {str(path.relative_to(ROOT)) for path in self.files}
        self.assertEqual(sorted(names - self.runtime), [])
        self.assertEqual(sorted(names - self.source), [])

    def test_assets_carry_no_metadata(self):
        for path in self.files:
            with self.subTest(path=path.name):
                self.assertEqual(extra_chunks(path.read_bytes()), ())

    def test_total_size_stays_within_the_budget(self):
        self.assertLessEqual(sum(path.stat().st_size for path in self.files), MAX_TOTAL_BYTES)


if __name__ == "__main__":
    unittest.main()
