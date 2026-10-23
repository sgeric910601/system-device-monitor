"""「我的照片」：依拍攝方向轉正、從中間裁成正方形、重新編碼成 512 px 的 PNG，不留任何照片資訊。

測試用的照片在暫存目錄中產生：左半紅、右半藍的 JPEG，再插入含方向與文字說明的 EXIF。
"""
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

from sdm.constants import Companion
from sdm.core.png_chunks import chunk_types
from sdm.services.companion_photo import PhotoError, PhotoStore

MARK = b"SYNTHETIC-PLACE-NAME"
RED, BLUE = 0xE02020FF, 0x2020E0FF


def exif_segment(orientation):
    """APP1 Exif：IFD0 有 Orientation 與 ImageDescription 兩項。"""
    text = MARK + b"\x00"
    entries = struct.pack(">HHIHH", 0x0112, 3, 1, orientation, 0)
    entries += struct.pack(">HHII", 0x010E, 2, len(text), 8 + 2 + 2 * 12 + 4)
    tiff = b"MM\x00\x2a" + struct.pack(">I", 8) + struct.pack(">H", 2) + entries + \
        struct.pack(">I", 0) + text
    body = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body


def halves_jpeg(path, orientation=1, width=640, height=480):
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    pixbuf.new_subpixbuf(0, 0, width // 2, height).fill(RED)
    pixbuf.new_subpixbuf(width // 2, 0, width // 2, height).fill(BLUE)
    ok, data = pixbuf.save_to_bufferv("jpeg", ["quality"], ["95"])
    data = bytes(data)
    path.write_bytes(data[:2] + exif_segment(orientation) + data[2:])
    return path


def pixel(pixbuf, x, y):
    data = pixbuf.get_pixels()
    start = y * pixbuf.get_rowstride() + x * pixbuf.get_n_channels()
    return tuple(data[start:start + 3])


class Store(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.target = self.base/"data/system-device-monitor/companion-photo.png"
        self.store = PhotoStore(self.target)

    def test_saved_photo_is_a_512_px_png_without_metadata(self):
        source = halves_jpeg(self.base/"photo.jpg")
        self.assertIn(MARK, source.read_bytes())
        self.store.save(source)
        data = self.target.read_bytes()
        self.assertEqual(chunk_types(data), ("IHDR", "IDAT", "IEND"))
        self.assertNotIn(MARK, data)
        self.assertNotIn(b"Exif", data)
        saved = GdkPixbuf.Pixbuf.new_from_file(str(self.target))
        self.assertEqual((saved.get_width(), saved.get_height()), (512, 512))

    def test_square_crop_keeps_the_middle(self):
        self.store.save(halves_jpeg(self.base/"photo.jpg"))
        saved = self.store.load()
        left, right = pixel(saved, 60, 256), pixel(saved, 450, 256)
        self.assertGreater(left[0], 180)
        self.assertGreater(right[2], 180)

    def test_orientation_from_exif_is_applied(self):
        self.store.save(halves_jpeg(self.base/"photo.jpg", orientation=6))
        saved = self.store.load()
        top, bottom = pixel(saved, 256, 40), pixel(saved, 256, 470)
        self.assertGreater(top[0], 180)
        self.assertGreater(bottom[2], 180)

    def test_only_the_owner_can_read_it(self):
        self.store.save(halves_jpeg(self.base/"photo.jpg"))
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.target.parent.stat().st_mode), 0o700)

    def test_remove_and_version(self):
        self.assertFalse(self.store.has_photo())
        version = self.store.version
        self.store.save(halves_jpeg(self.base/"photo.jpg"))
        self.assertTrue(self.store.has_photo())
        self.store.remove()
        self.assertFalse(self.target.exists())
        self.assertFalse(self.store.has_photo())
        self.assertEqual(self.store.version, version + 2)
        self.assertIsNone(self.store.load())

    def test_unreadable_or_oversized_files_are_rejected(self):
        text = self.base/"notes.jpg"
        text.write_text("not an image")
        with self.assertRaises(PhotoError):
            self.store.save(text)
        with self.assertRaises(PhotoError):
            self.store.save(self.base/"missing.jpg")
        source = halves_jpeg(self.base/"photo.jpg")
        with patch.object(Companion, "PHOTO_MAX_BYTES", 10):
            with self.assertRaises(PhotoError):
                self.store.save(source)
        self.assertFalse(self.target.exists())

    def test_disabled_store_never_touches_the_photo(self):
        PhotoStore(self.target).save(halves_jpeg(self.base/"photo.jpg"))
        disabled = PhotoStore(self.target, enabled=False)
        self.assertFalse(disabled.has_photo())
        self.assertIsNone(disabled.load())
        with self.assertRaises(PhotoError):
            disabled.save(self.base/"photo.jpg")
        disabled.remove()
        self.assertTrue(self.target.exists())


if __name__ == "__main__":
    unittest.main()
