"""Linux 桌機的電力拆解：整機耗電減去已知元件，差值歸為「其他」。

整機耗電來自不斷電系統輸出（含螢幕等所有插在上面的設備）；沒有不斷電系統時，
只能把已知元件相加。CPU 欄位先保留，第 6 階段才有可信的 CPU 功耗。
狀態名稱沿用參考專案 PowerBreakdown（available／sampling／incomplete／
inconsistent／stale），但判斷規則與訊息依 Linux 的資料來源改寫：沒有取樣窗對齊的概念，
上下限不同，缺少的元件一律算進「其他」並標為資料不完整。
"""
from dataclasses import dataclass
import math
from typing import NamedTuple, Optional

from sdm.constants import Collect, Power

AVAILABLE = "available"
SAMPLING = "sampling"
INCOMPLETE = "incomplete"
INCONSISTENT = "inconsistent"
STALE = "stale"

SOURCE_UPS = "ups"
SOURCE_COMPONENTS = "components"
SOURCE_NONE = "none"

OTHER_ID = "other"
OTHER_LABEL = "其他"
TOTAL_SYSTEM = "整機耗電"
TOTAL_KNOWN = "已知元件合計"

MESSAGES = {
    AVAILABLE: "其他＝整機減去已知元件・含螢幕與電源損耗",
    SAMPLING: "取樣中・正在讀取元件功耗",
    INCONSISTENT: "取樣不同步・暫不計算其他功耗",
    STALE: "等待新的功耗樣本",
}
MESSAGE_MISSING = "其他包含未取得的元件功耗・差值估算"
MESSAGE_PARTS_ONLY = "資料不完整・無法取得整機耗電，只顯示已知元件合計"
MESSAGE_NONE = "無法取得整機耗電與元件功耗"


class Component(NamedTuple):
    """一個已知元件的讀值；stamp 為單調時鐘秒數，watts 讀不到時為 None。"""
    id: str
    label: str
    watts: Optional[float]
    stamp: float


class Item(NamedTuple):
    """拆解結果中的一列：（id、名稱、瓦數）。"""
    id: str
    label: str
    watts: Optional[float]


@dataclass(frozen=True)
class Breakdown:
    system_w: Optional[float] = None
    source: str = SOURCE_NONE
    items: tuple = ()
    other_w: Optional[float] = None
    status: str = INCOMPLETE
    message: str = MESSAGE_NONE
    total_label: str = TOTAL_SYSTEM


def valid_watts(value, limit):
    """有限、0 以上且小於 limit 才採用，否則為 None。"""
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and 0 <= value < limit else None


def is_stale(component, now, interval):
    age = now - component.stamp
    return age < 0 or age > interval * Power.STALE_FACTOR


def make_breakdown(system_w, components=(), now=0.0, interval=Collect.SLOW_INTERVAL,
                   sampling=False, cpu=None):
    """依整機瓦數與元件讀值產生拆解；now 與元件 stamp 使用同一個單調時鐘。"""
    system = valid_watts(system_w, Power.SYSTEM_MAX_W)
    parts = tuple(components) + ((cpu,) if cpu is not None else ())
    source = SOURCE_UPS if system is not None else SOURCE_NONE
    if any(is_stale(component, now, interval) for component in parts):
        return Breakdown(system, source, (), None, STALE, MESSAGES[STALE])
    items = tuple(Item(component.id, component.label,
                       valid_watts(component.watts, Power.COMPONENT_MAX_W))
                  for component in parts)
    if sampling:
        return Breakdown(system, source, items, None, SAMPLING, MESSAGES[SAMPLING])
    if system is None:
        return components_only(items)
    return with_system(system, items)


def components_only(items):
    """沒有整機讀值：合計已知元件，狀態一律為資料不完整。"""
    known = [item.watts for item in items if item.watts is not None]
    if not known:
        return Breakdown(None, SOURCE_NONE, items, None, INCOMPLETE, MESSAGE_NONE)
    return Breakdown(sum(known), SOURCE_COMPONENTS, items, None, INCOMPLETE,
                     MESSAGE_PARTS_ONLY, TOTAL_KNOWN)


def with_system(system, items):
    """整機減去已知元件；差值為負代表取樣不同步，不計算其他。"""
    residual = system - sum(item.watts for item in items if item.watts is not None)
    if residual < 0:
        return Breakdown(system, SOURCE_UPS, items, None, INCONSISTENT, MESSAGES[INCONSISTENT])
    missing = any(item.watts is None for item in items)
    return Breakdown(system, SOURCE_UPS, items + (Item(OTHER_ID, OTHER_LABEL, residual),),
                     residual, INCOMPLETE if missing else AVAILABLE,
                     MESSAGE_MISSING if missing else MESSAGES[AVAILABLE])


class SamplingGate:
    """與參考專案 SamplingGate 相同：避免重疊取樣，並拒絕停用前發出的取樣結果。"""

    def __init__(self):
        self.active = False
        self.generation = 0
        self._busy = False

    def set_active(self, active):
        if self.active == active:
            return
        self.active = active
        self.generation += 1
        self._busy = False

    def begin(self):
        """可以開始取樣時回傳票號，否則回傳 None。"""
        if not self.active or self._busy:
            return None
        self._busy = True
        return self.generation

    def finish(self, ticket):
        """票號仍有效時回傳 True，結果才可採用。"""
        if not self.active or ticket != self.generation:
            return False
        self._busy = False
        return True
