"""周邊設備低電量通知（純邏輯）：參考專案 PeripheralTests 的數值、只有電量等級的設備、
斷線的設備、啟動時只記錄，以及設定視窗與鈴鐺按鈕寫入的門檻。"""
import hashlib
import hmac
import unittest

from sdm.core.peripheral_alerts import (BELL_CHOICES, DEFAULT_THRESHOLD, THRESHOLD_CHOICES,
                                        PeripheralAlertState, PeripheralReading, bell_icon,
                                        bell_tooltip, evaluate_peripherals, fitted, migrated,
                                        new_salt,
                                        peripheral_key, peripheral_readings, present_keys,
                                        storage_key, threshold_changes, threshold_for, with_all)
from sdm.core.preference_specs import MAX_ITEMS, PERIPHERAL_LEVELS
from sdm.core.settings import Preferences

MOUSE, PODS = peripheral_key("mouse"), peripheral_key("pods")
SALT = "0123456789abcdef" * 2
FROM_READINGS = object()       # 連線中的設備＝這次有讀值的設備


def reading(device_id, percent=None, level_low=None, name=None):
    return PeripheralReading(device_id=device_id, key=peripheral_key(device_id),
                             name=name or device_id, icon="input-mouse-symbolic",
                             percent=percent, level_low=level_low)


class Run:
    def __init__(self, thresholds):
        self.thresholds, self.state = thresholds, PeripheralAlertState()

    def __call__(self, readings, present=FROM_READINGS, record_only=False):
        if present is FROM_READINGS:
            present = frozenset(r.key for r in readings)
        self.state, alerts = evaluate_peripherals(self.state, readings, self.thresholds,
                                                  present, record_only)
        return [alert.device for alert in alerts]


class ReferenceNumbers(unittest.TestCase):
    """參考專案 testPerDeviceAlerts（耳機只有一個電量：Linux 藍牙不分左右耳）。"""

    def test_per_device_alerts(self):
        run = Run({MOUSE: 20, PODS: 10})
        pods = reading("pods", 9)
        self.assertEqual(run([reading("mouse", 25), pods]), [PODS])
        self.assertEqual(run([reading("mouse", 25), pods]), [])
        self.assertEqual(run([reading("mouse", 20), pods]), [MOUSE])
        self.assertEqual(run([reading("mouse", 15)]), [])
        self.assertEqual(run([reading("mouse", 22)]), [])     # 門檻＋2 還不算充過電
        self.assertEqual(run([reading("mouse", 23)]), [])
        self.assertEqual(run([reading("mouse", 19)]), [MOUSE])
        silent = Run({MOUSE: 0})
        self.assertEqual(silent([reading("mouse", 19)]), [])

    def test_default_threshold_is_twenty(self):
        self.assertEqual(DEFAULT_THRESHOLD, 20)
        run = Run({})
        self.assertEqual(run([reading("mouse", 21)]), [])
        self.assertEqual(run([reading("mouse", 20)]), [MOUSE])

    def test_threshold_zero_rearms(self):
        thresholds = {MOUSE: 20}
        run = Run(thresholds)
        self.assertEqual(run([reading("mouse", 10)]), [MOUSE])
        thresholds[MOUSE] = 0
        self.assertEqual(run([reading("mouse", 10)]), [])
        thresholds[MOUSE] = 20
        self.assertEqual(run([reading("mouse", 10)]), [MOUSE])


class LevelOnly(unittest.TestCase):
    def test_low_or_critical_level_counts_as_low_and_normal_rearms(self):
        run = Run({})
        self.assertEqual(run([reading("mouse", level_low=False)]), [])
        self.assertEqual(run([reading("mouse", level_low=True)]), [MOUSE])
        self.assertEqual(run([reading("mouse", level_low=True)]), [])
        self.assertEqual(run([reading("mouse", level_low=False)]), [])
        self.assertEqual(run([reading("mouse", level_low=True)]), [MOUSE])

    def test_off_threshold_is_silent(self):
        run = Run({MOUSE: 0})
        self.assertEqual(run([reading("mouse", level_low=True)]), [])
        run.thresholds = {MOUSE: 10}
        self.assertEqual(run([reading("mouse", level_low=True)]), [MOUSE])

    def test_message_for_a_level_only_device(self):
        _, alerts = evaluate_peripherals(PeripheralAlertState(),
                                         [reading("mouse", level_low=True, name="無線滑鼠")],
                                         {}, frozenset({MOUSE}))
        self.assertEqual((alerts[0].title, alerts[0].body),
                         ("無線滑鼠 電量偏低", "設備回報的電量等級為「低電量」，建議充電。"))
        self.assertEqual((alerts[0].page, alerts[0].kind), ("devices", "peripheral_low"))

    def test_message_for_a_percent(self):
        _, alerts = evaluate_peripherals(PeripheralAlertState(),
                                         [reading("mouse", 9, name="無線滑鼠")],
                                         {MOUSE: 10}, frozenset({MOUSE}))
        self.assertEqual((alerts[0].title, alerts[0].body),
                         ("無線滑鼠 電量偏低：9%", "已降到你設定的 10% 以下，建議充電。"))
        self.assertEqual((alerts[0].icon, alerts[0].device, alerts[0].critical),
                         ("input-mouse-symbolic", MOUSE, False))


class Presence(unittest.TestCase):
    def test_vanished_device_is_forgotten_and_alerts_again_when_it_returns(self):
        run = Run({})
        self.assertEqual(run([reading("mouse", 10)]), [MOUSE])
        self.assertEqual(run([]), [])
        self.assertEqual(run([reading("mouse", 10)]), [MOUSE])

    def test_device_still_connected_without_a_level_is_kept(self):
        run = Run({})
        self.assertEqual(run([reading("mouse", 10)]), [MOUSE])
        self.assertEqual(run([], present=frozenset({MOUSE})), [])
        self.assertEqual(run([reading("mouse", 10)]), [])

    def test_incomplete_device_list_keeps_everything(self):
        run = Run({})
        self.assertEqual(run([reading("mouse", 10)]), [MOUSE])
        self.assertEqual(run([], present=None), [])
        self.assertEqual(run([reading("mouse", 10)]), [])

    def test_start_only_records(self):
        run = Run({})
        self.assertEqual(run([reading("mouse", 10), reading("pods", 50)], record_only=True), [])
        self.assertEqual(run([reading("mouse", 9), reading("pods", 50)]), [])
        self.assertEqual(run([reading("mouse", 9), reading("pods", 20)]), [PODS])


def payload(*devices, errors=()):
    return {"devices": list(devices), "errors": list(errors), "stamp": 1.0}


def device(device_id, name="MX 滑鼠", kind="無線周邊", battery="45%", percent=45.0):
    return dict(id=device_id, name=name, kind=kind, battery=battery, percent=percent,
                status="已連線", stamp=1.0, source="UPower", detail="")


class Readings(unittest.TestCase):
    def test_battery_reporting_peripherals_only(self):
        found = peripheral_readings(payload(
            device("power:/ups", "Back-UPS", "UPS", "100%", 100.0),
            device("power:/mouse"),
            device("usb:/receiver", "接收器", "USB", "電量不適用／未回報", None),
            device("bt:/hci0/dev", "耳機", "藍牙", "電量等級：低電量", None),
            device("power:/kb", "鍵盤", "無線周邊", "電量等級：正常", None),
            device("power:/coarse", "G305", "無線周邊", "約 10%（分段回報）", 10)))
        self.assertEqual([(r.name, r.percent, r.level_low) for r in found],
                         [("MX 滑鼠", 45, None), ("耳機", None, True), ("鍵盤", None, False),
                          ("G305", 10, None)])
        self.assertEqual(found[0].key, hashlib.sha256(b"power:/mouse").hexdigest()[:16])
        self.assertEqual(found[1].icon, "bluetooth-symbolic")

    def test_percent_is_rounded_half_up(self):
        found = peripheral_readings(payload(device("a", percent=20.5), device("b", percent=20.4)))
        self.assertEqual([r.percent for r in found], [21, 20])

    def test_critical_level_is_low(self):
        found = peripheral_readings(payload(device("a", battery="電量等級：極低電量",
                                                   percent=None)))
        self.assertTrue(found[0].level_low)

    def test_names_never_carry_addresses(self):
        mac = "-".join(["0a"] * 6)
        found = peripheral_readings(payload(device("bt:/x", name=mac + "\x07"),
                                            device("b", name="192.0.2.15 滑鼠"),
                                            device("c", name="")))
        self.assertEqual([r.name for r in found],
                         ["xx:xx:xx:xx:xx:xx", "x.x.x.x 滑鼠", "周邊設備"])

    def test_present_keys_unless_the_list_is_incomplete(self):
        self.assertEqual(present_keys(payload(device("a"), device("b", battery="", percent=None))),
                         frozenset({peripheral_key("a"), peripheral_key("b")}))
        self.assertIsNone(present_keys(payload(device("a"), errors=["藍牙服務暫時無法取得"])))


class Settings(unittest.TestCase):
    """設定視窗的下拉選單與連線設備頁的鈴鐺都寫入 peripheral_thresholds（鍵為識別碼的雜湊）。"""

    def test_choices_match_the_preference_spec(self):
        self.assertEqual(tuple(value for value, _ in THRESHOLD_CHOICES), PERIPHERAL_LEVELS)
        self.assertEqual(tuple(value for value, _ in BELL_CHOICES), PERIPHERAL_LEVELS)
        self.assertEqual([title for _, title in THRESHOLD_CHOICES],
                         ["不通知", "10%", "15%", "20%", "30%", "50%"])
        self.assertEqual(BELL_CHOICES[0][1], "不通知")
        self.assertEqual(BELL_CHOICES[1][1], "低於 10% 時通知")

    def test_first_write_creates_a_salt_and_uses_hmac_keys(self):
        prefs = Preferences.defaults()
        changes = threshold_changes(prefs, ("bt:/org/bluez/hci0/dev_x",), 10, make_salt=lambda: SALT)
        key = hmac.new(bytes.fromhex(SALT), b"bt:/org/bluez/hci0/dev_x",
                       hashlib.sha256).hexdigest()[:16]
        self.assertEqual(changes, {"peripheral_key_salt": SALT, "peripheral_thresholds": {key: 10}})
        updated = prefs.with_changes(**changes)
        self.assertEqual(threshold_for(updated.peripheral_thresholds, SALT,
                                       "bt:/org/bluez/hci0/dev_x"), 10)
        self.assertNotIn("dev_x", str(updated.to_json()))
        again = threshold_changes(updated, ("k",), 30, make_salt=self.fail)
        self.assertNotIn("peripheral_key_salt", again)
        self.assertEqual(again["peripheral_thresholds"],
                         {key: 10, storage_key(SALT, "k"): 30})

    def test_salts_are_random_hex(self):
        first, second = new_salt(), new_salt()
        self.assertRegex(first, r"^[0-9a-f]{32}$")
        self.assertNotEqual(first, second)
        self.assertNotEqual(storage_key(first, "m"), storage_key(second, "m"))

    def test_without_a_salt_the_first_version_keys_are_read(self):
        self.assertEqual(storage_key("", "m"), peripheral_key("m"))
        self.assertEqual(threshold_for({peripheral_key("m"): 0}, "", "m"), 0)
        self.assertEqual(threshold_for({peripheral_key("m"): 0}, SALT, "m"), DEFAULT_THRESHOLD)

    def test_old_keys_of_present_devices_are_migrated_and_stale_ones_dropped(self):
        old = {peripheral_key("m"): 0, peripheral_key("gone"): 50}
        self.assertEqual(migrated(old, SALT, ("m", "k")), {storage_key(SALT, "m"): 0})
        prefs = Preferences.defaults().with_changes(peripheral_thresholds=old)
        changes = threshold_changes(prefs, ("k",), 30, known_ids=("m", "k"),
                                    make_salt=lambda: SALT)
        self.assertEqual(changes["peripheral_thresholds"],
                         {storage_key(SALT, "m"): 0, storage_key(SALT, "k"): 30})

    def test_every_bell_choice_round_trips_through_preferences(self):
        prefs = Preferences.defaults()
        for value, _ in BELL_CHOICES:
            prefs = prefs.with_changes(**threshold_changes(prefs, ("m",), value))
            self.assertEqual(threshold_for(prefs.peripheral_thresholds,
                                           prefs.peripheral_key_salt, "m"), value)

    def test_default_value_removes_the_entry(self):
        start = {storage_key(SALT, "m"): 10, storage_key(SALT, "k"): 50}
        self.assertEqual(with_all(start, SALT, ("m",), DEFAULT_THRESHOLD),
                         {storage_key(SALT, "k"): 50})
        self.assertEqual(len(start), 2)

    def test_set_all_connected_devices(self):
        start = {storage_key(SALT, "old"): 30}
        self.assertEqual(with_all(start, SALT, ("m", "k"), 0),
                         {storage_key(SALT, "old"): 30, storage_key(SALT, "k"): 0,
                          storage_key(SALT, "m"): 0})
        self.assertEqual(with_all({storage_key(SALT, "m"): 0}, SALT, ("m",), 20), {})

    def test_map_within_the_limit_is_untouched(self):
        start = {f"{index:016x}": 10 for index in range(MAX_ITEMS)}
        self.assertEqual(fitted(start, set(), set()), start)

    def test_full_map_drops_unlisted_devices_first_then_the_oldest(self):
        start = {f"{index:016x}": 10 for index in range(MAX_ITEMS + 3)}
        keys = list(start)
        known = set(keys[:2])                         # 最舊的兩台還連著
        result = fitted(start, {keys[-1]}, known)
        self.assertEqual(len(result), MAX_ITEMS)
        self.assertEqual(set(start) - set(result), set(keys[2:5]))
        everything_known = fitted(start, {keys[0]}, set(keys))
        self.assertEqual(set(start) - set(everything_known), set(keys[1:4]))

    def test_new_device_fits_into_a_full_map(self):
        full = {f"{index:016x}": 10 for index in range(MAX_ITEMS)}
        prefs = Preferences.defaults().with_changes(peripheral_thresholds=full,
                                                    peripheral_key_salt=SALT)
        changes = threshold_changes(prefs, ("new",), 30, known_ids=())
        result = changes["peripheral_thresholds"]
        self.assertEqual(len(result), MAX_ITEMS)
        self.assertEqual(result[storage_key(SALT, "new")], 30)
        self.assertNotIn(f"{0:016x}", result)
        prefs.with_changes(**changes)

    def test_invalid_value_is_rejected(self):
        with self.assertRaises(ValueError):
            with_all({}, SALT, ("m",), 25)

    def test_evaluation_reads_salted_thresholds(self):
        thresholds = {storage_key(SALT, "mouse"): 0}
        _, alerts = evaluate_peripherals(PeripheralAlertState(), [reading("mouse", 5)],
                                         thresholds, frozenset({MOUSE}), salt=SALT)
        self.assertEqual(alerts, ())

    def test_bell_icon_and_tooltip(self):
        self.assertEqual(bell_icon(0), "notifications-disabled-symbolic")
        self.assertEqual(bell_icon(20), "preferences-system-notifications-symbolic")
        self.assertEqual(bell_tooltip(0), "低電量通知：關閉")
        self.assertEqual(bell_tooltip(15), "低電量通知：低於 15% 時通知")


if __name__ == "__main__":
    unittest.main()
