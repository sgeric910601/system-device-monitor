"""「我的照片」外觀用的照片：只存在本機 XDG 資料目錄，不上傳、不屬於發布內容。

選照片時依拍攝方向轉正、從中間裁成正方形、縮放成 512 × 512，再重新編碼成 PNG，
並只保留畫面需要的區塊（拍攝時間、地點、相機等照片資訊一律不留）；檔案權限 0600。
資料夾不存在時以 0700 建立；已經存在時（例如耗電歷史先建立的）不改它的權限。

解碼前先檢查（不合格的完全不解碼，避免一張宣告超大尺寸的小檔把記憶體吃光）：
- 以不跟隨符號連結、不會卡在管線上的方式開啟，必須是一般檔案，不超過 50 MB；
- 檢查與解碼都透過同一個已開啟的檔案描述子，檢查完不會被換成別的檔案；
- 格式只接受 PNG、JPEG、WebP、GIF、BMP，宣告的尺寸不超過 1 億像素、任一邊不超過 16384。
解碼與重新編碼可以在背景執行緒進行（save_async），結果回到主執行緒才寫檔。
示範模式與模擬測試停用（enabled=False）：完全不讀寫這張照片，截圖只會出現內建的貓咪大頭照。
存放位置是符號連結或不是一般檔案時當作沒有照片，也不刪除它。
version 在換照片或刪除時加一，畫面依此重新載入。
"""
import logging
import os
from pathlib import Path
import stat
import threading

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
OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
DESCRIPTOR_PATH = "/proc/self/fd/{}"
UNREADABLE = "讀不到這張照片，請換一張"
TOO_LARGE = "照片檔太大（超過 50 MB），請換一張"
TOO_MANY_PIXELS = "照片的尺寸太大（超過 1 億像素或邊長超過 16384），請換一張"
UNSUPPORTED = "只能使用 PNG、JPEG、WebP、GIF 或 BMP 照片"
DISABLED = "示範模式不使用自己的照片"
SAVE_FAILED = "無法儲存照片"


class PhotoError(Exception):
    """選的照片無法使用；訊息可以直接顯示給使用者。"""


def open_regular(source):
    """開啟 source 並回傳檔案描述子；不是一般檔案、太大或打不開時拋出 PhotoError。"""
    try:
        descriptor = os.open(source, OPEN_FLAGS)
    except OSError as error:
        raise PhotoError(UNREADABLE) from error
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_size > Companion.PHOTO_MAX_BYTES:
        os.close(descriptor)
        raise PhotoError(UNREADABLE if not stat.S_ISREG(info.st_mode) else TOO_LARGE)
    return descriptor


def check_image(path):
    """只讀標頭：格式與宣告的尺寸都要在範圍內。"""
    image_format, width, height = GdkPixbuf.Pixbuf.get_file_info(path)
    if image_format is None or width <= 0 or height <= 0:
        raise PhotoError(UNREADABLE)
    if image_format.get_name() not in Companion.PHOTO_FORMATS:
        raise PhotoError(UNSUPPORTED)
    if width * height > Companion.PHOTO_MAX_PIXELS or max(width, height) > Companion.PHOTO_MAX_SIDE:
        raise PhotoError(TOO_MANY_PIXELS)


def decode(path):
    """解碼時先縮到不超過 PHOTO_DECODE_PIXELS（保持比例），大照片也不佔太多記憶體。"""
    side = Companion.PHOTO_DECODE_PIXELS
    return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, side, side, True)


def square_photo(source):
    """檢查後讀取、轉正、置中裁成正方形並縮成 PHOTO_PIXELS；回傳 Pixbuf。"""
    descriptor = open_regular(source)
    try:
        path = DESCRIPTOR_PATH.format(descriptor)
        check_image(path)
        pixbuf = decode(path)
    except GLib.Error as error:
        raise PhotoError(UNREADABLE) from error
    finally:
        os.close(descriptor)
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


def start_thread(work):
    threading.Thread(target=work, name="companion-photo", daemon=True).start()


def regular_file(path):
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


class PhotoStore:
    def __init__(self, path=None, enabled=True):
        self.path = Path(path) if path is not None else paths.DATA/Companion.PHOTO_FILE
        self.enabled, self.version = enabled, 0

    def has_photo(self):
        return self.enabled and regular_file(self.path)

    def encode(self, source):
        """檢查、解碼並重新編碼（可在背景執行緒）；回傳要存的 PNG 位元組。"""
        if not self.enabled:
            raise PhotoError(DISABLED)
        return encode_png(square_photo(source))

    def store(self, data):
        try:
            self.path.parent.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=True)
            write_bytes_atomic(self.path, data, mode=FILE_MODE)
        except OSError as error:
            LOG.warning("Cannot save the companion photo: %s", type(error).__name__)
            raise PhotoError(SAVE_FAILED) from error
        self.version += 1

    def save(self, source):
        """換成 source 這張照片；失敗時拋出 PhotoError，原本的照片不變。"""
        self.store(self.encode(source))

    def save_async(self, source, done, call_soon=GLib.idle_add, start=start_thread):
        """在背景解碼；完成後由 call_soon 回到主執行緒寫檔，再把錯誤訊息（成功時為 None）交給 done。"""
        def work():
            try:
                data, message = self.encode(source), None
            except PhotoError as error:
                data, message = None, str(error)
            call_soon(lambda: self.finish(data, message, done))
        start(work)

    def finish(self, data, message, done):
        if message is None:
            try:
                self.store(data)
            except PhotoError as error:
                message = str(error)
        done(message)
        return False

    def remove(self):
        if not self.enabled or not regular_file(self.path):
            return
        try:
            self.path.unlink()
        except OSError as error:
            LOG.warning("Cannot remove the companion photo: %s", type(error).__name__)
            return
        self.version += 1

    def load(self):
        """目前的照片（Pixbuf）；沒有照片、停用、不是一般檔案或讀不到時為 None。"""
        if not self.has_photo():
            return None
        try:
            return GdkPixbuf.Pixbuf.new_from_file(str(self.path))
        except GLib.Error as error:
            LOG.warning("Cannot load the companion photo: %s", error.message)
            return None
