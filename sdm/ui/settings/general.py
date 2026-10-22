"""設定 → 一般：登入自動啟動、監控範圍、更新頻率與省電模式。"""
from sdm.core.schedule import cadence, saving_active
from sdm.services.settings_store import AUTOSTART
from sdm.ui.monitor_widgets import card
from sdm.ui.settings import text
from sdm.ui.settings.controls import wrapped
from sdm.ui.widgets import box

EXTERNAL = "_external"      # 供電狀態改變（不是設定）：更新省電說明
KEEP_RUNNING = "關閉視窗後仍會在頂端列運作。需要停止時，使用頂端列選單中的「退出」。"
BATTERY_ONLY = "只在使用電池時（筆電電池或不斷電系統改用電池）啟用"
SAVING_KEYS = {"power_saving", "power_saving_on_battery_only", "refresh_interval", EXTERNAL}


def current_cadence(store, context):
    prefs = store.preferences
    saving = saving_active(prefs.power_saving, prefs.power_saving_on_battery_only,
                           context.external_power())
    return cadence(prefs.refresh_interval, saving)


def build(controls, context):
    store, page = controls.store, box(spacing=16)
    page.pack_start(card("啟動", controls.switch(AUTOSTART, "登入 Ubuntu 桌面後自動啟動"),
                         wrapped(KEEP_RUNNING, "muted")), False, False, 0)
    page.pack_start(card(
        "監控範圍", controls.radios("system_monitors", text.scope_choices(context.laptop)),
        controls.note(lambda: text.monitors_note(store.preferences, context.laptop),
                      {"system_monitors"})), False, False, 0)
    page.pack_start(card(
        "更新頻率與省電", controls.choice("refresh_interval", "更新頻率", text.INTERVALS),
        controls.switch("power_saving", "省電模式"),
        controls.switch("power_saving_on_battery_only", BATTERY_ONLY,
                        when=lambda: store.preferences.power_saving),
        controls.note(lambda: text.power_saving_note(store.preferences,
                                                     context.external_power()), SAVING_KEYS),
        controls.note(lambda: text.cadence_note(current_cadence(store, context)), SAVING_KEYS)),
        False, False, 0)
    return page
