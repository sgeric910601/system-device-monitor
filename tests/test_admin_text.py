"""設定 → 管理員功能的文字（不載入 GTK）：元件狀態、安裝指令、取樣狀態與旁路說明。"""
from pathlib import Path
import unittest

from sdm.constants import CpuPower
from sdm.services.privileged import (AdminState, Layout, SOCKET_ABSENT, SOCKET_MINE,
                                     SOCKET_OTHER)
from sdm.services.charge_control import LAYOUTS as LIMIT_LAYOUTS, ChargeState
from sdm.ui import admin_text as text

DEB = Layout("deb", CpuPower.DEB_HELPER, CpuPower.DEB_SOCKET)
LOCAL = Layout("local", CpuPower.LOCAL_HELPER, CpuPower.LOCAL_SOCKET)
CHECKOUT = Path("/opt/system-device-monitor")
PACKAGE = Path("/usr/share/system-device-monitor")


class Components(unittest.TestCase):
    def test_install_titles(self):
        self.assertEqual(text.component_title(AdminState(DEB, True)), "已安裝（.deb）")
        self.assertEqual(text.component_title(AdminState(LOCAL, True)), "已安裝（原始碼）")
        self.assertEqual(text.component_title(AdminState()), "未安裝")
        self.assertEqual(text.component_title(AdminState(LOCAL, None)), "已安裝（原始碼）・確認版本中…")
        self.assertEqual(text.component_title(AdminState(LOCAL, False)),
                         "已安裝（原始碼）・需要重新安裝管理員元件")

    def test_source_checkout_gets_the_exact_install_command(self):
        self.assertEqual(text.install_command(AdminState(), CHECKOUT),
                         "/usr/bin/python3 -B /opt/system-device-monitor/install.py "
                         "--system-components install")
        self.assertEqual(text.install_command(AdminState(LOCAL, False), CHECKOUT),
                         text.install_command(AdminState(), CHECKOUT))
        self.assertIsNone(text.install_command(AdminState(LOCAL, True), CHECKOUT))

    def test_package_install_never_shows_a_source_command(self):
        self.assertIsNone(text.install_command(AdminState(), PACKAGE))
        self.assertIsNone(text.install_command(AdminState(DEB, False), CHECKOUT))
        self.assertIn("重新安裝", text.component_note(AdminState(), PACKAGE))
        self.assertIn("重新安裝", text.component_note(AdminState(DEB, False), CHECKOUT))

    def test_component_notes(self):
        self.assertIn("終端機", text.component_note(AdminState(), CHECKOUT))
        self.assertIn("終端機", text.component_note(AdminState(LOCAL, False), CHECKOUT))
        self.assertIn("移除", text.component_note(AdminState(LOCAL, True), CHECKOUT))
        self.assertIn("apt", text.component_note(AdminState(DEB, True), PACKAGE))


class Sampling(unittest.TestCase):
    def test_status_lines(self):
        cases = (
            (AdminState(), False, "需要先安裝管理員元件"),
            (AdminState(LOCAL, False), True, "需要重新安裝管理員元件"),
            (AdminState(LOCAL, True, busy=True), True, "等待管理員授權…"),
            (AdminState(LOCAL, True, SOCKET_MINE), True, "已開啟・只在「電源」頁顯示時取樣"),
            (AdminState(LOCAL, True, SOCKET_ABSENT), False, "未開啟"),
            (AdminState(LOCAL, True, SOCKET_MINE), False, "服務仍開啟中，本程式不會連線；關閉需要管理員授權"),
            (AdminState(LOCAL, True, SOCKET_OTHER), False, "由這台電腦的其他使用者開啟；開啟此選項會改為只允許你連線"),
            (AdminState(LOCAL, True, SOCKET_ABSENT), True, "未開啟"),
        )
        for state, preference, expected in cases:
            with self.subTest(state=state, preference=preference):
                self.assertEqual(text.sampling_status(state, preference), expected)

    def test_last_error(self):
        self.assertEqual(text.error_line(AdminState(error="未授權：已取消授權或密碼不正確")),
                         "上次的錯誤：未授權：已取消授權或密碼不正確")
        self.assertEqual(text.error_line(AdminState()), "")

    def test_explanations(self):
        self.assertIn("RAPL", text.EXPLANATION)
        self.assertIn("管理員授權", text.EXPLANATION)
        for words in ("旁路", "只有你的帳號", "任何程式", "0.1 W", "固定 1 秒"):
            self.assertIn(words, text.SIDE_CHANNEL)

    def test_full_width_punctuation(self):
        for value in (text.EXPLANATION, text.SIDE_CHANNEL, text.STARTUP_OFF):
            for mark in (",", ";", ":", "(", ")"):
                self.assertNotIn(mark, value)



class ChargeComponent(unittest.TestCase):
    installed = ChargeState(supply="BAT0", layout=LIMIT_LAYOUTS[1], protocol=True)

    def test_component_lines(self):
        cases = (
            (None, ""),
            (self.installed, "充電上限元件：已安裝（原始碼）"),
            (ChargeState(supply="BAT0", layout=LIMIT_LAYOUTS[0], protocol=True),
             "充電上限元件：已安裝（.deb）"),
            (ChargeState(supply="BAT0"), "充電上限元件：未安裝"),
            (ChargeState(supply="BAT0", layout=LIMIT_LAYOUTS[1]), "充電上限元件：已安裝（原始碼）・確認版本中…"),
            (ChargeState(supply="BAT0", layout=LIMIT_LAYOUTS[1], protocol=False),
             "充電上限元件：已安裝（原始碼）・需要重新安裝"),
            (ChargeState(layout=LIMIT_LAYOUTS[0], protocol=True),
             "充電上限元件：已安裝（.deb）・這台電腦沒有可調整充電上限的電池"),
            (ChargeState(supply="BAT0", demo=True), "充電上限元件：示範模式，不會寫入"),
        )
        for state, expected in cases:
            with self.subTest(state=state):
                self.assertEqual(text.charge_line(state), expected)

    def test_missing_charge_component_shows_the_install_command(self):
        command = text.install_command(AdminState(), CHECKOUT)
        self.assertEqual(text.install_command(AdminState(LOCAL, True), CHECKOUT,
                                              ChargeState(supply="BAT0")), command)
        self.assertEqual(text.install_command(AdminState(LOCAL, True), CHECKOUT,
                                              ChargeState(layout=LIMIT_LAYOUTS[1],
                                                          protocol=False)), command)
        self.assertIsNone(text.install_command(AdminState(LOCAL, True), CHECKOUT, self.installed))
        self.assertIsNone(text.install_command(AdminState(LOCAL, True), CHECKOUT,
                                               ChargeState(supply="BAT0", demo=True)))
        self.assertIsNone(text.install_command(AdminState(LOCAL, True), PACKAGE,
                                               ChargeState(supply="BAT0")))
        self.assertIn("終端機", text.component_note(AdminState(LOCAL, True), CHECKOUT,
                                                  ChargeState(supply="BAT0")))

    def test_notes_mention_both_components(self):
        self.assertIn("充電上限", text.SOURCE_NOTE)
        self.assertIn("CPU 功耗取樣", text.SOURCE_NOTE)

if __name__ == "__main__":
    unittest.main()
