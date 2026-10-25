"""電源頁電量標題下方的「充電上限」：滑桿（每一格是一個可選的上限，預設 80–100% 每 5%，
100 為不限制）、目前的值、狀態列、「開機與喚醒後保留」開關、與其他程式衝突的提醒，
以及缺少管理員元件時到「設定 → 管理員功能」的連結。

只負責顯示與把操作交給 ChargeControl（或示範模式的 DemoChargeControl）；按住滑桿時不寫入，
放開或停 0.6 秒才由 ChargeControl 寫入。程式設定數值時暫停訊號，不會被當成使用者的調整。
文字都在 sdm.ui.charge_limit_text。
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.core.charge_limit import index_of, limit_at
from sdm.ui import charge_limit_text as text
from sdm.ui.widgets import box, label

TONE_CLASSES = {text.TONE_MUTED: "muted", text.TONE_WARNING: "power-warning"}
SETTINGS_SECTION = "admin"
SLIDER_MIN_WIDTH = 220


def set_text(widget, value):
    if widget.get_text() != value:
        widget.set_text(value)


def show_if(widget, visible):
    widget.set_no_show_all(True)
    widget.set_visible(visible)


class ChargeLimitView(Gtk.Box):
    def __init__(self, control, open_settings):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.control, self.open_settings = control, open_settings
        self.options, self.syncing = None, False
        self.pack_start(self.build_slider_row(), False, False, 0)
        self.status = label("", "muted", True)
        self.pack_start(self.status, False, False, 0)
        self.pack_start(self.build_persist_row(), False, False, 0)
        self.persist_warning = label("", "power-warning", True)
        self.pack_start(self.persist_warning, False, False, 0)
        self.conflicts = label("", "power-warning", True)
        self.link = Gtk.LinkButton.new_with_label("", text.SETTINGS_LINK)
        self.link.set_halign(Gtk.Align.START)
        self.link.set_has_tooltip(False)
        self.link.connect("activate-link", self.on_link)
        for widget in (self.conflicts, self.link):
            self.pack_start(widget, False, False, 0)
        self.connect("map", lambda *_: self.control.reload())
        self.show_all()
        self.sync()

    def build_slider_row(self):
        row = box(False, 12)
        self.title = label(text.TITLE, "power-state")
        self.title.set_valign(Gtk.Align.START)
        self.scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL)
        self.scale.set_draw_value(False)
        self.scale.set_digits(0)
        self.scale.set_round_digits(0)
        self.scale.set_hexpand(True)
        self.scale.set_size_request(SLIDER_MIN_WIDTH, -1)
        self.scale.connect("value-changed", self.on_value)
        self.scale.connect("button-press-event", lambda *_: self.editing(True))
        self.scale.connect("button-release-event", lambda *_: self.editing(False))
        self.value = label("", "power-state")
        self.value.set_valign(Gtk.Align.START)
        self.value.set_width_chars(len(text.UNLIMITED) + 2)
        self.value.set_xalign(1)
        row.pack_start(self.title, False, False, 0)
        row.pack_start(self.scale, True, True, 0)
        row.pack_start(self.value, False, False, 0)
        return row

    def build_persist_row(self):
        row = box(False, 12)
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.switch.connect("notify::active", self.on_persist)
        texts = box(spacing=2)
        texts.pack_start(label(text.PERSIST_TITLE), False, False, 0)
        texts.pack_start(label(text.PERSIST_NOTE, "muted", True), False, False, 0)
        row.pack_start(texts, True, True, 0)
        row.pack_end(self.switch, False, False, 0)
        self.persist_row = row
        return row

    # ── 使用者的操作 ───────────────────────────────────────

    def editing(self, active):
        if not self.syncing:
            self.control.set_editing(active)
        return False

    def on_value(self, scale):
        if self.syncing or not self.options:
            return
        value = limit_at(scale.get_value(), self.options)
        if value is not None:
            self.control.drag(value)

    def on_link(self, _button):
        """開啟設定視窗的「管理員功能」；回傳 True 表示已處理（不開啟網址）。"""
        self.open_settings(SETTINGS_SECTION)
        return True

    def on_persist(self, switch, _spec):
        if not self.syncing:
            self.control.set_persist(switch.get_active())

    # ── 顯示 ───────────────────────────────────────────────

    def set_options(self, options):
        if options == self.options:
            return
        self.options = options
        self.scale.set_range(0, max(len(options) - 1, 1))
        self.scale.set_increments(1, 1)
        self.scale.clear_marks()
        for index, option in enumerate(options):
            self.scale.add_mark(index, Gtk.PositionType.BOTTOM, text.mark_text(option))

    def set_tone(self, tone):
        context = self.status.get_style_context()
        for name in TONE_CLASSES.values():
            context.remove_class(name)
        context.add_class(TONE_CLASSES[tone])

    def sync(self):
        """依 ChargeControl 的狀態更新所有元件（不觸發使用者操作的處理）。"""
        state = self.control.state
        self.syncing = True
        try:
            self.set_options(state.options)
            index = index_of(state.shown, state.options)
            if self.scale.get_value() != index:
                self.scale.set_value(index)
            self.scale.set_sensitive(text.slider_enabled(state))
            set_text(self.value, text.value_text(state.shown))
            line, tone = text.status_line(state)
            set_text(self.status, line)
            self.set_tone(tone)
            show_if(self.status, bool(line))
            if not state.busy and self.switch.get_active() != state.persist:
                self.switch.set_active(state.persist)
            self.persist_row.set_sensitive(text.persist_enabled(state))
            warning = text.persist_warning(state)
            set_text(self.persist_warning, warning)
            show_if(self.persist_warning, bool(warning))
            set_text(self.conflicts, text.conflict_text(state))
            show_if(self.conflicts, bool(state.conflicts))
            show_if(self.link, text.needs_settings(state))
        finally:
            self.syncing = False
        show_if(self, text.visible(state))
