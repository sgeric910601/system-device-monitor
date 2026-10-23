"""貓咪動畫的兩種做法（Companion.ANIMATION 選擇，預設 procedural）：

- procedural：只用透明背景的靜態圖（assets/companion/*.png），由程式畫呼吸
  （以腳底為基準放大 1.00–1.02 倍，身體再往上多伸展一點，腳底不動）；
  粒子（Z、火花）由 companion_draw 畫。
  邊緣與透明度和原圖完全相同，放大也不會有黑邊。
- frames：預先算好的畫格圖（<名稱>-frames.png，由 scripts/make_assets.py --frames 產生，
  預設不隨程式發布）。找不到畫格圖時自動改用 procedural。
不播放時（看不到、省電模式）兩種做法都畫第一格的靜態圖。圖片只在第一次用到時載入一次。
"""
import logging
from pathlib import Path
from typing import NamedTuple

import cairo
import gi
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib

from sdm import paths
from sdm.constants import Companion
from sdm.core.sprite_motion import breath, strip_index

LOG = logging.getLogger(__name__)
COMPANION_DIR = "assets/companion"
PROCEDURAL, FRAMES = "procedural", "frames"
FRAMES_SUFFIX = "-frames"
OPAQUE_ALPHA = 128              # 找腳底時，透明度至少一半才算看得到


class FrameSheet(NamedTuple):
    surface: object             # 整張畫格圖
    width: int                  # 每格大小
    height: int
    columns: int
    count: int


def surface_from_pixbuf(pixbuf):
    """轉成 Cairo 圖面（不需要顯示器，離屏繪製與測試也能用）。"""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, pixbuf.get_width(), pixbuf.get_height())
    context = cairo.Context(surface)
    Gdk.cairo_set_source_pixbuf(context, pixbuf, 0, 0)
    context.paint()
    surface.flush()
    return surface


def lowest_opaque_row(pixbuf):
    """最下面一列有夠不透明像素的下緣（0–1）；沒有透明度的圖片為 1。"""
    if not pixbuf.get_has_alpha():
        return 1.0
    data, stride, height = pixbuf.get_pixels(), pixbuf.get_rowstride(), pixbuf.get_height()
    row_bytes = pixbuf.get_width() * pixbuf.get_n_channels()
    for row in range(height - 1, -1, -1):
        alphas = data[row * stride + 3:row * stride + row_bytes:4]
        if max(alphas, default=0) >= OPAQUE_ALPHA:
            return (row + 1) / height
    return 1.0


class Images:
    """assets/companion 的圖片；第一次用到才載入，之後重複使用同一個 Cairo 圖面。"""

    def __init__(self, root=None):
        self.folder = Path(root or paths.ROOT)/COMPANION_DIR
        self.pixbufs, self.surfaces, self.sheets = {}, {}, {}
        self.derived = {}       # 由圖片算出、只需要算一次的資料（例如眼皮的顏色）

    def pixbuf(self, name):
        if name not in self.pixbufs:
            path = self.folder/(name + ".png")
            try:
                self.pixbufs[name] = GdkPixbuf.Pixbuf.new_from_file(str(path))
            except GLib.Error:
                if path.exists():
                    LOG.warning("Cannot load companion image %s", path.name)
                self.pixbufs[name] = None
        return self.pixbufs[name]

    def surface(self, name):
        if name not in self.surfaces:
            pixbuf = self.pixbuf(name)
            self.surfaces[name] = None if pixbuf is None else surface_from_pixbuf(pixbuf)
        return self.surfaces[name]

    def feet(self, name):
        """圖中最下面一列不透明像素的位置（高度的比例），呼吸以這裡為基準；只算一次。"""
        key = ("feet", name)
        if key not in self.derived:
            pixbuf = self.pixbuf(name)
            self.derived[key] = 1.0 if pixbuf is None else lowest_opaque_row(pixbuf)
        return self.derived[key]

    def frames(self, name):
        """name-frames.png 依靜態圖的大小切成格；沒有畫格圖或大小對不上時為 None。"""
        if name not in self.sheets:
            still, sheet = self.pixbuf(name), self.pixbuf(name + FRAMES_SUFFIX)
            self.sheets[name] = None
            if still is not None and sheet is not None:
                width, height = still.get_width(), still.get_height()
                columns, rows = sheet.get_width() // width, sheet.get_height() // height
                if columns and rows:
                    self.sheets[name] = FrameSheet(surface_from_pixbuf(sheet), width, height,
                                                   columns, columns * rows)
        return self.sheets[name]


def paint_scaled(cr, surface, rect, source_rect=None):
    """把 surface（或其中 source_rect 那一塊）縮放畫進 rect。"""
    sx, sy, sw, sh = source_rect or (0, 0, surface.get_width(), surface.get_height())
    cr.save()
    cr.rectangle(*rect)
    cr.clip()
    cr.translate(rect.x, rect.y)
    cr.scale(rect.width / sw, rect.height / sh)
    cr.set_source_surface(surface, -sx, -sy)
    cr.get_source().set_filter(cairo.FILTER_GOOD)
    cr.paint()
    cr.restore()


def draw_breathing(cr, surface, rect, t, feet=1.0):
    """以腳底（下緣中央；feet 為腳底在圖中的高度比例）為基準放大；起伏是往上多伸展一點，腳底不離地。"""
    scale, bob = breath(t)
    feet_x, feet_y = rect.x + rect.width / 2, rect.y + rect.height * feet
    cr.save()
    cr.translate(feet_x, feet_y)
    cr.scale(scale, scale - bob)
    cr.translate(-feet_x, -feet_y)
    cr.set_source_surface(surface, 0, 0)
    matrix = cairo.Matrix(surface.get_width() / rect.width, 0, 0,
                          surface.get_height() / rect.height, 0, 0)
    matrix.translate(-rect.x, -rect.y)
    cr.get_source().set_matrix(matrix)
    cr.get_source().set_filter(cairo.FILTER_GOOD)
    cr.rectangle(*rect)
    cr.fill()
    cr.restore()


def draw_frame(cr, sheet, rect, t):
    index = strip_index(t, sheet.count)
    row, column = divmod(index, sheet.columns)
    paint_scaled(cr, sheet.surface, rect,
                 (column * sheet.width, row * sheet.height, sheet.width, sheet.height))


def draw_sprite(cr, images, name, rect, t, mode=None):
    """畫一張貓咪圖；回傳 True 表示用了預先算好的畫格（粒子已包含在畫格中，不另外畫）。"""
    mode = mode or Companion.ANIMATION
    if mode == FRAMES and t > 0:
        sheet = images.frames(name)
        if sheet is not None:
            draw_frame(cr, sheet, rect, t)
            return True
    surface = images.surface(name)
    if surface is not None:
        draw_breathing(cr, surface, rect, t, images.feet(name))
    return False
