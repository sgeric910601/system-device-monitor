"""「CPU／GPU」頁：使用率（使用者／系統）、2 分鐘曲線、各核心、平均負載與時脈、
每張顯示卡、CPU 使用率最高的 5 個 App、風扇與溫度。

各核心、App 排行與溫度只在這頁顯示時才取樣（見 sdm.core.tiers）。
"""
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.core.monitor_history import MonitorHistory
from sdm.core.thermal import GROUP_TITLES, stat_text
from sdm.ui.app_names import placeholder, top_rows
from sdm.ui.monitor_text import (cpu_header, frequency_text, gpu_rows, gpu_title, load_text,
                                 topology_text)
from sdm.ui.monitor_widgets import (CoreBars, MonitorChart, RowsGrid, TopList, card,
                                    chart_heading, set_text)
from sdm.ui.pages.monitor_page import CARD_MIN_WIDTH, Header, MonitorPage, card_flow, note
from sdm.ui.theme import SERIES
from sdm.ui.widgets import box, clear, label

PERCENT = 100
NO_TEMPERATURES = "這台電腦沒有回報可用的溫度感測器"
NO_FANS = "沒有偵測到可讀取的風扇轉速"


class CpuPage(MonitorPage):
    def __init__(self, names, clock=time.monotonic):
        super().__init__()
        self.names, self.clock = names, clock
        self.fast, self.slow = {}, {}
        self.cpu = self.thermal = self.apps = None
        self.history = MonitorHistory()
        self.gpu_ids, self.gpu_grids, self.gpu_note = None, [], None
        self.fan_keys, self.fan_widgets = None, []
        self.header = Header("CPU")
        self.pack_start(self.header, False, False, 0)
        self.pack_start(card_flow(self.build_usage(), self.build_apps()), False, False, 0)
        self.gpu_flow = card_flow()
        self.pack_start(self.gpu_flow, False, False, 0)

    def build_usage(self):
        self.chart = MonitorChart(SERIES["cpu"], SERIES["gpu"], percent=True, clock=self.clock)
        self.core_title = label("各核心", "muted")
        self.cores = CoreBars(SERIES["cpu"])
        self.rows = RowsGrid()
        return card("使用率", chart_heading(((SERIES["cpu"], "CPU"), (SERIES["gpu"], "顯示卡"))),
                    self.chart, self.core_title, self.cores, self.rows)

    def build_apps(self):
        self.top = TopList()
        return card("CPU 使用率最高的 App", self.top,
                    note("100% 代表用滿一個核心；同一個 App 的多個視窗與子程序已合併"))

    def build_thermal(self):
        self.temperatures = RowsGrid()
        self.temperature_note = note("")
        self.fans = box(spacing=6)
        return card("風扇與溫度", self.temperatures, self.temperature_note, self.fans)

    def seed(self, histories):
        self.history = histories.get("cpu", self.history)

    def set_system(self, fast, slow):
        self.fast, self.slow = fast, slow
        self.changed()

    def set_detail(self, snapshot):
        if snapshot.cpu is not None:
            self.cpu = snapshot.cpu
            total = snapshot.cpu.sample.total.total * PERCENT if snapshot.cpu.sample else None
            gpu = self.slow.get("gpu")
            self.history = self.history.appended(total, gpu.usage if gpu else None,
                                                 t=snapshot.stamp)
        if snapshot.thermal is not None:
            self.thermal = snapshot.thermal
        if snapshot.apps is not None and snapshot.apps.has_cpu:
            self.apps = snapshot.apps
        self.changed()

    def apply(self):
        info = self.fast.get("cpu_info", {})
        set_text(self.header.title, "CPU・" + info.get("name", "CPU"))
        value, sub = cpu_header(self.cpu)
        set_text(self.header.value, value)
        set_text(self.header.sub, sub)
        self.chart.set_history(self.history)
        self.apply_cores(info)
        self.apply_gpus()
        self.apply_apps()
        self.apply_thermal()

    def apply_cores(self, info):
        cores = self.cpu.sample.cores if self.cpu and self.cpu.sample else ()
        set_text(self.core_title, f"各核心（{len(cores)} 個邏輯處理器）" if cores else "各核心")
        self.cores.set_values([core.total for core in cores])
        self.rows.set_rows((
            ("平均負載（1／5／15 分）", load_text(self.cpu.load if self.cpu else None)),
            ("平均時脈", frequency_text(self.cpu.frequency_mhz if self.cpu else None)),
            ("核心", topology_text(info))))

    def apply_gpus(self):
        gpus = tuple(self.slow.get("gpus") or ())
        ids = tuple(gpu.gpu_id for gpu in gpus)
        if ids != self.gpu_ids:
            self.rebuild_gpus(gpus, ids)
        for grid, gpu in zip(self.gpu_grids, gpus):
            grid.set_rows(gpu_rows(gpu))
        if self.gpu_note is not None:
            set_text(self.gpu_note, self.slow.get("gpu_error") or "正在讀取…")

    def rebuild_gpus(self, gpus, ids):
        """顯示卡組成改變時重建（每張卡一張卡片，最後是風扇與溫度）。"""
        self.gpu_ids = ids
        clear(self.gpu_flow)
        self.gpu_grids = [RowsGrid() for _ in gpus]
        cards = [card(gpu_title(gpu), grid) for gpu, grid in zip(gpus, self.gpu_grids)]
        self.gpu_note = None if gpus else note("")
        if self.gpu_note is not None:
            cards.append(card("顯示卡", self.gpu_note))
        cards.append(self.build_thermal())
        self.fan_keys = None
        for child in cards:
            child.set_size_request(CARD_MIN_WIDTH, -1)
            self.gpu_flow.add(child)
        for child in self.gpu_flow.get_children():
            child.set_can_focus(False)
        self.gpu_flow.show_all()

    def apply_apps(self):
        if self.apps is None:
            self.top.set_rows((), placeholder(None, "cpu"))
            return
        rows = top_rows(self.apps, self.names, "cpu", self.apps.cores)
        self.top.set_rows(rows, placeholder(self.apps, "cpu"))

    def apply_thermal(self):
        if self.thermal is None:
            self.temperatures.set_rows((("溫度", "讀取中…"),))
            set_text(self.temperature_note, "")
            return
        rows = tuple((GROUP_TITLES[group], stat_text(stat)) for group, stat in self.thermal.groups)
        self.temperatures.set_rows(rows or (("溫度", NO_TEMPERATURES),))
        set_text(self.temperature_note, "平均（最高）・讀不到時沿用最近 2 分鐘內的讀值"
                 if rows else "")
        self.apply_fans(self.thermal.fans)

    def apply_fans(self, fans):
        """風扇組成改變才重建，否則只更新轉速與比例。"""
        keys = tuple((fan.name, fan.has_fraction) for fan in fans)
        if keys != self.fan_keys:
            self.fan_keys = keys
            clear(self.fans)
            self.fan_widgets = [self.build_fan(fan) for fan in fans]
            if not fans:
                self.fans.pack_start(note(NO_FANS), False, False, 0)
            self.fans.show_all()
        for (value, bar), fan in zip(self.fan_widgets, fans):
            set_text(value, fan.text())
            if bar is not None:
                bar.set_fraction(fan.fraction)

    def build_fan(self, fan):
        line = box(False, 8)
        value = label("")
        line.pack_start(label(fan.name, "muted"), False, False, 0)
        line.pack_end(value, False, False, 0)
        self.fans.pack_start(line, False, False, 0)
        bar = Gtk.ProgressBar() if fan.has_fraction else None
        if bar is not None:
            self.fans.pack_start(bar, False, False, 0)
        return value, bar
