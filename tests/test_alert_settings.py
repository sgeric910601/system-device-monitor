"""設定視窗的「通知」「周邊設備」與連線設備頁的鈴鐺：選項、寫入的鍵與即時更新（不建立 GTK 元件）。"""
from types import SimpleNamespace
import unittest

from sdm.core.peripheral_alerts import PeripheralReading, peripheral_key, storage_key
from sdm.core.preference_specs import PREFERENCE_SPECS
from sdm.core.settings import LEGACY_DEFAULTS, Preferences, PreferencesReadOnlyError
from sdm.data.ups import UpsReading
from sdm.services import peripheral_settings
from sdm.services.peripheral_settings import PeripheralSettings
from sdm.services.settings_store import STATUS, SettingsStore
from sdm.ui import app as ui_app
from sdm.ui import smoke
from sdm.ui.settings import notifications, peripherals, text
from sdm.ui.settings_window import SECTIONS, SettingsContext


SALT = "fedcba9876543210" * 2


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


def payload(*ids, errors=()):
    return {"devices": [dict(id=device_id) for device_id in ids], "errors": list(errors),
            "stamp": 1.0}


class PeripheralWrites(unittest.TestCase):
    """設定視窗的下拉選單與鈴鐺共用的寫入點。"""

    def make(self, preferences=None):
        self.store = store(preferences)
        return PeripheralSettings(self.store, make_salt=lambda: SALT)

    def thresholds(self):
        return dict(self.store.preferences.peripheral_thresholds)

    def test_first_write_creates_the_salt(self):
        settings = self.make()
        self.assertTrue(settings.choose(("bt:/dev_x",), 0))
        self.assertEqual(self.store.preferences.peripheral_key_salt, SALT)
        self.assertEqual(self.thresholds(), {storage_key(SALT, "bt:/dev_x"): 0})
        self.assertEqual(settings.threshold("bt:/dev_x"), 0)
        self.assertEqual(settings.threshold("other"), 20)

    def test_set_all_connected_devices(self):
        settings = self.make()
        settings.choose(("a", "b"), 50)
        self.assertEqual(self.thresholds(), {storage_key(SALT, "a"): 50,
                                             storage_key(SALT, "b"): 50})
        settings.choose(("a", "b"), 20)
        self.assertEqual(self.thresholds(), {})

    def test_old_keys_of_listed_devices_are_migrated(self):
        old = Preferences.defaults().with_changes(
            peripheral_thresholds={peripheral_key("m"): 0, peripheral_key("gone"): 50})
        settings = self.make(old)
        settings.observe(payload("m", "k"))
        settings.observe({"devices": [], "errors": ["設備資料暫時無法取得"]})   # 失敗：不算
        self.assertEqual(settings.threshold("m"), 0)
        settings.choose(("k",), 30)
        self.assertEqual(self.thresholds(), {storage_key(SALT, "m"): 0,
                                             storage_key(SALT, "k"): 30})

    def test_read_only_preferences_are_rejected_with_a_message(self):
        settings = self.make(Preferences.defaults(read_only=True))
        heard = []
        settings.subscribe(lambda: heard.append(settings.problem))
        self.assertFalse(settings.choose(("m",), 0))
        self.assertFalse(settings.choose(("m",), 0))
        self.assertEqual(self.thresholds(), {})
        self.assertIn("設定檔目前無法寫入", settings.problem)
        self.assertEqual(len(heard), 2)                  # 每次被拒絕都通知（選項要恢復）

    def test_message_clears_after_a_successful_write(self):
        settings = self.make()
        heard = []
        settings.subscribe(lambda: heard.append(settings.problem))
        settings.problem = peripheral_settings.REJECTED
        self.assertTrue(settings.choose(("m",), 0))
        self.assertEqual((settings.problem, heard), (None, [None]))
        self.assertTrue(settings.choose(("m",), 10))
        self.assertEqual(heard, [None])

    def test_set_all_shows_the_shared_value_or_nothing(self):
        settings = self.make()
        found = (reading("a"), reading("b"))
        self.assertEqual(peripherals.common_threshold(settings.threshold, found), 20)
        settings.choose(("a",), 0)
        self.assertIsNone(peripherals.common_threshold(settings.threshold, found))
        settings.choose(("b",), 0)
        self.assertEqual(peripherals.common_threshold(settings.threshold, found), 0)
        self.assertIsNone(peripherals.common_threshold(settings.threshold, ()))

    def test_composition_changes_only_with_devices(self):
        self.assertEqual(peripherals.composition((reading("a"),)),
                         peripherals.composition((reading("a"),)))
        self.assertNotEqual(peripherals.composition((reading("a"),)),
                            peripherals.composition((reading("a", "鍵盤"),)))


class Sections(unittest.TestCase):
    def test_smoke_test_opens_every_section_in_order(self):
        self.assertEqual(smoke.SETTINGS_SECTIONS, tuple(key for key, _, _ in SECTIONS))
        self.assertEqual([title for _, title, _ in SECTIONS],
                         ["一般", "頂端列", "監控模組", "顯示項目", "通知", "周邊設備", "夥伴",
                          "桌面寵物", "外觀", "管理員功能", "關於"])

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
    app.peripheral_settings = PeripheralSettings(app.store, make_salt=lambda: SALT)
    app.peripheral_settings.subscribe(app.peripheral_problem_changed)
    return app


class Wiring(unittest.TestCase):
    def test_bell_writes_through_the_store_and_redraws_the_bells(self):
        app = monitor()
        app.set_peripheral_threshold("power:/mouse", 30)
        self.assertEqual(dict(app.preferences.peripheral_thresholds),
                         {storage_key(SALT, "power:/mouse"): 30})
        self.assertIn("thresholds_changed", app.devices_page.calls)

    def test_rejected_bell_choice_is_logged_and_the_bells_restored(self):
        app = monitor(Preferences.defaults(read_only=True))
        with self.assertLogs("sdm.services.peripheral_settings", level="WARNING"):
            app.set_peripheral_threshold("power:/mouse", 30)
        self.assertEqual(app.devices_page.calls, ["thresholds_changed"])
        with self.assertLogs("sdm.services.peripheral_settings", level="WARNING"):
            app.set_peripheral_threshold("power:/mouse", 25)

    def test_rejected_write_is_shown_in_the_settings_and_on_the_bells(self):
        app = monitor(Preferences.defaults(read_only=True))
        with self.assertLogs("sdm.services.peripheral_settings", level="WARNING"):
            app.set_peripheral_threshold("power:/mouse", 30)
        self.assertIn("peripheral_problem_changed", app.settings_window.calls)
        self.assertIn("thresholds_changed", app.devices_page.calls)
        self.assertIn("無法儲存", app.peripheral_problem())

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
