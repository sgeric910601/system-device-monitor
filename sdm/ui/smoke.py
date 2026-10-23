"""--smoke-test：依序顯示各頁、選用時截圖，最後寫出 smoke.json 並結束。

做成 Monitor 的混入類別，讓 app.py 不超過檔案大小上限。示範模式沒有指定 --output-dir 時
不寫任何檔案（結果只印到標準輸出）。
"""
import json
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from sdm.constants import Smoke
from sdm.ui.settings_window import SECTIONS

# 模擬測試依序顯示的頁面，再依序打開設定視窗的各分類；LOWER_CAPTURES 另外截一張捲到底部的畫面
SMOKE_PAGES = ("power", "overview", "cpu", "memory", "disk", "network", "devices")
SETTINGS_SECTIONS = tuple(key for key, _, _ in SECTIONS)
STEPS = tuple(("page", name) for name in SMOKE_PAGES) + tuple(
    ("settings", name) for name in SETTINGS_SECTIONS)
LOWER_CAPTURES = ("power", "cpu", "memory", "disk", "general", "tray", "display",
                  "notifications", "admin")


def settle():
    """只在 --capture 時使用：處理事件一小段時間，讓捲動後的畫面確實重畫
    （電力流向動畫會讓重畫排在下一格，只處理一次事件可能截到舊畫面）。"""
    deadline = time.monotonic() + Smoke.SETTLE_SECONDS
    while time.monotonic() < deadline:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(Smoke.SETTLE_STEP_SECONDS)


class SmokeMixin:
    def smoke_check(self):
        if self.output_dir is not None:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        self.window.show_all()
        self.window.present()
        self.smoke_pages = []
        self.smoke_show(0)
        return False

    def smoke_target(self, kind):
        """（頁面堆疊、各頁捲動容器、視窗）：主視窗或設定視窗。"""
        if kind == "page":
            return self.stack, self.scrolls, self.window
        settings = self.settings_window
        return settings.stack, settings.scrolls, settings.window

    def smoke_show(self, index):
        kind, name = STEPS[index]
        if kind == "page":
            self.stack.set_visible_child_name(name)
        else:
            self.open_settings(name)
        GLib.timeout_add(Smoke.CAPTURE_DELAY_MS, self.smoke_capture, index)
        return False

    def smoke_capture(self, index):
        """確認頁面已切換並顯示，選用時截圖，再排下一頁或結束。"""
        kind, name = STEPS[index]
        stack, scrolls, window = self.smoke_target(kind)
        if stack.get_visible_child_name() == name and scrolls[name].get_mapped():
            self.smoke_pages.append(name if kind == "page" else "settings:" + name)
        file_name = name if kind == "page" else "settings-" + name
        self.capture(file_name, scrolls[name], window)
        if name in LOWER_CAPTURES:
            self.capture(file_name + "-lower", scrolls[name], window, bottom=True)
        if index + 1 < len(STEPS):
            GLib.timeout_add(Smoke.PAGE_DELAY_MS, self.smoke_show, index + 1)
        else:
            GLib.timeout_add(Smoke.FINISH_DELAY_MS, self.smoke_finish)
        return False

    def capture(self, name, scroll, toplevel, bottom=False):
        """只在 --capture 時把視窗存成 name.png；bottom 為 True 時先捲到頁面底部。"""
        if not self.capture_enabled or self.output_dir is None:
            return
        toplevel.set_focus(None)
        adjustment = scroll.get_vadjustment()
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size() if bottom else 0)
        settle()
        window = toplevel.get_window()
        if window:
            width, height = window.get_width(), window.get_height()
            pixbuf = Gdk.pixbuf_get_from_window(window, 0, 0, width, height)
            if pixbuf:
                pixbuf.savev(str(self.output_dir/f"{name}.png"), "png", [], [])

    def smoke_finish(self):
        # 系統監控模組全部關閉時一般層不取樣，沒有資料是正常的
        collector = getattr(self, "collector", None)
        expects_system = collector is None or bool(collector.modules)
        system_ready = bool(self.data["system"]) or not expects_system
        self.smoke_failed = bool(self.errors) or not system_ready
        report = dict(ok=not self.smoke_failed, collector_errors=sorted(self.errors),
                      system_ready=system_ready,
                      indicator_count=self.tray.count() if self.tray is not None else 0,
                      pages=list(getattr(self, "smoke_pages", [])))
        if self.output_dir is not None:
            (self.output_dir/"smoke.json").write_text(json.dumps(report, indent=2) + "\n")
        self.smoke_report = report
        self.stop()
        return False
