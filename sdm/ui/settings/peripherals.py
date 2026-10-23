"""設定 → 周邊設備：目前連線、回報電量的每台設備各自的低電量通知門檻，以及「全部設為」
（參考專案 PeripheralAlertsCard；與「連線設備」頁每台設備右邊的鈴鐺是同一個設定）。

門檻經由 PeripheralSettings 寫入 peripheral_thresholds（鍵為設備識別碼加 salt 的雜湊）；
設備組成改變時才重建列表，門檻或唯讀狀態改變時只更新下拉選單；寫入被拒絕時恢復原本的選項，並在列表下方寫出原因。
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.core.peripheral_alerts import PREFERENCE_KEY, SALT_KEY, THRESHOLD_CHOICES
from sdm.services.settings_store import STATUS
from sdm.ui.monitor_widgets import card
from sdm.ui.settings.controls import ROW_SPACING, wrapped
from sdm.ui.widgets import box, discard

DEVICES = "_devices"    # 設備組成改變（不是設定）
PROBLEM = "_peripheral_problem"     # 門檻寫入被拒絕或原因清除（不是設定）
KEYS = {PREFERENCE_KEY, SALT_KEY, DEVICES, STATUS, PROBLEM}
NOTE = ("電量降到門檻以下時通知一次，充電到門檻以上 2% 才會再通知；只回報電量等級的設備在等級變成"
        "低電量時通知。設備斷線後重新連上視為新設備，仍低於門檻會再通知一次。"
        "不斷電系統的提醒在「通知」。")
EMPTY = "目前沒有回報電量的周邊設備，連線後會列在這裡。"


def composition(readings):
    """決定是否要重建列表：設備、名稱與圖示（電量改變不重建）。"""
    return tuple((reading.device_id, reading.name, reading.icon) for reading in readings)


def common_threshold(threshold_of, readings):
    """列出的設備門檻都相同時回傳該值（「全部設為」顯示它），不同或沒有設備時回傳 None。"""
    values = {threshold_of(reading.device_id) for reading in readings}
    return values.pop() if len(values) == 1 else None


def problem_line(controls, settings):
    """寫入被拒絕的原因；沒有問題時隱藏。"""
    line = wrapped("", "power-warning")
    line.set_no_show_all(True)

    def sync():
        line.set_text(settings.problem or "")
        line.set_visible(bool(settings.problem))
    controls.bind({PROBLEM}, sync)
    return line


def threshold_combo():
    combo = Gtk.ComboBoxText(valign=Gtk.Align.CENTER)
    for value, title in THRESHOLD_CHOICES:
        combo.append(str(value), title)
    return combo


class PeripheralList:
    def __init__(self, controls, readings, settings):
        self.controls, self.readings, self.settings = controls, readings, settings
        self.widget = box(spacing=10)
        self.rows, self.shown = [], None
        controls.bind(KEYS, self.sync)

    def sync(self):
        found = self.readings()
        if composition(found) != self.shown:
            self.shown = composition(found)
            self.rebuild(found)
        writable = self.controls.writable(PREFERENCE_KEY)
        for device_id, combo, handler in self.rows:
            combo.handler_block(handler)
            combo.set_active_id(str(self.settings.threshold(device_id)))
            combo.handler_unblock(handler)
            combo.set_sensitive(writable)

    def rebuild(self, found):
        discard(self.widget, self.widget.get_children())
        self.rows = []
        if not found:
            self.widget.pack_start(wrapped(EMPTY, "muted"), False, False, 0)
        for reading in found:
            self.widget.pack_start(self.row(reading), False, False, 0)
        self.widget.show_all()

    def row(self, reading):
        row = box(False, ROW_SPACING)
        row.pack_start(Gtk.Image.new_from_icon_name(reading.icon, Gtk.IconSize.LARGE_TOOLBAR),
                       False, False, 0)
        row.pack_start(wrapped(reading.name), True, True, 0)
        combo = threshold_combo()
        handler = combo.connect("changed", self.changed, reading.device_id)
        row.pack_end(combo, False, False, 0)
        self.rows.append((reading.device_id, combo, handler))
        return row

    def changed(self, combo, device_id):
        value = combo.get_active_id()
        if value is not None:
            self.settings.choose((device_id,), int(value))


def set_all_row(controls, readings, settings):
    """「全部設為」：選了之後套用到目前列出的每台設備；門檻都相同時顯示該值，不同時空白。"""
    row = box(False, ROW_SPACING)
    row.pack_start(wrapped("全部設為"), True, True, 0)
    combo = threshold_combo()

    def changed(widget):
        value = widget.get_active_id()
        if value is not None:
            settings.choose([reading.device_id for reading in readings()], int(value))

    def sync():
        shared = common_threshold(settings.threshold, readings())
        combo.handler_block(handler)
        if shared is None:
            combo.set_active(-1)
        else:
            combo.set_active_id(str(shared))
        combo.handler_unblock(handler)
    handler = combo.connect("changed", changed)
    row.pack_end(combo, False, False, 0)
    controls.bind(KEYS, sync)
    controls.enable_when(row, PREFERENCE_KEY, when=lambda: bool(readings()))
    return row


def build(controls, context):
    page = box(spacing=16)
    settings = context.peripheral_settings
    devices = PeripheralList(controls, context.peripherals, settings)
    page.pack_start(card("低電量通知", set_all_row(controls, context.peripherals, settings),
                         Gtk.Separator(), devices.widget, problem_line(controls, settings),
                         wrapped(NOTE, "muted")),
                    False, False, 0)
    return page
