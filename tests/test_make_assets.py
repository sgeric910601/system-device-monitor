"""scripts/make_assets.py：以人工建立的參考資源產生素材，重跑結果完全相同、沒有附屬資訊。

不需要 ffmpeg：HEIC 解不開時改用全身照裁切的大頭照；HEIF 容器的解析另外以合成檔測試。
"""
import hashlib
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

from sdm.core.png_chunks import extra_chunks
from tests.png_samples import EXIF, TEXT, png_bytes

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/"scripts"/(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


make = load_script("make_assets")
SIZES = (4, 16)


def icns(entries):
    body = b"".join(kind + struct.pack(">I", len(data) + 8) + data for kind, data in entries)
    return b"icns" + struct.pack(">I", len(body) + 8) + body


def reference(base):
    """與參考專案 Resources 相同的資料夾結構，內容是小張的合成圖。"""
    (base/"AppIcons").mkdir(parents=True)
    (base/"Companion").mkdir()
    (base/"AppIcon.icns").write_bytes(icns([(b"ic07", png_bytes(4, 4)),
                                            (b"ic09", png_bytes(16, 16, (EXIF,)))]))
    for color, pixel in (("peach", b"\xf3\xb4\x86\xff"), ("night", b"\x14\x1c\x38\xff"),
                         ("sky", b"\x5b\x93\xd3\xff")):
        (base/"AppIcons"/f"icon-{color}.png").write_bytes(png_bytes(16, 16, (TEXT,), pixel))
    for name in ("companion-charging", "companion-resting", "pet-cat"):
        (base/"Companion"/f"{name}.png").write_bytes(png_bytes(6, 5, (EXIF,)))
    (base/"Companion/companion-photo.heic").write_bytes(b"not a heif file")
    (base/"icon-cat.png").write_bytes(png_bytes(40, 24, (TEXT,)))
    return base


def box(kind, body):
    return struct.pack(">I", len(body) + 8) + kind + body


def full_box(kind, body, version=0):
    return box(kind, bytes((version, 0, 0, 0)) + body)


def heif(nal_units, slice_data):
    """只有一個 hvc1 主要項目的最小 HEIF：參數集放在 hvcC，影像資料放在 mdat。"""
    arrays = b"".join(bytes((32 + index,)) + struct.pack(">HH", 1, len(unit)) + unit
                      for index, unit in enumerate(nal_units))
    hvcc = box(b"hvcC", bytes(21) + b"\x03" + bytes((len(nal_units),)) + arrays)
    ispe = full_box(b"ispe", struct.pack(">II", 2, 2))
    ipco = box(b"ipco", hvcc + ispe)
    ipma = full_box(b"ipma", struct.pack(">IHB", 1, 1, 2) + b"\x81\x82")
    infe = full_box(b"infe", struct.pack(">HH", 1, 0) + b"hvc1" + b"\x00", version=2)
    meta_children = (full_box(b"pitm", struct.pack(">H", 1))
                     + full_box(b"iinf", struct.pack(">H", 1) + infe)
                     + box(b"iprp", ipco + ipma))
    payload = struct.pack(">I", len(slice_data)) + slice_data
    ftyp = box(b"ftyp", b"heic" + bytes(4) + b"mif1heic")

    def iloc(offset):
        return full_box(b"iloc", b"\x44\x00" + struct.pack(">HHHHII", 1, 1, 0, 1, offset,
                                                             len(payload)))
    head_size = len(ftyp) + len(full_box(b"meta", meta_children + iloc(0))) + 8
    meta = full_box(b"meta", meta_children + iloc(head_size))
    return ftyp + meta + box(b"mdat", payload)


def hashes(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.rglob("*")) if path.is_file()}


class Build(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.ref = reference(self.base/"Resources")

    def build(self, name):
        out = self.base/name
        make.build(self.ref, out, sizes=SIZES, ffmpeg=None)
        return out

    def test_writes_every_asset_without_metadata(self):
        out = self.build("first")
        expected = {f"app/{color}-{size}.png" for color in make.COLORS for size in SIZES}
        expected |= {f"companion/{name}.png" for name in make.COMPANION_IMAGES}
        expected |= {"companion/" + make.HEADSHOT}
        self.assertEqual(set(hashes(out)), expected)
        for path in out.rglob("*.png"):
            with self.subTest(path=path.name):
                self.assertEqual(extra_chunks(path.read_bytes()), ())

    def test_rerun_produces_identical_bytes(self):
        self.assertEqual(hashes(self.build("first")), hashes(self.build("second")))

    def test_icon_sizes_come_from_the_large_source(self):
        out = self.build("first")
        for size in SIZES:
            image = make.png.decode((out/f"app/mint-{size}.png").read_bytes())
            self.assertEqual((image.width, image.height), (size, size))

    def test_headshot_falls_back_to_a_square_crop(self):
        image = make.png.decode((self.build("first")/"companion"/make.HEADSHOT).read_bytes())
        self.assertEqual(image.width, image.height)


class Containers(unittest.TestCase):
    def test_icns_entry_is_found_by_type(self):
        data = icns([(b"ic07", b"small"), (b"ic09", b"large")])
        self.assertEqual(make.icns_entry(data, b"ic09"), b"large")
        with self.assertRaises(ValueError):
            make.icns_entry(data, b"ic10")
        with self.assertRaises(ValueError):
            make.icns_entry(b"nope" + data[4:], b"ic09")

    def test_heif_primary_item_becomes_an_annex_b_stream(self):
        stream, size, turns = make.heif_hevc(heif((b"VPS", b"SPS", b"PPS"), b"SLICE"))
        start = b"\x00\x00\x00\x01"
        self.assertEqual(stream, start + b"VPS" + start + b"SPS" + start + b"PPS" + start + b"SLICE")
        self.assertEqual((size, turns), ((2, 2), 0))

    def test_unsupported_heif_is_rejected(self):
        for data in (b"", b"\x00\x00\x00\x08ftyp", heif((b"V",), b"S").replace(b"hvc1", b"grid")):
            with self.subTest(size=len(data)):
                with self.assertRaises(ValueError):
                    make.heif_hevc(data)


if __name__ == "__main__":
    unittest.main()
