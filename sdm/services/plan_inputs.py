"""智慧充電的即時輸入（不載入 GTK）：由筆電耗電快照（LaptopPower）算出判斷用的狀態、
充電速度與建立排程前的預估（參考專案 BatteryStore.chargingPlanInput／chargingForecast）。

- 充到目標的分鐘數：充電中用近 3 分鐘的平均（estimates.minutes_to_charge）；沒有時用最近 7 天
  歷史的充電速度推算；都沒有時交給判斷邏輯以每小時 20% 估算。
- 預估用的速度：充電中用目前的平均充電功率（或電流）÷ 滿充容量；沒在充電時用歷史速度；
  都沒有就以每小時 20% 估算。
- 「沒在充」：接著電源但顯示狀態是閒置、到達上限或已充飽（系統暫停的判斷用）。
"""
import math

from sdm.constants import Battery, SmartCharging
from sdm.core.battery import CHARGING, FULL, LIMIT_REACHED, PLUGGED_IDLE
from sdm.core.charging_plan import PlanInput, forecast
from sdm.core.estimates import capacities, minutes_to_charge, usable_rate

HOLDING = (PLUGGED_IDLE, LIMIT_REACHED, FULL)
SECONDS_PER_MINUTE = 60


def live_rate(power):
    """充電中的速度（每小時 %）：平均充電功率或電流 ÷ 滿充容量；沒有時為 None。"""
    estimates = getattr(power, "estimates", None)
    if estimates is None or estimates.state != CHARGING:
        return None
    averages = estimates.averages
    _, full = capacities(power.battery, averages.unit)
    if not full or not usable_rate(averages.charge, averages.unit):
        return None
    return averages.charge / full * Battery.PERCENT


def estimated_minutes(power, target, history_rate):
    minutes = minutes_to_charge(target, power.battery, power.estimates)
    percent = power.battery.percent
    if minutes is None and history_rate and target > percent:
        minutes = math.ceil((target - percent) / history_rate * SECONDS_PER_MINUTE)
    return minutes


def plan_input(power, current_limit, limit_supported, target, history_rate, now, options=()):
    """判斷用的即時狀態；沒有筆電電量時為 None。options 是驅動接受的上限（滑桿的選項）。"""
    battery = getattr(power, "battery", None)
    if battery is None or battery.percent is None:
        return None
    state = power.estimates.state
    return PlanInput(now=now, percent=int(battery.percent), connected=bool(battery.ac_online),
                     limit_supported=limit_supported, current_limit=current_limit,
                     estimated_minutes=estimated_minutes(power, target, history_rate),
                     charging=state == CHARGING,
                     paused=bool(battery.ac_online) and state in HOLDING,
                     options=tuple(options))


def forecast_rate(power, history_rate):
    rate = live_rate(power)
    if rate is None:
        rate = history_rate
    return SmartCharging.FALLBACK_RATE if rate is None else rate


def make_forecast(power, target, deadline, now, history_rate):
    """照目前的充電速度，在 deadline 前能不能充到 target；沒有筆電電量時為 None。"""
    battery = getattr(power, "battery", None)
    if battery is None or battery.percent is None:
        return None
    return forecast(int(battery.percent), target, (deadline - now) / SECONDS_PER_MINUTE,
                    forecast_rate(power, history_rate))
