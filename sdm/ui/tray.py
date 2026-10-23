"""頂端列指示器：分開顯示（耗電＋舊有五項，各一個）或合併成一個（沿用「耗電」那一個）。

- 指示器在第一次需要時才建立，之後切換模式只改成 PASSIVE（隱藏）並重複使用：
  AppIndicator 物件無法真正移除，所以切換多少次都不會多出新的指示器。
- 沒有任何項目要顯示時，改顯示程式圖示（換色後為選擇的顏色），讓選單仍然打得開。
- 文字、圖示與狀態只在改變時才送出，避免頂端列一直重畫。
- 耗電圖示是 tray_icons 畫的 SVG；寫不出檔案時改用 power_tray 的主題圖示。
所有指示器共用同一個選單（tray_menu）。內容由 tray_text.tray_view() 算好。
"""
import gi
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3

from sdm import paths
from sdm.core.settings import LEGACY_KEYS
from sdm.ui.tray_icons import render
from sdm.ui.tray_text import MERGED, POWER

ACTIVE = AyatanaAppIndicator3.IndicatorStatus.ACTIVE
PASSIVE = AyatanaAppIndicator3.IndicatorStatus.PASSIVE
CATEGORY = AyatanaAppIndicator3.IndicatorCategory.SYSTEM_SERVICES
BASE_ID = "system-device-monitor"
APP_ICON = "utilities-system-monitor-symbolic"
ITEM_ICONS = {key: str(paths.ROOT/"icons"/(key + ".svg")) for key in LEGACY_KEYS}
ORDER_BASE = 100                 # 數字越大越靠左：耗電最左，其後依舊版順序
TITLES = {POWER: "耗電", "cpu": "CPU", "temperature": "CPU 溫度", "ram": "記憶體",
          "gpu": "GPU", "network": "網速", None: "系統與裝置監看"}


def ordering(key):
    if key == POWER:
        return ORDER_BASE + len(LEGACY_KEYS)
    return ORDER_BASE + tuple(reversed(LEGACY_KEYS)).index(key)


class IndicatorFactory:
    """建立真正的 AppIndicator；每個都掛上同一個選單。"""

    def __init__(self, menu):
        self.menu = menu

    def create(self, key, icon):
        indicator = AyatanaAppIndicator3.Indicator.new(
            BASE_ID if key is None else f"{BASE_ID}-{key}", icon, CATEGORY)
        if key is not None:
            indicator.set_ordering_index(ordering(key))
        indicator.set_menu(self.menu)
        return indicator


class Tray:
    def __init__(self, factory, writer):
        self.factory, self.writer = factory, writer
        self.indicators, self.sent = {}, {}
        self.app_icon = APP_ICON

    def set_app_icon(self, icon):
        """程式圖示（主題名稱或檔案路徑）；下次更新頂端列時送出。"""
        self.app_icon = icon

    def apply(self, view):
        targets = self.targets(view)
        for key, (label, icon) in targets.items():
            self.show(key, label, icon)
        for key in self.indicators:
            if key not in targets:
                self.set_status(key, PASSIVE)

    def targets(self, view):
        """要顯示的指示器 →（文字、圖示）。"""
        if view.mode == MERGED:
            icon = self.power_icon(view) if POWER in view.visible else self.app_icon
            return {POWER: (view.merged_label, icon)}
        if not view.visible:
            return {None: ("", self.app_icon)}
        return {key: (view.labels[key], self.power_icon(view) if key == POWER
                      else ITEM_ICONS[key]) for key in view.visible}

    def power_icon(self, view):
        path = self.writer.publish(render(view.glyph))
        return str(path) if path is not None else view.fallback_icon

    def show(self, key, label, icon):
        indicator = self.indicators.get(key)
        if indicator is None:
            indicator = self.indicators[key] = self.factory.create(key, icon)
            self.sent[key] = (None, icon, None)
        sent_label, sent_icon, _ = self.sent[key]
        if label != sent_label:
            indicator.set_label(label, "")
        if icon != sent_icon:
            indicator.set_icon_full(icon, TITLES[key])
        self.sent[key] = (label, icon, self.sent[key][2])
        self.set_status(key, ACTIVE)

    def set_status(self, key, status):
        label, icon, sent = self.sent[key]
        if sent != status:
            self.indicators[key].set_status(status)
            self.sent[key] = (label, icon, status)

    def hide_all(self):
        for key in self.indicators:
            self.set_status(key, PASSIVE)

    def count(self):
        return len(self.indicators)
