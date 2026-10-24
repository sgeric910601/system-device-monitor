"""右鍵選單的內容與生命週期（不建立真正的選單）：用完就在閒置時銷毀，
從寵物自己的選單關閉寵物時延到閒置才做（選單還在處理中，不能馬上銷毀視窗）。"""
import unittest

from sdm.core.settings import Preferences
from sdm.ui import pet_menu
from sdm.ui.companion import appearance_entries


class FakeMenu:
    def __init__(self):
        self.handlers, self.destroyed = {}, 0

    def connect(self, signal, handler):
        self.handlers[signal] = handler

    def destroy(self):
        self.destroyed += 1


class Idle:
    def __init__(self):
        self.queued = []

    def __call__(self, callback):
        self.queued.append(callback)
        return len(self.queued)

    def run(self):
        while self.queued:
            self.assert_false(self.queued.pop(0)())

    @staticmethod
    def assert_false(value):
        assert value is False, "idle callbacks must not repeat"


def entries_by_title(entries):
    return {entry.title: entry for entry in entries if hasattr(entry, "title")}


class Lifecycle(unittest.TestCase):
    def test_menu_is_destroyed_on_idle_after_it_closes(self):
        menu, idle = FakeMenu(), Idle()
        self.assertIs(pet_menu.dispose_after_use(menu, idle), menu)
        self.assertEqual(menu.destroyed, 0)
        menu.handlers["deactivate"](menu)
        self.assertEqual(menu.destroyed, 0)
        idle.run()
        self.assertEqual(menu.destroyed, 1)

    def test_deferred_actions_wait_for_idle(self):
        calls, idle = [], Idle()
        action = pet_menu.deferred(lambda: calls.append("closed"), idle)
        action()
        self.assertEqual(calls, [])
        idle.run()
        self.assertEqual(calls, ["closed"])


class PetEntries(unittest.TestCase):
    def setUp(self):
        self.updates, self.idle, self.opened = [], Idle(), []
        self.entries = pet_menu.pet_entries(
            Preferences.defaults(), False, lambda **changes: self.updates.append(changes),
            lambda: self.opened.append("power"), lambda: self.opened.append("settings"),
            defer=lambda callback: pet_menu.deferred(callback, self.idle))

    def test_order_of_the_items(self):
        titles = [getattr(entry, "title", None) for entry in self.entries]
        self.assertEqual(titles, [pet_menu.OPEN, None, None, pet_menu.SIZE, pet_menu.LOOK, None,
                                  pet_menu.SETTINGS, pet_menu.CLOSE])

    def test_closing_the_pet_waits_until_the_menu_is_done(self):
        entries_by_title(self.entries)[pet_menu.CLOSE].callback()
        self.assertEqual(self.updates, [])
        self.idle.run()
        self.assertEqual(self.updates, [{"desktop_pet": False}])

    def test_size_and_place_choices_write_the_settings(self):
        size = entries_by_title(self.entries)[pet_menu.SIZE].entries[0]
        self.assertTrue(size.current(1.0))
        size.choose(2.0)
        place = self.entries[2]
        self.assertTrue(place.current(False))
        place.choose(True)
        self.assertEqual(self.updates, [{"desktop_pet_scale": 2.0},
                                        {"desktop_pet_on_wallpaper": True}])

    def test_my_photo_is_disabled_without_a_photo(self):
        looks = entries_by_title(self.entries)[pet_menu.LOOK].entries[0]
        self.assertFalse(looks.enabled("photo"))
        self.assertTrue(looks.enabled("cat"))
        self.assertIn((("photo", pet_menu.NO_PHOTO)), looks.options)


class CompanionEntries(unittest.TestCase):
    def test_appearance_menu_lists_every_look_and_the_settings(self):
        chosen, opened = [], []
        entries = appearance_entries("cat", True, chosen.append, lambda: opened.append(1))
        looks = entries[0]
        self.assertEqual([value for value, _ in looks.options],
                         ["cat", "buddy", "bar", "headshot", "photo"])
        self.assertTrue(looks.current("cat"))
        looks.choose("bar")
        entries[-1].callback()
        self.assertEqual((chosen, opened), (["bar"], [1]))


if __name__ == "__main__":
    unittest.main()
