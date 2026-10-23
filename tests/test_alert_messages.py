"""通知文字（純邏輯）：測試通知、圖示、緊急程度，以及名稱不含位址。"""
import unittest

from sdm.core import alert_messages as messages
from sdm.core.fmt import clean_text, mask_addresses
from sdm.data import sources


class Messages(unittest.TestCase):
    def test_test_notification(self):
        alert = messages.test_alert()
        self.assertEqual((alert.title, alert.body), ("系統與裝置監看", "通知功能正常。"))
        self.assertEqual((alert.kind, alert.icon, alert.critical),
                         (messages.TEST, "utilities-system-monitor", False))

    def test_key_separates_kinds_and_devices(self):
        first = messages.Alert(kind="a", title="", body="", icon="", device="1")
        self.assertEqual(first.key, ("a", "1"))

    def test_ups_icons_and_urgency(self):
        self.assertEqual(messages.ups_comm_lost().icon, "dialog-warning-symbolic")
        self.assertEqual(messages.ups_comm_lost().body,
                         "讀不到不斷電系統的狀態，請檢查連接線與 apcupsd 服務。")
        self.assertEqual(messages.ups_on_mains(None).icon, "ac-adapter-symbolic")

    def test_percent_text_rounds_half_up(self):
        self.assertEqual(messages.whole(49.5), 50)
        self.assertEqual(messages.whole(20.4), 20)


class Privacy(unittest.TestCase):
    def test_addresses_are_masked(self):
        mac = ":".join(["0b"] * 6).upper()
        self.assertEqual(mask_addresses(f"耳機 {mac}"), "耳機 xx:xx:xx:xx:xx:xx")
        self.assertEqual(mask_addresses("dev_" + "_".join(["0c"] * 6)),
                         "dev_xx:xx:xx:xx:xx:xx")
        self.assertEqual(mask_addresses("在 198.51.100.7 上"), "在 x.x.x.x 上")
        self.assertEqual(mask_addresses("MX Master 3"), "MX Master 3")

    def test_safe_name_cleans_and_limits(self):
        self.assertEqual(messages.safe_name("滑鼠\x1b[31m"), "滑鼠 [31m")
        self.assertEqual(len(messages.safe_name("長" * 200)), messages.NAME_MAX_CHARS)
        self.assertEqual(messages.safe_name("  "), "周邊設備")

    def test_clean_text_lives_only_in_the_core(self):
        self.assertFalse(hasattr(sources, "clean_text"))
        self.assertEqual(clean_text("a\x00b", 10), "a b")


if __name__ == "__main__":
    unittest.main()
