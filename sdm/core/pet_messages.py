"""桌面寵物要說的話（純邏輯，移植參考專案 DesktopPetMessages）。

- 想像框（滑鼠移過去）：目前的電量與狀態，有排程時加一行排程說明；桌機是整機耗電與供電來源。
- 對話框（點一下，每點一次換一句）：心情在第一句，之後是今天的用電、電池健康度與整機耗電；
  沒有資料的那句不出現。桌機改說不斷電系統的電量與負載。
「整台 Mac」改成「整台電腦」，其餘文字與參考專案相同。電量讀不到時不說百分比（不會說成 0%）。
"""
from dataclasses import dataclass
from typing import Optional

from sdm.constants import Companion
from sdm.core.battery import CHARGING, DISCHARGING, FULL, LIMIT_REACHED, PLUGGED_IDLE

MINUTES_PER_HOUR = 60
PURRS = ("呼嚕呼嚕～", "好舒服喔", "再摸一下嘛", "喵～")


@dataclass(frozen=True)
class PetStatus:
    """筆電：只放數值，方便測試。percent 讀不到時為 None。"""
    percent: Optional[int]
    state: str
    draining_on_ac: bool = False        # 接著電源但供電不足，電池仍在放電
    minutes_to_empty: Optional[int] = None
    minutes_to_full: Optional[int] = None   # 充到上限（有上限時）或充滿要多久
    limit: Optional[int] = None         # 有充電上限、而且正在充到上限時才有
    low_threshold: int = 20
    plan_title: Optional[str] = None    # 智慧充電的狀態與說明（沒有排程時是 None）
    plan_detail: Optional[str] = None
    plan_waiting: bool = False
    plan_ready: bool = False
    used_today: Optional[int] = None    # 今天用掉、補回的電量（沒有今天的紀錄時是 None）
    charged_today: Optional[int] = None
    health_percent: Optional[int] = None
    cycle_count: Optional[int] = None
    system_watts: Optional[float] = None


@dataclass(frozen=True)
class DesktopPetStatus:
    """桌機：整機耗電、供電來源與不斷電系統。"""
    system_watts: Optional[float]
    supply: str                         # 「市電」或「不斷電系統電池」
    on_ups_battery: bool = False
    ups_percent: Optional[float] = None
    ups_minutes: Optional[int] = None
    ups_load_pct: Optional[float] = None
    busy: bool = False


def duration(minutes):
    if minutes < MINUTES_PER_HOUR:
        return f"{max(1, minutes)} 分鐘"
    hours, rest = divmod(minutes, MINUTES_PER_HOUR)
    return f"{hours} 小時" if rest == 0 else f"{hours} 小時 {rest} 分"


def _charging_text(s):
    if s.minutes_to_full is None:
        return "充電中"
    if s.limit is not None:
        return f"充電中，{duration(s.minutes_to_full)}後到 {s.limit}%"
    return f"充電中，{duration(s.minutes_to_full)}後充滿"


def state_text(s):
    if s.state == CHARGING:
        return _charging_text(s)
    if s.state == DISCHARGING:
        return "使用電池中" if s.minutes_to_empty is None else \
            f"還能用 {duration(s.minutes_to_empty)}"
    if s.state == LIMIT_REACHED:
        return "已達充電上限，用變壓器供電"
    if s.state == FULL:
        return "已充飽"
    return "供電不足，電池在放電" if s.draining_on_ac else "接著電源，暫停充電"


def thought(s):
    lines = [state_text(s) if s.percent is None else f"{s.percent}%・{state_text(s)}"]
    if s.plan_title is not None:
        lines.append(s.plan_title + ("" if s.plan_detail is None else "：" + s.plan_detail))
    return "\n".join(lines)


def mood(s):
    if s.plan_ready:
        return "準備好了，隨時可以出門！"
    if s.plan_waiting and s.plan_detail is not None:
        return "我先睡一下，" + s.plan_detail
    if s.state == DISCHARGING or (s.state == PLUGGED_IDLE and s.draining_on_ac):
        if s.percent is None:
            return "陪你一起努力"
        if s.percent <= Companion.CRITICAL_PERCENT:
            return "快沒電了！拜託快接上電源"
        if s.percent <= s.low_threshold:
            return "肚子有點餓，記得幫我充電"
        return f"陪你一起努力，還有 {s.percent}%"
    if s.state == CHARGING:
        return "充電中，好溫暖～"
    if s.state in (LIMIT_REACHED, FULL):
        return "吃飽了，現在靠變壓器供電"
    return "插著電在休息"


def _watts_line(watts):
    return None if watts is None or watts <= 0 else f"現在整台電腦用了 {watts:.1f} W"


def sayings(s):
    lines = [mood(s)]
    if s.used_today is not None and s.charged_today is not None:
        lines.append(f"今天用掉 {s.used_today}%，補回 {s.charged_today}%")
    if s.health_percent is not None:
        lines.append(f"電池健康度 {s.health_percent}%" + (
            "" if s.cycle_count is None else f"，充過 {s.cycle_count} 次"))
    watts = _watts_line(s.system_watts)
    return lines + ([watts] if watts else [])


def _rounded_watts(watts):
    return f"{round(watts)} W"


def desktop_thought(s):
    total = "整機耗電讀取中" if s.system_watts is None else "整機 " + _rounded_watts(s.system_watts)
    lines = [f"{total}・{s.supply}"]
    if s.on_ups_battery and s.ups_percent is not None:
        backup = f"不斷電系統 {round(s.ups_percent)}%"
        if s.ups_minutes is not None:
            backup += f"・還能撐 {duration(s.ups_minutes)}"
        lines.append(backup)
    return "\n".join(lines)


def desktop_mood(s):
    if s.on_ups_battery:
        if s.ups_minutes is None:
            return "停電了！先靠不斷電系統撐著"
        return f"停電了！不斷電系統還能撐 {duration(s.ups_minutes)}"
    if s.busy and s.system_watts is not None:
        return "好忙喔，整機用了 " + _rounded_watts(s.system_watts)
    return "插著電在休息"


def desktop_sayings(s):
    lines = [desktop_mood(s)]
    watts = _watts_line(s.system_watts)
    if watts:
        lines.append(watts)
    if s.ups_percent is not None and s.ups_load_pct is not None:
        lines.append(f"不斷電系統電量 {round(s.ups_percent)}%，負載 {round(s.ups_load_pct)}%")
    return lines
