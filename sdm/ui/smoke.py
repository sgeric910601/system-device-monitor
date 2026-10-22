"""--smoke-test：依序顯示各頁、選用時截圖，最後寫出 smoke.json 並結束。

做成 Monitor 的混入類別，讓 app.py 不超過檔案大小上限。示範模式沒有指定 --output-dir 時
不寫任何檔案（結果只印到標準輸出）。
"""
import json

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from sdm.constants import Smoke

# 模擬測試依序顯示的頁面；LOWER_CAPTURES 另外截一張捲到底部的畫面
SMOKE_PAGES = ("power", "overview", "cpu", "memory", "disk", "network", "devices", "settings")
LOWER_CAPTURES = ("power", "cpu", "memory", "disk")


class SmokeMixin:
    def smoke_check(self):
        if self.output_dir is not None:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        self.window.show_all()
        self.window.present()
        self.smoke_pages = []
        self.smoke_show(0)
        return False

    def smoke_show(self, index):
        self.stack.set_visible_child_name(SMOKE_PAGES[index])
        GLib.timeout_add(Smoke.CAPTURE_DELAY_MS, self.smoke_capture, index)
        return False

    def smoke_capture(self, index):
        """確認頁面已切換並顯示，選用時截圖，再排下一頁或結束。"""
        name = SMOKE_PAGES[index]
        if self.stack.get_visible_child_name() == name and self.scrolls[name].get_mapped():
            self.smoke_pages.append(name)
        self.capture(name, name)
        if name in LOWER_CAPTURES:
            self.capture(name + "-lower", name, bottom=True)
        if index + 1 < len(SMOKE_PAGES):
            GLib.timeout_add(Smoke.PAGE_DELAY_MS, self.smoke_show, index + 1)
        else:
            GLib.timeout_add(Smoke.FINISH_DELAY_MS, self.smoke_finish)
        return False

    def capture(self, name, page, bottom=False):
        """只在 --capture 時把視窗存成 name.png；bottom 為 True 時先捲到頁面底部。"""
        if not self.capture_enabled or self.output_dir is None:
            return
        self.window.set_focus(None)
        adjustment = self.scrolls[page].get_vadjustment()
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size() if bottom else 0)
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        window = self.window.get_window()
        if window:
            width, height = window.get_width(), window.get_height()
            pixbuf = Gdk.pixbuf_get_from_window(window, 0, 0, width, height)
            if pixbuf:
                pixbuf.savev(str(self.output_dir/f"{name}.png"), "png", [], [])

    def smoke_finish(self):
        self.smoke_failed = bool(self.errors) or not bool(self.data["system"])
        report = dict(ok=not self.smoke_failed, collector_errors=sorted(self.errors),
                      system_ready=bool(self.data["system"]),
                      indicator_count=len(getattr(self, "indicators", {})) +
                      (1 if getattr(self, "power_indicator", None) else 0),
                      pages=list(getattr(self, "smoke_pages", [])))
        if self.output_dir is not None:
            (self.output_dir/"smoke.json").write_text(json.dumps(report, indent=2) + "\n")
        self.smoke_report = report
        self.stop()
        return False
