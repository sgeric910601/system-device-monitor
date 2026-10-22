"""記憶體組成、Swap／zswap 與壓力分級（/proc/meminfo 與 /proc/pressure/memory）。

組成（互不重疊，合計等於總量）：
- App＝AnonPages；共享＝Shmem（tmpfs、共用記憶體）
- 核心＝SUnreclaim＋KernelStack＋PageTables
- 快取＝Cached＋Buffers＋SReclaimable−Shmem（Cached 已含 Shmem，扣掉才不重複，最少 0）
- 空閒＝MemFree；其他＝總量減去以上各項（核心的其他用途，最少 0）
「可用」＝MemAvailable（包含可釋放的快取），與組成重疊，另外顯示。
已使用＝總量−可用，與總覽頁及頂端列相同。
"""
from dataclasses import dataclass
from typing import NamedTuple, Optional

from sdm.constants import Memory

NORMAL, WARNING, CRITICAL = "normal", "warning", "critical"
KILOBYTE_UNIT = "kB"
SEGMENT_KEYS = ("app", "shared", "kernel", "cache", "other", "free")


class Pressure(NamedTuple):
    some_avg10: Optional[float]
    full_avg10: Optional[float]


def parse_meminfo(text):
    """「鍵: 值 kB」→ 位元組；沒有單位的欄位保留原值，格式不對的列略過。"""
    values = {}
    for line in text.splitlines():
        key, separator, rest = line.partition(":")
        parts = rest.split()
        if not separator or not parts:
            continue
        try:
            number = int(parts[0])
        except ValueError:
            continue
        unit = parts[1] if len(parts) > 1 else ""
        values[key.strip()] = number * Memory.KILOBYTE if unit == KILOBYTE_UNIT else number
    return values


@dataclass(frozen=True)
class MemoryComposition:
    total: int
    app: int
    shared: int
    kernel: int
    cache: int
    free: int
    available: int
    swap_used: int
    swap_total: int
    zswap: Optional[int] = None         # 壓縮後佔用的記憶體
    zswapped: Optional[int] = None      # 壓縮前的大小

    @property
    def used(self):
        return max(0, self.total - self.available)

    @property
    def used_fraction(self):
        return self.used / self.total if self.total else 0.0

    @property
    def available_fraction(self):
        return self.available / self.total if self.total else 0.0

    def segments(self):
        """組成長條的（代號、位元組），依顯示順序；「其他」補足到總量。"""
        known = self.app + self.shared + self.kernel + self.cache + self.free
        other = max(0, self.total - known)
        values = dict(app=self.app, shared=self.shared, kernel=self.kernel, cache=self.cache,
                      other=other, free=self.free)
        return tuple((key, values[key]) for key in SEGMENT_KEYS)


def composition(values):
    """由 parse_meminfo() 的結果計算；沒有 MemTotal 時回傳 None。"""
    total = values.get("MemTotal", 0)
    if total <= 0:
        return None
    def get(key):
        return max(0, values.get(key, 0))
    shared = get("Shmem")
    swap_total = get("SwapTotal")
    return MemoryComposition(
        total=total, app=get("AnonPages"), shared=shared,
        kernel=get("SUnreclaim") + get("KernelStack") + get("PageTables"),
        cache=max(0, get("Cached") + get("Buffers") + get("SReclaimable") - shared),
        free=get("MemFree"), available=values.get("MemAvailable", get("MemFree")),
        swap_used=max(0, swap_total - get("SwapFree")), swap_total=swap_total,
        zswap=values.get("Zswap"), zswapped=values.get("Zswapped"))


def average10(line):
    for field in line.split()[1:]:
        key, _, value = field.partition("=")
        if key == "avg10":
            return float(value)
    raise ValueError("avg10 missing")


def parse_pressure(text):
    """/proc/pressure/memory → some 與 full 的 10 秒平均；沒有 some 列或格式不對時回傳 None。"""
    found = {}
    for line in text.splitlines():
        kind = line.split(" ", 1)[0]
        if kind in ("some", "full"):
            try:
                found[kind] = average10(line)
            except ValueError:
                return None
    if "some" not in found:
        return None
    return Pressure(found["some"], found.get("full"))


def pressure_level(memory, pressure):
    """嚴重：full ≥ 5 或可用 < 5%；警告：some ≥ 10 或可用 < 10%；否則正常。"""
    available = memory.available_fraction if memory is not None else None
    some = pressure.some_avg10 if pressure is not None else None
    full = pressure.full_avg10 if pressure is not None else None
    if (full is not None and full >= Memory.CRITICAL_FULL_AVG10) or \
            (available is not None and available < Memory.CRITICAL_AVAILABLE_FRACTION):
        return CRITICAL
    if (some is not None and some >= Memory.WARN_SOME_AVG10) or \
            (available is not None and available < Memory.WARN_AVAILABLE_FRACTION):
        return WARNING
    return NORMAL
