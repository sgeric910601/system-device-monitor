"""桌機耗電快照：每次慢速收集時讀取不斷電系統與顯示卡，產生拆解與 3 分鐘平均。

在收集執行緒中呼叫 sample()；同一時間只會有一個慢速收集在執行。
筆電電池在第 2 階段加入，屆時新增電池來源即可，快照欄位不變。
"""
from dataclasses import dataclass
import time
from typing import Optional

from sdm.constants import Collect, Power
from sdm.core.ema import Ema
from sdm.core.history import HistorySample
from sdm.core.power_breakdown import Breakdown, Component, make_breakdown
from sdm.data.machine import read_uptime as default_uptime
from sdm.data.ups import UpsReading

SUPPLY_MAINS = "mains"
SUPPLY_UPS_BATTERY = "ups_battery"
SUPPLY_LABELS = {SUPPLY_MAINS: "市電", SUPPLY_UPS_BATTERY: "不斷電系統電池"}
TIME_DECIMALS = 3
WATTS_DECIMALS = 2
PERCENT_DECIMALS = 1


@dataclass(frozen=True)
class DesktopPower:
    stamp: float                      # Unix 時間
    system_w: Optional[float]         # 整機耗電，或沒有不斷電系統時的已知元件合計
    average_w: Optional[float]        # 3 分鐘平均
    source: str                       # 拆解的來源：ups／components／none
    supply: str                       # 供電來源：市電或不斷電系統電池
    breakdown: Breakdown
    ups: Optional[UpsReading]
    gpus: tuple                       # 未過期的顯示卡讀值
    processor: str = ""
    model: str = ""
    uptime_s: Optional[float] = None


def supply_label(supply):
    return SUPPLY_LABELS.get(supply, SUPPLY_LABELS[SUPPLY_MAINS])


def gpu_components(gpus):
    """可量測功耗的顯示卡才算已知元件；內建顯示沒有功耗讀值時包含在處理器插槽功耗裡。"""
    return tuple(Component(gpu.gpu_id, gpu.name, gpu.watts, gpu.stamp)
                 for gpu in gpus if not (gpu.integrated and gpu.watts is None))


class PowerService:
    def __init__(self, read_ups, read_gpus, gpus_pending=lambda: False, processor="",
                 model="", read_uptime=default_uptime, clock=time.monotonic, wall=time.time,
                 interval=Collect.SLOW_INTERVAL):
        self.read_ups, self.read_gpus, self.gpus_pending = read_ups, read_gpus, gpus_pending
        self.processor, self.model, self.read_uptime = processor, model, read_uptime
        self.clock, self.wall, self.interval = clock, wall, interval
        self.average = Ema()
        self.source = None

    def sample(self):
        now = self.clock()
        ups = self.read_ups()
        gpus = tuple(self.read_gpus())
        breakdown = make_breakdown(ups.load_w if ups else None, gpu_components(gpus), now,
                                   self.interval, sampling=self.gpus_pending())
        if breakdown.source != self.source:
            # 整機讀值與已知元件合計不能混在同一個平均裡
            self.average.reset()
            self.source = breakdown.source
        limit = self.interval * Power.STALE_FACTOR
        return DesktopPower(
            stamp=self.wall(), system_w=breakdown.system_w,
            average_w=self.average.update(breakdown.system_w, now), source=breakdown.source,
            supply=SUPPLY_UPS_BATTERY if ups and ups.on_battery else SUPPLY_MAINS,
            breakdown=breakdown, ups=ups,
            gpus=tuple(gpu for gpu in gpus if 0 <= now - gpu.stamp <= limit),
            processor=self.processor, model=self.model, uptime_s=self.read_uptime())


def rounded(value, digits):
    return None if value is None else round(value, digits)


def desktop_history_sample(power):
    """桌機每分鐘一筆：沒有電量；接電狀態依不斷電系統，通訊中斷視同沒有不斷電系統。"""
    if power is None:
        return None
    ups = power.ups if power.ups is not None and not power.ups.comm_lost else None
    ups_pct = rounded(ups.charge_pct, PERCENT_DECIMALS) if ups else None
    if power.system_w is None and ups_pct is None:
        return None
    return HistorySample(t=round(power.stamp, TIME_DECIMALS), pct=None,
                         watts=rounded(power.system_w, WATTS_DECIMALS),
                         plugged=ups.online if ups else True, charging=False,
                         ups_pct=ups_pct, ups_online=ups.online if ups else None)
