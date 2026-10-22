"""UPower 與電源模式服務的唯讀讀取：以假的屬性表代替系統匯流排。"""
import unittest

from gi.repository import GLib

from sdm.core.estimates import SystemTimes
from sdm.data import upower
from sdm.data.upower import UpowerReading, read_power_profile, read_upower

BATTERY_TYPE = 2
UPS_TYPE = 3


class FakeBus:
    """（服務、路徑、介面、屬性）→ 值；沒有的屬性拋出 GLib.Error，並記錄讀過哪些屬性。"""

    def __init__(self, values):
        self.values, self.asked = dict(values), []

    def __call__(self, destination, path, interface, name):
        self.asked.append(name)
        try:
            return self.values[(destination, path, interface, name)]
        except KeyError:
            raise GLib.Error("unavailable") from None


def laptop(**overrides):
    device = {"Type": BATTERY_TYPE, "IsPresent": True, "State": 2, "Percentage": 82.0,
              "TimeToEmpty": 12300, "TimeToFull": 0, "EnergyRate": 6.5}
    device.update(overrides)
    values = {(upower.DESTINATION, upower.MANAGER_PATH, upower.MANAGER_INTERFACE, "OnBattery"): True}
    values.update({(upower.DESTINATION, upower.DISPLAY_PATH, upower.DEVICE_INTERFACE, key): value
                   for key, value in device.items()})
    return FakeBus(values)


class Upower(unittest.TestCase):
    def test_display_device_of_a_laptop(self):
        reading = read_upower(laptop())
        self.assertEqual(reading, UpowerReading(on_battery=True, is_battery=True, state=2,
                                                percentage=82.0, time_to_empty_s=12300,
                                                time_to_full_s=None, energy_rate_w=6.5))
        self.assertEqual(reading.times(), SystemTimes(reports=True, to_empty_min=205,
                                                      to_full_min=None))

    def test_still_estimating_is_none(self):
        reading = read_upower(laptop(TimeToEmpty=0, TimeToFull=0))
        self.assertEqual(reading.times(), SystemTimes(reports=True))

    def test_service_missing(self):
        self.assertIsNone(read_upower(FakeBus({})))

    def test_ups_display_device_is_not_a_laptop_estimate(self):
        bus = laptop(Type=UPS_TYPE)
        reading = read_upower(bus)
        self.assertEqual((reading.on_battery, reading.is_battery), (True, False))
        self.assertEqual(reading.times(), SystemTimes())
        self.assertNotIn("TimeToEmpty", bus.asked)

    def test_only_listed_properties_are_read(self):
        bus = laptop()
        read_upower(bus)
        self.assertFalse({"Serial", "NativePath", "Vendor", "Model"} & set(bus.asked))

    def test_invalid_values(self):
        reading = read_upower(laptop(TimeToEmpty=-5, Percentage=180.0, EnergyRate=float("nan"),
                                     State="x"))
        self.assertEqual((reading.time_to_empty_s, reading.percentage, reading.energy_rate_w,
                          reading.state), (None, None, None, None))

    def test_minutes_round_to_nearest(self):
        self.assertEqual(read_upower(laptop(TimeToEmpty=89)).times().to_empty_min, 1)
        self.assertEqual(read_upower(laptop(TimeToEmpty=29)).times().to_empty_min, 0)


class Profiles(unittest.TestCase):
    def test_new_service_name_first(self):
        bus = FakeBus({(upower.PROFILES[0] + ("ActiveProfile",)): "power-saver",
                       (upower.PROFILES[1] + ("ActiveProfile",)): "balanced"})
        self.assertEqual(read_power_profile(bus), "power-saver")

    def test_falls_back_to_the_old_service_name(self):
        bus = FakeBus({(upower.PROFILES[1] + ("ActiveProfile",)): "balanced"})
        self.assertEqual(read_power_profile(bus), "balanced")

    def test_missing_or_invalid(self):
        self.assertIsNone(read_power_profile(FakeBus({})))
        bus = FakeBus({(upower.PROFILES[0] + ("ActiveProfile",)): 7})
        self.assertIsNone(read_power_profile(bus))


if __name__ == "__main__":
    unittest.main()
