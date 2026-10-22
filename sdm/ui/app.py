"""GTK 看板應用程式：所有視圖共用同一份資料快照。"""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path
import signal
import sys
import time
from typing import NamedTuple, Optional

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GUdev", "1.0")
from gi.repository import Gtk, Gio, GLib, GUdev

from sdm import paths
from sdm.constants import Collect, History, Preview, Smoke, Ui
from sdm.core.schedule import cadence_for
from sdm.core.settings import load_settings
from sdm.data.collectors import Collectors
from sdm.services import preview as demo
from sdm.services.autostart import set_autostart, initialize_preferences
from sdm.services.settings_store import SettingsStore
from sdm.services.history_service import HistoryService, read_boot_time
from sdm.services.power_refresh import PowerRefresh
from sdm.services.ui_state import PageMemory
from sdm.ui.app_names import AppNames
from sdm.ui.main_window import PAGES, MainWindow
from sdm.ui.monitor_wiring import MonitorMixin
from sdm.ui.pages.devices import DevicesPage
from sdm.ui.pages.overview import OverviewPage
from sdm.ui.pages.power import PowerPage
from sdm.ui.power_wiring import POWER_REFRESH, PowerMixin
from sdm.ui.schedule_wiring import ScheduleMixin
from sdm.ui.settings_wiring import SettingsMixin
from sdm.ui.tray_wiring import TrayMixin
from sdm.ui.smoke import SmokeMixin

PAGE_TITLES = dict(PAGES)

APP_ID = "tw.local.SystemDeviceMonitor"


def create_history(smoke, preview=None):
    """模擬測試與示範模式只用記憶體中的歷史，絕不讀寫使用者的 history.json。"""
    if preview:
        return HistoryService.preloaded(demo.demo_history(preview, time.time()))
    return HistoryService.in_memory() if smoke else HistoryService()


class RunResult(NamedTuple):
    """執行結果：程序結束碼，以及 smoke test 的摘要（一般執行為 None）。"""
    code: int
    report: Optional[dict]


class Monitor(PowerMixin, SettingsMixin, ScheduleMixin, TrayMixin, SmokeMixin, MonitorMixin,
              Gtk.Application):
    preview = None          # 示範情境（--preview）；None 為一般執行
    power_refresh = None    # 插拔電源後的重新讀取排程
    boot_time = None
    window = None
    main = None             # 主視窗外殼（MainWindow）
    page_memory = None      # 最後顯示的頁面；示範模式與模擬測試為 None

    def __init__(self, background=False, smoke=False, output_dir=None, capture=False,
                 preview=None):
        # 示範模式另用一個不註冊為唯一實例的代號，絕不把請求交給正在執行的看板
        super().__init__(application_id=APP_ID + Preview.APP_ID_SUFFIX if preview else APP_ID,
                         flags=Gio.ApplicationFlags.NON_UNIQUE if preview
                         else Gio.ApplicationFlags.FLAGS_NONE)
        self.background, self.smoke, self.preview = background, smoke, preview
        default_dir = None if preview else paths.CACHE/"diagnostics"
        self.output_dir = Path(output_dir) if output_dir else default_dir
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
        # 示範模式不讀設定檔，一律使用預設值。
        loaded = demo.preview_settings() if preview else load_settings(paths.CONFIG,
                                                                      paths.PREFERENCES)
        self.settings = dict(loaded.legacy)
        self.preferences = loaded.preferences
        self.first_run = loaded.first_run
        # 所有設定變更的唯一入口；示範模式與模擬測試不寫入任何檔案
        self.store = SettingsStore(loaded.legacy, loaded.preferences, paths.CONFIG,
                                   paths.PREFERENCES, GLib.timeout_add, self.remove_source,
                                   persist=not (smoke or preview), write_autostart=set_autostart)
        self.store.subscribe(self.settings_changed)
        self.timers, self.subscriptions = [], []
        self.graphs = []
        self.event_timer = 0
        # 示範模式與模擬測試不讀寫視窗狀態
        self.page_memory = None if (smoke or preview) else PageMemory(paths.STATE_FILE,
                                                                       PAGE_TITLES)
        self.history = None
        self.last_recorded = None
        self.power_page = None
        self.scrolls = {}
        self.signal_sources = []   # 不放進 timers：結束流程不移除正在處理中的訊號來源
        self.power_refresh = PowerRefresh(self.request_power_refresh, self.schedule,
                                          self.remove_source)

    def do_activate(self):
        if self.window:
            self.window.show_all()
            self.window.present()
            return
        self.hold()
        if not (self.smoke or self.preview):
            initialize_preferences()
            self.store.autostart = paths.AUTOSTART.exists()   # 首次啟動時剛建立
        self.collector = demo.PreviewCollectors(self.preview) if self.preview else Collectors()
        # 先換成目前設定的間隔再啟動 nvidia-smi，啟動後不必馬上重新啟動
        self.collector.nvidia.set_interval_ms(cadence_for(self.preferences, None).nvidia_ms)
        self.collector.nvidia.start()
        self.history = create_history(self.smoke, self.preview)
        self.history.load()
        self.boot_time = read_boot_time()
        self.build_window()
        if not self.preview:     # 示範模式沒有頂端列，也不監看裝置事件
            self.build_tray()
            self.watch_devices()
        self.start_profiles()
        GLib.idle_add(self.ask_first_run)
        self.create_scheduler()
        self.apply_modules()        # 頁面、取樣層與頂端列；各層立即取樣一次
        self.timers.append(GLib.timeout_add_seconds(History.RECORD_INTERVAL_SECONDS,
                                                    self.record_history))
        self.install_signal_handlers()
        self.restore_page()
        if not self.background:
            self.window.show_all()
        if self.smoke:
            delay = Preview.CHECK_DELAY_SECONDS if self.preview else Smoke.CHECK_DELAY_SECONDS
            self.timers.append(GLib.timeout_add_seconds(delay, self.smoke_check))

    def install_signal_handlers(self):
        """登出或 kill 送來 SIGTERM／SIGINT 時照常結束：存歷史、停止 nvidia-smi。"""
        for number in (signal.SIGTERM, signal.SIGINT):
            self.signal_sources.append(GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, number,
                                                            self.on_signal))

    def on_signal(self, *_):
        self.stop()
        return GLib.SOURCE_REMOVE

    def watch_devices(self):
        self.udev = GUdev.Client.new(["usb", "power_supply"])
        self.udev.connect("uevent", self.on_uevent)
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
        if kind in ("slow", POWER_REFRESH):
            self.power_received(kind)
        if kind == "system":
            now = time.monotonic()
            gpu = self.data["slow"].get("gpu")
            for key in self.series:
                value = (gpu.usage if gpu else None) if key == "gpu" else self.data["system"].get(key)
                self.series[key].append((now, value))
        self.render()
        return False

    def build_window(self):
        self.main = MainWindow(self, self.refresh_all, self.open_settings, self.hide_window)
        self.window, self.stack = self.main.window, self.main.stack
        self.scrolls, self.footer = self.main.scrolls, self.main.footer
        self.app_names = AppNames()
        self.build_power()
        overview = OverviewPage(self.series, self.clickable)
        self.overview = overview
        self.graphs = overview.graphs
        self.page("overview", PAGE_TITLES["overview"]).pack_start(overview, False, False, 0)
        self.build_monitor_pages()
        self.devices_page = DevicesPage()
        self.page("devices", PAGE_TITLES["devices"]).pack_start(self.devices_page, False, False, 0)
        self.watch_visibility()

    def page(self, key, title):
        return self.main.page(key, title)

    def refresh_all(self):
        for kind in self.data:
            self.submit(kind)
        self.submit_detail()

    def build_power(self):
        """「電源」頁放在第一頁。"""
        content = self.page("power", PAGE_TITLES["power"])
        self.power_page = PowerPage(self.history_points, recap_for=self.today_recap,
                                    unplugged_for=self.history_unplugged,
                                    hidden_fields=lambda: self.preferences.hidden_detail_fields,
                                    hidden_flow=lambda: self.preferences.hidden_flow_items,
                                    names=self.app_names, switch_profile=self.switch_profile)
        content.pack_start(self.power_page, False, False, 0)

    def open_page(self, widget, page):
        self.window.show_all()
        if page is not None:
            self.main.show_page(page)
        self.window.present()

    def hide_window(self, *_):
        """關閉視窗只是隱藏（仍在頂端列運作）並記住目前的頁面；示範模式沒有頂端列，關閉就結束。"""
        if self.preview:
            self.stop()
        else:
            self.remember_page()
            self.window.hide()
        return True

    def remember_page(self):
        if self.page_memory is not None and self.window is not None:
            self.page_memory.save(self.main.current_page())

    def restore_page(self):
        """上次關閉視窗時的頁面；示範模式與模擬測試一律從「電源」開始。"""
        page = self.page_memory.load() if self.page_memory is not None else None
        if page is not None:
            self.main.show_page(page)

    def render(self):
        fast, slow = self.data["system"], self.data["slow"]
        self.power_page.update(self.current_power())
        self.overview.render(fast, slow)
        self.render_monitor_pages(fast, slow)
        self.devices_page.render(self.data["devices"])
        self.render_indicator()
        errors = list(self.errors.values()) + self.data["devices"].get("errors", [])
        self.footer.set_text(" · ".join(errors) if errors else
                             "更新於 " + time.strftime("%H:%M:%S") + "  ·  本機唯讀監看")

    def stop(self):
        """先存歷史，再停止其餘部分；任何一步失敗都只記錄，最後一定呼叫 quit()。

        介面最多被 nvidia-smi 的停止時限（Gpu.STOP_TIMEOUT）卡住。
        """
        if self.closing:
            return
        self.closing = True
        try:
            for name, action in (("history", self.save_history), ("settings", self.flush_settings),
                                 ("page", self.remember_page),
                                 ("sources", self.remove_sources),
                                 ("subscriptions", self.unsubscribe_all),
                                 ("indicators", self.hide_indicators),
                                 ("profiles", self.stop_profiles),
                                 ("workers", lambda: self.pool.shutdown(wait=False,
                                                                       cancel_futures=True)),
                                 ("stream", self.stop_stream)):
                try:
                    action()
                except Exception as error:
                    logging.warning("Stop step %s failed: %s", name, type(error).__name__)
        finally:
            self.quit()

    def save_history(self):
        if self.history is not None:
            self.history.save(force=True)

    def remove_sources(self):
        for timer in self.timers:
            if GLib.MainContext.default().find_source_by_id(timer):
                GLib.source_remove(timer)
        if self.event_timer:
            GLib.source_remove(self.event_timer)
            self.event_timer = 0
        self.stop_detail()
        self.stop_scheduler()
        if self.power_refresh is not None:
            self.power_refresh.stop()

    def unsubscribe_all(self):
        for subscription in self.subscriptions:
            try:
                self.collector.bus.signal_unsubscribe(subscription)
            except Exception as error:
                logging.warning("Cannot unsubscribe: %s", type(error).__name__)

    def stop_stream(self):
        if getattr(self, "collector", None) is not None:
            self.collector.nvidia.stop()


def run(options):
    """依命令列選項啟動看板，直到使用者退出或 smoke test 結束。"""
    app = Monitor(options.background, options.smoke_test, options.output_dir, options.capture,
                  getattr(options, "preview", None))
    result = app.run([sys.argv[0]])
    return RunResult(1 if app.smoke_failed else result, app.smoke_report)
