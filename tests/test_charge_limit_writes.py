"""充電上限小程式的寫入：寫入順序、讀回確認、保留（狀態檔與單元）、重新套用與狀態查詢。

假驅動要求起點小於上限（與 ThinkPad 等相同），寫入順序錯了就會以 EINVAL 拒絕；
ASUS 型的假驅動只接受 60／80／100，其他值改成最接近的一個。
"""
import fcntl
import json
import os

from sdm.constants import ChargeLimit
from tests.fixtures.charge_root import (DEB_STATE, DEB_UNIT, END, ENV, LOCAL_STATE, LOCAL_UNIT,
                                        LOCK, START, UNIT_ENV, Base, Driver, helper)


class Ordering(Base):
    def test_lower_end_below_the_current_start_writes_the_start_first(self):
        self.battery(end=80, start=75)
        self.assertEqual(self.main(["set", "BAT0", "60"]), helper.EXIT_OK)
        self.assertEqual(self.driver.writes, [(START, 55), (END, 60)])
        self.assertEqual((self.value("BAT0", END), self.value("BAT0", START)), (60, 55))
        self.assertEqual(self.output, [{"ok": True, "action": "set", "supply": "BAT0",
                                        "end": 60, "start": 55, "persist": "unchanged"}])

    def test_end_equal_to_the_current_start_also_lowers_the_start_first(self):
        self.battery(end=85, start=80)
        self.assertEqual(self.main(["set", "BAT0", "80"]), helper.EXIT_OK)
        self.assertEqual(self.driver.writes, [(START, 75), (END, 80)])

    def test_raising_writes_the_end_first(self):
        self.battery(end=60, start=55)
        self.assertEqual(self.main(["set", "BAT0", "90"]), helper.EXIT_OK)
        self.assertEqual(self.driver.writes, [(END, 90)])

    def test_start_is_left_alone_when_not_in_the_way(self):
        self.battery(end=80, start=40)
        self.assertEqual(self.main(["set", "BAT0", "90"]), helper.EXIT_OK)
        self.assertEqual(self.driver.writes, [(END, 90)])
        self.assertEqual(self.value("BAT0", START), 40)

    def test_driver_without_a_start_file(self):
        self.battery("BAT1", end=80, start=None)
        self.assertEqual(self.main(["set", "BAT1", "60"]), helper.EXIT_OK)
        self.assertEqual(self.driver.writes, [(END, 60)])
        self.assertIsNone(self.output[0]["start"])

    def test_values_already_in_place_are_not_written_again(self):
        self.battery(end=80, start=75)
        self.assertEqual(self.main(["set", "BAT0", "80"]), helper.EXIT_OK)
        self.assertEqual(self.driver.writes, [])
        self.assertEqual(self.sleeps, [])

    def test_unreadable_current_start_writes_the_end_first(self):
        directory = self.battery(end=80, start=75)
        (directory/START).write_text("garbage\n")
        self.driver.ordered = False
        self.assertEqual(self.main(["set", "BAT0", "60"]), helper.EXIT_OK)
        self.assertEqual(self.driver.writes, [(END, 60)])


class ReadBack(Base):
    def test_driver_rounding_is_reported_with_the_applied_value(self):
        self.driver = Driver(steps=(60, 80, 100))
        self.battery("BAT1", end=100, start=None)
        self.assertEqual(self.main(["set", "BAT1", "85"]), helper.EXIT_FAILED)
        self.assertEqual(self.output, [{"ok": False, "error": "not-applied", "applied": 80,
                                        "applied_start": None}])
        self.assertEqual(self.sleeps, [ChargeLimit.READBACK_INTERVAL] * 2)
        self.assertEqual(self.logged, ["set not-applied"])

    def test_rejected_write_reports_what_is_in_place(self):
        self.driver = Driver(refuse=(85,))
        self.battery(end=80, start=75)
        self.assertEqual(self.main(["set", "BAT0", "85"]), helper.EXIT_FAILED)
        self.assertEqual(self.output, [{"ok": False, "error": "not-applied", "applied": 80,
                                        "applied_start": 75}])

    def test_slow_driver_is_confirmed_on_a_later_read(self):
        directory = self.battery("BAT1", end=80, start=None)

        def slow(fd, data):
            return len(data)                      # 驅動晚一點才更新

        def later(seconds):
            self.sleeps.append(seconds)
            (directory/END).write_text("90\n")
        self.driver = slow
        context = self.context()._replace(write=slow, sleep=later)
        code = helper.main(["set", "BAT1", "90"], dict(ENV), context,
                           emit=self.output.append, report=self.logged.append)
        self.assertEqual(code, helper.EXIT_OK)
        self.assertEqual(self.sleeps, [ChargeLimit.READBACK_INTERVAL])

    def test_short_write_is_a_failure(self):
        self.battery(end=80, start=75)
        context = self.context()._replace(write=lambda fd, data: len(data) - 1)
        code = helper.main(["set", "BAT0", "85"], dict(ENV), context,
                           emit=self.output.append, report=self.logged.append)
        self.assertEqual(code, helper.EXIT_FAILED)
        self.assertEqual(self.output[0]["error"], "not-applied")

    def test_written_text_is_plain_decimal(self):
        seen = []
        self.battery("BAT1", end=80, start=None)

        def record(fd, data):
            seen.append(data)
            return self.driver(fd, data)
        context = self.context()._replace(write=record)
        helper.main(["set", "BAT1", "100"], dict(ENV), context, emit=self.output.append,
                    report=self.logged.append)
        self.assertEqual(seen, [b"100"])



class Persist(Base):
    def setUp(self):
        super().setUp()
        self.battery(end=80, start=75)

    def state(self, relative=DEB_STATE):
        return json.loads((self.root.path/relative).read_text())

    def test_persist_on_stores_the_value_and_enables_the_unit(self):
        self.assertEqual(self.main(["set", "BAT0", "85", "--persist", "on"]), helper.EXIT_OK)
        self.assertEqual(self.state(), {"format": 1, "supply": "BAT0", "end": 85})
        self.assertEqual(self.root.mode(DEB_STATE), 0o644)
        self.assertEqual(self.root.mode(os.path.dirname(DEB_STATE)), 0o755)
        self.assertEqual(self.systemctl_calls(), [("enable", DEB_UNIT)])
        self.assertEqual(self.output[0]["persist"], "on")
        leftovers = os.listdir(self.root.path/os.path.dirname(DEB_STATE))
        self.assertEqual(leftovers, ["charge-limit.json"])

    def test_state_modes_do_not_depend_on_the_umask(self):
        previous = os.umask(0o077)
        try:
            self.assertEqual(self.main(["set", "BAT0", "85", "--persist", "on"]), helper.EXIT_OK)
        finally:
            os.umask(previous)
        self.assertEqual(self.root.mode(DEB_STATE), 0o644)
        self.assertEqual(self.root.mode(os.path.dirname(DEB_STATE)), 0o755)

    def test_automatic_start_is_not_stored(self):
        self.main(["set", "BAT0", "60", "--persist", "on"])
        self.assertEqual(self.value("BAT0", START), 55)
        self.assertEqual(self.state(), {"format": 1, "supply": "BAT0", "end": 60})

    def test_persist_off_removes_the_state_and_disables_the_unit(self):
        self.main(["set", "BAT0", "85", "--persist", "on"])
        self.runner.calls.clear()
        self.assertEqual(self.main(["set", "BAT0", "85", "--persist", "off"]), helper.EXIT_OK)
        self.assertFalse((self.root.path/DEB_STATE).exists())
        self.assertFalse((self.root.path/os.path.dirname(DEB_STATE)).exists())
        self.assertEqual(self.systemctl_calls(), [("disable", DEB_UNIT)])

    def test_persist_off_keeps_other_files_in_the_state_directory(self):
        self.main(["set", "BAT0", "85", "--persist", "on"])
        other = self.root.file(os.path.dirname(DEB_STATE) + "/other.json", "{}\n")
        self.main(["set", "BAT0", "85", "--persist", "off"])
        self.assertTrue(other.exists())

    def test_persist_off_without_state_only_checks_the_unit(self):
        self.assertEqual(self.main(["set", "BAT0", "85", "--persist", "off"]), helper.EXIT_OK)
        self.assertEqual(self.systemctl_calls(), [("is-enabled", "--quiet", DEB_UNIT)])
        self.runner.enabled = True
        self.runner.calls.clear()
        self.main(["set", "BAT0", "85", "--persist", "off"])
        self.assertEqual(self.systemctl_calls(), [("is-enabled", "--quiet", DEB_UNIT),
                                                  ("disable", DEB_UNIT)])

    def test_without_the_option_persistence_is_unchanged(self):
        self.main(["set", "BAT0", "85", "--persist", "on"])
        self.runner.calls.clear()
        self.assertEqual(self.main(["set", "BAT0", "90"]), helper.EXIT_OK)
        self.assertEqual(self.state()["end"], 85)
        self.assertEqual(self.runner.calls, [])

    def test_failed_write_never_touches_persistence(self):
        self.driver.refuse = {90}
        self.assertEqual(self.main(["set", "BAT0", "90", "--persist", "on"]), helper.EXIT_FAILED)
        self.assertFalse((self.root.path/DEB_STATE).exists())
        self.assertEqual(self.runner.calls, [])

    def test_failed_enable_is_reported_after_applying(self):
        self.runner.failing = {"enable"}
        self.assertEqual(self.main(["set", "BAT0", "85", "--persist", "on"]), helper.EXIT_FAILED)
        self.assertEqual(self.output, [{"ok": False, "error": "persist-failed", "applied": 85,
                                        "applied_start": 75}])
        self.assertEqual(self.value("BAT0", END), 85)

    def test_local_layout_uses_its_own_unit_and_state(self):
        self.helper_path = ChargeLimit.LOCAL_HELPER
        self.assertEqual(self.main(["set", "BAT0", "85", "--persist", "on"]), helper.EXIT_OK)
        self.assertEqual(self.state(LOCAL_STATE)["end"], 85)
        self.assertFalse((self.root.path/DEB_STATE).exists())
        self.assertEqual(self.systemctl_calls(), [("enable", LOCAL_UNIT)])

    def test_symlinked_state_directory_is_refused(self):
        elsewhere = self.root.directory("elsewhere")
        (self.root.path/"var/lib/system-device-monitor").symlink_to(elsewhere)
        self.assertEqual(self.main(["set", "BAT0", "85", "--persist", "on"]), helper.EXIT_FAILED)
        self.assertEqual(os.listdir(elsewhere), [])
        self.assertEqual(self.runner.calls, [])

    def test_symlinked_state_file_is_not_replaced(self):
        target = self.root.file("elsewhere/secret", "keep\n")
        self.root.directory("var/lib/system-device-monitor")
        (self.root.path/DEB_STATE).symlink_to(target)
        self.assertEqual(self.main(["set", "BAT0", "85", "--persist", "on"]), helper.EXIT_FAILED)
        self.assertEqual(target.read_text(), "keep\n")
        self.assertTrue(os.path.islink(self.root.path/DEB_STATE))

    def test_untrusted_var_lib_is_refused(self):
        os.chmod(self.root.path/"var/lib", 0o775)
        self.assertEqual(self.main(["set", "BAT0", "85", "--persist", "on"]), helper.EXIT_FAILED)
        self.assertFalse((self.root.path/"var/lib/system-device-monitor").exists())

    def test_missing_var_lib_is_not_created(self):
        os.rmdir(self.root.path/"var/lib")
        self.assertEqual(self.main(["set", "BAT0", "85", "--persist", "on"]), helper.EXIT_FAILED)
        self.assertFalse((self.root.path/"var/lib").exists())


class Restore(Base):
    def write_state(self, value, relative=DEB_STATE, mode=0o644):
        text = value if isinstance(value, str) else json.dumps(value)
        return self.root.file(relative, text, mode=mode)

    def test_reapplies_the_persisted_value(self):
        self.battery(end=100, start=95)
        self.write_state({"format": 1, "supply": "BAT0", "end": 80})
        self.assertEqual(self.restore(), helper.EXIT_OK)
        self.assertEqual(self.output, [{"ok": True, "action": "restore", "restored": True}])
        self.assertEqual(self.driver.writes, [(START, 75), (END, 80)])
        self.assertEqual(self.logged, ["restore ok"])
        self.assertEqual(self.runner.calls, [])

    def test_is_idempotent(self):
        self.battery(end=100, start=95)
        self.write_state({"format": 1, "supply": "BAT0", "end": 80})
        self.restore()
        self.driver.writes.clear()
        self.assertEqual(self.restore(), helper.EXIT_OK)
        self.assertEqual(self.driver.writes, [])

    def test_nothing_persisted(self):
        self.battery()
        self.assertEqual(self.restore(), helper.EXIT_OK)
        self.assertEqual(self.output, [{"ok": True, "action": "restore", "restored": False}])
        self.assertEqual(self.driver.writes, [])

    def test_battery_that_is_gone_is_skipped(self):
        self.battery("BAT1")
        self.write_state({"format": 1, "supply": "BAT0", "end": 80})
        self.assertEqual(self.restore(), helper.EXIT_OK)
        self.assertEqual(self.output[0]["restored"], False)

    def test_local_layout_reads_its_own_state(self):
        self.helper_path = ChargeLimit.LOCAL_HELPER
        self.battery(end=100, start=None)
        self.write_state({"format": 1, "supply": "BAT0", "end": 80})
        self.assertEqual(self.restore(), helper.EXIT_OK)
        self.assertEqual(self.output[0]["restored"], False)
        self.write_state({"format": 1, "supply": "BAT0", "end": 85}, LOCAL_STATE)
        self.restore()
        self.assertEqual(self.value("BAT0", END), 85)

    def test_invalid_state_is_refused(self):
        self.battery(end=100, start=None)
        good = {"format": 1, "supply": "BAT0", "end": 80}
        cases = ("not json", "[]", json.dumps(dict(good, format=2)),
                 json.dumps(dict(good, end=101)), json.dumps(dict(good, end="80")),
                 json.dumps(dict(good, end=True)), json.dumps(dict(good, start=None)),
                 json.dumps(dict(good, start=70)), json.dumps(dict(good, supply="../BAT0")),
                 json.dumps(dict(good, extra=1)), json.dumps({"format": 1}),
                 "{" * 5000)
        for text in cases:
            with self.subTest(text=text[:40]):
                self.output.clear()
                self.write_state(text)
                self.assertEqual(self.restore(), helper.EXIT_FAILED)
                self.assertEqual(self.output, [{"ok": False, "error": "invalid-state"}])
        self.assertEqual(self.driver.writes, [])

    def test_untrusted_state_file_is_refused(self):
        self.battery(end=100, start=None)
        good = {"format": 1, "supply": "BAT0", "end": 80}
        path = self.write_state(good, mode=0o664)
        self.assertEqual(self.restore(), helper.EXIT_FAILED)
        path.unlink()
        target = self.root.file("elsewhere/state.json", json.dumps(good))
        path.symlink_to(target)
        self.assertEqual(self.restore(), helper.EXIT_FAILED)
        self.assertEqual(self.driver.writes, [])


class Status(Base):
    def test_reports_batteries_persistence_and_the_unit(self):
        self.battery("BAT0", end=80, start=75)
        self.battery("CMB0", end=None, start=None, device="platform/CMB0")
        self.battery("hidpp_battery_0", scope="Device", device="usb/hidpp")
        self.root.file(DEB_STATE, json.dumps({"format": 1, "supply": "BAT0", "end": 80}))
        self.runner.enabled = True
        self.assertEqual(self.main(["status"]), helper.EXIT_OK)
        self.assertEqual(self.output, [{
            "ok": True, "action": "status", "batteries": [{"supply": "BAT0", "end": 80,
                                                           "start": 75}],
            "persist": {"supply": "BAT0", "end": 80}, "enabled": True}])
        self.assertEqual(self.systemctl_calls(), [("is-enabled", "--quiet", DEB_UNIT)])

    def test_invalid_state_is_reported_as_unknown(self):
        self.battery()
        self.root.file(DEB_STATE, "garbage")
        self.assertEqual(self.main(["status"]), helper.EXIT_OK)
        self.assertEqual(self.output[0]["persist"], "invalid")

    def test_odd_names_in_the_class_directory_are_skipped(self):
        self.battery()
        (self.root.path/"sys/class/power_supply/bad name").mkdir()
        self.assertEqual(self.main(["status"]), helper.EXIT_OK)
        self.assertEqual([item["supply"] for item in self.output[0]["batteries"]], ["BAT0"])


class Serialized(Base):
    """同一時間只有一個小程式在讀寫門檻：root 專用的鎖檔，等待最多 10 秒。"""

    def setUp(self):
        super().setUp()
        self.battery(end=80, start=75)

    def hold_lock(self):
        path = self.root.path/LOCK
        self.root.directory(os.path.dirname(LOCK))
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        os.chmod(path, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        self.addCleanup(os.close, fd)
        return fd

    def test_lock_file_is_private_and_released_after_each_run(self):
        previous = os.umask(0o077)
        try:
            self.assertEqual(self.main(["set", "BAT0", "85"]), helper.EXIT_OK)
        finally:
            os.umask(previous)
        self.assertEqual(self.root.mode(LOCK), 0o600)
        self.assertEqual(self.root.mode(os.path.dirname(LOCK)), 0o755)
        self.assertEqual(self.main(["set", "BAT0", "90"]), helper.EXIT_OK)
        self.assertEqual(self.sleeps, [])

    def test_waits_for_another_run_then_proceeds(self):
        fd = self.hold_lock()

        def release():
            if len(self.sleeps) == 10:          # 另一個小程式在 1 秒後結束
                fcntl.flock(fd, fcntl.LOCK_UN)
        self.on_sleep.append(release)
        self.assertEqual(self.main(["set", "BAT0", "85"]), helper.EXIT_OK)
        self.assertEqual(self.sleeps, [ChargeLimit.LOCK_POLL] * 10)
        self.assertEqual(self.value("BAT0", END), 85)

    def test_gives_up_after_ten_seconds(self):
        self.hold_lock()
        runs = ((lambda: self.main(["set", "BAT0", "85"])), (lambda: self.main(["status"])),
                (lambda: self.restore()))
        for run in runs:
            with self.subTest(run=run):
                self.output.clear()
                started = self.now
                self.assertEqual(run(), helper.EXIT_FAILED)
                self.assertEqual(self.output, [{"ok": False, "error": "busy"}])
                self.assertGreaterEqual(self.now - started, ChargeLimit.LOCK_WAIT)
                self.assertLess(self.now - started, ChargeLimit.LOCK_WAIT + 1)
        self.assertEqual(self.driver.writes, [])
        self.assertEqual(self.logged, ["set busy", "status busy", "restore busy"])

    def test_interleaved_runs_are_serialized(self):
        inner, driver = [], self.driver

        def nested(fd, data):
            if not inner:
                inner.append(helper.main(["set", "BAT0", "90"], dict(ENV), self.context(),
                                         emit=self.output.append, report=self.logged.append))
            return driver(fd, data)
        context = self.context()._replace(write=nested)
        code = helper.main(["set", "BAT0", "85"], dict(ENV), context, emit=self.output.append,
                           report=self.logged.append)
        self.assertEqual((code, inner), (helper.EXIT_OK, [helper.EXIT_FAILED]))
        self.assertEqual(self.output[0], {"ok": False, "error": "busy"})
        self.assertEqual(self.driver.writes, [(END, 85)])
        self.assertEqual(self.value("BAT0", END), 85)

    def test_untrusted_lock_files_are_refused(self):
        lock = self.root.path/LOCK
        self.root.directory(os.path.dirname(LOCK))
        cases = (lambda: (lock.write_text(""), lock.chmod(0o644)),
                 lambda: lock.symlink_to(self.root.file("elsewhere/lock", "")),
                 lambda: os.chmod(lock.parent, 0o775))
        for prepare in cases:
            with self.subTest(prepare=prepare):
                for path in (lock,):
                    if os.path.lexists(path):
                        os.unlink(path)
                os.chmod(lock.parent, 0o755)
                prepare()
                self.assertEqual(self.main(["set", "BAT0", "85"]), helper.EXIT_FAILED)
        self.assertEqual(self.driver.writes, [])
