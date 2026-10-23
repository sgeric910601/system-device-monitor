"""設定視窗的「通知」「周邊設備」與連線設備頁的鈴鐺：選項、寫入的鍵與即時更新（不建立 GTK 元件）。"""
from types import SimpleNamespace
import unittest

from sdm.core.peripheral_alerts import PeripheralReading, peripheral_key
from sdm.core.preference_specs import PREFERENCE_SPECS
from sdm.core.settings import LEGACY_DEFAULTS, Preferences, PreferencesReadOnlyError
from sdm.data.ups import UpsReading
from sdm.services.settings_store import STATUS, SettingsStore
from sdm.ui import app as ui_app
from sdm.ui import smoke
from sdm.ui.settings import notifications, peripherals, text
from sdm.ui.settings_window import SECTIONS, SettingsContext


def store(preferences=None):
    return SettingsStore(LEGACY_DEFAULTS, preferences or Preferences.defaults(), None, None,
                         lambda ms, callback: None, lambda source: None, persist=False)


def reading(device_id, name="滑鼠"):
    return PeripheralReading(device_id=device_id, key=peripheral_key(device_id), name=name,
                             icon="input-mouse-symbolic", percent=50)


class Options(unittest.TestCase):
    def assert_valid(self, key, options):
        for value, _ in options:
            PREFERENCE_SPECS[key].check(value)

    def test_battery_threshold_choices(self):
        self.assertEqual([value for value, _ in text.LOW_THRESHOLDS], list(range(5, 55, 5)))
        self.assertEqual([title for _, title in text.FULL_THRESHOLDS],
                         ["80%", "85%", "90%", "95%", "100%"])
        self.assert_valid("low_threshold", text.LOW_THRESHOLDS)
        self.assert_valid("full_threshold", text.FULL_THRESHOLDS)

    def test_ups_choices_keep_a_custom_value(self):
        self.assertEqual([value for value, _ in text.ups_thresholds(50)], list(range(10, 95, 5)))
        custom = [value for value, _ in text.ups_thresholds(13)]
        self.assertEqual(custom[:3], [10, 13, 15])
        self.assert_valid("ups_low_threshold", text.ups_thresholds(13))


class NotificationKeys(unittest.TestCase):
    def test_every_control_writes_its_own_key(self):
        rows = notifications.BATTERY_ALERTS + (notifications.UPS_ALERT,)
        for switch_key, _, threshold_key, _, options, _ in rows:
            for key, value in ((switch_key, False), (threshold_key, options[0][0])):
                settings = store()
                self.assertEqual(settings.update(**{key: value}), frozenset({key}))
                self.assertEqual(settings.value(key), value)
        self.assertEqual([row[0] for row in rows],
                         ["low_alert_enabled", "full_alert_enabled", "ups_alerts_enabled"])
        self.assertEqual([row[2] for row in rows],
                         ["low_threshold", "full_threshold", "ups_low_threshold"])

    def test_read_only_preferences_reject_alert_changes(self):
        settings = store(Preferences.defaults(read_only=True))
        with self.assertRaises(PreferencesReadOnlyError):
            settings.update(low_alert_enabled=False)


class PeripheralKeys(unittest.TestCase):
    def test_one_device(self):
        settings = store()
        settings.update(**peripherals.one_change(settings.value("peripheral_thresholds"),
                                                 "bt:/dev_x", 0))
        self.assertEqual(dict(settings.preferences.peripheral_thresholds),
                         {peripheral_key("bt:/dev_x"): 0})

    def test_set_all_connected_devices(self):
        settings = store()
        found = (reading("a"), reading("b"))
        settings.update(**peripherals.all_change(settings.value("peripheral_thresholds"),
                                                 found, 50))
        self.assertEqual(dict(settings.preferences.peripheral_thresholds),
                         {peripheral_key("a"): 50, peripheral_key("b"): 50})
        settings.update(**peripherals.all_change(settings.value("peripheral_thresholds"),
                                                 found, 20))
        self.assertEqual(dict(settings.preferences.peripheral_thresholds), {})

    def test_set_all_shows_the_shared_value_or_nothing(self):
        found = (reading("a"), reading("b"))
        self.assertEqual(peripherals.common_threshold({}, found), 20)
        self.assertIsNone(peripherals.common_threshold({peripheral_key("a"): 0}, found))
        self.assertEqual(peripherals.common_threshold(
            {peripheral_key("a"): 0, peripheral_key("b"): 0}, found), 0)
        self.assertIsNone(peripherals.common_threshold({}, ()))

    def test_composition_changes_only_with_devices(self):
        self.assertEqual(peripherals.composition((reading("a"),)),
                         peripherals.composition((reading("a"),)))
        self.assertNotEqual(peripherals.composition((reading("a"),)),
                            peripherals.composition((reading("a", "鍵盤"),)))


class Sections(unittest.TestCase):
    def test_smoke_test_opens_every_section_in_order(self):
        self.assertEqual(smoke.SETTINGS_SECTIONS, tuple(key for key, _, _ in SECTIONS))
        self.assertEqual([title for _, title, _ in SECTIONS],
                         ["一般", "頂端列", "監控模組", "顯示項目", "通知", "周邊設備", "關於"])

    def test_context_defaults(self):
        context = SettingsContext(laptop=False, root=None, version="0", external_power=bool)
        self.assertEqual((context.ups_present(), context.peripherals(), context.notice()),
                         (False, (), ""))
        self.assertFalse(context.notifications_enabled)


class Recorder:
    def __init__(self): self.calls = []
    def __getattr__(self, name):
        return lambda *args: self.calls.append(name)


def monitor(preferences=None):
    app = ui_app.Monitor.__new__(ui_app.Monitor)
    app.store = store(preferences)
    app.preferences, app.settings = app.store.preferences, dict(LEGACY_DEFAULTS)
    app.scheduler, app.power_page = None, None
    app.devices_page, app.settings_window = Recorder(), Recorder()
    app.render_indicator = lambda: None
    app.store.subscribe(app.settings_changed)
    return app


class Wiring(unittest.TestCase):
    def test_bell_writes_through_the_store_and_redraws_the_bells(self):
        app = monitor()
        app.set_peripheral_threshold("power:/mouse", 30)
        self.assertEqual(dict(app.preferences.peripheral_thresholds),
                         {peripheral_key("power:/mouse"): 30})
        self.assertIn("thresholds_changed", app.devices_page.calls)

    def test_rejected_bell_choice_is_logged_and_the_bells_restored(self):
        app = monitor(Preferences.defaults(read_only=True))
        with self.assertLogs("sdm.ui.settings_wiring", level="WARNING"):
            app.set_peripheral_threshold("power:/mouse", 30)
        self.assertEqual(app.devices_page.calls, ["thresholds_changed"])
        with self.assertLogs("sdm.ui.settings_wiring", level="WARNING"):
            app.set_peripheral_threshold("power:/mouse", 25)

    def test_status_change_redraws_the_bells(self):
        app = monitor()
        app.settings_changed(frozenset({STATUS}), app.store)
        self.assertEqual(app.devices_page.calls, ["thresholds_changed"])

    def test_settings_window_hears_about_devices_ups_and_the_notification_service(self):
        app = monitor()
        app.alerts = None
        app.data = {"devices": {"devices": [], "errors": [], "stamp": 1.0}}
        ups = SimpleNamespace(sequence=1, ups=UpsReading(status=("ONLINE",), online=True))
        app.current_power = lambda: ups
        app.device_alerts()
        app.power_alerts()
        app.power_alerts()
        app.current_power = lambda: SimpleNamespace(sequence=2, ups=None)
        app.power_alerts()
        app.notice_changed()
        self.assertEqual(app.settings_window.calls,
                         ["devices_changed", "power_changed", "power_changed", "notice_changed"])
        self.assertFalse(app.ups_present())


if __name__ == "__main__":
    unittest.main()
