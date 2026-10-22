"""首次啟動：選擇監控範圍（參考專案 askForMode）。

- 只在舊版 settings.json 啟動前不存在時（Settings.first_run）詢問；模擬測試與示範模式不問。
- 兩張卡片：「只看電源」（桌機為「只看耗電」）與「電源＋系統監控」；
  直接關閉視窗等於選預設的「電源＋系統監控」。
- 選擇經由 SettingsStore 寫入 system_monitors，之後可在「設定 → 一般」更改。
"""
import logging

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.core.settings import PreferencesReadOnlyError
from sdm.ui.main_window import install_css
from sdm.ui.settings.controls import wrapped
from sdm.ui.settings.text import power_word
from sdm.ui.widgets import box, label

LOG = logging.getLogger(__name__)
POWER_ONLY, WITH_MONITORS = "power_only", "with_monitors"
WIDTH = 520
DEFAULT_MARK = "（預設，直接關閉視窗也是這個）"


def should_ask(first_run, smoke, preview):
    return bool(first_run) and not smoke and not preview


def system_monitors_for(response):
    """只有明確選「只看電源」才關閉系統監控。"""
    return response != POWER_ONLY


def choices(laptop):
    word = power_word(laptop)
    only = ("頂端列只顯示電量" if laptop else "頂端列只顯示整機耗電") + \
        "；CPU／GPU、記憶體、磁碟與網路四個模組都不取樣，最省電。"
    full = "另外在頂端列顯示 CPU、溫度、記憶體、GPU 與網速，並開啟四個系統監控頁面。"
    return ((POWER_ONLY, f"只看{word}", only), (WITH_MONITORS, f"{word}＋系統監控", full))


def apply_choice(store, response):
    try:
        store.update(system_monitors=system_monitors_for(response))
    except PreferencesReadOnlyError:
        LOG.warning("Preferences are read-only; keeping the monitoring scope")


class FirstRunWindow:
    def __init__(self, application, store, laptop):
        install_css()
        self.store, self.answered = store, False
        self.window = Gtk.ApplicationWindow(application=application)
        self.window.set_title("系統與裝置監看")
        self.window.set_default_size(WIDTH, -1)
        self.window.connect("delete-event", self.on_delete)
        content = box(spacing=12)
        content.get_style_context().add_class("page")
        content.pack_start(label("要在頂端列顯示哪些資訊？", "section"), False, False, 0)
        content.pack_start(wrapped("之後可以在「設定 → 一般 → 監控範圍」更改。", "muted"),
                           False, False, 0)
        for response, title, detail in choices(laptop):
            if response == WITH_MONITORS:
                title += DEFAULT_MARK
            content.pack_start(self.card(response, title, detail), False, False, 0)
        self.window.add(content)

    def card(self, response, title, detail):
        inner = box(spacing=4)
        inner.pack_start(label(title, "device-name"), False, False, 0)
        inner.pack_start(wrapped(detail, "muted"), False, False, 0)
        button = Gtk.Button()
        button.add(inner)
        button.connect("clicked", lambda *_: self.answer(response))
        return button

    def present(self):
        self.window.show_all()
        self.window.present()

    def answer(self, response):
        if not self.answered:
            self.answered = True
            apply_choice(self.store, response)
        self.window.destroy()

    def on_delete(self, *_):
        if not self.answered:
            self.answered = True
            apply_choice(self.store, None)
        return False
