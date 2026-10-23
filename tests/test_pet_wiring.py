"""桌面寵物的接線：依設定建立或關閉、位置與大小即時套用、文字跟著耗電快照、示範模式不記位置。

不建立真正的視窗：桌面寵物換成記錄呼叫的假物件。
"""
import unittest

from sdm.core.schedule import cadence
from tests.test_companion_wiring import FakePhotos, FakeView, desktop, monitor


class FakePet:
    instances = []

    def __init__(self, host):
        self.host, self.view = host, FakeView()
        self.calls = []
        FakePet.instances.append(self)

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name,) + args)


def pet_monitor(power=None, preview=None):
    app = monitor(power, preview)
    app.pet_factory = FakePet
    app.photos, app.companion_images = FakePhotos(), object()
    app.opened = []
    app.open_page = lambda widget, page: app.opened.append(page)
    app.open_settings = lambda section=None: app.opened.append("settings:" + str(section))
    app.today_recap = lambda now: None
    return app


class Lifecycle(unittest.TestCase):
    def setUp(self):
        FakePet.instances = []

    def test_turning_on_shows_the_pet_and_off_closes_it(self):
        app = pet_monitor(desktop())
        app.sync_pet()
        self.assertIsNone(app.pet)
        app.store.update(desktop_pet=True)
        pet = app.pet
        names = [call[0] for call in pet.calls]
        self.assertLess(names.index("show"), names.index("apply"))
        self.assertIn(pet.view, app.companion_views())
        app.store.update(desktop_pet=False)
        self.assertIsNone(app.pet)
        self.assertEqual(pet.calls[-1], ("destroy",))

    def test_place_and_size_are_applied_live(self):
        app = pet_monitor(desktop())
        app.store.update(desktop_pet=True)
        app.store.update(desktop_pet_on_wallpaper=True, desktop_pet_scale=1.5)
        self.assertEqual(app.pet.calls[-1], ("apply", True, 1.5))
        self.assertEqual(len(FakePet.instances), 1)

    def test_new_pet_gets_the_current_look_and_mood(self):
        app = pet_monitor(desktop())
        app.store.update(companion_appearance="buddy")
        app.store.update(desktop_pet=True)
        self.assertEqual(app.pet.view.last("appearance"), "buddy")
        self.assertIn("state", app.pet.view.configured[-1])

    def test_stop_closes_the_pet(self):
        app = pet_monitor(desktop())
        app.store.update(desktop_pet=True)
        pet = app.pet
        app.close_pet()
        self.assertIsNone(app.pet)
        self.assertEqual(pet.calls[-1], ("destroy",))


class Updates(unittest.TestCase):
    def test_new_data_refreshes_the_bubble_and_motion(self):
        app = pet_monitor(desktop())
        app.store.update(desktop_pet=True)
        app.update_companions()
        self.assertIn(("status_changed",), app.pet.calls)
        app.cadence = cadence(2, True)
        app.update_companion_motion()
        self.assertEqual(app.pet.calls[-1], ("set_motion", True))


class Host(unittest.TestCase):
    def test_texts_and_actions(self):
        app = pet_monitor(desktop())
        app.store.update(desktop_pet=True)
        host = app.pet.host
        self.assertEqual(host.thought(), "整機 250 W・市電")
        self.assertEqual(host.sayings()[0], "插著電在休息")
        host.update(desktop_pet_scale=2.0)
        self.assertEqual(app.store.preferences.desktop_pet_scale, 2.0)
        host.open_power()
        host.open_settings()
        self.assertEqual(app.opened, ["power", "settings:pet"])
        self.assertIs(host.preferences(), app.store.preferences)

    def test_invalid_updates_are_ignored(self):
        app = pet_monitor(desktop())
        app.store.update(desktop_pet=True)
        with self.assertLogs("sdm.ui.companion_wiring", "WARNING"):
            app.pet.host.update(desktop_pet_scale=9.0)
        self.assertEqual(app.store.preferences.desktop_pet_scale, 1.0)

    def test_position_is_remembered_only_in_normal_runs(self):
        normal = pet_monitor(desktop())
        normal.store.update(desktop_pet=True)
        self.assertTrue(normal.pet.host.position.enabled)
        demo = pet_monitor(desktop(), preview="desktop")
        demo.store.update(desktop_pet=True)
        self.assertFalse(demo.pet.host.position.enabled)


if __name__ == "__main__":
    unittest.main()
