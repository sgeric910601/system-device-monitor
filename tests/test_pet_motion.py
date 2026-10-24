"""摸牠的判斷、眨眼排程與大頭照眼睛的位置（移植參考專案 DesktopPet 與 CatEyes）。"""
import random
import unittest

from sdm.core.pet_motion import (BlinkScheduler, BlinkTimer, StrokeDetector, eye_circles, gaze,
                                 gaze_moved, hearts, hearts_done, iris_offset, lid_fraction,
                                 next_saying)


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

    def test_slow_strokes_in_small_steps_still_count(self):
        """滑鼠事件很密時每次只動 1–2 px：累積超過門檻才算一次移動。"""
        for step in (1, 2):
            with self.subTest(step=step):
                points, x, now = [], 20, 0.0
                for sweep in range(4):
                    direction = 1 if sweep % 2 == 0 else -1
                    for _ in range(60 // step):
                        x += direction * step
                        now += 0.005
                        points.append((x, now))
                self.assertEqual(len(feed(StrokeDetector(), points)), 1)

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


class Hearts(unittest.TestCase):
    def test_three_hearts_rise_and_fade_one_after_another(self):
        start = hearts(0.0)
        self.assertEqual(len(start), 3)
        self.assertEqual([heart.alpha for heart in start], [0.0, 0.0, 0.0])
        middle = hearts(0.5)
        self.assertGreater(middle[0].alpha, 0.9)
        self.assertLess(middle[0].y, start[0].y)
        self.assertLess(middle[0].y, middle[2].y)
        self.assertEqual([round(heart.x, 3) for heart in start], [0.409, 0.5, 0.591])

    def test_hearts_finish_after_the_last_one_fades(self):
        self.assertFalse(hearts_done(1.5))
        self.assertTrue(hearts_done(1.61))
        self.assertTrue(all(heart.alpha == 0 for heart in hearts(1.61)))


class Sayings(unittest.TestCase):
    def test_each_click_says_the_next_line(self):
        lines = ["a", "b", "c"]
        self.assertEqual([next_saying(lines, index) for index in range(5)],
                         ["a", "b", "c", "a", "b"])
        self.assertIsNone(next_saying([], 3))


class FakeTimers:
    def __init__(self):
        self.live, self.next_id = {}, 1

    def add(self, interval, callback):
        self.live[self.next_id] = (interval, callback)
        self.next_id += 1
        return self.next_id - 1

    def remove(self, source):
        del self.live[source]

    def fire(self):
        """執行唯一的計時器；回呼回傳 False 時移除（與 GLib 相同）。"""
        (source, (_, callback)), = self.live.items()
        if not callback():
            self.live.pop(source, None)


class Clock:
    def __init__(self):
        self.now = 50.0

    def __call__(self):
        return self.now


class Timer(unittest.TestCase):
    def setUp(self):
        self.timers, self.clock, self.changes = FakeTimers(), Clock(), []
        self.blink = BlinkTimer(lambda: self.changes.append(self.blink.lid), self.timers.add,
                                self.timers.remove, random.Random(5), self.clock)

    def test_waits_a_random_delay_then_blinks_and_reopens(self):
        self.blink.set_running(True)
        (delay, _), = self.timers.live.values()
        self.assertTrue(2500 <= delay <= 6000)
        self.timers.fire()
        (frame, _), = self.timers.live.values()
        self.assertGreaterEqual(frame, 84)
        self.clock.now += 0.09
        self.timers.fire()
        self.assertAlmostEqual(self.blink.lid, 1.0)
        self.clock.now += 1.0
        self.timers.fire()
        self.assertEqual(self.blink.lid, 0.0)
        (next_delay, _), = self.timers.live.values()
        self.assertTrue(2500 <= next_delay <= 6000)

    def test_stopping_opens_the_eyes_and_removes_the_timer(self):
        self.blink.set_running(True)
        self.timers.fire()
        self.clock.now += 0.09
        self.timers.fire()
        self.blink.set_running(False)
        self.assertEqual((self.timers.live, self.blink.lid), ({}, 0.0))
        self.blink.set_running(False)
        self.assertFalse(self.blink.running)


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

    def test_tiny_gaze_changes_do_not_redraw(self):
        self.assertFalse(gaze_moved(((0.0, 0.0),), ((0.02, 0.04),)))
        self.assertTrue(gaze_moved(((0.0, 0.0),), ((0.05, 0.0),)))
        self.assertTrue(gaze_moved(((0.0, 0.0),), ((0.0, 0.06),)))
        self.assertTrue(gaze_moved((), ((0.0, 0.0),)))

    def test_iris_moves_further_sideways_than_up_and_down(self):
        self.assertEqual(iris_offset((1.0, -1.0), 10), (3.0, -1.6))


if __name__ == "__main__":
    unittest.main()
