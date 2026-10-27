"""在私人的工作階段匯流排上，以假的 logind 驗證 SleepGate 真正經由 D-Bus 拿到與放開延遲鎖
（由 test_sleep_gate_bus 執行）。

只能在 dbus-run-session 建立的私人匯流排中執行（位址不對就拒絕），絕不連到系統匯流排或真正的
logind。假的 logind 在另一個執行緒回應 Inhibit：回傳管道的寫入端，保留讀取端；
讀到結尾就表示看板已經放開延遲鎖。結果以一行 JSON 印出。
"""
import json
import os
import sys
import threading
import time

from gi.repository import Gio, GLib

from sdm.services.sleep_gate import MANAGER, PATH, SERVICE, SleepGate

INTERFACE = """<node><interface name="org.freedesktop.login1.Manager">
<method name="Inhibit"><arg type="s" direction="in"/><arg type="s" direction="in"/>
<arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="h" direction="out"/></method>
<signal name="PrepareForSleep"><arg type="b"/></signal>
<signal name="PrepareForShutdown"><arg type="b"/></signal>
</interface></node>"""
WAIT_SECONDS = 5
PRIVATE_PREFIX = "unix:abstract=sdm-logind-test-"   # test_sleep_gate_bus 建立的私人匯流排


class FakeLogind(threading.Thread):
    def __init__(self, address):
        super().__init__(daemon=True)
        self.address, self.calls, self.reads = address, [], []
        self.ready, self.connection = threading.Event(), None

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
                                  GLib.Variant("(su)", (SERVICE, 4)), None,
                                  Gio.DBusCallFlags.NONE, -1, None)
        self.ready.set()
        GLib.MainLoop(context).run()

    def method(self, connection, sender, path, interface, name, parameters, invocation):
        read, write = os.pipe()
        os.set_blocking(read, False)
        self.calls.append(parameters.unpack())
        self.reads.append(read)
        fd_list = Gio.UnixFDList.new_from_array([write])
        invocation.return_value_with_unix_fd_list(GLib.Variant("(h)", (0,)), fd_list)

    def emit(self, signal, value):
        self.connection.emit_signal(None, PATH, MANAGER, signal, GLib.Variant("(b)", (value,)))

    def released(self, index):
        try:
            return os.read(self.reads[index], 1) == b""
        except (BlockingIOError, IndexError):
            return False


def spin(until):
    deadline = time.monotonic() + WAIT_SECONDS
    context = GLib.MainContext.default()
    while not until() and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.01)
    return until()


def main():
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    prefix = os.environ.get("SDM_PRIVATE_BUS", "")
    if not prefix.startswith(PRIVATE_PREFIX) or not address.startswith(prefix):
        print("refusing to run outside a private session bus", file=sys.stderr)
        return 2
    logind = FakeLogind(address)
    logind.start()
    logind.ready.wait(WAIT_SECONDS)
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    events, pending = [], []
    gate = SleepGate(lambda: connection, lambda done: (events.append("sleep"), pending.append(done)),
                     lambda: events.append("wake"))
    gate.start()
    gate.set_active(True)
    took = spin(lambda: gate.holding)
    held = not logind.released(0)
    logind.emit("PrepareForSleep", True)
    spin(lambda: pending)
    still_held = not logind.released(0)
    pending.pop()()
    released = spin(lambda: logind.released(0))
    logind.emit("PrepareForSleep", False)
    retaken = spin(lambda: "wake" in events and gate.holding and len(logind.calls) == 2)
    gate.set_active(False)
    ended = spin(lambda: logind.released(1))
    gate.stop()
    print(json.dumps(dict(took=took, held=held, still_held=still_held, released=released,
                          retaken=retaken, ended=ended, events=events,
                          call=list(logind.calls[0])), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
