"""充電狀態與電力流向的顯示濾波，移植參考專案 ChargingPresentation／PowerFlowPresentation。

只穩定「顯示」的狀態，不修改讀到的旗標、電流或功率：
- 第一次讀取與實際插拔立即顯示；接電中的狀態變化要持續 10 秒才切換。
- 沒有充電上限時，電量 100%（或核心回報 Full 且 ≥98%）鎖定為已充飽，低於 98% 才解除；
  充電上限改變時整個重來。
- 流向圖：接電未充電時 ±2 W 內視為由變壓器直接供電；方向明顯相反（超過 5 W）時立即切換。
時間參數 uptime 為單調時鐘秒數。
"""
from typing import NamedTuple

from sdm.constants import Battery
from sdm.core.battery import DISCHARGING, FULL

FLOW_BATTERY = "battery"      # 電池 → 系統
FLOW_ADAPTER = "adapter"      # 變壓器 → 系統，電池閒置
FLOW_CHARGING = "charging"    # 變壓器 → 系統與電池
FLOW_MIXED = "mixed"          # 變壓器與電池一起供電


class Pending(NamedTuple):
    value: str
    since: float


class ChargingPresentation:
    def __init__(self):
        self._reset()

    def _reset(self):
        self.displayed = None
        self.full_latched = False
        self.pending = None
        self.last_limit = None

    def state(self, snapshot, uptime):
        """回傳要顯示的狀態（battery.DISCHARGING／CHARGING／PLUGGED_IDLE／FULL／LIMIT_REACHED）。"""
        raw = snapshot.state
        if not snapshot.ac_online:
            self._reset()
            self.displayed = DISCHARGING
            return DISCHARGING
        if snapshot.limit != self.last_limit:
            self._reset()
            self.last_limit = snapshot.limit
        candidate = raw
        if snapshot.limit is None:
            latched = self._full_latch(snapshot, raw)
            if latched is not None:
                return latched
            candidate = FULL if self.full_latched and not snapshot.charging else raw
        return self._confirm(candidate, uptime)

    def _full_latch(self, snapshot, raw):
        """有上限時不套用滿電緩衝；回傳 None 代表交給一般的確認流程。"""
        level = snapshot.level
        if level >= Battery.FULL_PERCENT or (snapshot.full_flag
                                             and level >= Battery.FULL_RELEASE_PERCENT):
            self.full_latched, self.pending, self.displayed = True, None, FULL
            return FULL
        if self.full_latched and level < Battery.FULL_RELEASE_PERCENT:
            self.full_latched, self.pending, self.displayed = False, None, raw
            return raw
        return None

    def _confirm(self, candidate, uptime):
        previous = self.displayed
        if previous is None or previous == DISCHARGING:
            self.displayed, self.pending = candidate, None
            return candidate
        if candidate == previous:
            self.pending = None
            return previous
        if (self.pending is not None and self.pending.value == candidate
                and uptime - self.pending.since >= Battery.CONFIRM_SECONDS):
            self.displayed, self.pending = candidate, None
            return candidate
        if self.pending is None or self.pending.value != candidate:
            self.pending = Pending(candidate, uptime)
        return previous


def raw_flow_kind(connected, watts, holding):
    """holding：接著電源但沒在充電（滿電、達上限、暫停），±2 W 內視為由變壓器直接供電。"""
    if not connected:
        return FLOW_BATTERY
    threshold = Battery.FLOW_HOLD_W if holding else Battery.FLOW_ACTIVE_W
    if watts > threshold:
        return FLOW_CHARGING
    if watts < -threshold:
        return FLOW_MIXED
    return FLOW_ADAPTER


class PowerFlowPresentation:
    def __init__(self):
        self.displayed = None
        self.pending = None

    def kind(self, connected, watts, holding, uptime):
        candidate = raw_flow_kind(connected, watts, holding)
        if not (holding and connected):
            self.displayed, self.pending = candidate, None
            return candidate
        # 初次開啟或重新插電也先確認，避免第一筆功率差尖峰切換圖形
        previous = FLOW_ADAPTER if self.displayed in (None, FLOW_BATTERY) else self.displayed
        self.displayed = previous
        if previous == candidate:
            self.pending = None
            return previous
        swing = Battery.FLOW_SWING_W
        if (previous == FLOW_MIXED and watts > swing) or (previous == FLOW_CHARGING
                                                          and watts < -swing):
            self.displayed, self.pending = candidate, None
            return candidate
        if (self.pending is not None and self.pending.value == candidate
                and uptime - self.pending.since >= Battery.CONFIRM_SECONDS):
            self.displayed, self.pending = candidate, None
            return candidate
        if self.pending is None or self.pending.value != candidate:
            self.pending = Pending(candidate, uptime)
        return previous
