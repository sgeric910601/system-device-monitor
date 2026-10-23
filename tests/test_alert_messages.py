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

    def test_ipv6_addresses_are_masked(self):
        cases = {"耳機 2001:db8::8a2e:370:7334": "耳機 x::x",
                 "fe80::1ff:fe23:4567:890a 鍵盤": "x::x 鍵盤",
                 "2001:0db8:0000:0000:0000:ff00:0042:8329": "x::x",
                 "Logi::Mouse": "Logi::Mouse", "12:30:45": "12:30:45"}
        for raw, masked in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(mask_addresses(raw), masked)

    def test_serial_like_tokens_are_masked(self):
        cases = {"MX Master SN1A2B3C4D": "MX Master xxxx", "滑鼠ABC12345": "滑鼠xxxx",
                 "G305": "G305", "Basilisk V3": "Basilisk V3", "K380 鍵盤": "K380 鍵盤",
                 "12345678": "12345678", "Keyboards": "Keyboards",
                 "WH1000XM4": "xxxx"}             # 型號也可能被遮住：寧可多遮
        for raw, masked in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(mask_addresses(raw), masked)

    def test_peripheral_notification_never_shows_a_serial(self):
        reading = type("Reading", (), dict(name=messages.safe_name("滑鼠 SN0A1B2C3D4E"),
                                           percent=9, icon="x", key="k"))()
        alert = messages.peripheral_low(reading, 10)
        self.assertEqual(alert.title, "滑鼠 xxxx 電量偏低：9%")

    def test_safe_name_cleans_and_limits(self):
        self.assertEqual(messages.safe_name("滑鼠\x1b[31m"), "滑鼠 [31m")
        self.assertEqual(len(messages.safe_name("長" * 200)), messages.NAME_MAX_CHARS)
        self.assertEqual(messages.safe_name("  "), "周邊設備")

    def test_clean_text_lives_only_in_the_core(self):
        self.assertFalse(hasattr(sources, "clean_text"))
        self.assertEqual(clean_text("a\x00b", 10), "a b")


if __name__ == "__main__":
    unittest.main()
