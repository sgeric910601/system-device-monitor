"""歷史檔案服務：載入、每 15 筆存檔、損壞時備份成新檔且絕不覆寫，全部在暫存目錄。"""
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from sdm import paths
from sdm.constants import History
from sdm.core.history import HistorySample
from sdm.core.jsonfile import write_bytes_atomic
from sdm.services.history_service import HistoryService, default_path, read_boot_time

NOW = 1_800_000_000.0
LOGGER = "sdm.services.history_service"


def sample(t, watts=100.0):
    return HistorySample(t, None, watts, True, False, 100.0, True)


class Writer:
    """假的寫檔函式：只記錄呼叫次數與內容。"""
    def __init__(self):
        self.calls = []

    def __call__(self, path, data):
        self.calls.append((path, data))


class TempData(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.path = self.base/"data/system-device-monitor/history.json"
        self.clock = lambda: NOW

    def service(self, **options):
        service = HistoryService(self.path, clock=self.clock, **options)
        service.load()
        return service

    def write_file(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(data)

    def backups(self):
        return sorted(p.name for p in self.path.parent.glob("*.bak"))


class Saving(TempData):
    def test_default_path_is_in_the_xdg_data_directory(self):
        with patch.object(paths, "DATA", self.base/"xdg"):
            self.assertEqual(default_path(), self.base/"xdg"/History.FILE_NAME)

    def test_saves_every_fifteen_samples(self):
        writer = Writer()
        service = self.service(writer=writer)
        for index in range(14):
            self.assertFalse(service.add(sample(NOW - 100 + index)))
        self.assertEqual(writer.calls, [])
        self.assertTrue(service.add(sample(NOW - 50)))
        self.assertEqual(len(writer.calls), 1)
        for index in range(15):
            service.add(sample(NOW - 40 + index))
        self.assertEqual(len(writer.calls), 2)
        self.assertEqual(len(json.loads(writer.calls[-1][1])["samples"]), 30)

    def test_save_on_stop_only_with_unsaved_samples(self):
        writer = Writer()
        service = self.service(writer=writer)
        self.assertFalse(service.save())
        service.add(sample(NOW))
        self.assertTrue(service.save())
        self.assertFalse(service.save())
        self.assertEqual(len(writer.calls), 1)

    def test_history_survives_a_restart(self):
        service = self.service()
        service.add(sample(NOW - 60))
        service.add(sample(NOW, watts=288.0))
        service.save()
        data = json.loads(self.path.read_text())
        self.assertEqual(data, {"format": 1, "samples": [
            [NOW - 60, None, 100.0, True, False, 100.0, True],
            [NOW, None, 288.0, True, False, 100.0, True]]})
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), History.DIRECTORY_MODE)
        self.assertEqual(self.service().samples, service.samples)

    def test_add_trims_to_seven_days_and_keeps_order(self):
        service = self.service(writer=Writer())
        service.add(sample(NOW - 8 * 86400))
        service.add(sample(NOW - 60))
        service.add(sample(NOW - 120))
        self.assertEqual([s.t for s in service.samples], [NOW - 120, NOW - 60])

    def test_invalid_sample_is_ignored(self):
        service = self.service(writer=Writer())
        self.assertFalse(service.add(sample(NOW, watts=float("nan"))))
        self.assertEqual(service.samples, ())

    def test_write_failure_keeps_samples_for_the_next_try(self):
        def failing(path, data):
            raise OSError("disk full")
        service = self.service(writer=failing)
        with self.assertLogs(LOGGER, "WARNING"):
            for index in range(15):
                service.add(sample(NOW - 100 + index))
        self.assertEqual(len(service.samples), 15)
        self.assertTrue(service.writable)


class Loading(TempData):
    def test_missing_file_starts_empty_without_creating_anything(self):
        service = self.service()
        self.assertEqual(service.samples, ())
        self.assertTrue(service.writable)
        self.assertFalse(self.path.parent.exists())

    def test_invalid_and_future_rows_are_dropped_and_old_rows_trimmed(self):
        rows = [sample(NOW - 8 * 86400).to_row(), sample(NOW - 60).to_row(), [1, 2],
                sample(NOW + 301).to_row(), sample(NOW + 300).to_row()]
        self.write_file(json.dumps({"format": 1, "samples": rows}).encode())
        with self.assertLogs(LOGGER, "WARNING"):
            service = self.service()
        self.assertEqual([s.t for s in service.samples], [NOW - 60, NOW + 300])
        self.assertTrue(service.writable)

    def test_corrupt_file_is_copied_to_a_backup_and_replaced_on_next_save(self):
        corrupt = b"{\"format\": 1, \"samples\": [[1,"
        self.write_file(corrupt)
        with self.assertLogs(LOGGER, "WARNING"):
            service = self.service()
        self.assertEqual(service.samples, ())
        self.assertEqual(self.backups(), [f"history-{int(NOW)}.json.bak"])
        self.assertEqual((self.path.parent/self.backups()[0]).read_bytes(), corrupt)
        self.assertEqual(self.path.read_bytes(), corrupt)  # 下次存檔前原檔不動
        service.add(sample(NOW))
        service.save()
        self.assertEqual(len(json.loads(self.path.read_text())["samples"]), 1)

    def test_second_corruption_gets_a_distinct_backup(self):
        self.write_file(b"first broken")
        with self.assertLogs(LOGGER, "WARNING"):
            self.service()
        self.write_file(b"second broken")
        with self.assertLogs(LOGGER, "WARNING"):
            self.service()
        self.assertEqual(self.backups(), [f"history-{int(NOW)}-1.json.bak",
                                          f"history-{int(NOW)}.json.bak"])
        contents = {(self.path.parent/name).read_bytes() for name in self.backups()}
        self.assertEqual(contents, {b"first broken", b"second broken"})

    def test_backup_failure_disables_writes(self):
        writer = Writer()
        self.write_file(b"broken")

        def failing_backup(path, data, epoch):
            raise PermissionError("read-only")
        with self.assertLogs(LOGGER, "WARNING") as logs:
            service = self.service(writer=writer, backup=failing_backup)
        self.assertFalse(service.writable)
        self.assertTrue(any("disabled" in line for line in logs.output))
        for index in range(20):
            service.add(sample(NOW - 100 + index))
        self.assertFalse(service.save())
        self.assertEqual(writer.calls, [])
        self.assertEqual(self.path.read_bytes(), b"broken")

    def test_unreadable_file_disables_writes(self):
        self.path.mkdir(parents=True)
        with self.assertLogs(LOGGER, "WARNING"):
            service = self.service(writer=Writer())
        self.assertFalse(service.writable)

    def test_newer_format_disables_writes_without_backup(self):
        self.write_file(b'{"format": 2, "samples": []}')
        with self.assertLogs(LOGGER, "WARNING"):
            service = self.service(writer=Writer())
        self.assertFalse(service.writable)
        self.assertEqual(self.backups(), [])

    def test_points_and_last_unplugged_use_the_loaded_samples(self):
        service = self.service(writer=Writer())
        service.add(sample(NOW - 60))
        service.add(sample(NOW))
        self.assertEqual(len(service.points(3600)), 2)
        self.assertIsNone(service.last_unplugged(boot_time=None))


class AtomicBytes(TempData):
    def test_exact_bytes_and_no_temporary_left(self):
        write_bytes_atomic(self.path, b"first\n")
        write_bytes_atomic(self.path, b"second\n")
        self.assertEqual(self.path.read_bytes(), b"second\n")
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["history.json"])

    def test_failed_replace_keeps_the_original(self):
        write_bytes_atomic(self.path, b"kept\n")
        with patch("sdm.core.jsonfile.os.replace", side_effect=OSError("cross-device")):
            with self.assertRaises(OSError):
                write_bytes_atomic(self.path, b"lost\n")
        self.assertEqual(self.path.read_bytes(), b"kept\n")
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["history.json"])


class Boot(unittest.TestCase):
    def test_reads_btime_from_a_stat_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".stat") as handle:
            handle.write("cpu 1 2 3\nbtime 1791279265\n")
            handle.flush()
            self.assertEqual(read_boot_time(handle.name), 1791279265.0)
        self.assertIsNone(read_boot_time("/nonexistent/stat"))


if __name__ == "__main__":
    unittest.main()
