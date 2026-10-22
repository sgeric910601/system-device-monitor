"""電力流向動畫：只在看得到、沒有最小化、沒有省電時播放，最多每秒 15 格；
流速與瓦數相關；數值更新時不重設相位，停止時移除計時器（參考專案 AnimationTests 的兩個情境）。"""
import unittest

from sdm.constants import Flow
from sdm.ui.flow_animation import FlowAnimator, advanced, dash_speed, should_animate


class FakeTimers:
    def __init__(self):
        self.live, self.next_id = {}, 1

    def add(self, milliseconds, callback):
        source = self.next_id
        self.next_id += 1
        self.live[source] = (milliseconds, callback)
        return source

    def remove(self, source):
        del self.live[source]


class Gating(unittest.TestCase):
    def test_every_condition_is_required(self):
        self.assertTrue(should_animate(mapped=True, window_visible=True, iconified=False,
                                       saving=False))
        for blocked in (dict(mapped=False), dict(window_visible=False), dict(iconified=True),
                        dict(saving=True)):
            state = dict(mapped=True, window_visible=True, iconified=False, saving=False)
            state.update(blocked)
            with self.subTest(blocked):
                self.assertFalse(should_animate(**state))

    def test_frame_rate_is_at_most_fifteen(self):
        self.assertLessEqual(1000 / Flow.FRAME_MS, 15)


class Speed(unittest.TestCase):
    def test_more_watts_flow_faster_within_limits(self):
        self.assertEqual(dash_speed(None), 0)
        self.assertEqual(dash_speed(0), 0)
        self.assertLess(dash_speed(5), dash_speed(60))
        self.assertLess(dash_speed(60), dash_speed(300))
        self.assertEqual(dash_speed(100000), Flow.DASH_SPEED_MAX)


class Phases(unittest.TestCase):
    def test_numeric_updates_keep_the_phase(self):
        first = advanced({}, 1.0, {"gpu": 10.0})
        second = advanced(first, 0.5, {"gpu": 40.0})
        self.assertAlmostEqual(second["gpu"], (10.0 + 20.0) % Flow.DASH_PERIOD)
        self.assertEqual(first, {"gpu": 10.0 % Flow.DASH_PERIOD})     # 不修改原本的

    def test_obsolete_branches_are_dropped(self):
        phases = advanced({"gpu": 3.0, "old": 4.0}, 0.1, {"gpu": 10.0})
        self.assertEqual(set(phases), {"gpu"})


class Animator(unittest.TestCase):
    def setUp(self):
        self.timers, self.now, self.draws = FakeTimers(), [10.0], []
        self.animator = FlowAnimator(lambda: self.draws.append(True), self.timers.add,
                                     self.timers.remove, clock=lambda: self.now[0])

    def tick(self, seconds):
        self.now[0] += seconds
        (_, callback), = self.timers.live.values()
        return callback()

    def test_runs_one_timer_only_while_allowed(self):
        self.animator.set_running(True)
        self.animator.set_running(True)
        self.assertEqual([ms for ms, _ in self.timers.live.values()], [Flow.FRAME_MS])
        self.animator.set_running(False)
        self.assertEqual(self.timers.live, {})
        self.assertFalse(self.animator.running)

    def test_ticks_advance_and_redraw(self):
        self.animator.set_speeds({"source": 20.0})
        self.animator.set_running(True)
        self.assertTrue(self.tick(0.5))
        self.assertAlmostEqual(self.animator.phases["source"], 10.0)
        self.assertEqual(self.draws, [True])

    def test_speed_change_keeps_the_phase_and_restart_does_not_jump(self):
        self.animator.set_speeds({"source": 20.0})
        self.animator.set_running(True)
        self.tick(0.5)
        self.animator.set_speeds({"source": 40.0})
        self.tick(0.25)
        self.assertAlmostEqual(self.animator.phases["source"], 20.0 % Flow.DASH_PERIOD)
        self.animator.set_running(False)
        self.now[0] += 100            # 停止期間不累積
        self.animator.set_running(True)
        self.tick(0.25)
        self.assertAlmostEqual(self.animator.phases["source"], 30.0 % Flow.DASH_PERIOD)

    def test_stopping_redraws_once_for_the_static_variant(self):
        self.animator.set_running(True)
        self.draws.clear()
        self.animator.set_running(False)
        self.assertEqual(self.draws, [True])


if __name__ == "__main__":
    unittest.main()
