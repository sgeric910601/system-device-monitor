"""夥伴的圖案畫在離屏的 Cairo 圖面上（不需要顯示器）：每種外觀都畫得出來，
呼吸以腳底為基準、眼睛與表情會改變畫面，靜態圖等於動畫的第一格。"""
from pathlib import Path
import tempfile
import unittest

import cairo
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

from sdm.core.companion import (BUSY, CHARGING_MOOD, FULL_MOOD, GOOD, IDLE, LOW, WARNING,
                                CompanionState)
from sdm.core.pet_geometry import Rect
from sdm.ui import sprite
from sdm.ui.companion_draw import EyeScene, Scene, band_patch, draw_figure, lid_patches

ROOT = Path(__file__).resolve().parents[1]
FG = (0.2, 0.2, 0.2)
IMAGES = sprite.Images(ROOT)


def render(scene, width=96, height=100):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    draw_figure(cairo.Context(surface), Rect(0, 0, width, height), scene, IMAGES)
    surface.flush()
    return surface


def alpha_rows(surface, threshold=0):
    """有透明度超過 threshold 的像素的列（由上而下的索引）。"""
    data, stride = bytes(surface.get_data()), surface.get_stride()
    return [row for row in range(surface.get_height())
            if max(data[row * stride + 3:(row + 1) * stride:4]) > threshold]


def scene(appearance, state=CompanionState(IDLE, 0.6), **changes):
    values = dict(appearance=appearance, state=state, t=0.0, fg=FG, pet=False)
    values.update(changes)
    return Scene(**values)


class EveryLook(unittest.TestCase):
    def test_each_appearance_draws_on_page_and_pet(self):
        for appearance in ("cat", "buddy", "bar", "headshot", "photo"):
            for pet in (False, True):
                for state in (CompanionState(IDLE, 0.5), CompanionState(CHARGING_MOOD, 0.5, GOOD,
                                                                        on_pad=True),
                              CompanionState(LOW, 0.1, WARNING), CompanionState(BUSY, None)):
                    with self.subTest(appearance=appearance, pet=pet, mood=state.mood):
                        size = (176, 176) if pet else (72, 75)
                        surface = render(scene(appearance, state, pet=pet), *size)
                        self.assertTrue(alpha_rows(surface))

    def test_resting_cat_moves_between_frames(self):
        first = bytes(render(scene("cat", t=0.0)).get_data())
        self.assertNotEqual(first, bytes(render(scene("cat", t=1.0)).get_data()))


class Breathing(unittest.TestCase):
    def test_feet_stay_on_the_ground_while_the_body_grows(self):
        for pet in (False, True):
            with self.subTest(pet=pet):
                size = (176, 176) if pet else (96, 100)
                rest = alpha_rows(render(scene("cat", pet=pet), *size), 160)
                full = alpha_rows(render(scene("cat", pet=pet, t=2.0), *size), 160)
                self.assertEqual(rest[-1], full[-1])
                self.assertLessEqual(full[0], rest[0])

    def test_charging_cat_draws_the_pad_and_sparks(self):
        charging = CompanionState(CHARGING_MOOD, 0.6, GOOD, on_pad=True)
        still = bytes(render(scene("cat", charging, pet=True), 176, 176).get_data())
        moving = bytes(render(scene("cat", charging, pet=True, t=0.35), 176, 176).get_data())
        self.assertNotEqual(still, moving)


class Expressions(unittest.TestCase):
    def test_buddy_moods_differ(self):
        images = {mood: bytes(render(scene("buddy", CompanionState(mood, 0.6))).get_data())
                  for mood in (IDLE, BUSY, LOW, CHARGING_MOOD, FULL_MOOD)}
        self.assertEqual(len(set(images.values())), 5)

    def test_buddy_blinks_with_the_lid(self):
        awake = CompanionState(BUSY, 0.6)
        open_eyes = bytes(render(scene("buddy", awake)).get_data())
        closed = bytes(render(scene("buddy", awake, eyes=EyeScene(((0, 0), (0, 0)), 1.0)))
                       .get_data())
        self.assertNotEqual(open_eyes, closed)

    def test_headshot_eyes_follow_the_gaze_and_close(self):
        base = scene("headshot", pet=True)
        ahead = bytes(render(base._replace(eyes=EyeScene(((0, 0), (0, 0)), 0.0)), 176, 176)
                      .get_data())
        left = bytes(render(base._replace(eyes=EyeScene(((-1, 0), (-1, 0)), 0.0)), 176, 176)
                     .get_data())
        shut = bytes(render(base._replace(eyes=EyeScene(((0, 0), (0, 0)), 1.0)), 176, 176)
                     .get_data())
        self.assertEqual(len({ahead, left, shut}), 3)

    def test_my_photo_without_a_photo_shows_the_headshot(self):
        headshot = bytes(render(scene("headshot")).get_data())
        self.assertEqual(bytes(render(scene("photo")).get_data()), headshot)

    def test_my_photo_is_drawn_when_given(self):
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 16, 16)
        pixbuf.fill(0xff0000ff)
        photo = bytes(render(scene("photo", photo=sprite.surface_from_pixbuf(pixbuf)))
                      .get_data())
        self.assertNotEqual(photo, bytes(render(scene("headshot")).get_data()))


class ScaledCache(unittest.TestCase):
    def test_each_size_is_scaled_once_and_the_cache_stays_small(self):
        images = sprite.Images(ROOT)
        first = images.scaled("pet-cat", 160, 106)
        self.assertEqual((first.get_width(), first.get_height()), (160, 106))
        self.assertIs(images.scaled("pet-cat", 160, 106), first)
        for width in range(20, 40):
            images.scaled("pet-cat", width, width)
        self.assertLessEqual(len(images.scaled_cache), sprite.SCALED_CACHE_SIZE)
        self.assertIsNone(images.scaled("missing-name", 10, 10))


class Frames(unittest.TestCase):
    def test_frame_sheet_mode_falls_back_to_the_still_image(self):
        with tempfile.TemporaryDirectory() as temp:
            empty = sprite.Images(temp)
            self.assertIsNone(empty.frames("pet-cat"))
        self.assertIsNone(IMAGES.frames("missing-name"))

    def test_frame_sheet_is_cut_into_cells(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)/"assets/companion"
            folder.mkdir(parents=True)
            still = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 4, 3)
            still.savev(str(folder/"pet-cat.png"), "png", [], [])
            sheet = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 40, 9)
            sheet.savev(str(folder/"pet-cat-frames.png"), "png", [], [])
            frames = sprite.Images(temp).frames("pet-cat")
            self.assertEqual((frames.width, frames.height, frames.count), (4, 3, 30))


class Lids(unittest.TestCase):
    def test_each_eye_gets_a_small_fur_patch_once(self):
        images = sprite.Images(ROOT)
        patches = lid_patches(images)
        self.assertEqual([(patch.get_width(), patch.get_height()) for patch in patches],
                         [(5, 3), (5, 3)])
        self.assertIs(lid_patches(images), patches)

    def test_patch_ignores_the_part_outside_the_photo(self):
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 8, 8)
        pixbuf.fill(0x204060ff)
        self.assertIsNotNone(band_patch(pixbuf, (-3, -3, 6, 6)))
        self.assertIsNone(band_patch(pixbuf, (-9, -9, 4, 4)))


if __name__ == "__main__":
    unittest.main()
