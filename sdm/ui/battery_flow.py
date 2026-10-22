"""筆電電力流向：變壓器 → 系統 ⇄ 電池（參考專案的省電模式靜態流向圖）。

只用 GTK 元件與圖示主題（沒有自行繪圖、沒有動畫）：充電時箭頭指向電池，
用電池或電池一起供電時箭頭指向系統；沒有作用的節點與箭頭以淡色顯示。
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.ui.widgets import label

ICON_SIZE = 32
ARROW_SIZE = 22
NODE_WIDTH = 104
ADAPTER_ICON = "ac-adapter-symbolic"
SYSTEM_ICON = "computer-symbolic"
ARROW_RIGHT = "go-next-symbolic"
ARROW_LEFT = "go-previous-symbolic"
DIM = "dim-label"


def set_dim(widget, dim):
    context = widget.get_style_context()
    if dim:
        context.add_class(DIM)
    else:
        context.remove_class(DIM)


class FlowNode(Gtk.Box):
    def __init__(self, icon_name, title):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.set_size_request(NODE_WIDTH, -1)
        self.image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.DND)
        self.image.set_pixel_size(ICON_SIZE)
        self.title = label(title, "muted")
        self.value = label("—", "flow-value")
        for widget in (self.title, self.value):
            widget.set_xalign(0.5)
            widget.set_justify(Gtk.Justification.CENTER)
        for widget in (self.image, self.title, self.value):
            self.pack_start(widget, False, False, 0)

    def update(self, title, value, active, icon_name=None):
        if self.title.get_text() != title:
            self.title.set_text(title)
        if self.value.get_text() != value:
            self.value.set_text(value)
        if icon_name is not None and self.image.get_icon_name()[0] != icon_name:
            self.image.set_from_icon_name(icon_name, Gtk.IconSize.DND)
            self.image.set_pixel_size(ICON_SIZE)
        set_dim(self, not active)


class BatteryFlow(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.set_homogeneous(False)
        self.model = None
        self.adapter = FlowNode(ADAPTER_ICON, "變壓器")
        self.system = FlowNode(SYSTEM_ICON, "系統耗電")
        self.battery = FlowNode("battery-missing-symbolic", "電池")
        self.adapter_arrow = self.arrow()
        self.battery_arrow = self.arrow()
        for widget in (self.adapter, self.adapter_arrow, self.system, self.battery_arrow,
                       self.battery):
            self.pack_start(widget, True, True, 0)

    @staticmethod
    def arrow():
        image = Gtk.Image.new_from_icon_name(ARROW_RIGHT, Gtk.IconSize.BUTTON)
        image.set_pixel_size(ARROW_SIZE)
        return image

    def set_model(self, model):
        """model 為 battery_text.BatteryFlowModel；內容不變時不更新。"""
        if model == self.model:
            return
        self.model = model
        self.adapter.update("變壓器", model.adapter_text, model.adapter_active)
        self.system.update("系統耗電", model.system_text, True)
        self.battery.update(model.battery_title, model.battery_text, model.battery_active,
                            model.battery_icon)
        set_dim(self.adapter_arrow, not model.adapter_active)
        set_dim(self.battery_arrow, not model.battery_active)
        direction = ARROW_RIGHT if model.battery_charging else ARROW_LEFT
        self.battery_arrow.set_from_icon_name(direction, Gtk.IconSize.BUTTON)
        self.battery_arrow.set_pixel_size(ARROW_SIZE)
