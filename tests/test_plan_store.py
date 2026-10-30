"""智慧充電的排程檔與紀錄檔（sdm.core.plan_format、sdm.services.plan_store）。

排程檔 charging-plan.json 在 XDG 資料目錄：原子寫入、0600（資料夾 0700）；無法解讀時先另存
新的備份（絕不覆寫），來自較新版本時這次執行不寫入。紀錄檔 charging-plan.log 在 XDG 狀態目錄：
每行一筆、超過 256 KB 時只留後半。全部在暫存目錄進行。
"""
from dataclasses import replace
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
import unittest.mock

from sdm.constants import SmartCharging
from sdm.core import plan_format
from sdm.core.charging_plan import PRECISE_WAKE, READINESS_FIRST, Once, Weekly, make_plan
from sdm.services.plan_store import MemoryLog, PlanLog, PlanStore

NOW = 1_792_000_000.0
ONCE = replace(make_plan(Once(NOW + 3600), NOW + 3600, 100, 80, 90, READINESS_FIRST, NOW,
                         "0123456789abcdef0123456789abcdef"),
               completed=NOW + 10, applied=80, started_for=NOW + 3600, restoring=True)
WEEKLY = make_plan(Weekly(frozenset({1, 2, 3, 4, 5}), 480), NOW + 7200, 95, 85, 80,
                   PRECISE_WAKE, NOW, "fedcba9876543210fedcba9876543210")


def mode(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


class Format(unittest.TestCase):
    def test_round_trip(self):
        for plan in (ONCE, WEEKLY, None):
            with self.subTest(plan=plan):
                self.assertEqual(plan_format.decode_plan(plan_format.encode_plan(plan)), plan)

    def test_file_text(self):
        value = json.loads(plan_format.encode_plan(WEEKLY))
        self.assertEqual(value["format"], SmartCharging.PLAN_FORMAT)
        self.assertEqual(value["plan"]["schedule"], {"kind": "weekly", "weekdays": [1, 2, 3, 4, 5],
                                                     "minute": 480})
        self.assertEqual(value["plan"]["strategy"], "precise_wake")

    def corrupt(self, change):
        value = json.loads(plan_format.encode_plan(ONCE))
        change(value)
        return json.dumps(value).encode()

    def test_invalid_values_are_undecodable(self):
        changes = (
            lambda v: v["plan"].update(target=120), lambda v: v["plan"].update(target=True),
            lambda v: v["plan"].update(hold=95, target=90), lambda v: v["plan"].update(restore=49),
            lambda v: v["plan"].update(strategy="sometimes"),
            lambda v: v["plan"].update(deadline="soon"),
            lambda v: v["plan"].update(deadline=float("inf")),
            lambda v: v["plan"].update(applied=101), lambda v: v["plan"].update(restoring=1),
            lambda v: v["plan"].update(id="../x"), lambda v: v["plan"].pop("created"),
            lambda v: v["plan"].update(extra=1),
            lambda v: v["plan"].update(schedule={"kind": "weekly", "weekdays": [9], "minute": 1}),
            lambda v: v["plan"].update(schedule={"kind": "weekly", "weekdays": [1],
                                                 "minute": 1440}),
            lambda v: v["plan"].update(schedule={"kind": "weekly", "weekdays": [True],
                                                 "minute": 1}),
            lambda v: v["plan"].update(schedule={"kind": "monthly"}),
            lambda v: v.update(format=0), lambda v: v.update(format="1"),
            lambda v: v.update(plan=[]), lambda v: v.pop("plan"))
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                with self.assertRaises(plan_format.Undecodable):
                    plan_format.decode_plan(self.corrupt(change))
        for data in (b"", b"not json", b"[]", b"[" * 100000 + b"]" * 100000, b"\xff"):
            with self.assertRaises(plan_format.Undecodable):
                plan_format.decode_plan(data)

    def test_newer_format(self):
        with self.assertRaises(plan_format.NewerFormat):
            plan_format.decode_plan(json.dumps({"format": 2, "plan": None}).encode())


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.path = self.base/"data/system-device-monitor"/SmartCharging.PLAN_FILE
        self.now = NOW

    def store(self, **options):
        return PlanStore(self.path, clock=lambda: self.now, **options)

    def files(self):
        return sorted(path.name for path in self.path.parent.iterdir())


class Store(Base):
    def test_missing_file_is_no_plan(self):
        store = self.store()
        self.assertIsNone(store.load())
        self.assertFalse(self.path.parent.exists())

    def test_save_and_load_with_private_modes_whatever_the_umask(self):
        previous = os.umask(0)
        try:
            self.store().save(WEEKLY)
        finally:
            os.umask(previous)
        self.assertEqual((mode(self.path), mode(self.path.parent)), (0o600, 0o700))
        self.assertEqual(self.store().load(), WEEKLY)

    def test_cleared_plan_is_saved_as_null(self):
        store = self.store()
        store.save(ONCE)
        store.save(None)
        self.assertEqual(json.loads(self.path.read_text())["plan"], None)
        self.assertIsNone(self.store().load())

    def test_existing_directory_keeps_its_mode(self):
        self.path.parent.mkdir(parents=True)
        os.chmod(self.path.parent, 0o750)
        self.store().save(ONCE)
        self.assertEqual(mode(self.path.parent), 0o750)

    def test_corrupt_file_is_backed_up_before_anything_is_written(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"{broken")
        store = self.store()
        with self.assertLogs("sdm.services.plan_store", "WARNING"):
            self.assertIsNone(store.load())
        backup = self.path.parent/f"charging-plan-{int(NOW)}.json.bak"
        self.assertEqual(backup.read_bytes(), b"{broken")
        self.assertEqual(mode(backup), 0o600)
        self.assertEqual(self.path.read_bytes(), b"{broken")      # 原檔留到下次存檔
        store.save(ONCE)
        self.assertEqual(self.store().load(), ONCE)
        self.assertEqual(backup.read_bytes(), b"{broken")

    def test_existing_backup_names_are_never_overwritten(self):
        self.path.parent.mkdir(parents=True)
        taken = self.path.parent/f"charging-plan-{int(NOW)}.json.bak"
        taken.write_bytes(b"older")
        self.path.write_bytes(b"{broken")
        with self.assertLogs("sdm.services.plan_store", "WARNING"):
            self.store().load()
        self.assertEqual(taken.read_bytes(), b"older")
        self.assertEqual((self.path.parent/f"charging-plan-{int(NOW)}-1.json.bak").read_bytes(),
                         b"{broken")

    def test_failed_backup_disables_writing(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"{broken")

        def refuse(*_args, **_kwargs):
            raise OSError("read-only")
        store = self.store(backup=refuse)
        with self.assertLogs("sdm.services.plan_store", "WARNING"):
            self.assertIsNone(store.load())
        store.save(ONCE)
        self.assertEqual(self.path.read_bytes(), b"{broken")

    def test_newer_format_is_never_overwritten(self):
        self.path.parent.mkdir(parents=True)
        data = json.dumps({"format": 9, "plan": {"future": True}}).encode()
        self.path.write_bytes(data)
        store = self.store()
        with self.assertLogs("sdm.services.plan_store", "WARNING"):
            self.assertIsNone(store.load())
        store.save(ONCE)
        self.assertEqual(self.path.read_bytes(), data)
        self.assertEqual(self.files(), [SmartCharging.PLAN_FILE])

    def test_oversized_file_is_copied_aside(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b" " * (SmartCharging.PLAN_MAX_BYTES + 1))
        with self.assertLogs("sdm.services.plan_store", "WARNING"):
            self.assertIsNone(self.store().load())
        self.assertIn(f"charging-plan-{int(NOW)}.json.bak", self.files())

    def test_symlinked_or_special_plan_file_is_not_followed(self):
        """排程檔是符號連結或 FIFO 時不讀（不跟隨、不卡住），這次執行也不寫入（不取代它）。"""
        self.path.parent.mkdir(parents=True)
        target = self.base/"elsewhere.json"
        target.write_bytes(plan_format.encode_plan(ONCE))
        self.path.symlink_to(target)
        store = self.store()
        with self.assertLogs("sdm.services.plan_store", "WARNING"):
            self.assertIsNone(store.load())
        store.save(WEEKLY)
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(plan_format.decode_plan(target.read_bytes()), ONCE)
        self.path.unlink()
        os.mkfifo(self.path)
        with self.assertLogs("sdm.services.plan_store", "WARNING"):
            self.assertIsNone(self.store().load())

    def test_file_that_grows_after_the_size_check_is_not_read_past_the_limit(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(plan_format.encode_plan(ONCE))
        store = self.store()
        real = os.fstat

        def growing(fd):
            info = real(fd)
            with open(self.path, "ab") as handle:
                handle.write(b" " * (SmartCharging.PLAN_MAX_BYTES + 1))
            return info
        with unittest.mock.patch("os.fstat", growing), \
                self.assertLogs("sdm.services.plan_store", "WARNING"):
            self.assertIsNone(store.load())

    def test_memory_store_never_touches_the_disk(self):
        store = PlanStore.in_memory(WEEKLY)
        self.assertEqual(store.load(), WEEKLY)
        store.save(ONCE)
        self.assertEqual(store.load(), ONCE)
        self.assertFalse(self.path.parent.exists())

    def test_write_failures_are_reported_once_and_retried(self):
        calls = []

        def failing(path, data, mode):
            calls.append(mode)
            raise OSError("disk full")
        store = self.store(writer=failing)
        with self.assertLogs("sdm.services.plan_store", "WARNING") as logs:
            store.save(ONCE)
            store.save(WEEKLY)
        self.assertEqual(calls, [0o600, 0o600])
        self.assertEqual(len(logs.output), 1)


class Log(Base):
    def setUp(self):
        super().setUp()
        self.log_path = self.base/"state/system-device-monitor"/SmartCharging.LOG_FILE

    def log(self, **options):
        return PlanLog(self.log_path, clock=lambda: self.now, **options)

    def test_lines_have_a_timestamp_and_private_modes(self):
        previous = os.umask(0)
        try:
            log = self.log()
            log.write("建立排程")
            log.write("狀態：等待開始")
        finally:
            os.umask(previous)
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertRegex(lines[0], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4} 建立排程$")
        self.assertEqual((mode(self.log_path), mode(self.log_path.parent)), (0o600, 0o700))
        self.assertEqual(log.tail(1), [lines[1]])

    def test_tail_returns_the_last_twenty_lines(self):
        log = self.log()
        for index in range(30):
            log.write(f"第 {index} 筆")
        tail = log.tail(SmartCharging.LOG_TAIL_LINES)
        self.assertEqual(len(tail), 20)
        self.assertTrue(tail[0].endswith("第 10 筆") and tail[-1].endswith("第 29 筆"))
        self.assertEqual(PlanLog(self.base/"missing.log").tail(5), [])

    def test_trimmed_to_its_second_half_above_256_kb(self):
        self.log_path.parent.mkdir(parents=True)
        line = "x" * 99 + "\n"
        self.log_path.write_text(line * (SmartCharging.LOG_MAX_BYTES // len(line) + 2))
        log = self.log()
        log.write("新的一筆")
        data = self.log_path.read_bytes()
        self.assertLessEqual(len(data), SmartCharging.LOG_MAX_BYTES // 2 + 200)
        self.assertTrue(data.startswith(b"x" * 99 + b"\n"))     # 從完整的一行開始
        self.assertTrue(data.decode().endswith(" 新的一筆\n"))
        self.assertEqual(mode(self.log_path), 0o600)

    def test_huge_log_keeps_its_end(self):
        """紀錄檔被別的程式寫得很大時，修剪與最後幾行都取檔案的結尾，不是開頭。"""
        self.log_path.parent.mkdir(parents=True)
        line = "舊" * 30 + "\n"
        with open(self.log_path, "w", encoding="utf-8") as handle:
            for _ in range(4 * SmartCharging.LOG_MAX_BYTES // len(line.encode())):
                handle.write(line)
            handle.write("最後一筆舊的\n")
        log = self.log()
        self.assertEqual(log.tail(1), ["最後一筆舊的"])
        log.write("新的一筆")
        text = self.log_path.read_text(encoding="utf-8")
        self.assertLessEqual(len(text.encode()), SmartCharging.LOG_MAX_BYTES // 2 + 200)
        self.assertTrue(text.endswith("最後一筆舊的\n" + text.splitlines()[-1] + "\n"))
        self.assertTrue(text.splitlines()[-1].endswith(" 新的一筆"))
        self.assertTrue(text.startswith("舊"))

    def test_small_log_is_not_trimmed(self):
        log = self.log()
        log.write("一")
        size = self.log_path.stat().st_size
        log.write("二")
        self.assertGreater(self.log_path.stat().st_size, size)

    def test_newlines_in_messages_stay_on_one_line(self):
        log = self.log()
        log.write("第一行\n第二行")
        self.assertEqual(len(self.log_path.read_text().splitlines()), 1)

    def test_symlinked_log_is_not_followed(self):
        self.log_path.parent.mkdir(parents=True)
        target = self.base/"elsewhere"
        target.write_text("keep\n")
        self.log_path.symlink_to(target)
        with self.assertLogs("sdm.services.plan_store", "WARNING"):
            self.log().write("新的一筆")
        self.assertEqual(target.read_text(), "keep\n")

    def test_memory_log(self):
        log = MemoryLog(clock=lambda: self.now)
        log.write("一")
        log.write("二")
        self.assertEqual([line.split(" ", 1)[1] for line in log.tail(5)], ["一", "二"])


if __name__ == "__main__":
    unittest.main()
