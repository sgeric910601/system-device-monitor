"""「我的照片」外觀用的照片：只存在本機 XDG 資料目錄，不上傳、不屬於發布內容。

選照片時依拍攝方向轉正、從中間裁成正方形、縮放成 512 × 512，再重新編碼成 PNG，
並只保留畫面需要的區塊（拍攝時間、地點、相機等照片資訊一律不留）；檔案權限 0600、資料夾 0700。
示範模式與模擬測試停用（enabled=False）：完全不讀寫這張照片，截圖只會出現內建的貓咪大頭照。
version 在換照片或刪除時加一，畫面依此重新載入。
"""
import logging
from pathlib import Path

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib

from sdm import paths
from sdm.constants import Companion
from sdm.core.jsonfile import write_bytes_atomic
from sdm.core.png_chunks import PngError, strip_metadata

LOG = logging.getLogger(__name__)
FILE_MODE = 0o600
DIRECTORY_MODE = 0o700
UNREADABLE = "讀不到這張照片，請換一張"
TOO_LARGE = "照片檔太大（超過 50 MB），請換一張"
DISABLED = "示範模式不使用自己的照片"
SAVE_FAILED = "無法儲存照片"


class PhotoError(Exception):
    """選的照片無法使用；訊息可以直接顯示給使用者。"""


def square_photo(source):
    """讀取、轉正、置中裁成正方形並縮成 PHOTO_PIXELS；回傳 Pixbuf。"""
    try:
        if Path(source).stat().st_size > Companion.PHOTO_MAX_BYTES:
            raise PhotoError(TOO_LARGE)
        side = Companion.PHOTO_DECODE_PIXELS
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(source), side, side, True)
    except (OSError, GLib.Error) as error:
        raise PhotoError(UNREADABLE) from error
    pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
    width, height = pixbuf.get_width(), pixbuf.get_height()
    side = min(width, height)
    square = pixbuf.new_subpixbuf((width - side) // 2, (height - side) // 2, side, side)
    size = Companion.PHOTO_PIXELS
    return square.scale_simple(size, size, GdkPixbuf.InterpType.HYPER)


def encode_png(pixbuf):
    ok, data = pixbuf.save_to_bufferv("png", [], [])
    if not ok:
        raise PhotoError(SAVE_FAILED)
    try:
        return strip_metadata(bytes(data))
    except PngError as error:
        raise PhotoError(SAVE_FAILED) from error


class PhotoStore:
    def __init__(self, path=None, enabled=True):
        self.path = Path(path) if path is not None else paths.DATA/Companion.PHOTO_FILE
        self.enabled, self.version = enabled, 0

    def has_photo(self):
        return self.enabled and self.path.is_file()

    def save(self, source):
        """換成 source 這張照片；失敗時拋出 PhotoError，原本的照片不變。"""
        if not self.enabled:
            raise PhotoError(DISABLED)
        data = encode_png(square_photo(source))
        try:
            self.path.parent.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=True)
            write_bytes_atomic(self.path, data, mode=FILE_MODE)
        except OSError as error:
            LOG.warning("Cannot save the companion photo: %s", type(error).__name__)
            raise PhotoError(SAVE_FAILED) from error
        self.version += 1

    def remove(self):
        if not self.enabled:
            return
        try:
            self.path.unlink(missing_ok=True)
        except OSError as error:
            LOG.warning("Cannot remove the companion photo: %s", type(error).__name__)
            return
        self.version += 1

    def load(self):
        """目前的照片（Pixbuf）；沒有照片、停用或讀不到時為 None。"""
        if not self.has_photo():
            return None
        try:
            return GdkPixbuf.Pixbuf.new_from_file(str(self.path))
        except GLib.Error as error:
            LOG.warning("Cannot load the companion photo: %s", error.message)
            return None
