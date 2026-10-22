"""CPU 使用者／系統比例與各核心使用率（參考專案 CPUMath），以及平均負載與時脈。

/proc/stat 的欄位依序為 user nice system idle iowait irq softirq steal guest guest_nice：
- 使用者＝user＋nice（guest 已含在 user 裡，不重複計算）
- 系統＝system＋irq＋softirq＋steal
- 閒置＝idle＋iowait
任何一個累計值倒退（計數器回繞或重置）時這次不計算。
"""
from dataclasses import dataclass
import math
from typing import NamedTuple

from sdm.constants import Counter

USER, NICE, SYSTEM, IDLE, IOWAIT, IRQ, SOFTIRQ, STEAL = range(8)
MIN_FIELDS = 4              # 舊核心只有 user nice system idle
KHZ_PER_MHZ = 1000
LOAD_FIELDS = 3


@dataclass(frozen=True)
class CpuTicks:
    user: int
    system: int
    idle: int
    nice: int = 0

    def __add__(self, other):
        return CpuTicks(self.user + other.user, self.system + other.system,
                        self.idle + other.idle, self.nice + other.nice)


@dataclass(frozen=True)
class CpuUsage:
    """0～1 的比例。"""
    user: float
    system: float

    @property
    def total(self):
        return self.user + self.system


class CpuSample(NamedTuple):
    total: CpuUsage
    cores: tuple


ZERO = CpuTicks(0, 0, 0, 0)
IDLE_CORE = CpuUsage(0.0, 0.0)


def usage(old, new):
    """兩次累計刻度的差值換算成使用率；任何一項倒退或沒有經過時間時回傳 None。"""
    fields = ("user", "system", "idle", "nice")
    if any(getattr(new, name) < getattr(old, name) for name in fields):
        return None
    user = (new.user - old.user) + (new.nice - old.nice)
    system = new.system - old.system
    total = user + system + (new.idle - old.idle)
    if total <= 0:
        return None
    return CpuUsage(user / total, system / total)


def usages(old, new):
    """（總計、各核心）；核心數不同或總計無法計算時回傳 None，個別核心倒退時該核心為 0。"""
    if len(old) != len(new) or not new:
        return None
    total = usage(sum(old, ZERO), sum(new, ZERO))
    if total is None:
        return None
    cores = tuple(usage(before, after) or IDLE_CORE for before, after in zip(old, new))
    return CpuSample(total, cores)


def ticks(values):
    def field(index):
        return values[index] if index < len(values) else 0
    return CpuTicks(user=field(USER), nice=field(NICE),
                    system=field(SYSTEM) + field(IRQ) + field(SOFTIRQ) + field(STEAL),
                    idle=field(IDLE) + field(IOWAIT))


def parse_stat(text):
    """/proc/stat → （總計刻度、各核心刻度）；格式不對時回傳 None。"""
    total, cores = None, []
    for line in text.splitlines():
        name, _, rest = line.partition(" ")
        if not name.startswith("cpu"):
            continue
        try:
            values = [int(value) for value in rest.split()]
        except ValueError:
            return None
        if len(values) < MIN_FIELDS:
            return None
        if name == "cpu":
            total = ticks(values)
        else:
            cores.append(ticks(values))
    return None if total is None else (total, tuple(cores))


class CpuTracker:
    """保留上一次的刻度；第一次與間隔太久（睡眠、頁面隱藏）時只建立基準。"""

    def __init__(self):
        self.previous = None

    def sample(self, text, now):
        parsed = parse_stat(text)
        previous, self.previous = self.previous, (None if parsed is None else (parsed, now))
        if parsed is None or previous is None or now - previous[1] > Counter.RESET_SECONDS:
            return None
        (old_total, old_cores), (new_total, new_cores) = previous[0], parsed
        if new_cores:
            return usages(old_cores, new_cores)
        total = usage(old_total, new_total)
        return None if total is None else CpuSample(total, ())


def parse_loadavg(text):
    """/proc/loadavg → 1、5、15 分鐘平均負載；讀不到或數值不合理時回傳 None。"""
    parts = text.split()
    if len(parts) < LOAD_FIELDS:
        return None
    try:
        values = tuple(float(part) for part in parts[:LOAD_FIELDS])
    except ValueError:
        return None
    if not all(math.isfinite(value) and value >= 0 for value in values):
        return None
    return values


def average_mhz(texts):
    """各核心 scaling_cur_freq（kHz）的平均，換算成 MHz；讀不到時回傳 None。"""
    values = []
    for text in texts:
        try:
            value = int(str(text).strip())
        except ValueError:
            continue
        if value > 0:
            values.append(value)
    return sum(values) / len(values) / KHZ_PER_MHZ if values else None

