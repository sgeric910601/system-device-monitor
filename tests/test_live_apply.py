"""設定即時套用：更新頻率與省電模式重新排程（不漏掉計時器）、模組開關立即停止或開始取樣並
隱藏或顯示頁面、頂端列與顯示項目的設定重畫。不建立真正的 GTK 視窗與 GLib 計時器。"""
from types import SimpleNamespace
import unittest

from sdm.constants import Solaar
from sdm.core.settings import LEGACY_DEFAULTS, Preferences
from sdm.data.ups import UpsReading
from sdm.services.power_service import PowerService
from sdm.services.scheduler import TierScheduler
from sdm.services.settings_store import SettingsStore
from sdm.ui import app as ui_app


def desktop(on_battery=False):
    ups = UpsReading(status=("ONBATT",) if on_battery else ("ONLINE",), online=not on_battery,
                     on_battery=on_battery, load_w=200.0)
    return PowerService(read_ups=lambda: ups, read_gpus=lambda: (), read_uptime=lambda: None,
                        clock=lambda: 1.0, wall=lambda: 1.0).sample()


class FakeTimers:
    def __init__(self): self.live, self.next_id = {}, 1
    def add(self, seconds, kind):
        self.live[self.next_id] = (kind, seconds)
        self.next_id += 1
        return self.next_id - 1
    def remove(self, source): del self.live[source]
    def intervals(self): return {kind: seconds for kind, seconds in self.live.values()}


class Recorder:
    def __init__(self): self.calls = []
    def __getattr__(self, name):
        return lambda *args: self.calls.append((name,) + args)


class FakeStream:
    def __init__(self): self.interval_ms = None
    def set_interval_ms(self, value): self.interval_ms = value


def monitor(power):
    app = ui_app.Monitor.__new__(ui_app.Monitor)
    app.closing, app.busy, app.errors, app.preview, app.smoke = False, set(), {}, None, False
    app.preferences, app.settings = Preferences.defaults(), dict(LEGACY_DEFAULTS)
    app.main, app.overview, app.power_page = Recorder(), Recorder(), Recorder()
    app.collector = SimpleNamespace(modules=frozenset(), nvidia=FakeStream(),
                                    power=SimpleNamespace(interval=5))
    app.timers_fake = FakeTimers()
    app.scheduler = TierScheduler(app.timers_fake.add, app.timers_fake.remove)
    app.submitted, app.rendered = [], []
    app.submit = app.submitted.append
    app.render_indicator = lambda: app.rendered.append(True)
    app.detail_timer, app.window_iconified = 0, False
    app.power = power
    app.current_power = lambda: app.power
    app.store = SettingsStore(LEGACY_DEFAULTS, Preferences.defaults(), None, None,
                              lambda ms, callback: None, lambda source: None, persist=False)
    app.store.subscribe(app.settings_changed)
    return app


class Startup(unittest.TestCase):
    def test_first_apply_schedules_and_samples_every_tier(self):
        app = monitor(desktop())
        app.apply_modules()
        self.assertEqual(app.timers_fake.intervals(),
                         {"system": 2, "slow": 5, "devices": 5, "logitech": Solaar.INTERVAL})
        self.assertEqual(sorted(app.submitted), ["devices", "logitech", "slow", "system"])
        self.assertEqual(app.collector.nvidia.interval_ms, 2000)
        self.assertEqual(app.collector.modules, frozenset({"cpu", "memory", "disk", "network"}))


class Interval(unittest.TestCase):
    def setUp(self):
        self.app = monitor(desktop())
        self.app.apply_modules()
        self.app.submitted.clear()

    def test_new_interval_reschedules_without_leaking(self):
        for seconds in (1, 5, 10, 2, 5):
            self.app.store.update(refresh_interval=seconds)
        self.assertEqual(len(self.app.timers_fake.live), 4)
        self.assertEqual(self.app.timers_fake.intervals()["system"], 5)
        self.assertEqual((self.app.collector.nvidia.interval_ms, self.app.collector.power.interval),
                         (5000, 5))
        self.assertEqual(self.app.submitted, [])

    def test_running_detail_timer_follows_the_new_pace(self):
        restarted = []
        self.app.detail_timer = 7
        self.app.detail_gate = SimpleNamespace(request="cpu")
        self.app.restart_detail_timer = restarted.append
        self.app.store.update(refresh_interval=10)
        self.assertEqual((restarted, self.app.cadence.detail), (["cpu"], 10))

    def test_power_saving_on_battery_only_follows_the_ups(self):
        self.app.store.update(refresh_interval=1, power_saving=True,
                              power_saving_on_battery_only=True)
        self.assertEqual(self.app.timers_fake.intervals()["system"], 1)
        self.assertIn(("set_motion", True, False, False), self.app.power_page.calls)
        self.app.power = desktop(on_battery=True)
        self.app.power_changed()
        self.assertEqual(self.app.timers_fake.intervals()["system"], 2)
        self.assertEqual(self.app.power_page.calls[-1], ("set_motion", True, False, True))


class ModulesLive(unittest.TestCase):
    def setUp(self):
        self.app = monitor(desktop())
        self.app.apply_modules()
        self.app.submitted.clear()

    def test_turning_a_module_off_hides_its_page_and_stops_its_sampling(self):
        self.app.store.update(monitor_disk=False)
        self.assertNotIn("disk", self.app.collector.modules)
        self.assertIn(("set_page_visible", "disk", False), self.app.main.calls)
        self.assertEqual(self.app.overview.calls[-1],
                         ("set_modules", frozenset({"cpu", "memory", "network"})))
        self.assertIn("system", self.app.timers_fake.intervals())
        self.assertTrue(self.app.rendered)

    def test_master_switch_removes_the_system_tier_and_brings_it_back(self):
        self.app.store.update(system_monitors=False)
        self.assertNotIn("system", self.app.timers_fake.intervals())
        for page in ("cpu", "memory", "disk", "network"):
            self.assertIn(("set_page_visible", page, False), self.app.main.calls)
        self.app.store.update(system_monitors=True)
        self.assertEqual(self.app.timers_fake.intervals()["system"], 2)
        self.assertEqual(self.app.submitted, ["system"])


class Redraw(unittest.TestCase):
    def test_tray_and_hidden_items(self):
        app = monitor(desktop())
        app.apply_modules()
        app.rendered.clear()
        app.store.update(tray_mode="merged")
        self.assertEqual(app.rendered, [True])
        app.store.update(hidden_flow_items=("gpu",))
        self.assertIn(("settings_changed",), app.power_page.calls)


if __name__ == "__main__":
    unittest.main()
