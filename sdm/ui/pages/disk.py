"""「磁碟」頁：每顆磁碟一張卡片（型號・連線・容量、健康徽章、溫度、SMART、各分割區用量、
讀寫速率與 2 分鐘曲線、開機以來累計、出廠以來讀寫量）。

讀寫速率、累計與 SMART 壽命資料只在這頁顯示時才取樣（見 sdm.core.tiers）。
SMART 來自系統的磁碟服務（UDisks2），不需要管理員權限。
"""
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango

from sdm.core import byte_format
from sdm.core.disk_health import summarize
from sdm.core.monitor_history import MonitorHistory
from sdm.ui.monitor_text import (disk_rates, disk_temperature, disk_title, disk_totals,
                                 volume_fraction, volume_title, volume_tone, volume_usage)
from sdm.ui.monitor_widgets import (MonitorChart, RowsGrid, badge, chart_heading, ellipsized,
                                    set_text, set_tone)
from sdm.ui.pages.monitor_page import Header, MonitorPage, note
from sdm.ui.theme import SERIES
from sdm.ui.widgets import box, clear, label

SMART_NOTE = "SMART 由系統的磁碟服務（UDisks2）提供，不需要管理員權限；每分鐘更新"
EXTERNAL_TAG = "外接"
INNER_MIN_WIDTH = 360
SEED_PREFIX = "disk:"


class DiskCard(Gtk.Box):
    """一顆磁碟；分割區組成改變時由頁面整張重建。"""

    def __init__(self, disk, clock):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.get_style_context().add_class("card")
        self.volume_keys = tuple(volume.mount for volume in disk.volumes)
        heading = box(False, 8)
        self.title = ellipsized(disk_title(disk), "section")
        heading.pack_start(self.title, True, True, 0)
        if disk.external:
            heading.pack_end(badge(EXTERNAL_TAG), False, False, 0)
        self.pack_start(heading, False, False, 0)
        left, right = self.build_health(disk), self.build_activity(clock)
        for column in (left, right):
            column.set_size_request(INNER_MIN_WIDTH, -1)
        inner = Gtk.FlowBox(homogeneous=True, min_children_per_line=1, max_children_per_line=2,
                            column_spacing=24, row_spacing=12,
                            selection_mode=Gtk.SelectionMode.NONE)
        inner.add(left)
        inner.add(right)
        for child in inner.get_children():
            child.set_can_focus(False)
        self.pack_start(inner, False, False, 0)

    def build_health(self, disk):
        column = box(spacing=8)
        status = box(False, 10)
        self.health = badge()
        self.temperature = label("", "muted")
        status.pack_start(self.health, False, False, 0)
        status.pack_start(self.temperature, False, False, 0)
        column.pack_start(status, False, False, 0)
        self.smart = RowsGrid()
        column.pack_start(self.smart, False, False, 0)
        self.volumes = []
        for volume in disk.volumes:
            title, usage, bar = label(volume_title(volume), "device-name"), label("", "muted"), \
                Gtk.ProgressBar()
            title.set_ellipsize(Pango.EllipsizeMode.END)
            bar.get_style_context().add_class("volume-bar")
            for widget in (title, usage, bar):
                column.pack_start(widget, False, False, 0)
            self.volumes.append((usage, bar))
        if not disk.volumes:
            column.pack_start(note("沒有已掛載的分割區"), False, False, 0)
        return column

    def build_activity(self, clock):
        column = box(spacing=8)
        self.chart = MonitorChart(SERIES["read"], SERIES["write"], percent=False, clock=clock)
        self.rates = label("", "metric-value")
        self.totals = label("", "muted", True)
        column.pack_start(chart_heading(((SERIES["read"], "讀取"), (SERIES["write"], "寫入"))),
                          False, False, 0)
        for widget in (self.chart, self.rates, self.totals):
            column.pack_start(widget, False, False, 0)
        return column

    def update(self, disk, history):
        set_text(self.title, disk_title(disk))
        summary = summarize(disk.smart)
        set_text(self.health, summary.badge)
        set_tone(self.health, summary.tone)
        set_text(self.temperature, disk_temperature(disk) if disk.temperature is None
                 else "溫度 " + disk_temperature(disk))
        self.smart.set_rows(tuple(line for line in summary.lines if line[0] != "溫度"))
        for (usage, bar), volume in zip(self.volumes, disk.volumes):
            set_text(usage, volume_usage(volume))
            bar.set_fraction(volume_fraction(volume))
            set_tone(bar, volume_tone(volume), prefix="bar-")
        self.chart.set_history(history)
        set_text(self.rates, disk_rates(disk))
        set_text(self.totals, "開機以來：" + disk_totals(disk))


class DiskPage(MonitorPage):
    def __init__(self, clock=time.monotonic):
        super().__init__()
        self.clock = clock
        self.disks, self.histories, self.keys, self.cards = (), {}, None, []
        self.header = Header("磁碟")
        self.pack_start(self.header, False, False, 0)
        self.list = box(spacing=16)
        self.pack_start(self.list, False, False, 0)
        self.pack_start(note(SMART_NOTE), False, False, 0)

    def seed(self, histories):
        """鍵為「disk:磁碟名稱」。"""
        self.histories = {key[len(SEED_PREFIX):]: value for key, value in histories.items()
                          if key.startswith(SEED_PREFIX)}

    def set_detail(self, snapshot):
        if snapshot.disks is None:
            return
        self.disks = snapshot.disks
        self.histories = {disk.name: self.histories.get(disk.name, MonitorHistory()).appended(
            disk.read_rate, disk.write_rate, t=snapshot.stamp) for disk in snapshot.disks}
        self.changed()

    def apply(self):
        reads = [disk.read_rate for disk in self.disks if disk.read_rate is not None]
        writes = [disk.write_rate for disk in self.disks if disk.write_rate is not None]
        set_text(self.header.value, f"{len(self.disks)} 顆" if self.disks else "—")
        set_text(self.header.sub, "全部磁碟：讀取 " + byte_format.rate(sum(reads) if reads else None)
                 + "・寫入 " + byte_format.rate(sum(writes) if writes else None)
                 if self.disks else "讀取中…")
        keys = tuple((disk.name, disk.external, tuple(v.mount for v in disk.volumes))
                     for disk in self.disks)
        if keys != self.keys:
            self.keys = keys
            clear(self.list)
            self.cards = [DiskCard(disk, self.clock) for disk in self.disks]
            for disk_card in self.cards:
                self.list.pack_start(disk_card, False, False, 0)
            self.list.show_all()
        for disk_card, disk in zip(self.cards, self.disks):
            disk_card.update(disk, self.histories.get(disk.name, MonitorHistory()))
