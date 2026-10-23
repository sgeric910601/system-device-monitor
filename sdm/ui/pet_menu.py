"""桌面寵物的右鍵選單：打開看板、浮在視窗上／放在桌布上、大小、外觀、設定與關閉。

每個選項都只呼叫 update 寫入設定（由設定的唯一寫入點驗證並即時套用）。
"""
import math

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.core.companion import APPEARANCES, PHOTO, TITLES
from sdm.core.pet_geometry import size_presets

OPEN = "打開看板"
PLACES = ((False, "浮在視窗上"), (True, "放在桌布上"))
SIZE = "大小"
LOOK = "外觀"
NO_PHOTO = "我的照片（先在設定選一張）"
SETTINGS = "桌面寵物設定…"
CLOSE = "關閉桌面寵物"


def radio_items(menu, options, current, choose, enabled=lambda value: True):
    """options 為（值、名稱）；目前的值打勾，選了其他值才呼叫 choose。"""
    group = None
    for value, title in options:
        item = Gtk.RadioMenuItem.new_with_label_from_widget(group, title)
        group = group or item
        item.set_active(current(value))
        item.set_sensitive(enabled(value))
        item.connect("activate", lambda widget, chosen=value: choose(chosen)
                     if widget.get_active() and not current(chosen) else None)
        menu.append(item)


def submenu(title, build):
    item, menu = Gtk.MenuItem(label=title), Gtk.Menu()
    build(menu)
    item.set_submenu(menu)
    return item


def action(title, callback):
    item = Gtk.MenuItem(label=title)
    item.connect("activate", lambda *_: callback())
    return item


def pet_menu(preferences, has_photo, update, open_power, open_settings):
    menu = Gtk.Menu()
    menu.append(action(OPEN, open_power))
    menu.append(Gtk.SeparatorMenuItem())
    radio_items(menu, PLACES, lambda value: value == preferences.desktop_pet_on_wallpaper,
                lambda value: update(desktop_pet_on_wallpaper=value))
    menu.append(submenu(SIZE, lambda sizes: radio_items(
        sizes, size_presets(), lambda value: math.isclose(value, preferences.desktop_pet_scale),
        lambda value: update(desktop_pet_scale=value))))
    looks = tuple((name, NO_PHOTO if name == PHOTO and not has_photo else TITLES[name])
                  for name in APPEARANCES)
    menu.append(submenu(LOOK, lambda items: radio_items(
        items, looks, lambda value: value == preferences.companion_appearance,
        lambda value: update(companion_appearance=value),
        enabled=lambda value: has_photo or value != PHOTO)))
    menu.append(Gtk.SeparatorMenuItem())
    menu.append(action(SETTINGS, open_settings))
    menu.append(action(CLOSE, lambda: update(desktop_pet=False)))
    menu.show_all()
    return menu
