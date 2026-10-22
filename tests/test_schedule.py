"""更新頻率與省電模式：各取樣層的間隔，以及不漏掉計時器的重新排程。

沿用參考專案的規則：更新頻率只能是 1／2／5／10 秒（其他值改用預設的 2 秒）；
省電模式至少每 2 秒；「只在使用電池時」接著電源就不生效。
"""
import unittest

from sdm.core import schedule
from sdm.core.schedule import Cadence, cadence, external_power, saving_active
from sdm.core.settings import Preferences, preferences_from_mapping
from sdm.data.power_supply import BatteryReading
from sdm.data.ups import UpsReading
from sdm.services.power_service import PowerService
from sdm.services.scheduler import TierScheduler


def desktop(on_battery=False):
    status = ("ONBATT",) if on_battery else ("ONLINE",)
    ups = UpsReading(status=status, online=not on_battery, on_battery=on_battery, load_w=200.0)
    return PowerService(read_ups=lambda: ups, read_gpus=lambda: (), read_uptime=lambda: None,
                        clock=lambda: 1.0, wall=lambda: 1.0).sample()


def laptop(status="Discharging", online=False):
    from sdm.data.power_supply import AdapterReading
    battery = BatteryReading(name="BAT0", status=status, capacity=70, power_w=8.0)
    adapter = AdapterReading(name="AC", kind="Mains", online=online)
    return PowerService(read_ups=lambda: None, read_gpus=lambda: (), read_uptime=lambda: None,
                        clock=lambda: 1.0, wall=lambda: 1.0, read_batteries=lambda: (battery,),
                        read_adapters=lambda: (adapter,)).sample()


class Interval(unittest.TestCase):
    def test_unknown_interval_in_the_file_falls_back_to_two_seconds(self):
        with self.assertLogs("sdm.core.settings", "WARNING"):
            loaded = preferences_from_mapping({"refresh_interval": 3})
        self.assertEqual(loaded.refresh_interval, 2)
        self.assertEqual(schedule.effective_interval(3, False), 2)

    def test_default_cadence_matches_the_previous_fixed_timers(self):
        self.assertEqual(cadence(2, False), Cadence(system=2, slow=5, devices=5, detail=2,
                                                    nvidia_ms=2000, saving=False))

    def test_every_second_keeps_the_slower_tiers_at_their_own_pace(self):
        result = cadence(1, False)
        self.assertEqual((result.system, result.slow, result.detail, result.nvidia_ms),
                         (1, 5, 2, 2000))

    def test_slow_tiers_never_run_faster_than_the_interval(self):
        result = cadence(10, False)
        self.assertEqual((result.system, result.slow, result.devices, result.detail,
                          result.nvidia_ms), (10, 10, 10, 10, 10000))

    def test_power_saving_means_at_least_two_seconds(self):
        self.assertEqual(cadence(1, True).system, 2)
        self.assertEqual(cadence(5, True).system, 5)
        self.assertTrue(cadence(1, True).saving)


class Saving(unittest.TestCase):
    def test_off_unless_enabled(self):
        self.assertFalse(saving_active(False, False, False))
        self.assertTrue(saving_active(True, False, True))

    def test_battery_only_is_inactive_on_external_power(self):
        self.assertFalse(saving_active(True, True, True))
        self.assertTrue(saving_active(True, True, False))

    def test_laptop_external_power_follows_the_adapter(self):
        self.assertFalse(external_power(laptop()))
        self.assertTrue(external_power(laptop("Charging", online=True)))

    def test_desktop_on_battery_means_the_ups_runs_on_battery(self):
        self.assertTrue(external_power(desktop()))
        self.assertFalse(external_power(desktop(on_battery=True)))
        self.assertTrue(external_power(None))

    def test_cadence_from_preferences_and_power(self):
        prefs = Preferences.defaults().with_changes(refresh_interval=1, power_saving=True,
                                                    power_saving_on_battery_only=True)
        self.assertEqual(schedule.cadence_for(prefs, desktop()).system, 1)
        self.assertEqual(schedule.cadence_for(prefs, desktop(on_battery=True)).system, 2)


class FakeTimers:
    """假的 GLib 計時器：記錄每個仍存在的來源。"""
    def __init__(self):
        self.live, self.next_id, self.log = {}, 1, []

    def add(self, seconds, kind):
        source = self.next_id
        self.next_id += 1
        self.live[source] = (kind, seconds)
        self.log.append(("add", kind, seconds))
        return source

    def remove(self, source):
        self.log.append(("remove", self.live.pop(source)[0]))


class Rescheduling(unittest.TestCase):
    def setUp(self):
        self.timers = FakeTimers()
        self.scheduler = TierScheduler(self.timers.add, self.timers.remove)

    def live(self):
        return sorted(self.timers.live.values())

    def test_first_apply_starts_every_tier(self):
        started = self.scheduler.apply({"system": 2, "slow": 5})
        self.assertEqual(set(started), {"system", "slow"})
        self.assertEqual(self.live(), [("slow", 5), ("system", 2)])

    def test_only_changed_tiers_are_replaced(self):
        self.scheduler.apply({"system": 2, "slow": 5})
        self.timers.log.clear()
        started = self.scheduler.apply({"system": 1, "slow": 5})
        self.assertEqual(started, ())
        self.assertEqual(self.timers.log, [("remove", "system"), ("add", "system", 1)])
        self.assertEqual(self.live(), [("slow", 5), ("system", 1)])

    def test_missing_or_none_tiers_are_removed(self):
        self.scheduler.apply({"system": 2, "slow": 5})
        self.scheduler.apply({"system": None, "slow": 5})
        self.assertEqual(self.live(), [("slow", 5)])
        self.scheduler.apply({})
        self.assertEqual(self.live(), [])

    def test_a_tier_that_comes_back_is_reported_as_started(self):
        self.scheduler.apply({"system": 2})
        self.scheduler.apply({"system": None})
        self.assertEqual(self.scheduler.apply({"system": 2}), ("system",))
        self.assertEqual(self.scheduler.interval("system"), 2)

    def test_many_changes_never_leak_sources(self):
        for seconds in (1, 2, 5, 10, 2, 2, 1):
            self.scheduler.apply({"system": seconds, "slow": max(5, seconds)})
        self.assertEqual(len(self.timers.live), 2)
        self.scheduler.stop()
        self.assertEqual(self.timers.live, {})
        self.assertIsNone(self.scheduler.interval("system"))


if __name__ == "__main__":
    unittest.main()
