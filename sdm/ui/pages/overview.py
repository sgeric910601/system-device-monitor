"""「系統總覽」頁：CPU、溫度、記憶體、GPU、網路與不斷電系統卡片、磁碟與網路介面、其他感測器。

卡片排在 FlowBox，視窗變窄時自動換行；點一下卡片開啟對應頁面。
關閉的系統監控模組不顯示對應的卡片與區塊（不斷電系統卡片屬於耗電，一直顯示）。
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.core.fmt import percent, size, temperature, value_text
from sdm.data.ups import UpsReading
from sdm.ui.monitor_wiring import OVERVIEW_TARGETS
from sdm.ui.theme import GRAPH_COLORS
from sdm.ui.widgets import Graph, box, clear, label

CARDS = (("cpu", "CPU"), ("temperature", "CPU 溫度"), ("ram", "記憶體"), ("gpu", "GPU"),
         ("network", "網路 · 實體介面合計"), ("ups", "APC UPS"))
CARD_MODULES = {"cpu": "cpu", "temperature": "cpu", "gpu": "cpu", "ram": "memory",
                "network": "network", "ups": None}
SECTION_MODULES = {"storage": "disk", "interfaces": "network", "sensors": "cpu"}
CARD_MIN_WIDTH = 232            # 曲線最小寬度 200 加上卡片內距
MAX_COLUMNS = 3
ALL_OFF = "系統監控已關閉：可在「設定 → 監控模組」開啟。"


def card_box(key, name):
    card = box()
    for css in ("card", "accent-" + key):
        card.get_style_context().add_class(css)
    title = label(name, "muted", True)
    card.pack_start(title, False, False, 0)
    return card, title


class OverviewPage(Gtk.Box):
    def __init__(self, series, clickable):
        """series：曲線資料（cpu、gpu、ram）；clickable(元件, 頁面) 包成可點擊的容器。"""
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.cards, self.card_titles, self.graphs, self.children = {}, {}, [], {}
        self.pack_start(label("工作站即時狀態", "section"), False, False, 0)
        self.flow = Gtk.FlowBox(homogeneous=True, min_children_per_line=1,
                                max_children_per_line=MAX_COLUMNS, column_spacing=12,
                                row_spacing=12, selection_mode=Gtk.SelectionMode.NONE)
        for key, name in CARDS:
            self.add_card(key, name, series, clickable)
        self.pack_start(self.flow, False, False, 0)
        self.note = label("曲線顯示最近 5 分鐘 · 點一下卡片可開啟詳細頁面", "muted", True)
        self.note.show()
        self.note.set_no_show_all(True)     # 顯示與否只由 set_modules() 決定
        self.pack_start(self.note, False, False, 0)
        self.empty = label(ALL_OFF, "muted", True)
        self.empty.set_no_show_all(True)
        self.pack_start(self.empty, False, False, 0)
        self.storage_box, self.network_box = box(spacing=10), box(spacing=8)
        self.sensor_label = label("讀取中…", "muted", True)
        self.sections = {
            "storage": self.section("磁碟與儲存空間", clickable(self.storage_box, "disk")),
            "interfaces": self.section("網路介面", clickable(self.network_box, "network")),
            "sensors": self.section("其他感測器與風扇", self.sensor_label)}

    def add_card(self, key, name, series, clickable):
        card, title = card_box(key, name)
        self.card_titles[key] = title
        value, sub = label("—", "value"), label("正在讀取…", "muted", True)
        card.pack_start(value, False, False, 0)
        if key in series:
            graph = Graph(series[key], GRAPH_COLORS[key])
            self.graphs.append(graph)
            card.pack_start(graph, True, True, 0)
        card.pack_start(sub, False, False, 0)
        card.set_size_request(CARD_MIN_WIDTH, -1)
        self.cards[key] = (value, sub)
        self.flow.add(clickable(card, OVERVIEW_TARGETS[key]))
        child = self.flow.get_children()[-1]
        child.set_can_focus(False)
        self.children[key] = child

    def section(self, title, content):
        container = box(spacing=10)
        container.pack_start(label(title, "section"), False, False, 0)
        container.pack_start(content, False, False, 0)
        self.pack_start(container, False, False, 0)
        return container

    def set_modules(self, modules):
        """modules：開啟中的系統監控模組（cpu、memory、disk、network）。"""
        for key, child in self.children.items():
            module = CARD_MODULES[key]
            shown = module is None or module in modules
            child.set_no_show_all(not shown)
            child.show_all() if shown else child.hide()
        for key, container in self.sections.items():
            shown = SECTION_MODULES[key] in modules
            container.set_no_show_all(not shown)
            container.show_all() if shown else container.hide()
        self.note.set_visible(bool(modules))
        self.empty.set_visible(not modules)

    def render(self, fast, slow):
        self.render_cards(fast, slow)
        for graph in self.graphs:
            graph.queue_draw()
        self.render_storage(fast, slow)
        self.render_networks(fast, slow)
        sensors = [f"{s['chip']} {s['label']}  {temperature(s['value'])}"
                   for s in slow.get("temperatures", [])]
        sensors += [f"{f['name']}  {f['value']:.0f} RPM" for f in slow.get("fans", [])]
        if not slow.get("fans"):
            sensors.append("CPU／機殼風扇轉速：無法取得")
        self.sensor_label.set_text("   ·   ".join(sensors))

    def render_cards(self, fast, slow):
        gpu, ups = fast.get("gpu") or slow.get("gpu"), slow.get("ups") or UpsReading()
        info = fast.get("cpu_info", {})
        cards, titles = self.cards, self.card_titles
        cards["cpu"][0].set_text(percent(fast.get("cpu")))
        titles["cpu"].set_text(info.get("name", "CPU") + " · " + temperature(slow.get("cpu_temp")))
        cards["cpu"][1].set_text(f"{info.get('cores') or '—'} 核心 · "
                                 f"{info.get('threads') or '—'} 執行緒 · 全機使用率")
        titles["gpu"].set_text((gpu.name if gpu else "GPU") + " · " +
                               temperature(gpu.temp_c if gpu else None))
        cards["gpu"][0].set_text(percent(gpu.usage if gpu else None))
        cards["gpu"][1].set_text(
            f"顯示記憶體 {size(gpu.vram_used)} / {size(gpu.vram_total)}\n"
            f"{value_text(gpu.watts, ' W')} · 風扇 {percent(gpu.fan_pct)}"
            if gpu else slow.get("gpu_error") or "正在讀取…")
        cards["ram"][0].set_text(percent(fast.get("ram")))
        cards["ram"][1].set_text(f"{size(fast.get('ram_used'))} / {size(fast.get('ram_total'))}"
                                 "\n依系統可用記憶體計算")
        cards["network"][0].set_text("↓ " + size(fast.get("rx"), True))
        cards["network"][1].set_text("↑ " + size(fast.get("tx"), True) + " · 虛擬網路獨立列出")
        cards["temperature"][0].set_text(temperature(slow.get("cpu_temp")))
        cards["temperature"][1].set_text(slow.get("cpu_temp_label", "讀取中…"))
        mode = "市電供電" if ups.online else ("電池供電" if ups.on_battery else "通訊暫時無法取得")
        cards["ups"][0].set_text(percent(ups.charge_pct))
        cards["ups"][1].set_text(f"{mode} · 負載 {percent(ups.load_pct)}\n"
                                 f"預估續航 {value_text(ups.time_left_min, ' 分鐘')}")

    def render_storage(self, fast, slow):
        clear(self.storage_box)
        rates = {d["name"]: d for d in fast.get("disks", [])}
        for disk in slow.get("storage", []):
            self.storage_box.pack_start(self.disk_card(disk, rates.get(disk["name"], {})),
                                        False, False, 0)
        self.storage_box.show_all()

    @staticmethod
    def disk_card(disk, rates):
        container = box(spacing=6)
        container.get_style_context().add_class("card")
        accent = {"sda": "gpu", "sdb": "network"}.get(disk["name"], "ram")
        container.get_style_context().add_class("accent-" + accent)
        heading = f"{disk['name']} · {disk['model']} · {size(disk['size'])} · " + (
            temperature(disk["temperature"]) if disk.get("temperature") is not None
            else "溫度未回報")
        container.pack_start(label(heading, "device-name", True), False, False, 0)
        container.pack_start(label(f"讀取 {size(rates.get('read'), True)}    "
                                   f"寫入 {size(rates.get('write'), True)}", "muted", True),
                             False, False, 0)
        for volume in disk["volumes"]:
            used, total = volume["used"], volume["total"]
            container.pack_start(label(f"{volume['mount']}  ·  已用 {size(used)} / {size(total)}",
                                       "muted", True), False, False, 0)
            bar = Gtk.ProgressBar()
            bar.set_fraction(max(0, min(1, used / total)) if total else 0)
            container.pack_start(bar, False, False, 0)
        return container

    def render_networks(self, fast, slow):
        clear(self.network_box)
        temperatures = slow.get("network_temperatures", {})
        for net in fast.get("networks", []):
            extra = (" · " + temperature(temperatures[net["name"]])
                     if net["name"] in temperatures else "")
            self.network_box.pack_start(label(
                f"{net['name']} · {net['kind']}{extra}     ↓ {size(net['rx'], True)}"
                f"     ↑ {size(net['tx'], True)}", "muted", True), False, False, 0)
        self.network_box.show_all()
