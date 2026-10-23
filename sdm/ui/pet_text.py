"""由耗電快照產生充電夥伴的表情與桌面寵物的文字；不載入 GTK，方便測試。

筆電（LaptopPower）與桌機（DesktopPower）各自轉成 sdm.core.pet_messages 的狀態，
文字本身在那裡產生。桌機的不斷電系統通訊中斷時視同沒有不斷電系統。
"""
from sdm.core.battery import round_half_up
from sdm.core.companion import CompanionState, busy, desktop_state, laptop_state
from sdm.core.pet_messages import (DesktopPetStatus, PetStatus, desktop_sayings,
                                   desktop_thought, sayings, thought)
from sdm.services.battery_service import LaptopPower
from sdm.services.power_service import supply_label
from sdm.ui.power_text import on_ups_battery


def _ups(power):
    ups = power.ups
    return None if ups is None or ups.comm_lost else ups


def companion_state(power, low_threshold):
    if power is None:
        return CompanionState()
    if isinstance(power, LaptopPower):
        return laptop_state(power.percent, power.state, power.estimates.draining_on_ac,
                            low_threshold)
    ups = _ups(power)
    return desktop_state(on_ups_battery(power), ups.charge_pct if ups else None,
                         ups.load_pct if ups else None, power.system_w)


def _laptop_status(power, low_threshold, recap):
    e, battery = power.estimates, power.battery
    today = recap is not None and recap.start_percent is not None
    return PetStatus(
        percent=power.percent if power.percent is not None else 0, state=power.state,
        draining_on_ac=e.draining_on_ac, minutes_to_empty=e.headline_to_empty,
        minutes_to_full=e.headline_to_full,
        limit=battery.limit if e.to_limit is not None else None, low_threshold=low_threshold,
        used_today=recap.used_percent if today else None,
        charged_today=recap.charged_percent if today else None,
        health_percent=None if e.health_pct is None else round_half_up(e.health_pct),
        cycle_count=battery.cycles if battery.cycles else None, system_watts=e.system_w)


def _desktop_status(power):
    ups = _ups(power)
    minutes = ups.time_left_min if ups else None
    return DesktopPetStatus(
        system_watts=power.system_w, supply=supply_label(power.supply),
        on_ups_battery=on_ups_battery(power), ups_percent=ups.charge_pct if ups else None,
        ups_minutes=None if minutes is None else round(minutes),
        ups_load_pct=ups.load_pct if ups else None,
        busy=busy(ups.load_pct if ups else None, power.system_w))


def pet_status(power, low_threshold, recap=None):
    if power is None:
        return None
    if isinstance(power, LaptopPower):
        return _laptop_status(power, low_threshold, recap)
    return _desktop_status(power)


def thought_for(power, low_threshold):
    """滑鼠移到寵物上時的想像框；還沒有快照時為 None。"""
    status = pet_status(power, low_threshold)
    if status is None:
        return None
    return thought(status) if isinstance(status, PetStatus) else desktop_thought(status)


def sayings_for(power, low_threshold, recap):
    """點一下時依序說的話；還沒有快照時是空的。"""
    status = pet_status(power, low_threshold, recap)
    if status is None:
        return []
    return sayings(status) if isinstance(status, PetStatus) else desktop_sayings(status)
