"""剩餘時間與充電時間（分鐘），移植參考專案 Estimator 與 BatteryStore 的平均規則。

- 系統預估來自 UPower：TimeToEmpty／TimeToFull 為 0 代表還在估算（None，畫面顯示「計算中…」）；
  UPower 有回報這顆電池時，充滿時間絕不改用直線推算（80% 以上充電會變慢，直線推算太樂觀）。
- 依近 3 分鐘平均：充電與放電分開平均（τ = 180 秒），狀態一改變就重算；App 執行中才開始充電時，
  頭 60 秒的電流還在爬升，不納入平均。平均電流超過 10 mA（或功率超過 0.1 W）才推算。
- 「到 10%」＝ 總時間 ×（電量 − 10）÷ 電量。
"""
from dataclasses import dataclass, field, replace
import math
from typing import Optional

from sdm.constants import Battery
from sdm.core.battery import (CHARGING, DISCHARGING, FULL, LIMIT_REACHED, PLUGGED_IDLE,
                              UNIT_AMPS, UNIT_WATTS, round_half_up)
from sdm.core.ema import ema
from sdm.core.presentation import raw_flow_kind

MINUTES = 60
PERCENT = 100
HOLDING_STATES = (FULL, LIMIT_REACHED, PLUGGED_IDLE)


@dataclass(frozen=True)
class SystemTimes:
    """系統（UPower）預估；reports 為 False 代表沒有系統預估來源。"""
    reports: bool = False
    to_empty_min: Optional[int] = None
    to_full_min: Optional[int] = None


@dataclass(frozen=True)
class Averages:
    """近 3 分鐘平均的放電與充電速率；unit 為 W（功率）或 A（電流）。"""
    discharge: Optional[float] = None
    charge: Optional[float] = None
    unit: Optional[str] = None


@dataclass(frozen=True)
class Estimates:
    state: str
    battery_w: Optional[float] = None       # 正值充入電池、負值放出
    system_w: Optional[float] = None
    adapter_w: Optional[float] = None
    health_pct: Optional[float] = None
    system_to_empty: Optional[int] = None
    system_to_10: Optional[int] = None
    live_to_empty: Optional[int] = None
    live_to_10: Optional[int] = None
    to_full: Optional[int] = None
    to_limit: Optional[int] = None
    averages: Averages = field(default_factory=Averages)
    draining_on_ac: bool = False            # 接著電源但供電不足，電池仍在放電
    system_reports: bool = False
    display_flow: Optional[str] = None      # 經過顯示濾波的流向（沒有時依瓦數判斷）

    @property
    def headline_to_empty(self):
        return self.system_to_empty if self.system_to_empty is not None else self.live_to_empty

    @property
    def headline_to_full(self):
        return self.to_limit if self.to_limit is not None else self.to_full

    @property
    def flow_kind(self):
        if self.display_flow is not None:
            return self.display_flow
        holding = self.state in HOLDING_STATES and not self.draining_on_ac
        return raw_flow_kind(self.state != DISCHARGING, self.battery_w or 0.0, holding)


def live_minimum(unit):
    return Battery.LIVE_MIN_W if unit == UNIT_WATTS else Battery.LIVE_MIN_A


def capacities(snapshot, unit):
    """（目前、滿充）容量，單位與平均速率相同：W 對瓦時、A 對安培小時。"""
    if unit == UNIT_WATTS:
        return snapshot.energy_now_wh, snapshot.energy_full_wh
    if unit == UNIT_AMPS:
        return snapshot.charge_now_ah, snapshot.charge_full_ah
    return None, None


def usable_rate(value, unit):
    return value is not None and value > live_minimum(unit)


def to_empty_fields(snapshot, averages, system):
    """用電池（或供電不足）時：系統與依平均推算的到 0%、到 10%。"""
    pct, reserve = snapshot.level, Battery.RESERVE_PERCENT
    fields = dict(system_to_empty=system.to_empty_min if system.reports else None)
    total = fields["system_to_empty"]
    if total is not None and pct > 0:
        fields["system_to_10"] = max(0, total * (pct - reserve) // pct)
    remaining, _ = capacities(snapshot, averages.unit)
    if remaining is not None and usable_rate(averages.discharge, averages.unit) and pct > 0:
        minutes = remaining / averages.discharge * MINUTES
        fields["live_to_empty"] = round_half_up(minutes)
        fields["live_to_10"] = max(0, round_half_up(minutes * (pct - reserve) / pct))
    return fields


def to_full_minutes(snapshot, averages, system):
    """UPower 有回報就只用它（還在估算為 None）；沒有系統預估時才依平均直線推算。"""
    if system.reports:
        return system.to_full_min
    remaining, full = capacities(snapshot, averages.unit)
    if full is None or remaining is None or not usable_rate(averages.charge, averages.unit):
        return None
    return round_half_up(max(0.0, full - remaining) / averages.charge * MINUTES)


def minutes_to_charge(target, snapshot, estimates):
    """充電中充到 target% 大約要多久（參考 Estimator.minutesToCharge）。"""
    pct = snapshot.level
    if estimates.state != CHARGING or pct >= target:
        return None
    averages = estimates.averages
    _, full = capacities(snapshot, averages.unit)
    if full is not None and usable_rate(averages.charge, averages.unit):
        needed = full * (target - pct) / PERCENT
        return round_half_up(needed / averages.charge * MINUTES)
    if estimates.to_full is not None and pct < PERCENT:
        return estimates.to_full * (target - pct) // (PERCENT - pct)
    return None


def estimate(snapshot, averages=Averages(), display_state=None, system=SystemTimes(),
             just_plugged=False):
    """由快照、平均與系統預估算出顯示用的數值；display_state 為顯示濾波後的狀態。"""
    online = snapshot.ac_online
    state = (display_state or snapshot.state) if online else DISCHARGING
    battery_w = snapshot.battery_w
    if battery_w is not None and not online:
        battery_w = min(0.0, battery_w)
    draining = state == PLUGGED_IDLE and snapshot.draining
    fields = dict(state=state, battery_w=battery_w, system_w=snapshot.system_w,
                  adapter_w=snapshot.adapter_w if online else None,
                  health_pct=snapshot.health_pct, averages=averages, draining_on_ac=draining,
                  system_reports=system.reports)
    if state == DISCHARGING or draining:
        fields.update(to_empty_fields(snapshot, averages, system))
    result = Estimates(**fields)
    if state == CHARGING:
        result = replace(result, to_full=to_full_minutes(snapshot, averages, system))
        if snapshot.limit is not None:
            result = replace(result, to_limit=minutes_to_charge(snapshot.limit, snapshot, result))
    # 剛插電 30 秒內電池還在放電是充電器還沒開始充電，不算供電不足（時間值照算，與參考相同）
    if just_plugged and result.draining_on_ac:
        result = replace(result, draining_on_ac=False)
    return result


class LiveAverages:
    """近 3 分鐘的充電與放電平均速率；在同一個執行緒（持有電源服務的鎖）中更新。"""

    def __init__(self):
        self.discharge = self.charge = None
        self.unit = None
        self.last_stamp = None
        self.last_state = None
        self.charging_since = None

    def update(self, state, snapshot, now):
        """state 為顯示濾波後的狀態；now 為單調時鐘秒數。"""
        dt = 0.0 if self.last_stamp is None else now - self.last_stamp
        if state != self.last_state:
            if self.last_state is not None:
                self.discharge = self.charge = None
            started_mid_charge = self.last_state is None
            self.charging_since = ((-math.inf if started_mid_charge else now)
                                   if state == CHARGING else None)
        unit = snapshot.rate_unit
        if unit != self.unit:
            self.discharge = self.charge = None
            self.unit = unit
        self._add(self._rate(snapshot, unit), now, dt)
        self.last_stamp, self.last_state = now, state
        return Averages(self.discharge, self.charge, self.unit)

    @staticmethod
    def _rate(snapshot, unit):
        if unit == UNIT_WATTS:
            return snapshot.battery_w
        return snapshot.current_a if unit == UNIT_AMPS else None

    def _add(self, rate, now, dt):
        if rate is None:
            return
        since = -math.inf if self.charging_since is None else self.charging_since
        if rate < 0:
            self.discharge = ema(self.discharge, -rate, dt)
        elif rate > 0 and now - since >= Battery.CHARGE_WARMUP_SECONDS:
            self.charge = ema(self.charge, rate, dt)
