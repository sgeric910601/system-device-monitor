"""設定 → 頂端列：分開顯示或合併成一個、耗電（筆電為電池）項目的顯示格式、要顯示的項目。

舊版的五個項目仍寫回 settings.json；關閉的監控模組的項目停用。
"""
from sdm.ui.monitor_widgets import card
from sdm.ui.settings import text
from sdm.ui.settings.controls import wrapped
from sdm.ui.tray_text import module_enabled
from sdm.ui.widgets import box

ITEMS = (("cpu", "CPU 使用率"), ("temperature", "CPU 溫度"), ("ram", "記憶體使用率"),
         ("gpu", "GPU 使用率"), ("network", "下載／上傳速度"))
MODE_NOTES = {
    "split": "每個勾選的項目在頂端列各有一個圖示。",
    "merged": "頂端列只留一個圖示，勾選的項目依序串成一行，例如「297 W｜CPU 12%｜RAM 34%」；"
              "點一下打開選單。頂端列太擠時使用。"}
ITEMS_NOTE = ("灰色的項目屬於已關閉的監控模組。頂端列需要 GNOME 的 AppIndicator 擴充功能。")


def display_note(laptop, threshold):
    if laptop:
        return (f"電量低於 {threshold}% 且正在放電時電池變紅色，接著電源時為綠色。"
                "「只有百分比」時 GNOME 仍會保留圖示的位置。")
    return "桌機顯示整機耗電瓦數，「只有圖示」時不顯示瓦數；不斷電系統改用電池時圖示變成橘色。"


def build(controls, context):
    store, page = controls.store, box(spacing=16)
    item = "電池" if context.laptop else "耗電"
    page.pack_start(card("顯示方式", controls.radios("tray_mode", text.TRAY_MODES),
                         controls.note(lambda: MODE_NOTES[store.preferences.tray_mode],
                                       {"tray_mode"})), False, False, 0)
    page.pack_start(card(
        f"{item}項目",
        controls.choice("tray_display", "顯示格式", text.DISPLAY_FORMATS),
        controls.note(lambda: display_note(context.laptop, store.preferences.low_threshold),
                      {"low_threshold"})), False, False, 0)
    checks = [controls.check("tray_power", "電池電量" if context.laptop else "整機耗電")]
    for key, title in ITEMS:
        checks.append(controls.check(key, title, when=lambda key=key: module_enabled(
            store.preferences, key)))
    page.pack_start(card(f"頂端列項目（{item}在最左邊）", *checks,
                         wrapped(ITEMS_NOTE, "muted")), False, False, 0)
    return page

