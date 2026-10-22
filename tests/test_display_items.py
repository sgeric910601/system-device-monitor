"""「設定 → 顯示項目」：電力流向細項（上層隱藏時下層一起隱藏）與詳細資訊欄位。"""
import unittest

from sdm.core.display_items import (DESKTOP_DETAIL_FIELDS, FLOW_ITEMS, children, flow_category,
                                    flow_hidden, toggled, visible_flow_items)
from sdm.core.preference_specs import NAME_PATTERN
from sdm.ui.battery_details import DETAIL_FIELDS, detail_fields

ITEMS = (("gpu:nvidia:0", "Example GPU", 61.5), ("gpu:amd:1", "AMD 顯示卡", 20.0),
         ("cpu", "處理器", 40.0), ("other", "其他", 180.0))


class Flow(unittest.TestCase):
    def test_every_id_can_be_stored_in_preferences(self):
        ids = [item.id for item in FLOW_ITEMS] + [field for field, _ in DESKTOP_DETAIL_FIELDS]
        self.assertTrue(all(NAME_PATTERN.fullmatch(name) for name in ids), ids)
        self.assertEqual(len(ids), len(set(ids)))

    def test_categories(self):
        self.assertEqual([flow_category(item[0]) for item in ITEMS],
                         ["gpu_nvidia", "gpu_amd", "cpu", "other"])
        self.assertEqual(flow_category("gpu:intel:2"), "gpu")
        self.assertEqual(flow_category("future"), "other")

    def test_hiding_the_parent_hides_its_children(self):
        self.assertEqual([item.id for item in children("gpu")], ["gpu_nvidia", "gpu_amd"])
        self.assertTrue(flow_hidden("gpu:nvidia:0", ("gpu",)))
        self.assertTrue(flow_hidden("gpu:amd:1", ("gpu",)))
        self.assertFalse(flow_hidden("gpu:amd:1", ("gpu_nvidia",)))
        self.assertEqual([item[0] for item in visible_flow_items(ITEMS, ("gpu", "other"))],
                         ["cpu"])
        self.assertEqual(visible_flow_items(ITEMS, ()), ITEMS)

    def test_toggling_returns_a_new_tuple(self):
        hidden = ("other",)
        self.assertEqual(toggled(hidden, "gpu", visible=False), ("other", "gpu"))
        self.assertEqual(toggled(hidden, "other", visible=True), ())
        self.assertEqual(toggled(hidden, "other", visible=False), hidden)
        self.assertEqual(hidden, ("other",))


class Details(unittest.TestCase):
    def test_fields_follow_the_kind_of_computer(self):
        self.assertEqual(detail_fields(laptop=True), DETAIL_FIELDS)
        self.assertEqual(detail_fields(laptop=False), DESKTOP_DETAIL_FIELDS)
        self.assertEqual(len(DESKTOP_DETAIL_FIELDS), 8)
        self.assertEqual(detail_fields(True, limit_shown_elsewhere=True)[-1][0],
                         "low_power")


if __name__ == "__main__":
    unittest.main()
