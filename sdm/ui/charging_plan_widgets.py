"""智慧充電的共用元件：編輯視窗與設定的「充電」都用到的目標、保養上限、睡眠策略與
「允許無人時自動調整」。設定一律經由 Controls 寫入（驗證 → 儲存 → 通知）；
每個元件都回傳（元件、sync(snapshot)），由擁有者在狀態改變時呼叫 sync。文字在 charging_plan_text。
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.constants import SmartCharging
from sdm.core.charging_plan import PRECISE_WAKE, READINESS_FIRST
from sdm.ui import charging_plan_text as text
from sdm.ui.monitor_widgets import card
from sdm.ui.settings.controls import wrapped
from sdm.ui.widgets import box

TARGET, HOLD, STRATEGY = ("charging_target_percent", "charging_hold_percent",
                          "charging_sleep_strategy")
TONE_CLASSES = {text.TONE_MUTED: "muted", text.TONE_WARNING: "power-warning",
                text.TONE_GOOD: "tone-good"}
ROW_SPACING = 12


def set_text(widget, value):
    if widget.get_text() != value:
        widget.set_text(value)


def show_if(widget, visible):
    widget.set_no_show_all(True)
    widget.set_visible(visible)


def set_tone(widget, tone):
    context = widget.get_style_context()
    for name in TONE_CLASSES.values():
        context.remove_class(name)
    context.add_class(TONE_CLASSES[tone])


def percent_options(snapshot):
    options = snapshot.charge.options if snapshot.charge is not None else ()
    return tuple(value for value in options if value >= SmartCharging.MIN_PERCENT)


def target_row(controls, snapshot):
    """完成目標：「原本的上限（N%）」或滑桿的選項；原本的上限或選項改變時重建清單。"""
    row = box(False, ROW_SPACING)
    row.pack_start(wrapped("完成目標"), True, True, 0)
    combo = Gtk.ComboBoxText(valign=Gtk.Align.CENTER)
    row.pack_end(combo, False, False, 0)
    state = {"key": None, "snapshot": snapshot}

    def chosen(widget):
        if widget.get_active_id() is not None:
            controls.apply(**{TARGET: int(widget.get_active_id())})
    handler = combo.connect("changed", chosen)

    def refresh():
        current = state["snapshot"]
        key = (current.original_limit, percent_options(current))
        combo.handler_block(handler)
        if key != state["key"]:
            state["key"] = key
            combo.remove_all()
            combo.append("0", text.ORIGINAL_LIMIT.format(current.original_limit))
            for value in key[1]:
                combo.append(str(value), f"{value}%")
        combo.set_active_id(str(controls.store.value(TARGET)))
        combo.handler_unblock(handler)

    def sync(current):
        state["snapshot"] = current
        refresh()
    controls.bind({TARGET}, refresh)
    controls.enable_when(row, TARGET)
    return row, sync


def hold_row(controls, snapshot):
    options = [(value, f"{value}%") for value in percent_options(snapshot)]
    row = controls.choice(HOLD, "保養上限（等待時維持；不會高於完成目標）", options)
    return row, lambda current: None


def strategy_rows(controls):
    """睡眠策略：準備優先／精準喚醒，下方是說明與喚醒的狀態。"""
    container = box(spacing=6)
    container.pack_start(wrapped("睡眠時"), False, False, 0)
    container.pack_start(controls.radios(STRATEGY, [
        (key, text.STRATEGY_TITLES[key]) for key in (READINESS_FIRST, PRECISE_WAKE)]),
        False, False, 0)
    note = controls.note(lambda: text.STRATEGY_NOTES[controls.store.value(STRATEGY)],
                         {STRATEGY})
    wake = wrapped("", "muted")
    container.pack_start(note, False, False, 0)
    container.pack_start(wake, False, False, 0)

    def sync(current):
        line = text.wake_note(current.wake, controls.store.value(STRATEGY), current.now)
        set_text(wake, line)
        show_if(wake, bool(line))
    return container, sync


def unattended_card(actions, snapshot):
    """「允許無人時自動調整」：開關、目前的狀態、授權範圍的說明與上次的錯誤。"""
    switch = Gtk.Switch(valign=Gtk.Align.CENTER)
    row = box(False, ROW_SPACING)
    title = wrapped("允許無人時自動調整（只限充電上限與定時喚醒）")
    row.pack_start(title, True, True, 0)
    row.pack_end(switch, False, False, 0)
    status, scope, error = wrapped(""), wrapped("", "muted"), wrapped("", "power-warning")

    def toggled(widget, _spec):
        widget.set_sensitive(False)
        actions.set_unattended(widget.get_active(), lambda ok: sync(actions.snapshot()))
    handler = switch.connect("notify::active", toggled)

    def sync(current):
        state = current.unattended
        switch.handler_block(handler)
        switch.set_active(state.granted is True)
        switch.handler_unblock(handler)
        switch.set_sensitive(state.ready and not state.busy)
        set_text(status, text.unattended_status(state))
        set_text(scope, text.unattended_scope(current.user))
        set_text(error, f"上次的錯誤：{state.error}" if state.error else "")
        show_if(error, bool(state.error))
    sync(snapshot)
    return card("無人時自動調整", row, status, scope, error), sync
