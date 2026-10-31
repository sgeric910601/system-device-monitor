"""在私人的工作階段匯流排上，以假的 UPower 驗證補回真正經由 D-Bus 非同步讀取電量紀錄
（由 test_upower_history_bus 執行）。

只能在 dbus-run-session 建立的私人匯流排中執行（位址不對就拒絕），絕不連到系統匯流排或真正的
UPower。假的 UPower 在另一個執行緒回應 EnumerateDevices、屬性讀取與 GetHistory；不斷電系統的
GetHistory 刻意不回覆，用來確認逾時不會卡住主迴圈。結果以一行 JSON 印出。
"""
import json
import os
import sys
import threading
import time

from gi.repository import Gio, GLib

from sdm.core.backfill import SERIES_UPS
from sdm.core.history import HistorySample
from sdm.services.backfill_service import BackfillService
from sdm.services.history_service import HistoryService
from sdm.services.upower_history import UpowerHistoryReader

MANAGER_XML = """<node><interface name="org.freedesktop.UPower">
<method name="EnumerateDevices"><arg type="ao" direction="out"/></method>
</interface></node>"""
DEVICE_XML = """<node><interface name="org.freedesktop.UPower.Device">
<property name="Type" type="u" access="read"/>
<property name="PowerSupply" type="b" access="read"/>
<method name="GetHistory"><arg type="s" direction="in"/><arg type="u" direction="in"/>
<arg type="u" direction="in"/><arg type="a(udu)" direction="out"/></method>
</interface></node>"""
SERVICE = "org.freedesktop.UPower"
PREFIX = "/org/freedesktop/UPower/devices/"
DEVICES = {"battery_BAT0": (2, True), "battery_hidpp_example": (2, False),
           "ups_hiddev0": (3, True)}
WAIT_SECONDS = 10
TICK_MS = 50
PRIVATE_PREFIX = "unix:abstract=sdm-upower-test-"   # test_upower_history_bus 建立的私人匯流排


class FakeUpower(threading.Thread):
    def __init__(self, address, now):
        super().__init__(daemon=True)
        self.address, self.now = address, now
        self.history_calls, self.properties, self.held = [], [], []
        self.ready, self.connection = threading.Event(), None

    def run(self):
        context = GLib.MainContext()
        context.push_thread_default()
        flags = (Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                 | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION)
        self.connection = Gio.DBusConnection.new_for_address_sync(self.address, flags, None,
                                                                   None)
        manager = Gio.DBusNodeInfo.new_for_xml(MANAGER_XML).interfaces[0]
        device = Gio.DBusNodeInfo.new_for_xml(DEVICE_XML).interfaces[0]
        self.connection.register_object("/org/freedesktop/UPower", manager, self.manager, None,
                                        None)
        for name in DEVICES:
            self.connection.register_object(PREFIX + name, device, self.device, self.property,
                                            None)
        self.connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                  "org.freedesktop.DBus", "RequestName",
                                  GLib.Variant("(su)", (SERVICE, 4)), None,
                                  Gio.DBusCallFlags.NONE, -1, None)
        self.ready.set()
        GLib.MainLoop(context).run()

    def manager(self, connection, sender, path, interface, name, parameters, invocation):
        invocation.return_value(GLib.Variant("(ao)", ([PREFIX + item for item in DEVICES],)))

    def property(self, connection, sender, path, interface, name):
        self.properties.append(name)
        kind, supply = DEVICES[path[len(PREFIX):]]
        return GLib.Variant("u", kind) if name == "Type" else GLib.Variant("b", supply)

    def device(self, connection, sender, path, interface, name, parameters, invocation):
        kind = "ups_pct" if path.endswith("ups_hiddev0") else "pct"
        self.history_calls.append([path[len(PREFIX):], kind] + list(parameters.unpack()))
        if path.endswith("ups_hiddev0"):
            self.held.append(invocation)     # 刻意不回覆：模擬太慢的服務
            return
        now = self.now
        # 與真正的 UPower 相同：最新的在前，啟動時有一筆「0 % 狀態不明」的假點
        points = [(now - 30, 80.0, 2), (now - 3600, 70.0, 1), (now - 7200, 65.0, 2),
                  (now - 7200, 0.0, 0), (now - 9 * 86400, 50.0, 2)]
        invocation.return_value(GLib.Variant("(a(udu))", (points,)))


def spin(until, seconds=WAIT_SECONDS):
    deadline = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while not until() and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.01)
    return until()


def laptop_history(now):
    history = HistoryService.in_memory(clock=lambda: now)
    for t in (now - 3 * 3600, now):
        history.add(HistorySample(t, 80, 12.0, False, False, None, None))
    return history


def desktop_history(now):
    history = HistoryService.in_memory(clock=lambda: now)
    for t in (now - 3 * 3600, now):
        history.add(HistorySample(t, None, 200.0, True, False, 100.0, True))
    return history


def absent(connection, now):
    """服務還沒啟動：不自動啟動，回報失敗。"""
    service = BackfillService(laptop_history(now), UpowerHistoryReader(lambda: connection),
                              clock=lambda: now)
    service.check()
    spin(lambda: not service.busy)
    return dict(busy=service.busy, failures=service.failures)


def laptop(connection, upower, now):
    history = laptop_history(now)
    service = BackfillService(history, UpowerHistoryReader(lambda: connection),
                              clock=lambda: now)
    asked = service.check()
    done = spin(lambda: not service.busy)
    calls = len(upower.history_calls)
    again = service.check()
    return dict(asked=asked, done=done, again=again, calls_after=len(upower.history_calls) - calls,
                imported=[[now - s.t, s.pct, s.plugged, s.imported]
                          for s in history.imported.samples],
                spans=len(history.imported.spans), own=len(history.samples),
                history_calls=[list(call) for call in upower.history_calls])


def slow(connection, upower, now):
    """不斷電系統的紀錄不回覆：逾時後回報失敗，等待期間主迴圈照常運作。"""
    ticks = []
    GLib.timeout_add(TICK_MS, lambda: ticks.append(1) or True)
    service = BackfillService(desktop_history(now), UpowerHistoryReader(lambda: connection),
                              clock=lambda: now)
    started = time.monotonic()
    service.check()
    spin(lambda: not service.busy)
    return dict(busy=service.busy, failures=service.failures, ticks=len(ticks),
                seconds=round(time.monotonic() - started, 1),
                spans=len(service.history.imported.spans),
                asked=[call[0] for call in upower.history_calls if call[1] == SERIES_UPS])


def main():
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    prefix = os.environ.get("SDM_PRIVATE_BUS", "")
    if not prefix.startswith(PRIVATE_PREFIX) or not address.startswith(prefix):
        print("refusing to run outside a private session bus", file=sys.stderr)
        return 2
    now = int(time.time())      # 整數秒：與 UPower 的時間相同，比較時不受捨入影響
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    report = dict(absent=absent(connection, now))
    upower = FakeUpower(address, now)
    upower.start()
    upower.ready.wait(WAIT_SECONDS)
    report["laptop"] = laptop(connection, upower, now)
    report["properties"] = sorted(set(upower.properties))
    report["slow"] = slow(connection, upower, now)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
