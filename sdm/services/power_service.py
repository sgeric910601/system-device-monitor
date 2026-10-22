"""耗電快照：有系統電池時產生筆電快照（LaptopPower），否則產生桌機快照（DesktopPower）。

桌機：每次慢速收集時讀取不斷電系統與顯示卡，產生拆解與 3 分鐘平均。
筆電：sysfs 電池與變壓器為主，UPower 提供系統預估，電源模式服務提供省電狀態。
sample() 在收集執行緒中呼叫；插拔電源時也會另外觸發，所以用鎖確保一次只取樣一個，
sequence 依取樣先後遞增，讓介面能分辨哪一份快照比較新。
"""
from dataclasses import dataclass, replace
import threading
import time
from typing import Optional

from sdm.constants import Battery, Collect, Power
from sdm.core.battery import combine
from sdm.core.ema import Ema
from sdm.core.estimates import SystemTimes
from sdm.core.history import HistorySample
from sdm.core.power_breakdown import Breakdown, Component, make_breakdown
from sdm.data.machine import read_uptime as default_uptime
from sdm.data.ups import UpsReading
from sdm.services.battery_service import BatteryTracker, LaptopPower, laptop_history_sample

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
    sequence: int = 0


def supply_label(supply):
    return SUPPLY_LABELS.get(supply, SUPPLY_LABELS[SUPPLY_MAINS])


def gpu_components(gpus):
    """可量測功耗的顯示卡才算已知元件；內建顯示沒有功耗讀值時包含在處理器插槽功耗裡。"""
    return tuple(Component(gpu.gpu_id, gpu.name, gpu.watts, gpu.stamp)
                 for gpu in gpus if not (gpu.integrated and gpu.watts is None))


class PowerService:
    def __init__(self, read_ups, read_gpus, gpus_pending=lambda: False, processor="",
                 model="", read_uptime=default_uptime, clock=time.monotonic, wall=time.time,
                 interval=Collect.SLOW_INTERVAL, read_batteries=lambda: (),
                 read_adapters=lambda: (), read_upower=lambda: None, read_profile=lambda: None):
        self.read_ups, self.read_gpus, self.gpus_pending = read_ups, read_gpus, gpus_pending
        self.processor, self.model, self.read_uptime = processor, model, read_uptime
        self.clock, self.wall, self.interval = clock, wall, interval
        self.read_batteries, self.read_adapters = read_batteries, read_adapters
        self.read_upower, self.read_profile = read_upower, read_profile
        self.average = Ema()
        self.source = None
        self.tracker = None
        self.last_laptop, self.empty_reads = None, 0
        self.sequence = 0
        self.lock = threading.Lock()

    def sample(self):
        with self.lock:
            self.sequence += 1
            batteries = tuple(self.read_batteries())
            if batteries:
                self.empty_reads = 0
                self.last_laptop = self.laptop(batteries)
                return self.last_laptop
            if self.last_laptop is not None:
                self.empty_reads += 1
                if self.empty_reads < Battery.MODE_SWITCH_EMPTY_READS:
                    # 電池暫時讀不到：沿用上一份筆電快照（時間不變，不會重複記入歷史）
                    return replace(self.last_laptop, sequence=self.sequence)
            self.tracker, self.last_laptop, self.empty_reads = None, None, 0
            return self.desktop()

    def laptop(self, batteries):
        adapters, upower = tuple(self.read_adapters()), self.read_upower()
        profile, ups, gpus = self.read_profile(), self.read_ups(), tuple(self.read_gpus())
        now, wall = self.clock(), self.wall()
        state = upower.state if upower is not None and upower.is_battery else None
        snapshot = combine(batteries, adapters, upower.on_battery if upower else None, state)
        if self.tracker is None:
            self.tracker = BatteryTracker()
        times = upower.times() if upower else SystemTimes()
        estimates = self.tracker.update(snapshot, times, now, wall)
        limit = self.interval * Power.STALE_FACTOR
        return LaptopPower(
            stamp=wall, battery=snapshot, estimates=estimates,
            breakdown=make_breakdown(None, gpu_components(gpus), now, self.interval,
                                     sampling=self.gpus_pending()),
            upower=upower, profile=profile, unplugged_at=self.tracker.unplugged_at,
            plugged_in_at=self.tracker.plugged_in_at, ups=ups,
            gpus=tuple(gpu for gpu in gpus if 0 <= now - gpu.stamp <= limit),
            processor=self.processor, model=self.model, uptime_s=self.read_uptime(),
            sequence=self.sequence)

    def desktop(self):
        ups = self.read_ups()
        gpus = tuple(self.read_gpus())
        # 讀完才取時間：讀取期間打上的時間戳記不會晚於 now 而被當成未來樣本
        now = self.clock()
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
            processor=self.processor, model=self.model, uptime_s=self.read_uptime(),
            sequence=self.sequence)


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


def newest(*snapshots):
    """sequence 最大（最後取樣）的快照；全部都是 None 時回傳 None。"""
    known = [snapshot for snapshot in snapshots if snapshot is not None]
    return max(known, key=lambda snapshot: snapshot.sequence) if known else None


def history_sample(power):
    """依快照種類產生一筆歷史紀錄；沒有快照或沒有可記錄的數值時回傳 None。"""
    if isinstance(power, LaptopPower):
        return laptop_history_sample(power)
    return desktop_history_sample(power)
