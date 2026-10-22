"""App 代號 → 顯示名稱與圖示：在主執行緒查桌面檔（Gio.DesktopAppInfo），結果快取。

資料層只回傳代號；GNOME 桌面與系統服務是固定項目；示範模式的名稱隨資料提供，
不查這台電腦安裝了哪些 App。名稱去掉控制字元，最多 64 字。

排行刻意依「桌面檔」合併：同一個 App 用不同方式啟動（例如 org.example.Editor 與
org.example.editor 指到同一個桌面檔）合成一列。不同桌面檔只是名稱相同時不合併，
改在名稱後加上代號的最後一段區分，例如「Google Chrome（Chrome）」。
找不到桌面檔的結果只快取 10 分鐘（剛安裝的 App 桌面檔可能稍後才出現）。
"""
from collections import Counter
import re
import time
from typing import NamedTuple, Optional

from gi.repository import Gio

from sdm.core.apps import SESSION_ID, SYSTEM_ID, rank
from sdm.data.sources import clean_text
from sdm.ui.monitor_text import CALCULATING, cpu_app_rows, memory_app_rows

DEFAULT_ICON = "application-x-executable"
SPECIAL = {SESSION_ID: ("GNOME 桌面", "user-desktop"),
           SYSTEM_ID: ("系統服務", "preferences-system")}
NAME_MAX_CHARS = 64
NEGATIVE_TTL_SECONDS = 600
CPU = "cpu"
DESKTOP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")


class AppLabel(NamedTuple):
    name: str
    icon: object                # Gio.Icon 或圖示名稱
    key: str                    # 合併用：桌面檔代號，找不到時為 App 代號
    expires: Optional[float] = None     # 找不到桌面檔時的快取期限（單調時鐘）；None 代表一直有效


def desktop_lookup(app_id):
    """（名稱、Gio.Icon、桌面檔代號）；找不到桌面檔時回傳 None。也試一次全小寫的代號。"""
    if not DESKTOP_ID.fullmatch(app_id or "") or ".." in app_id:
        return None
    for candidate in dict.fromkeys((app_id, app_id.lower())):
        try:
            info = Gio.DesktopAppInfo.new(candidate + ".desktop")
        except TypeError:
            info = None
        if info is not None:
            return info.get_name(), info.get_icon(), info.get_id() or candidate + ".desktop"
    return None


def last_part(app_id):
    """「com.example.Browser」→「Browser」，用來區分名稱相同的不同 App。"""
    return app_id.rsplit(".", 1)[-1]


class AppNames:
    def __init__(self, lookup=desktop_lookup, clock=time.monotonic):
        self.lookup, self.clock, self.cache = lookup, clock, {}

    def label(self, app_id, labels=()):
        for label_id, name, icon in labels:
            if label_id == app_id:
                return AppLabel(name, icon, app_id)
        if app_id in SPECIAL:
            return AppLabel(*SPECIAL[app_id], app_id)
        cached = self.cache.get(app_id)
        if cached is None or (cached.expires is not None and self.clock() >= cached.expires):
            cached = self.cache[app_id] = self.fetch(app_id)
        return cached

    def fetch(self, app_id):
        found = self.lookup(app_id)
        if not found:
            return AppLabel(app_id, DEFAULT_ICON, app_id, self.clock() + NEGATIVE_TTL_SECONDS)
        name = clean_text(found[0], NAME_MAX_CHARS) if found[0] else ""
        return AppLabel(name or app_id, found[1] or DEFAULT_ICON, found[2] or app_id)

    def resolve(self, app_id, labels=()):
        """（名稱、圖示）：圖示是 Gio.Icon 或圖示名稱。"""
        label = self.label(app_id, labels)
        return label.name, label.icon

    def name_for(self, labels):
        return lambda app_id: self.label(app_id, labels).name

    def key_for(self, labels):
        return lambda app_id: self.label(app_id, labels).key


def display_names(ranked, names, labels):
    """每列的顯示名稱；不同桌面檔名稱相同時加上代號的最後一段。"""
    plain = [names.label(row.app_ids[0], labels).name for row in ranked]
    repeated = {name for name, count in Counter(plain).items() if count > 1}
    return [f"{name}（{last_part(row.app_ids[0])}）" if name in repeated else name
            for name, row in zip(plain, ranked)]


def top_rows(apps, names, metric, cores):
    """前 5 名的（名稱、圖示、數值、次要文字）；metric 為 "cpu" 或 "memory"。"""
    if metric == CPU:
        items = ((usage.app_id, usage.core_percent) for usage in apps.sample.usages)
    else:
        items = ((usage.app_id, usage.memory) for usage in apps.sample.usages)
    ranked = rank(items, names.key_for(apps.labels))
    shown = tuple(row._replace(name=name)
                  for row, name in zip(ranked, display_names(ranked, names, apps.labels)))
    texts = cpu_app_rows(shown, cores) if metric == CPU else memory_app_rows(shown)
    return tuple((name, names.resolve(row.app_ids[0], apps.labels)[1], value, secondary)
                 for row, (name, value, secondary) in zip(shown, texts))


def placeholder(apps, metric):
    """排行是空的時候顯示的文字：第一次取樣還在建立基準時為「計算中…」。"""
    if apps is None or (metric == CPU and not apps.sample.ready):
        return CALCULATING
    return "目前沒有明顯的 CPU 使用量" if metric == CPU else "目前沒有可顯示的 App"
