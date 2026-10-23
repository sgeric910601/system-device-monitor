"""開發用圖檔工具（scripts/asset_png.py）：純 Python 的 PNG 解碼與編碼、縮小、裁切、遮罩。"""
import importlib.util
from pathlib import Path
import random
import struct
import unittest
import zlib

from sdm.core.png_chunks import chunk_types
from tests.png_samples import EXIF, SIGNATURE, TEXT, chunk, png_bytes

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/"scripts"/(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


png = load_script("asset_png")


def noise(width, height, channels, seed=3):
    rng = random.Random(seed)
    return png.Image(width, height, channels, bytes(rng.randrange(256)
                                                   for _ in range(width * height * channels)))


class Codec(unittest.TestCase):
    def test_decodes_a_simple_png(self):
        image = png.decode(png_bytes(3, 2, pixel=b"\x01\x02\x03\x04"))
        self.assertEqual((image.width, image.height, image.channels), (3, 2, 4))
        self.assertEqual(image.pixels, b"\x01\x02\x03\x04" * 6)

    def test_every_row_filter_round_trips(self):
        for channels in (3, 4):
            image = noise(7, 5, channels)
            for kind in range(5):
                with self.subTest(channels=channels, filter=kind):
                    self.assertEqual(png.decode(png.encode(image, filter_type=kind)), image)
            self.assertEqual(png.decode(png.encode(image)), image)

    def test_output_has_only_image_chunks_and_is_reproducible(self):
        image = png.decode(png_bytes(4, 4, extra=(TEXT, EXIF)))
        data = png.encode(image)
        self.assertEqual(chunk_types(data), ("IHDR", "IDAT", "IEND"))
        self.assertEqual(png.encode(image), data)

    def test_unsupported_formats_are_rejected(self):
        for depth, color, interlace in ((16, 6, 0), (8, 3, 0), (8, 0, 0), (8, 6, 1)):
            header = struct.pack(">IIBBBBB", 1, 1, depth, color, 0, 0, interlace)
            data = SIGNATURE + chunk(b"IHDR", header) + chunk(
                b"IDAT", zlib.compress(b"\x00" * 9)) + chunk(b"IEND", b"")
            with self.subTest(depth=depth, color=color, interlace=interlace):
                with self.assertRaises(ValueError):
                    png.decode(data)


class Transforms(unittest.TestCase):
    def test_rgb_gains_an_opaque_alpha_channel(self):
        image = png.Image(1, 1, 3, b"\x05\x06\x07")
        self.assertEqual(png.to_rgba(image), png.Image(1, 1, 4, b"\x05\x06\x07\xff"))

    def test_crop_takes_the_requested_rectangle(self):
        image = png.Image(3, 2, 3, bytes(range(18)))
        self.assertEqual(png.crop(image, 1, 1, 2, 1), png.Image(2, 1, 3, bytes(range(12, 18))))

    def test_box_downscale_averages_with_premultiplied_alpha(self):
        red, clear_blue = b"\xff\x00\x00\xff", b"\x00\x00\xff\x00"
        image = png.Image(2, 2, 4, red + clear_blue + red + clear_blue)
        self.assertEqual(png.downscale(image, 2).pixels, b"\xff\x00\x00\x80")

    def test_area_resize_handles_fractional_ratios(self):
        image = png.Image(3, 1, 3, b"\x00\x00\x00" + b"\x96\x96\x96" + b"\xff\xff\xff")
        resized = png.resize(image, 2, 1)
        self.assertEqual((resized.width, resized.height), (2, 1))
        self.assertEqual(resized.pixels, b"\x32\x32\x32\xdc\xdc\xdc")

    def test_alpha_dilation_grows_by_the_radius(self):
        alpha = bytearray(7 * 7)
        alpha[3 * 7 + 3] = 200
        grown = png.dilate(bytes(alpha), 7, 7, 2)
        rows = [grown[row * 7:(row + 1) * 7] for row in range(7)]
        self.assertEqual(rows[0], bytes(7))
        self.assertEqual(rows[3], bytes((0, 200, 200, 200, 200, 200, 0)))

    def test_mask_combines_rgb_and_alpha(self):
        image = png.Image(2, 1, 3, b"\x01\x02\x03\x04\x05\x06")
        self.assertEqual(png.apply_alpha(image, b"\x00\xff").pixels,
                         b"\x01\x02\x03\x00\x04\x05\x06\xff")

    def test_quarter_turn_rotates_clockwise(self):
        image = png.Image(2, 1, 3, b"\x01\x01\x01\x02\x02\x02")
        turned = png.rotate(image, 1)
        self.assertEqual((turned.width, turned.height), (1, 2))
        self.assertEqual(turned.pixels, b"\x01\x01\x01\x02\x02\x02")
        self.assertEqual(png.rotate(image, 2).pixels, b"\x02\x02\x02\x01\x01\x01")

    def test_grid_places_frames_left_to_right(self):
        frames = [png.Image(1, 1, 4, bytes((value,) * 4)) for value in (1, 2, 3)]
        sheet = png.grid(frames, 2)
        self.assertEqual((sheet.width, sheet.height), (2, 2))
        self.assertEqual(sheet.pixels, bytes((1,) * 4 + (2,) * 4 + (3,) * 4 + (0,) * 4))


if __name__ == "__main__":
    unittest.main()
