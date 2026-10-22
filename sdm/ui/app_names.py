"""App 代號 → 顯示名稱與圖示：在主執行緒查桌面檔（Gio.DesktopAppInfo），結果快取。

資料層只回傳代號；GNOME 桌面與系統服務是固定項目；示範模式的名稱隨資料提供，
不查這台電腦安裝了哪些 App。名稱去掉控制字元，最多 64 字。
"""
import re

from gi.repository import Gio

from sdm.core.apps import SESSION_ID, SYSTEM_ID, rank
from sdm.data.sources import clean_text
from sdm.ui.monitor_text import CALCULATING, cpu_app_rows, memory_app_rows

DEFAULT_ICON = "application-x-executable"
SPECIAL = {SESSION_ID: ("GNOME 桌面", "user-desktop"),
           SYSTEM_ID: ("系統服務", "preferences-system")}
NAME_MAX_CHARS = 64
CPU = "cpu"
DESKTOP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")


def desktop_lookup(app_id):
    """（名稱、Gio.Icon）；找不到桌面檔時回傳 None。也試一次全小寫的代號。"""
    if not DESKTOP_ID.fullmatch(app_id or "") or ".." in app_id:
        return None
    for candidate in dict.fromkeys((app_id, app_id.lower())):
        try:
            info = Gio.DesktopAppInfo.new(candidate + ".desktop")
        except TypeError:
            info = None
        if info is not None:
            return info.get_name(), info.get_icon()
    return None


class AppNames:
    def __init__(self, lookup=desktop_lookup):
        self.lookup, self.cache = lookup, {}

    def resolve(self, app_id, labels=()):
        """（名稱、圖示）：圖示是 Gio.Icon 或圖示名稱。"""
        for label_id, name, icon in labels:
            if label_id == app_id:
                return name, icon
        if app_id in SPECIAL:
            return SPECIAL[app_id]
        if app_id not in self.cache:
            found = self.lookup(app_id)
            name = clean_text(found[0], NAME_MAX_CHARS) if found and found[0] else ""
            icon = found[1] if found and found[1] else DEFAULT_ICON
            self.cache[app_id] = (name or app_id, icon)
        return self.cache[app_id]

    def name_for(self, labels):
        return lambda app_id: self.resolve(app_id, labels)[0]


def top_rows(apps, names, metric, cores):
    """前 5 名的（名稱、圖示、數值、次要文字）；metric 為 "cpu" 或 "memory"。"""
    if metric == CPU:
        items = ((usage.app_id, usage.core_percent) for usage in apps.sample.usages)
    else:
        items = ((usage.app_id, usage.memory) for usage in apps.sample.usages)
    ranked = rank(items, names.name_for(apps.labels))
    texts = cpu_app_rows(ranked, cores) if metric == CPU else memory_app_rows(ranked)
    return tuple((name, names.resolve(row.app_ids[0], apps.labels)[1], value, secondary)
                 for row, (name, value, secondary) in zip(ranked, texts))


def placeholder(apps, metric):
    """排行是空的時候顯示的文字：第一次取樣還在建立基準時為「計算中…」。"""
    if apps is None or (metric == CPU and not apps.sample.ready):
        return CALCULATING
    return "目前沒有明顯的 CPU 使用量" if metric == CPU else "目前沒有可顯示的 App"
