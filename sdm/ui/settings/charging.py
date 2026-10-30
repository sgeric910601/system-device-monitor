"""設定 → 充電（只有筆電）：智慧充電的預設（完成目標、保養上限、睡眠策略）、
「允許無人時自動調整」、精準喚醒元件的狀態與智慧充電紀錄檔。

與電源頁「智慧充電」的編輯視窗用同一份設定與同一組動作（sdm.ui.charging_plan_wiring.PlanActions）；
桌機（沒有可調整的電池）時設定視窗不顯示這個分類。文字在 sdm.ui.charging_plan_text。
"""
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.services import system_components
from sdm.ui import charging_plan_text as text
from sdm.ui.charging_plan_widgets import (hold_row, set_text, show_if, strategy_rows,
                                          target_row, unattended_card)
from sdm.ui.monitor_widgets import card
from sdm.ui.settings.controls import wrapped
from sdm.ui.widgets import box, label

CHARGING = "_charging"              # 智慧充電的狀態改變（不是設定）：更新這一頁
NO_BATTERY = "這台電腦沒有可調整充電上限的電池，不支援智慧充電。"
DEMO_LOG = "示範模式不寫紀錄檔。"
PACKAGE_ROOT = Path("/usr/share/system-device-monitor")
WAKE_READY = "已安裝"
WAKE_MISSING = "沒有安裝或版本不符（精準喚醒會改用準備優先）"
LOG_NOTE = ("每次狀態改變、寫入的上限與讀回的結果、喚醒排程都記在這個檔案（只在這台電腦，"
            "超過 256 KB 時只留後半），用來確認排程照預期運作。")


def home_relative(path):
    """顯示用的位置：家目錄寫成 ~（截圖與說明不出現帳號名稱）。"""
    try:
        return "~/" + str(Path(path).relative_to(Path.home()))
    except ValueError:
        return str(path)


def wake_card(snapshot, root):
    status, hint = wrapped(""), wrapped("", "monospace")
    hint.set_selectable(True)

    def sync(current):
        ready = current.wake is not None and current.wake.component
        set_text(status, "喚醒元件：" + (WAKE_READY if ready else WAKE_MISSING))
        command = "" if ready or current.demo or root is None or Path(root) == PACKAGE_ROOT \
            else system_components.install_command(root)
        set_text(hint, command)
        show_if(hint, bool(command))
    sync(snapshot)
    return card("精準喚醒元件", status, hint,
                wrapped("精準喚醒在該開始充電前一分鐘以 RTC 鬧鐘叫醒電腦。部分電腦關機後也會在"
                        "那個時間開機：看板在關機前會取消鬧鐘（需要已允許無人時自動調整）。",
                        "muted")), sync


def log_card(actions, snapshot):
    path = wrapped("", "monospace")
    path.set_selectable(True)
    button = Gtk.Button(label="開啟紀錄檔", halign=Gtk.Align.START)
    button.connect("clicked", lambda *_: actions.open_log())

    def sync(current):
        location = current.log_path
        set_text(path, DEMO_LOG if location is None else home_relative(location))
        button.set_sensitive(location is not None and Path(location).is_file())
    sync(snapshot)
    return card("智慧充電紀錄", path, wrapped(LOG_NOTE, "muted"), button), sync


def build(controls, context):
    page = box(spacing=16)
    actions = getattr(context, "charging", None)
    if actions is None:
        page.pack_start(card("充電", label(NO_BATTERY, "muted")), False, False, 0)
        return page
    snapshot = actions.snapshot()
    target, sync_target = target_row(controls, snapshot)
    hold, sync_hold = hold_row(controls, snapshot)
    strategy, sync_strategy = strategy_rows(controls)
    defaults = card("智慧充電的預設", wrapped(text.HINT, "muted"), target, hold, strategy,
                    wrapped("套用到之後建立的排程；睡眠策略也套用到目前的排程。"
                            "建立排程：電源頁「智慧充電」的「設定…」。", "muted"))
    unattended, sync_unattended = unattended_card(actions, snapshot)
    wake, sync_wake = wake_card(snapshot, getattr(context, "root", None))
    log, sync_log = log_card(actions, snapshot)
    for widget in (defaults, unattended, wake, log):
        page.pack_start(widget, False, False, 0)
    syncs = (sync_target, sync_hold, sync_strategy, sync_unattended, sync_wake, sync_log)

    def refresh():
        current = actions.snapshot()
        for sync in syncs:
            sync(current)
    controls.bind({CHARGING}, refresh)
    return page
