"""sysfs power_supply 唯讀讀取：筆電的系統電池與變壓器。

- 系統電池＝type 為 Battery、scope 不是 Device（滑鼠等周邊）、present 不是 0；名稱不固定
  （BAT0、BAT1、CMB0、BATT、macsmc-battery…）。
- 只讀取明確列出的屬性；絕不讀 serial_number，也不讀含序號的 uevent。
- 驅動對電流與功率的正負號定義不一致，這裡只保留大小，方向交給核心依狀態判斷。
- 讀不到、不是數字或超出合理範圍的值一律為 None，不會拋出例外。
所有函式都接受 root，測試可以指向假的 /sys 目錄。
"""
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Optional

from sdm.constants import Battery
from sdm.core.fmt import number
from sdm.data.sources import clean_text, read

MICRO = 1_000_000           # µWh、µAh、µV、µA、µW → Wh、Ah、V、A、W
TENTHS = 10                 # temp 以 0.1 °C 為單位
PERCENT_MAX = 100
TYPE_BATTERY = "Battery"
SCOPE_DEVICE = "Device"
ADAPTER_TYPES = ("Mains", "USB")
USB_TYPE = "USB"
PD_MARKER = "[PD"           # usb_type 中以方括號標示目前使用的類型，例如「C [PD] PD_PPS」
UCSI_PREFIX = "ucsi-source-psy-"
ACTIVE_CHOICE = re.compile(r"\[([^\]]+)\]")


@dataclass(frozen=True)
class BatteryReading:
    """一顆系統電池的讀值：容量 Wh／Ah、電壓 V、電流 A、功率 W、溫度 °C。"""
    name: str
    status: str = ""
    capacity: Optional[int] = None
    capacity_level: str = ""
    health: str = ""
    energy_now_wh: Optional[float] = None
    energy_full_wh: Optional[float] = None
    energy_design_wh: Optional[float] = None
    charge_now_ah: Optional[float] = None
    charge_full_ah: Optional[float] = None
    charge_design_ah: Optional[float] = None
    voltage_v: Optional[float] = None
    voltage_min_design_v: Optional[float] = None
    power_w: Optional[float] = None       # 只有大小，方向由狀態決定
    current_a: Optional[float] = None     # 只有大小，方向由狀態決定
    cycle_count: Optional[int] = None
    temperature_c: Optional[float] = None
    technology: str = ""
    manufacturer: str = ""
    model: str = ""
    start_threshold: Optional[int] = None
    end_threshold: Optional[int] = None
    behaviour: str = ""                   # charge_behaviour 目前選用的值（只讀）

    @property
    def thresholds_supported(self):
        """有可讀的充電上限檔（charge_control_end_threshold）。"""
        return self.end_threshold is not None


@dataclass(frozen=True)
class AdapterReading:
    """一個變壓器（Mains）或 USB-C PD 電源；瓦數讀不到時為 None。"""
    name: str
    kind: str
    online: bool
    voltage_v: Optional[float] = None
    current_a: Optional[float] = None
    rated_w: Optional[float] = None

    @property
    def measured_w(self):
        """即時輸入功率＝電壓 × 電流；任一讀不到或為 0 時回傳 None。"""
        if self.voltage_v is None or self.current_a is None:
            return None
        watts = self.voltage_v * self.current_a
        return watts if watts > 0 else None


def scaled(directory, name, scale, low, high, absolute=False):
    """讀數值屬性並除以 scale；不是有限數字或不在 low–high（含）之間時回傳 None。"""
    raw = number(read(directory/name))
    if raw is None:
        return None
    value = (abs(raw) if absolute else raw) / scale
    return value if low <= value <= high else None


def positive(directory, name, scale, high):
    """必須大於 0 的量（滿充、設計容量、電壓）。"""
    value = scaled(directory, name, scale, 0, high)
    return value if value is not None and value > 0 else None


def integer(directory, name, low, high):
    value = scaled(directory, name, 1, low, high)
    return None if value is None else int(round(value))


def text(directory, name, limit=Battery.TEXT_MAX_CHARS):
    return clean_text(read(directory/name), limit)


def active_choice(value):
    """「auto [inhibit-charge] force-discharge」取方括號內的值；只有一個值時就是它。"""
    found = ACTIVE_CHOICE.search(value)
    if found:
        return found.group(1)
    parts = value.split()
    return parts[0] if len(parts) == 1 else ""


def energy_fields(directory):
    return dict(
        energy_now_wh=scaled(directory, "energy_now", MICRO, 0, Battery.MAX_WH),
        energy_full_wh=positive(directory, "energy_full", MICRO, Battery.MAX_WH),
        energy_design_wh=positive(directory, "energy_full_design", MICRO, Battery.MAX_WH),
        charge_now_ah=scaled(directory, "charge_now", MICRO, 0, Battery.MAX_AH),
        charge_full_ah=positive(directory, "charge_full", MICRO, Battery.MAX_AH),
        charge_design_ah=positive(directory, "charge_full_design", MICRO, Battery.MAX_AH))


def electrical_fields(directory):
    temperature = scaled(directory, "temp", TENTHS, Battery.TEMP_MIN_C, Battery.TEMP_MAX_C)
    return dict(
        voltage_v=positive(directory, "voltage_now", MICRO, Battery.MAX_V),
        voltage_min_design_v=positive(directory, "voltage_min_design", MICRO, Battery.MAX_V),
        power_w=scaled(directory, "power_now", MICRO, 0, Battery.MAX_W, absolute=True),
        current_a=scaled(directory, "current_now", MICRO, 0, Battery.MAX_A, absolute=True),
        cycle_count=integer(directory, "cycle_count", 1, Battery.CYCLES_MAX),
        temperature_c=temperature)


def descriptive_fields(directory):
    return dict(
        status=text(directory, "status", Battery.STATUS_MAX_CHARS),
        capacity=integer(directory, "capacity", 0, PERCENT_MAX),
        capacity_level=text(directory, "capacity_level", Battery.STATUS_MAX_CHARS),
        health=text(directory, "health", Battery.STATUS_MAX_CHARS),
        technology=text(directory, "technology"),
        manufacturer=text(directory, "manufacturer"),
        model=text(directory, "model_name"),
        start_threshold=integer(directory, "charge_control_start_threshold", 0, PERCENT_MAX),
        end_threshold=integer(directory, "charge_control_end_threshold", 0, PERCENT_MAX),
        behaviour=active_choice(text(directory, "charge_behaviour")))


def battery_reading(directory):
    return BatteryReading(name=directory.name, **descriptive_fields(directory),
                          **energy_fields(directory), **electrical_fields(directory))


def supply_directories(root=Path("/")):
    """依名稱排序的 power_supply 項目；目錄不存在時回傳空 tuple。"""
    try:
        return tuple(sorted((Path(root)/Battery.SUPPLY_DIR).iterdir(),
                            key=lambda entry: entry.name))
    except OSError:
        return ()


def is_system_battery(directory):
    return (read(directory/"type") == TYPE_BATTERY and read(directory/"scope") != SCOPE_DEVICE
            and read(directory/"present") != "0")


def is_adapter(directory):
    """Mains，或正在以 USB-C PD 供電的 USB 項目（ucsi 來源）；周邊的電源不算。"""
    kind = read(directory/"type")
    if kind not in ADAPTER_TYPES or read(directory/"scope") == SCOPE_DEVICE:
        return False
    if kind != USB_TYPE:
        return True
    return PD_MARKER in read(directory/"usb_type") or directory.name.startswith(UCSI_PREFIX)


def rated_watts(directory):
    """額定瓦數＝最大電壓 × 最大電流；沒有時改用 input_power_limit。"""
    volts = positive(directory, "voltage_max", MICRO, Battery.MAX_V)
    amps = positive(directory, "current_max", MICRO, Battery.MAX_A)
    if volts is not None and amps is not None:
        return volts * amps
    return positive(directory, "input_power_limit", MICRO, Battery.MAX_W)


def adapter_reading(directory):
    return AdapterReading(
        name=directory.name, kind=read(directory/"type"), online=read(directory/"online") == "1",
        voltage_v=positive(directory, "voltage_now", MICRO, Battery.MAX_V),
        current_a=scaled(directory, "current_now", MICRO, 0, Battery.MAX_A, absolute=True),
        rated_w=rated_watts(directory))


def read_batteries(root=Path("/")):
    """所有系統電池（依名稱排序）；沒有時回傳空 tuple。"""
    return tuple(battery_reading(directory) for directory in supply_directories(root)
                 if is_system_battery(directory))


def read_adapters(root=Path("/")):
    return tuple(adapter_reading(directory) for directory in supply_directories(root)
                 if is_adapter(directory))


def has_system_battery(root=Path("/")):
    return any(is_system_battery(directory) for directory in supply_directories(root))
