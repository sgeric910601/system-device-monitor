"""UDisks2 SMART 唯讀讀取：屬性逐一 Get、NVMe 另呼叫 SmartGetAttributes、逾時就停（假的匯流排）。

物件路徑與數值都是虛構的；不讀序號、WWN 或任何識別碼。
"""
import unittest

from gi.repository import Gio, GLib

from sdm.constants import Disk
from sdm.core.disk_health import ATA, NONE, NVME
from sdm.data import udisks

NVME_DRIVE = "/org/freedesktop/UDisks2/drives/Example_SSD"
ATA_DRIVE = "/org/freedesktop/UDisks2/drives/Example_HDD"
USB_DRIVE = "/org/freedesktop/UDisks2/drives/Example_USB"
BLOCK = "/org/freedesktop/UDisks2/block_devices/"
PROPERTIES = {
    (BLOCK + "nvme0n1", udisks.BLOCK_INTERFACE, "Drive"): NVME_DRIVE,
    (BLOCK + "sda", udisks.BLOCK_INTERFACE, "Drive"): ATA_DRIVE,
    (BLOCK + "sdb", udisks.BLOCK_INTERFACE, "Drive"): USB_DRIVE,
    (BLOCK + "sdc", udisks.BLOCK_INTERFACE, "Drive"): "/",
    (NVME_DRIVE, udisks.NVME_INTERFACE, "SmartUpdated"): 1_800_000_000,
    (NVME_DRIVE, udisks.NVME_INTERFACE, "SmartCriticalWarning"): [],
    (NVME_DRIVE, udisks.NVME_INTERFACE, "SmartPowerOnHours"): 1177,
    (NVME_DRIVE, udisks.NVME_INTERFACE, "SmartTemperature"): 314,
    (ATA_DRIVE, udisks.ATA_INTERFACE, "SmartSupported"): True,
    (ATA_DRIVE, udisks.ATA_INTERFACE, "SmartEnabled"): True,
    (ATA_DRIVE, udisks.ATA_INTERFACE, "SmartUpdated"): 1_800_000_000,
    (ATA_DRIVE, udisks.ATA_INTERFACE, "SmartFailing"): False,
    (ATA_DRIVE, udisks.ATA_INTERFACE, "SmartNumBadSectors"): 0,
    (ATA_DRIVE, udisks.ATA_INTERFACE, "SmartPowerOnSeconds"): 4_248_000,
    (ATA_DRIVE, udisks.ATA_INTERFACE, "SmartTemperature"): 310.15,
}
ATTRIBUTES = {"avail_spare": 100, "spare_thresh": 5, "percent_used": 2,
              "total_data_read": 542_052_352_000, "total_data_written": 2_087_800_832_000,
              "media_errors": 0, "num_err_log_entries": 98}


def timeout_error():
    return GLib.Error.new_literal(Gio.io_error_quark(), "timed out", int(Gio.IOErrorEnum.TIMED_OUT))


class FakeBus:
    def __init__(self, properties=None, attributes=None):
        self.properties = dict(PROPERTIES if properties is None else properties)
        self.attributes = ATTRIBUTES if attributes is None else attributes
        self.gets, self.calls, self.slow = [], [], set()

    def get(self, destination, path, interface, name):
        self.gets.append((path, interface, name))
        if destination != udisks.DESTINATION:
            raise GLib.Error("wrong service")
        if (path, interface, name) in self.slow:
            raise timeout_error()
        try:
            return self.properties[(path, interface, name)]
        except KeyError:
            raise GLib.Error("No such interface") from None

    def call(self, destination, path, interface, method, args):
        self.calls.append((path, interface, method, args.unpack()))
        if (path, method) in self.slow:
            raise timeout_error()
        return (self.attributes,)


class Clock:
    def __init__(self):
        self.now = 1000.0
    def __call__(self):
        return self.now


class Reading(unittest.TestCase):
    def setUp(self):
        self.bus, self.clock = FakeBus(), Clock()
        self.source = udisks.SmartSource(self.bus.get, self.bus.call, self.clock)

    def test_nvme_ata_and_unsupported_drives(self):
        result = self.source.refresh(("nvme0n1", "sda", "sdb", "sdc"), attributes=True)
        nvme = result["nvme0n1"]
        self.assertEqual((nvme.kind, nvme.power_on_hours, round(nvme.temperature_c, 2)),
                         (NVME, 1177, 40.85))
        self.assertEqual((nvme.percent_used, nvme.avail_spare, nvme.spare_thresh,
                          nvme.bytes_read, nvme.bytes_written, nvme.media_errors),
                         (2, 100, 5, 542_052_352_000, 2_087_800_832_000, 0))
        self.assertEqual(nvme.critical_warning, ())
        ata = result["sda"]
        self.assertEqual((ata.kind, ata.supported, ata.enabled, ata.failing, ata.bad_sectors,
                          ata.power_on_hours, round(ata.temperature_c)),
                         (ATA, True, True, False, 0, 1180, 37))
        self.assertEqual((result["sdb"].kind, result["sdc"].kind), (NONE, NONE))
        self.assertEqual([call[2] for call in self.bus.calls], ["SmartGetAttributes"])
        self.assertEqual(self.bus.calls[0][3], ({},))

    def test_only_listed_properties_are_read(self):
        self.source.refresh(("nvme0n1", "sda"), attributes=True)
        names = {name for _, _, name in self.bus.gets}
        self.assertTrue(names <= set(udisks.READ_PROPERTIES))
        self.assertFalse({"Serial", "WWN", "Id", "SubsystemNQN"} & names)

    def test_properties_every_minute_and_attributes_every_ten_minutes(self):
        self.source.refresh(("nvme0n1",), attributes=True)
        gets, calls = len(self.bus.gets), len(self.bus.calls)
        self.clock.now += Disk.SMART_PROPERTIES_INTERVAL - 1
        self.source.refresh(("nvme0n1",), attributes=True)
        self.assertEqual((len(self.bus.gets), len(self.bus.calls)), (gets, calls))
        self.clock.now += 1
        self.source.refresh(("nvme0n1",), attributes=True)
        self.assertGreater(len(self.bus.gets), gets)
        self.assertEqual(len(self.bus.calls), calls)
        self.clock.now += Disk.SMART_ATTRIBUTES_INTERVAL
        self.source.refresh(("nvme0n1",), attributes=True)
        self.assertEqual(len(self.bus.calls), calls + 1)

    def test_attributes_only_when_asked(self):
        result = self.source.refresh(("nvme0n1",), attributes=False)
        self.assertEqual(self.bus.calls, [])
        self.assertIsNone(result["nvme0n1"].percent_used)
        self.assertEqual(result["nvme0n1"].power_on_hours, 1177)

    def test_cached_results_keep_attributes(self):
        self.source.refresh(("nvme0n1",), attributes=True)
        self.clock.now += Disk.SMART_PROPERTIES_INTERVAL
        result = self.source.refresh(("nvme0n1",), attributes=False)
        self.assertEqual(result["nvme0n1"].percent_used, 2)
        self.assertEqual(self.source.cached("nvme0n1").power_on_hours, 1177)
        self.assertIsNone(self.source.cached("sdz"))

    def test_removed_disks_are_forgotten(self):
        self.source.refresh(("nvme0n1", "sda"))
        self.clock.now += Disk.SMART_PROPERTIES_INTERVAL
        result = self.source.refresh(("sda",))
        self.assertEqual(sorted(result), ["sda"])
        self.assertIsNone(self.source.cached("nvme0n1"))

    def test_invalid_names_are_never_sent(self):
        result = self.source.refresh(("../sda", "sd a", ""))
        self.assertEqual((result, self.bus.gets), ({}, []))


class Failures(unittest.TestCase):
    def setUp(self):
        self.bus, self.clock = FakeBus(), Clock()
        self.source = udisks.SmartSource(self.bus.get, self.bus.call, self.clock)

    def test_a_timeout_stops_this_sample_and_backs_off(self):
        self.bus.slow.add((BLOCK + "nvme0n1", udisks.BLOCK_INTERFACE, "Drive"))
        result = self.source.refresh(("nvme0n1", "sda"), attributes=True)
        self.assertEqual(result, {})
        self.assertEqual(len(self.bus.gets), 1)
        self.clock.now += Disk.SMART_BACKOFF_SECONDS - 1
        self.source.refresh(("nvme0n1", "sda"))
        self.assertEqual(len(self.bus.gets), 1)
        self.bus.slow.clear()
        self.clock.now += 1
        result = self.source.refresh(("nvme0n1", "sda"))
        self.assertEqual(sorted(result), ["nvme0n1", "sda"])

    def test_attribute_timeout_keeps_the_properties(self):
        self.bus.slow.add((NVME_DRIVE, "SmartGetAttributes"))
        result = self.source.refresh(("nvme0n1", "sda"), attributes=True)
        self.assertEqual(result["nvme0n1"].power_on_hours, 1177)
        self.assertNotIn("sda", result)

    def test_unexpected_values_are_dropped(self):
        properties = dict(PROPERTIES)
        properties[(NVME_DRIVE, udisks.NVME_INTERFACE, "SmartPowerOnHours")] = "many"
        properties[(NVME_DRIVE, udisks.NVME_INTERFACE, "SmartTemperature")] = 0
        properties[(NVME_DRIVE, udisks.NVME_INTERFACE, "SmartCriticalWarning")] = [
            "spare\x07", 5, "x" * 100]
        bus = FakeBus(properties, dict(ATTRIBUTES, percent_used=-1, total_data_read="lots"))
        smart = udisks.SmartSource(bus.get, bus.call, Clock()).refresh(
            ("nvme0n1",), attributes=True)["nvme0n1"]
        self.assertEqual((smart.power_on_hours, smart.temperature_c), (None, None))
        self.assertEqual(smart.critical_warning, ("spare", "x" * udisks.WARNING_MAX_CHARS))
        self.assertEqual((smart.percent_used, smart.bytes_read), (None, None))

    def test_bus_unavailable(self):
        source = udisks.SmartSource(udisks.bus_getter(None), udisks.bus_caller(None), Clock())
        self.assertEqual(source.refresh(("sda",)), {"sda": udisks.DiskSmart(kind=NONE)})


if __name__ == "__main__":
    unittest.main()
