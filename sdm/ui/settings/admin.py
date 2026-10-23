"""設定 → 管理員功能：管理員元件的安裝狀態，以及選用的 CPU 功耗取樣。

- 管理員元件：已安裝（.deb）／已安裝（原始碼）／未安裝；原始碼資料夾還沒安裝時顯示
  可以直接複製到終端機的安裝指令（按「複製」才寫入剪貼簿）。
- CPU 功耗取樣：開關（元件可用而且沒有在等待授權時才能切換）、目前狀態、用途、
  旁路攻擊的說明與上次的錯誤。開關只寫入設定，實際的授權流程在 sdm.ui.cpu_power_wiring。
文字在 sdm.ui.admin_text（不載入 GTK）。
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk, Pango

from sdm.ui import admin_text as text
from sdm.ui.monitor_widgets import card
from sdm.ui.settings.controls import wrapped
from sdm.ui.widgets import box, label

ADMIN = "_admin"                    # 管理員元件狀態改變（不是設定）：更新這一頁
PREFERENCE = "admin_cpu_sampling"
UNAVAILABLE = "無法取得管理員元件的狀態。"


def hidden_unless(widget, visible):
    """內容已經 show 過、外層 show_all 不會再顯示它；依 visible 切換。"""
    widget.set_no_show_all(True)
    widget.set_visible(visible)


def copy_text(value):
    Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(value, -1)


def command_row(controls, admin):
    """完整顯示（會換行、可選取）的指令與「複製」按鈕。"""
    command = wrapped("", "monospace")
    command.set_selectable(True)
    command.set_line_wrap_mode(Pango.WrapMode.CHAR)
    attributes = Pango.AttrList()
    attributes.insert(Pango.attr_insert_hyphens_new(False))     # 斷行處不加連字號，避免誤讀
    command.set_attributes(attributes)
    command.set_tooltip_text("複製後貼到終端機執行")
    button = Gtk.Button(label="複製", valign=Gtk.Align.CENTER)
    button.connect("clicked", lambda *_: copy_text(command.get_text()))
    row = box(False, 12)
    row.pack_start(command, True, True, 0)
    row.pack_start(button, False, False, 0)
    row.show_all()

    def sync():
        value = text.install_command(admin.state, admin.root) or ""
        if command.get_text() != value:
            command.set_text(value)
        hidden_unless(row, bool(value))
    controls.bind({ADMIN}, sync)
    return row


def components_card(controls, admin):
    title = controls.note(lambda: text.component_title(admin.state), {ADMIN}, css="section")
    note = controls.note(lambda: text.component_note(admin.state, admin.root), {ADMIN})
    return card("管理員元件", title, note, command_row(controls, admin))


def error_label(controls, admin):
    widget = wrapped("", "power-warning")

    def sync():
        line = text.error_line(admin.state)
        widget.set_text(line)
        hidden_unless(widget, bool(line))
    controls.bind({ADMIN}, sync)
    return widget


def sampling_card(controls, admin):
    store = controls.store
    switch = controls.switch(PREFERENCE, "CPU 功耗取樣",
                             when=lambda: admin.state.ready and not admin.state.busy)
    status = controls.note(lambda: text.sampling_status(admin.state, store.value(PREFERENCE)),
                           {ADMIN, PREFERENCE})
    return card("CPU 功耗取樣", switch, status, wrapped(text.EXPLANATION, "muted"),
                wrapped(text.SIDE_CHANNEL, "muted"), error_label(controls, admin))


def build(controls, context):
    page = box(spacing=16)
    admin = getattr(context, "admin", None)
    if admin is None:
        page.pack_start(card("管理員功能", label(UNAVAILABLE, "muted")), False, False, 0)
        return page
    page.pack_start(components_card(controls, admin), False, False, 0)
    page.pack_start(sampling_card(controls, admin), False, False, 0)
    return page
