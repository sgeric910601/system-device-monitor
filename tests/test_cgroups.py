"""讀取使用者與系統的程序群組（假的 /sys/fs/cgroup，App 名稱都是虛構的）。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sdm.core.apps import SESSION_ID, SYSTEM_ID
from sdm.data import cgroups

UID = 1000


def unit(directory, usec=None, anon=None, current=None):
    directory.mkdir(parents=True, exist_ok=True)
    if usec is not None:
        (directory/"cpu.stat").write_text(f"usage_usec {usec}\nuser_usec 1\nsystem_usec 2\n")
    if anon is not None:
        (directory/"memory.stat").write_text(f"anon {anon}\nfile 99\nkernel 7\n")
    if current is not None:
        (directory/"memory.current").write_text(f"{current}\n")
    return directory


class Scan(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        cgroup = self.root/"sys/fs/cgroup"
        user = cgroup/f"user.slice/user-{UID}.slice"
        service = user/f"user@{UID}.service"
        app = service/"app.slice"
        unit(app/"app-gnome-example\\x2dbrowser-225.scope", 3_000_000, 500, 900)
        unit(app/"app-com.example.Editor-77.scope", 1_000, None, 400)
        unit(app/"app-org.example.Terminal.slice", 2_000, 300)
        unit(app/"example-helper.service", 5, 10)
        unit(app/"dbus.socket", 1, 1)
        unit(service/"background.slice/example-indexer.service", 7, 8)
        unit(service/"session.slice", 40_000, 4_000)
        unit(user/"session-2.scope", 60_000, 6_000)
        unit(user/"other.scope", 1, 1)
        unit(cgroup/"system.slice", 90_000, 9_000)
        (app/"cgroup.procs").write_text("")

    def scan(self, **options):
        return {reading.key: reading for reading in cgroups.scan(self.root, UID, **options)}

    def test_apps_desktop_and_system_entries(self):
        readings = self.scan()
        self.assertEqual(
            sorted((r.app_id, r.usage_usec, r.memory) for r in readings.values()),
            sorted([("@session", 40_000, 4_000), ("@session", 60_000, 6_000),
                    ("@system", 90_000, 9_000), ("com.example.Editor", 1_000, 400),
                    ("example-browser", 3_000_000, 500), ("example-helper", 5, 10),
                    ("example-indexer", 7, 8), ("org.example.Terminal", 2_000, 300)]))
        self.assertEqual((SESSION_ID, SYSTEM_ID), ("@session", "@system"))
        self.assertNotIn("dbus.socket", readings)

    def test_only_the_requested_files_are_read(self):
        readings = self.scan(cpu=False)
        self.assertTrue(all(r.usage_usec is None and r.memory is not None
                            for r in readings.values()))
        readings = self.scan(memory=False)
        self.assertTrue(all(r.memory is None and r.usage_usec is not None
                            for r in readings.values()))

    def test_missing_hierarchy_gives_nothing(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(cgroups.scan(Path(empty), UID), ())

    def test_unreadable_counters(self):
        self.assertIsNone(cgroups.usage_usec("usage_usec x\n"))
        self.assertIsNone(cgroups.usage_usec(""))
        self.assertEqual(cgroups.usage_usec("user_usec 3\nusage_usec 12\n"), 12)
        self.assertIsNone(cgroups.anon_bytes("file 1\n"))
        self.assertEqual(cgroups.anon_bytes("anon 42\n"), 42)

    def test_symbolic_links_are_not_followed(self):
        app = self.root/f"sys/fs/cgroup/user.slice/user-{UID}.slice/user@{UID}.service/app.slice"
        outside = unit(self.root/"outside", 1, 1)
        (app/"app-com.example.Link-1.scope").symlink_to(outside)
        self.assertNotIn("app-com.example.Link-1.scope", self.scan())

    def test_cpu_count_comes_from_the_system(self):
        with patch("sdm.data.cgroups.os.cpu_count", return_value=None):
            self.assertEqual(cgroups.cpu_count(), 1)


if __name__ == "__main__":
    unittest.main()
