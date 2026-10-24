"""右鍵選單（桌面寵物與電源頁的夥伴共用）。

- 內容先排成項目清單（pet_entries 等，不需要 GTK 視窗就能測試），再由 build 建成 Gtk.Menu。
- 每個選項都只呼叫 update 寫入設定（由設定的唯一寫入點驗證並即時套用）。
- 選單收起後在閒置時銷毀（dispose_after_use），不會每按一次右鍵就留下一份。
- 「關閉桌面寵物」延到閒置才寫入設定：關閉會銷毀寵物視窗，選單當時還在處理這個選項。
"""
import math
from typing import Callable, NamedTuple

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from sdm.core.companion import APPEARANCES, PHOTO, TITLES
from sdm.core.pet_geometry import size_presets

OPEN = "打開看板"
PLACES = ((False, "浮在視窗上"), (True, "放在桌布上"))
SIZE = "大小"
LOOK = "外觀"
NO_PHOTO = "我的照片（先在設定選一張）"
SETTINGS = "桌面寵物設定…"
CLOSE = "關閉桌面寵物"


class Action(NamedTuple):
    title: str
    callback: Callable


class Separator(NamedTuple):
    pass


class Radios(NamedTuple):
    options: tuple                  # （值、名稱）
    current: Callable               # 傳入一個值，回傳它是不是目前的選擇（打勾）
    choose: Callable                # 選了其他值時呼叫
    enabled: Callable = lambda value: True


class Submenu(NamedTuple):
    title: str
    entries: tuple


def dispose_after_use(menu, idle_add=GLib.idle_add):
    """選單收起後（選到的項目已經執行）在閒置時銷毀。"""
    def destroy():
        menu.destroy()
        return False
    menu.connect("deactivate", lambda *_: idle_add(destroy))
    return menu


def deferred(callback, idle_add=GLib.idle_add):
    """回傳一個函式：呼叫時把 callback 排到閒置時才執行。"""
    def run():
        callback()
        return False
    return lambda *_: idle_add(run)


def look_options(has_photo):
    return tuple((name, NO_PHOTO if name == PHOTO and not has_photo else TITLES[name])
                 for name in APPEARANCES)


def pet_entries(preferences, has_photo, update, open_power, open_settings, defer=deferred):
    sizes = Radios(size_presets(), lambda value: math.isclose(value, preferences.desktop_pet_scale),
                   lambda value: update(desktop_pet_scale=value))
    looks = Radios(look_options(has_photo), lambda value: value == preferences.companion_appearance,
                   lambda value: update(companion_appearance=value),
                   lambda value: has_photo or value != PHOTO)
    return (Action(OPEN, open_power), Separator(),
            Radios(PLACES, lambda value: value == preferences.desktop_pet_on_wallpaper,
                   lambda value: update(desktop_pet_on_wallpaper=value)),
            Submenu(SIZE, (sizes,)), Submenu(LOOK, (looks,)), Separator(),
            Action(SETTINGS, open_settings),
            Action(CLOSE, defer(lambda: update(desktop_pet=False))))


def radio_items(menu, entry):
    """目前的值打勾，選了其他值才呼叫 choose。"""
    group = None
    for value, title in entry.options:
        item = Gtk.RadioMenuItem.new_with_label_from_widget(group, title)
        group = group or item
        item.set_active(entry.current(value))
        item.set_sensitive(entry.enabled(value))
        item.connect("activate", lambda widget, chosen=value: entry.choose(chosen)
                     if widget.get_active() and not entry.current(chosen) else None)
        menu.append(item)


def fill(menu, entries):
    for entry in entries:
        if isinstance(entry, Radios):
            radio_items(menu, entry)
        elif isinstance(entry, Submenu):
            item, submenu = Gtk.MenuItem(label=entry.title), Gtk.Menu()
            fill(submenu, entry.entries)
            item.set_submenu(submenu)
            menu.append(item)
        elif isinstance(entry, Action):
            item = Gtk.MenuItem(label=entry.title)
            item.connect("activate", lambda _item, callback=entry.callback: callback())
            menu.append(item)
        else:
            menu.append(Gtk.SeparatorMenuItem())


def build(entries):
    """把項目清單建成選單；收起後自動銷毀。"""
    menu = Gtk.Menu()
    fill(menu, entries)
    menu.show_all()
    return dispose_after_use(menu)


def pet_menu(preferences, has_photo, update, open_power, open_settings):
    return build(pet_entries(preferences, has_photo, update, open_power, open_settings))
