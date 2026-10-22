"""取樣分層：只有頁面顯示時才做的詳細取樣，以及丟棄隱藏前發出的取樣結果。"""
import unittest

from sdm.core import tiers
from sdm.core.tiers import DetailGate, DetailRequest


class Requests(unittest.TestCase):
    def test_each_page_asks_only_for_what_it_shows(self):
        self.assertEqual(tiers.request_for({"cpu"}),
                         DetailRequest(cpu=True, thermal=True, apps_cpu=True))
        self.assertEqual(tiers.request_for({"memory"}),
                         DetailRequest(memory=True, apps_memory=True))
        self.assertEqual(tiers.request_for({"disk"}),
                         DetailRequest(disks=True, smart=True, smart_attributes=True))
        self.assertEqual(tiers.request_for({"network"}), DetailRequest(network=True))
        self.assertEqual(tiers.request_for({"overview"}), DetailRequest(smart=True))

    def test_pages_are_combined(self):
        request = tiers.request_for({"overview", "memory"})
        self.assertEqual(request, DetailRequest(smart=True, memory=True, apps_memory=True))

    def test_nothing_visible_means_no_detail_sampling(self):
        self.assertIsNone(tiers.request_for(set()))
        self.assertIsNone(tiers.request_for({"power", "devices", "settings"}))

    def test_storage_visibility_controls_the_lsblk_cadence(self):
        self.assertTrue(tiers.storage_visible({"overview"}))
        self.assertTrue(tiers.storage_visible({"disk"}))
        self.assertFalse(tiers.storage_visible({"cpu", "network"}))
        self.assertFalse(tiers.storage_visible(set()))


class Gate(unittest.TestCase):
    def test_results_from_before_a_change_are_dropped(self):
        gate = DetailGate()
        self.assertIsNone(gate.ticket())
        cpu, memory = tiers.request_for({"cpu"}), tiers.request_for({"memory"})
        self.assertTrue(gate.update(cpu))
        first = gate.ticket()
        self.assertTrue(gate.accepts(first))
        self.assertFalse(gate.update(cpu))           # 沒有改變：同一個世代
        self.assertTrue(gate.accepts(first))
        self.assertTrue(gate.update(None))           # 視窗關閉
        self.assertIsNone(gate.ticket())
        self.assertFalse(gate.accepts(first))
        gate.update(cpu)                             # 再打開：舊的結果仍然不採用
        self.assertFalse(gate.accepts(first))
        self.assertTrue(gate.accepts(gate.ticket()))
        gate.update(memory)
        self.assertFalse(gate.accepts(first))
        self.assertEqual(gate.request, memory)


if __name__ == "__main__":
    unittest.main()
