"""在私人的工作階段匯流排上，以假的通知服務驗證真正的 libnotify 呼叫（由 test_notifier_bus 執行）。

只能在 dbus-run-session 建立的匯流排中執行；匯流排位址不是私人的就拒絕執行，
絕不把通知送到真正的桌面。假的服務在另一個執行緒（自己的 GMainContext）回應，
主執行緒照看板的方式使用 Notifier。結果以一行 JSON 印出。
"""
import json
import os
import sys
import threading
import time

from gi.repository import Gio, GLib

from sdm.core import alert_messages as messages
from sdm.data.ups import UpsReading
from sdm.services.notifier import Notifier

INTERFACE = """<node><interface name="org.freedesktop.Notifications">
<method name="Notify"><arg type="s" direction="in"/><arg type="u" direction="in"/>
<arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/>
<arg type="as" direction="in"/><arg type="a{sv}" direction="in"/><arg type="i" direction="in"/>
<arg type="u" direction="out"/></method>
<method name="CloseNotification"><arg type="u" direction="in"/></method>
<method name="GetCapabilities"><arg type="as" direction="out"/></method>
<method name="GetServerInformation"><arg type="s" direction="out"/><arg type="s" direction="out"/>
<arg type="s" direction="out"/><arg type="s" direction="out"/></method>
<signal name="NotificationClosed"><arg type="u"/><arg type="u"/></signal>
<signal name="ActionInvoked"><arg type="u"/><arg type="s"/></signal>
</interface></node>"""
NAME, PATH = "org.freedesktop.Notifications", "/org/freedesktop/Notifications"
WAIT_SECONDS = 5
PRIVATE_PREFIX = "unix:abstract=sdm-notify-test-"   # test_notifier_bus 建立的私人匯流排


class FakeServer(threading.Thread):
    def __init__(self, address):
        super().__init__(daemon=True)
        self.address, self.calls, self.ready = address, [], threading.Event()
        self.connection, self.loop = None, None

    def run(self):
        context = GLib.MainContext()
        context.push_thread_default()
        flags = (Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                 | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION)
        self.connection = Gio.DBusConnection.new_for_address_sync(self.address, flags, None,
                                                                   None)
        info = Gio.DBusNodeInfo.new_for_xml(INTERFACE).interfaces[0]
        self.connection.register_object(PATH, info, self.method, None, None)
        self.connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                  "org.freedesktop.DBus", "RequestName",
                                  GLib.Variant("(su)", (NAME, 4)), None,
                                  Gio.DBusCallFlags.NONE, -1, None)
        self.loop = GLib.MainLoop(context)
        self.ready.set()
        self.loop.run()

    def method(self, connection, sender, path, interface, name, parameters, invocation):
        if name == "Notify":
            self.calls.append(parameters.unpack())
            invocation.return_value(GLib.Variant("(u)", (len(self.calls),)))
        elif name == "GetCapabilities":
            invocation.return_value(GLib.Variant("(as)", (["actions", "body"],)))
        elif name == "GetServerInformation":
            invocation.return_value(GLib.Variant("(ssss)", ("fake", "test", "1", "1.2")))
        else:
            invocation.return_value(None)

    def emit(self, signal, values, signature):
        """GDBusConnection 可以跨執行緒使用：直接從主執行緒送出訊號。"""
        self.connection.emit_signal(None, PATH, NAME, signal, GLib.Variant(signature, values))


def spin(until):
    deadline = time.monotonic() + WAIT_SECONDS
    context = GLib.MainContext.default()
    while not until() and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.01)


def probe_status(connection):
    notifier = Notifier(lambda page: None, connection=lambda: connection)
    notifier.probe()
    spin(lambda: notifier.status != "unknown")
    return notifier.status


def main():
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    prefix = os.environ.get("SDM_PRIVATE_BUS", "")
    if not prefix.startswith(PRIVATE_PREFIX) or not address.startswith(prefix):
        print("refusing to run outside a private session bus", file=sys.stderr)
        return 2
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    before = probe_status(connection)
    server = FakeServer(address)
    server.start()
    server.ready.wait(WAIT_SECONDS)
    opened = []
    notifier = Notifier(opened.append, connection=lambda: connection)
    sent = notifier.send(messages.ups_on_battery(UpsReading(
        status=("ONBATT",), on_battery=True, charge_pct=99.0, load_w=297.0, time_left_min=44.0)))
    spin(lambda: server.calls)
    after = probe_status(connection)
    key = (messages.UPS_ON_BATTERY, "")
    kept = key in notifier.live
    server.emit("ActionInvoked", (1, "default"), "(us)")
    spin(lambda: opened)
    server.emit("NotificationClosed", (1, 2), "(uu)")
    spin(lambda: key not in notifier.live)
    app, _, icon, summary, body, actions, hints, _ = server.calls[0]
    print(json.dumps(dict(
        before=before, after=after, sent=sent, app=app, icon=icon, summary=summary, body=body,
        actions=actions, desktop_entry=hints.get("desktop-entry"), urgency=hints.get("urgency"),
        opened=opened, kept=kept, released=key not in notifier.live), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
