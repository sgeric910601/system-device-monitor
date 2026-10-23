"""--test-notification：請正在執行的看板送出測試通知（Gio.Application 遠端動作），自己絕不送通知。

單元測試用假的應用程式物件；整合測試在私人匯流排上執行（不提供任何服務、沒有顯示器），
一次沒有正在執行的看板、一次有提供動作的替身。
"""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from gi.repository import Gio, GLib

from sdm.constants import Alerts, App
from sdm.services import remote
from tests.fixtures.session_bus import DBUS_RUN_SESSION, clean_environment, write_config

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_SECONDS = 60


class FakeConnection:
    def __init__(self): self.flushed = 0
    def flush_sync(self, cancellable): self.flushed += 1


class FakeApplication:
    def __init__(self, remote=True, actions=(Alerts.TEST_ACTION,), error=None):
        self.remote, self.actions, self.error = remote, actions, error
        self.activated, self.connection = [], FakeConnection()

    def register(self, cancellable):
        if self.error is not None:
            raise self.error
        return True

    def get_is_remote(self): return self.remote
    def has_action(self, name): return name in self.actions
    def activate_action(self, name, parameter): self.activated.append((name, parameter))
    def get_dbus_connection(self): return self.connection


def no_owner():
    return GLib.Error.new_literal(Gio.dbus_error_quark(), "no owner",
                                  Gio.DBusError.SERVICE_UNKNOWN)


class Requests(unittest.TestCase):
    def request(self, application):
        stderr = io.StringIO()
        code = remote.request_test_notification(lambda: application, stderr)
        return code, stderr.getvalue()

    def test_running_instance_gets_the_action(self):
        application = FakeApplication()
        self.assertEqual(self.request(application), (0, ""))
        self.assertEqual(application.activated, [(Alerts.TEST_ACTION, None)])
        self.assertEqual(application.connection.flushed, 1)

    def test_no_running_instance(self):
        code, message = self.request(FakeApplication(error=no_owner()))
        self.assertEqual((code, message.strip()), (1, remote.NOT_RUNNING))

    def test_launcher_that_somehow_became_primary_is_treated_as_not_running(self):
        code, message = self.request(FakeApplication(remote=False))
        self.assertEqual((code, message.strip()), (1, remote.NOT_RUNNING))

    def test_older_instance_without_the_action(self):
        application = FakeApplication(actions=())
        code, message = self.request(application)
        self.assertEqual((code, message.strip()), (1, remote.TOO_OLD))
        self.assertEqual(application.activated, [])

    def test_no_session_bus(self):
        error = GLib.Error.new_literal(Gio.io_error_quark(), "no bus", Gio.IOErrorEnum.FAILED)
        code, message = self.request(FakeApplication(error=error))
        self.assertEqual((code, message.strip()), (2, remote.NO_BUS))

    def test_default_application_never_tries_to_become_primary(self):
        application = remote.default_application()
        self.assertEqual(application.get_application_id(), App.ID)
        self.assertTrue(application.get_flags() & Gio.ApplicationFlags.IS_LAUNCHER)


def run_on_private_bus(prefix, command):
    with tempfile.TemporaryDirectory() as temp:
        config, _ = write_config(temp, prefix)
        return subprocess.run([DBUS_RUN_SESSION, f"--config-file={config}", "--", *command],
                              env=clean_environment(), cwd=ROOT, capture_output=True, text=True,
                              timeout=TIMEOUT_SECONDS)


@unittest.skipUnless(DBUS_RUN_SESSION, "needs dbus-run-session")
class Isolated(unittest.TestCase):
    def test_without_a_running_instance(self):
        result = run_on_private_bus("sdm-remote-test-", [
            sys.executable, "-B", str(ROOT/"device_monitor.py"), "--test-notification"])
        self.assertEqual(result.returncode, 1, result.stderr[-2000:])
        self.assertEqual((result.stdout, result.stderr.strip()), ("", remote.NOT_RUNNING))

    def test_running_instance_receives_the_action(self):
        result = run_on_private_bus("sdm-remote-test-", [
            sys.executable, "-B", "-m", "tests.fixtures.remote_session", "session"])
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        report = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual((report["client"], report["client_stdout"], report["client_stderr"]),
                         (0, "", ""))
        self.assertEqual((report["primary"], report["primary_output"]),
                         (0, ["ready", "activated"]))

    def test_cannot_be_combined_with_other_options(self):
        for extra in ("--preview=desktop", "--smoke-test", "--dump", "--background"):
            result = subprocess.run([sys.executable, "-B", str(ROOT/"device_monitor.py"),
                                     "--test-notification", extra], cwd=ROOT,
                                    env=clean_environment(), capture_output=True, text=True,
                                    timeout=TIMEOUT_SECONDS)
            self.assertEqual(result.returncode, 2, extra)
            self.assertIn("--test-notification", result.stderr)


if __name__ == "__main__":
    unittest.main()
