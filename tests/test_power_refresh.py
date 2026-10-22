"""插拔電源後的重新讀取：立即一次，之後 +1、+3、+6 秒（參考專案 powerSourceChanged）。"""
import unittest

from sdm.services.power_refresh import PowerRefresh, refresh_delays


class FakeTimers:
    def __init__(self):
        self.next_id, self.active, self.cancelled = 1, {}, []

    def schedule(self, seconds, callback):
        source = self.next_id
        self.next_id += 1
        self.active[source] = (seconds, callback)
        return source

    def cancel(self, source):
        self.cancelled.append(source)
        self.active.pop(source, None)

    def fire(self, seconds):
        for source, (delay, callback) in list(self.active.items()):
            if delay == seconds:
                del self.active[source]
                self.assert_removed = callback()

    def delays(self):
        return sorted(delay for delay, _ in self.active.values())


class Delays(unittest.TestCase):
    def test_event_kinds(self):
        self.assertEqual(refresh_delays("Mains", ""), (0, 1, 3, 6))
        self.assertEqual(refresh_delays("USB", "Unknown"), (0, 1, 3, 6))
        self.assertEqual(refresh_delays("Battery", "System"), (0,))
        self.assertEqual(refresh_delays("Battery", ""), (0,))
        self.assertEqual(refresh_delays("Battery", "Device"), ())
        self.assertEqual(refresh_delays("Mains", "Device"), ())
        self.assertEqual(refresh_delays("Wireless", ""), ())
        self.assertEqual(refresh_delays(None, None), ())


class Scheduling(unittest.TestCase):
    def setUp(self):
        self.timers, self.refreshed = FakeTimers(), []
        self.refresh = PowerRefresh(lambda: self.refreshed.append(True), self.timers.schedule,
                                    self.timers.cancel)

    def test_adapter_event_refreshes_now_and_three_more_times(self):
        self.refresh.on_event("Mains", "")
        self.assertEqual(len(self.refreshed), 1)
        self.assertEqual(self.timers.delays(), [1, 3, 6])
        for seconds in (1, 3, 6):
            self.timers.fire(seconds)
            self.assertIs(self.timers.assert_removed, False)
        self.assertEqual(len(self.refreshed), 4)
        self.assertEqual(self.refresh.pending, [])

    def test_new_adapter_event_restarts_the_sequence(self):
        self.refresh.on_event("Mains", "")
        self.timers.fire(1)
        self.refresh.on_event("USB", "")
        self.assertEqual(self.timers.cancelled, [2, 3])
        self.assertEqual(self.timers.delays(), [1, 3, 6])

    def test_battery_event_refreshes_once_and_keeps_a_running_sequence(self):
        self.refresh.on_event("Mains", "")
        self.refresh.on_event("Battery", "")
        self.assertEqual(len(self.refreshed), 2)
        self.assertEqual(self.timers.delays(), [1, 3, 6])
        self.assertEqual(self.timers.cancelled, [])

    def test_peripheral_events_are_ignored(self):
        self.refresh.on_event("Battery", "Device")
        self.assertEqual((self.refreshed, self.timers.delays()), ([], []))

    def test_observed_supply_change_starts_follow_up_reads(self):
        self.refresh.observe(False)
        self.refresh.observe(False)
        self.assertEqual(self.timers.delays(), [])
        self.refresh.observe(True)
        self.assertEqual((self.refreshed, self.timers.delays()), ([], [1, 3, 6]))
        self.refresh.observe(False)
        self.assertEqual(self.timers.cancelled, [])

    def test_stop_cancels_pending_reads(self):
        self.refresh.on_event("Mains", "")
        self.refresh.stop()
        self.assertEqual(sorted(self.timers.cancelled), [1, 2, 3])
        self.assertEqual(self.refresh.pending, [])


if __name__ == "__main__":
    unittest.main()
