#!/usr/bin/python3
"""Pure-Python PNG helpers for scripts/make_assets.py (development only, not shipped).

只用標準函式庫：8 位元 RGB／RGBA、不交錯的 PNG 解碼與編碼（只寫 IHDR、IDAT、IEND，
不寫任何附屬資訊），以及縮小、裁切、旋轉、透明度擴張與排成格狀。
編碼結果只取決於像素內容與 zlib 版本，重跑會得到相同的位元組。
"""
from pathlib import Path
import struct
import sys
from typing import NamedTuple
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sdm.core.png_chunks import SIGNATURE, chunks

COLOR_CHANNELS = {2: 3, 6: 4}       # PNG 色彩類型 → 每個像素的位元組數
CHANNEL_COLORS = {3: 2, 4: 6}
FILTERS = 5                         # None、Sub、Up、Average、Paeth
COMPRESSION = 9
OPAQUE = 255


class Image(NamedTuple):
    width: int
    height: int
    channels: int                   # 3（RGB）或 4（RGBA，非預乘）
    pixels: bytes                   # 由上而下、由左而右，沒有濾波位元組


def _header(data):
    found = chunks(data)
    width, height, depth, color, compression, method, interlace = struct.unpack(
        ">IIBBBBB", found[0][1])
    if depth != 8 or color not in COLOR_CHANNELS or compression or method or interlace:
        raise ValueError("Only 8-bit, non-interlaced RGB or RGBA PNG files are supported")
    return width, height, COLOR_CHANNELS[color], b"".join(body for kind, body in found
                                                           if kind == b"IDAT")


def _paeth(left, up, corner):
    estimate = left + up - corner
    distances = (abs(estimate - left), abs(estimate - up), abs(estimate - corner))
    return (left, up, corner)[distances.index(min(distances))]


def _unfilter_row(kind, row, previous, bpp):
    out = bytearray(row)
    for index in range(len(out)):
        left = out[index - bpp] if index >= bpp else 0
        up = previous[index]
        corner = previous[index - bpp] if index >= bpp else 0
        if kind == 1:
            out[index] = (out[index] + left) & 0xFF
        elif kind == 2:
            out[index] = (out[index] + up) & 0xFF
        elif kind == 3:
            out[index] = (out[index] + (left + up) // 2) & 0xFF
        elif kind == 4:
            out[index] = (out[index] + _paeth(left, up, corner)) & 0xFF
        elif kind:
            raise ValueError("Unknown row filter")
    return bytes(out)


def decode(data):
    width, height, channels, compressed = _header(data)
    raw, stride = zlib.decompress(compressed), width * channels
    if len(raw) != height * (stride + 1):
        raise ValueError("Unexpected image data length")
    rows, previous = [], bytes(stride)
    for row in range(height):
        start = row * (stride + 1)
        previous = _unfilter_row(raw[start], raw[start + 1:start + 1 + stride], previous,
                                 channels)
        rows.append(previous)
    return Image(width, height, channels, b"".join(rows))


def _filter_row(kind, row, previous, bpp):
    out = bytearray(len(row))
    for index, value in enumerate(row):
        left = row[index - bpp] if index >= bpp else 0
        up = previous[index]
        corner = previous[index - bpp] if index >= bpp else 0
        predictor = (0, left, up, (left + up) // 2, _paeth(left, up, corner))[kind]
        out[index] = (value - predictor) & 0xFF
    return bytes(out)


def _cost(filtered):
    """libpng 的經驗法則：把位元組當成有號數，絕對值總和越小越好壓縮。"""
    return sum(value if value < 128 else 256 - value for value in filtered)


def _best_row(row, previous, bpp, filter_type):
    kinds = range(FILTERS) if filter_type is None else (filter_type,)
    options = [(_cost(filtered), kind, filtered) for kind in kinds
               for filtered in (_filter_row(kind, row, previous, bpp),)]
    _, kind, filtered = min(options, key=lambda option: option[:2])
    return bytes((kind,)) + filtered


def _chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(
        ">I", zlib.crc32(kind + body))


def encode(image, filter_type=None):
    """只有 IHDR、IDAT、IEND；filter_type 為 None 時每列挑最小的濾波結果。"""
    stride, previous, rows = image.width * image.channels, None, []
    for row in range(image.height):
        current = image.pixels[row * stride:(row + 1) * stride]
        rows.append(_best_row(current, previous or bytes(stride), image.channels, filter_type))
        previous = current
    header = struct.pack(">IIBBBBB", image.width, image.height, 8,
                         CHANNEL_COLORS[image.channels], 0, 0, 0)
    return SIGNATURE + _chunk(b"IHDR", header) + _chunk(
        b"IDAT", zlib.compress(b"".join(rows), COMPRESSION)) + _chunk(b"IEND", b"")


def to_rgba(image):
    if image.channels == 4:
        return image
    pixels = bytearray()
    for index in range(0, len(image.pixels), 3):
        pixels += image.pixels[index:index + 3] + bytes((OPAQUE,))
    return Image(image.width, image.height, 4, bytes(pixels))


def crop(image, x, y, width, height):
    stride, size = image.width * image.channels, width * image.channels
    rows = (image.pixels[row * stride + x * image.channels:][:size]
            for row in range(y, y + height))
    return Image(width, height, image.channels, b"".join(rows))


def _weights(source, target):
    """每個輸出位置涵蓋的來源範圍與比重（面積平均）。"""
    scale, spans = source / target, []
    for index in range(target):
        start, end = index * scale, (index + 1) * scale
        spans.append([(position, min(end, position + 1) - max(start, position))
                      for position in range(int(start), min(source, int(-(-end // 1))))])
    return spans


def _average(image, sources):
    """sources 為（像素索引、比重）；RGBA 以預乘透明度平均，透明像素的顏色不會滲出。"""
    channels, pixels = image.channels, image.pixels
    total = sum(weight for _, weight in sources)
    if channels == 3:
        return bytes(round(sum(pixels[i * 3 + c] * w for i, w in sources) / total)
                     for c in range(3))
    alpha = sum(pixels[i * 4 + 3] * w for i, w in sources)
    if alpha == 0:
        return bytes(4)
    color = (round(sum(pixels[i * 4 + c] * pixels[i * 4 + 3] * w for i, w in sources) / alpha)
             for c in range(3))
    return bytes(color) + bytes((round(alpha / total),))


def resize(image, width, height):
    """面積平均縮放（主要用來縮小）。"""
    columns, rows = _weights(image.width, width), _weights(image.height, height)
    out = bytearray()
    for row_span in rows:
        for column_span in columns:
            sources = [(y * image.width + x, wy * wx) for y, wy in row_span
                       for x, wx in column_span]
            out += _average(image, sources)
    return Image(width, height, image.channels, bytes(out))


def downscale(image, factor):
    return resize(image, image.width // factor, image.height // factor)


def dilate(alpha, width, height, radius):
    """透明度往外擴張 radius 像素（正方形範圍的最大值，先橫向再縱向）。"""
    def spread(values, length):
        return [max(values[max(0, i - radius):i + radius + 1]) for i in range(length)]
    rows = [spread(alpha[r * width:(r + 1) * width], width) for r in range(height)]
    columns = [spread([rows[r][c] for r in range(height)], height) for c in range(width)]
    return bytes(columns[c][r] for r in range(height) for c in range(width))


def apply_alpha(image, alpha):
    pixels = bytearray()
    for index in range(image.width * image.height):
        pixels += image.pixels[index * 3:index * 3 + 3] + alpha[index:index + 1]
    return Image(image.width, image.height, 4, bytes(pixels))


def alpha_of(image):
    if image.channels == 3:
        return bytes((OPAQUE,)) * (image.width * image.height)
    return image.pixels[3::4]


def rotate(image, turns):
    """順時針轉 turns 個 90 度。"""
    for _ in range(turns % 4):
        size, out = image.channels, bytearray()
        for column in range(image.width):
            for row in range(image.height - 1, -1, -1):
                start = (row * image.width + column) * size
                out += image.pixels[start:start + size]
        image = Image(image.height, image.width, image.channels, bytes(out))
    return image


def grid(frames, columns):
    """同樣大小的畫格由左而右、由上而下排成一張圖；空格為透明。"""
    first = frames[0]
    rows = -(-len(frames) // columns)
    stride, out = first.width * 4, bytearray()
    blank = bytes(stride)
    for row in range(rows):
        cells = [to_rgba(frame) for frame in frames[row * columns:(row + 1) * columns]]
        for line in range(first.height):
            for column in range(columns):
                out += (cells[column].pixels[line * stride:(line + 1) * stride]
                        if column < len(cells) else blank)
    return Image(first.width * columns, first.height * rows, 4, bytes(out))
