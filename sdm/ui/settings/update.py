"""設定 → 更新：目前版本與安裝方式、自動檢查與通知的開關、「立即檢查」、檢查結果、
更新內容摘要、「安裝更新」（.deb）或原始碼資料夾的 git 指令，以及上次檢查的時間。

開關只寫入設定（更新服務每次排程時讀取）；按鈕呼叫更新服務（sdm.services.updater 或示範模式的
sdm.services.update_demo）。文字在 sdm.ui.update_text（不載入 GTK）。
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk, Pango

from sdm.services.updater import KIND_DEB
from sdm.ui import update_text as text
from sdm.ui.monitor_widgets import card
from sdm.ui.settings.admin import ADMIN     # 管理員元件的狀態（原始碼資料夾的重新安裝提示）
from sdm.ui.settings.controls import wrapped
from sdm.ui.widgets import box, label

UPDATE = "_update"                  # 更新服務的狀態改變（不是設定）：更新這一頁
AUTO, NOTIFY = "auto_check_updates", "update_notification"


def hidden_unless(widget, visible):
    widget.set_no_show_all(True)
    widget.set_visible(visible)


def command_row(controls, compute, keys):
    """完整顯示（會換行、可選取）的指令與「複製」按鈕。"""
    command = wrapped("", "monospace")
    command.set_selectable(True)
    command.set_line_wrap_mode(Pango.WrapMode.CHAR)
    attributes = Pango.AttrList()
    attributes.insert(Pango.attr_insert_hyphens_new(False))     # 斷行處不加連字號，避免誤讀
    command.set_attributes(attributes)
    command.set_tooltip_text("複製後貼到終端機執行")
    button = Gtk.Button(label="複製", valign=Gtk.Align.CENTER)
    button.connect("clicked", lambda *_: Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(
        command.get_text(), -1))
    row = box(False, 12)
    row.pack_start(command, True, True, 0)
    row.pack_start(button, False, False, 0)
    controls.bind(keys, lambda: command.set_text(compute()))
    return row


def shown_label(controls, compute, keys, css=None):
    """只在 compute() 有文字時顯示的標籤。"""
    widget = wrapped("", css)

    def sync():
        value = compute()
        widget.set_text(value)
        hidden_unless(widget, bool(value))
    controls.bind(keys, sync)
    return widget


def reminder_box(controls, updater, context):
    """原始碼資料夾的管理員元件版本不符時，提示重新安裝並附上指令。"""
    admin = getattr(context, "admin", None)
    container = box(spacing=8)
    container.pack_start(wrapped(text.HELPER_MISMATCH, "power-warning"), False, False, 0)
    container.pack_start(command_row(controls, lambda: text.reinstall_command(updater.root),
                                     {UPDATE}), False, False, 0)
    container.show_all()
    controls.bind({UPDATE, ADMIN}, lambda: hidden_unless(container, text.helper_mismatch(
        updater.kind, getattr(admin, "state", None))))
    return container


def version_card(controls, updater, context):
    keys = shown_label(controls, lambda: text.keys_text(updater.kind, updater.keys_configured),
                       {UPDATE}, "power-warning")
    return card("版本", wrapped(text.version_line(updater.current, updater.kind)), keys,
                reminder_box(controls, updater, context))


def automatic_card(controls):
    store = controls.store
    return card("自動檢查", controls.switch(AUTO, text.AUTO_TITLE),
                controls.switch(NOTIFY, text.NOTIFY_TITLE, when=lambda: bool(store.value(AUTO))),
                wrapped(text.AUTO_NOTE, "muted"))


def check_row(controls, updater):
    button = Gtk.Button(label=text.CHECK, valign=Gtk.Align.CENTER)
    button.connect("clicked", lambda *_: updater.check_now())
    status = wrapped("", "section")
    row = box(False, 12)
    row.pack_start(status, True, True, 0)
    row.pack_end(button, False, False, 0)

    def sync():
        status.set_text(text.status_text(updater.state))
        button.set_sensitive(not updater.busy)
    controls.bind({UPDATE}, sync)
    return row


def install_rows(controls, updater):
    button = Gtk.Button(label=text.INSTALL, halign=Gtk.Align.START)
    button.get_style_context().add_class("suggested-action")
    button.connect("clicked", lambda *_: updater.install())
    controls.bind({UPDATE}, lambda: button.set_sensitive(updater.installable))
    return (button, wrapped(text.INSTALL_NOTE, "muted"))


def source_rows(controls, updater):
    return (wrapped(text.SOURCE_NOTE),
            command_row(controls, lambda: text.git_command(updater.root), {UPDATE}),
            wrapped(text.HELPER_NOTE, "muted"),
            command_row(controls, lambda: text.reinstall_command(updater.root), {UPDATE}))


def release_box(controls, updater):
    """新版本的內容與安裝方式；只在列出新版本時顯示。"""
    notes = wrapped("")
    beta = wrapped(text.BETA_NOTE, "power-warning")
    error = shown_label(controls, lambda: text.error_text(updater.state), {UPDATE},
                        "power-warning")
    link = Gtk.LinkButton.new_with_label("https://github.com/", text.PAGE_LINK)
    link.set_halign(Gtk.Align.START)
    rows = install_rows(controls, updater) if updater.kind == KIND_DEB else \
        source_rows(controls, updater)
    container = box(spacing=10)
    for child in (label(text.NOTES_TITLE, "section"), notes, beta, *rows, error, link):
        container.pack_start(child, False, False, 0)
    container.show_all()

    def sync():
        state = updater.state
        visible = text.show_release(state)
        hidden_unless(container, visible)
        if visible:
            notes.set_text(text.notes_text(state))
            hidden_unless(beta, state.release.is_beta)
            link.set_uri(state.release.page_url)
    controls.bind({UPDATE}, sync)
    return container


def check_card(controls, updater):
    return card("檢查更新", check_row(controls, updater),
                controls.note(lambda: text.last_check_text(updater.state.last_check), {UPDATE}),
                wrapped(text.CHECK_NOTE, "muted"), release_box(controls, updater))


def build(controls, context):
    page = box(spacing=16)
    updater = getattr(context, "updater", None)
    if updater is None:
        page.pack_start(card("更新", wrapped(text.DISABLED, "muted")), False, False, 0)
        return page
    for widget in (version_card(controls, updater, context), automatic_card(controls),
                   check_card(controls, updater)):
        page.pack_start(widget, False, False, 0)
    return page
