"""設定 →「夥伴」「桌面寵物」「外觀」的文字，以及選照片、移除照片之後的設定變化。"""
import unittest

from sdm.services.companion_photo import PhotoError
from sdm.ui.settings import companion_text as text


class FakePhotos:
    def __init__(self, enabled=True, fail=None):
        self.enabled, self.fail, self.saved, self.removed = enabled, fail, [], 0
        self.photo = False

    def has_photo(self):
        return self.photo

    def save(self, source):
        if self.fail:
            raise PhotoError(self.fail)
        self.saved.append(source)
        self.photo = True

    def remove(self):
        self.removed += 1
        self.photo = False


class Notes(unittest.TestCase):
    def test_animation_note_follows_power_saving(self):
        self.assertIn("省電模式生效中", text.animation_note(True))
        self.assertIn("每秒最多 12 格", text.animation_note(False))

    def test_photo_note_in_preview(self):
        self.assertEqual(text.photo_note(FakePhotos(enabled=False)), text.PREVIEW_PHOTO)
        self.assertEqual(text.photo_note(FakePhotos()), text.PHOTO_PRIVACY)

    def test_pet_note_mentions_wayland(self):
        self.assertIn("Wayland", text.pet_note(False))
        self.assertNotIn("Wayland", text.pet_note(True))

    def test_icon_note_and_problem(self):
        self.assertIn("重新登入", text.icon_note(None))
        self.assertTrue(text.icon_note("無法寫入圖示").startswith("無法寫入圖示"))

    def test_scale_choices_are_the_presets(self):
        self.assertEqual([value for value, _ in text.SCALES], [0.75, 1.0, 1.5, 2.0])


class PhotoActions(unittest.TestCase):
    def setUp(self):
        self.changes, self.redraws = [], []

    def apply(self, **changes):
        self.changes.append(changes)

    def test_choosing_a_photo_switches_to_it(self):
        photos = FakePhotos()
        message = text.choose_photo(photos, "/picked.jpg", self.apply, lambda: self.redraws.append(1))
        self.assertIsNone(message)
        self.assertEqual((photos.saved, self.changes, self.redraws),
                         (["/picked.jpg"], [{"companion_appearance": "photo"}], [1]))

    def test_a_bad_photo_keeps_everything(self):
        photos = FakePhotos(fail="讀不到這張照片，請換一張")
        message = text.choose_photo(photos, "/bad.jpg", self.apply, lambda: self.redraws.append(1))
        self.assertEqual(message, "讀不到這張照片，請換一張")
        self.assertEqual((self.changes, self.redraws), ([], []))

    def test_removing_the_photo_falls_back_to_the_headshot(self):
        photos = FakePhotos()
        photos.photo = True
        text.remove_photo(photos, "photo", self.apply, lambda: self.redraws.append(1))
        self.assertEqual((photos.removed, self.changes, self.redraws),
                         (1, [{"companion_appearance": "headshot"}], [1]))
        text.remove_photo(photos, "cat", self.apply, lambda: None)
        self.assertEqual(len(self.changes), 1)


if __name__ == "__main__":
    unittest.main()
