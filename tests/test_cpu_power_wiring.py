"""看板的 CPU 功耗接線（sdm.ui.cpu_power_wiring）：只在需要時連線、依更新頻率請求、
偏好與實際狀態不符時恢復，以及開啟程式時絕不要求授權。

以假的管理員元件、假的客戶端與立即執行的假執行緒池代替；設定使用真正的 SettingsStore
（不寫檔）。
"""
from pathlib import Path
from types import SimpleNamespace
import unittest

from sdm.constants import CpuPower
from sdm.core.schedule import cadence
from sdm.core.settings import LEGACY_DEFAULTS, Preferences
from sdm.services.privileged import AdminState, Layout, SOCKET_ABSENT, SOCKET_MINE
from sdm.services.settings_store import SettingsStore
from sdm.ui import admin_text
from sdm.ui.cpu_power_wiring import CPU_POWER, PREFERENCE, CpuPowerMixin

LOCAL = Layout("local", CpuPower.LOCAL_HELPER, CpuPower.LOCAL_SOCKET)
READY = AdminState(LOCAL, True, SOCKET_ABSENT)
ENABLED = AdminState(LOCAL, True, SOCKET_MINE)


class FakeClient:
    def __init__(self):
        self.path, self.active, self.requests = None, False, 0

    def configure(self, path):
        self.path = path

    def set_active(self, active):
        self.active = active

    def request(self):
        self.requests += 1


class FakeAdmin:
    def __init__(self, state):
        self.state, self.calls, self.stopped, self.socket_checks = state, [], False, 0
        self.root = Path("/opt/system-device-monitor")

    def set_enabled(self, wanted, done):
        self.calls.append((wanted, done))

    def update_socket(self):
        self.socket_checks += 1

    def report(self, text):
        self.state = AdminState(self.state.layout, self.state.protocol, self.state.socket,
                                self.state.busy, text)

    def stop(self):
        self.stopped = True


class Future:
    def __init__(self, function):
        self.function = function

    def add_done_callback(self, callback):
        self.function()
        callback(self)

    def result(self):
        return None


class Pool:
    def __init__(self):
        self.submitted = 0

    def submit(self, function):
        self.submitted += 1
        return Future(function)


class Host(CpuPowerMixin):
    def __init__(self, state=READY, preference=False, visible=True):
        self.admin = FakeAdmin(state)
        self.collector = SimpleNamespace(cpu_power=FakeClient())
        prefs = Preferences.defaults().with_changes(admin_cpu_sampling=preference)
        self.store = SettingsStore(LEGACY_DEFAULTS, prefs, None, None, lambda ms, cb: 0,
                                   lambda source: None, persist=False)
        self.store.subscribe(self.changed)
        self.preferences = self.store.preferences
        self.cadence = cadence(2, False)
        self.busy, self.closing, self.settings_window = set(), False, None
        self.pool, self.timers, self.next_id = Pool(), {}, 1
        self.visible = {"power"} if visible else set()

    def changed(self, keys, store):
        self.preferences = store.preferences
        if PREFERENCE in keys:
            self.cpu_power_preference_changed()

    def visible_pages(self):
        return self.visible

    def schedule(self, seconds, callback):
        self.next_id += 1
        self.timers[self.next_id] = (seconds, callback)
        return self.next_id

    def remove_source(self, source):
        self.timers.pop(source, None)

    def on_main(self, function, *args):
        function(*args)

    @property
    def client(self):
        return self.collector.cpu_power


class Activity(unittest.TestCase):
    def test_requests_only_when_everything_allows_it(self):
        cases = ((ENABLED, True, True, True), (READY, True, True, False),
                 (ENABLED, False, True, False), (ENABLED, True, False, False),
                 (AdminState(LOCAL, False, SOCKET_MINE), True, True, False),
                 (AdminState(), True, True, False))
        for state, preference, visible, expected in cases:
            with self.subTest(state=state, preference=preference, visible=visible):
                host = Host(state, preference, visible)
                host.update_cpu_power()
                self.assertEqual(host.client.active, expected)
                self.assertEqual(bool(host.timers), expected)
                self.assertEqual(host.client.requests, 1 if expected else 0)

    def test_client_points_at_the_installed_socket(self):
        host = Host(ENABLED, True)
        host.update_cpu_power()
        self.assertEqual(host.client.path, CpuPower.LOCAL_SOCKET)
        host.admin.state = AdminState()
        host.update_cpu_power()
        self.assertIsNone(host.client.path)

    def test_timer_follows_the_refresh_interval(self):
        host = Host(ENABLED, True)
        host.update_cpu_power()
        self.assertEqual([seconds for seconds, _ in host.timers.values()], [2])
        host.cadence = cadence(10, False)
        host.update_cpu_power()
        self.assertEqual([seconds for seconds, _ in host.timers.values()], [10])
        _, tick = next(iter(host.timers.values()))
        self.assertTrue(tick())
        self.assertEqual(host.client.requests, 3)

    def test_hiding_the_page_stops_and_closes(self):
        host = Host(ENABLED, True)
        host.update_cpu_power()
        host.visible = set()
        host.update_cpu_power()
        self.assertFalse(host.client.active)
        self.assertEqual(host.timers, {})
        self.assertEqual(host.client.requests, 2)       # 最後一次請求負責關閉連線

    def test_requests_never_overlap(self):
        host = Host(ENABLED, True)
        host.busy.add(CPU_POWER)
        host.update_cpu_power()
        self.assertEqual(host.pool.submitted, 0)

    def test_closing_stops_everything(self):
        host = Host(ENABLED, True)
        host.update_cpu_power()
        host.stop_cpu_power()
        self.assertEqual(host.timers, {})
        self.assertFalse(host.client.active)
        self.assertTrue(host.admin.stopped)
        host.closing = True
        host.update_cpu_power()
        self.assertFalse(host.client.active)

    def test_socket_state_is_rechecked(self):
        host = Host(ENABLED, True)
        host.update_cpu_power()
        self.assertGreaterEqual(host.admin.socket_checks, 1)


class Preference(unittest.TestCase):
    def test_turning_on_asks_for_authorization(self):
        host = Host(READY)
        host.store.update(admin_cpu_sampling=True)
        self.assertEqual([wanted for wanted, _ in host.admin.calls], [True])

    def test_refused_authorization_turns_the_option_back_off(self):
        host = Host(READY)
        host.store.update(admin_cpu_sampling=True)
        host.admin.calls[0][1](False)
        self.assertFalse(host.store.value(PREFERENCE))
        self.assertEqual(len(host.admin.calls), 1)

    def test_successful_enable_starts_sampling(self):
        host = Host(READY)
        host.store.update(admin_cpu_sampling=True)
        host.admin.state = ENABLED
        host.admin.calls[0][1](True)
        self.assertTrue(host.store.value(PREFERENCE))
        self.assertTrue(host.client.active)

    def test_turning_off_asks_for_authorization_and_reverts_on_failure(self):
        host = Host(ENABLED, True)
        host.store.update(admin_cpu_sampling=False)
        self.assertFalse(host.client.active)
        self.assertEqual([wanted for wanted, _ in host.admin.calls], [False])
        host.admin.calls[0][1](False)
        self.assertTrue(host.store.value(PREFERENCE))

    def test_matching_state_needs_no_authorization(self):
        host = Host(ENABLED, False)
        host.store.update(admin_cpu_sampling=True)
        host = Host(READY, True)
        host.store.update(admin_cpu_sampling=False)
        self.assertEqual(host.admin.calls, [])


class Startup(unittest.TestCase):
    def test_waits_for_the_protocol_check(self):
        host = Host(AdminState(LOCAL, None, SOCKET_ABSENT), True)
        host.admin_changed()
        self.assertTrue(host.store.value(PREFERENCE))

    def test_option_left_on_without_the_service_is_turned_off_without_asking(self):
        for state in (READY, AdminState(), AdminState(LOCAL, False, SOCKET_MINE)):
            with self.subTest(state=state):
                host = Host(state, True)
                host.admin_changed()
                self.assertFalse(host.store.value(PREFERENCE))
                self.assertEqual(host.admin.calls, [])
                self.assertEqual(host.admin.state.error, admin_text.STARTUP_OFF)

    def test_running_service_keeps_the_option(self):
        host = Host(ENABLED, True)
        host.admin_changed()
        self.assertTrue(host.store.value(PREFERENCE))
        self.assertTrue(host.client.active)

    def test_checked_only_once(self):
        host = Host(ENABLED, True)
        host.admin_changed()
        host.admin.state = READY
        host.admin_changed()
        self.assertTrue(host.store.value(PREFERENCE))


if __name__ == "__main__":
    unittest.main()
