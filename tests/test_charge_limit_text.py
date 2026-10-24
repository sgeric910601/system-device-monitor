"""充電上限滑桿的文字：數值、狀態列（套用中…／已套用／未套用：…）、連結與可否操作。"""
from dataclasses import replace
import unittest

from sdm.constants import ChargeLimit
from sdm.data.charge_conflicts import Conflict
from sdm.services.charge_control import ChargeState, LAYOUTS
from sdm.ui import charge_limit_text as text

READY = ChargeState(supply="BAT0", limit=80, layout=LAYOUTS[0], protocol=True)


class Values(unittest.TestCase):
    def test_value_and_marks(self):
        self.assertEqual(text.value_text(85), "85%")
        self.assertEqual(text.value_text(100), "不限制")
        self.assertEqual([text.mark_text(value) for value in ChargeLimit.OPTIONS],
                         ["80", "85", "90", "95", "100"])


class StatusLine(unittest.TestCase):
    def line(self, **changes):
        return text.status_line(replace(READY, **changes))

    def test_states(self):
        self.assertEqual(self.line(supply=None), ("", text.TONE_MUTED))
        self.assertEqual(self.line(busy=True), ("套用中…", text.TONE_MUTED))
        self.assertEqual(self.line(outcome="applied"), ("已套用", text.TONE_MUTED))
        self.assertEqual(self.line(outcome="failed", message="未授權"),
                         ("未套用：未授權", text.TONE_WARNING))
        self.assertEqual(self.line(layout=None, protocol=None)[1], text.TONE_WARNING)
        self.assertIn("安裝", self.line(layout=None, protocol=None)[0])
        self.assertIn("重新安裝", self.line(protocol=False)[0])
        self.assertIn("確認", self.line(protocol=None)[0])
        self.assertIn("放開", self.line()[0])
        self.assertIn("放開", self.line(draft=85)[0])

    def test_demo_never_writes(self):
        demo = ChargeState(supply="BAT0", limit=80, demo=True)
        self.assertEqual(text.status_line(demo), (text.DEMO, text.TONE_MUTED))
        self.assertEqual(text.status_line(replace(demo, outcome="applied")),
                         ("已套用（" + text.DEMO + "）", text.TONE_MUTED))
        self.assertEqual(text.status_line(replace(demo, busy=True)), ("套用中…", text.TONE_MUTED))


class Flags(unittest.TestCase):
    def test_settings_link_only_when_components_are_missing(self):
        self.assertTrue(text.needs_settings(replace(READY, layout=None, protocol=None)))
        self.assertTrue(text.needs_settings(replace(READY, protocol=False)))
        self.assertFalse(text.needs_settings(READY))
        self.assertFalse(text.needs_settings(replace(READY, supply=None)))
        self.assertFalse(text.needs_settings(ChargeState(supply="BAT0", demo=True)))

    def test_slider_and_switch_sensitivity(self):
        self.assertTrue(text.slider_enabled(READY))
        self.assertTrue(text.slider_enabled(replace(READY, busy=True)))
        self.assertFalse(text.slider_enabled(replace(READY, protocol=None)))
        self.assertTrue(text.persist_enabled(READY))
        self.assertFalse(text.persist_enabled(replace(READY, busy=True)))
        self.assertFalse(text.persist_enabled(replace(READY, draft=85)))
        self.assertFalse(text.persist_enabled(replace(READY, layout=None)))

    def test_visible_only_with_an_adjustable_battery(self):
        self.assertTrue(text.visible(READY))
        self.assertFalse(text.visible(replace(READY, supply=None)))

    def test_conflicts_are_listed_one_per_line(self):
        state = replace(READY, conflicts=(Conflict("tlp", "甲。"), Conflict("asusd", "乙。")))
        self.assertEqual(text.conflict_text(state), "甲。\n乙。")
        self.assertEqual(text.conflict_text(READY), "")


if __name__ == "__main__":
    unittest.main()
