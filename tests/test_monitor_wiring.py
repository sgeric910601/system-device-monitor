"""系統監控頁的接線：頁面顯示時才排詳細取樣、視窗關閉或最小化就停止、過期結果丟棄。

不建立真正的 GTK 視窗：沒有顯示器時建立元件會讓程序中止。
"""
from concurrent.futures import Future
import unittest
from unittest.mock import patch

from sdm.core.snapshots import DetailSnapshot
from sdm.core.tiers import DetailGate, DetailRequest, request_for
from sdm.ui import app as ui_app
from sdm.ui import monitor_wiring


class FakeScroll:
    def __init__(self, mapped=False): self.mapped = mapped
    def get_mapped(self): return self.mapped


class FakeCollector:
    storage_visible = False
    def detail(self, request): return DetailSnapshot(stamp=1.0)


class FakePool:
    def __init__(self): self.submitted = []
    def submit(self, function, *args):
        self.submitted.append((function, args))
        return Future()


class FakePage:
    def __init__(self): self.details, self.systems = [], []
    def set_detail(self, snapshot): self.details.append(snapshot)
    def set_system(self, fast, slow): self.systems.append((fast, slow))


class FakeStack:
    def __init__(self): self.visible = None
    def set_visible_child_name(self, name): self.visible = name


def monitor():
    app = ui_app.Monitor.__new__(ui_app.Monitor)
    app.closing, app.busy, app.errors = False, set(), {}
    app.collector, app.pool = FakeCollector(), FakePool()
    app.detail_gate = DetailGate()
    app.window_iconified = False
    app.scrolls = {name: FakeScroll() for name in ui_app.SMOKE_PAGES}
    app.monitor_pages = {"cpu": FakePage(), "memory": FakePage()}
    app.power_page = FakePage()
    return app


class Visibility(unittest.TestCase):
    def setUp(self):
        self.app = monitor()
        timer = patch.object(monitor_wiring.GLib, "timeout_add_seconds", return_value=42)
        self.add = timer.start()
        self.addCleanup(timer.stop)
        remove = patch.object(monitor_wiring.GLib, "source_remove")
        self.remove = remove.start()
        self.addCleanup(remove.stop)

    def show(self, *pages):
        for name, scroll in self.app.scrolls.items():
            scroll.mapped = name in pages
        self.app.visibility_changed()

    def test_window_closed_means_no_timer_and_no_sampling(self):
        self.show()
        self.add.assert_not_called()
        self.assertEqual(self.app.pool.submitted, [])
        self.assertFalse(self.app.collector.storage_visible)

    def test_showing_a_page_starts_sampling_at_once(self):
        self.show("cpu")
        self.add.assert_called_once()
        self.assertEqual(self.app.detail_timer, 42)
        self.assertEqual(self.app.pool.submitted[0][1], (request_for({"cpu"}),))
        self.assertIn(monitor_wiring.DETAIL, self.app.busy)

    def test_device_page_needs_no_detail(self):
        self.show("devices")
        self.add.assert_not_called()

    def test_power_page_samples_only_the_app_ranking(self):
        self.show("power")
        self.add.assert_called_once()
        self.assertEqual(self.app.pool.submitted[0][1], (DetailRequest(apps_cpu=True),))

    def test_overview_speeds_up_the_disk_list(self):
        self.show("overview")
        self.assertTrue(self.app.collector.storage_visible)
        self.show("network")
        self.assertFalse(self.app.collector.storage_visible)

    def test_hiding_the_window_stops_the_timer(self):
        self.show("memory")
        self.show()
        self.remove.assert_called_once_with(42)
        self.assertEqual(self.app.detail_timer, 0)

    def test_detail_error_clears_when_no_detail_page_is_shown(self):
        self.show("cpu")
        self.app.errors[monitor_wiring.DETAIL] = monitor_wiring.DETAIL_ERROR
        self.show("devices")
        self.assertEqual(self.app.errors, {})

    def test_minimized_window_counts_as_hidden(self):
        self.show("cpu")
        self.app.window_iconified = True
        self.app.visibility_changed()
        self.assertIsNone(self.app.detail_gate.request)
        self.assertEqual(self.app.detail_timer, 0)

    def test_tick_stops_when_nothing_is_visible(self):
        self.show("cpu")
        self.app.busy.clear()
        self.assertTrue(self.app.detail_tick())
        self.app.detail_gate.update(None)
        self.assertFalse(self.app.detail_tick())
        self.assertEqual(self.app.detail_timer, 0)

    def test_no_overlap_while_one_runs(self):
        self.show("cpu")
        self.app.submit_detail()
        self.assertEqual(len(self.app.pool.submitted), 1)


class Seeding(unittest.TestCase):
    def test_demo_curves_are_filled_once_when_a_page_is_first_shown(self):
        app = monitor()
        seeds = []
        class Page(FakePage):
            def seed(self, histories): seeds.append(histories)
        app.monitor_pages = {"cpu": Page(), "memory": Page()}
        app.collector.histories = lambda stamp: {"cpu": stamp}
        app.seeded = set()
        app.seed_visible({"overview", "cpu"})
        app.seed_visible({"cpu"})
        self.assertEqual(len(seeds), 1)
        app.seed_visible({"memory"})
        self.assertEqual(len(seeds), 2)

    def test_real_data_is_never_seeded(self):
        app = monitor()
        app.seeded = set()
        app.seed_visible({"cpu"})
        self.assertEqual(app.seeded, set())


class Results(unittest.TestCase):
    def setUp(self):
        self.app = monitor()
        self.app.detail_gate.update(request_for({"cpu"}))
        self.ticket = self.app.detail_gate.ticket()

    def finished(self, value=None, error=None):
        future = Future()
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(value or DetailSnapshot(stamp=5.0))
        return future

    def test_current_results_reach_every_page(self):
        self.app.busy.add(monitor_wiring.DETAIL)
        snapshot = DetailSnapshot(stamp=5.0)
        self.assertFalse(self.app.detail_received(self.ticket, self.finished(snapshot)))
        self.assertEqual(self.app.monitor_pages["cpu"].details, [snapshot])
        self.assertEqual(self.app.monitor_pages["memory"].details, [snapshot])
        self.assertEqual(self.app.power_page.details, [snapshot])
        self.assertNotIn(monitor_wiring.DETAIL, self.app.busy)

    def test_results_from_before_a_page_change_are_dropped_and_resent(self):
        self.app.detail_gate.update(request_for({"memory"}))
        self.app.detail_received(self.ticket, self.finished())
        self.assertEqual(self.app.monitor_pages["cpu"].details, [])
        self.assertEqual(self.app.pool.submitted[0][1], (request_for({"memory"}),))

    def test_results_after_the_window_closed_are_dropped(self):
        self.app.detail_gate.update(None)
        self.app.detail_received(self.ticket, self.finished())
        self.assertEqual((self.app.monitor_pages["cpu"].details, self.app.pool.submitted), ([], []))

    def test_failure_is_reported_without_raising(self):
        with self.assertLogs(level="ERROR"):
            self.app.detail_received(self.ticket, self.finished(error=OSError("boom")))
        self.assertEqual(self.app.errors, {monitor_wiring.DETAIL: monitor_wiring.DETAIL_ERROR})
        self.app.detail_received(self.ticket, self.finished())
        self.assertEqual(self.app.errors, {})

    def test_nothing_happens_while_closing(self):
        self.app.closing = True
        self.app.detail_received(self.ticket, self.finished())
        self.assertEqual(self.app.monitor_pages["cpu"].details, [])
        self.app.submit_detail()
        self.assertEqual(self.app.pool.submitted, [])


class Overview(unittest.TestCase):
    def test_cards_open_their_pages(self):
        app = monitor()
        app.stack = FakeStack()
        self.assertTrue(app.open_from_overview(None, None, "memory"))
        self.assertEqual(app.stack.visible, "memory")
        self.assertEqual(monitor_wiring.OVERVIEW_TARGETS,
                         {"cpu": "cpu", "temperature": "cpu", "gpu": "cpu", "ram": "memory",
                          "network": "network", "ups": "power"})

    def test_system_data_reaches_the_pages(self):
        app = monitor()
        app.render_monitor_pages({"cpu": 1}, {"gpus": ()})
        self.assertEqual(app.monitor_pages["cpu"].systems, [({"cpu": 1}, {"gpus": ()})])


if __name__ == "__main__":
    unittest.main()
