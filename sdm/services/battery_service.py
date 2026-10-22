"""筆電電池快照：顯示濾波、近 3 分鐘平均、插拔時間與歷史紀錄。

BatteryTracker 保存跨取樣的狀態，只在電源服務持有鎖時呼叫（收集執行緒）。
插拔時間沿用參考專案 BatteryStore.refresh：這次執行中實際拔掉電源才記錄拔電時間
（啟動時就在用電池則由歷史紀錄推算）；實際插上電源才記錄插電時間，30 秒內電池仍在放電
不算供電不足。
"""
from dataclasses import dataclass, replace
from typing import Optional

from sdm.constants import Battery, Power
from sdm.core.battery import CHARGING, DISCHARGING, BatterySnapshot
from sdm.core.estimates import HOLDING_STATES, Estimates, LiveAverages, SystemWatch, estimate
from sdm.core.history import HistorySample
from sdm.core.power_breakdown import Breakdown
from sdm.core.presentation import ChargingPresentation, PowerFlowPresentation
from sdm.data.ups import UpsReading
from sdm.data.upower import UpowerReading

TIME_DECIMALS = 3
WATTS_DECIMALS = 2


@dataclass(frozen=True)
class LaptopPower:
    stamp: float                          # Unix 時間
    battery: BatterySnapshot
    estimates: Estimates
    breakdown: Breakdown                  # 只用來判斷顯示卡讀值是否仍在取樣中
    upower: Optional[UpowerReading] = None
    profile: Optional[str] = None         # 電源模式（power-saver 等）；服務不存在時為 None
    unplugged_at: Optional[float] = None  # 這次執行中拔掉電源的 Unix 時間
    plugged_in_at: Optional[float] = None
    ups: Optional[UpsReading] = None
    gpus: tuple = ()
    processor: str = ""
    model: str = ""
    uptime_s: Optional[float] = None
    sequence: int = 0

    @property
    def percent(self):
        return self.battery.percent

    @property
    def state(self):
        """顯示用（經過濾波）的狀態。"""
        return self.estimates.state

    @property
    def system_w(self):
        return self.estimates.system_w


class BatteryTracker:
    def __init__(self):
        self.presentation = ChargingPresentation()
        self.flow = PowerFlowPresentation()
        self.averages = LiveAverages()
        self.watch = SystemWatch()
        self.previous_online = None
        self.last_state = None
        self.plugged_in_mono = None
        self.plugged_in_at = None
        self.unplugged_at = None

    def track_plug(self, online, state, now, wall):
        if online:
            if self.previous_online is False:
                self.plugged_in_mono, self.plugged_in_at = now, wall
        else:
            self.plugged_in_mono = self.plugged_in_at = None
        if state != self.last_state:
            if state != DISCHARGING:
                self.unplugged_at = None
            elif self.last_state is not None:
                self.unplugged_at = wall
        self.previous_online, self.last_state = online, state

    def just_plugged(self, now):
        return (self.plugged_in_mono is not None
                and now - self.plugged_in_mono < Battery.JUST_PLUGGED_SECONDS)

    def update(self, snapshot, system, now, wall):
        """回傳含顯示狀態與流向的 Estimates；now 為單調時鐘、wall 為 Unix 時間。"""
        state = self.presentation.state(snapshot, now)
        self.track_plug(snapshot.ac_online, state, now, wall)
        averages = self.averages.update(state, snapshot, now)
        system = self.watch.update(system, state, snapshot.draining, now)
        result = estimate(snapshot, averages, state, system, self.just_plugged(now))
        holding = (state in HOLDING_STATES and not result.draining_on_ac
                   and not snapshot.draining_above_limit)
        flow = self.flow.kind(snapshot.ac_online, result.battery_w or 0.0, holding, now)
        return replace(result, display_flow=flow)


def laptop_history_sample(power):
    """筆電每分鐘一筆：電量、整機耗電（未知為 None）、是否接電、是否顯示為充電中。"""
    pct = power.battery.percent
    if pct is None:
        return None
    watts = power.estimates.system_w
    if watts is not None and not 0 <= watts < Power.SYSTEM_MAX_W:
        watts = None
    return HistorySample(t=round(power.stamp, TIME_DECIMALS), pct=int(pct),
                         watts=None if watts is None else round(watts, WATTS_DECIMALS),
                         plugged=power.battery.ac_online,
                         charging=power.estimates.state == CHARGING, ups_pct=None,
                         ups_online=None)
