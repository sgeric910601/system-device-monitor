"""看板的一鍵更新接線：模擬測試與示範模式絕不建立網路用戶端、設定視窗的註冊位置、
點通知開啟設定 → 更新，以及安裝後以 exec 重新啟動。"""
import dataclasses
from pathlib import Path
import unittest
from unittest import mock

from sdm.constants import Update
from sdm.services.update_demo import DemoUpdater
from sdm.services.update_runner import PatientRunner
from sdm.services.updater import KIND_DEB, KIND_SOURCE, Updater
from sdm.ui import settings_window, smoke, update_wiring
from sdm.ui.alert_wiring import AlertMixin
from sdm.ui.settings import update as section


class App(update_wiring.UpdateMixin):
    def __init__(self, smoke_test=False, preview=None):
        self.smoke, self.preview = smoke_test, preview
        self.settings_window, self.notifier = None, None
        self.stopped, self.shown = 0, True

    def stop(self):
        self.stopped += 1

    def window_shown(self):
        return self.shown


class NoNetwork(unittest.TestCase):
    def test_smoke_tests_and_previews_never_create_a_client(self):
        refuse = mock.Mock(side_effect=AssertionError("network client created"))
        with mock.patch.object(update_wiring, "GitHubClient", refuse), \
                mock.patch.object(update_wiring, "ThreadPoolExecutor", refuse):
            smoke_app = App(smoke_test=True)
            smoke_app.start_updates()
            self.assertIsNone(smoke_app.updater)
            for scenario in ("desktop", "laptop-battery", "laptop-charging", "laptop-limit"):
                with self.subTest(scenario=scenario):
                    app = App(smoke_test=True, preview=scenario)
                    app.start_updates()
                    self.assertIsInstance(app.updater, DemoUpdater)
                    app.updater.check_now()
                    app.stop_updates()
        refuse.assert_not_called()

    def test_normal_runs_build_and_start_the_updater(self):
        app = App()
        with mock.patch.object(Updater, "start") as start:
            app.start_updates()
        self.assertIsInstance(app.updater, Updater)
        self.assertIsInstance(app.updater.services.runner, PatientRunner)   # 從不放棄等待 pkexec
        start.assert_called_once()
        app.stop_updates()


class Kind(unittest.TestCase):
    def test_install_kind_follows_the_program_location(self):
        self.assertEqual(update_wiring.install_kind(Path(Update.PACKAGE_ROOT)), KIND_DEB)
        self.assertEqual(update_wiring.install_kind(Path("/opt/checkout")), KIND_SOURCE)


class Restart(unittest.TestCase):
    def test_installed_update_restarts_with_the_launcher(self):
        app = App()
        app.update_installed()
        self.assertEqual(app.restart_argv, [Update.LAUNCHER])
        self.assertEqual(app.stopped, 1)
        hidden = App()
        hidden.shown = False
        hidden.update_installed()
        self.assertEqual(hidden.restart_argv, [Update.LAUNCHER, "--background"])

    def test_restart_execs_and_reports_failures(self):
        calls = []
        update_wiring.restart([Update.LAUNCHER], execv=lambda path, argv: calls.append(
            (path, argv)))
        self.assertEqual(calls, [(Update.LAUNCHER, [Update.LAUNCHER])])

        def broken(path, argv):
            raise FileNotFoundError(path)
        with self.assertLogs(update_wiring.LOG, "ERROR"):
            self.assertFalse(update_wiring.restart([Update.LAUNCHER], execv=broken))


class Notifications(unittest.TestCase):
    def test_clicking_the_update_notification_opens_the_settings_section(self):
        class Clicked(AlertMixin):
            closing, window = False, object()

            def __init__(self):
                self.opened = []

            def open_settings(self, section=None):
                self.opened.append(("settings", section))

            def open_page(self, widget, page):
                self.opened.append(("page", page))
        app = Clicked()
        app.notification_clicked("settings:update")
        app.notification_clicked("devices")
        self.assertEqual(app.opened, [("settings", "update"), ("page", "devices")])


class Registration(unittest.TestCase):
    def test_section_order(self):
        keys = [key for key, _, _ in settings_window.SECTIONS]
        self.assertEqual(keys[-3:], ["admin", "update", "about"])
        titles = dict((key, title) for key, title, _ in settings_window.SECTIONS)
        self.assertEqual(titles["update"], "更新")
        modules = dict((key, module) for key, _, module in settings_window.SECTIONS)
        self.assertIs(modules["update"], section)

    def test_smoke_test_opens_and_captures_the_section(self):
        self.assertIn(("settings", "update"), smoke.STEPS)
        self.assertIn("update", smoke.LOWER_CAPTURES)

    def test_context_carries_the_updater(self):
        fields = {field.name: field for field in dataclasses.fields(settings_window.SettingsContext)}
        self.assertIsNone(fields["updater"].default)


if __name__ == "__main__":
    unittest.main()
