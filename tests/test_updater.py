"""一鍵更新的協調：排程（15 秒、6 小時、24 小時的閘門，假時鐘）、每個版本只通知一次、
Beta 只在手動檢查時列出、檢查失敗，以及安裝流程（pkexec 由假的執行器代替）。"""
import json
from pathlib import Path
import unittest

from sdm.constants import Update
from sdm.core.update_logic import AppVersion
from sdm.data.github import FetchError
from sdm.services import updater as module
from sdm.services.update_install import Prepared, UpdateError
from sdm.services.updater import (AVAILABLE, CHECKING, DOWNLOADING, FAILED, IDLE, INSTALLING,
                                  KIND_DEB, KIND_SOURCE, LATEST, RESTARTING, UpdateMemory)
from tests.fixtures.signing import private_dir
from tests.test_update_logic import entry, listing

HOUR = 3600
KEYS = ("a" * 43 + "=",)


class Done:
    """立即完成的 Future。"""

    def __init__(self, function):
        try:
            self.value, self.error = function(), None
        except Exception as error:      # 與真正的 Future 一樣保留例外
            self.value, self.error = None, error

    def result(self):
        if self.error is not None:
            raise self.error
        return self.value

    def add_done_callback(self, callback):
        callback(self)


class Scheduler:
    def __init__(self):
        self.timers, self.cancelled, self.next = {}, [], 1

    def schedule(self, seconds, callback):
        self.next += 1
        self.timers[self.next] = (seconds, callback)
        return self.next

    def cancel(self, source):
        self.cancelled.append(source)
        self.timers.pop(source, None)

    def fire(self, seconds):
        """觸發間隔為 seconds 的計時器；回傳 False 的計時器移除（與 GLib 相同）。"""
        for source, (interval, callback) in list(self.timers.items()):
            if interval == seconds and not callback():
                self.timers.pop(source, None)


class Runner:
    def __init__(self):
        self.calls = []

    def run(self, argv, timeout, done):
        self.calls.append((argv, timeout, done))

    def finish(self, status, output):
        self.calls[-1][2](status, output)


class Hooks:
    def __init__(self):
        self.notified, self.installed, self.changes = [], 0, 0
        self.auto, self.notify_enabled = True, True

    def preferences(self):
        class Prefs:
            auto_check_updates = self.auto
            update_notification = self.notify_enabled
        return Prefs()


class Base(unittest.TestCase):
    def setUp(self):
        self.now = 1_000_000.0
        self.scheduler, self.runner, self.hooks = Scheduler(), Runner(), Hooks()
        self.responses = [listing(entry("v0.3.0"), entry("v0.4.0-beta.1", prerelease=True))]
        self.fetched, self.prepared, self.cleaned = 0, [], []
        self.prepare_error = None
        self.memory = UpdateMemory(None)

    def fetch(self):
        self.fetched += 1
        response = self.responses[min(self.fetched, len(self.responses)) - 1]
        if isinstance(response, Exception):
            raise response
        return response

    def prepare(self, release):
        self.prepared.append(release)
        if self.prepare_error:
            raise UpdateError(self.prepare_error)
        base = Path("/run/user/1000/sdm-update-x")
        return Prepared(base, base/release.asset_name, base/"SHA256SUMS",
                        base/"SHA256SUMS.sig", release.debian_version)

    def make(self, kind=KIND_DEB, keys=KEYS, current="0.2.0"):
        services = module.Services(
            fetch=self.fetch, prepare=self.prepare, submit=Done, on_main=lambda f, *a: f(*a),
            schedule=self.scheduler.schedule, cancel=self.scheduler.cancel, runner=self.runner,
            clock=lambda: self.now, cleanup=self.cleaned.append)
        hooks = module.Hooks(on_change=self.changed, notify=self.hooks.notified.append,
                             installed=self.installed, preferences=self.hooks.preferences)
        self.updater = module.Updater(services, hooks, AppVersion.current(current), kind,
                                      keys=keys, memory=self.memory, root=Path("/opt/sdm"))
        return self.updater

    def changed(self):
        self.hooks.changes += 1

    def installed(self):
        self.hooks.installed += 1


class Schedule(Base):
    def test_first_check_after_15_seconds_then_every_6_hours(self):
        self.make().start()
        self.assertEqual([seconds for seconds, _ in self.scheduler.timers.values()], [15])
        self.assertEqual(self.fetched, 0)
        self.scheduler.fire(15)
        self.assertEqual(self.fetched, 1)
        self.assertEqual([seconds for seconds, _ in self.scheduler.timers.values()], [6 * HOUR])
        self.assertEqual(self.memory.last_check, self.now)

    def test_api_is_contacted_only_after_24_hours(self):
        self.make().start()
        self.scheduler.fire(15)
        for hours, expected in ((6, 1), (12, 1), (18, 1), (24, 1), (24.01, 2), (30, 2),
                                (48.02, 3)):
            with self.subTest(hours=hours):
                self.now = 1_000_000.0 + hours * HOUR
                self.scheduler.fire(6 * HOUR)
                self.assertEqual(self.fetched, expected)

    def test_last_check_survives_a_restart(self):
        self.memory.last_check = self.now - 2 * HOUR
        self.make().start()
        self.scheduler.fire(15)
        self.assertEqual(self.fetched, 0)
        self.now += 23 * HOUR
        self.scheduler.fire(6 * HOUR)
        self.assertEqual(self.fetched, 1)

    def test_clock_moving_backwards_makes_a_check_due(self):
        self.memory.last_check = self.now + 10 * HOUR
        self.make().start()
        self.scheduler.fire(15)
        self.assertEqual(self.fetched, 1)

    def test_disabled_automatic_checks_never_contact_github(self):
        self.hooks.auto = False
        updater = self.make()
        updater.start()
        self.scheduler.fire(15)
        self.now += 100 * HOUR
        self.scheduler.fire(6 * HOUR)
        self.assertEqual(self.fetched, 0)
        updater.check_now()
        self.assertEqual(self.fetched, 1)

    def test_failed_checks_are_retried_on_the_next_tick(self):
        self.responses = [FetchError("無法連線到 GitHub"), listing(entry("v0.3.0"))]
        updater = self.make()
        updater.start()
        self.scheduler.fire(15)
        self.assertIsNone(self.memory.last_check)
        self.assertEqual((updater.state.phase, updater.state.error), (IDLE, ""))
        self.now += 6 * HOUR
        self.scheduler.fire(6 * HOUR)
        self.assertEqual(self.fetched, 2)
        self.assertEqual(updater.state.phase, AVAILABLE)

    def test_stop_cancels_timers_and_ignores_late_results(self):
        updater = self.make()
        updater.start()
        updater.stop()
        self.assertEqual(self.scheduler.timers, {})
        changes = self.hooks.changes
        updater.check_now()
        self.assertEqual((self.fetched, self.hooks.changes), (0, changes))


class Notifications(Base):
    def test_notify_once_per_version(self):
        self.responses = [listing(entry("v0.3.0")), listing(entry("v0.3.0")),
                          listing(entry("v0.3.1"), entry("v0.3.0"))]
        self.make().start()
        self.scheduler.fire(15)
        self.assertEqual([release.tag for release in self.hooks.notified], ["v0.3.0"])
        for _ in range(2):
            self.now += 25 * HOUR
            self.scheduler.fire(6 * HOUR)
        self.assertEqual([release.tag for release in self.hooks.notified], ["v0.3.0", "v0.3.1"])
        self.assertEqual(self.memory.last_notified, "v0.3.1")

    def test_notification_switch(self):
        self.hooks.notify_enabled = False
        self.make().start()
        self.scheduler.fire(15)
        self.assertEqual(self.hooks.notified, [])
        self.assertEqual(self.updater.state.phase, AVAILABLE)

    def test_manual_checks_do_not_notify_but_count_as_seen(self):
        self.responses = [listing(entry("v0.3.0"))]
        updater = self.make()
        updater.check_now()
        self.assertEqual(self.hooks.notified, [])
        self.assertEqual(self.memory.last_notified, "v0.3.0")
        self.now += 25 * HOUR
        updater.start()
        self.scheduler.fire(15)
        self.assertEqual(self.hooks.notified, [])

    def test_automatic_checks_ignore_betas(self):
        self.responses = [listing(entry("v0.3.0-beta.1", prerelease=True), entry("v0.2.0"))]
        updater = self.make()
        updater.start()
        self.scheduler.fire(15)
        self.assertEqual(updater.state.phase, LATEST)
        self.assertEqual(self.hooks.notified, [])
        updater.check_now()
        self.assertEqual(updater.state.phase, AVAILABLE)
        self.assertEqual(updater.state.release.tag, "v0.3.0-beta.1")
        self.assertEqual(self.hooks.notified, [])
        self.assertIsNone(self.memory.last_notified)        # Beta 不算通知過的版本

    def test_memory_file(self):
        path = private_dir(self)/"update-state.json"
        memory = UpdateMemory(path)
        memory.save(last_check=123.5, last_notified="v0.3.0")
        again = UpdateMemory(path)
        self.assertEqual((again.last_check, again.last_notified), (123.5, "v0.3.0"))
        for text in ("garbage", json.dumps({"last_check": "x", "last_notified": 5}),
                     json.dumps({"last_check": float("nan")}), json.dumps([1])):
            with self.subTest(text=text):
                path.write_text(text)
                broken = UpdateMemory(path)
                self.assertEqual((broken.last_check, broken.last_notified), (None, None))


class Checks(Base):
    def test_manual_check_states(self):
        updater = self.make(current="0.3.0")
        self.responses = [listing(entry("v0.3.0"))]
        updater.check_now()
        self.assertEqual(updater.state.phase, LATEST)
        self.assertEqual(updater.state.last_check, self.now)

    def test_manual_failures_are_shown(self):
        self.responses = [FetchError("無法連線到 GitHub"), b"[]"]
        updater = self.make()
        updater.check_now()
        self.assertEqual(updater.state.phase, FAILED)
        self.assertEqual(updater.state.error, "檢查失敗：無法連線到 GitHub")
        updater.check_now()
        self.assertEqual(updater.state.phase, FAILED)
        self.assertTrue(updater.state.error.startswith("檢查失敗："))

    def test_only_one_check_at_a_time(self):
        pending = []
        updater = self.make()
        updater.services = updater.services._replace(submit=lambda f: pending.append(f) or Pending())
        updater.check_now()
        updater.check_now()
        self.assertEqual(len(pending), 1)
        self.assertEqual(updater.state.phase, CHECKING)


class Pending:
    def add_done_callback(self, callback):
        return None


class Install(Base):
    def available(self, **options):
        updater = self.make(**options)
        updater.check_now()
        self.assertEqual(updater.state.phase, AVAILABLE)
        return updater

    def test_successful_install_restarts(self):
        updater = self.available()
        self.assertTrue(updater.installable)
        updater.install()
        self.assertEqual(updater.state.phase, INSTALLING)
        argv, timeout, _ = self.runner.calls[-1]
        base = "/run/user/1000/sdm-update-x/"
        self.assertEqual(argv, [Update.PKEXEC, Update.HELPER, "install",
                                base + "system-device-monitor_0.3.0_all.deb",
                                base + "SHA256SUMS", base + "SHA256SUMS.sig"])
        self.assertEqual(timeout, 300)
        self.runner.finish(0, '{"ok":true,"action":"install","version":"0.3.0"}\n')
        self.assertEqual(updater.state.phase, RESTARTING)
        self.assertEqual(self.hooks.installed, 1)
        self.assertEqual(self.cleaned, [Path("/run/user/1000/sdm-update-x")])

    def test_source_checkouts_never_install(self):
        updater = self.available(kind=KIND_SOURCE)
        self.assertFalse(updater.installable)
        updater.install()
        self.assertEqual((self.prepared, self.runner.calls), ([], []))

    def test_missing_keys_refuse_every_install(self):
        updater = self.available(keys=())
        self.assertFalse(updater.keys_configured)
        self.assertFalse(updater.installable)
        updater.install()
        self.assertEqual(updater.state.error, "安裝失敗：此版本未設定更新簽章金鑰，無法一鍵更新；"
                                              "請到 GitHub 下載新版。")
        self.assertEqual((self.prepared, self.runner.calls), ([], []))

    def test_unsigned_release(self):
        self.responses = [listing(entry("v0.3.0", names=("system-device-monitor_0.3.0_all.deb",)))]
        updater = self.available()
        self.assertFalse(updater.installable)
        updater.install()
        self.assertTrue(updater.state.error.startswith("安裝失敗："))
        self.assertEqual(self.runner.calls, [])

    def test_verification_failures_keep_the_release(self):
        self.prepare_error = "簽章驗證失敗，已停止更新。"
        updater = self.available()
        updater.install()
        self.assertEqual(updater.state.phase, AVAILABLE)
        self.assertEqual(updater.state.error, "安裝失敗：簽章驗證失敗，已停止更新。")
        self.assertEqual(self.runner.calls, [])

    def test_helper_failures(self):
        cases = ((126, "", "未授權"), (127, "", "未授權"), (None, "", "逾時"),
                 (3, '{"ok":false,"error":"failed","reason":"signature"}', "簽章"),
                 (3, '{"ok":false,"error":"failed","reason":"busy"}', "套件管理程式"),
                 (3, '{"ok":false,"error":"failed","reason":"apt-timeout"}',
                  "sudo dpkg --configure -a"),
                 (3, '{"ok":false,"error":"failed","reason":"no-keys"}', "簽章金鑰"),
                 (3, '{"ok":false,"error":"failed","reason":"other"}', "系統日誌"),
                 (2, "", "重新安裝"), (4, "", "沒有權限"),
                 (0, '{"ok":true,"action":"install","version":"0.2.9"}', "版本"),
                 (0, "not json", "版本"))
        for status, output, words in cases:
            with self.subTest(status=status, output=output):
                updater = self.available()
                updater.install()
                self.runner.finish(status, output)
                self.assertEqual(updater.state.phase, AVAILABLE)
                self.assertIn(words, updater.state.error)
                self.assertEqual(self.hooks.installed, 0)
        self.assertEqual(len(self.cleaned), len(cases))

    def test_busy_states_block_checks_and_installs(self):
        pending = []
        updater = self.available()
        updater.services = updater.services._replace(submit=lambda f: pending.append(f) or Pending())
        updater.install()
        self.assertEqual(updater.state.phase, DOWNLOADING)
        updater.check_now()
        updater.install()
        self.assertEqual(len(pending), 1)


if __name__ == "__main__":
    unittest.main()
