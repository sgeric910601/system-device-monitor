"""充電夥伴的圖案（Cairo）：電源頁與桌面寵物共用，只依 Scene 畫圖，不保存狀態。

- 貓咪：電源頁用 companion-charging／companion-resting（充電時躺在充電墊上），
  桌面寵物用解析度較高的 pet-cat，充電墊與上方的充電標示用向量畫（參考專案 DesktopPetCompanion）。
  休息時頭上飄 Z，充電時充電墊旁有火花；呼吸由 sprite 畫。
- 電池夥伴：有表情的電池，底色是目前的電量；充電時微笑、充飽很開心、電量低擔心、
  很忙時流汗、休息時閉眼打瞌睡（參考專案 BatteryMascot）。
- 電量條：膠囊形電量，充電時有光點往右流。
- 貓咪大頭照與我的照片：照片裁成圓形，外圈是電量環；大頭照的眼睛可以跟著游標、眨眼。
設計大小與參考專案相同：寬 48、高 50 的單位，依實際寬度等比例縮放。
"""
import math
from typing import NamedTuple, Optional

import cairo
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

from sdm.core.companion import BUSY, CHARGING_MOOD, CRITICAL, FULL_MOOD, GOOD, LOW, WARNING
from sdm.core.pet_geometry import Rect
from sdm.core.pet_motion import eye_circles, iris_offset
from sdm.core.sprite_motion import sparks, zzz
from sdm.ui import theme
from sdm.ui.sprite import draw_sprite, surface_from_pixbuf

DESIGN_WIDTH, DESIGN_HEIGHT = 48, 50
PET_BASE = 176                  # 桌面寵物 100% 的邊長；以下桌面寵物的尺寸都以此為單位
PET_FIGURE_RATIO = 140 / 176    # 桌面寵物：貓咪以外的外觀寬度
CAT_IMAGES = {True: "companion-charging", False: "companion-resting"}
PET_CAT, HEADSHOT = "pet-cat", "cat-headshot"
DEFAULT_ASPECT = 152 / 160
TONES = {GOOD: theme.rgb(theme.GOOD), WARNING: theme.rgb(theme.WARNING),
         CRITICAL: theme.rgb(theme.CRITICAL)}
COLORS = {key: theme.rgb(value) for key, value in theme.COMPANION.items()}
FALLBACK_GRAY = (0.5, 0.5, 0.5)
BAR_TRIP_SECONDS = 1.6
LID_SOLID = 0.9                 # 眼皮到半徑的 1.17 倍都不透明（蓋住整個眼窩），之後柔邊
LID_PATCH = (5, 3)              # 眼睛上方那條毛縮成 5 × 3 px，放大時自然變成柔和的毛色
LID_SHADOW = 0.2                # 眼皮下緣的陰影
TAU = 2 * math.pi


class EyeScene(NamedTuple):
    gaze: tuple                 # 每隻眼睛的方向（-1～1, -1～1）
    lid: float                  # 眼皮蓋住的比例（0 張開、1 閉上）


class Scene(NamedTuple):
    appearance: str
    state: object               # sdm.core.companion.CompanionState
    t: float = 0.0              # 動畫時間（秒）；靜態圖為 0
    fg: tuple = FALLBACK_GRAY   # 主題前景色
    pet: bool = False           # 桌面寵物的版面
    photo: object = None        # 我的照片（Cairo 圖面）；沒有時改畫貓咪大頭照
    eyes: Optional[EyeScene] = None


def tint(scene):
    return TONES.get(scene.state.tone, scene.fg)


def ellipse(cr, cx, cy, rx, ry):
    cr.save()
    cr.translate(cx, cy)
    cr.scale(rx, ry)
    cr.arc(0, 0, 1, 0, TAU)
    cr.restore()


def rounded(cr, x, y, width, height, radius):
    radius = min(radius, width / 2, height / 2)
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


def bolt(cr, cx, cy, size):
    """閃電符號（高度 size）。"""
    points = ((0.12, -0.5), (-0.3, 0.08), (-0.02, 0.08), (-0.12, 0.5), (0.3, -0.1), (0.02, -0.1))
    cr.move_to(cx + points[0][0] * size, cy + points[0][1] * size)
    for x, y in points[1:]:
        cr.line_to(cx + x * size, cy + y * size)
    cr.close_path()


def soft_ellipse(cx, cy, rx, ry, solid):
    """中間到 solid 是滿的、之後漸漸透明的橢圓遮罩。"""
    pattern = cairo.RadialGradient(0, 0, 0, 0, 0, 1)
    for offset, alpha in ((0, 1), (solid, 1), (1, 0)):
        pattern.add_color_stop_rgba(offset, 0, 0, 0, alpha)
    pattern.set_matrix(cairo.Matrix(1 / rx, 0, 0, 1 / ry, -cx / rx, -cy / ry))
    return pattern


def fit_bottom(rect, aspect):
    width = min(rect.width, rect.height * aspect)
    height = width / aspect
    return Rect(rect.x + (rect.width - width) / 2, rect.y + rect.height - height, width, height)


def figure_box(rect, pet):
    """貓咪以外的外觀：寬高比 48 × 50，置中。"""
    width = rect.width * PET_FIGURE_RATIO if pet else min(
        rect.width, rect.height * DESIGN_WIDTH / DESIGN_HEIGHT)
    height = width * DESIGN_HEIGHT / DESIGN_WIDTH
    return Rect(rect.x + (rect.width - width) / 2, rect.y + (rect.height - height) / 2,
                width, height)


def draw_zzz(cr, box, t):
    cr.save()
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    for particle in zzz(t):
        cr.set_font_size(max(1.0, particle.size * box.height))
        cr.move_to(box.x + particle.x * box.width, box.y + particle.y * box.height)
        cr.set_source_rgba(*COLORS["zzz"], particle.alpha * 0.9)
        cr.show_text("z")
    cr.restore()


def draw_sparks(cr, box, t):
    """四角星形的火花：四段往中心彎的曲線。"""
    for particle in sparks(t):
        x, y = box.x + particle.x * box.width, box.y + particle.y * box.height
        r = particle.size * box.height
        cr.move_to(x, y - r)
        for tip_x, tip_y in ((x + r, y), (x, y + r), (x - r, y), (x, y - r)):
            cr.curve_to(x, y, x, y, tip_x, tip_y)
        cr.set_source_rgba(*COLORS["spark"], particle.alpha)
        cr.fill()


def draw_particles(cr, box, scene, spark_box=None):
    if scene.state.resting:
        draw_zzz(cr, box, scene.t)
    if scene.state.on_pad:
        draw_sparks(cr, spark_box or box, scene.t)


def draw_page_cat(cr, rect, scene, images):
    name = CAT_IMAGES[scene.state.on_pad]
    surface = images.surface(name)
    aspect = surface.get_width() / surface.get_height() if surface else DEFAULT_ASPECT
    box = fit_bottom(rect, aspect)
    if not draw_sprite(cr, images, name, box, scene.t):
        draw_particles(cr, box, scene)


def draw_pad(cr, rect, unit):
    """發光的充電墊：上亮下暗的底座，加上綠色光圈。"""
    cx, cy = rect.x + rect.width / 2, rect.y + rect.height - 16 * unit
    ellipse(cr, cx, cy, 69 * unit, 14 * unit)
    shade = cairo.LinearGradient(0, cy - 14 * unit, 0, cy + 14 * unit)
    shade.add_color_stop_rgb(0, 0.97, 0.97, 0.97)
    shade.add_color_stop_rgb(1, 0.82, 0.82, 0.82)
    cr.set_source(shade)
    cr.fill()
    for width, alpha in ((7 * unit, 0.3), (3 * unit, 1.0)):
        ellipse(cr, cx, cy - unit, 56 * unit, 8.5 * unit)
        cr.set_line_width(width)
        cr.set_source_rgba(*TONES[GOOD], alpha)
        cr.stroke()


def draw_sign(cr, rect, cat, unit):
    """充電標示：上方兩道訊號波紋，下面接綠色圓圈裡的白色閃電。"""
    cx, cy = rect.x + rect.width / 2, cat.y - 14 * unit
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_width(3.2 * unit)
    cr.set_source_rgba(*TONES[GOOD], 0.55)
    for radius in (16 * unit, 23 * unit):
        cr.arc(cx, cy + 2 * unit, radius, -0.78 * math.pi, -0.22 * math.pi)
        cr.stroke()
    cr.arc(cx, cy, 12 * unit, 0, TAU)
    cr.set_source_rgb(*TONES[GOOD])
    cr.fill()
    bolt(cr, cx, cy, 13 * unit)
    cr.set_source_rgb(1, 1, 1)
    cr.fill()


def draw_pet_cat(cr, rect, scene, images):
    unit = rect.width / PET_BASE
    on_pad = scene.state.on_pad
    width, height = 160 * unit, 106 * unit
    cat = Rect(rect.x + (rect.width - width) / 2,
               rect.y + rect.height - (12 if on_pad else 4) * unit - height, width, height)
    if on_pad:
        draw_pad(cr, rect, unit)
        draw_sign(cr, rect, cat, unit)
    if not draw_sprite(cr, images, PET_CAT, cat, scene.t):
        draw_particles(cr, cat, scene, spark_box=rect)


def buddy_mouth(cr, x, y, unit, mood):
    """嘴巴在寬 13、高 7 的格子裡（左上角 x, y）。"""
    def point(px, py):
        return x + px * unit, y + py * unit
    if mood == BUSY:
        ellipse(cr, *point(6.5, 4), 2.2 * unit, 2.6 * unit)
        return
    shapes = {FULL_MOOD: ((0, 0), (6.5, 10), (13, 0)), CHARGING_MOOD: ((2, 2), (6.5, 7), (11, 2)),
              LOW: ((1, 6), (6.5, 0), (12, 6))}
    if mood not in shapes:
        cr.move_to(*point(3, 4))
        cr.line_to(*point(10, 4))
        return
    start, control, end = (point(*p) for p in shapes[mood])
    cr.move_to(*start)
    cr.curve_to(start[0] + 2 / 3 * (control[0] - start[0]), start[1] + 2 / 3 * (control[1] - start[1]),
                end[0] + 2 / 3 * (control[0] - end[0]), end[1] + 2 / 3 * (control[1] - end[1]), *end)


def buddy_body(cr, cx, cy, unit, color, level):
    x, y, width, height = cx - 20 * unit, cy - 15 * unit, 40 * unit, 30 * unit
    rounded(cr, x, y, width, height, 9 * unit)
    cr.set_source_rgba(*color, 0.12)
    cr.fill()
    if level is not None:
        rounded(cr, x + 3 * unit, y + 3 * unit, max(6 * unit, (width - 6 * unit) * level),
                height - 6 * unit, 7 * unit)
        cr.set_source_rgba(*color, 0.22)
        cr.fill()
    rounded(cr, x + unit, y + unit, width - 2 * unit, height - 2 * unit, 8 * unit)
    cr.set_line_width(2 * unit)
    cr.set_source_rgba(*color, 0.7)
    cr.stroke()
    rounded(cr, x + width + 0.75 * unit, cy - 5.5 * unit, 3.5 * unit, 11 * unit, 1.75 * unit)
    cr.fill()


def buddy_eyes(cr, cx, cy, unit, color, closed):
    cr.set_source_rgb(*color)
    for side in (-1, 1):
        ex, ey = cx + side * 8 * unit, cy - 4 * unit
        if closed:
            rounded(cr, ex - 3.5 * unit, ey - unit, 7 * unit, 2 * unit, unit)
        else:
            cr.arc(ex, ey, 2.25 * unit, 0, TAU)
        cr.fill()


def buddy_extras(cr, cx, cy, unit, color, mood):
    """休息時的 z、很忙時的汗滴。"""
    if mood == BUSY:
        x, y = cx + 15 * unit, cy - 13 * unit
        cr.move_to(x, y - 3 * unit)
        cr.curve_to(x + 2.5 * unit, y + 0.5 * unit, x + 2 * unit, y + 2.5 * unit, x, y + 2.5 * unit)
        cr.curve_to(x - 2 * unit, y + 2.5 * unit, x - 2.5 * unit, y + 0.5 * unit, x, y - 3 * unit)
        cr.set_source_rgb(*COLORS["sweat"])
        cr.fill()
        return
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(9 * unit)
    cr.move_to(cx + 12 * unit, cy - 8 * unit)
    cr.set_source_rgb(*color)
    cr.show_text("z")


def draw_buddy(cr, box, scene, _images):
    unit, color, mood = box.width / DESIGN_WIDTH, tint(scene), scene.state.mood
    cx, cy = box.x + box.width / 2, box.y + box.height / 2
    buddy_body(cr, cx, cy, unit, color, scene.state.level)
    blinking = scene.eyes is not None and scene.eyes.lid >= 0.5
    buddy_eyes(cr, cx, cy, unit, color, scene.state.resting or blinking)
    cr.save()
    buddy_mouth(cr, cx - 6.5 * unit, cy + 3.5 * unit, unit, mood)
    cr.set_line_width(2 * unit)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_source_rgb(*color)
    cr.stroke()
    cr.restore()
    if scene.state.resting or mood == BUSY:
        buddy_extras(cr, cx, cy, unit, color, mood)


def draw_bar(cr, box, scene, _images):
    unit, color, level = box.width / DESIGN_WIDTH, tint(scene), scene.state.level
    cy = box.y + box.height / 2 + 4 * unit
    rounded(cr, box.x, cy - 4 * unit, box.width, 8 * unit, 4 * unit)
    cr.set_source_rgba(*color, 0.18)
    cr.fill()
    if level is not None:
        rounded(cr, box.x, cy - 4 * unit, max(8 * unit, box.width * level), 8 * unit, 4 * unit)
        cr.set_source_rgba(*color, 0.8)
        cr.fill()
    if scene.state.on_pad:
        x = box.x + 3 * unit + 40 * unit * ((scene.t % BAR_TRIP_SECONDS) / BAR_TRIP_SECONDS)
        glow = cairo.RadialGradient(x, cy, 0, x, cy, 6 * unit)
        glow.add_color_stop_rgba(0, 1, 1, 1, 1)
        glow.add_color_stop_rgba(0.45, 1, 1, 1, 1)
        glow.add_color_stop_rgba(1, *color, 0)
        cr.arc(x, cy, 6 * unit, 0, TAU)
        cr.set_source(glow)
        cr.fill()
    bolt(cr, box.x + box.width / 2, cy - 14 * unit, 13 * unit)
    cr.set_source_rgb(*color)
    cr.fill()


def band_patch(pixbuf, region):
    """Pixbuf 中 region（左、上、寬、高）那一塊縮成 LID_PATCH 大小的 Cairo 圖面；範圍外的部分不算。"""
    x0, y0 = max(0, int(region[0])), max(0, int(region[1]))
    x1 = min(pixbuf.get_width(), int(region[0] + region[2]))
    y1 = min(pixbuf.get_height(), int(region[1] + region[3]))
    if x1 <= x0 or y1 <= y0:
        return None
    band = pixbuf.new_subpixbuf(x0, y0, x1 - x0, y1 - y0)
    return surface_from_pixbuf(band.scale_simple(*LID_PATCH, GdkPixbuf.InterpType.BILINEAR))


def lid_patches(images):
    """每隻眼睛正上方那一條毛（參考專案的眼皮做法）；只做一次。"""
    if "lids" not in images.derived:
        pixbuf = images.pixbuf(HEADSHOT)
        side = pixbuf.get_width() if pixbuf is not None else 0
        images.derived["lids"] = tuple(
            band_patch(pixbuf, (x - 1.25 * r, y - 1.5 * r, 2.5 * r, 0.42 * r))
            if pixbuf is not None else None for x, y, r in eye_circles(side))
    return images.derived["lids"]


def photo_source(cr, surface, rect, dx=0.0, dy=0.0):
    """以照片中間的正方形填滿 rect（可再平移 dx, dy）。"""
    width, height = surface.get_width(), surface.get_height()
    side = min(width, height)
    scale = rect.width / side
    cr.set_source_surface(surface, 0, 0)
    matrix = cairo.Matrix(1 / scale, 0, 0, 1 / scale,
                          (width - side) / 2 - (rect.x + dx) / scale,
                          (height - side) / 2 - (rect.y + dy) / scale)
    cr.get_source().set_matrix(matrix)
    cr.get_source().set_filter(cairo.FILTER_GOOD)


def draw_lid(cr, eye, lid, patch):
    """眼皮：眼睛上方那條毛放大成整塊往下蓋（下緣稍暗），完全閉上時加一條往下彎的眼線。"""
    cx, cy, r = eye
    cover = soft_ellipse(cx, cy, 1.3 * r, 1.3 * r, LID_SOLID)
    cr.save()
    cr.rectangle(cx - 1.3 * r, cy - 1.3 * r, 2.6 * r, 2.6 * r * lid)
    cr.clip()
    if patch is None:
        cr.set_source_rgb(*FALLBACK_GRAY)
    else:
        cr.set_source_surface(patch, 0, 0)
        scale_x, scale_y = 2.6 * r / patch.get_width(), 2.6 * r / patch.get_height()
        cr.get_source().set_matrix(cairo.Matrix(1 / scale_x, 0, 0, 1 / scale_y,
                                                -(cx - 1.3 * r) / scale_x,
                                                -(cy - 1.3 * r) / scale_y))
        cr.get_source().set_filter(cairo.FILTER_BILINEAR)
        cr.get_source().set_extend(cairo.EXTEND_PAD)
    cr.mask(cover)
    shadow = cairo.LinearGradient(0, cy - 0.4 * r, 0, cy + 1.3 * r)
    shadow.add_color_stop_rgba(0, 0, 0, 0, 0)
    shadow.add_color_stop_rgba(1, 0, 0, 0, LID_SHADOW)
    cr.set_source(shadow)
    cr.mask(cover)
    cr.restore()
    if lid >= 0.9:
        cr.move_to(cx - 0.85 * r, cy + 0.65 * r)
        cr.curve_to(cx - 0.28 * r, cy + 0.85 * r, cx + 0.28 * r, cy + 0.85 * r,
                    cx + 0.85 * r, cy + 0.65 * r)
        cr.set_line_width(max(1.2, 0.14 * r))
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_source_rgba(*COLORS["lid_line"], 0.85)
        cr.stroke()


def draw_eyes(cr, photo_rect, surface, eyes, patches):
    """眼窩是深色底，上面是照片中眼睛那一塊依注視方向平移（柔邊）；眨眼時蓋上眼皮。"""
    circles = eye_circles(photo_rect.width, photo_rect.x, photo_rect.y)
    for eye, direction, patch in zip(circles, eyes.gaze, patches):
        cx, cy, r = eye
        dx, dy = iris_offset(direction, r)
        cr.push_group()
        cr.set_source_rgb(*COLORS["socket"])
        cr.paint()
        photo_source(cr, surface, photo_rect, dx, dy)
        cr.mask(soft_ellipse(cx + dx, cy + dy, 1.06 * r, 1.06 * r, 0.82))
        cr.pop_group_to_source()
        cr.mask(soft_ellipse(cx, cy, 1.16 * r, 1.10 * r, 0.8))
        if eyes.lid > 0:
            draw_lid(cr, eye, eyes.lid, patch)


def photo_ring(cr, cx, cy, unit, scene):
    color = tint(scene)
    cr.set_line_width(3 * unit)
    cr.arc(cx, cy, 22 * unit, 0, TAU)
    cr.set_source_rgba(*color, 0.22)
    cr.stroke()
    level = scene.state.level
    if level is not None:
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        start = -math.pi / 2
        cr.arc(cx, cy, 22 * unit, start, start + TAU * max(0.02, min(1.0, level)))
        cr.set_source_rgb(*color)
        cr.stroke()
    if scene.state.on_pad:
        bx, by = cx + 17 * unit, cy - 18 * unit
        cr.arc(bx, by, 7 * unit, 0, TAU)
        cr.set_source_rgb(*color)
        cr.fill()
        bolt(cr, bx, by, 8 * unit)
        cr.set_source_rgb(1, 1, 1)
        cr.fill()


def photo_square(box):
    """照片外觀中圓形照片外接的正方形（設計大小中直徑 38）。"""
    radius = 19 * box.width / DESIGN_WIDTH
    cx, cy = box.x + box.width / 2, box.y + box.height / 2
    return Rect(cx - radius, cy - radius, 2 * radius, 2 * radius)


def photo_rect(rect, pet):
    """元件範圍 rect 中照片的位置（游標追蹤用，與 draw_photo 相同）。"""
    return photo_square(figure_box(rect, pet))


def draw_photo(cr, box, scene, images):
    unit = box.width / DESIGN_WIDTH
    cx, cy, radius = box.x + box.width / 2, box.y + box.height / 2, 19 * unit
    own = scene.appearance == "photo" and scene.photo is not None
    surface = scene.photo if own else images.surface(HEADSHOT)
    photo_rect = photo_square(box)
    cr.save()
    cr.arc(cx, cy, radius, 0, TAU)
    cr.clip()
    cr.set_source_rgba(*scene.fg, 0.15)
    cr.paint()
    if surface is not None:
        photo_source(cr, surface, photo_rect)
        cr.paint()
        if scene.eyes is not None and not own:
            draw_eyes(cr, photo_rect, surface, scene.eyes, lid_patches(images))
    cr.restore()
    photo_ring(cr, cx, cy, unit, scene)


def draw_figure(cr, rect, scene, images):
    """在 rect 中畫出 scene 的外觀。"""
    cr.save()
    if scene.appearance == "cat":
        (draw_pet_cat if scene.pet else draw_page_cat)(cr, rect, scene, images)
    else:
        draw = {"buddy": draw_buddy, "bar": draw_bar}.get(scene.appearance, draw_photo)
        draw(cr, figure_box(rect, scene.pet), scene, images)
    cr.restore()
