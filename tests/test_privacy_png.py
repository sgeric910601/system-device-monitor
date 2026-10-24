"""隱私檢查的 PNG 規則：只允許清單中 assets/ 底下、沒有任何附屬資訊區塊的 PNG。"""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from sdm.paths import VERSION
from tests.png_samples import EXIF, SIGNATURE, TEXT, TIME, chunk, png_bytes

ROOT = Path(__file__).resolve().parents[1]


def load_privacy():
    spec = importlib.util.spec_from_file_location("privacy_check", ROOT/"scripts/privacy_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


privacy = load_privacy()


def stored_png(row):
    """不壓縮的 PNG：像素位元組原樣出現在檔案中（測試文字規則不套用到圖檔內容）。"""
    header = struct.pack(">IIBBBBB", len(row) // 4, 1, 8, 6, 0, 0, 0)
    return SIGNATURE + chunk(b"IHDR", header) + chunk(
        b"IDAT", zlib.compress(b"\x00" + row, 0)) + chunk(b"IEND", b"")


class PngFindings(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)

    def write(self, data, name="image.png"):
        path = self.directory/name
        path.write_bytes(data)
        return path

    def test_allowed_clean_png_passes(self):
        self.assertEqual(privacy.findings(self.write(png_bytes()), png_allowed=True), [])

    def test_allowed_png_with_metadata_chunks_fails(self):
        for extra in (TEXT, EXIF, TIME):
            with self.subTest(chunk=extra[0]):
                path = self.write(png_bytes(extra=(extra,)))
                self.assertEqual(privacy.findings(path, png_allowed=True), ["PNG metadata chunk"])

    def test_png_outside_the_allowlist_is_still_blocked(self):
        self.assertIn("non-public file type", privacy.findings(self.write(png_bytes())))

    def test_invalid_png_is_reported(self):
        path = self.write(b"not a png at all")
        self.assertEqual(privacy.findings(path, png_allowed=True), ["invalid PNG"])

    def test_text_patterns_are_not_applied_to_image_bytes(self):
        row = b"xx" + b"@" + b"yy.zz" + b"\x00"
        path = self.write(stored_png(row))
        self.assertIn(b"@yy.zz", path.read_bytes())
        self.assertEqual(privacy.findings(path, png_allowed=True), [])
        self.assertIn("personal email", privacy.findings(path))


class Allowlist(unittest.TestCase):
    def test_repository_pngs_must_be_listed_and_under_assets(self):
        names = {"assets/app/mint-128.png", "icons/cpu.png"}
        self.assertTrue(privacy.png_listed("assets/app/mint-128.png", names))
        self.assertFalse(privacy.png_listed("assets/app/peach-128.png", names))
        self.assertFalse(privacy.png_listed("icons/cpu.png", names))
        self.assertFalse(privacy.png_listed("assets/notes.txt", {"assets/notes.txt"}))

    def test_unpacked_artifacts_allow_only_listed_files_at_known_places(self):
        manifest = {"runtime": ["assets/app/mint-128.png"], "source": ["assets/app/mint-128.png"],
                    "system": [{"source": "assets/app/mint-128.png",
                                "target": "/usr/share/icons/hicolor/128x128/apps/"
                                          "system-device-monitor.png"}]}
        for name in ("usr/share/system-device-monitor/assets/app/mint-128.png",
                     "usr/share/icons/hicolor/128x128/apps/system-device-monitor.png",
                     f"system-device-monitor-{VERSION}/assets/app/mint-128.png"):
            with self.subTest(name=name):
                self.assertTrue(privacy.tree_png_allowed(name, manifest))
        for name in ("usr/share/system-device-monitor/assets/app/other.png",
                     "usr/share/system-device-monitor/sdm/assets/app/mint-128.png",
                     "home/user/assets/app/mint-128.png", "assets/app/mint-128.png",
                     "usr/share/icons/hicolor/512x512/apps/system-device-monitor.png",
                     "system-device-monitor-0.0.1/assets/app/mint-128.png",
                     "usr/share/doc/system-device-monitor/shot.png"):
            with self.subTest(name=name):
                self.assertFalse(privacy.tree_png_allowed(name, manifest))


if __name__ == "__main__":
    unittest.main()
