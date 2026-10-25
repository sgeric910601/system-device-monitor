"""設定視窗：左側分類（一般、頂端列、監控模組、顯示項目、通知、周邊設備、夥伴、桌面寵物、外觀、
管理員功能、更新、關於），右側該分類的卡片。

視窗寬度小於 640 px 時分類側欄收起，改用標題列的選單按鈕（與主視窗相同）。
所有控制項都經由 SettingsStore 寫入並即時套用；關閉視窗只是隱藏，下次開啟維持原本的分類。
看板在不斷電系統出現或消失、設備組成改變與通知服務狀態改變時通知視窗更新；
每次開啟時重新確認一次通知服務。
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.constants import Ui
from sdm.services.settings_store import STATUS
from sdm.ui.main_window import install_css
from sdm.ui.navigation import NavigationShell
from sdm.ui.settings import (about, admin, display, general, monitors, notifications,
                             peripherals, topbar)
from sdm.ui.settings import appearance, companion, pet
from sdm.ui.settings import update
from sdm.ui.settings.controls import Controls, wrapped
from sdm.ui.widgets import box

SECTIONS = (("general", "一般", general), ("tray", "頂端列", topbar),
            ("monitors", "監控模組", monitors), ("display", "顯示項目", display),
            ("notifications", "通知", notifications), ("peripherals", "周邊設備", peripherals),
            ("companion", "夥伴", companion), ("pet", "桌面寵物", pet),
            ("appearance", "外觀", appearance),
            ("admin", "管理員功能", admin), ("update", "更新", update), ("about", "關於", about))


def nothing():
    return None


@dataclass(frozen=True)
class SettingsContext:
    laptop: bool                        # 有系統電池：用「電源」「電池」的說法與筆電的詳細欄位
    root: Path                          # 程式所在位置（找說明文件）
    version: str
    external_power: Callable[[], bool]  # 目前是否接著外部電源（省電說明）
    ups_present: Callable[[], bool] = bool          # 目前有不斷電系統（顯示不斷電系統提醒）
    peripherals: Callable[[], tuple] = tuple        # 回報電量的周邊（PeripheralReading）
    peripheral_settings: object = None              # PeripheralSettings（周邊門檻的寫入點）
    notice: Callable[[], str] = str                 # 通知服務的狀態說明；沒有問題時為空字串
    send_test: Callable[[], Optional[bool]] = nothing
    probe: Callable[[], None] = nothing             # 重新確認通知服務（非同步）
    notifications_enabled: bool = False             # 模擬測試與示範模式為 False
    admin: object = None                # 管理員元件（sdm.services.privileged.AdminComponents）
    charge_limit: object = None         # 充電上限（sdm.services.charge_control.ChargeControl）
    companion: object = None            # 夥伴、桌面寵物與圖示（settings.companion.CompanionContext）
    updater: object = None              # 一鍵更新（sdm.services.updater.Updater 或示範模式的假狀態）


def section_page(content):
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    content.get_style_context().add_class("page")
    scroll.add(content)
    return scroll


class SettingsWindow:
    def __init__(self, application, store, context):
        install_css()
        self.store, self.context = store, context
        self.window = Gtk.ApplicationWindow(application=application)
        self.window.set_title("設定")
        self.window.set_default_size(Ui.SETTINGS_WIDTH, Ui.SETTINGS_HEIGHT)
        self.window.connect("delete-event", self.on_delete)
        header = Gtk.HeaderBar(title="設定", subtitle="系統與裝置監看")
        header.set_show_close_button(True)
        self.window.set_titlebar(header)
        self.shell = NavigationShell(header, "切換分類")
        self.shell.track(self.window)
        self.controls = Controls(store)
        self.banner = wrapped("", "power-warning")
        self.banner.set_margin_start(16)
        self.banner.set_margin_end(16)
        self.banner.set_margin_top(8)
        self.banner.set_no_show_all(True)
        root = box(spacing=0)
        root.pack_start(self.banner, False, False, 0)
        root.pack_start(self.shell.widget, True, True, 0)
        self.window.add(root)
        self.scrolls = {}
        for key, title, module in SECTIONS:
            scroll = section_page(module.build(self.controls, context))
            self.shell.add(key, title, scroll)
            self.scrolls[key] = scroll
        self.controls.bind({STATUS}, self.show_problem)
        self.devices_shown = peripherals.composition(context.peripherals())

    @property
    def stack(self):
        return self.shell.stack

    def show_problem(self):
        problem = self.store.problem
        self.banner.set_text(problem or "")
        self.banner.set_visible(bool(problem))

    def external_power_changed(self):
        self.controls.changed(frozenset({general.EXTERNAL}))

    def power_changed(self):
        """不斷電系統出現或消失。"""
        self.controls.changed(frozenset({notifications.POWER}))

    def peripheral_problem_changed(self):
        self.controls.changed(frozenset({peripherals.PROBLEM}))

    def notice_changed(self):
        self.controls.changed(frozenset({notifications.NOTICE}))

    def devices_changed(self):
        """新的設備清單：組成（設備、名稱、圖示）改變時才更新周邊設備列表。"""
        shown = peripherals.composition(self.context.peripherals())
        if shown != self.devices_shown:
            self.devices_shown = shown
            self.controls.changed(frozenset({peripherals.DEVICES}))
    def admin_changed(self):
        self.controls.changed(frozenset({admin.ADMIN}))

    def update_changed(self):
        self.controls.changed(frozenset({update.UPDATE}))

    def present(self, section=None):
        self.context.probe()
        self.window.show_all()
        if section in self.scrolls:
            self.stack.set_visible_child_name(section)
        self.window.present()

    def on_delete(self, *_):
        self.window.hide()
        return True
