"""示範模式（--preview）：記憶體中的示範資料，不讀寫任何使用者檔案，也不啟動外部程序。"""
import unittest
from unittest.mock import patch

from sdm.constants import History
from sdm.core.battery import CHARGING, DISCHARGING, LIMIT_REACHED
from sdm.core.history import last_unplugged, valid_sample
from sdm.core.recap import make_recap
from sdm.core.tiers import request_for
from sdm.services import preview
from sdm.services.battery_service import LaptopPower
from sdm.services.power_service import DesktopPower
from sdm.ui.battery_text import header

NOW = 1_800_000_000.0
LAPTOPS = ("laptop-battery", "laptop-charging", "laptop-limit")


class Scenarios(unittest.TestCase):
    def test_names(self):
        self.assertEqual(preview.SCENARIOS, LAPTOPS + ("desktop",))

    def test_laptop_states(self):
        expected = {"laptop-battery": (DISCHARGING, "78%", "還能用 7 小時 25 分"),
                    "laptop-charging": (CHARGING, "62%", "35 分鐘後到 80%"),
                    "laptop-limit": (LIMIT_REACHED, "80%", "暫停充電")}
        for scenario, (state, big, headline) in expected.items():
            with self.subTest(scenario=scenario):
                power = preview.power_service(scenario).sample()
                self.assertIsInstance(power, LaptopPower)
                self.assertEqual(power.state, state)
                self.assertEqual(header(power)[0], big)
                self.assertEqual(header(power)[2], headline)

    def test_charging_scenario_has_only_a_negotiated_adapter(self):
        power = preview.power_service("laptop-charging").sample()
        self.assertEqual((power.battery.adapter_negotiated_w, power.system_w), (25.0, None))

    def test_desktop(self):
        power = preview.power_service("desktop").sample()
        self.assertIsInstance(power, DesktopPower)
        self.assertEqual(power.system_w, 288.0)
        self.assertEqual([gpu.name for gpu in power.gpus], ["示範顯示卡"])
        # 不顯示這台電腦真正的開機時間
        self.assertEqual(power.uptime_s, (3 * 24 + 4) * 3600)
        self.assertEqual(preview.power_service("laptop-limit").sample().uptime_s,
                         (3 * 24 + 4) * 3600)

    def test_unknown_scenario(self):
        with self.assertRaises(ValueError):
            preview.power_service("tablet")


class DemoHistory(unittest.TestCase):
    def test_seven_days_of_valid_rows_ending_now(self):
        for scenario in preview.SCENARIOS:
            with self.subTest(scenario=scenario):
                samples = preview.demo_history(scenario, NOW)
                self.assertTrue(all(valid_sample(sample, NOW) for sample in samples))
                self.assertEqual(samples[-1].t, NOW)
                self.assertGreaterEqual(NOW - samples[0].t, History.RETENTION_SECONDS - 600)
                self.assertEqual(list(samples), sorted(samples, key=lambda item: item.t))

    def test_laptop_rows_have_percent_and_desktop_rows_do_not(self):
        self.assertTrue(all(row.pct is not None for row in preview.demo_history("laptop-limit",
                                                                                NOW)))
        desktop = preview.demo_history("desktop", NOW)
        self.assertTrue(all(row.pct is None and row.ups_pct == 100.0 for row in desktop))

    def test_history_matches_the_current_state(self):
        battery = preview.demo_history("laptop-battery", NOW)
        self.assertEqual((battery[-1].pct, battery[-1].plugged), (78, False))
        self.assertEqual(last_unplugged(battery, NOW, None), NOW - 130 * 60)
        charging = preview.demo_history("laptop-charging", NOW)
        self.assertEqual((charging[-1].pct, charging[-1].plugged, charging[-1].charging),
                         (62, True, True))
        self.assertEqual(preview.demo_history("laptop-limit", NOW)[-1].pct, 80)
        # 最後一筆的整機耗電與目前的讀值相同，圖表與之後的紀錄接得起來
        self.assertEqual((battery[-1].watts, charging[-1].watts), (6.5, None))
        # 接電時量不到整機耗電：示範歷史只有用電池的時段有耗電值
        self.assertTrue(all(row.watts is None for row in charging if row.plugged))
        self.assertIsNone(preview.demo_history("laptop-limit", NOW)[-1].watts)
        self.assertEqual(preview.demo_history("desktop", NOW)[-1].watts, 288.0)

    def test_today_has_a_recap(self):
        recap = make_recap(preview.demo_history("laptop-battery", NOW), NOW)
        self.assertIsNotNone(recap.start_percent)
        self.assertGreater(recap.used_percent, 0)


class Collectors(unittest.TestCase):
    def test_no_files_programs_or_system_bus(self):
        with patch("sdm.data.collectors.load_json", side_effect=AssertionError("file read")), \
                patch("sdm.data.collectors.Gio.bus_get_sync",
                      side_effect=AssertionError("system bus")):
            collector = preview.PreviewCollectors("laptop-battery")
        collector.nvidia.start()
        collector.nvidia.stop()
        self.assertEqual(collector.nvidia.latest(), ())
        self.assertIsNone(collector.bus)
        devices = collector.devices()
        self.assertEqual(devices["errors"], [])
        self.assertTrue(all(device["name"].startswith("示範") for device in devices["devices"]))
        self.assertEqual(collector.logitech(), {})
        with patch("sdm.data.collectors.command", side_effect=AssertionError("lsblk")):
            collector.refresh_disk_meta()
        self.assertEqual([disk["model"] for disk in collector.disk_meta], ["示範固態硬碟", "示範隨身碟"])
        self.assertIsInstance(collector.power_refresh()["power"], LaptopPower)

    def test_overview_data_is_demo_only(self):
        machine = AssertionError("this machine's data was read")
        with patch("sdm.data.collectors.read", side_effect=machine), \
                patch("sdm.data.collectors.hwmon_temperatures", side_effect=machine), \
                patch("sdm.data.collectors.hwmon_fans", side_effect=machine):
            collector = preview.PreviewCollectors("desktop")
            system, slow = collector.system(), collector.slow()
        self.assertEqual(system["cpu_info"]["name"], "示範處理器")
        self.assertTrue(system["networks"])
        self.assertTrue(all(net["name"].startswith("示範") for net in system["networks"]))
        self.assertTrue(0 <= system["cpu"] <= 100 and 0 <= system["ram"] <= 100)
        self.assertEqual([disk["name"] for disk in system["disks"]], ["nvme0n1", "sdb"])
        self.assertTrue(all(sensor["chip"].startswith("示範") for sensor in slow["temperatures"]))
        self.assertTrue(all(fan["name"].startswith("示範") for fan in slow["fans"]))
        self.assertTrue(slow["cpu_temp_label"].startswith("示範"))
        self.assertEqual(slow["gpu"].name, "示範顯示卡")
        self.assertEqual(slow["power"].system_w, 288.0)
        self.assertEqual([disk["model"] for disk in slow["storage"]], ["示範固態硬碟", "示範隨身碟"])

    def test_monitor_pages_are_demo_only(self):
        machine = AssertionError("this machine's data was read")
        everything = request_for({"overview", "cpu", "memory", "disk", "network"})
        with patch("sdm.data.detail.read", side_effect=machine), \
                patch("sdm.data.cgroups.scan", side_effect=machine), \
                patch("sdm.data.sensors.hwmon_temperatures", side_effect=machine), \
                patch("sdm.data.collectors.Gio.bus_get_sync", side_effect=machine):
            collector = preview.PreviewCollectors("desktop")
            snapshot = collector.detail(everything)
            histories = collector.histories(snapshot.stamp)
        self.assertEqual(len(snapshot.cpu.sample.cores), 16)
        labels = dict((app_id, name) for app_id, name, _ in snapshot.apps.labels)
        self.assertTrue(all(name.startswith("示範") for name in labels.values()))
        self.assertTrue(all(usage.app_id in labels or usage.app_id.startswith("@")
                            for usage in snapshot.apps.sample.usages))
        self.assertTrue(all(fan.name.startswith("示範") for fan in snapshot.thermal.fans))
        self.assertEqual([disk.model for disk in snapshot.disks], ["示範固態硬碟", "示範隨身碟"])
        self.assertTrue(snapshot.disks[1].external)
        self.assertEqual(snapshot.disks[0].smart.health_percent, 98)
        self.assertTrue(snapshot.network.primary.name.startswith("示範"))
        self.assertTrue(snapshot.network.address.startswith("192.0.2."))
        self.assertTrue(all(item.name.startswith("示範") for item in snapshot.network.others))
        self.assertEqual(sorted(histories), ["cpu", "disk:nvme0n1", "disk:sdb", "memory", "network"])
        self.assertGreater(len(histories["cpu"].points), 50)

    def test_monitor_pages_follow_the_request(self):
        collector = preview.PreviewCollectors("desktop")
        snapshot = collector.detail(request_for({"memory"}))
        self.assertIsNone(snapshot.cpu)
        self.assertTrue(snapshot.apps.has_memory and not snapshot.apps.has_cpu)
        self.assertIsNone(collector.detail(request_for({"overview"})).disks)

    def test_demo_curves_lead_into_the_live_values(self):
        collector = preview.PreviewCollectors("desktop")
        now = 1_800_000_000.0
        with patch("sdm.services.preview.time.time", return_value=now):
            histories = collector.histories(500.0)
        last = histories["cpu"].points[-1]
        self.assertEqual(last.t, 498.0)
        self.assertEqual(last.a, preview.demo_cpu_percent(now - 2))
        memory = histories["memory"].points[-1].a
        self.assertAlmostEqual(memory, preview.demo_ram_used(now - 2) / preview.DEMO_RAM_BYTES * 100)
        self.assertEqual(histories["network"].points[-1].a, preview.demo_rates(now - 2)[0])

    def test_memory_page_matches_the_overview(self):
        collector = preview.PreviewCollectors("desktop")
        now = 1_800_000_000.0
        with patch("sdm.services.preview.time.time", return_value=now):
            system = collector.system()
            memory = collector.detail(request_for({"memory"})).memory.composition
        self.assertEqual(memory.used, system["ram_used"])
        self.assertEqual(memory.total, system["ram_total"])
        self.assertEqual(sum(value for _, value in memory.segments()), memory.total)

    def test_settings_are_defaults(self):
        settings = preview.preview_settings()
        self.assertFalse(settings.first_run)
        self.assertTrue(all(settings.legacy.values()))
        self.assertFalse(settings.preferences.read_only)


if __name__ == "__main__":
    unittest.main()
