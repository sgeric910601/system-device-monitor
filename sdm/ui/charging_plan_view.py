"""電源頁充電上限滑桿下方的「智慧充電」那一列：狀態（等待開始・05:57 開始、充電中・預計 07:52
到 100%、已準備好、可能趕不上：只能充到 88%、暫停（系統）…）、上次寫入的錯誤、精準喚醒改用
準備優先的原因，以及開啟編輯視窗的「設定…」按鈕。桌機（沒有可調整的電池）不顯示。
文字在 sdm.ui.charging_plan_text。
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.core.charging_plan import PRECISE_WAKE
from sdm.services.wake_control import FAILED, MISSING, NOT_GRANTED
from sdm.ui import charging_plan_text as text
from sdm.ui.charging_plan_widgets import set_text, set_tone, show_if
from sdm.ui.widgets import box, label

FALLBACK_NOTES = (MISSING, NOT_GRANTED, FAILED)     # 精準喚醒做不到、睡眠前改用準備優先


class ChargingPlanView(Gtk.Box):
    def __init__(self, open_editor):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        texts = box(spacing=2)
        self.title = label(text.TITLE, "power-state")
        self.status = label("", "muted", True)
        self.note = label("", "power-warning", True)
        self.wake = label("", "muted", True)
        for widget in (self.title, self.status, self.note, self.wake):
            texts.pack_start(widget, False, False, 0)
        self.button = Gtk.Button(label=text.SETTINGS_BUTTON, valign=Gtk.Align.CENTER)
        self.button.set_tooltip_text("設定「幾點前充到幾 %」、保養上限與睡眠時的做法")
        self.button.connect("clicked", lambda *_: open_editor())
        self.pack_start(texts, True, True, 0)
        self.pack_end(self.button, False, False, 0)
        self.show_all()
        show_if(self, False)

    def sync(self, snapshot):
        line, tone = text.status_line(snapshot.plan, snapshot.decision, snapshot.now, None,
                                      snapshot.reachable)
        set_text(self.status, line if snapshot.plan is not None else line + "・" + text.HINT)
        set_tone(self.status, tone)
        error = snapshot.write_error or ""
        set_text(self.note, error)
        show_if(self.note, bool(error))
        plan = snapshot.plan
        wake = ""
        if plan is not None and plan.strategy == PRECISE_WAKE and snapshot.wake is not None \
                and snapshot.wake.note in FALLBACK_NOTES:
            wake = text.wake_note(snapshot.wake, PRECISE_WAKE, snapshot.now)
        set_text(self.wake, wake)
        show_if(self.wake, bool(wake))
        show_if(self, snapshot.laptop)
