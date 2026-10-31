"""夥伴動畫的控制：何時播放、每秒最多 12 格、呼吸幅度、粒子與預先算好的畫格。"""
import unittest

from sdm.constants import Companion
from sdm.core.companion import BUSY, CHARGING_MOOD, IDLE, CompanionState
from sdm.core.sprite_motion import (MotionPlan, Ticker, breath, frame_interval_ms, motion_plan,
                                    should_animate, sparks, strip_index, zzz)


class FakeTimers:
    def __init__(self):
        self.live, self.next_id = {}, 1

    def add(self, interval, callback):
        self.live[self.next_id] = (interval, callback)
        self.next_id += 1
        return self.next_id - 1

    def remove(self, source):
        del self.live[source]


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Gating(unittest.TestCase):
    def test_plays_only_when_mapped_visible_and_not_saving(self):
        self.assertTrue(should_animate(True, True, False))
        for mapped, visible, saving in ((False, True, False), (True, False, False),
                                        (True, True, True), (False, False, True)):
            with self.subTest(mapped=mapped, visible=visible, saving=saving):
                self.assertFalse(should_animate(mapped, visible, saving))


class Plan(unittest.TestCase):
    """哪些外觀需要哪種計時器：沒有需要的就不留任何計時器。"""
    resting, charging = CompanionState(IDLE), CompanionState(CHARGING_MOOD, on_pad=True)

    def plan(self, appearance, state=None, live_eyes=True, follow=True, visible=True,
             saving=False):
        return motion_plan(appearance, state or self.resting, live_eyes, follow, visible, saving)

    def test_cat_always_breathes(self):
        self.assertEqual(self.plan("cat"), MotionPlan(True, False, False))

    def test_bar_moves_only_while_charging(self):
        self.assertEqual(self.plan("bar"), MotionPlan(False, False, False))
        self.assertEqual(self.plan("bar", self.charging), MotionPlan(True, False, False))

    def test_buddy_blinks_unless_asleep(self):
        self.assertEqual(self.plan("buddy"), MotionPlan(False, False, False))
        self.assertEqual(self.plan("buddy", CompanionState(BUSY)), MotionPlan(False, True, False))

    def test_headshot_eyes_blink_and_follow_the_pointer(self):
        self.assertEqual(self.plan("headshot"), MotionPlan(False, True, True))
        self.assertEqual(self.plan("headshot", follow=False), MotionPlan(False, True, False))
        self.assertEqual(self.plan("headshot", live_eyes=False), MotionPlan(False, False, False))

    def test_my_photo_never_moves(self):
        self.assertEqual(self.plan("photo", self.charging), MotionPlan(False, False, False))

    def test_hidden_or_power_saving_stops_everything(self):
        for appearance in ("cat", "bar", "buddy", "headshot"):
            for visible, saving in ((False, False), (True, True)):
                with self.subTest(appearance=appearance, visible=visible, saving=saving):
                    self.assertEqual(self.plan(appearance, CompanionState(BUSY, on_pad=True),
                                               visible=visible, saving=saving),
                                     MotionPlan(False, False, False))


class Timing(unittest.TestCase):
    def test_never_faster_than_twelve_frames_per_second(self):
        for fps in (1, 8, 12, 15, 60, 1000):
            with self.subTest(fps=fps):
                self.assertGreaterEqual(frame_interval_ms(fps), 1000 / Companion.MAX_FPS)
        self.assertEqual(frame_interval_ms(8), 125)
        self.assertEqual(frame_interval_ms(60), 84)

    def test_default_rate_is_within_the_limit(self):
        self.assertLessEqual(Companion.FPS, Companion.MAX_FPS)

    def test_eye_redraws_stay_within_the_limit(self):
        """眼睛跟著游標時每讀一次游標位置最多重畫一次，所以讀取頻率也不能超過每秒 12 格。"""
        self.assertLessEqual(Companion.POINTER_HZ, Companion.MAX_FPS)


class TickerTests(unittest.TestCase):
    def setUp(self):
        self.timers, self.clock, self.draws = FakeTimers(), Clock(), []
        self.ticker = Ticker(lambda: self.draws.append(self.ticker.elapsed), self.timers.add,
                             self.timers.remove, fps=60, clock=self.clock)

    def test_running_keeps_one_timer_at_the_capped_rate(self):
        self.ticker.set_running(True)
        self.ticker.set_running(True)
        self.assertEqual([interval for interval, _ in self.timers.live.values()], [84])

    def test_stopping_removes_the_timer_and_draws_the_still_frame(self):
        self.ticker.set_running(True)
        self.ticker.set_running(False)
        self.assertEqual(self.timers.live, {})
        self.assertEqual(self.draws, [0.0])
        self.assertFalse(self.ticker.running)

    def test_time_advances_while_running_and_restarts_from_the_still_frame(self):
        self.ticker.set_running(True)
        self.clock.now += 0.5
        self.ticker.tick()
        self.assertAlmostEqual(self.ticker.elapsed, 0.5)
        self.ticker.set_running(False)
        self.assertEqual(self.ticker.elapsed, 0.0)
        self.clock.now += 10
        self.ticker.set_running(True)
        self.clock.now += 0.25
        self.ticker.tick()
        self.assertAlmostEqual(self.ticker.elapsed, 0.25)

    def test_tick_keeps_the_timer(self):
        self.ticker.set_running(True)
        self.assertTrue(self.ticker.tick())


class Breathing(unittest.TestCase):
    def test_scale_stays_between_one_and_one_point_zero_two(self):
        samples = [breath(step / 100) for step in range(0, 800)]
        scales = [scale for scale, _ in samples]
        self.assertAlmostEqual(min(scales), 1.0)
        self.assertAlmostEqual(max(scales), 1.02, places=4)
        self.assertEqual(breath(0.0), (1.0, 0.0))

    def test_one_breath_every_four_seconds(self):
        self.assertAlmostEqual(breath(2.0)[0], 1.02)
        self.assertAlmostEqual(breath(4.0)[0], 1.0)

    def test_bob_rises_while_breathing_in(self):
        _, bob = breath(2.0)
        self.assertAlmostEqual(bob, -Companion.BOB_RATIO)


class Particles(unittest.TestCase):
    def test_zzz_rise_and_fade(self):
        early, late = zzz(0.1), zzz(0.1 + Companion.ZZZ_PERIOD * 0.8)
        self.assertEqual(len(early), Companion.ZZZ_COUNT)
        for particle in early + late:
            self.assertTrue(0 <= particle.alpha <= 1)
        first = sorted(zzz(0.0), key=lambda particle: particle.y)
        self.assertGreater(first[-1].y, first[0].y)

    def test_sparks_twinkle(self):
        frames = [sparks(step / 10) for step in range(14)]
        self.assertTrue(all(len(frame) == Companion.SPARK_COUNT for frame in frames))
        alphas = {round(particle.alpha, 2) for frame in frames for particle in frame}
        self.assertGreater(len(alphas), 3)


class Frames(unittest.TestCase):
    def test_strip_follows_the_source_rate_and_loops(self):
        self.assertEqual(strip_index(0.0, 90), 0)
        self.assertEqual(strip_index(1.0, 90), 15)
        self.assertEqual(strip_index(6.0, 90), 0)
        self.assertEqual(strip_index(6.5, 90), 7)
        self.assertEqual(strip_index(1.0, 0), 0)


if __name__ == "__main__":
    unittest.main()
