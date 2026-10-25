"""設定 → 關於：版本、用途與本機說明文件的位置。"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.ui.monitor_widgets import card
from sdm.ui.settings import text
from sdm.ui.settings.controls import wrapped
from sdm.ui.widgets import box, label

PURPOSE = ("在 Ubuntu 頂端列與視窗中唯讀監看這台電腦的耗電、電池、系統資源與周邊電量。"
           "只讀取本機資料，沒有雲端上傳或遙測；唯一的網路存取是選用的更新檢查（只連到 GitHub）。")
NO_DOCS = "找不到本機的說明文件。"


def doc_buttons(root):
    links = text.doc_links(root)
    if not links:
        return (wrapped(NO_DOCS, "muted"),)
    buttons = []
    for title, path in links:
        button = Gtk.LinkButton.new_with_label(path.as_uri(), title)
        button.set_halign(Gtk.Align.START)
        button.set_tooltip_text(str(path))
        buttons.append(button)
    return tuple(buttons)


def build(_controls, context):
    page = box(spacing=16)
    page.pack_start(card("系統與裝置監看", label(f"版本 {context.version}", "muted"),
                         wrapped(PURPOSE)), False, False, 0)
    page.pack_start(card("說明文件", *doc_buttons(context.root)), False, False, 0)
    return page
