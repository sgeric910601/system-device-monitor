"""磁碟清單（lsblk）與讀寫統計（/proc/diskstats）：型號、連線類型、外接判斷與檔案系統。

型號與掛載點都是虛構的。
"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sdm.constants import Disk
from sdm.data import storage
from tests.fixtures import procfs

GB = 1000 ** 3


class Statvfs:
    f_blocks, f_frsize, f_bfree, f_bavail = 1000, 4096, 400, 300


def lsblk(**external):
    return json.dumps({"blockdevices": [
        {"name": "sda", "type": "disk", "tran": "sata", "size": 4000 * GB, "fstype": None,
         "mountpoints": [None], "model": "Example‮HDD\u0007", "rm": False, "hotplug": False,
         "children": [{"name": "sda1", "type": "part", "tran": None, "size": 4000 * GB,
                       "fstype": "ext4", "mountpoints": ["/srv/example"], "model": None,
                       "rm": False, "hotplug": False}]},
        {"name": "sdb", "type": "disk", "tran": "usb", "size": 1000 * GB, "fstype": None,
         "mountpoints": [None], "model": None, "rm": False, "hotplug": False,
         "children": [{"name": "sdb1", "type": "part", "fstype": "exfat",
                       "mountpoints": ["/media/example/Backup", None]}], **external},
        {"name": "zram0", "type": "disk", "size": 1, "mountpoints": ["[SWAP]"]},
    ]})


class Lsblk(unittest.TestCase):
    def parse(self, text):
        with patch("sdm.data.storage.os.statvfs", return_value=Statvfs()):
            return storage.parse_lsblk(text)

    def test_disks_with_connection_placement_and_filesystems(self):
        internal, external = self.parse(lsblk())
        self.assertEqual((internal["name"], internal["model"], internal["connection"],
                          internal["external"]), ("sda", "ExampleHDD", "SATA", False))
        self.assertEqual(internal["volumes"], [dict(mount="/srv/example", fstype="ext4",
                                                    total=4096000, used=2457600,
                                                    available=1228800)])
        self.assertEqual((external["model"], external["connection"], external["external"]),
                         ("外接磁碟", "USB", True))
        self.assertEqual([volume["fstype"] for volume in external["volumes"]], ["exfat"])

    def test_removable_flag_marks_a_disk_as_external(self):
        disks = self.parse(lsblk(tran="sata", rm=True))
        self.assertTrue(disks[1]["external"])

    def test_internal_disk_without_a_model(self):
        text = json.dumps({"blockdevices": [{"name": "vda", "type": "disk", "size": 5,
                                             "model": "   ", "mountpoints": []}]})
        disk = self.parse(text)[0]
        self.assertEqual((disk["model"], disk["connection"], disk["external"]), ("磁碟", "其他", False))

    def test_invalid_names_are_skipped(self):
        text = json.dumps({"blockdevices": [{"name": "../sda", "type": "disk", "size": 5}]})
        self.assertEqual(self.parse(text), [])

    def test_command_asks_for_the_placement_columns(self):
        args = storage.lsblk_command()
        self.assertEqual(args[0], "/usr/bin/lsblk")
        self.assertIn("NAME,TYPE,TRAN,SIZE,FSTYPE,MOUNTPOINTS,MODEL,RM,HOTPLUG", args)

    def test_cadence(self):
        self.assertEqual(storage.meta_interval(True), Disk.META_VISIBLE_INTERVAL)
        self.assertEqual(storage.meta_interval(False), Disk.META_HIDDEN_INTERVAL)
        self.assertEqual((Disk.META_VISIBLE_INTERVAL, Disk.META_HIDDEN_INTERVAL), (30, 300))


class Diskstats(unittest.TestCase):
    def test_whole_physical_disks_in_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            procfs.write_system(Path(temp))
            counters = storage.parse_diskstats(procfs.diskstats(0), temp)
        self.assertEqual([(c.name, c.read_bytes, c.write_bytes) for c in counters],
                         [("nvme0n1", 512_000, 1_024_000), ("sda", 25_600, 30_720)])

    def test_malformed_lines(self):
        self.assertEqual(storage.parse_diskstats("8 0 sda 1 2\n8 0 sdb a b c d e f g h\n"), ())


if __name__ == "__main__":
    unittest.main()
