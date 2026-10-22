"""各 App 的 CPU 與記憶體：systemd 單元名稱解析、計數差值與排行（名稱都是虛構的）。

不照搬：MonitorTests.testTopParsing 與 ProcessOwner（macOS 以 top 輸出與 .app 路徑歸類程序；
Linux 每個 App 有自己的程序群組，直接讀 cpu.stat 與 memory.stat）。
"""
import unittest

from sdm.core import apps
from sdm.core.apps import AppTracker, CgroupReading

AT = "@"        # systemd 實例名稱的分隔符號；分開寫，隱私檢查才不會誤認成電子郵件


class Names(unittest.TestCase):
    def test_unescape(self):
        self.assertEqual(apps.unescape("org.example.photo\\x2dviewer"), "org.example.photo-viewer")
        self.assertEqual(apps.unescape("a\\x20b"), "a b")
        self.assertEqual(apps.unescape("bad\\xZZ"), "bad\\xZZ")
        self.assertEqual(apps.unescape("tail\\x2"), "tail\\x2")

    def test_scopes_services_and_slices(self):
        cases = {
            "app-gnome-org.example.photo\\x2dviewer-77.scope": "org.example.photo-viewer",
            "app-gnome-example\\x2dbrowser-225.scope": "example-browser",
            "app-com.example.Editor-537838.scope": "com.example.Editor",
            "app-flatpak-com.example.Chat-1234.scope": "com.example.Chat",
            "app-org.example.Sync.service": "org.example.Sync",
            "app-gnome-org.example.Tool" + AT + "4f2a.service": "org.example.Tool",
            "app-org.example.Terminal.slice": "org.example.Terminal",
            "app-gnome\\x2dsession\\x2dmanager.slice": "gnome-session-manager",
            "example-helper.service": "example-helper",
            "example-worker" + AT + "2.service": "example-worker",
        }
        for unit, expected in cases.items():
            with self.subTest(unit=unit):
                self.assertEqual(apps.parse_unit(unit), expected)

    def test_rejected_units(self):
        for unit in ("dbus.socket", "app-.scope", "app-123.scope", "app.slice", "",
                     "../app-x-1.scope", "app-..-1.scope", "app-a/b-1.scope", ".hidden.service",
                     "app-\\x2f\\x2e\\x2e-1.scope", "app-x\\x0a-1.scope", "init.scope",
                     "app-" + "a" * 300 + "-1.scope", "cgroup.procs"):
            with self.subTest(unit=unit):
                self.assertIsNone(apps.parse_unit(unit))


def reading(key, app_id, usec=None, memory=None):
    return CgroupReading(key, app_id, usec, memory)


class Deltas(unittest.TestCase):
    def test_first_sample_sets_the_baseline_only(self):
        tracker = AppTracker()
        result = tracker.sample((reading("a-1", "org.example.A", 1_000_000, 300),), 10.0, 4)
        self.assertFalse(result.ready)
        self.assertEqual([(u.app_id, u.core_percent, u.memory) for u in result.usages],
                         [("org.example.A", None, 300)])

    def test_percent_of_one_core_and_of_all_cores(self):
        tracker = AppTracker()
        tracker.sample((reading("a-1", "org.example.A", 1_000_000),), 10.0, 4)
        result = tracker.sample((reading("a-1", "org.example.A", 4_000_000),), 12.0, 4)
        usage = result.usages[0]
        self.assertTrue(result.ready)
        self.assertAlmostEqual(usage.core_percent, 150.0)    # 1.5 秒 CPU ÷ 2 秒
        self.assertAlmostEqual(usage.machine_percent, 37.5)  # 四個核心

    def test_instances_of_the_same_app_are_merged(self):
        tracker = AppTracker()
        first = (reading("a-1", "org.example.A", 0, 100), reading("a-2", "org.example.A", 0, 50),
                 reading("b-1", "org.example.B", 0, 10))
        tracker.sample(first, 0.0, 2)
        second = (reading("a-1", "org.example.A", 500_000, 120),
                  reading("a-2", "org.example.A", 500_000, 60),
                  reading("b-1", "org.example.B", 200_000, None))
        usages = {u.app_id: u for u in tracker.sample(second, 1.0, 2).usages}
        self.assertAlmostEqual(usages["org.example.A"].core_percent, 100.0)
        self.assertEqual(usages["org.example.A"].memory, 180)
        self.assertAlmostEqual(usages["org.example.B"].core_percent, 20.0)
        self.assertIsNone(usages["org.example.B"].memory)

    def test_negative_delta_resets_that_unit(self):
        tracker = AppTracker()
        tracker.sample((reading("a-1", "org.example.A", 900_000),
                        reading("a-2", "org.example.A", 100)), 0.0, 1)
        result = tracker.sample((reading("a-1", "org.example.A", 10),           # 單元重新建立
                                 reading("a-2", "org.example.A", 200_100)), 2.0, 1)
        self.assertAlmostEqual(result.usages[0].core_percent, 10.0)
        result = tracker.sample((reading("a-1", "org.example.A", 2_000_010),), 4.0, 1)
        self.assertAlmostEqual(result.usages[0].core_percent, 100.0)

    def test_new_units_and_unknown_counters_have_no_cpu_yet(self):
        tracker = AppTracker()
        tracker.sample((reading("a-1", "org.example.A", 0),), 0.0, 1)
        result = tracker.sample((reading("a-1", "org.example.A", 100_000),
                                 reading("c-1", "org.example.C", 5_000_000),
                                 reading("d-1", "org.example.D", None, 5)), 1.0, 1)
        usages = {u.app_id: u.core_percent for u in result.usages}
        self.assertAlmostEqual(usages["org.example.A"], 10.0)
        self.assertIsNone(usages["org.example.C"])
        self.assertIsNone(usages["org.example.D"])

    def test_a_long_gap_starts_a_new_baseline(self):
        tracker = AppTracker()
        tracker.sample((reading("a-1", "org.example.A", 0),), 0.0, 1)
        result = tracker.sample((reading("a-1", "org.example.A", 100_000),), 600.0, 1)
        self.assertFalse(result.ready)
        self.assertIsNone(result.usages[0].core_percent)
        result = tracker.sample((reading("a-1", "org.example.A", 300_000),), 602.0, 1)
        self.assertAlmostEqual(result.usages[0].core_percent, 10.0)


class Ranking(unittest.TestCase):
    def test_top_five_by_value_merged_by_display_name(self):
        values = {"a": 5.0, "b": 50.0, "c": 7.0, "d": 1.0, "e": 0.0, "f": None, "g": 3.0,
                  "h": 2.0, "i": 4.0}
        names = {"a": "Browser", "c": "Browser"}
        ranked = apps.rank(values.items(), lambda app_id: names.get(app_id, app_id.upper()))
        self.assertEqual([(row.name, row.value) for row in ranked],
                         [("B", 50.0), ("Browser", 12.0), ("I", 4.0), ("G", 3.0), ("H", 2.0)])
        self.assertEqual(ranked[1].app_ids, ("a", "c"))

    def test_limit_and_empty(self):
        self.assertEqual(apps.rank((), str), ())
        self.assertEqual(len(apps.rank((("x", 1.0), ("y", 2.0)), str, limit=1)), 1)


if __name__ == "__main__":
    unittest.main()
