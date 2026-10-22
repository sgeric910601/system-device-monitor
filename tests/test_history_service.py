"""歷史檔案服務：載入、每 15 筆存檔、損壞時備份成新檔且絕不覆寫，全部在暫存目錄。"""
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from sdm import paths
from sdm.constants import History
from sdm.core.history import HistorySample, encode_history
from sdm.core.jsonfile import create_backup, write_bytes_atomic, write_exclusive
from sdm.services.history_service import HistoryService, default_path, read_boot_time

NOW = 1_800_000_000.0
LOGGER = "sdm.services.history_service"


def sample(t, watts=100.0):
    return HistorySample(t, None, watts, True, False, 100.0, True)


class Writer:
    """假的寫檔函式：只記錄呼叫次數與內容。"""
    def __init__(self):
        self.calls = []

    def __call__(self, path, data, **options):
        self.calls.append((path, data))


def file_mode(path):
    return stat.S_IMODE(path.stat().st_mode)


class TempData(unittest.TestCase):
    def setUp(self):
        previous = os.umask(0o022)
        self.addCleanup(os.umask, previous)
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
        def failing(path, data, **options):
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

    def test_invalid_rows_are_dropped_and_old_rows_trimmed(self):
        rows = [sample(NOW - 8 * 86400).to_row(), sample(NOW - 60).to_row(), [1, 2]]
        self.write_file(json.dumps({"format": 1, "samples": rows}).encode())
        with self.assertLogs(LOGGER, "WARNING"):
            service = self.service()
        self.assertEqual([s.t for s in service.samples], [NOW - 60])
        self.assertTrue(service.writable)
        self.assertEqual(self.backups(), [])


class ClockAnomalies(TempData):
    def write_rows(self, *times):
        self.write_file(encode_history(tuple(sample(t) for t in times)))

    def test_clock_behind_at_load_keeps_every_row(self):
        self.write_rows(NOW - 8 * 86400, NOW - 60, NOW + 3600)
        service = self.service()
        self.assertEqual([s.t for s in service.samples], [NOW - 8 * 86400, NOW - 60, NOW + 3600])
        service.add(sample(NOW))
        service.save()
        self.assertEqual(len(json.loads(self.path.read_text())["samples"]), 4)
        self.assertEqual(self.backups(), [])

    def test_forward_jump_at_load_backs_up_before_trimming(self):
        original = encode_history(tuple(sample(NOW - 10 * 86400 + i) for i in (0, 60)))
        self.write_file(original)
        with self.assertLogs(LOGGER, "WARNING"):
            service = self.service()
        self.assertEqual(service.samples, ())
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual((self.path.parent/self.backups()[0]).read_bytes(), original)

    def test_forward_jump_while_running_backs_up_once(self):
        self.write_rows(NOW - 120, NOW - 60)
        service = self.service()
        jumped = NOW + 10 * 86400
        self.clock = lambda: jumped
        service.clock = self.clock
        with self.assertLogs(LOGGER, "WARNING"):
            service.add(sample(jumped))
        self.assertEqual([s.t for s in service.samples], [jumped])
        (backup,) = self.backups()
        self.assertEqual(len(json.loads((self.path.parent/backup).read_text())["samples"]), 3)
        later = jumped + 10 * 86400
        service.clock = lambda: later
        service.add(sample(later))
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual([s.t for s in service.samples], [later])

    def test_ordinary_retention_does_not_back_up(self):
        self.write_rows(NOW - 7 * 86400 - 120, NOW - 7 * 86400 + 30, NOW - 3600, NOW - 60)
        service = self.service()
        self.assertEqual(len(service.samples), 3)
        self.clock = lambda: NOW + 60
        service.clock = self.clock
        service.add(sample(NOW + 60))
        self.assertEqual([s.t for s in service.samples], [NOW - 3600, NOW - 60, NOW + 60])
        self.assertEqual(self.backups(), [])

    def test_backup_failure_on_a_jump_keeps_everything_and_disables_writes(self):
        self.write_rows(NOW - 120, NOW - 60)

        def failing_backup(path, data, epoch, **options):
            raise PermissionError("read-only")
        service = self.service(backup=failing_backup)
        jumped = NOW + 10 * 86400
        service.clock = lambda: jumped
        with self.assertLogs(LOGGER, "WARNING"):
            service.add(sample(jumped))
        self.assertEqual(len(service.samples), 3)
        self.assertFalse(service.writable)

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

        def failing_backup(path, data, epoch, **options):
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


class SaveBackoff(TempData):
    """寫檔失敗後不洗版：只記一次警告，之後 15、30、60 分鐘重試，連續 5 次就放棄。"""
    def setUp(self):
        super().setUp()
        self.mono, self.attempts, self.fail = [0.0], [], [True]
        self.index = 0

    def writer(self, path, data, **options):
        self.attempts.append(self.mono[0])
        if self.fail[0]:
            raise OSError("disk full")
        path.write_bytes(data)

    def add(self, service, count=1):
        for _ in range(count):
            self.index += 1
            service.add(sample(NOW - 5000 + self.index))

    def backoff_service(self):
        return self.service(writer=self.writer, monotonic=lambda: self.mono[0])

    def test_schedule_and_give_up(self):
        service = self.backoff_service()
        with self.assertLogs(LOGGER, "DEBUG") as logs:
            self.add(service, 15)
            self.mono[0] = 15 * 60 - 1
            self.add(service)
            for minutes in (1 / 60, 30, 60, 60):
                self.mono[0] += minutes * 60
                self.add(service)
            self.mono[0] += 10 * 3600
            self.add(service)
            self.assertFalse(service.save(force=True))
        self.assertEqual(self.attempts, [0.0, 900.0, 2700.0, 6300.0, 9900.0])
        warnings = [r.getMessage() for r in logs.records if r.levelname == "WARNING"]
        debugs = [r.getMessage() for r in logs.records if r.levelname == "DEBUG"]
        self.assertEqual(len(warnings), 2)
        self.assertIn("giving up", warnings[1])
        self.assertEqual(len(debugs), 3)

    def test_success_resets_the_backoff(self):
        service = self.backoff_service()
        with self.assertLogs(LOGGER, "WARNING"):
            self.add(service, 15)
        self.fail[0] = False
        self.mono[0] = 15 * 60
        self.add(service)
        self.assertEqual(len(self.attempts), 2)
        self.assertEqual(service.unsaved, 0)
        self.add(service, 15)
        self.assertEqual(len(self.attempts), 3)

    def test_forced_save_on_exit_ignores_the_retry_window(self):
        service = self.backoff_service()
        with self.assertLogs(LOGGER, "WARNING"):
            self.add(service, 15)
        self.fail[0] = False
        self.assertFalse(service.save())
        self.assertTrue(service.save(force=True))
        self.assertEqual(len(self.attempts), 2)


class InMemory(TempData):
    """模擬測試用的歷史：可以新增與畫圖，但完全不讀寫檔案。"""
    def test_no_file_io(self):
        writer = Writer()
        self.write_file(b"real history must stay untouched")
        with patch.object(paths, "DATA", self.path.parent):
            service = HistoryService.in_memory(clock=self.clock, writer=writer)
            service.load()
            self.assertIsNone(service.path)
            for index in range(20):
                service.add(sample(NOW - 100 + index))
            self.assertFalse(service.save())
        self.assertEqual(writer.calls, [])
        self.assertEqual(len(service.samples), 20)
        self.assertEqual(len(service.points(3600)), 20)
        self.assertEqual(self.path.read_bytes(), b"real history must stay untouched")
        self.assertEqual(self.backups(), [])

    def test_forward_jump_trims_without_a_backup(self):
        service = HistoryService.in_memory(clock=self.clock)
        service.add(sample(NOW))
        jumped = NOW + 10 * 86400
        service.clock = lambda: jumped
        service.add(sample(jumped))
        self.assertEqual([s.t for s in service.samples], [jumped])


class Permissions(TempData):
    def test_writers_accept_a_mode(self):
        self.path.parent.mkdir(parents=True)
        write_exclusive(self.path.parent/"a", b"x", mode=0o600)
        write_bytes_atomic(self.path.parent/"b", b"x", mode=0o600)
        backup = create_backup(self.path.parent/"c.json", b"x", 1, mode=0o600)
        write_bytes_atomic(self.path.parent/"d", b"x")
        self.assertEqual([file_mode(self.path.parent/name) for name in ("a", "b")], [0o600] * 2)
        self.assertEqual(file_mode(backup), 0o600)
        self.assertEqual(file_mode(self.path.parent/"d"), 0o644)   # 預設仍依 umask

    def test_history_file_and_backups_are_owner_only(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"broken")
        with self.assertLogs(LOGGER, "WARNING"):
            service = self.service()
        service.add(sample(NOW))
        service.save()
        self.assertEqual(file_mode(self.path), 0o600)
        self.assertEqual([file_mode(self.path.parent/name) for name in self.backups()], [0o600])

    def test_created_data_directory_is_private_and_parents_untouched(self):
        parent = self.path.parent.parent
        parent.mkdir(parents=True, mode=0o755)
        os.chmod(parent, 0o755)
        service = self.service()
        service.add(sample(NOW))
        service.save()
        self.assertEqual(file_mode(self.path.parent), 0o700)
        self.assertEqual(file_mode(parent), 0o755)

    def test_existing_data_directory_keeps_its_mode(self):
        self.path.parent.mkdir(parents=True)
        os.chmod(self.path.parent, 0o755)
        service = self.service()
        service.add(sample(NOW))
        service.save()
        self.assertEqual(file_mode(self.path.parent), 0o755)


class Boot(unittest.TestCase):
    def test_reads_btime_from_a_stat_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".stat") as handle:
            handle.write("cpu 1 2 3\nbtime 1791279265\n")
            handle.flush()
            self.assertEqual(read_boot_time(handle.name), 1791279265.0)
        self.assertIsNone(read_boot_time("/nonexistent/stat"))


if __name__ == "__main__":
    unittest.main()
