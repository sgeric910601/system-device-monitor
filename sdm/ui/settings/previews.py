"""有預覽圖的單選（夥伴外觀、程式圖示顏色）：按鈕外觀的單選，每個選項上面是預覽、下面是名稱。

與 Controls.radios 相同：只透過 Controls.apply 寫入，設定改變時（包括其他地方改的）自動更新。
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.ui.widgets import box, label

SPACING = 8
OPTION_WIDTH = 92


def preview_radios(controls, key, options, enabled=lambda value: True, keys=()):
    """options 為（值、名稱、建立預覽元件的函式）；enabled 回傳 False 的選項停用。
    keys 為另外要重新檢查停用狀態的鍵（例如照片換了）。"""
    flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=SPACING,
                       row_spacing=SPACING, homogeneous=True, max_children_per_line=len(options))
    group, buttons = None, []
    for value, title, make_preview in options:
        button = Gtk.RadioButton.new_from_widget(group)
        group = group or button
        button.set_mode(False)
        content = box(spacing=4)
        content.set_margin_top(6)
        content.set_margin_bottom(6)
        content.pack_start(make_preview(), False, False, 0)
        caption = label(title)
        caption.set_xalign(0.5)
        caption.set_line_wrap(True)
        content.pack_start(caption, False, False, 0)
        button.add(content)
        button.set_size_request(OPTION_WIDTH, -1)
        handler = button.connect("toggled", lambda widget, chosen=value: controls.apply(
            **{key: chosen}) if widget.get_active() else None)
        buttons.append((button, handler, value))
        flow.add(button)
        controls.enable_when(button, key, when=lambda value=value: enabled(value))
    for child in flow.get_children():
        child.set_can_focus(False)

    def sync():
        for button, handler, value in buttons:
            if value == controls.store.value(key):
                controls.set_active(button, handler, True)
    controls.bind({key, *keys}, sync)
    return flow
