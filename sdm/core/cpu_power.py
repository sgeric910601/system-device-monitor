"""CPU 功耗（不載入 GTK）：取樣服務的讀值、新鮮度，以及耗電拆解中的 CPU 項目。

- RAPL（CPU 功耗取樣服務）：package 瓦數在 max_age 秒內才採用（耗電服務用 3 × 慢速間隔）；
  有 core 讀值時，CPU 底下再分「核心」與「插槽其餘」兩個下層項目，只在拆解清單顯示，
  不重複計入合計與「其他」。
- AMD APU 的 PPT（顯示卡驅動 power1，標籤 PPT）：只回報原始值與時間，
  要不要當作 CPU 由 sdm.core.ppt_calibration 的校正決定。
"""
from dataclasses import dataclass
import math
from typing import Optional

from sdm.constants import CpuPower, Power
from sdm.core.power_breakdown import Component

CPU_ID, CPU_LABEL = "cpu", "CPU"
PPT_LABEL = "CPU（插槽功耗估算）"
CORE_ID, CORE_LABEL = "cpu:core", "核心"
REST_ID, REST_LABEL = "cpu:rest", "插槽其餘"
FRESH_SECONDS = 3.0                 # 參考專案 freshWatts 的預設最長時間


@dataclass(frozen=True)
class CpuPowerReading:
    package_w: float                # 處理器插槽（package）功耗
    core_w: Optional[float]         # 其中核心（core）的部分；讀不到時為 None
    stamp: float                    # 收到回應時的單調時鐘秒數


def valid_watts(value):
    """有限、0 以上且小於單一元件上限的數字（bool 不算）。"""
    if type(value) not in (int, float) or not math.isfinite(value):
        return False
    return 0 <= value < Power.COMPONENT_MAX_W


def fresh_watts(reading, now, max_age=FRESH_SECONDS):
    """reading 在 max_age 秒內（不接受未來的時間）而且數值合理時回傳 package 瓦數。"""
    if reading is None or not valid_watts(reading.package_w):
        return None
    return reading.package_w if 0 <= now - reading.stamp <= max_age else None


def core_split(package, core):
    """（核心、插槽其餘）兩個下層項目；沒有核心讀值或核心大於整個插槽時不分。"""
    if core is None or not valid_watts(core) or core > package:
        return ()
    rest = round(package - core, CpuPower.WATTS_DIGITS)
    return ((CORE_ID, CORE_LABEL, core), (REST_ID, REST_LABEL, rest))


def rapl_component(reading, now, max_age):
    package = fresh_watts(reading, now, max_age)
    if package is None:
        return None
    return Component(CPU_ID, CPU_LABEL, package, reading.stamp,
                     core_split(package, reading.core_w))


def apu_ppt(gpus, now, max_age):
    """編號最小、讀值仍新鮮的 AMD 內建顯示 PPT：（原始值、時間）；沒有時為 None。"""
    for gpu in sorted(gpus, key=lambda reading: reading.index):
        raw = gpu.ppt_raw
        if gpu.vendor != "amd" or not gpu.integrated or raw is None:
            continue
        if isinstance(raw, (int, float)) and math.isfinite(raw) and raw >= 0 \
                and 0 <= now - gpu.stamp <= max_age:
            return float(raw), gpu.stamp
    return None


def ppt_component(watts, stamp):
    return Component(CPU_ID, PPT_LABEL, watts, stamp)
