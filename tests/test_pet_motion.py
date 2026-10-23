"""摸牠的判斷、眨眼排程與大頭照眼睛的位置（移植參考專案 DesktopPet 與 CatEyes）。"""
import random
import unittest

from sdm.core.pet_motion import (BlinkScheduler, StrokeDetector, eye_circles, gaze,
                                 iris_offset, lid_fraction)


def feed(detector, points):
    """依序送入（x、時間），回傳判定為摸了一下的時間。"""
    return [now for x, now in points if detector.feed(x, now)]


class Stroke(unittest.TestCase):
    def test_reference_self_test_pets_once(self):
        points = [(x, index / 10) for index, x in enumerate((20, 80, 20, 80, 20, 80, 20))]
        petted = feed(StrokeDetector(), points)
        self.assertEqual(len(petted), 1)
        self.assertAlmostEqual(petted[0], 0.4)

    def test_cooldown_waits_before_the_next_purr(self):
        points = [(20 if index % 2 == 0 else 80, index / 10) for index in range(25)]
        petted = feed(StrokeDetector(), points)
        self.assertEqual(len(petted), 2)
        self.assertGreater(petted[1] - petted[0], 1.2)
        self.assertLess(petted[1] - petted[0], 1.4)

    def test_slow_strokes_do_not_count(self):
        points = [(20 if index % 2 == 0 else 80, index * 0.8) for index in range(10)]
        self.assertEqual(feed(StrokeDetector(), points), [])

    def test_tiny_moves_are_ignored(self):
        points = [(20 + (index % 2) * 2, index / 10) for index in range(20)]
        self.assertEqual(feed(StrokeDetector(), points), [])

    def test_leaving_resets_the_turns(self):
        detector = StrokeDetector()
        feed(detector, [(20, 0.0), (80, 0.1), (20, 0.2), (80, 0.3)])
        detector.reset()
        self.assertEqual(feed(detector, [(20, 0.4), (80, 0.5)]), [])


class Blink(unittest.TestCase):
    def test_delays_stay_between_two_and_a_half_and_six_seconds(self):
        scheduler = BlinkScheduler(random.Random(7))
        delays = [scheduler.next_delay() for _ in range(500)]
        self.assertTrue(all(2.5 <= delay <= 6.0 for delay in delays))
        self.assertLess(min(delays), 2.7)
        self.assertGreater(max(delays), 5.8)

    def test_one_in_five_blinks_twice(self):
        scheduler = BlinkScheduler(random.Random(11))
        doubles = sum(scheduler.twice() for _ in range(2000))
        self.assertTrue(300 <= doubles <= 500, doubles)

    def test_seeded_schedule_is_reproducible(self):
        first, second = BlinkScheduler(random.Random(3)), BlinkScheduler(random.Random(3))
        self.assertEqual([(first.next_delay(), first.twice()) for _ in range(20)],
                         [(second.next_delay(), second.twice()) for _ in range(20)])

    def test_lid_closes_and_opens_once_or_twice(self):
        self.assertEqual(lid_fraction(0.0, False), 0.0)
        self.assertAlmostEqual(lid_fraction(0.09, False), 1.0)
        self.assertAlmostEqual(lid_fraction(0.045, False), 0.5)
        self.assertEqual(lid_fraction(0.18, False), 0.0)
        self.assertEqual(lid_fraction(0.27, False), 0.0)
        self.assertAlmostEqual(lid_fraction(0.27, True), 1.0)
        self.assertEqual(lid_fraction(0.36, True), 0.0)
        self.assertEqual(lid_fraction(-1.0, True), 0.0)


class Eyes(unittest.TestCase):
    def test_normalized_eyes_map_to_pixels(self):
        circles = eye_circles(224, 10, 20)
        self.assertEqual(len(circles), 2)
        for (x, y, r), expected in zip(circles, ((10 + 41.216, 20 + 107.296, 13.664),
                                                 (10 + 128.128, 20 + 104.16, 16.576))):
            self.assertAlmostEqual(x, expected[0])
            self.assertAlmostEqual(y, expected[1])
            self.assertAlmostEqual(r, expected[2])

    def test_gaze_points_toward_the_pointer_and_saturates(self):
        self.assertEqual(gaze((100, 100), (100.5, 100), 100), (0.0, 0.0))
        gx, gy = gaze((100, 100), (175, 100), 100)
        self.assertAlmostEqual(gx, 0.5)
        self.assertAlmostEqual(gy, 0.0)
        gx, gy = gaze((100, 100), (100, -900), 100)
        self.assertAlmostEqual((gx, gy), (0.0, -1.0))

    def test_small_figures_use_the_minimum_range(self):
        gx, _ = gaze((0, 0), (30, 0), 20)
        self.assertAlmostEqual(gx, 0.5)

    def test_iris_moves_further_sideways_than_up_and_down(self):
        self.assertEqual(iris_offset((1.0, -1.0), 10), (3.0, -1.6))


if __name__ == "__main__":
    unittest.main()
