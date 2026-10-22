"""筆電電池快照（純邏輯）：把 sysfs 讀值合併成一個不可變的 BatterySnapshot。

- 方向：驅動對電流與功率的正負號不一致，一律依狀態決定；Charging 為充入（正），
  Discharging 為放出（負）。其他狀態（Unknown、Not charging、Full）接著電源時先看
  UPower 的狀態（充電中／放電中），仍無法判斷才視為閒置（0）；沒接電源時視為放出。
- 多顆電池：能量與功率相加；電量＝能量合計 ÷ 滿充合計，讀不到時改用各顆電量的平均。
  只有一顆電池時以核心回報的 capacity 為準，與系統顯示的電量一致。
- 判斷規則沿用參考專案：chargingNow（≥3 W 才算充電）、Estimator.state、drainingAboveLimit。
- 系統耗電：用電池時＝電池放出的功率；接電時只有量得到變壓器的實際輸入才算
  變壓器 − 充入電池，否則未知，不猜測。USB-C PD 的電壓 × 電流是協商的契約，
  不是實際輸入，只記在 adapter_negotiated_w；目前沒有任何 Linux 來源能填入 adapter_w。
"""
from dataclasses import dataclass
import math
from typing import Optional

from sdm.constants import Battery

DISCHARGING = "discharging"
CHARGING = "charging"
PLUGGED_IDLE = "plugged_idle"
FULL = "full"
LIMIT_REACHED = "limit_reached"

STATUS_CHARGING = "Charging"
STATUS_DISCHARGING = "Discharging"
STATUS_NOT_CHARGING = "Not charging"
STATUS_FULL = "Full"
STATUS_UNKNOWN = "Unknown"
PLUGGED_STATUSES = (STATUS_CHARGING, STATUS_FULL, STATUS_NOT_CHARGING)
UNIT_WATTS = "W"
UNIT_AMPS = "A"


@dataclass(frozen=True)
class BatterySnapshot:
    """某一時間點的電池狀態；battery_w、current_a 正值為充入電池、負值為放出。"""
    percent: Optional[int] = None
    ac_online: bool = False
    status: str = ""
    charging_flag: bool = False
    full_flag: bool = False
    battery_w: Optional[float] = None
    current_a: Optional[float] = None
    voltage_v: Optional[float] = None
    adapter_w: Optional[float] = None       # 實際量到的變壓器輸入功率（目前沒有來源）
    adapter_negotiated_w: Optional[float] = None   # USB-C PD 協商的契約功率
    adapter_rated_w: Optional[float] = None
    adapter_v: Optional[float] = None       # 協商的電壓與電流
    adapter_a: Optional[float] = None
    limit: Optional[int] = None             # 充電上限（低於 100% 才算有設定）
    limit_start: Optional[int] = None
    limit_supported: bool = False
    energy_now_wh: Optional[float] = None
    energy_full_wh: Optional[float] = None
    energy_design_wh: Optional[float] = None
    charge_now_ah: Optional[float] = None
    charge_full_ah: Optional[float] = None
    charge_design_ah: Optional[float] = None
    rate_unit: Optional[str] = None         # 剩餘時間用功率（W）還是電流（A）推算
    cycles: Optional[int] = None
    temperature_c: Optional[float] = None
    capacity_level: str = ""
    health: str = ""
    technology: str = ""
    manufacturer: str = ""
    model: str = ""
    behaviour: str = ""
    count: int = 1

    @property
    def level(self):
        """比較用的電量；讀不到時當成 0。"""
        return self.percent or 0

    @property
    def charging(self):
        """參考 chargingNow：有功率讀值時以功率為準，模糊區間才看核心的狀態旗標。"""
        if not self.ac_online:
            return False
        watts = self.battery_w
        if watts is None:
            return self.charging_flag
        if self.limit is not None and self.level >= self.limit:
            return self.charging_flag and watts >= Battery.CHARGING_MIN_W
        if watts >= Battery.CHARGING_MIN_W:
            return True
        if watts < Battery.IDLE_MAX_W:
            return False
        return self.charging_flag

    @property
    def draining(self):
        """電池正在放電（功率低於 -0.5 W；沒有功率時看電流是否低於 -50 mA）。"""
        if self.battery_w is not None:
            return self.battery_w < Battery.DRAIN_W
        return self.current_a is not None and self.current_a < Battery.DRAIN_A

    @property
    def draining_above_limit(self):
        return self.limit is not None and self.level > self.limit and self.draining

    @property
    def state(self):
        """參考 Estimator.state：未經顯示濾波的原始狀態。"""
        if not self.ac_online:
            return DISCHARGING
        if self.charging:
            return CHARGING
        if self.limit is not None and self.level >= self.limit - Battery.LIMIT_MARGIN:
            return LIMIT_REACHED
        if self.draining:
            return PLUGGED_IDLE
        if self.full_flag or self.level >= Battery.PERCENT:
            return FULL
        return PLUGGED_IDLE

    @property
    def system_w(self):
        """整機耗電；接電又量不到變壓器輸入時為 None。"""
        if self.battery_w is None:
            return None
        if not self.ac_online:
            return -self.battery_w if self.battery_w < 0 else None
        if self.adapter_w is None:
            return None
        value = self.adapter_w - self.battery_w
        return value if value >= 0 else None

    @property
    def health_pct(self):
        """健康度＝滿充 ÷ 設計容量 × 100（能量優先，否則用電量）。"""
        for full, design in ((self.energy_full_wh, self.energy_design_wh),
                             (self.charge_full_ah, self.charge_design_ah)):
            if full is not None and design:
                return full / design * Battery.PERCENT
        return None


def round_half_up(value):
    """四捨五入到整數（Python 的 round 是銀行家捨入，與參考專案不同）。"""
    return int(math.floor(value + 0.5)) if value >= 0 else -int(math.floor(-value + 0.5))


def direction(status, ac_online, upower_state=None):
    if status == STATUS_CHARGING:
        return 1
    if status == STATUS_DISCHARGING:
        return -1
    if not ac_online:
        return -1
    if upower_state == Battery.UPOWER_CHARGING:
        return 1
    return -1 if upower_state == Battery.UPOWER_DISCHARGING else 0


def conversion_volts(reading):
    return reading.voltage_min_design_v or reading.voltage_v


def energy_values(reading):
    """（現在、滿充、設計）瓦時；只有電量讀值時以設計最低電壓換算。"""
    volts = conversion_volts(reading)
    values = []
    for energy, charge in ((reading.energy_now_wh, reading.charge_now_ah),
                           (reading.energy_full_wh, reading.charge_full_ah),
                           (reading.energy_design_wh, reading.charge_design_ah)):
        if energy is None and charge is not None and volts:
            energy = charge * volts
        values.append(energy)
    return tuple(values)


def charge_values(reading):
    """（現在、滿充、設計）安培小時；只有能量讀值時以設計最低電壓換算。"""
    volts = conversion_volts(reading)
    values = []
    for charge, energy in ((reading.charge_now_ah, reading.energy_now_wh),
                           (reading.charge_full_ah, reading.energy_full_wh),
                           (reading.charge_design_ah, reading.energy_design_wh)):
        if charge is None and energy is not None and volts:
            charge = energy / volts
        values.append(charge)
    return tuple(values)


def signed_flow(reading, ac_online, upower_state=None):
    """（功率 W、電流 A），依狀態加上方向；兩者可互相以電壓換算。"""
    sign = direction(reading.status, ac_online, upower_state)
    watts, amps, volts = reading.power_w, reading.current_a, reading.voltage_v
    if watts is None and amps is not None and volts:
        watts = amps * volts
    if amps is None and watts is not None and volts:
        amps = watts / volts
    return (None if watts is None else sign * watts + 0.0,
            None if amps is None else sign * amps + 0.0)


def total(values):
    """全部都有值才相加，否則為 None。"""
    values = list(values)
    return sum(values) if values and all(value is not None for value in values) else None


def known_sum(values):
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def combined_percent(batteries, energies):
    """一顆電池用核心的 capacity；多顆用能量合計，讀不到時用電量平均。"""
    capacities = [battery.capacity for battery in batteries if battery.capacity is not None]
    if len(batteries) == 1 and capacities:
        return capacities[0]
    now, full = total(item[0] for item in energies), total(item[1] for item in energies)
    if now is not None and full:
        return min(Battery.PERCENT, round_half_up(now / full * Battery.PERCENT))
    return round_half_up(sum(capacities) / len(capacities)) if capacities else None


def combined_status(statuses):
    for status in (STATUS_CHARGING, STATUS_DISCHARGING, STATUS_NOT_CHARGING):
        if status in statuses:
            return status
    if statuses and all(status == STATUS_FULL for status in statuses):
        return STATUS_FULL
    return statuses[0] if len(statuses) == 1 else STATUS_UNKNOWN


def supply_online(adapters, on_battery, status):
    """有變壓器項目時看它們是否接上；沒有時改看 UPower，再沒有就看電池狀態。
    電池正在充電一定有外接電源，即使變壓器項目讀不到或不認得也算接電。"""
    if adapters:
        return any(adapter.online for adapter in adapters) or status == STATUS_CHARGING
    if on_battery is not None:
        return not on_battery
    return status in PLUGGED_STATUSES


def adapter_fields(adapters):
    """接上的變壓器：協商的契約與額定瓦數；兩者都不是實際輸入，不填 adapter_w。"""
    online = [adapter for adapter in adapters if adapter.online]
    contract = next((adapter for adapter in online if adapter.negotiated_w is not None), None)
    rated = next((adapter.rated_w for adapter in online if adapter.rated_w is not None), None)
    if contract is None:
        return dict(adapter_rated_w=rated)
    return dict(adapter_negotiated_w=contract.negotiated_w, adapter_rated_w=rated,
                adapter_v=contract.voltage_v, adapter_a=contract.current_a)


def limit_fields(batteries):
    """第一顆有充電上限檔的電池；上限 100% 或 0% 視為沒有設定。"""
    battery = next((item for item in batteries if item.thresholds_supported), None)
    if battery is None:
        return dict()
    end = battery.end_threshold
    return dict(limit=end if 0 < end < Battery.PERCENT else None,
                limit_start=battery.start_threshold, limit_supported=True)


def rate_unit(batteries, energy_now, battery_w, charge_now, current_a):
    """原生是能量制就用功率推算，原生是電量制就用電流推算；混合時用換算後的能量。"""
    if all(item.energy_now_wh is not None for item in batteries) and battery_w is not None:
        return UNIT_WATTS
    if all(item.charge_now_ah is not None for item in batteries) and current_a is not None:
        return UNIT_AMPS
    if energy_now is not None and battery_w is not None:
        return UNIT_WATTS
    return UNIT_AMPS if charge_now is not None and current_a is not None else None


ENERGY_NAMES = ("energy_now_wh", "energy_full_wh", "energy_design_wh")
CHARGE_NAMES = ("charge_now_ah", "charge_full_ah", "charge_design_ah")


def summed(rows, names):
    """每一欄全部電池都有值才相加。"""
    return {name: total(row[index] for row in rows) for index, name in enumerate(names)}


def describe(first, batteries):
    temperatures = [item.temperature_c for item in batteries if item.temperature_c is not None]
    return dict(voltage_v=first.voltage_v, cycles=first.cycle_count,
                temperature_c=max(temperatures) if temperatures else None,
                capacity_level=first.capacity_level, health=first.health,
                technology=first.technology, manufacturer=first.manufacturer, model=first.model,
                behaviour=first.behaviour, count=len(batteries))


def combine(batteries, adapters=(), on_battery=None, upower_state=None):
    """合併所有系統電池與變壓器讀值；沒有系統電池時回傳 None（桌機）。

    on_battery 與 upower_state 來自 UPower（沒有時為 None），只在 sysfs 無法判斷時採用。
    """
    batteries = tuple(batteries)
    if not batteries:
        return None
    status = combined_status([battery.status for battery in batteries])
    online = supply_online(tuple(adapters), on_battery, status)
    flows = [signed_flow(battery, online, upower_state) for battery in batteries]
    battery_w, current_a = known_sum(f[0] for f in flows), known_sum(f[1] for f in flows)
    energies = [energy_values(battery) for battery in batteries]
    energy = summed(energies, ENERGY_NAMES)
    charge = summed([charge_values(battery) for battery in batteries], CHARGE_NAMES)
    unit = rate_unit(batteries, energy["energy_now_wh"], battery_w, charge["charge_now_ah"],
                     current_a)
    return BatterySnapshot(
        percent=combined_percent(batteries, energies), ac_online=online, status=status,
        charging_flag=status == STATUS_CHARGING, full_flag=status == STATUS_FULL,
        battery_w=battery_w, current_a=current_a, rate_unit=unit, **energy, **charge,
        **adapter_fields(adapters), **limit_fields(batteries), **describe(batteries[0], batteries))
