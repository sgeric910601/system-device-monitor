"""--test-notification 的實際遠端動作（由 test_remote 在私人匯流排中執行）。

「primary」：用看板的應用程式代號註冊、提供 test-notification 動作的替身（不載入 GTK、
不送任何通知），動作被觸發時印出「activated」後結束。
「session」：先啟動替身並等它取得名稱，再執行 device_monitor.py --test-notification，
印出一行 JSON（兩邊的結束碼與輸出）。匯流排位址不是私人的就拒絕執行。
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from gi.repository import Gio, GLib

from sdm.constants import Alerts, App

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_PREFIX = "unix:abstract=sdm-remote-test-"
WAIT_SECONDS = 10


def primary():
    application = Gio.Application(application_id=App.ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
    action = Gio.SimpleAction.new(Alerts.TEST_ACTION, None)

    def activated(*_):
        print("activated", flush=True)
        application.quit()
    action.connect("activate", activated)
    application.add_action(action)
    application.connect("activate", lambda *_: None)
    application.hold()
    GLib.timeout_add_seconds(WAIT_SECONDS, lambda: application.quit() or False)
    application.register(None)
    print("ready", flush=True)
    return application.run([])


def owned(connection):
    reply = connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                 "org.freedesktop.DBus", "NameHasOwner",
                                 GLib.Variant("(s)", (App.ID,)), None, Gio.DBusCallFlags.NONE,
                                 -1, None)
    return reply.unpack()[0]


def session():
    stand_in = subprocess.Popen([sys.executable, "-B", "-m", "tests.fixtures.remote_session",
                                 "primary"], cwd=ROOT, stdout=subprocess.PIPE, text=True)
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    deadline = time.monotonic() + WAIT_SECONDS
    while not owned(connection) and time.monotonic() < deadline:
        time.sleep(0.05)
    client = subprocess.run([sys.executable, "-B", str(ROOT/"device_monitor.py"),
                             "--test-notification"], cwd=ROOT, capture_output=True, text=True,
                            timeout=WAIT_SECONDS)
    output, _ = stand_in.communicate(timeout=WAIT_SECONDS)
    print(json.dumps(dict(client=client.returncode, client_stdout=client.stdout,
                          client_stderr=client.stderr, primary=stand_in.returncode,
                          primary_output=output.split())))
    return 0


def main(mode):
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if not address.startswith(PRIVATE_PREFIX):
        print("refusing to run outside a private session bus", file=sys.stderr)
        return 2
    return primary() if mode == "primary" else session()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "session"))
