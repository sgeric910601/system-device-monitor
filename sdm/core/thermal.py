"""溫度分組與風扇（參考專案 ThermalMath、FanReading），對應 Linux 的 hwmon 與磁碟 SMART。

- 分組：CPU（k10temp 的 Tctl／Tdie／Tccd、coretemp 的 Package／Core）、顯示卡（amdgpu、
  NVIDIA 等）、儲存裝置（nvme、drivetemp、UDisks 的 SMART 溫度）、網路（網路卡的 PHY）、其他。
- 合理範圍外（0 °C 以下、125 °C 以上）的讀值視為讀取失敗；每個感測器讀不到時沿用最近一次
  有效值最多 120 秒，群組平均才不會因為某個感測器暫時讀不到而跳動。
- 同一個感測器有兩個來源（例如 NVMe 的 hwmon 與 SMART）時用相同的 key，先平均成一點。
"""
from dataclasses import dataclass
import math
from typing import NamedTuple, Optional

from sdm.constants import Thermal
from sdm.core.battery import round_half_up
from sdm.core.fmt import PLACEHOLDER, temperature

CPU, GPU, STORAGE, NETWORK, OTHER = "cpu", "gpu", "storage", "network", "other"
GROUP_ORDER = (CPU, GPU, STORAGE, NETWORK, OTHER)
GROUP_TITLES = {CPU: "CPU", GPU: "顯示卡", STORAGE: "儲存裝置", NETWORK: "網路卡", OTHER: "其他"}
CPU_CHIPS = {"k10temp": ("tctl", "tdie", "tccd"), "coretemp": ("package", "core")}
GPU_CHIPS = ("amdgpu", "radeon", "nouveau", "nvidia", "i915", "xe")
STORAGE_CHIPS = ("nvme", "drivetemp", "udisks")
PERCENT = 100


def category(chip, label, network=False):
    """hwmon 晶片名稱與標籤 → 群組；網路卡 PHY 由資料層依硬體路徑判斷後傳入 network。"""
    if network:
        return NETWORK
    chip, label = str(chip).lower(), str(label).lower()
    if chip in CPU_CHIPS:
        return CPU if label.startswith(CPU_CHIPS[chip]) else OTHER
    if chip in GPU_CHIPS:
        return GPU
    if chip in STORAGE_CHIPS:
        return STORAGE
    return OTHER


def plausible(value):
    real = isinstance(value, (int, float)) and not isinstance(value, bool)
    return real and math.isfinite(value) and Thermal.MIN_C < value < Thermal.MAX_C


class ThermalReading(NamedTuple):
    key: str                    # 感測器的唯一名稱；同一感測器的不同來源用相同 key
    group: str
    value: Optional[float]      # °C；讀不到時為 None


class Stat(NamedTuple):
    average: float
    maximum: float


class ThermalHold:
    """每個感測器最近一次有效的讀值、時間與群組；讀到無效值或這次沒出現（讀取失敗）時沿用，
    超過 120 秒沒有有效值就放棄並忘掉這個感測器（例如已拔掉的外接碟）。"""

    def __init__(self):
        self.held = {}

    def apply(self, readings, now):
        previous, held, result = self.held, {}, []
        batch = list(readings)
        seen = {reading.key for reading in batch}
        missing = [ThermalReading(key, group, None) for key, (_, _, group) in previous.items()
                   if key not in seen]
        for reading in batch + missing:
            if plausible(reading.value):
                held[reading.key] = (reading.value, now, reading.group)
                result.append(reading)
                continue
            old = previous.get(reading.key)
            if old is not None and now - old[1] <= Thermal.HOLD_SECONDS:
                held[reading.key] = old
                result.append(reading._replace(value=old[0]))
        self.held = held
        return tuple(result)


def statistic(values):
    valid = [value for value in values if plausible(value)]
    if not valid:
        return None
    return Stat(sum(valid) / len(valid), max(valid))


def summary(readings):
    """群組 → Stat（平均、最高），依顯示順序；同一 key 的讀值先平均成一點，無效值丟棄。"""
    per_key = {}
    for reading in readings:
        if plausible(reading.value):
            per_key.setdefault((reading.group, reading.key), []).append(reading.value)
    per_group = {}
    for (group, _), values in per_key.items():
        per_group.setdefault(group, []).append(sum(values) / len(values))
    return {group: statistic(per_group[group]) for group in GROUP_ORDER if group in per_group}


def stat_text(stat):
    """「43°C（最高 45°C）」；平均與最高四捨五入後相同時只顯示一個值。"""
    if stat is None:
        return PLACEHOLDER
    if round_half_up(stat.average) == round_half_up(stat.maximum):
        return temperature(stat.average)
    return f"{temperature(stat.average)}（最高 {temperature(stat.maximum)}）"


@dataclass(frozen=True)
class Fan:
    """hwmon 風扇（轉速與最高轉速）或顯示卡風扇（百分比）。"""
    name: str
    rpm: Optional[float] = None
    max_rpm: Optional[float] = None
    percent: Optional[float] = None

    @property
    def has_fraction(self):
        return self.percent is not None or bool(self.max_rpm and self.max_rpm > 0)

    @property
    def fraction(self):
        """佔最高轉速的比例（0～1）；沒有最高轉速時為 0（參考專案）。"""
        if self.percent is not None:
            return min(1.0, max(0.0, self.percent / PERCENT))
        if self.rpm is None or not self.max_rpm or self.max_rpm <= 0:
            return 0
        return min(1, max(0, self.rpm / self.max_rpm))

    def text(self):
        parts = [] if self.rpm is None else [f"{self.rpm:.0f} RPM"]
        if self.has_fraction:
            parts.append(f"{round_half_up(self.fraction * PERCENT)}%")
        return "・".join(parts) or PLACEHOLDER
