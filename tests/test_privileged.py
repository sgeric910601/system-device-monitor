"""管理員元件（sdm.services.privileged）：探索、信任檢查、通訊協定、pkexec 結束碼與開關流程。

檔案系統以注入的 lstat 代替；pkexec 以假的執行器代替，只有 HelperRunner 的測試
在 GLib 主迴圈中執行暫存目錄裡的假程式（不會執行 pkexec）。
"""
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest

from gi.repository import GLib

from sdm.constants import CpuPower
from sdm.services import privileged
from sdm.services.privileged import (AdminComponents, HelperRunner, discover, failure_text,
                                     parse_protocol, parse_result, socket_state, trusted_path)

ME = 1000
PROTOCOL_OK = '{"protocol":1,"version":"1.0"}\n'


def entry(kind="file", uid=0, mode=None):
    bits = {"file": stat.S_IFREG | 0o755, "dir": stat.S_IFDIR | 0o755,
            "link": stat.S_IFLNK | 0o777, "socket": stat.S_IFSOCK | 0o600}[kind]
    return SimpleNamespace(st_mode=(bits & ~0o7777) | mode if mode is not None else bits,
                           st_uid=uid)


def fake_tree(*files):
    """所有上層目錄都是 root 0755；files 為存在的一般檔案；之後可直接修改 lstat.entries。"""
    entries = {}
    for name in files:
        path = Path(name)
        entries[str(path)] = entry("file")
        for parent in path.parents:
            entries[str(parent)] = entry("dir")

    def lstat(path):
        try:
            return entries[str(path)]
        except KeyError:
            raise FileNotFoundError(path) from None
    lstat.entries = entries
    return lstat


class Trust(unittest.TestCase):
    path = CpuPower.DEB_HELPER

    def check(self, change=None):
        lstat = fake_tree(self.path)
        lstat.entries.update(change or {})
        return trusted_path(self.path, lstat)

    def test_root_owned_helper_is_trusted(self):
        self.assertTrue(self.check())

    def test_untrusted_cases(self):
        directory = CpuPower.DEB_HELPER_DIR
        cases = ({self.path: entry("link")}, {self.path: entry("file", uid=ME)},
                 {self.path: entry("file", mode=0o775)}, {self.path: entry("file", mode=0o757)},
                 {self.path: entry("dir")}, {directory: entry("dir", mode=0o775)},
                 {directory: entry("dir", uid=ME)}, {"/usr": entry("dir", mode=0o777)},
                 {"/": entry("dir", uid=ME)}, {directory: entry("link")})
        for change in cases:
            with self.subTest(change=change):
                self.assertFalse(self.check(change))

    def test_missing_or_relative_paths_are_not_trusted(self):
        self.assertFalse(trusted_path(self.path, fake_tree()))
        self.assertFalse(trusted_path("usr/libexec/x", fake_tree("/usr/libexec/x")))


class Discovery(unittest.TestCase):
    def test_package_comes_first(self):
        found = discover(fake_tree(CpuPower.DEB_HELPER, CpuPower.LOCAL_HELPER))
        self.assertEqual((found.kind, found.helper, found.socket),
                         ("deb", CpuPower.DEB_HELPER, CpuPower.DEB_SOCKET))

    def test_source_install_when_there_is_no_package(self):
        found = discover(fake_tree(CpuPower.LOCAL_HELPER))
        self.assertEqual((found.kind, found.socket), ("local", CpuPower.LOCAL_SOCKET))

    def test_untrusted_package_helper_is_skipped(self):
        lstat = fake_tree(CpuPower.DEB_HELPER, CpuPower.LOCAL_HELPER)
        lstat.entries[CpuPower.DEB_HELPER] = entry("file", uid=ME)
        self.assertEqual(discover(lstat).kind, "local")

    def test_nothing_installed(self):
        self.assertIsNone(discover(fake_tree()))


class Sockets(unittest.TestCase):
    def test_states(self):
        path = CpuPower.DEB_SOCKET
        cases = ((entry("socket", uid=ME), privileged.SOCKET_MINE),
                 (entry("socket", uid=ME + 1), privileged.SOCKET_OTHER),
                 (entry("file", uid=ME), privileged.SOCKET_ABSENT))
        for status, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(socket_state(path, lambda p, s=status: s, ME), expected)
        self.assertEqual(socket_state(path, fake_tree(), ME), privileged.SOCKET_ABSENT)


class Parsing(unittest.TestCase):
    def test_protocol(self):
        self.assertEqual(parse_protocol(PROTOCOL_OK), 1)
        self.assertEqual(parse_protocol('{"protocol":2,"version":"9"}'), 2)
        for text in ("", "garbage", "[]", '{"protocol":"1"}', '{"protocol":true}',
                     '{"version":"1"}', PROTOCOL_OK + "extra\n"):
            with self.subTest(text=text):
                self.assertIsNone(parse_protocol(text))

    def test_result(self):
        self.assertEqual(parse_result('{"ok":true,"action":"enable"}\n'),
                         {"ok": True, "action": "enable"})
        for text in ("", "nope", '["ok"]', '{"ok":"yes"}'):
            with self.subTest(text=text):
                self.assertIsNone(parse_result(text))

    def test_failure_texts(self):
        self.assertIn("未授權", failure_text(126, None))
        self.assertIn("未授權", failure_text(127, None))
        self.assertIn("逾時", failure_text(None, None))
        self.assertIn("參數", failure_text(2, {"ok": False, "error": "usage"}))
        self.assertIn("沒有權限", failure_text(4, None))
        self.assertIn("失敗", failure_text(3, {"ok": False, "error": "failed"}))
        self.assertIn("失敗", failure_text(-1, None))


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run(self, argv, timeout_s, done):
        self.calls.append((list(argv), timeout_s, done))

    def finish(self, status, output="", index=-1):
        self.calls[index][2](status, output)


class Components(unittest.TestCase):
    def setUp(self):
        self.lstat = fake_tree(CpuPower.DEB_HELPER)
        self.runner = FakeRunner()
        self.changes = 0
        self.admin = AdminComponents(self.runner, self.changed, lstat=self.lstat, uid=lambda: ME)
        self.results = []

    def changed(self):
        self.changes += 1

    def ready(self):
        self.admin.refresh()
        argv, timeout, _ = self.runner.calls[-1]
        self.assertEqual((argv, timeout), ([CpuPower.DEB_HELPER, "--protocol"],
                                           CpuPower.PROTOCOL_TIMEOUT))
        self.runner.finish(0, PROTOCOL_OK)
        self.assertTrue(self.admin.state.ready)

    def enable_socket(self, uid=ME):
        self.lstat.entries[CpuPower.DEB_SOCKET] = entry("socket", uid=uid)

    def test_refresh_reports_installation_and_protocol(self):
        self.admin.refresh()
        state = self.admin.state
        self.assertEqual((state.kind, state.protocol, state.enabled), ("deb", None, False))
        self.runner.finish(0, PROTOCOL_OK)
        self.assertTrue(self.admin.state.protocol)
        self.assertGreaterEqual(self.changes, 2)

    def test_protocol_mismatch_asks_for_reinstall(self):
        self.admin.refresh()
        self.runner.finish(0, '{"protocol":2,"version":"9"}')
        self.assertFalse(self.admin.state.protocol)
        self.assertIn("需要重新安裝管理員元件", self.admin.state.error)
        self.assertFalse(self.admin.state.ready)

    def test_unreadable_protocol_is_a_mismatch(self):
        self.admin.refresh()
        self.runner.finish(None, "")
        self.assertFalse(self.admin.state.protocol)

    def test_nothing_installed_runs_nothing(self):
        admin = AdminComponents(self.runner, self.changed, lstat=fake_tree(), uid=lambda: ME)
        admin.refresh()
        self.assertEqual((admin.state.kind, admin.state.ready), (None, False))
        self.assertEqual(self.runner.calls, [])

    def test_enable_runs_pkexec_and_reads_the_socket(self):
        self.ready()
        self.admin.set_enabled(True, self.results.append)
        argv, timeout, _ = self.runner.calls[-1]
        self.assertEqual((argv, timeout), ([CpuPower.PKEXEC, CpuPower.DEB_HELPER, "enable"],
                                           CpuPower.PKEXEC_TIMEOUT))
        self.assertTrue(self.admin.state.busy)
        self.enable_socket()
        self.runner.finish(0, '{"ok":true,"action":"enable"}\n')
        self.assertEqual(self.results, [True])
        self.assertEqual((self.admin.state.busy, self.admin.state.enabled, self.admin.state.error),
                         (False, True, ""))

    def test_cancelled_authorization(self):
        self.ready()
        self.admin.set_enabled(True, self.results.append)
        self.runner.finish(126)
        self.assertEqual(self.results, [False])
        self.assertIn("未授權", self.admin.state.error)
        self.assertFalse(self.admin.state.enabled)

    def test_success_without_our_socket_is_a_failure(self):
        self.ready()
        self.admin.set_enabled(True, self.results.append)
        self.runner.finish(0, '{"ok":true,"action":"enable"}\n')
        self.assertEqual(self.results, [False])
        self.assertTrue(self.admin.state.error)

    def test_disable(self):
        self.ready()
        self.enable_socket()
        self.admin.update_socket()
        self.admin.set_enabled(False, self.results.append)
        self.assertEqual(self.runner.calls[-1][0], [CpuPower.PKEXEC, CpuPower.DEB_HELPER,
                                                    "disable"])
        del self.lstat.entries[CpuPower.DEB_SOCKET]
        self.runner.finish(0, '{"ok":true,"action":"disable"}\n')
        self.assertEqual(self.results, [True])
        self.assertFalse(self.admin.state.enabled)

    def test_no_pkexec_when_the_state_already_matches(self):
        self.ready()
        calls = len(self.runner.calls)
        self.admin.set_enabled(False, self.results.append)
        self.enable_socket()
        self.admin.update_socket()
        self.admin.set_enabled(True, self.results.append)
        self.assertEqual(self.results, [True, True])
        self.assertEqual(len(self.runner.calls), calls)

    def test_socket_of_another_user_needs_enabling(self):
        self.ready()
        self.enable_socket(uid=ME + 1)
        self.admin.update_socket()
        self.assertEqual(self.admin.state.socket, privileged.SOCKET_OTHER)
        self.admin.set_enabled(True, self.results.append)
        self.assertEqual(self.runner.calls[-1][0][-1], "enable")

    def test_refused_while_busy_or_not_ready(self):
        admin = AdminComponents(self.runner, self.changed, lstat=fake_tree(), uid=lambda: ME)
        admin.refresh()
        admin.set_enabled(True, self.results.append)
        self.ready()
        self.admin.set_enabled(True, self.results.append)
        self.admin.set_enabled(True, self.results.append)
        self.assertEqual(self.results, [False, False])

    def test_late_result_after_stop_is_ignored(self):
        self.ready()
        self.admin.set_enabled(True, self.results.append)
        self.admin.stop()
        self.runner.finish(0, '{"ok":true,"action":"enable"}\n')
        self.assertEqual(self.results, [])


class Runner(unittest.TestCase):
    """真正的 Gio 子程序：暫存目錄裡的假程式，測試成功、結束碼與逾時。"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def program(self, body):
        path = self.dir/"fake-helper"
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)
        return str(path)

    def run_and_wait(self, argv, timeout_s, wait_s=5):
        """執行並等待回報（最多 wait_s 秒）；無法啟動時會在 run() 當下就回報。"""
        loop, results = GLib.MainLoop(), []

        def done(status, output):
            results.append((status, output))
            if loop.is_running():
                loop.quit()

        def give_up():
            loop.quit()
            return False
        HelperRunner().run(argv, timeout_s, done)
        if not results:
            guard = GLib.timeout_add_seconds(wait_s, give_up)
            loop.run()
            if results:
                GLib.source_remove(guard)
        return results

    def test_success_and_output(self):
        program = self.program('echo \'{"ok":true}\'\nexit 0\n')
        self.assertEqual(self.run_and_wait([program], 5), [(0, '{"ok":true}\n')])

    def test_exit_status(self):
        self.assertEqual(self.run_and_wait([self.program("exit 126\n")], 5), [(126, "")])

    def test_timeout_reports_none_and_kills(self):
        marker = self.dir/"finished"
        program = self.program(f"sleep 3\ntouch '{marker}'\n")
        self.assertEqual(self.run_and_wait([program], 1), [(None, "")])
        GLib.usleep(2500000)
        self.assertFalse(marker.exists())

    def test_missing_program(self):
        self.assertEqual(self.run_and_wait([str(self.dir/"missing")], 5), [(None, "")])


if __name__ == "__main__":
    unittest.main()
