"""「我的照片」：依拍攝方向轉正、從中間裁成正方形、重新編碼成 512 px 的 PNG，不留任何照片資訊。

測試用的照片在暫存目錄中產生：左半紅、右半藍的 JPEG，再插入含方向與文字說明的 EXIF。
"""
import os
from pathlib import Path
import stat
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
import zlib

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

from sdm.constants import Companion
from sdm.core.png_chunks import chunk_types
from sdm.services import companion_photo
from sdm.services.companion_photo import PhotoError, PhotoStore
from tests.png_samples import SIGNATURE, chunk

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


def header_only_png(path, width, height):
    """只有標頭的 PNG：宣告很大的尺寸，實際只有幾十個位元組（解碼炸彈）。"""
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.write_bytes(SIGNATURE + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(64)))
                     + chunk(b"IEND", b""))
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


class Limits(unittest.TestCase):
    """解碼之前先檢查：一般檔案、常見格式、尺寸上限；不合格的完全不解碼。"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.store = PhotoStore(self.base/"data/companion-photo.png")
        decode = patch.object(companion_photo, "decode", side_effect=AssertionError("decoded"))
        self.decode = decode.start()
        self.addCleanup(decode.stop)

    def rejected(self, source, message):
        with self.assertRaises(PhotoError) as caught:
            self.store.save(source)
        self.assertEqual(str(caught.exception), message)
        self.decode.assert_not_called()
        self.assertFalse(self.store.path.exists())

    def test_huge_declared_size_is_rejected_without_decoding(self):
        self.rejected(header_only_png(self.base/"bomb.png", 30000, 30000),
                      companion_photo.TOO_MANY_PIXELS)

    def test_a_very_long_side_is_rejected(self):
        self.rejected(header_only_png(self.base/"strip.png", 20000, 8),
                      companion_photo.TOO_MANY_PIXELS)

    def test_uncommon_formats_are_rejected(self):
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 4, 4)
        pixbuf.fill(RED)
        pixbuf.savev(str(self.base/"photo.tiff"), "tiff", [], [])
        self.rejected(self.base/"photo.tiff", companion_photo.UNSUPPORTED)

    def test_only_regular_files_are_read(self):
        real = halves_jpeg(self.base/"real.jpg")
        (self.base/"link.jpg").symlink_to(real)
        os.mkfifo(self.base/"pipe.jpg")
        (self.base/"folder.jpg").mkdir()
        for name in ("link.jpg", "pipe.jpg", "folder.jpg", "missing.jpg"):
            with self.subTest(name=name):
                self.rejected(self.base/name, companion_photo.UNREADABLE)


class Symlinks(unittest.TestCase):
    def test_a_symlink_in_place_of_the_photo_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            other = halves_jpeg(base/"elsewhere.jpg")
            target = base/"data/companion-photo.png"
            target.parent.mkdir()
            target.symlink_to(other)
            store = PhotoStore(target)
            self.assertFalse(store.has_photo())
            self.assertIsNone(store.load())
            store.remove()
            self.assertTrue(target.is_symlink())


class Background(unittest.TestCase):
    """解碼在背景執行緒，結果回到主執行緒才寫檔並通知。"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.store = PhotoStore(self.base/"data/companion-photo.png")

    def test_result_is_delivered_through_the_main_loop_callback(self):
        queued, results = [], []
        self.store.save_async(halves_jpeg(self.base/"photo.jpg"), results.append,
                              call_soon=queued.append, start=lambda work: work())
        self.assertEqual((results, self.store.has_photo()), ([], False))
        for callback in queued:
            callback()
        self.assertEqual(results, [None])
        self.assertTrue(self.store.has_photo())

    def test_errors_come_back_as_messages(self):
        queued, results = [], []
        (self.base/"notes.jpg").write_text("not an image")
        self.store.save_async(self.base/"notes.jpg", results.append, call_soon=queued.append,
                              start=lambda work: work())
        for callback in queued:
            callback()
        self.assertEqual(results, [companion_photo.UNREADABLE])

    def test_work_runs_on_another_thread(self):
        done, threads = threading.Event(), []

        def call_soon(callback):
            threads.append(threading.current_thread())
            callback()
            done.set()
        self.store.save_async(halves_jpeg(self.base/"photo.jpg"), lambda result: None,
                              call_soon=call_soon)
        self.assertTrue(done.wait(30))
        self.assertIsNot(threads[0], threading.main_thread())


if __name__ == "__main__":
    unittest.main()
