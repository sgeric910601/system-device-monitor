"""「連線設備」頁：UPS、無線周邊、藍牙與 USB 設備的電量與狀態。

設備組成改變才重建列表，否則只更新「幾分鐘前回報」的時間。
"""
import json

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.constants import Ui
from sdm.core.fmt import age
from sdm.ui.widgets import box, clear, label


def accent_for(device):
    if device["kind"] == "UPS":
        return "ups"
    name = device["name"]
    if "K73R" in name:
        return "cpu"
    if "Mouse" in name:
        return "ram"
    if "Razer" in name:
        return "gpu"
    return "network" if device["kind"] == "藍牙" else "temperature"


def icon_for(device):
    name = device["name"]
    if device["kind"] == "UPS":
        return "battery-symbolic"
    if device["kind"] == "藍牙":
        return "bluetooth-symbolic"
    if "鍵盤" in name:
        return "input-keyboard-symbolic"
    if "Mouse" in name:
        return "input-mouse-symbolic"
    return "audio-headphones-symbolic" if "Razer" in name else "drive-removable-media-symbolic"


class DevicesPage(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.heading = label("目前連線設備", "section")
        self.pack_start(self.heading, False, False, 0)
        self.pack_start(label("電量依設備回報更新；接收器存在與無線端連線狀態分別顯示。",
                              "muted", True), False, False, 0)
        self.notice = label("", "muted", True)
        self.pack_start(self.notice, False, False, 0)
        self.rows = box(spacing=0)
        self.pack_start(self.rows, False, False, 0)
        self.fingerprint, self.age_labels = None, []

    def render(self, data):
        devices = data.get("devices", [])
        fingerprint = json.dumps(devices, sort_keys=True, ensure_ascii=False)
        self.heading.set_text(f"目前連線設備 · {len(devices)}")
        self.notice.set_text(" · ".join(data.get("errors", [])))
        if fingerprint != self.fingerprint:
            self.fingerprint = fingerprint
            self.rebuild(devices)
        for item, stamp, kind in self.age_labels:
            item.set_text(age(stamp) if stamp else kind)

    def rebuild(self, devices):
        clear(self.rows)
        self.age_labels = []
        if not devices:
            self.rows.pack_start(label("目前沒有可顯示的連線設備。", "muted"), False, False, 0)
        for device in devices:
            self.rows.pack_start(self.row(device), False, False, 0)
        self.rows.show_all()

    def row(self, device):
        row = box(False, 16)
        for css in ("device-row", "accent-" + accent_for(device)):
            row.get_style_context().add_class(css)
        row.pack_start(Gtk.Image.new_from_icon_name(icon_for(device), Gtk.IconSize.DND),
                       False, False, 0)
        description = box(spacing=4)
        description.pack_start(label(device["name"], "device-name", True), False, False, 0)
        description.pack_start(label(device["status"], "muted", True), False, False, 0)
        description.pack_start(label(device["detail"], "muted", True), False, False, 0)
        row.pack_start(description, True, True, 0)
        power = box(spacing=4)
        power.set_size_request(Ui.DEVICE_POWER_WIDTH, -1)
        power.pack_start(label(device["battery"], "device-power", True), False, False, 0)
        stamp_label = label(age(device["stamp"]) if device["stamp"] else device["kind"], "muted")
        power.pack_start(stamp_label, False, False, 0)
        if device["percent"] is not None:
            progress = Gtk.ProgressBar()
            progress.set_fraction(device["percent"] / 100)
            power.pack_start(progress, False, False, 0)
        self.age_labels.append((stamp_label, device["stamp"], device["kind"]))
        row.pack_end(power, False, False, 0)
        return row
