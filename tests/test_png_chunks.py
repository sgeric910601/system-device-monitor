"""PNG 區塊檢查與去除附屬資訊：只留下 IHDR、PLTE、tRNS、IDAT、IEND。"""
import unittest

from sdm.core.png_chunks import PngError, chunk_types, extra_chunks, strip_metadata
from tests.png_samples import EXIF, SRGB, TEXT, TIME, chunk, png_bytes


class ChunkTypes(unittest.TestCase):
    def test_lists_chunks_in_order(self):
        self.assertEqual(chunk_types(png_bytes(extra=(TEXT,))), ("IHDR", "tEXt", "IDAT", "IEND"))

    def test_clean_png_has_no_extra_chunks(self):
        self.assertEqual(extra_chunks(png_bytes()), ())

    def test_metadata_and_other_ancillary_chunks_are_reported_once(self):
        data = png_bytes(extra=(TEXT, EXIF, TIME, SRGB, TEXT))
        self.assertEqual(extra_chunks(data), ("eXIf", "sRGB", "tEXt", "tIME"))

    def test_rejects_data_that_is_not_a_complete_png(self):
        clean = png_bytes()
        broken_crc = clean[:-5] + b"\x00" + clean[-4:]
        for data in (b"", b"GIF89a", clean[:20], clean[:-12], broken_crc,
                     clean + chunk(b"tEXt", b"after end")):
            with self.subTest(size=len(data)):
                with self.assertRaises(PngError):
                    chunk_types(data)


class Strip(unittest.TestCase):
    def test_removes_every_ancillary_chunk_and_keeps_the_image(self):
        data = png_bytes(extra=(TEXT, EXIF, TIME, SRGB))
        cleaned = strip_metadata(data)
        self.assertEqual(chunk_types(cleaned), ("IHDR", "IDAT", "IEND"))
        self.assertEqual(cleaned, png_bytes())
        self.assertNotIn(b"synthetic", cleaned)

    def test_clean_png_is_unchanged(self):
        self.assertEqual(strip_metadata(png_bytes()), png_bytes())

    def test_transparency_and_palette_chunks_are_kept(self):
        data = png_bytes(extra=((b"PLTE", b"\x00" * 3), (b"tRNS", b"\x00")))
        self.assertEqual(chunk_types(strip_metadata(data)), ("IHDR", "PLTE", "tRNS", "IDAT", "IEND"))


if __name__ == "__main__":
    unittest.main()
