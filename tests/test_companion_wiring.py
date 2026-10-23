"""電源頁的充電夥伴接線：表情跟著耗電快照、點一下換外觀、設定即時套用、動畫只在看得到時播放。

不建立真正的 GTK 元件：夥伴換成記錄呼叫的假元件。
"""
import unittest

from sdm.core.companion import IDLE, LOW, CompanionState
from sdm.core.schedule import cadence
from sdm.core.settings import LEGACY_DEFAULTS, Preferences
from sdm.data.ups import UpsReading
from sdm.services.power_service import PowerService
from sdm.services.settings_store import SettingsStore
from sdm.ui import app as ui_app


class FakeView:
    def __init__(self):
        self.configured, self.motion = [], []

    def configure(self, **values):
        self.configured.append({key: value for key, value in values.items() if value is not None})

    def set_motion(self, visible, saving):
        self.motion.append((visible, saving))

    def last(self, key):
        return next(values[key] for values in reversed(self.configured) if key in values)


class FakePhotos:
    def __init__(self, has_photo=False):
        self.photo = has_photo

    def has_photo(self):
        return self.photo


def desktop(on_battery=False):
    ups = UpsReading(status=("ONBATT",) if on_battery else ("ONLINE",), online=not on_battery,
                     on_battery=on_battery, charge_pct=90.0, load_pct=30.0, load_w=250.0)
    return PowerService(read_ups=lambda: ups, read_gpus=lambda: (), read_uptime=lambda: None,
                        clock=lambda: 1.0, wall=lambda: 1.0).sample()


def monitor(power=None, preview=None):
    app = ui_app.Monitor.__new__(ui_app.Monitor)
    app.preview, app.smoke, app.closing = preview, False, False
    app.preferences, app.settings = Preferences.defaults(), dict(LEGACY_DEFAULTS)
    app.scheduler, app.power_page, app.tray, app.pet = None, None, None, None
    app.companion, app.photos = FakeView(), FakePhotos()
    app.power = power
    app.current_power = lambda: app.power
    app.window_iconified, app.window_obscured = False, False
    app.window_shown = lambda: True
    app.cadence = cadence(2, False)
    app.store = SettingsStore(LEGACY_DEFAULTS, Preferences.defaults(), None, None,
                              lambda ms, callback: None, lambda source: None, persist=False)
    app.store.subscribe(app.settings_changed)
    return app


class State(unittest.TestCase):
    def test_power_snapshots_set_the_mood(self):
        app = monitor(desktop())
        app.update_companions()
        self.assertEqual(app.companion.last("state"), CompanionState(IDLE, 0.9))
        app.power = desktop(on_battery=True)
        app.update_companions()
        self.assertEqual(app.companion.last("state").mood, LOW)

    def test_settings_are_applied_to_the_view(self):
        app = monitor(desktop())
        app.apply_companion_settings()
        self.assertEqual(app.companion.configured[-1],
                         dict(appearance="cat", live_eyes=True, follow_cursor=True))
        app.store.update(companion_appearance="buddy", desktop_pet_live_eyes=False)
        self.assertEqual(app.companion.last("appearance"), "buddy")
        self.assertFalse(app.companion.last("live_eyes"))

    def test_low_threshold_changes_redo_the_mood(self):
        app = monitor(desktop())
        app.store.update(low_threshold=30)
        self.assertIn("state", app.companion.configured[-1])


class Cycle(unittest.TestCase):
    def test_click_moves_to_the_next_look(self):
        app = monitor()
        app.select_appearance(None)
        self.assertEqual(app.store.preferences.companion_appearance, "buddy")
        app.store.update(companion_appearance="headshot")
        app.select_appearance(None)
        self.assertEqual(app.store.preferences.companion_appearance, "cat")

    def test_photo_is_part_of_the_cycle_once_chosen(self):
        app = monitor()
        app.photos = FakePhotos(has_photo=True)
        app.store.update(companion_appearance="headshot")
        app.select_appearance(None)
        self.assertEqual(app.store.preferences.companion_appearance, "photo")

    def test_menu_picks_a_specific_look(self):
        app = monitor()
        app.select_appearance("bar")
        self.assertEqual(app.store.preferences.companion_appearance, "bar")

    def test_read_only_preferences_keep_the_look(self):
        app = monitor()
        app.store.preferences = app.store.preferences.__class__.defaults(read_only=True)
        with self.assertLogs("sdm.ui.companion_wiring", "WARNING"):
            app.select_appearance(None)
        self.assertEqual(app.store.preferences.companion_appearance, "cat")


class Motion(unittest.TestCase):
    def test_animation_follows_window_visibility_and_power_saving(self):
        app = monitor()
        app.update_companion_motion()
        app.window_obscured = True
        app.update_companion_motion()
        app.window_obscured, app.cadence = False, cadence(2, True)
        app.update_companion_motion()
        app.window_shown = lambda: False
        app.cadence = cadence(2, False)
        app.update_companion_motion()
        self.assertEqual(app.companion.motion, [(True, False), (False, False), (True, True),
                                                (False, False)])

    def test_nothing_happens_before_the_view_exists(self):
        app = monitor()
        app.companion = None
        app.update_companions()
        app.update_companion_motion()
        app.apply_companion_settings()


if __name__ == "__main__":
    unittest.main()
