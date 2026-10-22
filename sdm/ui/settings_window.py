"""設定視窗：左側分類（一般、頂端列、監控模組、顯示項目、關於），右側該分類的卡片。

視窗寬度小於 640 px 時分類側欄收起，改用標題列的選單按鈕（與主視窗相同）。
所有控制項都經由 SettingsStore 寫入並即時套用；關閉視窗只是隱藏，下次開啟維持原本的分類。
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.constants import Ui
from sdm.services.settings_store import STATUS
from sdm.ui.main_window import install_css
from sdm.ui.navigation import NavigationShell
from sdm.ui.settings import about, display, general, monitors, topbar
from sdm.ui.settings.controls import Controls, wrapped
from sdm.ui.widgets import box

SECTIONS = (("general", "一般", general), ("tray", "頂端列", topbar),
            ("monitors", "監控模組", monitors), ("display", "顯示項目", display),
            ("about", "關於", about))


@dataclass(frozen=True)
class SettingsContext:
    laptop: bool                        # 有系統電池：用「電源」「電池」的說法與筆電的詳細欄位
    root: Path                          # 程式所在位置（找說明文件）
    version: str
    external_power: Callable[[], bool]  # 目前是否接著外部電源（省電說明）


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
        self.window.set_icon_name("utilities-system-monitor")
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

    @property
    def stack(self):
        return self.shell.stack

    def show_problem(self):
        problem = self.store.problem
        self.banner.set_text(problem or "")
        self.banner.set_visible(bool(problem))

    def external_power_changed(self):
        self.controls.changed(frozenset({general.EXTERNAL}))

    def present(self, section=None):
        self.window.show_all()
        if section is not None:
            self.stack.set_visible_child_name(section)
        self.window.present()

    def on_delete(self, *_):
        self.window.hide()
        return True
