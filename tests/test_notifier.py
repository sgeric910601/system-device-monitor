"""桌面通知（libnotify 包裝）：用假的 Notify 模組測試，絕不連到真正的通知服務。

- 成功時：圖示、desktop-entry 提示、緊急程度（只有不斷電系統改用電池與電量低是緊急）、
  點通知開啟相關頁面；通知保留到關閉為止，同一種通知更新原本那則。
- 失敗（沒有通知服務、Notify.init 失敗、GLib.Error、函式庫載入失敗）：第一次 WARNING、
  之後 DEBUG，絕不拋出；日誌不含通知內容。
- 確認通知服務是否存在：非同步 D-Bus 呼叫，結果通知監聽者。
"""
import logging
import unittest

from gi.repository import GLib

from sdm.constants import Alerts
from sdm.core import alert_messages as messages
from sdm.services import notifier as notifier_module
from sdm.services.notifier import AVAILABLE, UNAVAILABLE, UNKNOWN, Notifier

LOGGER = "sdm.services.notifier"


class FakeNotification:
    def __init__(self, module, title, body, icon):
        self.module, self.title, self.body, self.icon = module, title, body, icon
        self.hints, self.actions, self.handlers = {}, [], []
        self.urgency, self.shown, self.updates = None, 0, 0

    def update(self, title, body, icon):
        self.title, self.body, self.icon = title, body, icon
        self.updates += 1

    def set_urgency(self, urgency):
        self.urgency = urgency

    def set_hint(self, key, value):
        self.hints[key] = value.unpack()

    def add_action(self, action, label, callback, *data):
        self.actions.append((action, label, callback, data))

    def connect(self, signal, callback, *data):
        self.handlers.append((signal, callback, data))

    def show(self):
        if self.module.show_error is not None:
            raise self.module.show_error
        self.shown += 1
        self.module.clock.now += self.module.show_seconds    # 通知服務回應所花的時間
        return True

    def click(self):
        for action, _, callback, data in self.actions:
            if action == "default":
                callback(self, action, *data)

    def close(self):
        for signal, callback, data in self.handlers:
            if signal == "closed":
                callback(self, *data)


class FakeNotify:
    class Urgency:
        NORMAL, CRITICAL = "normal", "critical"

    def __init__(self, init_result=True, show_error=None):
        self.inits, self.created = [], []
        self.init_result, self.show_error = init_result, show_error
        self.clock, self.show_seconds = Clock(), 0.0
        module = self

        class Notification:
            @staticmethod
            def new(title, body, icon):
                created = FakeNotification(module, title, body, icon)
                module.created.append(created)
                return created
        self.Notification = Notification

    def is_initted(self):
        return bool(self.inits) and self.init_result

    def init(self, name):
        self.inits.append(name)
        return self.init_result


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


def ups_alert():
    return messages.Alert(kind=messages.UPS_ON_BATTERY, title="不斷電系統改用電池供電",
                          body="電量 99%", icon="uninterruptible-power-supply-symbolic",
                          critical=True)


def make(module=None, opened=None, load=None):
    module = module or FakeNotify()
    opened = [] if opened is None else opened
    return Notifier(opened.append, load=load or (lambda: module)), module, opened


class Sending(unittest.TestCase):
    def test_lazy_init_hints_icon_and_urgency(self):
        notifier, module, _ = make()
        self.assertEqual(module.inits, [])
        self.assertTrue(notifier.send(messages.test_alert()))
        self.assertEqual(module.inits, ["系統與裝置監看"])
        created = module.created[0]
        self.assertEqual((created.title, created.body, created.icon),
                         ("系統與裝置監看", "通知功能正常。", "system-device-monitor"))
        self.assertEqual(created.hints, {"desktop-entry": "system-device-monitor"})
        self.assertEqual((created.urgency, created.shown), ("normal", 1))
        self.assertEqual(notifier.status, AVAILABLE)
        notifier.send(ups_alert())
        self.assertEqual(module.created[1].urgency, "critical")
        self.assertEqual(module.inits, ["系統與裝置監看"])

    def test_default_action_opens_the_related_page(self):
        opened = []
        notifier, module, _ = make(opened=opened)
        notifier.send(ups_alert())
        low = messages.Alert(messages.PERIPHERAL_LOW, "滑鼠 電量偏低：9%", "", "x",
                             page="devices", device="k")
        notifier.send(low)
        module.created[0].click()
        module.created[1].click()
        self.assertEqual(opened, ["power", "devices"])
        self.assertEqual(module.created[0].actions[0][:2], ("default", "開啟"))

    def test_kept_until_closed_and_same_kind_is_updated(self):
        notifier, module, _ = make()
        notifier.send(ups_alert())
        notifier.send(ups_alert())
        self.assertEqual(len(module.created), 1)
        self.assertEqual((module.created[0].updates, module.created[0].shown), (1, 2))
        self.assertEqual(len(notifier.live), 1)
        module.created[0].close()
        self.assertEqual(notifier.live, {})
        notifier.send(ups_alert())
        self.assertEqual(len(module.created), 2)

    def test_number_of_kept_notifications_is_limited(self):
        notifier, module, _ = make()
        for index in range(Alerts.MAX_LIVE + 5):
            notifier.send(messages.Alert("k", "t", "b", "i", device=str(index)))
        self.assertEqual(len(notifier.live), Alerts.MAX_LIVE)
        self.assertNotIn(("k", "0"), notifier.live)

    def test_page_errors_are_logged_not_raised(self):
        def broken(page):
            raise RuntimeError("no window")
        module = FakeNotify()
        notifier = Notifier(broken, load=lambda: module)
        notifier.send(ups_alert())
        with self.assertLogs(LOGGER, level="WARNING"):
            module.created[0].click()


class Failures(unittest.TestCase):
    def assert_warns_once_then_debug(self, notifier):
        with self.assertLogs(LOGGER, level="DEBUG") as logs:
            self.assertFalse(notifier.send(ups_alert()))
            self.assertFalse(notifier.send(ups_alert()))
            self.assertFalse(notifier.send(messages.test_alert()))
        levels = [record.levelno for record in logs.records]
        self.assertEqual(levels, [logging.WARNING, logging.DEBUG, logging.DEBUG])
        text = "\n".join(logs.output)
        for secret in ("不斷電系統", "電量", "通知功能正常"):
            self.assertNotIn(secret, text)
        self.assertEqual(notifier.status, UNAVAILABLE)

    def test_no_notification_service(self):
        notifier, module, _ = make(FakeNotify(show_error=GLib.Error("ServiceUnknown")))
        self.assert_warns_once_then_debug(notifier)
        self.assertEqual(notifier.live, {})

    def test_init_returning_false(self):
        notifier, module, _ = make(FakeNotify(init_result=False))
        self.assert_warns_once_then_debug(notifier)
        self.assertEqual(module.created, [])
        self.assertEqual(len(module.inits), 3)      # 每次都再試一次（通知服務可能晚點才啟動）

    def test_library_missing(self):
        def missing():
            raise ValueError("Namespace Notify not available")
        notifier, _, _ = make(load=missing)
        self.assert_warns_once_then_debug(notifier)

    def test_unexpected_error_is_contained(self):
        module = FakeNotify(show_error=TypeError("bad"))
        notifier, _, _ = make(module)
        self.assert_warns_once_then_debug(notifier)

    def test_recovers_when_the_service_appears(self):
        module = FakeNotify(show_error=GLib.Error("ServiceUnknown"))
        notifier, _, _ = make(module)
        with self.assertLogs(LOGGER, level="WARNING"):
            notifier.send(ups_alert())
        module.show_error = None
        self.assertTrue(notifier.send(ups_alert()))
        self.assertEqual(notifier.status, AVAILABLE)


class SlowService(unittest.TestCase):
    """libnotify 0.7／0.8 沒有非同步的 show()：一次超過 2 秒就暫停 60 秒，介面不會一直卡住。"""

    def setUp(self):
        self.module = FakeNotify()
        self.notifier = Notifier(lambda page: None, load=lambda: self.module,
                                 clock=self.module.clock)

    def test_a_slow_show_pauses_sending(self):
        self.module.show_seconds = Alerts.SLOW_SHOW_SECONDS + 1
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            self.assertTrue(self.notifier.send(ups_alert()))
            self.assertFalse(self.notifier.send(messages.test_alert()))
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(len(self.module.created), 1)
        self.module.clock.now += Alerts.SLOW_PAUSE_SECONDS
        self.module.show_seconds = 0.0
        self.assertTrue(self.notifier.send(messages.test_alert()))
        self.assertEqual(len(self.module.created), 2)

    def test_a_quick_show_does_not_pause(self):
        self.module.show_seconds = Alerts.SLOW_SHOW_SECONDS - 0.5
        self.assertTrue(self.notifier.send(ups_alert()))
        self.assertTrue(self.notifier.send(messages.test_alert()))
        self.assertEqual((Alerts.SLOW_SHOW_SECONDS, Alerts.SLOW_PAUSE_SECONDS), (2, 60))


class FakeResult:
    def __init__(self, value=None, error=None):
        self.value, self.error = value, error


class FakeConnection:
    def __init__(self, owned=False, activatable=(), error=None):
        self.owned, self.activatable, self.error = owned, activatable, error
        self.methods = []

    def call(self, bus, path, interface, method, parameters, reply, flags, timeout,
             cancellable, callback, *data):
        self.methods.append((bus, method, None if parameters is None else parameters.unpack()))
        value = (self.owned,) if method == "NameHasOwner" else (list(self.activatable),)
        callback(self, FakeResult(value, self.error), *data)

    def call_finish(self, result):
        if result.error is not None:
            raise result.error
        return GLib.Variant("(b)" if isinstance(result.value[0], bool) else "(as)", result.value)


class Probe(unittest.TestCase):
    def probe(self, connection):
        changes = []
        notifier = Notifier(lambda page: None, load=FakeNotify, connection=lambda: connection)
        notifier.subscribe(changes.append)
        notifier.probe()
        return notifier, changes, connection

    def test_owned_name_is_available(self):
        notifier, changes, connection = self.probe(FakeConnection(owned=True))
        self.assertEqual((notifier.status, changes), (AVAILABLE, [AVAILABLE]))
        self.assertEqual(connection.methods,
                         [("org.freedesktop.DBus", "NameHasOwner", ("org.freedesktop.Notifications",))])

    def test_activatable_service_is_available(self):
        notifier, _, connection = self.probe(FakeConnection(
            activatable=("org.freedesktop.Notifications",)))
        self.assertEqual(notifier.status, AVAILABLE)
        self.assertEqual([method for _, method, _ in connection.methods],
                         ["NameHasOwner", "ListActivatableNames"])

    def test_missing_service_is_unavailable_and_explained(self):
        notifier, changes, _ = self.probe(FakeConnection())
        self.assertEqual((notifier.status, changes), (UNAVAILABLE, [UNAVAILABLE]))
        self.assertIn("通知服務", notifier.status_text())
        self.assertEqual(Notifier(lambda page: None).status_text(), "")

    def test_bus_errors_leave_the_status_unknown(self):
        notifier, changes, _ = self.probe(FakeConnection(error=GLib.Error("Timeout")))
        self.assertEqual((notifier.status, changes), (UNKNOWN, []))

    def test_without_a_bus_nothing_happens(self):
        notifier = Notifier(lambda page: None, connection=lambda: None)
        notifier.probe()
        self.assertEqual(notifier.status, UNKNOWN)

    def test_listeners_only_hear_changes(self):
        changes = []
        module = FakeNotify()
        notifier = Notifier(lambda page: None, load=lambda: module)
        notifier.subscribe(changes.append)
        notifier.send(ups_alert())
        notifier.send(ups_alert())
        self.assertEqual(changes, [AVAILABLE])


class Loading(unittest.TestCase):
    def test_real_loader_returns_the_typelib_without_initialising(self):
        module = notifier_module.load_notify()
        self.assertTrue(hasattr(module, "Notification"))


if __name__ == "__main__":
    unittest.main()
