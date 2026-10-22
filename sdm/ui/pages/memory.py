"""「記憶體」頁：已使用比例、組成長條與圖例、2 分鐘曲線、壓力徽章（正常／橘／紅）、
Swap 與 zswap，以及使用記憶體最多的 5 個 App。

組成、壓力與 App 排行只在這頁顯示時才取樣（見 sdm.core.tiers）。
已使用＝總量−可用，與總覽頁、頂端列相同。
"""
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.core import byte_format
from sdm.core.memory import CRITICAL, NORMAL, WARNING
from sdm.core.monitor_history import MonitorHistory
from sdm.ui.app_names import placeholder, top_rows
from sdm.ui.monitor_text import (LEGEND_NAMES, memory_header, memory_legend, pressure_badge,
                                 pressure_text, swap_text, zswap_text)
from sdm.ui.monitor_widgets import (CompositionBar, MonitorChart, RowsGrid, TopList, card,
                                    set_text, set_tone)
from sdm.ui.pages.cpu import chart_heading
from sdm.ui.pages.monitor_page import Header, MonitorPage, card_flow, note
from sdm.ui.theme import SEGMENTS, SERIES
from sdm.ui.widgets import box, label

PERCENT = 100
TONES = {NORMAL: "good", WARNING: "warning", CRITICAL: "critical"}
COLUMNS = 2
AVAILABLE_NOTE = "可用＝空閒＋可以立即釋放的快取，App 需要時可以直接使用"


def swatch(color):
    """圖例的色塊（與組成長條同色）。"""
    def draw(_widget, cr):
        cr.set_source_rgba(*color)
        cr.paint()
        return False
    area = Gtk.DrawingArea()
    area.set_size_request(10, 10)
    area.set_valign(Gtk.Align.CENTER)
    area.get_style_context().add_class("swatch")
    area.connect("draw", draw)
    return area


class MemoryPage(MonitorPage):
    def __init__(self, names, clock=time.monotonic):
        super().__init__()
        self.names, self.clock = names, clock
        self.memory = self.apps = None
        self.history = MonitorHistory()
        self.header = Header("記憶體", with_badge=True)
        self.pack_start(self.header, False, False, 0)
        self.pack_start(card_flow(self.build_composition(), self.build_chart(),
                                  self.build_swap(), self.build_apps()), False, False, 0)

    def build_composition(self):
        self.bar = CompositionBar()
        grid = Gtk.Grid(column_spacing=12, row_spacing=6)
        self.legend_values = {}
        colors = dict(SEGMENTS)
        for index, (key, name) in enumerate(LEGEND_NAMES):
            row, column = divmod(index, COLUMNS)
            item = box(False, 6)
            value = label("—")
            item.pack_start(swatch(colors[key]), False, False, 0)
            item.pack_start(label(name, "muted"), False, False, 0)
            item.pack_start(value, False, False, 0)
            grid.attach(item, column, row, 1, 1)
            self.legend_values[key] = value
        self.available = RowsGrid()
        return card("組成", self.bar, grid, self.available, note(AVAILABLE_NOTE))

    def build_chart(self):
        self.chart = MonitorChart(SERIES["memory"], percent=True, clock=self.clock)
        return card("已使用", chart_heading(((SERIES["memory"], "已使用"),)), self.chart)

    def build_swap(self):
        self.swap = RowsGrid()
        return card("Swap 與記憶體壓力", self.swap,
                    note("壓力：有程序等待記憶體的時間比例；超過 10% 轉橘、全部停住超過 5% 轉紅"))

    def build_apps(self):
        self.top = TopList()
        return card("使用記憶體最多的 App", self.top,
                    note("App 自己配置的記憶體（不含共用的檔案快取）"))

    def seed(self, histories):
        self.history = histories.get("memory", self.history)

    def set_detail(self, snapshot):
        if snapshot.memory is not None:
            self.memory = snapshot.memory
            composition = snapshot.memory.composition
            used = composition.used_fraction * PERCENT if composition else None
            self.history = self.history.appended(used, None, t=snapshot.stamp)
        if snapshot.apps is not None and snapshot.apps.has_memory:
            self.apps = snapshot.apps
        self.changed()

    def apply(self):
        value, sub = memory_header(self.memory)
        set_text(self.header.value, value)
        set_text(self.header.sub, sub)
        level = self.memory.level if self.memory else NORMAL
        set_text(self.header.badge, pressure_badge(level))
        set_tone(self.header.badge, TONES[level])
        set_tone(self.header.value, None if level == NORMAL else TONES[level])
        self.chart.set_history(self.history)
        self.apply_composition()
        self.apply_apps()

    def apply_composition(self):
        memory = self.memory.composition if self.memory else None
        if memory is None:
            return
        colors = dict(SEGMENTS)
        self.bar.set_segments([(value / memory.total, colors[key])
                               for key, value in memory.segments()])
        for key, _, text in memory_legend(memory):
            set_text(self.legend_values[key], text)
        self.available.set_rows((("可用", byte_format.memory(memory.available)),))
        rows = [("Swap", swap_text(memory))]
        zswap = zswap_text(memory)
        if zswap is not None:
            rows.append(("壓縮快取（zswap）", zswap))
        rows.append(("記憶體壓力", pressure_text(self.memory.pressure)))
        self.swap.set_rows(tuple(rows))

    def apply_apps(self):
        if self.apps is None:
            self.top.set_rows((), placeholder(None, "memory"))
            return
        self.top.set_rows(top_rows(self.apps, self.names, "memory", self.apps.cores),
                          placeholder(self.apps, "memory"))
