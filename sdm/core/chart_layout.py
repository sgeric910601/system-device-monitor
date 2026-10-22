"""圖表與電力流向圖的純幾何：刻度上限、時間刻度、流帶寬度與節點位置。

不載入 GTK，繪圖元件只負責把這裡算好的座標畫出來。
"""
from datetime import datetime, timedelta
import math
from typing import NamedTuple

from sdm.constants import Chart, Flow
from sdm.core.fmt import value_text

HOUR = 3600
DAY = 24 * HOUR


class Rect(NamedTuple):
    x: float
    y: float
    width: float
    height: float


class Band(NamedTuple):
    """從 (x0, y0) 到 (x1, y1) 的流帶；y 為流帶中心，width 為粗細。"""
    x0: float
    y0: float
    x1: float
    y1: float
    width: float


class FlowGeometry(NamedTuple):
    source: Rect
    total: Rect
    items: tuple
    source_band: Band
    item_bands: tuple


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def nice_axis_max(value, minimum=Chart.REFERENCE_AXIS_MINIMUM):
    """往上取到 1、2、5 × 10ⁿ（參考專案 niceAxisMax），最高的資料不會超出最上面的格線。"""
    target = max(minimum, value if is_number(value) else 0)
    magnitude = 10 ** math.floor(math.log10(target))
    for step in (1, 2, 5, 10):
        if step * magnitude >= target:
            return float(step * magnitude)
    return float(10 * magnitude)


def linear(value, low, high, start, end):
    """把 low–high 的值線性對應到 start–end 的座標。"""
    if high == low:
        return start
    return start + (value - low) / (high - low) * (end - start)


def tick_plan(range_s):
    """（間隔、對齊到整點或整天、是否顯示日期）。"""
    if range_s <= HOUR:
        return timedelta(minutes=15), "hour", False
    if range_s <= DAY:
        return timedelta(hours=6), "day", False
    return timedelta(days=1), "day", True


def axis_ticks(end, range_s, tz=None):
    """對齊整點或整天的時間刻度（參考專案 axisDates）；tz 為 None 時用本機時區。"""
    start = end - range_s
    step, align, dated = tick_plan(range_s)
    current = datetime.fromtimestamp(start, tz).replace(minute=0, second=0, microsecond=0)
    if align == "day":
        current = current.replace(hour=0)
    margin = range_s * Chart.AXIS_MARGIN_RATIO
    ticks = []
    while current.timestamp() <= end:
        stamp = current.timestamp()
        if stamp - start > margin and end - stamp > margin:
            label = f"{current.month}/{current.day}" if dated else current.strftime("%H:%M")
            ticks.append((stamp, label))
        current += step
    return tuple(ticks)


def band_widths(values, reference, max_width, min_width=Flow.BAND_MIN):
    """流帶寬度與 √W 成正比；reference 對應 max_width，正值至少 min_width，沒有值為 0。"""
    positive = [value for value in values if is_number(value) and value > 0]
    if not (is_number(reference) and reference > 0):
        reference = max(positive, default=None)
    widths = []
    for value in values:
        if reference is None or not is_number(value) or value <= 0:
            widths.append(0.0)
            continue
        widths.append(min(max_width, max(min_width, max_width * math.sqrt(value / reference))))
    return tuple(widths)


def flow_height(count):
    """依項目數決定流向圖高度，讓每個項目節點都放得下兩行文字。"""
    stacked = count * (Flow.ITEM_HEIGHT + Flow.ITEM_GAP) - Flow.ITEM_GAP + 2 * Flow.PADDING
    return max(Flow.MIN_HEIGHT, stacked)


def item_rects(count, x, top, inner_height):
    if not count:
        return ()
    gap = Flow.ITEM_GAP
    height = min(Flow.ITEM_HEIGHT, max(0.0, (inner_height - (count - 1) * gap) / count))
    first = top + (inner_height - (count * height + (count - 1) * gap)) / 2
    return tuple(Rect(x, first + index * (height + gap), Flow.ITEM_WIDTH, height)
                 for index in range(count))


def stacked_bands(widths, total, items, limit):
    """整機節點右側依序堆疊各流帶；合計超過節點高度時等比例縮小。"""
    sum_width = sum(widths)
    scale = min(1.0, limit / sum_width) if sum_width > 0 else 1.0
    scaled = [width * scale for width in widths]
    cursor = total.y + total.height / 2 - sum(scaled) / 2
    bands = []
    for width, item in zip(scaled, items):
        bands.append(Band(total.x + total.width, cursor + width / 2, item.x,
                          item.y + item.height / 2, width))
        cursor += width
    return tuple(bands)


def flow_geometry(width, height, item_values, total_w):
    """來源 → 整機 → 各項目的節點位置與流帶。"""
    pad = Flow.PADDING
    inner = max(0.0, height - 2 * pad)
    node_height = min(inner, Flow.NODE_HEIGHT)
    center = height / 2
    source = Rect(pad, center - node_height / 2, Flow.SOURCE_WIDTH, node_height)
    item_x = max(source.x + source.width + Flow.TOTAL_WIDTH, width - pad - Flow.ITEM_WIDTH)
    gap = item_x - (source.x + source.width) - Flow.TOTAL_WIDTH
    total = Rect(source.x + source.width + gap / 2, center - node_height / 2,
                 Flow.TOTAL_WIDTH, node_height)
    items = item_rects(len(item_values), item_x, pad, inner)
    max_band = min(Flow.BAND_MAX, node_height)
    source_width = band_widths((total_w,), total_w, max_band)[0]
    source_band = Band(source.x + source.width, center, total.x, center, source_width)
    widths = band_widths(tuple(item_values), total_w, max_band)
    return FlowGeometry(source, total, items, source_band,
                        stacked_bands(widths, total, items, node_height))


def line_segments(points, value, segment, break_on_missing=False):
    """依分段把圖表點切成多條線，每點為 (t, 值, 是否接電)。

    value、segment 為取值函式；沒有值的點略過，break_on_missing 為 True 時在該處斷開。
    """
    runs, current, key = [], [], None
    for point in points:
        number = value(point)
        if number is None:
            if break_on_missing and current:
                runs.append(current)
                current = []
            continue
        if current and segment(point) != key:
            runs.append(current)
            current = []
        key = segment(point)
        current.append((point.t, number, point.plugged))
    if current:
        runs.append(current)
    return runs


def watts_segments(points):
    """耗電線：依 watts_segment 斷開；接電狀態改變的複製點沒有耗電值，略過但不斷線。"""
    return line_segments(points, lambda point: point.watts, lambda point: point.watts_segment)


def ups_segments(points):
    """不斷電系統電量線：依 segment 斷開（接電與改用電池分色），讀不到電量時也斷開。"""
    return line_segments(points, lambda point: point.ups_pct, lambda point: point.segment,
                         break_on_missing=True)


def pct_segments(points):
    """筆電電量線：與不斷電系統電量線相同，依接電與用電池分色，讀不到電量時斷開。"""
    return line_segments(points, lambda point: point.pct, lambda point: point.segment,
                         break_on_missing=True)


def watts_summary(points):
    """圖表下方摘要「平均耗電 X W · 最高 Y W」；沒有耗電資料時回傳 None。"""
    watts = [point.watts for point in points if point.watts is not None]
    if not watts:
        return None
    return (f"平均耗電 {value_text(sum(watts) / len(watts), ' W')} · "
            f"最高 {value_text(max(watts), ' W')}")
