"""看板的系統監控頁（Monitor 的混入類別）：建立四個頁面、依頁面顯示狀態排程詳細取樣，
並讓總覽頁的卡片點一下就開啟對應的頁面。

- 每個頁面的 map／unmap 與視窗最小化都會重新計算需要的詳細取樣（sdm.core.tiers）。
- 沒有任何頁面需要時不留計時器：視窗關閉時的 CPU 用量與之前相同。
- 頁面顯示狀態一改變，世代就加一；之前送出的取樣結果一律丟棄。
拆出 app.py，讓主程式維持在檔案大小上限內；方法仍透過 Monitor 物件呼叫。
"""
import logging
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from sdm.core.tiers import (PAGE_CPU, PAGE_DISK, PAGE_MEMORY, PAGE_NETWORK, DetailGate,
                            request_for, storage_visible)
from sdm.ui.app_names import AppNames
from sdm.ui.pages.cpu import CpuPage
from sdm.ui.pages.disk import DiskPage
from sdm.ui.pages.memory import MemoryPage
from sdm.ui.pages.network import NetworkPage

DETAIL = "detail"                   # busy 集合中代表詳細取樣進行中
DETAIL_ERROR = "部分詳細資料暫時無法取得"
PAGE_TITLES = ((PAGE_CPU, "CPU／GPU"), (PAGE_MEMORY, "記憶體"), (PAGE_DISK, "磁碟"),
               (PAGE_NETWORK, "網路"))
# 總覽頁卡片 → 點一下開啟的頁面
OVERVIEW_TARGETS = {"cpu": PAGE_CPU, "temperature": PAGE_CPU, "gpu": PAGE_CPU,
                    "ram": PAGE_MEMORY, "network": PAGE_NETWORK, "ups": "power"}
POINTER = "pointer"


class MonitorMixin:
    detail_timer = 0
    detail_gate = None
    window_iconified = False
    monitor_pages = {}
    seeded = frozenset()

    def build_monitor_pages(self):
        """「系統總覽」之後的四個頁面；示範模式先填好示範曲線。"""
        names = AppNames()
        pages = {PAGE_CPU: CpuPage(names), PAGE_MEMORY: MemoryPage(names),
                 PAGE_DISK: DiskPage(), PAGE_NETWORK: NetworkPage()}
        for key, title in PAGE_TITLES:
            self.page(key, title).pack_start(pages[key], False, False, 0)
        self.monitor_pages = pages
        self.detail_gate = DetailGate()
        self.seeded = set()

    def seed_visible(self, pages):
        """示範模式：頁面第一次顯示時填好 2 分鐘的示範曲線，接得上之後的即時資料。"""
        seed = getattr(self.collector, "histories", None)
        fresh = [name for name in pages if name in self.monitor_pages and name not in self.seeded]
        if seed is None or not fresh:
            return
        histories = seed(time.monotonic())
        for name in fresh:
            self.monitor_pages[name].seed(histories)
        self.seeded = self.seeded | set(fresh)

    def watch_visibility(self):
        """所有頁面建立後呼叫：頁面顯示或隱藏、視窗最小化時重新計算詳細取樣。"""
        for scroll in self.scrolls.values():
            scroll.connect("map", self.visibility_changed)
            scroll.connect("unmap", self.visibility_changed)
        self.window.connect("window-state-event", self.window_state_changed)

    def window_state_changed(self, _window, event):
        iconified = bool(event.new_window_state & Gdk.WindowState.ICONIFIED)
        if iconified != self.window_iconified:
            self.window_iconified = iconified
            self.visibility_changed()
            self.update_motion()
        return False

    def visible_pages(self):
        if self.window_iconified:
            return set()
        return {name for name, scroll in self.scrolls.items() if scroll.get_mapped()}

    def visibility_changed(self, *_):
        if self.closing or self.detail_gate is None:
            return
        pages = self.visible_pages()
        self.seed_visible(pages)
        self.collector.storage_visible = storage_visible(pages)
        request = request_for(pages)
        if request is None:
            self.errors.pop(DETAIL, None)    # 沒有頁面需要詳細資料：頁尾不再顯示它的錯誤
        if self.detail_gate.update(request):
            self.restart_detail_timer(request)

    def restart_detail_timer(self, request):
        self.stop_detail()
        if request is not None and not self.closing:
            self.detail_timer = GLib.timeout_add_seconds(self.cadence.detail, self.detail_tick)
            self.submit_detail()

    def stop_detail(self):
        if self.detail_timer:
            GLib.source_remove(self.detail_timer)
            self.detail_timer = 0

    def detail_tick(self):
        if self.closing or self.detail_gate.ticket() is None:
            self.detail_timer = 0
            return False
        self.submit_detail()
        return True

    def submit_detail(self):
        ticket = self.detail_gate.ticket()
        if ticket is None or DETAIL in self.busy or self.closing:
            return
        self.busy.add(DETAIL)
        future = self.pool.submit(self.collector.detail, self.detail_gate.request)
        future.add_done_callback(
            lambda result: GLib.idle_add(self.detail_received, ticket, result))

    def detail_received(self, ticket, future):
        """過期（頁面已隱藏或換頁）的結果丟棄；還有頁面需要時立即補一次。"""
        self.busy.discard(DETAIL)
        if self.closing:
            return False
        if not self.detail_gate.accepts(ticket):
            self.submit_detail()
            return False
        try:
            snapshot = future.result()
        except Exception as error:
            logging.error("Collector detail failed: %s", type(error).__name__)
            self.errors[DETAIL] = DETAIL_ERROR
            return False
        self.errors.pop(DETAIL, None)
        for page in self.monitor_pages.values():
            page.set_detail(snapshot)
        if self.power_page is not None:
            self.power_page.set_detail(snapshot)    # 耗電排行
        return False

    def render_monitor_pages(self, fast, slow):
        for page in self.monitor_pages.values():
            page.set_system(fast, slow)

    def clickable(self, widget, page):
        """總覽頁的卡片：點一下開啟對應頁面，滑鼠移上去顯示手指游標。

        不加提示文字：卡片下方已有說明，而且提示視窗會蓋住卡片內容。
        """
        events = Gtk.EventBox()
        events.set_visible_window(False)    # 只接收點擊，背景與邊框仍由卡片自己畫
        events.add(widget)
        events.connect("button-release-event", self.open_from_overview, page)
        events.connect("realize", self.pointer_cursor)
        return events

    def open_from_overview(self, _widget, _event, page):
        self.stack.set_visible_child_name(page)
        return True

    @staticmethod
    def pointer_cursor(widget):
        window = widget.get_window()
        if window is not None:
            window.set_cursor(Gdk.Cursor.new_from_name(widget.get_display(), POINTER))
