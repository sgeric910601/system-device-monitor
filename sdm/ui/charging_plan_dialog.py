"""智慧充電的編輯視窗（電源頁「智慧充電」的「設定…」開啟；第一次開啟時建立，關閉只是隱藏）。

- 目前的排程：摘要、狀態、驅動不接受要求的上限時實際的值、上次寫入的錯誤與「取消排程並還原為 N%」。
- 新排程：單次（日期與時間）或每週（選星期＋時間），建立前的預估（「需要約 1 小時 48 分，
  會在 05:57 開始」或來不及時最多能充到幾 %），以及「開始」（排程進行中為「改成這個排程」）。
- 目標、保養上限與睡眠策略寫入設定（設定的「充電」是同一份），之後建立的排程使用；
  睡眠策略也套用到目前的排程。驅動不接受選的值時說明實際的保養上限與提高時的上限。
- 「允許無人時自動調整」：開關（每次都要管理員密碼）與授權範圍的說明。
所有動作都經由 PlanActions（sdm.ui.charging_plan_wiring），這裡只負責排版。
"""
from datetime import date, datetime, timedelta

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.core.charging_plan import Once, Weekly
from sdm.ui import charging_plan_text as text
from sdm.ui.charging_plan_widgets import (HOLD, STRATEGY, TARGET, hold_row, set_text, show_if,
                                          strategy_rows, target_row, unattended_card)
from sdm.ui.main_window import install_css
from sdm.ui.monitor_widgets import card
from sdm.ui.settings.controls import Controls, wrapped
from sdm.ui.widgets import box, label

WIDTH, HEIGHT = 600, 680
DAYS_AHEAD = 7                      # 單次排程可以選今天起的 7 天
DEFAULT_DAY, DEFAULT_HOUR, DEFAULT_MINUTE = 1, 8, 0     # 預設：明天 08:00
DEFAULT_WEEKDAYS = (1, 2, 3, 4, 5)
MINUTES_PER_HOUR = 60
NOT_INSTALLED = "需要先安裝管理員元件（設定 → 管理員功能）才能建立排程。"
PAST = "這個時間已經過了，請選之後的時間。"
NO_DAYS = "請至少選一天。"
STARTED = "已建立排程。"


def spin(upper):
    widget = Gtk.SpinButton.new_with_range(0, upper, 1)
    widget.set_numeric(True)
    widget.set_width_chars(2)
    widget.connect("output", lambda button: button.set_text(f"{int(button.get_value()):02d}")
                   or True)
    return widget


def day_title(day, today):
    weekday = text.WEEKDAY_NAMES[day.isoweekday() - 1]
    prefix = {0: "今天 ", 1: "明天 "}.get((day - today).days, "")
    return f"{prefix}{day.month}/{day.day}（{weekday}）"


class ChargingPlanDialog:
    def __init__(self, parent, store, actions):
        install_css()
        self.actions, self.controls = actions, Controls(store)
        self.window = Gtk.Window(title=text.TITLE)
        self.window.set_transient_for(parent)
        self.window.set_default_size(WIDTH, HEIGHT)
        self.window.connect("delete-event", lambda *_: self.window.hide() or True)
        header = Gtk.HeaderBar(title=text.TITLE, subtitle="在指定的時間前充好")
        header.set_show_close_button(True)
        self.window.set_titlebar(header)
        snapshot, self.syncs, self.today = actions.snapshot(), [], None
        page = box(spacing=16)
        page.get_style_context().add_class("page")
        unattended, sync = unattended_card(actions, snapshot)
        self.syncs.append(sync)
        for widget in (self.current_card(), self.schedule_card(), self.options_card(snapshot),
                       unattended):
            page.pack_start(widget, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(page)
        self.window.add(scroll)
        self.snapshot = snapshot
        self.controls.bind({TARGET, HOLD, STRATEGY}, self.update_forecast)
        self.sync(snapshot)

    # ── 目前的排程 ─────────────────────────────────────────

    def current_card(self):
        self.summary, self.state = wrapped(""), wrapped("", "power-state")
        self.current_fitted = wrapped("", "muted")
        self.error = wrapped("", "power-warning")
        self.cancel = Gtk.Button(label="", halign=Gtk.Align.START)
        self.cancel.connect("clicked", lambda *_: self.actions.cancel())
        self.current = card("目前的排程", self.state, self.summary, self.current_fitted,
                            self.error, self.cancel)
        return self.current

    def sync_current(self, snapshot):
        plan = snapshot.plan
        show_if(self.current, plan is not None)
        if plan is None:
            return
        line, _ = text.status_line(plan, snapshot.decision, snapshot.now, None,
                                   snapshot.reachable)
        set_text(self.state, line)
        set_text(self.summary, text.plan_summary(plan, snapshot.now) + "・睡眠時"
                 + text.STRATEGY_TITLES[plan.strategy])
        fitted = text.plan_fitted_note(plan, snapshot.charge)
        set_text(self.current_fitted, fitted)
        show_if(self.current_fitted, bool(fitted))
        set_text(self.error, snapshot.write_error or "")
        show_if(self.error, bool(snapshot.write_error))
        self.cancel.set_label(text.cancel_label(plan))
        self.cancel.set_sensitive(not plan.restoring)

    # ── 新排程 ─────────────────────────────────────────────

    def schedule_card(self):
        self.once = Gtk.RadioButton.new_with_label_from_widget(None, "單次")
        self.weekly = Gtk.RadioButton.new_with_label_from_widget(self.once, "每週")
        kinds = box(False, 16)
        for button in (self.once, self.weekly):
            kinds.pack_start(button, False, False, 0)
            button.connect("toggled", lambda *_: self.kind_changed())
        self.date = Gtk.ComboBoxText()
        self.date.connect("changed", lambda *_: self.update_forecast())
        self.days = self.weekday_row()
        self.hour, self.minute = spin(23), spin(59)
        self.hour.set_value(DEFAULT_HOUR)
        self.minute.set_value(DEFAULT_MINUTE)
        clock = box(False, 6)          # 「完成時間」放在上一行：480 px 寬的視窗也放得下這一列
        for widget in (self.date, self.hour, label("："), self.minute):
            clock.pack_start(widget, False, False, 0)
        for widget in (self.hour, self.minute):
            widget.connect("value-changed", lambda *_: self.update_forecast())
        self.forecast, self.result = wrapped("", "muted"), wrapped("", "muted")
        self.start = Gtk.Button(label="開始", halign=Gtk.Align.START)
        self.start.get_style_context().add_class("suggested-action")
        self.start.connect("clicked", lambda *_: self.start_plan())
        return card("新排程", kinds, self.days, label("完成時間"), clock, self.forecast, self.start,
                    self.result)

    def weekday_row(self):
        row = box(False, 4)
        self.day_buttons = []
        for day, name in enumerate(text.WEEKDAY_NAMES, start=1):
            button = Gtk.ToggleButton(label=name)
            button.set_active(day in DEFAULT_WEEKDAYS)
            button.set_tooltip_text("星期" + name)
            button.connect("toggled", lambda *_: self.update_forecast())
            row.pack_start(button, False, False, 0)
            self.day_buttons.append((day, button))
        return row

    def kind_changed(self):
        weekly = self.weekly.get_active()
        show_if(self.days, weekly)
        show_if(self.date, not weekly)
        self.update_forecast()

    def fill_dates(self, now):
        """今天起 7 天；換了一天才重建（保留選的那一天）。"""
        today = datetime.fromtimestamp(now).date()
        if today == self.today:
            return
        chosen = self.date.get_active()
        self.today = today
        self.date.remove_all()
        for offset in range(DAYS_AHEAD):
            self.date.append_text(day_title(today + timedelta(days=offset), today))
        self.date.set_active(DEFAULT_DAY if chosen < 0 else min(chosen, DAYS_AHEAD - 1))

    def minute_of_day(self):
        return int(self.hour.get_value()) * MINUTES_PER_HOUR + int(self.minute.get_value())

    def chosen_schedule(self):
        if self.weekly.get_active():
            days = frozenset(day for day, button in self.day_buttons if button.get_active())
            return Weekly(days, self.minute_of_day())
        day = (self.today or date.today()) + timedelta(days=max(0, self.date.get_active()))
        hours, minutes = divmod(self.minute_of_day(), MINUTES_PER_HOUR)
        return Once(datetime(day.year, day.month, day.day, hours, minutes).timestamp())

    def chosen_values(self, snapshot):
        """（完成目標、保養上限）：目標 0 是原本的上限；保養上限不高於目標。"""
        target = self.controls.store.value(TARGET) or snapshot.original_limit
        return target, min(self.controls.store.value(HOLD), target)

    def update_forecast(self):
        snapshot = self.actions.snapshot()
        schedule = self.chosen_schedule()
        deadline = schedule.next_deadline(snapshot.now)
        problem = (NO_DAYS if schedule.weekly and not schedule.days() else PAST) \
            if deadline is None else ""
        if not problem and not (snapshot.demo or snapshot.charge.ready):
            problem = NOT_INSTALLED
        target, hold = self.chosen_values(snapshot)
        line = problem or self.actions.forecast(deadline, target, hold)
        set_text(self.forecast, line)
        self.start.set_sensitive(not problem)
        options = snapshot.charge.options if snapshot.charge is not None else ()
        fitted = text.fitted_note(target, hold, options)
        set_text(self.fitted, fitted)
        show_if(self.fitted, bool(fitted))

    def start_plan(self):
        started = self.actions.start(self.chosen_schedule())
        set_text(self.result, STARTED if started else PAST)

    # ── 目標、保養上限、睡眠策略 ───────────────────────────

    def options_card(self, snapshot):
        target, sync_target = target_row(self.controls, snapshot)
        hold, _ = hold_row(self.controls, snapshot)
        strategy, sync_strategy = strategy_rows(self.controls)
        self.syncs += [sync_target, sync_strategy]
        self.fitted = wrapped("", "muted")
        return card("目標與睡眠", target, hold, self.fitted, strategy,
                    wrapped("套用到之後建立的排程；睡眠策略也套用到目前的排程。", "muted"))

    # ── 顯示 ───────────────────────────────────────────────

    def sync(self, snapshot):
        self.snapshot = snapshot
        self.fill_dates(snapshot.now)
        self.sync_current(snapshot)
        self.start.set_label("改成這個排程" if snapshot.plan is not None else "開始")
        for sync in self.syncs:
            sync(snapshot)
        self.update_forecast()

    def present(self, snapshot):
        set_text(self.result, "")
        self.window.show_all()
        self.kind_changed()
        self.sync(snapshot)
        self.window.present()
