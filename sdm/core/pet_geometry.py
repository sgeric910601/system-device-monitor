"""桌面寵物的大小與位置（純邏輯）。

- 大小：100% 時邊長 176 px（和日曆小工具差不多），範圍 50%–200%；設定與右鍵選單提供
  小、標準、大、特大四個選項。
- 位置：可以放在任何螢幕的任何地方，也可以有一部分超出螢幕，只確保至少留
  48 px（寵物比 96 px 小時留一半寬度）在重疊最多的那個螢幕的工作區裡找得到
  （重疊一樣多時以寵物中心所在的螢幕為準）；
  完全不在任何螢幕上時回到預設位置（主螢幕工作區右下角）。改大小時腳底的位置不動。
- 對話框貼在寵物頭上，上面空間不夠時改到下方。
"""
import math
from typing import NamedTuple

from sdm.constants import Pet

PRESET_NAMES = ("小", "標準", "大", "特大")
PERCENT = 100
DEFAULT_SCALE = 1.0


class Rect(NamedTuple):
    x: float
    y: float
    width: float
    height: float


def clamp_scale(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return DEFAULT_SCALE
    return min(Pet.SCALE_MAX, max(Pet.SCALE_MIN, float(value)))


def pet_size(scale):
    return round(Pet.BASE_SIZE * clamp_scale(scale))


def size_presets():
    return tuple((scale, f"{name}（{round(scale * PERCENT)}%）")
                 for scale, name in zip(Pet.SCALE_PRESETS, PRESET_NAMES))


def preset_title(scale):
    """預設選項的名稱；其他大小（手動修改設定檔）只顯示百分比。"""
    for value, title in size_presets():
        if math.isclose(value, scale):
            return title
    return f"{round(scale * PERCENT)}%"


def _overlap(a, b):
    width = min(a.x + a.width, b.x + b.width) - max(a.x, b.x)
    height = min(a.y + a.height, b.y + b.height) - max(a.y, b.y)
    return width * height if width > 0 and height > 0 else 0


def _contains(monitor, x, y):
    return monitor.x <= x < monitor.x + monitor.width and monitor.y <= y < monitor.y + monitor.height


def best_monitor(rect, monitors):
    """重疊面積最大的螢幕索引；面積相同時選寵物中心所在的螢幕。完全不重疊時為 None。"""
    cx, cy = rect.x + rect.width / 2, rect.y + rect.height / 2
    scored = [(_overlap(rect, monitor), _contains(monitor, cx, cy), index)
              for index, monitor in enumerate(monitors)]
    area, _, index = max(scored, key=lambda item: item[:2], default=(0, False, None))
    return index if area > 0 else None


def keep_on_screen(rect, monitors, default):
    """回傳調整後的（x, y）。monitors 為各螢幕的工作區（扣掉頂端列與 Dock）；
    default 為找不到螢幕時的位置。"""
    index = best_monitor(rect, monitors)
    if index is None:
        return default
    monitor = monitors[index]
    keep = min(Pet.KEEP_VISIBLE, rect.width / 2)
    x = min(max(rect.x, monitor.x - rect.width + keep), monitor.x + monitor.width - keep)
    y = min(max(rect.y, monitor.y - rect.height + keep), monitor.y + monitor.height - keep)
    return x, y


def bubble_origin(pet, size, workarea, scale):
    """對話框視窗的（x, y, 是否在下方）：貼在寵物頭上（往下疊 30 px × 大小），
    上面放不下時改放在寵物下方；左右收進工作區。"""
    width, height = size
    x = pet.x + pet.width / 2 - width / 2
    y = pet.y - height + Pet.BUBBLE_OVERLAP * scale
    below = y < workarea.y
    if below:
        y = pet.y + pet.height - Pet.BUBBLE_BELOW_OVERLAP
    x = min(max(x, workarea.x), workarea.x + workarea.width - width)
    return round(x), round(y), below


def resized_origin(rect, size):
    """改大小時下緣中間（腳底）不動，回傳新的左上角。"""
    return (round(rect.x + rect.width / 2 - size / 2), round(rect.y + rect.height - size))


def is_drag(start, current):
    """按住後移動超過 3 px 才算拖曳，否則是點一下。"""
    dx, dy = current[0] - start[0], current[1] - start[1]
    return dx * dx + dy * dy > Pet.DRAG_THRESHOLD ** 2


def default_position(workarea, size):
    """主螢幕工作區右下角，往內留一點邊。"""
    return (workarea.x + workarea.width - size - Pet.EDGE_MARGIN,
            workarea.y + workarea.height - size - Pet.EDGE_MARGIN)
