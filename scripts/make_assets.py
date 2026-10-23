#!/usr/bin/python3
"""Regenerate assets/ from the reference project's Resources folder (development only).

開發用，不屬於執行期：只用標準函式庫，ffmpeg 為選用（解 HEIC 大頭照與影片畫格）。
- assets/app/<顏色>-<尺寸>.png：薄荷綠取自 AppIcon.icns 的 ic09（512 px PNG），其他三色取自
  AppIcons/icon-<顏色>.png；128 px 由 512 px 以面積平均縮小。
- assets/companion/：貓咪三張靜態圖原樣重新編碼；cat-headshot.png 由 companion-photo.heic
  抽出 HEVC 交給 ffmpeg 解碼，沒有 ffmpeg 或解不開時改從 icon-cat.png 裁切（眼睛位置的
  座標只對 HEIC 那張有效）。
- --frames 資料夾：把 .mov 解成 RGB 畫格，套上靜態圖擴張 2 px 的透明度，排成
  <名稱>-frames.png（每列 FRAME_COLUMNS 格），供比較「預先算好的畫格」做法。
所有輸出都重新編碼成只含 IHDR、IDAT、IEND 的 PNG，重跑得到相同的位元組。
"""
import argparse
import hashlib
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import asset_png as png

COLORS = ("mint", "peach", "night", "sky")
SIZES = (128, 512)
COMPANION_IMAGES = ("companion-charging", "companion-resting", "pet-cat")
HEADSHOT = "cat-headshot.png"
HEADSHOT_SIZE = 224
FALLBACK_CROP = (0.83, 0.234, 0.278)    # icon-cat.png 中頭部的正方形：左、上（比例）與邊長（高度比例）
FRAME_COLUMNS = 10
MASK_DILATION = 2
ICNS_LARGE = b"ic09"
START_CODE = b"\x00\x00\x00\x01"
FFMPEG_TIMEOUT = 120
SWS_FLAGS = "accurate_rnd+full_chroma_int+bitexact"


def icns_entry(data, kind):
    """回傳 icns 中指定類型的內容（ic09 為 512 px PNG）。"""
    if data[:4] != b"icns":
        raise ValueError("Not an icns file")
    position = 8
    while position + 8 <= len(data):
        entry, length = data[position:position + 4], struct.unpack(
            ">I", data[position + 4:position + 8])[0]
        if length < 8:
            raise ValueError("Invalid icns entry")
        if entry == kind:
            return data[position + 8:position + length]
        position += length
    raise ValueError("Missing icns entry " + kind.decode())


def boxes(data, start=0, end=None):
    """ISO BMFF 方塊：類型對應（內容起點、內容終點），同類型只記第一個。"""
    found, position, end = {}, start, len(data) if end is None else end
    while position + 8 <= end:
        size, kind = struct.unpack(">I", data[position:position + 4])[0], data[position + 4:position + 8]
        header = 8
        if size == 1:
            size, header = struct.unpack(">Q", data[position + 8:position + 16])[0], 16
        if size < header or position + size > end:
            raise ValueError("Invalid box")
        found.setdefault(kind, (position + header, position + size))
        position += size
    return found


def _primary(data, meta):
    """主要項目必須是單一張 hvc1（不支援格狀拼接）；回傳項目代號。"""
    pitm = data[meta[b"pitm"][0] + 4:][:2]
    item = struct.unpack(">H", pitm)[0]
    start, end = meta[b"iinf"]
    count_size = 2 if data[start] == 0 else 4
    infe = boxes(data, start + 4 + count_size, end)[b"infe"]
    item_id, _, item_type = struct.unpack(">HH4s", data[infe[0] + 4:infe[0] + 12])
    if data[infe[0]] != 2 or item_id != item or item_type != b"hvc1":
        raise ValueError("Unsupported HEIF item")
    return item


def _properties(data, meta):
    """hvcC 的參數集、ispe 的寬高與 irot 的旋轉（逆時針幾個 90 度）。"""
    ipco = boxes(data, *meta[b"iprp"])[b"ipco"]
    props = boxes(data, *ipco)
    start, end = props[b"hvcC"]
    units, position = [], start + 23
    for _ in range(data[start + 22]):
        count = struct.unpack(">H", data[position + 1:position + 3])[0]
        position += 3
        for _ in range(count):
            length = struct.unpack(">H", data[position:position + 2])[0]
            units.append(data[position + 2:position + 2 + length])
            position += 2 + length
    width, height = struct.unpack(">II", data[props[b"ispe"][0] + 4:][:8])
    turns = data[props[b"irot"][0]] & 3 if b"irot" in props else 0
    return units, (width, height), turns, (data[start + 21] & 3) + 1


def _item_data(data, meta, item):
    """iloc 第 0 版：每個範圍是檔案中的位置與長度。"""
    start = meta[b"iloc"][0]
    if data[start] != 0:
        raise ValueError("Unsupported iloc version")
    sizes = data[start + 4]
    if sizes != 0x44 or data[start + 5] >> 4:
        raise ValueError("Unsupported iloc layout")
    position = start + 8
    for _ in range(struct.unpack(">H", data[start + 6:start + 8])[0]):
        item_id, _, extents = struct.unpack(">HHH", data[position:position + 6])
        position += 6
        spans = [struct.unpack(">II", data[position + 8 * i:position + 8 * i + 8])
                 for i in range(extents)]
        position += 8 * extents
        if item_id == item:
            return b"".join(data[offset:offset + length] for offset, length in spans)
    raise ValueError("Missing HEIF item data")


def heif_hevc(data):
    """把 HEIC 的主要影像轉成 Annex B 的 HEVC 串流；回傳（串流、寬高、順時針轉幾次）。"""
    try:
        meta_start, meta_end = boxes(data)[b"meta"]
        meta = boxes(data, meta_start + 4, meta_end)
        item = _primary(data, meta)
        units, size, turns, length_size = _properties(data, meta)
        payload, position, slices = _item_data(data, meta, item), 0, []
    except (KeyError, IndexError, struct.error) as error:
        raise ValueError("Unsupported HEIF file") from error
    while position < len(payload):
        length = int.from_bytes(payload[position:position + length_size], "big")
        slices.append(payload[position + length_size:position + length_size + length])
        position += length_size + length
    stream = b"".join(START_CODE + unit for unit in units + slices)
    return stream, size, (4 - turns) % 4


def ffmpeg_raw(ffmpeg, arguments, stdin=None):
    """以 ffmpeg 輸出 rgb24 原始像素；失敗時拋出 ValueError。"""
    command = [ffmpeg, "-v", "error", "-nostdin", *arguments, "-sws_flags", SWS_FLAGS,
               "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    try:
        result = subprocess.run(command, input=stdin, capture_output=True, check=True,
                                timeout=FFMPEG_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("ffmpeg failed") from error
    return result.stdout


def heic_headshot(path, ffmpeg):
    stream, (width, height), turns = heif_hevc(path.read_bytes())
    raw = ffmpeg_raw(ffmpeg, ["-f", "hevc", "-i", "pipe:0", "-frames:v", "1"], stream)
    if len(raw) != width * height * 3:
        raise ValueError("Unexpected decoded size")
    return png.rotate(png.Image(width, height, 3, raw), turns)


def fallback_headshot(path):
    image = png.decode(path.read_bytes())
    left, top, side = FALLBACK_CROP
    side_px = max(1, int(side * image.height))
    x = min(int(left * image.width), image.width - side_px)
    y = min(int(top * image.height), image.height - side_px)
    square = png.crop(image, x, y, side_px, side_px)
    size = min(side_px, HEADSHOT_SIZE)
    return png.resize(square, size, size)


def headshot(ref, ffmpeg):
    if ffmpeg:
        try:
            return heic_headshot(ref/"Companion/companion-photo.heic", ffmpeg)
        except ValueError as error:
            print(f"HEIC headshot unavailable ({error}); using the icon-cat.png crop",
                  file=sys.stderr)
    return fallback_headshot(ref/"icon-cat.png")


def icon_sources(ref):
    sources = {"mint": png.decode(icns_entry((ref/"AppIcon.icns").read_bytes(), ICNS_LARGE))}
    for color in COLORS[1:]:
        sources[color] = png.decode((ref/"AppIcons"/f"icon-{color}.png").read_bytes())
    return sources


def write(path, image):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png.encode(image))
    return path


def build(ref, out, sizes=SIZES, ffmpeg=None):
    """產生全部素材並回傳寫出的路徑（依名稱排序）。"""
    written = []
    largest = max(sizes)
    for color, source in sorted(icon_sources(ref).items()):
        if source.width != largest or source.height != largest:
            raise ValueError(f"{color} icon must be {largest} px")
        for size in sizes:
            image = source if size == largest else png.downscale(source, largest // size)
            written.append(write(out/"app"/f"{color}-{size}.png", image))
    for name in COMPANION_IMAGES:
        image = png.decode((ref/"Companion"/f"{name}.png").read_bytes())
        written.append(write(out/"companion"/f"{name}.png", image))
    written.append(write(out/"companion"/HEADSHOT, headshot(ref, ffmpeg)))
    return sorted(written)


def video_frames(ffmpeg, path, width, height):
    raw = ffmpeg_raw(ffmpeg, ["-i", str(path)])
    size = width * height * 3
    if not raw or len(raw) % size:
        raise ValueError("Unexpected frame data")
    return [png.Image(width, height, 3, raw[i:i + size]) for i in range(0, len(raw), size)]


def build_frames(ref, out, ffmpeg):
    """影片畫格套上靜態圖（第一格）擴張 2 px 的透明度，排成一張格狀圖。"""
    written = []
    for name in COMPANION_IMAGES:
        still = png.decode((ref/"Companion"/f"{name}.png").read_bytes())
        mask = png.dilate(png.alpha_of(still), still.width, still.height, MASK_DILATION)
        frames = [png.apply_alpha(frame, mask) for frame in video_frames(
            ffmpeg, ref/"Companion"/f"{name}.mov", still.width, still.height)]
        written.append(write(out/f"{name}-frames.png", png.grid(frames, FRAME_COLUMNS)))
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", type=Path, required=True,
                        help="The reference project's Resources folder")
    parser.add_argument("--out", type=Path, default=ROOT/"assets")
    parser.add_argument("--frames", type=Path, help="Also write video frame sheets here")
    args = parser.parse_args(argv)
    ffmpeg = shutil.which("ffmpeg")
    written = build(args.ref, args.out, ffmpeg=ffmpeg)
    if args.frames:
        if not ffmpeg:
            parser.error("--frames needs ffmpeg")
        written += build_frames(args.ref, args.frames, ffmpeg)
    for path in written:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        print(f"{path.stat().st_size:>8}  {digest}  {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
