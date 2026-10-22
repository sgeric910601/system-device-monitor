"""設定 → 監控模組：系統監控總開關與四個模組（CPU／GPU、記憶體、磁碟、網路）。

關閉的模組立即停止取樣，並隱藏它的頁面、總覽卡片與頂端列項目。
"""
from sdm.ui.monitor_widgets import card
from sdm.ui.settings import text
from sdm.ui.widgets import box

MODULES = (("monitor_cpu", "CPU／GPU"), ("monitor_memory", "記憶體"),
           ("monitor_disk", "磁碟"), ("monitor_network", "網路"))


def build(controls, context):
    store, page = controls.store, box(spacing=16)
    switches = [controls.switch(key, title, when=lambda: store.preferences.system_monitors)
                for key, title in MODULES]
    page.pack_start(card(
        "系統監控", controls.switch("system_monitors", "系統監控（CPU／GPU、記憶體、磁碟、網路）"),
        *switches,
        controls.note(lambda: text.monitors_note(store.preferences, context.laptop),
                      {"system_monitors"})), False, False, 0)
    return page
