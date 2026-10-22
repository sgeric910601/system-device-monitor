"""各 App 的 CPU 與記憶體：systemd 單元名稱 → App 代號、累計 CPU 時間差值、排行。

GNOME 啟動的每個 App 都有自己的程序群組（cgroup），單元名稱依 systemd 的慣例：
app[-<啟動器>]-<App 代號>-<隨機>.scope、app[-<啟動器>]-<App 代號>[@<隨機>].service，
以及 app-<App 代號>.slice（例如終端機底下的分頁）。代號裡的「-」寫成 \\x2d。
資料層只回傳 App 代號；名稱與圖示由介面在主執行緒查詢桌面檔並快取。
"""
from dataclasses import dataclass
import re
from typing import NamedTuple, Optional

from sdm.constants import Counter, Monitor

SESSION_ID = "@session"         # GNOME 桌面：session.slice 與登入工作階段（含 Xorg）
SYSTEM_ID = "@system"           # 系統服務：system.slice
SPECIAL_IDS = (SESSION_ID, SYSTEM_ID)
APP_PREFIX = "app-"
UNIT_MAX_CHARS = 255
ID_MAX_CHARS = 128
VALID_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
ESCAPE = re.compile(r"\\x([0-9a-fA-F]{2})")
USEC_PER_SECOND = 1_000_000
PERCENT = 100


def unescape(text):
    """systemd 的 \\xNN 跳脫還原成字元；格式不完整的保留原樣。"""
    return ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), text)


def valid_id(app_id):
    if not app_id or len(app_id) > ID_MAX_CHARS or ".." in app_id:
        return None
    return app_id if VALID_ID.fullmatch(app_id) else None


def app_part(parts, suffix):
    """app- 之後以「-」分開的部分 → 未還原跳脫的 App 代號。"""
    if suffix == "scope":
        if len(parts) < 2:
            return None
        return parts[0] if len(parts) == 2 else "-".join(parts[1:-1])
    if suffix == "service":
        parts = "-".join(parts).partition("@")[0].split("-")
        return parts[0] if len(parts) == 1 else "-".join(parts[1:])
    return "-".join(parts)


def parse_unit(name):
    """程序群組目錄名稱 → App 代號；不認得或可能是路徑穿越的名稱回傳 None。"""
    if not name or len(name) > UNIT_MAX_CHARS or "/" in name or name.startswith("."):
        return None
    stem, _, suffix = name.rpartition(".")
    if suffix not in ("scope", "service", "slice"):
        return None
    if stem.startswith(APP_PREFIX):
        raw = app_part(stem[len(APP_PREFIX):].split("-"), suffix)
    elif suffix == "service":
        raw = stem.partition("@")[0]
    else:
        return None
    return None if raw is None else valid_id(unescape(raw))


class CgroupReading(NamedTuple):
    key: str                        # 程序群組目錄（同一個 App 可能有好幾個）
    app_id: str
    usage_usec: Optional[int]       # cpu.stat 的累計 CPU 時間（微秒）
    memory: Optional[int]           # memory.stat 的 anon（位元組）


@dataclass(frozen=True)
class AppUsage:
    app_id: str
    core_percent: Optional[float]       # 單一核心百分比（100 代表用滿一個核心）
    machine_percent: Optional[float]    # 全機百分比
    memory: Optional[int]


@dataclass(frozen=True)
class AppsSample:
    usages: tuple
    ready: bool                         # False：第一次取樣，只建立了 CPU 基準


class RankedApp(NamedTuple):
    name: str
    value: float
    app_ids: tuple


def merge_memory(previous, value):
    if value is None:
        return previous
    return value if previous is None else previous + value


class AppTracker:
    """保留每個程序群組上一次的累計 CPU 時間；計數倒退的單元重新建立基準。"""

    def __init__(self):
        self.previous, self.stamp = {}, None

    def sample(self, readings, now, cores):
        """ready 只在至少一個程序群組算得出差值時為 True：上一次只讀了記憶體（沒有 CPU 時間）
        或間隔太久時，這次只是 CPU 基準。完全沒有可讀的 CPU 時間時不必等基準。"""
        elapsed = None if self.stamp is None else now - self.stamp
        in_window = elapsed is not None and 0 < elapsed <= Counter.RESET_SECONDS
        deltas, memory, order = {}, {}, []
        for reading in readings:
            if reading.app_id not in memory:
                order.append(reading.app_id)
                memory[reading.app_id] = None
            memory[reading.app_id] = merge_memory(memory[reading.app_id], reading.memory)
            old = self.previous.get(reading.key)
            if in_window and old is not None and reading.usage_usec is not None \
                    and reading.usage_usec >= old:
                deltas[reading.app_id] = deltas.get(reading.app_id, 0) + reading.usage_usec - old
        self.previous = {reading.key: reading.usage_usec for reading in readings
                         if reading.usage_usec is not None}
        self.stamp = now
        measurable = any(reading.usage_usec is not None for reading in readings)
        ready = bool(deltas) or (in_window and not measurable)
        return AppsSample(tuple(self.usage(app_id, deltas.get(app_id), memory[app_id],
                                           elapsed, cores) for app_id in order), ready)

    @staticmethod
    def usage(app_id, delta, memory, elapsed, cores):
        if delta is None:
            return AppUsage(app_id, None, None, memory)
        core = delta / (elapsed * USEC_PER_SECOND) * PERCENT
        return AppUsage(app_id, core, core / max(1, cores), memory)


def rank(items, name_for, limit=Monitor.TOP_APPS):
    """（App 代號、數值）→ 依顯示名稱合併、由大到小的前幾名；沒有數值或為 0 的不列入。"""
    totals, members = {}, {}
    for app_id, value in items:
        if value is None or value <= 0:
            continue
        name = name_for(app_id)
        totals[name] = totals.get(name, 0) + value
        members[name] = members.get(name, ()) + (app_id,)
    ordered = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    return tuple(RankedApp(name, value, members[name]) for name, value in ordered[:limit])
