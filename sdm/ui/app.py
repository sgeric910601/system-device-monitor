"""GTK 看板應用程式：所有視圖共用同一份資料快照。"""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
import logging
from pathlib import Path
import sys
import time
from typing import NamedTuple, Optional

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GUdev", "1.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import Gtk, Gio, GLib, Gdk, GUdev, AyatanaAppIndicator3

from sdm import paths
from sdm.constants import Collect, History, Smoke, Solaar, Ui
from sdm.core.devices import battery_menu_rows
from sdm.core.fmt import percent, size, age, temperature, value_text
from sdm.core.settings import LEGACY_KEYS, load_settings, save_legacy
from sdm.data.collectors import Collectors
from sdm.data.ups import UpsReading
from sdm.services.autostart import set_autostart, initialize_preferences
from sdm.services.history_service import HistoryService
from sdm.services.power_service import desktop_history_sample
from sdm.ui.theme import GRAPH_COLORS, css_data
from sdm.ui.widgets import label, box, clear, Graph

APP_ID = "tw.local.SystemDeviceMonitor"
MENU_INDENT = "    "


class RunResult(NamedTuple):
    """執行結果：程序結束碼，以及 smoke test 的摘要（一般執行為 None）。"""
    code: int
    report: Optional[dict]


class Monitor(Gtk.Application):
    def __init__(self, background=False, smoke=False, output_dir=None, capture=False):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.background, self.smoke = background, smoke
        self.output_dir = Path(output_dir) if output_dir else paths.CACHE/"diagnostics"
        self.capture_enabled = capture
        self.smoke_failed = False
        self.smoke_report = None
        self.window = None
        self.pool = ThreadPoolExecutor(max_workers=Collect.WORKERS, thread_name_prefix="monitor")
        self.busy = set()
        self.closing = False
        self.data = {"system": {}, "slow": {}, "devices": {"devices": [], "errors": []}}
        self.errors = {}
        self.series = {key: deque(maxlen=Ui.SERIES_MAXLEN) for key in ("cpu", "gpu", "ram")}
        # 只讀取；首次啟動判斷必須在 initialize_preferences() 建立 settings.json 之前。
        loaded = load_settings(paths.CONFIG, paths.PREFERENCES)
        self.settings = dict(loaded.legacy)
        self.preferences = loaded.preferences  # 新版設定：後續階段才使用
        self.first_run = loaded.first_run
        self.timers, self.subscriptions = [], []
        self.cards = {}
        self.card_titles = {}
        self.graphs = []
        self.device_fingerprint = None
        self.event_timer = 0
        self.history = None
        self.last_recorded = None

    def do_activate(self):
        if self.window:
            self.window.show_all()
            self.window.present()
            return
        self.hold()
        if not self.smoke:
            initialize_preferences()
        self.collector = Collectors()
        self.collector.nvidia.start()
        # 模擬測試也照常讀寫：XDG 資料目錄在測試時指向暫存資料夾
        self.history = HistoryService()
        self.history.load()
        self.build_window()
        self.build_indicator()
        self.watch_devices()
        for kind, interval in (("system", Collect.SYSTEM_INTERVAL), ("slow", Collect.SLOW_INTERVAL),
                               ("devices", Collect.DEVICES_INTERVAL),
                               ("logitech", Solaar.INTERVAL)):
            self.submit(kind)
            self.timers.append(GLib.timeout_add_seconds(interval, self.tick, kind))
        self.timers.append(GLib.timeout_add_seconds(History.RECORD_INTERVAL_SECONDS,
                                                    self.record_history))
        if not self.background:
            self.window.show_all()
        if self.smoke:
            self.timers.append(GLib.timeout_add_seconds(Smoke.CHECK_DELAY_SECONDS, self.smoke_check))

    def watch_devices(self):
        self.udev = GUdev.Client.new(["usb", "power_supply"])
        self.udev.connect("uevent", lambda *_: self.device_event())
        if self.collector.bus is None:
            return
        for sender, interface, member in (
            ("org.bluez", "org.freedesktop.DBus.Properties", "PropertiesChanged"),
            ("org.bluez", "org.freedesktop.DBus.ObjectManager", "InterfacesAdded"),
            ("org.bluez", "org.freedesktop.DBus.ObjectManager", "InterfacesRemoved"),
            ("org.freedesktop.UPower", "org.freedesktop.UPower", "DeviceAdded"),
            ("org.freedesktop.UPower", "org.freedesktop.UPower", "DeviceRemoved"),
            ("org.freedesktop.UPower", "org.freedesktop.DBus.Properties", "PropertiesChanged"),
        ):
            subscription = self.collector.bus.signal_subscribe(sender, interface, member, None,
                None, Gio.DBusSignalFlags.NONE, lambda *_: self.device_event())
            self.subscriptions.append(subscription)

    def device_event(self):
        if not self.event_timer and not self.closing:
            self.event_timer = GLib.timeout_add(Ui.EVENT_DEBOUNCE_MS, self.device_event_ready)

    def device_event_ready(self):
        self.event_timer = 0
        self.submit("devices")
        return False

    def tick(self, kind):
        if self.closing:
            return False
        self.submit(kind)
        return True

    def submit(self, kind):
        if kind in self.busy or self.closing:
            return
        self.busy.add(kind)
        future = self.pool.submit(getattr(self.collector, kind))
        future.add_done_callback(lambda result: GLib.idle_add(self.received, kind, result))

    def received(self, kind, future):
        self.busy.discard(kind)
        if self.closing:
            return False
        try:
            self.data[kind] = future.result()
            self.errors.pop(kind, None)
            if kind == "logitech":
                self.submit("devices")
        except Exception as error:
            logging.error("Collector %s failed: %s", kind, type(error).__name__)
            self.errors[kind] = "資料暫時無法取得"
            self.data[kind] = {} if kind != "devices" else {"devices": [], "errors": ["設備資料暫時無法取得"]}
        if kind == "slow" and self.last_recorded is None:
            self.record_history()
        if kind == "system":
            now = time.monotonic()
            gpu = self.data["slow"].get("gpu")
            for key in self.series:
                value = (gpu.usage if gpu else None) if key == "gpu" else self.data["system"].get(key)
                self.series[key].append((now, value))
        self.render()
        return False

    def record_history(self):
        """每分鐘把最新的耗電快照記入歷史；第一次收到快照時也記一筆，同一快照不重複記。"""
        if self.closing:
            return False
        sample = desktop_history_sample(self.data["slow"].get("power"))
        if sample is not None and sample.t != self.last_recorded:
            self.last_recorded = sample.t
            self.history.add(sample)
        return True

    def build_window(self):
        settings = Gtk.Settings.get_default()
        theme = settings.get_property("gtk-theme-name") or ""
        self.dark = "dark" in theme.lower() or settings.get_property("gtk-application-prefer-dark-theme")
        css = Gtk.CssProvider()
        css.load_from_data(css_data())
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.window = Gtk.ApplicationWindow(application=self)
        self.window.set_title("系統與裝置監看")
        self.window.set_default_size(Ui.WINDOW_WIDTH, Ui.WINDOW_HEIGHT)
        self.window.set_icon_name("utilities-system-monitor")
        self.window.connect("delete-event", self.hide_window)
        header = Gtk.HeaderBar(title="系統與裝置", subtitle="本機即時監看")
        header.set_show_close_button(True)
        self.window.set_titlebar(header)
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        refresh.set_tooltip_text("更新所有資料")
        refresh.connect("clicked", lambda *_: [self.submit(k) for k in self.data])
        header.pack_end(refresh)
        root = box(spacing=0)
        self.window.add(root)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        switcher = Gtk.StackSwitcher(stack=self.stack)
        switcher.set_halign(Gtk.Align.CENTER)
        switcher.set_margin_top(12)
        switcher.set_margin_bottom(8)
        root.pack_start(switcher, False, False, 0)
        root.pack_start(self.stack, True, True, 0)
        self.footer = label("正在讀取本機資料…", "footnote")
        root.pack_end(self.footer, False, False, 0)
        self.build_overview()
        self.build_devices()
        self.build_settings()

    def page(self, key, title):
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        content = box(spacing=16)
        content.get_style_context().add_class("page")
        scroll.add(content)
        self.stack.add_titled(scroll, key, title)
        if key == "overview":
            self.overview_scroll = scroll
        return content

    def build_overview(self):
        page = self.page("overview", "系統總覽")
        intro = label("工作站即時狀態", "section")
        page.pack_start(intro, False, False, 0)
        grid = Gtk.Grid(column_spacing=12, row_spacing=12, column_homogeneous=True)
        names = (("cpu", "CPU"), ("temperature", "CPU 溫度"),
                 ("ram", "記憶體"), ("gpu", "GPU"),
                 ("network", "網路 · 實體介面合計"), ("ups", "APC UPS"))
        for i, (key, name) in enumerate(names):
            card = box()
            card.get_style_context().add_class("card")
            card.get_style_context().add_class("accent-" + key)
            title = label(name, "muted", True)
            self.card_titles[key] = title
            card.pack_start(title, False, False, 0)
            value = label("—", "value")
            sub = label("正在讀取…", "muted", True)
            card.pack_start(value, False, False, 0)
            if key in self.series:
                color = GRAPH_COLORS[key]
                graph = Graph(self.series[key], color)
                self.graphs.append(graph)
                card.pack_start(graph, True, True, 0)
            card.pack_start(sub, False, False, 0)
            self.cards[key] = (value, sub)
            grid.attach(card, i%3, i//3, 1, 1)
        page.pack_start(grid, False, False, 0)
        page.pack_start(label("曲線顯示最近 5 分鐘 · 數據由裝置與系統回報", "muted"), False, False, 0)
        page.pack_start(label("磁碟與儲存空間", "section"), False, False, 0)
        self.storage_box = box(spacing=10)
        page.pack_start(self.storage_box, False, False, 0)
        page.pack_start(label("網路介面", "section"), False, False, 0)
        self.network_box = box(spacing=8)
        page.pack_start(self.network_box, False, False, 0)
        page.pack_start(label("其他感測器與風扇", "section"), False, False, 0)
        self.sensor_label = label("讀取中…", "muted", True)
        page.pack_start(self.sensor_label, False, False, 0)

    def build_devices(self):
        page = self.page("devices", "連線設備")
        top = box(False)
        self.device_heading = label("目前連線設備", "section")
        top.pack_start(self.device_heading, True, True, 0)
        page.pack_start(top, False, False, 0)
        page.pack_start(label("電量依設備回報更新；接收器存在與無線端連線狀態分別顯示。", "muted", True), False, False, 0)
        self.device_notice = label("", "muted", True)
        page.pack_start(self.device_notice, False, False, 0)
        self.device_box = box(spacing=0)
        page.pack_start(self.device_box, False, False, 0)
        self.age_labels = []

    def build_settings(self):
        page = self.page("settings", "設定")
        page.pack_start(label("頂端列顯示", "section"), False, False, 0)
        for key, name in (("cpu","CPU 使用率"),("temperature","CPU 溫度"),("ram","記憶體使用率"),
                          ("gpu","GPU 使用率"),("network","下載／上傳速度")):
            check = Gtk.CheckButton(label=name)
            check.set_active(self.settings[key])
            check.connect("toggled", self.toggle_setting, key)
            page.pack_start(check, False, False, 0)
        page.pack_start(label("啟動與背景運作", "section"), False, False, 0)
        autostart = Gtk.CheckButton(label="登入 Ubuntu 桌面後自動啟動")
        autostart.set_active(paths.AUTOSTART.exists())
        autostart.connect("toggled", self.toggle_autostart)
        page.pack_start(autostart, False, False, 0)
        page.pack_start(label("關閉視窗後仍會在頂端列運作。需要停止時，使用頂端列選單中的「退出」。", "muted", True), False, False, 0)
        page.pack_start(label("資料更新", "section"), False, False, 0)
        page.pack_start(label("CPU／記憶體／網速／磁碟讀寫：2 秒\nGPU／溫度／UPS：5 秒\n磁碟容量：30 秒\n設備插拔及連線事件會即時觸發更新。", "muted", True), False, False, 0)

    def toggle_setting(self, widget, key):
        self.settings = {**self.settings, key: widget.get_active()}
        save_legacy(paths.CONFIG, self.settings)
        self.render_indicator()

    def toggle_autostart(self, widget):
        set_autostart(widget.get_active())

    def build_indicator(self):
        self.indicator = AyatanaAppIndicator3.Indicator.new("system-device-monitor",
            "utilities-system-monitor-symbolic", AyatanaAppIndicator3.IndicatorCategory.SYSTEM_SERVICES)
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        self.menu = Gtk.Menu()
        self.summary_items = {}
        for key, title in (("cpu","CPU"),("temperature","CPU 溫度"),("ram","記憶體"),("gpu","GPU"),
                           ("network","網速"),("ups","UPS")):
            item = Gtk.MenuItem(label=title + "：讀取中")
            item.connect("activate", self.open_page, "overview")
            self.menu.append(item)
            self.summary_items[key] = item
        self.battery_header = Gtk.MenuItem(label="設備電量")
        self.battery_header.connect("activate", self.open_page, "devices")
        self.menu.append(self.battery_header)
        self.battery_items, self.battery_rows = [], None
        self.menu.append(Gtk.SeparatorMenuItem())
        for title, page in (("開啟完整看板","overview"),("查看設備電量","devices"),("設定","settings")):
            item = Gtk.MenuItem(label=title)
            item.connect("activate", self.open_page, page)
            self.menu.append(item)
        item = Gtk.MenuItem(label="退出")
        item.connect("activate", lambda *_: self.stop())
        self.menu.append(item)
        self.menu.show_all()
        self.indicator.set_menu(self.menu)
        self.indicators = {}
        for index, key in enumerate(reversed(LEGACY_KEYS)):
            indicator = AyatanaAppIndicator3.Indicator.new("system-device-monitor-" + key,
                str(paths.ROOT/"icons"/(key + ".svg")), AyatanaAppIndicator3.IndicatorCategory.SYSTEM_SERVICES)
            indicator.set_ordering_index(100 + index)
            indicator.set_menu(self.menu)
            self.indicators[key] = indicator
        self.render_indicator()

    def open_page(self, widget, page):
        self.stack.set_visible_child_name(page)
        self.window.show_all()
        self.window.present()

    def hide_window(self, *_):
        self.window.hide()
        return True

    def render(self):
        fast, slow = self.data["system"], self.data["slow"]
        gpu, ups = slow.get("gpu"), slow.get("ups") or UpsReading()
        self.cards["cpu"][0].set_text(percent(fast.get("cpu")))
        info = fast.get("cpu_info", {})
        self.card_titles["cpu"].set_text(info.get("name", "CPU") + " · " + temperature(slow.get("cpu_temp")))
        self.cards["cpu"][1].set_text(
            f"{info.get('cores') or '—'} 核心 · {info.get('threads') or '—'} 執行緒 · 全機使用率")
        self.card_titles["gpu"].set_text((gpu.name if gpu else "GPU") + " · " +
                                         temperature(gpu.temp_c if gpu else None))
        self.cards["gpu"][0].set_text(percent(gpu.usage if gpu else None))
        self.cards["gpu"][1].set_text(
            f"顯示記憶體 {size(gpu.vram_used)} / {size(gpu.vram_total)}\n"
            f"{value_text(gpu.watts, ' W')} · 風扇 {percent(gpu.fan_pct)}"
            if gpu else slow.get("gpu_error") or "正在讀取…")
        self.cards["ram"][0].set_text(percent(fast.get("ram")))
        self.cards["ram"][1].set_text(f"{size(fast.get('ram_used'))} / {size(fast.get('ram_total'))}\n依系統可用記憶體計算")
        self.cards["network"][0].set_text("↓ " + size(fast.get("rx"), True))
        self.cards["network"][1].set_text("↑ " + size(fast.get("tx"), True) + " · 虛擬網路獨立列出")
        self.cards["temperature"][0].set_text(temperature(slow.get("cpu_temp")))
        self.cards["temperature"][1].set_text(slow.get("cpu_temp_label", "讀取中…"))
        mode = "市電供電" if ups.online else ("電池供電" if ups.on_battery else "通訊暫時無法取得")
        self.cards["ups"][0].set_text(percent(ups.charge_pct))
        self.cards["ups"][1].set_text(
            f"{mode} · 負載 {percent(ups.load_pct)}\n"
            f"預估續航 {value_text(ups.time_left_min, ' 分鐘')}")
        for graph in self.graphs:
            graph.queue_draw()
        self.render_storage()
        clear(self.network_box)
        for net in fast.get("networks", []):
            self.network_box.pack_start(label(
                f"{net['name']} · {net['kind']}" +
                (" · " + temperature(slow["network_temperatures"][net["name"]])
                 if net["name"] in slow.get("network_temperatures", {}) else "") +
                f"     ↓ {size(net['rx'], True)}     ↑ {size(net['tx'], True)}",
                "muted"), False, False, 0)
        self.network_box.show_all()
        sensors = [f"{s['chip']} {s['label']}  {temperature(s['value'])}" for s in slow.get("temperatures", [])]
        sensors += [f"{f['name']}  {f['value']:.0f} RPM" for f in slow.get("fans", [])]
        if not slow.get("fans"):
            sensors.append("CPU／機殼風扇轉速：無法取得")
        self.sensor_label.set_text("   ·   ".join(sensors))
        self.render_devices()
        self.render_indicator()
        errors = list(self.errors.values()) + self.data["devices"].get("errors", [])
        self.footer.set_text(" · ".join(errors) if errors else "更新於 " + time.strftime("%H:%M:%S") + "  ·  本機唯讀監看")

    def render_storage(self):
        clear(self.storage_box)
        rates = {d["name"]: d for d in self.data["system"].get("disks", [])}
        for disk in self.data["slow"].get("storage", []):
            container = box(spacing=6)
            container.get_style_context().add_class("card")
            container.get_style_context().add_class("accent-" + {"sda": "gpu", "sdb": "network"}.get(disk["name"], "ram"))
            rates_for_disk = rates.get(disk["name"], {})
            heading = f"{disk['name']} · {disk['model']} · {size(disk['size'])} · " + (
                temperature(disk["temperature"]) if disk.get("temperature") is not None else "溫度未回報")
            container.pack_start(label(heading, "device-name"), False, False, 0)
            container.pack_start(label(
                f"讀取 {size(rates_for_disk.get('read'), True)}    寫入 {size(rates_for_disk.get('write'), True)}",
                "muted"), False, False, 0)
            for volume in disk["volumes"]:
                used, total = volume["used"], volume["total"]
                container.pack_start(label(f"{volume['mount']}  ·  已用 {size(used)} / {size(total)}", "muted"), False, False, 0)
                bar = Gtk.ProgressBar()
                bar.set_fraction(max(0, min(1, used/total)) if total else 0)
                container.pack_start(bar, False, False, 0)
            self.storage_box.pack_start(container, False, False, 0)
        self.storage_box.show_all()

    def render_devices(self):
        data = self.data["devices"]
        devices = data.get("devices", [])
        fingerprint = json.dumps(devices, sort_keys=True, ensure_ascii=False)
        self.device_heading.set_text(f"目前連線設備 · {len(devices)}")
        self.device_notice.set_text(" · ".join(data.get("errors", [])))
        if fingerprint != self.device_fingerprint:
            self.device_fingerprint = fingerprint
            clear(self.device_box)
            self.age_labels = []
            if not devices:
                self.device_box.pack_start(label("目前沒有可顯示的連線設備。", "muted"), False, False, 0)
            for d in devices:
                row = box(False, 16)
                row.get_style_context().add_class("device-row")
                accent = ("ups" if d["kind"] == "UPS" else
                          "cpu" if "K73R" in d["name"] else
                          "ram" if "Mouse" in d["name"] else
                          "gpu" if "Razer" in d["name"] else
                          "network" if d["kind"] == "藍牙" else "temperature")
                row.get_style_context().add_class("accent-" + accent)
                icon = "battery-symbolic" if d["kind"] == "UPS" else (
                    "bluetooth-symbolic" if d["kind"] == "藍牙" else
                    "input-keyboard-symbolic" if "鍵盤" in d["name"] else
                    "input-mouse-symbolic" if "Mouse" in d["name"] else
                    "audio-headphones-symbolic" if "Razer" in d["name"] else "drive-removable-media-symbolic")
                row.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.DND), False, False, 0)
                description = box(spacing=4)
                description.pack_start(label(d["name"], "device-name", True), False, False, 0)
                description.pack_start(label(d["status"], "muted", True), False, False, 0)
                description.pack_start(label(d["detail"], "muted", True), False, False, 0)
                row.pack_start(description, True, True, 0)
                power = box(spacing=4)
                power.set_size_request(Ui.DEVICE_POWER_WIDTH, -1)
                power.pack_start(label(d["battery"], "device-power", True), False, False, 0)
                stamp_label = label(age(d["stamp"]) if d["stamp"] else d["kind"], "muted")
                power.pack_start(stamp_label, False, False, 0)
                if d["percent"] is not None:
                    progress = Gtk.ProgressBar()
                    progress.set_fraction(d["percent"]/100)
                    power.pack_start(progress, False, False, 0)
                self.age_labels.append((stamp_label, d["stamp"], d["kind"]))
                row.pack_end(power, False, False, 0)
                self.device_box.pack_start(row, False, False, 0)
            self.device_box.show_all()
        for item, stamp, kind in self.age_labels:
            item.set_text(age(stamp) if stamp else kind)

    def render_indicator(self):
        if not hasattr(self, "indicator"):
            return
        fast, slow = self.data["system"], self.data["slow"]
        gpu = slow.get("gpu")
        parts = {
            "cpu": "CPU " + percent(fast.get("cpu")),
            "gpu": "GPU " + percent(gpu.usage if gpu else None),
            "ram": "RAM " + percent(fast.get("ram")),
            "network": "↓" + size(fast.get("rx"), True) + " ↑" + size(fast.get("tx"), True),
            "temperature": temperature(slow.get("cpu_temp")),
        }
        self.indicator.set_label("", "")
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.PASSIVE if any(self.settings.values())
                                  else AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        for key, indicator in getattr(self, "indicators", {}).items():
            indicator.set_label(parts[key], "")
            indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE if self.settings[key]
                                 else AyatanaAppIndicator3.IndicatorStatus.PASSIVE)
        for key, text in parts.items():
            self.summary_items[key].set_label(text)
        ups = slow.get("ups") or UpsReading()
        self.summary_items["ups"].set_label("UPS " + percent(ups.charge_pct) +
            " · 續航 " + value_text(ups.time_left_min, " 分鐘"))
        self.sync_battery_items(battery_menu_rows(self.data["devices"]))

    def battery_item(self, text):
        return Gtk.MenuItem(label=text)

    def sync_battery_items(self, rows):
        """在標題下方每台設備一行；只有設備組成改變時才重建選單項目。"""
        if self.battery_rows is not None and [k for k, _ in rows] == [k for k, _ in self.battery_rows]:
            for item, (_, text) in zip(self.battery_items, rows):
                if item.get_label() != MENU_INDENT + text:
                    item.set_label(MENU_INDENT + text)
        else:
            for item in self.battery_items:
                self.menu.remove(item)
                item.destroy()
            start = self.menu.get_children().index(self.battery_header) + 1
            self.battery_items = []
            for offset, (key, text) in enumerate(rows):
                item = self.battery_item(MENU_INDENT + text)
                item.set_sensitive(key is not None)
                if key is not None:
                    item.connect("activate", self.open_page, "devices")
                item.show()
                self.menu.insert(item, start + offset)
                self.battery_items.append(item)
        self.battery_rows = rows

    def smoke_check(self):
        artifact = self.output_dir
        artifact.mkdir(parents=True, exist_ok=True)
        self.window.show_all()
        self.window.present()
        self.stack.set_visible_child_name("overview")
        self.overview_scroll.get_vadjustment().set_value(0)
        GLib.timeout_add(Smoke.CAPTURE_DELAY_MS, self.capture, "overview")
        GLib.timeout_add(Smoke.DEVICES_DELAY_MS, self.smoke_devices)
        return False

    def capture(self, name):
        if not self.capture_enabled:
            return False
        if name == "overview":
            self.window.set_focus(None)
            self.overview_scroll.get_vadjustment().set_value(0)
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
        window = self.window.get_window()
        if window:
            width, height = window.get_width(), window.get_height()
            pixbuf = Gdk.pixbuf_get_from_window(window, 0, 0, width, height)
            if pixbuf:
                pixbuf.savev(str(self.output_dir/f"{name}.png"), "png", [], [])
        return False

    def smoke_devices(self):
        self.stack.set_visible_child_name("devices")
        GLib.timeout_add(Smoke.CAPTURE_DELAY_MS, self.capture, "devices")
        GLib.timeout_add(Smoke.FINISH_DELAY_MS, self.smoke_finish)
        return False

    def smoke_finish(self):
        self.smoke_failed = bool(self.errors) or not bool(self.data["system"])
        report = dict(ok=not self.smoke_failed, collector_errors=sorted(self.errors),
                      system_ready=bool(self.data["system"]),
                      indicator_count=len(getattr(self, "indicators", {})))
        (self.output_dir/"smoke.json").write_text(json.dumps(report, indent=2) + "\n")
        self.smoke_report = report
        self.stop()
        return False

    def stop(self):
        if self.closing:
            return
        self.closing = True
        for timer in self.timers:
            if GLib.MainContext.default().find_source_by_id(timer):
                GLib.source_remove(timer)
        if self.event_timer:
            GLib.source_remove(self.event_timer)
        for subscription in self.subscriptions:
            self.collector.bus.signal_unsubscribe(subscription)
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.PASSIVE)
        for indicator in getattr(self, "indicators", {}).values():
            indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.PASSIVE)
        self.pool.shutdown(wait=False, cancel_futures=True)
        if getattr(self, "collector", None) is not None:
            self.collector.nvidia.stop()
        if self.history is not None:
            self.history.save()
        self.quit()


def run(options):
    """依命令列選項啟動看板，直到使用者退出或 smoke test 結束。"""
    app = Monitor(options.background, options.smoke_test, options.output_dir, options.capture)
    result = app.run([sys.argv[0]])
    return RunResult(1 if app.smoke_failed else result, app.smoke_report)
