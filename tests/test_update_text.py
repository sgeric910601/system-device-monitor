"""設定 → 更新的文字、更新通知與示範模式的假狀態（不載入 GTK、不連網路）。"""
import dataclasses
from pathlib import Path
import time
import unittest

from sdm.core.update_logic import AppVersion, ReleaseInfo
from sdm.services import update_demo
from sdm.services.updater import (AVAILABLE, CHECKING, DOWNLOADING, FAILED, IDLE, INSTALLING,
                                  KIND_DEB, KIND_SOURCE, LATEST, RESTARTING, UpdateState)
from sdm.ui import update_text as text

RELEASE = ReleaseInfo(tag="v0.3.0", numbers=(0, 3, 0), page_url="https://github.com/x",
                      asset_name="system-device-monitor_0.3.0_all.deb",
                      asset_url="https://github.com/x.deb", asset_size=1,
                      checksum_url="https://github.com/s", signature_url="https://github.com/g",
                      notes="## 新增\n- 一鍵更新\x1b[0m")
BETA = dataclasses.replace(RELEASE, tag="v0.4.0-beta.2", numbers=(0, 4, 0), beta=2)


class Status(unittest.TestCase):
    def test_every_phase_has_a_status(self):
        cases = {IDLE: "尚未檢查", CHECKING: "檢查中…", LATEST: "已是最新版本",
                 AVAILABLE: "有新版本 0.3.0", DOWNLOADING: "正在下載並驗證 0.3.0…",
                 INSTALLING: "等待管理員授權並安裝…", RESTARTING: "已安裝，正在重新啟動…"}
        for phase, expected in cases.items():
            with self.subTest(phase=phase):
                self.assertEqual(text.status_text(UpdateState(phase=phase, release=RELEASE)),
                                 expected)
        failed = UpdateState(phase=FAILED, error="檢查失敗：無法連線到 GitHub")
        self.assertEqual(text.status_text(failed), "檢查失敗：無法連線到 GitHub")
        self.assertEqual(text.status_text(UpdateState(phase=AVAILABLE, release=BETA)),
                         "有新版本 0.4.0 beta 2（測試版）")

    def test_errors_are_shown_separately_unless_the_check_failed(self):
        self.assertEqual(text.error_text(UpdateState(phase=AVAILABLE, error="安裝失敗：x")),
                         "安裝失敗：x")
        self.assertEqual(text.error_text(UpdateState(phase=FAILED, error="檢查失敗：x")), "")

    def test_version_and_kind(self):
        self.assertEqual(text.version_line(AppVersion((0, 2, 0)), KIND_DEB),
                         "目前版本 0.2.0（.deb 套件）")
        self.assertEqual(text.version_line(AppVersion((0, 3, 0), 1), KIND_SOURCE),
                         "目前版本 0.3.0 beta 1（原始碼資料夾）")

    def test_missing_keys_only_matter_for_deb_installs(self):
        self.assertIn("未設定簽章金鑰", text.keys_text(KIND_DEB, False))
        self.assertEqual(text.keys_text(KIND_DEB, True), "")
        self.assertEqual(text.keys_text(KIND_SOURCE, False), "")

    def test_last_check(self):
        self.assertEqual(text.last_check_text(None), "還沒有檢查過")
        stamp = time.mktime((2026, 10, 24, 15, 30, 0, 0, 0, -1))
        self.assertEqual(text.last_check_text(stamp), "上次檢查：2026-10-24 15:30")

    def test_release_details_and_notes(self):
        self.assertTrue(text.show_release(UpdateState(phase=AVAILABLE, release=RELEASE)))
        self.assertTrue(text.show_release(UpdateState(phase=INSTALLING, release=RELEASE)))
        self.assertFalse(text.show_release(UpdateState(phase=LATEST)))
        self.assertEqual(text.notes_text(UpdateState(phase=AVAILABLE, release=RELEASE)),
                         "新增\n・一鍵更新[0m")
        self.assertEqual(text.notes_text(UpdateState(phase=LATEST)), "")

    def test_source_commands_are_quoted(self):
        root = Path("/opt/checkout with 'quote")
        self.assertEqual(text.git_command(root),
                         "git -C '/opt/checkout with '\"'\"'quote' pull --ff-only")
        self.assertTrue(text.reinstall_command(root).endswith("--system-components install"))


class HelperReminder(unittest.TestCase):
    def test_source_installs_with_an_outdated_helper(self):
        from sdm.services.privileged import KIND_LOCAL, AdminState, Layout
        local = Layout(KIND_LOCAL, "/usr/local/libexec/x", "/run/x")
        self.assertTrue(text.helper_mismatch(KIND_SOURCE, AdminState(local, False)))
        self.assertFalse(text.helper_mismatch(KIND_SOURCE, AdminState(local, True)))
        self.assertFalse(text.helper_mismatch(KIND_SOURCE, AdminState(local, None)))
        self.assertFalse(text.helper_mismatch(KIND_SOURCE, AdminState()))
        self.assertFalse(text.helper_mismatch(KIND_SOURCE, None))
        self.assertFalse(text.helper_mismatch(KIND_DEB, AdminState(local, False)))


class Alert(unittest.TestCase):
    def test_update_notification_opens_the_update_settings(self):
        alert = text.update_alert(RELEASE)
        self.assertEqual(alert.page, "settings:update")
        self.assertEqual(alert.title, "有新版本：0.3.0")
        self.assertFalse(alert.critical)
        self.assertIn("設定 → 更新", alert.body)


class Demo(unittest.TestCase):
    def test_each_scenario_has_a_state_without_network(self):
        changes = []
        expected = {"desktop": (AVAILABLE, KIND_DEB, True),
                    "laptop-battery": (LATEST, KIND_DEB, True),
                    "laptop-charging": (AVAILABLE, KIND_DEB, False),
                    "laptop-limit": (AVAILABLE, KIND_SOURCE, True)}
        for scenario, (phase, kind, keys) in expected.items():
            with self.subTest(scenario=scenario):
                demo = update_demo.demo_updater(scenario, lambda: changes.append(1), "0.2.0")
                self.assertEqual((demo.state.phase, demo.kind, demo.keys_configured),
                                 (phase, kind, keys))
                self.assertEqual(demo.current, AppVersion((0, 2, 0)))
                self.assertEqual(demo.installable, scenario == "desktop")

    def test_demo_release_is_newer_than_the_current_version(self):
        demo = update_demo.demo_updater("desktop", lambda: None, "1.9.4")
        self.assertEqual(demo.state.release.tag, "v1.10.0")
        self.assertTrue(demo.state.release.notes)

    def test_failed_variant(self):
        demo = update_demo.DemoUpdater("failed", lambda: None, "0.2.0")
        self.assertEqual(demo.state.phase, FAILED)
        self.assertTrue(demo.state.error.startswith("檢查失敗："))

    def test_buttons_change_only_memory(self):
        changes = []
        demo = update_demo.demo_updater("desktop", lambda: changes.append(1), "0.2.0")
        demo.check_now()
        demo.install()
        self.assertIn("示範模式", demo.state.error)
        self.assertGreaterEqual(len(changes), 2)
        demo.start()
        demo.stop()


if __name__ == "__main__":
    unittest.main()
