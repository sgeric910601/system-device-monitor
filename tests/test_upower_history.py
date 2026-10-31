"""非同步讀取 UPower 電量紀錄（sdm.services.upower_history）：只讀唯一的系統電池或不斷電系統、
不自動啟動服務、逾時與錯誤都只回報失敗、裝置路徑絕不寫進日誌。

以假的匯流排連線代替系統匯流排（不連任何匯流排）；經由真正 D-Bus 的測試在 test_upower_history_bus。
"""
import logging
import unittest

from gi.repository import Gio, GLib

from sdm.constants import Backfill
from sdm.core.backfill import SERIES_BATTERY, SERIES_UPS
from sdm.services.upower_history import (DESTINATION, DEVICE_INTERFACE, MANAGER_INTERFACE,
                                         MANAGER_PATH, PROPERTIES_INTERFACE, FetchResult,
                                         UpowerHistoryReader)
from tests.fixtures.fake_timers import FakeTimers

LOGGER = "sdm.services.upower_history"
PREFIX = "/org/freedesktop/UPower/devices/"
BATTERY = PREFIX + "battery_SECRETSERIAL0"
SECOND = PREFIX + "battery_BAT1"
UPS = PREFIX + "ups_hiddev0"
MOUSE = PREFIX + "battery_mouse"
POINTS = ((1_800_000_000, 80.0, 2), (1_799_999_000, 81.0, 2))


def remote_error(name):
    return Gio.DBusError.new_for_dbus_error(name, "fake failure")


def timeout_error():
    return GLib.Error.new_literal(Gio.io_error_quark(), "timed out", int(Gio.IOErrorEnum.TIMED_OUT))


class Bus:
    """假的系統匯流排：記錄每個非同步呼叫，run() 依假的 UPower 依序回答直到沒有待答的呼叫。"""

    def __init__(self, devices=None, histories=None, errors=None):
        self.devices = devices if devices is not None else {}
        self.histories = histories or {}
        self.errors = errors or {}
        self.calls, self.pending = [], []

    def call(self, name, path, interface, method, parameters, reply_type, flags, timeout,
             cancellable, callback, data):
        call = dict(name=name, path=path, interface=interface, method=method,
                    args=parameters.unpack() if parameters is not None else None,
                    reply=reply_type.dup_string(), flags=flags, timeout=timeout,
                    cancellable=cancellable, callback=callback, data=data)
        self.calls.append(call)
        self.pending.append(call)

    def call_finish(self, result):
        if isinstance(result, GLib.Error):
            raise result
        return result

    def answer(self, call):
        key = (call["path"], call["method"], call["args"][1] if call["method"] == "Get" else None)
        if key in self.errors:
            return self.errors[key]
        if call["method"] == "EnumerateDevices":
            return GLib.Variant("(ao)", (list(self.devices),))
        if call["method"] == "Get":
            kind, supply = self.devices[call["path"]]
            value = GLib.Variant("u", kind) if call["args"][1] == "Type" else \
                GLib.Variant("b", supply)
            return GLib.Variant("(v)", (value,))
        return GLib.Variant("(a(udu))", (list(self.histories.get(call["path"], ())),))

    def run(self, limit=100):
        while self.pending and limit:
            call = self.pending.pop(0)
            call["callback"](self, self.answer(call), call["data"])
            limit -= 1


class Base(unittest.TestCase):
    def setUp(self):
        self.timers = FakeTimers()
        self.results = []

    def reader(self, bus):
        return UpowerHistoryReader(lambda: bus, schedule=self.timers.schedule,
                                   cancel=self.timers.cancel)

    def fetch(self, bus, series=(SERIES_BATTERY,), timespan=3600):
        reader = self.reader(bus)
        reader.fetch(series, timespan, self.results.append)
        bus.run()
        return reader


class Reading(Base):
    def test_reads_the_single_laptop_battery(self):
        bus = Bus({BATTERY: (2, True), MOUSE: (2, False), UPS: (3, True)}, {BATTERY: POINTS})
        self.fetch(bus, timespan=7200)
        self.assertEqual(self.results, [FetchResult(points=((SERIES_BATTERY, POINTS),))])
        history = [call for call in bus.calls if call["method"] == "GetHistory"]
        self.assertEqual([(call["path"], call["args"]) for call in history],
                         [(BATTERY, ("charge", 7200, Backfill.MAX_POINTS))])
        self.assertEqual(history[0]["interface"], DEVICE_INTERFACE)
        self.assertEqual(history[0]["reply"], "(a(udu))")

    def test_every_call_is_bounded_and_never_starts_the_service(self):
        bus = Bus({BATTERY: (2, True)}, {BATTERY: POINTS})
        self.fetch(bus)
        self.assertEqual({call["name"] for call in bus.calls}, {DESTINATION})
        self.assertEqual({call["flags"] for call in bus.calls}, {Gio.DBusCallFlags.NO_AUTO_START})
        self.assertEqual({call["timeout"] for call in bus.calls}, {Backfill.CALL_TIMEOUT_MS})
        self.assertTrue(all(isinstance(call["cancellable"], Gio.Cancellable)
                            for call in bus.calls))
        first = bus.calls[0]
        self.assertEqual((first["path"], first["interface"], first["method"]),
                         (MANAGER_PATH, MANAGER_INTERFACE, "EnumerateDevices"))

    def test_only_type_and_power_supply_are_read(self):
        bus = Bus({BATTERY: (2, True), UPS: (3, True), MOUSE: (5, False)}, {BATTERY: POINTS})
        self.fetch(bus)
        gets = [call for call in bus.calls if call["method"] == "Get"]
        self.assertEqual({call["interface"] for call in gets}, {PROPERTIES_INTERFACE})
        self.assertEqual({call["args"][0] for call in gets}, {DEVICE_INTERFACE})
        self.assertEqual({call["args"][1] for call in gets}, {"Type", "PowerSupply"})
        self.assertEqual([call["path"] for call in gets if call["args"][1] == "PowerSupply"],
                         [BATTERY])

    def test_reads_the_single_ups(self):
        bus = Bus({UPS: (3, True), MOUSE: (5, False)}, {UPS: POINTS})
        self.fetch(bus, series=(SERIES_UPS,))
        self.assertEqual(self.results, [FetchResult(points=((SERIES_UPS, POINTS),))])

    def test_both_series_in_one_request(self):
        bus = Bus({BATTERY: (2, True), UPS: (3, True)}, {BATTERY: POINTS, UPS: POINTS[:1]})
        self.fetch(bus, series=(SERIES_BATTERY, SERIES_UPS))
        self.assertEqual(self.results, [FetchResult(points=((SERIES_BATTERY, POINTS),
                                                            (SERIES_UPS, POINTS[:1])))])

    def test_no_single_matching_device_is_reported_missing(self):
        cases = ({}, {MOUSE: (2, False)}, {BATTERY: (2, True), SECOND: (2, True)},
                 {UPS: (3, True), PREFIX + "ups_hiddev1": (3, True)})
        for devices in cases:
            with self.subTest(devices=sorted(devices)):
                self.results = []
                bus = Bus(devices)
                self.fetch(bus, series=(SERIES_BATTERY, SERIES_UPS))
                self.assertEqual(self.results, [FetchResult(missing=(SERIES_BATTERY, SERIES_UPS))])
                self.assertFalse([call for call in bus.calls if call["method"] == "GetHistory"])

    def test_device_count_is_capped(self):
        devices = {PREFIX + f"mouse_{index}": (5, False) for index in range(40)}
        bus = Bus(devices)
        self.fetch(bus)
        self.assertEqual(len([call for call in bus.calls if call["method"] == "Get"]),
                         Backfill.MAX_DEVICES)


class Failures(Base):
    def test_upower_not_running(self):
        error = remote_error("org.freedesktop.DBus.Error.ServiceUnknown")
        bus = Bus(errors={(MANAGER_PATH, "EnumerateDevices", None): error})
        self.fetch(bus)
        self.assertEqual(self.results,
                         [FetchResult(error="org.freedesktop.DBus.Error.ServiceUnknown")])

    def test_no_system_bus(self):
        reader = UpowerHistoryReader(lambda: None, schedule=self.timers.schedule,
                                     cancel=self.timers.cancel)
        reader.fetch((SERIES_BATTERY,), 3600, self.results.append)
        self.assertEqual(self.results, [FetchResult(error="no system bus")])

    def test_device_that_vanishes_is_skipped(self):
        error = remote_error("org.freedesktop.DBus.Error.UnknownObject")
        bus = Bus({MOUSE: (2, True), BATTERY: (2, True)}, {BATTERY: POINTS},
                  errors={(MOUSE, "Get", "Type"): error})
        self.fetch(bus)
        self.assertEqual(self.results, [FetchResult(points=((SERIES_BATTERY, POINTS),))])

    def test_history_not_supported_marks_the_series_missing(self):
        error = remote_error("org.freedesktop.UPower.GeneralError")
        bus = Bus({BATTERY: (2, True)}, errors={(BATTERY, "GetHistory", None): error})
        self.fetch(bus)
        self.assertEqual(self.results, [FetchResult(missing=(SERIES_BATTERY,))])

    def test_history_timeout_marks_the_series_for_a_later_retry(self):
        bus = Bus({BATTERY: (2, True)}, errors={(BATTERY, "GetHistory", None): timeout_error()})
        self.fetch(bus)
        self.assertEqual(self.results, [FetchResult(failed=(SERIES_BATTERY,))])

    def test_one_series_timing_out_keeps_the_other(self):
        bus = Bus({BATTERY: (2, True), UPS: (3, True)}, {UPS: POINTS},
                  errors={(BATTERY, "GetHistory", None): timeout_error()})
        self.fetch(bus, series=(SERIES_BATTERY, SERIES_UPS))
        self.assertEqual(self.results, [FetchResult(points=((SERIES_UPS, POINTS),),
                                                    failed=(SERIES_BATTERY,))])

    def test_unexpected_reply_fails_at_once_instead_of_waiting_for_the_deadline(self):
        bus = Bus({BATTERY: (2, True)})
        original = bus.answer

        def odd(call):
            if call["method"] == "GetHistory":
                return GLib.Variant("(i)", (5,))
            return original(call)
        bus.answer = odd
        with self.assertLogs(LOGGER, "WARNING"):
            self.fetch(bus)
        self.assertEqual(self.results, [FetchResult(error="TypeError")])
        self.assertEqual(self.timers.pending, {})

    def test_unexpected_reply_values_are_ignored(self):
        bus = Bus({BATTERY: (2, True)}, {BATTERY: POINTS})
        original = bus.answer

        def odd(call):
            if call["method"] == "Get" and call["args"][1] == "PowerSupply":
                return GLib.Variant("(v)", (GLib.Variant("s", "yes"),))
            return original(call)
        bus.answer = odd
        self.fetch(bus)
        self.assertEqual(self.results, [FetchResult(missing=(SERIES_BATTERY,))])

    def test_deadline_gives_up_and_ignores_late_replies(self):
        bus = Bus({BATTERY: (2, True)}, {BATTERY: POINTS})
        reader = self.reader(bus)
        reader.fetch((SERIES_BATTERY,), 3600, self.results.append)
        self.timers.advance(Backfill.DEADLINE_SECONDS)
        self.assertEqual(self.results, [FetchResult(error="deadline")])
        self.assertTrue(bus.calls[0]["cancellable"].is_cancelled())
        bus.run()
        self.assertEqual(len(self.results), 1)
        self.assertEqual(len(bus.calls), 1)

    def test_finished_request_cancels_its_deadline(self):
        bus = Bus({BATTERY: (2, True)}, {BATTERY: POINTS})
        self.fetch(bus)
        self.assertEqual(self.timers.pending, {})

    def test_stop_cancels_without_reporting(self):
        bus = Bus({BATTERY: (2, True)}, {BATTERY: POINTS})
        reader = self.reader(bus)
        reader.fetch((SERIES_BATTERY,), 3600, self.results.append)
        reader.stop()
        bus.run()
        self.assertEqual(self.results, [])
        self.assertTrue(bus.calls[0]["cancellable"].is_cancelled())
        self.assertEqual(self.timers.pending, {})

    def test_new_fetch_replaces_an_unfinished_one(self):
        bus = Bus({BATTERY: (2, True)}, {BATTERY: POINTS})
        reader = self.reader(bus)
        reader.fetch((SERIES_BATTERY,), 3600, self.results.append)
        first = bus.calls[0]
        reader.fetch((SERIES_BATTERY,), 3600, self.results.append)
        self.assertTrue(first["cancellable"].is_cancelled())
        bus.run()
        self.assertEqual(self.results, [FetchResult(points=((SERIES_BATTERY, POINTS),))])


class Privacy(Base):
    def test_device_paths_never_reach_the_log(self):
        error = remote_error("org.freedesktop.DBus.Error.UnknownObject")
        bus = Bus({BATTERY: (2, True), SECOND: (2, True)},
                  errors={(SECOND, "Get", "Type"): error})
        logging.getLogger(LOGGER).setLevel(logging.DEBUG)
        self.addCleanup(logging.getLogger(LOGGER).setLevel, logging.NOTSET)
        with self.assertLogs(LOGGER, "DEBUG") as logs:
            self.fetch(bus)
            logging.getLogger(LOGGER).debug("end of test")
        text = " ".join(logs.output)
        for secret in ("SECRETSERIAL", "BAT1", PREFIX):
            self.assertNotIn(secret, text)

    def test_result_holds_no_device_paths(self):
        bus = Bus({BATTERY: (2, True)}, {BATTERY: POINTS})
        self.fetch(bus)
        self.assertNotIn("SECRETSERIAL", repr(self.results))


if __name__ == "__main__":
    unittest.main()
