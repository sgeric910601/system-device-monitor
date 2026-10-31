"""看板的補回接線（sdm.ui.backfill_wiring）：示範模式不讀 UPower、模擬測試只用注入的讀取器、
每記錄一筆就檢查空白、補回後重畫電源頁、結束時停止。

不建立真正的 GTK 視窗：沒有顯示器時建立元件會讓程序中止。
"""
import unittest
from unittest.mock import patch

from sdm.core.history import HistorySample
from sdm.services.backfill_service import BackfillService
from sdm.services.history_service import HistoryService
from sdm.services.upower_history import UpowerHistoryReader
from sdm.ui import app as ui_app
from sdm.ui import power_wiring

NOW = 1_800_000_000.0


class FakeBackfill:
    def __init__(self):
        self.checks, self.stops = 0, 0

    def check(self):
        self.checks += 1
        return False

    def stop(self):
        self.stops += 1


class FakePage:
    def __init__(self):
        self.changes = 0

    def history_changed(self):
        self.changes += 1


class FakeHistory:
    def __init__(self):
        self.added = []

    def add(self, sample):
        self.added.append(sample)


def monitor(preview=None, smoke=False):
    instance = ui_app.Monitor.__new__(ui_app.Monitor)
    instance.preview, instance.smoke = preview, smoke
    instance.closing, instance.power_page = False, None
    instance.history = HistoryService.in_memory(clock=lambda: NOW)
    return instance


class Creation(unittest.TestCase):
    def test_preview_never_reads_upower(self):
        app = monitor(preview="desktop")
        app.backfill_reader = FakeBackfill()
        app.start_backfill()
        self.assertIsNone(app.backfill)

    def test_smoke_test_only_with_an_injected_reader(self):
        app = monitor(smoke=True)
        app.start_backfill()
        self.assertIsNone(app.backfill)
        reader = FakeBackfill()
        app.backfill_reader = reader
        app.start_backfill()
        self.assertIsInstance(app.backfill, BackfillService)
        self.assertIs(app.backfill.reader, reader)

    def test_normal_run_reads_through_the_collector_bus(self):
        app = monitor()
        bus = object()
        app.collector = type("Collector", (), {"bus": bus})()
        app.start_backfill()
        self.assertIsInstance(app.backfill, BackfillService)
        self.assertIsInstance(app.backfill.reader, UpowerHistoryReader)
        self.assertIs(app.backfill.reader.connection(), bus)
        self.assertIs(app.backfill.history, app.history)

    def test_without_a_collector_there_is_no_bus(self):
        app = monitor()
        app.start_backfill()
        self.assertIsNone(app.backfill.reader.connection())

    def test_without_history_nothing_is_created(self):
        app = monitor()
        app.history = None
        app.start_backfill()
        self.assertIsNone(app.backfill)


class Wiring(unittest.TestCase):
    def setUp(self):
        self.app = monitor()
        self.app.history = FakeHistory()
        self.app.backfill = FakeBackfill()
        self.app.last_recorded = None
        self.power = None
        self.app.current_power = lambda: self.power

    def test_each_recorded_sample_checks_for_gaps(self):
        sample = HistorySample(NOW, 80, 10.0, False, False, None, None)
        with patch.object(power_wiring, "history_sample", lambda power: power):
            self.power = sample
            self.app.record_history()
            self.app.record_history()       # 同一筆不重複記錄，也不再檢查
        self.assertEqual(self.app.history.added, [sample])
        self.assertEqual(self.app.backfill.checks, 1)

    def test_nothing_to_record_checks_nothing(self):
        self.app.record_history()
        self.assertEqual(self.app.backfill.checks, 0)

    def test_closing_checks_nothing(self):
        self.app.closing = True
        self.app.check_backfill()
        self.assertEqual(self.app.backfill.checks, 0)

    def test_imported_points_redraw_the_power_page(self):
        self.app.power_page = FakePage()
        self.app.backfill_changed()
        self.assertEqual(self.app.power_page.changes, 1)
        self.app.closing = True
        self.app.backfill_changed()
        self.assertEqual(self.app.power_page.changes, 1)

    def test_stop_stops_the_backfill(self):
        self.app.stop_backfill()
        self.assertEqual(self.app.backfill.stops, 1)
        self.app.backfill = None
        self.app.stop_backfill()


class Wording(unittest.TestCase):
    def test_note_and_tooltip_say_filled_in_not_recharged(self):
        """今日電量回顧用「補回」表示充回來的電量；圖表的說明改用「補上」，避免混淆。"""
        from sdm.ui.charts import FILLED_TEXT, FILLED_TOOLTIP
        self.assertIn("系統紀錄補上", FILLED_TEXT)
        self.assertIn("補上", FILLED_TOOLTIP)
        for text in (FILLED_TEXT, FILLED_TOOLTIP):
            self.assertNotIn("補回", text)


if __name__ == "__main__":
    unittest.main()
