"""設定 → 通知：筆電的低電量與充飽提醒（只在有電池時顯示）、不斷電系統提醒（只在有不斷電系統時顯示）、
傳送測試通知，以及通知服務的狀態說明（參考專案 SettingsView 的「通知」卡片）。

所有開關與門檻都經由 SettingsStore 寫入並立即生效；設定檔唯讀時停用（視窗上方說明原因）。
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.ui.monitor_widgets import card
from sdm.ui.settings import text
from sdm.ui.settings.controls import wrapped
from sdm.ui.widgets import box

NOTICE = "_notice"      # 通知服務的狀態改變（不是設定）
POWER = "_power"        # 不斷電系統出現或消失（不是設定）
LOW_NOTE = ("正在放電（或接著電源但電量仍在下降）且電量降到門檻以下時通知一次；"
            "接上電源或電量回升 2% 以上才會再通知。這個門檻也決定頂端列電池圖示何時變紅。")
FULL_NOTE = ("這次接上電源後有充過電，且電量達到門檻或充電上限時通知一次；"
             "拔掉電源或電量比門檻（或上限）低 5% 以上才會再通知。")
UPS_NOTE = ("連續兩次取樣都在用電池時以緊急通知提醒，恢復市電時再通知一次；用電池時電量降到門檻以下"
            "再以緊急通知提醒；通訊中斷時通知一次。")
DESKTOP_NOTE = "這台電腦沒有內建電池，只會有不斷電系統與周邊設備的提醒。"
TEST_NOTE = ("提醒由桌面的通知服務顯示，點通知會開啟相關的頁面；同一種提醒每分鐘最多一則。"
             "啟動與睡眠喚醒後的第一次取樣只記錄狀態，不會馬上跳出提醒。"
             "周邊設備的門檻在「周邊設備」。")
# （開關的鍵、開關名稱、門檻的鍵、門檻名稱、選項、說明）
BATTERY_ALERTS = (
    ("low_alert_enabled", "低電量提醒", "low_threshold", "電量低於", text.LOW_THRESHOLDS,
     LOW_NOTE),
    ("full_alert_enabled", "充飽提醒", "full_threshold", "充到", text.FULL_THRESHOLDS,
     FULL_NOTE))
UPS_ALERT = ("ups_alerts_enabled", "不斷電系統提醒", "ups_low_threshold", "電量低於",
             text.ups_thresholds(50), UPS_NOTE)


def alert_rows(controls, row, options=None):
    switch_key, switch_title, key, title, default_options, note = row
    store = controls.store
    return (controls.switch(switch_key, switch_title),
            controls.choice(key, title, options or default_options,
                            when=lambda: bool(store.value(switch_key))),
            wrapped(note, "muted"))


def battery_card(controls):
    rows = []
    for row in BATTERY_ALERTS:
        rows.extend(alert_rows(controls, row))
    return card("電池提醒", *rows)


def shown_when(controls, widget, keys, predicate):
    """只在 predicate() 成立時顯示 widget；視窗的 show_all() 不會把隱藏的卡片打開。"""
    widget.show_all()
    widget.set_no_show_all(True)
    controls.bind(keys, lambda: widget.set_visible(bool(predicate())))
    return widget


def ups_card(controls, context):
    options = text.ups_thresholds(controls.store.value(UPS_ALERT[2]))
    return shown_when(controls, card("不斷電系統", *alert_rows(controls, UPS_ALERT, options)),
                      {POWER}, context.ups_present)


def status_line(controls, context):
    status = wrapped("", "power-warning")
    status.set_no_show_all(True)

    def sync():
        notice = context.notice()
        status.set_text(notice)
        status.set_visible(bool(notice))
    controls.bind({NOTICE}, sync)
    return status


def sending_card(controls, context):
    button = Gtk.Button(label="傳送測試通知", halign=Gtk.Align.START)
    button.connect("clicked", lambda *_: context.send_test())
    button.set_sensitive(context.notifications_enabled)
    rows = [] if context.laptop else [wrapped(DESKTOP_NOTE)]
    return card("測試通知", *rows, wrapped(TEST_NOTE, "muted"), button,
                status_line(controls, context))


def build(controls, context):
    page = box(spacing=16)
    if context.laptop:
        page.pack_start(battery_card(controls), False, False, 0)
    page.pack_start(ups_card(controls, context), False, False, 0)
    page.pack_start(sending_card(controls, context), False, False, 0)
    return page
