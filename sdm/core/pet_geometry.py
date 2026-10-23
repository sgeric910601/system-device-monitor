"""桌面寵物的大小與位置（純邏輯）。

- 大小：100% 時邊長 176 px（和日曆小工具差不多），範圍 50%–200%；設定與右鍵選單提供
  小、標準、大、特大四個選項。
- 位置：可以放在任何螢幕的任何地方，也可以有一部分超出螢幕，只確保至少留
  48 px（寵物比 96 px 小時留一半寬度）在重疊最多的那個螢幕上找得到；
  完全不在任何螢幕上時回到預設位置（主螢幕工作區右下角）。
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


def keep_on_screen(rect, monitors, default):
    """回傳調整後的（x, y）。monitors 為各螢幕的範圍；default 為找不到螢幕時的位置。"""
    areas = [(_overlap(rect, monitor), index) for index, monitor in enumerate(monitors)]
    best = max(areas, default=(0, None))
    if best[0] <= 0:
        return default
    monitor = monitors[best[1]]
    keep = min(Pet.KEEP_VISIBLE, rect.width / 2)
    x = min(max(rect.x, monitor.x - rect.width + keep), monitor.x + monitor.width - keep)
    y = min(max(rect.y, monitor.y - rect.height + keep), monitor.y + monitor.height - keep)
    return x, y


def default_position(workarea, size):
    """主螢幕工作區右下角，往內留一點邊。"""
    return (workarea.x + workarea.width - size - Pet.EDGE_MARGIN,
            workarea.y + workarea.height - size - Pet.EDGE_MARGIN)
