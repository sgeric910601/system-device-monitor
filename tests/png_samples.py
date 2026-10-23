"""測試用的最小 PNG：以標準函式庫產生，可加上任意的附屬區塊（例如含文字或拍攝資訊的區塊）。"""
import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"
RGBA = 6


def chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def png_bytes(width=2, height=2, extra=(), pixel=b"\x10\x20\x30\xff"):
    """width × height 的單色 RGBA PNG；extra 為（類型、內容）放在 IHDR 之後。"""
    header = struct.pack(">IIBBBBB", width, height, 8, RGBA, 0, 0, 0)
    rows = b"".join(b"\x00" + pixel * width for _ in range(height))
    parts = [chunk(b"IHDR", header)]
    parts += [chunk(kind, body) for kind, body in extra]
    parts += [chunk(b"IDAT", zlib.compress(rows)), chunk(b"IEND", b"")]
    return SIGNATURE + b"".join(parts)


TEXT = (b"tEXt", b"Comment\x00synthetic note")
EXIF = (b"eXIf", b"MM\x00\x2a\x00\x00\x00\x08synthetic")
TIME = (b"tIME", bytes(7))
SRGB = (b"sRGB", b"\x00")
