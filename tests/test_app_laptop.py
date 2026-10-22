"""看板的筆電與示範模式接線：最新快照、歷史紀錄、插拔事件、示範模式不寫檔。

不建立真正的 GTK 視窗：沒有顯示器時建立元件會讓程序中止。
"""
from concurrent.futures import Future
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sdm.constants import Preview
from sdm.core.settings import Preferences
from sdm.data.power_supply import BatteryReading
from sdm.data.ups import UpsReading
from sdm.services import preview
from sdm.services.battery_service import LaptopPower
from sdm.services.power_service import PowerService
from sdm.ui import app as ui_app


def laptop_service(online=False):
    battery = BatteryReading(name="BAT0", status="Discharging", capacity=82, power_w=15.0)
    return PowerService(read_ups=lambda: None, read_gpus=lambda: (), read_uptime=lambda: None,
                        wall=lambda: 1_800_000_000.0, read_batteries=lambda: (battery,),
                        read_upower=lambda: None)


def desktop_service():
    ups = UpsReading(status=("ONLINE",), online=True, charge_pct=100.0, load_w=288.0)
    return PowerService(read_ups=lambda: ups, read_gpus=lambda: (), read_uptime=lambda: None)


class FakeHistory:
    def __init__(self): self.added = []
    def add(self, sample): self.added.append(sample)


class FakeRefresh:
    def __init__(self): self.events, self.observed, self.stopped = [], [], 0
    def on_event(self, kind, scope): self.events.append((kind, scope))
    def observe(self, online): self.observed.append(online)
    def stop(self): self.stopped += 1


class FakeDevice:
    def __init__(self, subsystem, kind, scope=None):
        self.values = {"POWER_SUPPLY_TYPE": kind, "POWER_SUPPLY_SCOPE": scope}
        self.subsystem = subsystem
    def get_subsystem(self): return self.subsystem
    def get_property(self, name): return self.values.get(name)


def monitor():
    instance = ui_app.Monitor.__new__(ui_app.Monitor)
    instance.closing, instance.last_recorded, instance.power_page = False, None, None
    instance.history = FakeHistory()
    instance.power_refresh = FakeRefresh()
    instance.data = {"system": {}, "slow": {}, "devices": {"devices": [], "errors": []}}
    instance.events = []
    instance.device_event = lambda: instance.events.append("devices")
    return instance


class CurrentPower(unittest.TestCase):
    def test_newest_snapshot_wins(self):
        service = laptop_service()
        app = monitor()
        older, newer = service.sample(), service.sample()
        app.data["slow"] = {"power": newer}
        app.data[ui_app.POWER_REFRESH] = {"power": older}
        self.assertIs(app.current_power(), newer)
        app.data[ui_app.POWER_REFRESH] = {"power": service.sample()}
        self.assertIs(app.current_power(), app.data[ui_app.POWER_REFRESH]["power"])
        self.assertIsNone(monitor().current_power())

    def test_laptop_history_row(self):
        app = monitor()
        app.data["slow"] = {"power": laptop_service().sample()}
        app.record_history()
        row = app.history.added[0]
        self.assertEqual((row.pct, row.watts, row.plugged), (82, 15.0, False))


class RefreshKind(unittest.TestCase):
    def test_refresh_submits_a_collector_method(self):
        app = ui_app.Monitor.__new__(ui_app.Monitor)
        app.busy, app.closing, submitted = set(), False, []
        app.collector = preview.PreviewCollectors("laptop-battery")
        app.pool = type("Pool", (), {"submit": staticmethod(
            lambda function: submitted.append(function) or Future())})()
        refresh = ui_app.PowerRefresh(lambda: app.submit(ui_app.POWER_REFRESH), None, None)
        refresh.on_event("Battery", "")
        self.assertEqual(len(submitted), 1)
        result = submitted[0]()
        self.assertIsInstance(result["power"], LaptopPower)


class LostRefresh(unittest.TestCase):
    def test_refresh_requested_while_one_runs_is_sent_after_it(self):
        app = monitor()
        app.busy, app.errors, app.render, submitted = {ui_app.POWER_REFRESH}, {}, lambda: None, []
        app.submit = submitted.append
        app.request_power_refresh()
        app.request_power_refresh()
        self.assertEqual(submitted, [])
        future = Future()
        future.set_result({"power": laptop_service().sample(), "stamp": 1.0})
        app.received(ui_app.POWER_REFRESH, future)
        self.assertEqual(submitted, [ui_app.POWER_REFRESH])
        app.received(ui_app.POWER_REFRESH, future)
        self.assertEqual(submitted, [ui_app.POWER_REFRESH])

    def test_idle_refresh_is_sent_at_once(self):
        app = monitor()
        app.busy, submitted = set(), []
        app.submit = submitted.append
        app.request_power_refresh()
        self.assertEqual(submitted, [ui_app.POWER_REFRESH])


class Events(unittest.TestCase):
    def test_power_supply_events_reach_the_refresh_only_in_laptop_mode(self):
        app = monitor()
        app.data["slow"] = {"power": laptop_service().sample()}
        app.on_uevent(None, "change", FakeDevice("power_supply", "Mains"))
        app.on_uevent(None, "change", FakeDevice("usb", None))
        self.assertEqual(app.power_refresh.events, [("Mains", None)])
        self.assertEqual(app.events, ["devices", "devices"])
        desktop = monitor()
        desktop.data["slow"] = {"power": desktop_service().sample()}
        desktop.on_uevent(None, "change", FakeDevice("power_supply", "Mains"))
        self.assertEqual(desktop.power_refresh.events, [])

    def test_received_laptop_snapshots_are_observed(self):
        app = monitor()
        app.busy, app.errors, app.render = {ui_app.POWER_REFRESH}, {}, lambda: None
        future = Future()
        future.set_result({"power": laptop_service().sample(), "stamp": 1.0})
        app.received(ui_app.POWER_REFRESH, future)
        self.assertEqual(app.power_refresh.observed, [False])
        self.assertIsInstance(app.data[ui_app.POWER_REFRESH]["power"], LaptopPower)

    def test_stop_cancels_pending_refreshes_and_tolerates_missing_indicators(self):
        app = monitor()
        app.timers, app.event_timer, app.subscriptions = [], 0, []
        app.pool = type("Pool", (), {"shutdown": staticmethod(lambda **options: None)})()
        app.history = type("History", (), {"save": staticmethod(lambda **options: None)})()
        quits = []
        app.quit = lambda: quits.append(True)
        with self.assertNoLogs(level="WARNING"):
            app.stop()
        self.assertEqual((app.power_refresh.stopped, quits), (1, [True]))


class PreviewMode(unittest.TestCase):
    def test_separate_non_unique_application_without_reading_settings(self):
        with patch.object(ui_app, "load_settings", side_effect=AssertionError("settings read")):
            app = ui_app.Monitor(preview="laptop-battery")
        self.addCleanup(app.pool.shutdown)
        self.assertEqual(app.get_application_id(), ui_app.APP_ID + Preview.APP_ID_SUFFIX)
        self.assertTrue(app.get_flags() & ui_app.Gio.ApplicationFlags.NON_UNIQUE)
        self.assertIsNone(app.output_dir)
        self.assertFalse(app.first_run)

    def test_normal_application_id_is_unchanged(self):
        app = ui_app.Monitor()
        self.addCleanup(app.pool.shutdown)
        self.assertEqual(app.get_application_id(), ui_app.APP_ID)
        self.assertFalse(app.get_flags() & ui_app.Gio.ApplicationFlags.NON_UNIQUE)

    def test_preview_history_is_preloaded_in_memory(self):
        history = ui_app.create_history(smoke=False, preview="laptop-charging")
        self.assertIsNone(history.path)
        self.assertGreater(len(history.samples), 1000)

    def test_settings_toggles_do_not_write(self):
        app = ui_app.Monitor.__new__(ui_app.Monitor)
        app.preview = "desktop"
        app.settings = {key: True for key in ui_app.LEGACY_KEYS}
        app.preferences = Preferences.defaults()
        app.render_indicator = lambda: None
        check = type("Check", (), {"get_active": staticmethod(lambda: False)})()
        with patch.object(ui_app, "save_legacy", side_effect=AssertionError("write")), \
                patch.object(ui_app, "save_preferences", side_effect=AssertionError("write")), \
                patch.object(ui_app, "set_autostart", side_effect=AssertionError("write")):
            app.toggle_setting(check, "cpu")
            app.toggle_power_tray(check)
            app.toggle_autostart(check)
        self.assertFalse(app.settings["cpu"])
        self.assertFalse(app.preferences.tray_power)

    def test_closing_the_window_quits_because_there_is_no_tray(self):
        hidden, stopped = [], []
        window = type("Window", (), {"hide": staticmethod(lambda: hidden.append(True))})()
        for scenario, expected in ((None, ([True], [])), ("laptop-limit", ([], [True]))):
            hidden.clear(), stopped.clear()
            app = ui_app.Monitor.__new__(ui_app.Monitor)
            app.preview, app.window = scenario, window
            app.stop = lambda: stopped.append(True)
            self.assertTrue(app.hide_window())
            self.assertEqual((hidden, stopped), expected)

    def test_smoke_report_without_an_output_directory_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            app = ui_app.Monitor.__new__(ui_app.Monitor)
            app.output_dir, app.capture_enabled = None, False
            app.errors, app.data = {}, {"system": {"cpu": 1}}
            app.smoke_pages = ["power"]
            app.stop = lambda: None
            with patch.object(ui_app.paths, "CACHE", Path(temp)):
                app.smoke_finish()
            self.assertEqual(list(Path(temp).iterdir()), [])
        self.assertEqual((app.smoke_report["ok"], app.smoke_report["indicator_count"]), (True, 0))


if __name__ == "__main__":
    unittest.main()
