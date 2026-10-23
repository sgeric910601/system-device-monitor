"""充電夥伴的外觀與表情（純邏輯，不載入 GTK）。

外觀（preferences.companion_appearance）：貓咪、電池夥伴、電量條、貓咪大頭照與我的照片。
點一下夥伴換下一個外觀；還沒選照片時跳過「我的照片」。

表情（CompanionState）：
- 筆電依電池狀態：充電中躺在充電墊上（綠色）、充飽或到達上限很開心、電量低（≤ 低電量門檻，
  ≤ 10% 為紅色）或接電時供電不足會擔心，其他時候休息（打瞌睡）。
- 桌機依不斷電系統與負載：改用不斷電系統電池時擔心、負載高（不斷電系統負載 ≥ 70%，
  沒有負載讀值時整機 ≥ 400 W）時很忙，其他時候休息；電量條顯示不斷電系統的電量。
"""
from dataclasses import dataclass
from typing import Optional

from sdm.constants import Battery, Companion
from sdm.core.battery import CHARGING, DISCHARGING, FULL, LIMIT_REACHED, PLUGGED_IDLE

APPEARANCES = ("cat", "buddy", "bar", "headshot", "photo")
PHOTO = "photo"
HEADSHOT = "headshot"
TITLES = {"cat": "貓咪", "buddy": "電池夥伴", "bar": "電量條", "headshot": "貓咪大頭照",
          PHOTO: "我的照片"}

CHARGING_MOOD, FULL_MOOD, LOW, IDLE, BUSY = "charging", "full", "low", "idle", "busy"
NEUTRAL, GOOD, WARNING, CRITICAL = "neutral", "good", "warning", "critical"


@dataclass(frozen=True)
class CompanionState:
    mood: str = IDLE
    level: Optional[float] = None       # 0–1：筆電電量或不斷電系統電量；沒有時為 None
    tone: str = NEUTRAL                 # 顏色：neutral 前景色、good 綠、warning 橘、critical 紅
    on_pad: bool = False                # 貓咪躺在充電墊上（充電中）

    @property
    def resting(self):
        """休息中：貓咪頭上飄 Z，電池夥伴閉著眼睛。"""
        return self.mood == IDLE


def next_appearance(current, has_photo):
    """點一下換下一個外觀；沒有自己的照片時跳過「我的照片」，不認得的外觀回到貓咪。"""
    if current not in APPEARANCES:
        return APPEARANCES[0]
    order = [name for name in APPEARANCES if has_photo or name != PHOTO]
    if current not in order:
        return order[0]
    return order[(order.index(current) + 1) % len(order)]


def _level(percent):
    return None if percent is None else max(0.0, min(1.0, percent / Battery.PERCENT))


def _low_tone(percent):
    return CRITICAL if percent is not None and percent <= Companion.CRITICAL_PERCENT else WARNING


def laptop_state(percent, state, draining_on_ac, low_threshold):
    level = _level(percent)
    if state == CHARGING:
        return CompanionState(CHARGING_MOOD, level, GOOD, on_pad=True)
    if state in (FULL, LIMIT_REACHED):
        return CompanionState(FULL_MOOD, level, GOOD)
    if state == PLUGGED_IDLE:
        if draining_on_ac:
            return CompanionState(LOW, level, _low_tone(percent))
        return CompanionState(IDLE, level, GOOD)
    if state == DISCHARGING and percent is not None and percent <= low_threshold:
        return CompanionState(LOW, level, _low_tone(percent))
    return CompanionState(IDLE, level, NEUTRAL)


def busy(load_pct, system_w):
    if load_pct is not None:
        return load_pct >= Companion.BUSY_LOAD_PCT
    return system_w is not None and system_w >= Companion.BUSY_WATTS


def desktop_state(on_ups_battery, ups_percent, load_pct, system_w):
    level = _level(ups_percent)
    if on_ups_battery:
        return CompanionState(LOW, level, _low_tone(ups_percent))
    if busy(load_pct, system_w):
        return CompanionState(BUSY, level, NEUTRAL)
    return CompanionState(IDLE, level, NEUTRAL)
