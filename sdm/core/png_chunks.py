"""PNG 區塊檢查與去除附屬資訊（不載入 GTK，只用標準函式庫）。

公開的圖檔與使用者自選的照片只保留畫面本身需要的區塊（IHDR、PLTE、tRNS、IDAT、IEND），
文字（tEXt、zTXt、iTXt）、拍攝資訊（eXIf）、時間（tIME）、色彩描述檔（iCCP，可能含名稱）
以及其他附屬區塊一律移除；隱私檢查也依同一份清單判斷。
"""
import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"
ALLOWED = frozenset({b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND"})
LENGTH_BYTES = 4
TYPE_BYTES = 4
CRC_BYTES = 4
OVERHEAD = LENGTH_BYTES + TYPE_BYTES + CRC_BYTES


class PngError(ValueError):
    """不是完整、正確的 PNG（簽章、長度、檢查碼或結尾有誤）。"""


def _read_chunk(data, position):
    """回傳（類型、內容、下一個位置）；長度或檢查碼不對時拋出 PngError。"""
    if position + OVERHEAD > len(data):
        raise PngError("Truncated chunk")
    length, = struct.unpack(">I", data[position:position + LENGTH_BYTES])
    start = position + LENGTH_BYTES + TYPE_BYTES
    end = start + length
    if end + CRC_BYTES > len(data):
        raise PngError("Truncated chunk")
    kind = data[position + LENGTH_BYTES:start]
    crc, = struct.unpack(">I", data[end:end + CRC_BYTES])
    if zlib.crc32(kind + data[start:end]) != crc:
        raise PngError("Chunk checksum mismatch")
    return kind, data[start:end], end + CRC_BYTES


def chunks(data):
    """依序回傳（類型、內容）；第一個必須是 IHDR，最後一個是 IEND，之後不能再有資料。"""
    if not data.startswith(SIGNATURE):
        raise PngError("Not a PNG file")
    found, position = [], len(SIGNATURE)
    while position < len(data):
        kind, body, position = _read_chunk(data, position)
        found.append((kind, body))
        if kind == b"IEND":
            break
    if not found or found[0][0] != b"IHDR" or found[-1][0] != b"IEND" or position != len(data):
        raise PngError("Incomplete PNG structure")
    return tuple(found)


def chunk_types(data):
    return tuple(kind.decode("latin-1") for kind, _ in chunks(data))


def extra_chunks(data):
    """畫面本身不需要的區塊類型（排序、不重複）；空的代表沒有任何附屬資訊。"""
    return tuple(sorted({kind.decode("latin-1") for kind, _ in chunks(data)
                         if kind not in ALLOWED}))


def _encode(kind, body):
    return (struct.pack(">I", len(body)) + kind + body
            + struct.pack(">I", zlib.crc32(kind + body)))


def strip_metadata(data):
    """只保留 ALLOWED 中的區塊重新組成 PNG；畫面資料原封不動。"""
    kept = (_encode(kind, body) for kind, body in chunks(data) if kind in ALLOWED)
    return SIGNATURE + b"".join(kept)
