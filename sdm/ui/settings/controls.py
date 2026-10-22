"""設定視窗的控制項：每個控制項都只透過 SettingsStore.update() 寫入
（驗證 → 儲存 → 通知 → 即時套用），設定改變時（包括首次啟動的選擇等其他來源）自動更新顯示。

preferences.json 唯讀時新設定的控制項停用（舊版頂端列項目與登入自啟仍可變更），
視窗上方的說明列寫出原因。
"""
import logging

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango

from sdm.core.display_items import toggled
from sdm.core.settings import LEGACY_KEYS, PreferencesReadOnlyError
from sdm.services.settings_store import AUTOSTART, STATUS
from sdm.ui.widgets import box, label

LOG = logging.getLogger(__name__)
ROW_SPACING = 12


def wrapped(text, css=None):
    widget = label(text, css, True)
    widget.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    widget.set_hexpand(True)
    return widget


class Controls:
    def __init__(self, store):
        self.store, self.bindings, self.rules = store, [], []
        store.subscribe(self.changed)

    def changed(self, keys, _store=None):
        for binding_keys, sync in self.bindings:
            if STATUS in keys or keys & binding_keys:
                sync()
        for widget, predicate in self.rules:
            widget.set_sensitive(predicate())

    def bind(self, keys, sync):
        self.bindings.append((frozenset(keys), sync))
        sync()

    def writable(self, key):
        return key in LEGACY_KEYS or key == AUTOSTART or not self.store.read_only

    def enable_when(self, widget, key, when=None):
        def predicate():
            return self.writable(key) and (when is None or when())
        self.rules.append((widget, predicate))
        widget.set_sensitive(predicate())

    def apply(self, **changes):
        """寫入設定；被拒絕時恢復控制項原本顯示的值。"""
        try:
            self.store.update(**changes)
        except PreferencesReadOnlyError:
            self.changed(frozenset(changes))
        except (KeyError, ValueError) as error:
            LOG.warning("Rejected setting %s: %s", ",".join(changes), type(error).__name__)
            self.changed(frozenset(changes))

    def note(self, compute, keys, css="muted"):
        widget = wrapped("", css)
        self.bind(keys, lambda: widget.set_text(compute()))
        return widget

    def switch(self, key, title, when=None):
        row = box(False, ROW_SPACING)
        row.pack_start(wrapped(title), True, True, 0)
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.pack_end(switch, False, False, 0)
        handler = switch.connect("notify::active",
                                 lambda widget, _: self.apply(**{key: widget.get_active()}))
        self.bind({key}, lambda: self.set_active(switch, handler, bool(self.store.value(key))))
        self.enable_when(row, key, when)
        return row

    def check(self, key, title, when=None):
        check = Gtk.CheckButton(label=title)
        check.get_child().set_line_wrap(True)
        handler = check.connect("toggled", lambda widget: self.apply(**{key: widget.get_active()}))
        self.bind({key}, lambda: self.set_active(check, handler, bool(self.store.value(key))))
        self.enable_when(check, key, when)
        return check

    def hidden_check(self, key, item_id, title, when=None):
        """「顯示這個項目」勾選框：取消勾選＝把 item_id 加入 key（隱藏清單）。"""
        check = Gtk.CheckButton(label=title)
        def on_toggled(widget):
            self.apply(**{key: toggled(self.store.value(key), item_id, widget.get_active())})
        handler = check.connect("toggled", on_toggled)
        self.bind({key}, lambda: self.set_active(check, handler,
                                                 item_id not in self.store.value(key)))
        self.enable_when(check, key, when)
        return check

    def choice(self, key, title, options):
        """下拉選單；options 為（值、名稱）。"""
        row = box(False, ROW_SPACING)
        row.pack_start(wrapped(title), True, True, 0)
        combo = Gtk.ComboBoxText(valign=Gtk.Align.CENTER)
        values = {str(value): value for value, _ in options}
        for value, name in options:
            combo.append(str(value), name)
        row.pack_end(combo, False, False, 0)
        handler = combo.connect("changed", lambda widget: self.apply(
            **{key: values[widget.get_active_id()]}) if widget.get_active_id() else None)
        def sync():
            combo.handler_block(handler)
            combo.set_active_id(str(self.store.value(key)))
            combo.handler_unblock(handler)
        self.bind({key}, sync)
        self.enable_when(row, key)
        return row

    def radios(self, key, options):
        """單選；options 為（值、名稱）。"""
        container, group, buttons = box(spacing=6), None, []
        for value, name in options:
            button = Gtk.RadioButton.new_with_label_from_widget(group, name)
            button.get_child().set_line_wrap(True)
            group = group or button
            handler = button.connect("toggled", lambda widget, chosen=value: self.apply(
                **{key: chosen}) if widget.get_active() else None)
            buttons.append((button, handler, value))
            container.pack_start(button, False, False, 0)
        def sync():
            for button, handler, value in buttons:
                if value == self.store.value(key):
                    self.set_active(button, handler, True)
        self.bind({key}, sync)
        self.enable_when(container, key)
        return container

    @staticmethod
    def set_active(widget, handler, active):
        if widget.get_active() != active:
            widget.handler_block(handler)
            widget.set_active(active)
            widget.handler_unblock(handler)
