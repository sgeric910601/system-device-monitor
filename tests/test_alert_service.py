"""通知的協調與看板接線：耗電快照與設備清單交給狀態機、頻率限制的計時器、
啟動與喚醒後只記錄，以及模擬測試與示範模式完全不建立通知。不連到任何通知服務。"""
from concurrent.futures import Future
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sdm.constants import Alerts
from sdm.core import alert_messages as messages
from sdm.core.battery import CHARGING, DISCHARGING, PLUGGED_IDLE
from sdm.core.settings import Preferences
from sdm.data.power_supply import BatteryReading
from sdm.data.ups import UpsReading
from sdm.services.alert_service import AlertService, battery_input
from sdm.services.power_service import PowerService
from sdm.ui import alert_wiring
from sdm.ui import app as ui_app

PREFS = Preferences.defaults()


class Clock:
    def __init__(self, now=1000.0): self.now = now
    def __call__(self): return self.now


class Idle:
    """取代 GLib.idle_add：每次 iterate() 讓每個回呼執行一次，回傳 False 的就移除。"""
    def __init__(self): self.callbacks, self.next_id = {}, 1000   # 與計時器的代號分開
    def defer(self, callback):
        self.callbacks[self.next_id] = callback
        self.next_id += 1
        return self.next_id - 1
    def cancel(self, source): self.callbacks.pop(source, None)
    def iterate(self):
        for source, callback in list(self.callbacks.items()):
            if not callback():
                self.callbacks.pop(source, None)
    def drain(self):
        while self.callbacks:
            self.iterate()


class Timers:
    def __init__(self): self.live, self.next_id = {}, 1
    def schedule(self, seconds, callback):
        self.live[self.next_id] = (seconds, callback)
        self.next_id += 1
        return self.next_id - 1
    def cancel(self, source): self.live.pop(source, None)
    def fire(self):
        for source, (_, callback) in list(self.live.items()):
            del self.live[source]
            callback()


def laptop(sequence, percent, state=DISCHARGING, online=False, weak=False, limit=None):
    battery = SimpleNamespace(percent=percent, ac_online=online, limit=limit)
    return SimpleNamespace(sequence=sequence, battery=battery, state=state, ups=None,
                           estimates=SimpleNamespace(draining_on_ac=weak))


def desktop(sequence, on_battery=False):
    reading = UpsReading(status=("ONBATT",) if on_battery else ("ONLINE",),
                         online=not on_battery, on_battery=on_battery, charge_pct=90.0,
                         load_w=200.0, time_left_min=30.0)
    return SimpleNamespace(sequence=sequence, ups=reading)


def devices(*levels, errors=()):
    return {"devices": [dict(id=f"power:/mouse{index}", name=f"滑鼠 {index}", kind="無線周邊",
                             battery=f"{level}%", percent=level, status="", stamp=1.0,
                             source="UPower", detail="") for index, level in enumerate(levels)],
            "errors": list(errors), "stamp": 1.0}


class Notifications:
    """假的送出：working 為 False 時模擬通知服務不存在。"""
    def __init__(self): self.sent, self.attempts, self.working = [], [], True
    def __call__(self, alert):
        self.attempts.append(alert)
        if self.working:
            self.sent.append(alert)
        return self.working


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.notifications, self.timers, self.idle = Notifications(), Timers(), Idle()
        self.sent = self.notifications.sent
        self.clock, self.boot = Clock(), Clock(5000.0)
        self.service = AlertService(self.notifications, self.timers.schedule, self.cancel,
                                    defer=self.idle.defer, clock=self.clock, boot=self.boot)
        self.addCleanup(self.service.stop)

    def cancel(self, source):
        """GLib.source_remove 同時適用計時器與閒置回呼。"""
        self.timers.cancel(source)
        self.idle.cancel(source)

    def power(self, snapshot):
        self.service.power(snapshot, PREFS)
        self.idle.drain()

    def devices(self, payload):
        self.service.devices(payload, PREFS)
        self.idle.drain()

    def fire(self):
        self.timers.fire()
        self.idle.drain()


class Service(ServiceTest):
    def step(self, seconds=5):
        self.clock.now += seconds
        self.boot.now += seconds

    def titles(self):
        return [alert.title for alert in self.sent]

    def test_first_snapshot_only_records_then_alerts(self):
        self.power(laptop(1, 15))
        self.assertEqual(self.sent, [])
        self.step()
        self.power(laptop(2, 14))
        self.assertEqual(self.sent, [])                    # 啟動時就已經低於門檻
        self.step()
        self.power(laptop(3, 30, state=CHARGING, online=True))
        self.step()
        self.power(laptop(4, 20))
        self.assertEqual(self.titles(), ["電量偏低：20%"])

    def test_same_snapshot_is_evaluated_once(self):
        self.power(desktop(1))
        for _ in range(3):
            self.step()
            self.power(desktop(2, on_battery=True))
        self.assertEqual(self.sent, [])
        self.step()
        self.power(desktop(3, on_battery=True))
        self.assertEqual(self.titles(), ["不斷電系統改用電池供電"])

    def test_resume_after_sleep_only_records(self):
        self.power(laptop(1, 40))
        self.step()
        self.power(laptop(2, 30))
        self.boot.now += Alerts.RESUME_GAP_SECONDS + 1        # 睡眠：單調時鐘不動
        self.power(laptop(3, 12))
        self.step()
        self.power(laptop(4, 11))
        self.assertEqual(self.sent, [])

    def test_missing_snapshot_is_ignored(self):
        self.power(None)
        self.assertEqual(self.sent, [])

    def test_peripherals_with_priming_and_rate_limit(self):
        self.devices(devices(10))
        self.assertEqual(self.sent, [])
        self.step()
        self.devices(devices(10, 9))
        self.assertEqual(self.titles(), ["滑鼠 1 電量偏低：9%"])
        self.step()
        self.devices(devices(10))              # 滑鼠 1 斷線
        self.step()
        self.devices(devices(10, 8))           # 重新連上：視為新設備
        self.assertEqual(len(self.sent), 1)                   # 60 秒內：等待
        self.assertEqual(list(self.timers.live.values())[0][0], 50)   # 上一則在 10 秒前：再等 50 秒
        self.clock.now += 50
        self.fire()
        self.assertEqual(self.titles(), ["滑鼠 1 電量偏低：9%", "滑鼠 1 電量偏低：8%"])
        self.assertEqual(self.timers.live, {})

    def test_payload_without_a_stamp_is_ignored(self):
        self.devices({"devices": [], "errors": ["設備資料暫時無法取得"]})
        self.devices(devices(50))
        self.step()
        self.devices(devices(5))
        self.assertEqual(self.titles(), ["滑鼠 0 電量偏低：5%"])

    def test_incomplete_list_does_not_forget_devices(self):
        self.devices(devices(50))
        self.step()
        self.devices(devices(5))
        self.step()
        self.devices(devices(errors=["藍牙服務暫時無法取得"]))
        self.step(100)
        self.devices(devices(5))
        self.assertEqual(len(self.sent), 1)

    def test_stop_cancels_the_pending_timer(self):
        self.devices(devices(50))
        self.step()
        self.devices(devices(5))
        self.step()
        self.devices(devices())
        self.step()
        self.devices(devices(4))
        self.assertEqual(len(self.timers.live), 1)
        self.service.stop()
        self.assertEqual(self.timers.live, {})

    def low_mice(self, *levels):
        """啟動後先記錄，再讓這些滑鼠同時低於門檻。"""
        self.devices(devices(*[50] * len(levels)))
        self.step()
        self.service.devices(devices(*levels), PREFS)

    def test_one_notification_per_main_loop_iteration(self):
        self.low_mice(5, 6)
        self.assertEqual(self.sent, [])
        self.idle.iterate()
        self.assertEqual(self.titles(), ["滑鼠 0 電量偏低：5%"])
        self.idle.iterate()
        self.assertEqual(self.titles(), ["滑鼠 0 電量偏低：5%", "滑鼠 1 電量偏低：6%"])
        self.assertEqual(self.idle.callbacks, {})

    def test_failed_send_is_retried_after_thirty_seconds(self):
        self.notifications.working = False
        self.low_mice(5)
        self.idle.drain()
        self.assertEqual((len(self.notifications.attempts), self.sent), (1, []))
        self.assertEqual([delay for delay, _ in self.timers.live.values()],
                         [Alerts.SEND_RETRY_SECONDS])
        self.notifications.working = True
        self.clock.now += Alerts.SEND_RETRY_SECONDS
        self.fire()
        self.assertEqual(self.titles(), ["滑鼠 0 電量偏低：5%"])
        self.assertEqual(self.timers.live, {})

    def test_failed_sends_stop_after_five_attempts(self):
        self.notifications.working = False
        self.low_mice(5)
        self.idle.drain()
        for _ in range(Alerts.SEND_MAX_ATTEMPTS):
            self.clock.now += Alerts.SEND_RETRY_SECONDS
            self.fire()
        self.assertEqual(len(self.notifications.attempts), Alerts.SEND_MAX_ATTEMPTS)
        self.assertEqual(self.timers.live, {})

    def test_stop_cancels_queued_notifications(self):
        self.low_mice(5, 6)
        self.service.stop()
        self.idle.drain()
        self.assertEqual(self.sent, [])

    def test_test_notification_is_not_rate_limited(self):
        self.service.send_test()
        self.service.send_test()
        self.assertEqual(self.titles(), ["系統與裝置監看", "系統與裝置監看"])


class Inputs(unittest.TestCase):
    def test_battery_input_from_a_real_laptop_snapshot(self):
        battery = BatteryReading(name="BAT0", status="Discharging", capacity=18, power_w=12.0)
        power = PowerService(read_ups=lambda: None, read_gpus=lambda: (), read_uptime=lambda: None,
                             read_batteries=lambda: (battery,), read_upower=lambda: None).sample()
        reading = battery_input(power)
        self.assertEqual((reading.percent, reading.plugged, reading.draining, reading.charging),
                         (18, False, True, False))

    def test_weak_charger_counts_as_draining(self):
        reading = battery_input(laptop(1, 30, state=PLUGGED_IDLE, online=True, weak=True))
        self.assertEqual((reading.plugged, reading.draining, reading.weak_charger),
                         (True, True, True))

    def test_desktop_and_unknown_percent_have_no_battery_input(self):
        self.assertIsNone(battery_input(desktop(1)))
        self.assertIsNone(battery_input(laptop(1, None)))


class FakeNotifier:
    instances = []

    def __init__(self, open_page, connection=None):
        self.open_page, self.connection, self.sent = open_page, connection, []
        self.listeners = []
        FakeNotifier.instances.append(self)

    def send(self, alert):
        self.sent.append(alert)
        return True

    def subscribe(self, listener):
        self.listeners.append(listener)

    def status_text(self):
        return ""


class Wiring(unittest.TestCase):
    def build(self, **options):
        with patch.object(alert_wiring, "Notifier", FakeNotifier):
            app = ui_app.Monitor(**options)
        self.addCleanup(app.pool.shutdown)
        return app

    def test_smoke_test_and_preview_never_create_a_notifier(self):
        refuse = AssertionError("notifier created")
        with patch.object(alert_wiring, "Notifier", side_effect=refuse):
            for options in (dict(smoke=True), dict(preview="desktop"),
                            dict(preview="laptop-battery", smoke=True)):
                app = ui_app.Monitor(**options)
                self.addCleanup(app.pool.shutdown)
                self.assertIsNone(app.notifier)
                self.assertIsNone(app.alerts)
                self.assertFalse(app.send_test_notification())
                self.assertIn("不會送出通知", app.notice_text())

    def test_normal_run_creates_the_notifier_and_the_test_action(self):
        app = self.build()
        self.assertIsInstance(app.notifier, FakeNotifier)
        app.lookup_action(Alerts.TEST_ACTION).activate(None)
        self.assertEqual([alert.kind for alert in app.notifier.sent], [messages.TEST])

    def test_test_action_exists_in_smoke_tests_but_sends_nothing(self):
        with patch.object(alert_wiring, "Notifier", side_effect=AssertionError("created")):
            app = ui_app.Monitor(smoke=True)
        self.addCleanup(app.pool.shutdown)
        with self.assertLogs("sdm.ui.alert_wiring", level="INFO"):
            app.lookup_action(Alerts.TEST_ACTION).activate(None)

    def test_samples_reach_the_alert_service(self):
        app = ui_app.Monitor.__new__(ui_app.Monitor)
        calls = []
        app.alerts = SimpleNamespace(power=lambda power, prefs: calls.append(("power", power)),
                                     devices=lambda data, prefs: calls.append(("devices", data)))
        app.closing, app.busy, app.errors = False, set(), {}
        app.preferences, app.last_recorded = PREFS, 1.0
        app.data = {"system": {}, "slow": {}, "devices": {"devices": [], "errors": []}}
        app.render = lambda: None
        app.power_received = lambda kind: None
        snapshot = desktop(7)
        for kind, result in (("slow", {"power": snapshot}), ("devices", devices(40))):
            future = Future()
            future.set_result(result)
            app.received(kind, future)
        self.assertEqual(calls, [("power", snapshot), ("devices", devices(40))])

    def test_stop_cancels_alert_timers(self):
        app = self.build()
        stopped = []
        app.alerts = SimpleNamespace(stop=lambda: stopped.append(True))
        app.stop_alerts()
        self.assertEqual(stopped, [True])


if __name__ == "__main__":
    unittest.main()
