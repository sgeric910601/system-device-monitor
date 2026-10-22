"""「網路」頁：主要介面（預設路由）的類型、名稱、IPv4、精簡速率、2 分鐘曲線與開機以來的流量，
以及其他連線中的介面。

主要介面改變時重新建立速率基準，曲線也重新開始。IPv4 只顯示在視窗裡，
不寫進日誌、模擬測試結果或 --dump。
"""
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.core.monitor_history import MonitorHistory
from sdm.ui.monitor_text import (address_text, compact_rates, network_rows, network_title,
                                 network_totals, other_rows)
from sdm.ui.monitor_widgets import MonitorChart, RowsGrid, card, chart_heading, ellipsized, set_text
from sdm.ui.pages.monitor_page import Header, MonitorPage, card_flow, note
from sdm.ui.theme import SERIES
from sdm.ui.widgets import clear, label

NO_OTHERS = "沒有其他連線中的介面"


class NetworkPage(MonitorPage):
    def __init__(self, clock=time.monotonic):
        super().__init__()
        self.clock, self.detail = clock, None
        self.history = MonitorHistory()
        self.other_keys, self.other_cells = None, []
        self.header = Header("網路")
        self.pack_start(self.header, False, False, 0)
        self.pack_start(card_flow(self.build_primary(), self.build_others()), False, False, 0)

    def build_primary(self):
        self.rows = RowsGrid()
        self.chart = MonitorChart(SERIES["down"], SERIES["up"], percent=False, clock=self.clock)
        self.totals = label("", "muted", True)
        return card("主要介面", self.rows,
                    chart_heading(((SERIES["down"], "下載"), (SERIES["up"], "上傳"))),
                    self.chart, self.totals)

    def build_others(self):
        self.others = Gtk.Grid(column_spacing=12, row_spacing=6)
        return card("其他連線中的介面", self.others,
                    note("虛擬介面（容器、VPN 等）也列在這裡；累計為介面建立以來的流量"))

    def seed(self, histories):
        self.history = histories.get("network", self.history)

    def set_detail(self, snapshot):
        network = snapshot.network
        if network is None:
            return
        self.detail = network
        history = MonitorHistory() if network.changed else self.history
        self.history = history.appended(network.rx_rate, network.tx_rate, t=snapshot.stamp)
        self.changed()

    def apply(self):
        detail = self.detail
        set_text(self.header.value, compact_rates(detail))
        set_text(self.header.sub, network_title(detail) + "・" + address_text(detail))
        primary = detail.primary if detail else None
        rows = (("介面", network_title(detail)), ("位址", address_text(detail)))
        if detail is not None:
            rows += network_rows(detail)
        self.rows.set_rows(rows)
        self.chart.set_history(self.history)
        set_text(self.totals, "開機以來：" + network_totals(primary))
        self.apply_others(other_rows(detail) if detail else ())

    def apply_others(self, rows):
        keys = tuple(row[0] for row in rows)
        if keys != self.other_keys:
            self.other_keys = keys
            clear(self.others)
            self.other_cells = []
            if not rows:
                self.others.attach(label(NO_OTHERS, "muted"), 0, 0, 1, 1)
            for index, row in enumerate(rows):
                cells = [label(row[0], "device-name"), label("", "muted"), ellipsized(),
                         ellipsized("", "muted")]
                for column, widget in enumerate(cells):
                    self.others.attach(widget, column % 2, index * 2 + column // 2, 1, 1)
                self.other_cells.append(cells)
            self.others.show_all()
        for cells, row in zip(self.other_cells, rows):
            for widget, text in zip(cells[1:], row[1:]):
                set_text(widget, text)
