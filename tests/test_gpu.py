"""顯示卡讀取：NVIDIA 常駐串流（以假執行檔測試）與 AMD 假 /sys 目錄。"""
import dataclasses
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import threading
import time
import unittest

from sdm.constants import Gpu
from sdm.data.gpu import (GpuReading, NvidiaStream, parse_nvidia_line, primary_gpu,
                          read_amd_gpus, terminate)

MIB = 1024 * 1024
ROWS = ("0, Example GPU A, 37, 1644, 24564, 34, 0, 61.50\n"
        "1, Example GPU B, [N/A], 200, 2048, [Not Supported], [N/A], [Not Supported]\n")


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class NvidiaLine(unittest.TestCase):
    def test_two_gpus_and_unavailable_values(self):
        first, second = (parse_nvidia_line(line, 7.0) for line in ROWS.splitlines())
        self.assertEqual(first, GpuReading(vendor="nvidia", index=0, name="Example GPU A",
                                           integrated=False, usage=37.0, vram_used=1644 * MIB,
                                           vram_total=24564 * MIB, temp_c=34.0, fan_pct=0.0,
                                           watts=61.5, stamp=7.0))
        self.assertEqual((second.index, second.usage, second.temp_c, second.fan_pct, second.watts),
                         (1, None, None, None, None))
        self.assertEqual(second.vram_total, 2048 * MIB)
        self.assertEqual(first.gpu_id, "gpu:nvidia:0")

    def test_malformed_lines_are_ignored(self):
        for line in ("", "garbage", "x, Example, 1, 2, 3, 4, 5, 6", "0, Example, 1, 2, 3",
                     "-1, Example, 1, 2, 3, 4, 5, 6", "0, , 1, 2, 3, 4, 5, 6"):
            with self.subTest(line=line):
                self.assertIsNone(parse_nvidia_line(line, 1.0))

    def test_out_of_range_values_become_none(self):
        reading = parse_nvidia_line("0, Example, 140, -5, 10, 400, 101, nan", 1.0)
        self.assertEqual((reading.usage, reading.vram_used, reading.temp_c, reading.fan_pct,
                          reading.watts), (None, None, None, None, None))
        self.assertEqual(reading.vram_total, 10 * MIB)

    def test_reading_is_frozen(self):
        reading = parse_nvidia_line(ROWS.splitlines()[0], 1.0)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            reading.watts = 1.0


class FakeExecutable(unittest.TestCase):
    """假的 nvidia-smi：寫在暫存目錄的腳本，忽略參數。"""
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def script(self, body):
        path = self.dir/"fake-smi"
        path.write_text("#!/usr/bin/python3 -B\nimport os, sys, time\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return str(path)

    def spawns(self):
        log = self.dir/"spawns"
        return log.read_text().count("\n") if log.exists() else 0

    def record_spawn(self):
        return (f"with open({str(self.dir/'spawns')!r}, 'a') as log:\n"
                "    log.write(str(os.getpid()) + '\\n')\n")


class Stream(FakeExecutable):
    def test_rows_are_read_and_exit_restarts_with_backoff(self):
        command = self.script(self.record_spawn() + f"sys.stdout.write({ROWS!r})\n")
        delays, finished = [], threading.Event()

        def wait(delay):
            delays.append(delay)
            if len(delays) >= 4:
                finished.set()
                return True
            return False
        stream = NvidiaStream(command=command, interval_ms=5000, wait=wait)
        stream.start()
        self.addCleanup(stream.stop)
        self.assertTrue(finished.wait(10))
        self.assertEqual(delays, [1, 2, 4, 8])
        self.assertEqual(self.spawns(), 4)
        readings = stream.readings()
        self.assertEqual([r.index for r in readings], [0, 1])
        self.assertEqual(readings[0].watts, 61.5)

    def test_backoff_is_capped(self):
        self.assertEqual([NvidiaStream.next_backoff(value) for value in (1, 32, 60)],
                         [2, Gpu.BACKOFF_MAX, Gpu.BACKOFF_MAX])

    def test_stop_terminates_the_child_without_zombies(self):
        command = self.script(self.record_spawn() + (
            "while True:\n"
            "    sys.stdout.write('0, Example GPU A, 1, 2, 3, 4, 5, 6.5\\n')\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(0.05)\n"))
        stream = NvidiaStream(command=command, interval_ms=5000)
        stream.start()
        self.assertTrue(wait_until(lambda: stream.readings()))
        self.assertTrue(stream.running())
        pid = int((self.dir/"spawns").read_text().split()[0])
        stream.stop()
        self.assertFalse(Path(f"/proc/{pid}").exists())
        self.assertFalse(stream.running())
        time.sleep(0.2)
        self.assertEqual(self.spawns(), 1)

    def test_child_gets_c_locale_and_no_stdin(self):
        out = self.dir/"env"
        command = self.script(
            f"open({str(out)!r}, 'w').write(os.environ.get('LC_ALL', '') + '|' + "
            "str(sys.stdin.read() if sys.stdin else 'closed'))\n")
        stream = NvidiaStream(command=command, interval_ms=5000, wait=lambda delay: True)
        stream.start()
        self.assertTrue(wait_until(lambda: out.exists() and out.read_text()))
        stream.stop()
        self.assertEqual(out.read_text(), "C|")

    def test_missing_executable_does_not_start(self):
        stream = NvidiaStream(command=str(self.dir/"missing"), interval_ms=5000)
        self.assertFalse(stream.start())
        self.assertFalse(stream.running())
        self.assertFalse(stream.pending())
        self.assertEqual(stream.readings(), ())
        stream.stop()

    def test_stale_readings_are_not_returned(self):
        now = [100.0]
        stream = NvidiaStream(command=str(self.dir/"missing"), interval_ms=5000,
                              clock=lambda: now[0])
        stream.accept_line(ROWS.splitlines()[0])
        self.assertEqual(len(stream.readings()), 1)
        now[0] += 15.0
        self.assertEqual(len(stream.readings()), 1)
        now[0] += 0.1
        self.assertEqual(stream.readings(), ())
        self.assertEqual(len(stream.latest()), 1)

    def test_pending_until_first_row(self):
        now = [10.0]
        command = self.script("time.sleep(5)\n")
        stream = NvidiaStream(command=command, interval_ms=5000, clock=lambda: now[0])
        self.assertTrue(stream.start())
        self.addCleanup(stream.stop)
        self.assertTrue(stream.pending())
        stream.accept_line(ROWS.splitlines()[0])
        self.assertFalse(stream.pending())


class HangingProcess:
    """不理會 SIGTERM、連 SIGKILL 後 wait() 也一直逾時的假程序。"""
    pid = 0

    def __init__(self):
        self.calls = []

    def terminate(self):
        self.calls.append("terminate")

    def kill(self):
        self.calls.append("kill")

    def wait(self, timeout=None):
        if timeout is None:
            raise AssertionError("wait() without a timeout would block forever")
        time.sleep(timeout)
        raise subprocess.TimeoutExpired("fake", timeout)


class BoundedStop(FakeExecutable):
    def test_terminate_gives_up_within_the_budget(self):
        process = HangingProcess()
        began = time.monotonic()
        with self.assertLogs("sdm.data.gpu", "WARNING"):
            self.assertFalse(terminate(process, 0.3))
        self.assertLess(time.monotonic() - began, 0.6)
        self.assertEqual(process.calls, ["terminate", "kill"])

    def test_stop_with_a_hung_process_and_thread_is_bounded(self):
        stream = NvidiaStream(command=str(self.dir/"missing"), interval_ms=5000)
        release = threading.Event()
        stream._process = HangingProcess()
        stream._thread = threading.Thread(target=release.wait, daemon=True)
        stream._thread.start()
        self.addCleanup(release.set)
        began = time.monotonic()
        with self.assertLogs("sdm.data.gpu", "WARNING"):
            stream.stop(timeout=0.4)
        self.assertLess(time.monotonic() - began, 0.8)
        self.assertFalse(stream.running())

    def test_child_ignoring_sigterm_is_killed_and_reaped(self):
        command = self.script(self.record_spawn() + (
            "import signal\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "while True:\n"
            "    sys.stdout.write('0, Example GPU A, 1, 2, 3, 4, 5, 6.5\\n')\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(0.05)\n"))
        stream = NvidiaStream(command=command, interval_ms=5000)
        stream.start()
        self.assertTrue(wait_until(lambda: stream.readings()))
        pid = int((self.dir/"spawns").read_text().split()[0])
        began = time.monotonic()
        stream.stop()
        self.assertLessEqual(time.monotonic() - began, Gpu.STOP_TIMEOUT + 0.5)
        self.assertFalse(Path(f"/proc/{pid}").exists())

    def test_start_after_a_timed_out_stop_is_refused_until_the_thread_ends(self):
        stream = NvidiaStream(command=self.script("time.sleep(5)\n"), interval_ms=5000)
        release = threading.Event()
        stream._run = release.wait          # 執行緒卡住，模擬停止逾時
        self.assertTrue(stream.start())
        with self.assertLogs("sdm.data.gpu", "WARNING"):
            stream.stop(timeout=0.1)
        self.assertFalse(stream.running())
        with self.assertLogs("sdm.data.gpu", "WARNING"):
            self.assertFalse(stream.start())
        release.set()
        self.assertTrue(wait_until(lambda: not stream._thread.is_alive()))
        del stream._run
        self.assertTrue(stream.start())
        self.assertTrue(stream.running())
        stream.stop()


class SilentFailures(FakeExecutable):
    def test_gives_up_after_repeated_exits_without_output(self):
        command = self.script(self.record_spawn() + "sys.exit(9)\n")
        delays = []

        def wait(delay):
            delays.append(delay)
            return False
        stream = NvidiaStream(command=command, interval_ms=5000, wait=wait)
        with self.assertLogs("sdm.data.gpu", "WARNING") as logs:
            stream.start()
            self.assertTrue(wait_until(lambda: not stream._thread.is_alive()))
        self.assertEqual(self.spawns(), Gpu.MAX_SILENT_FAILURES)
        self.assertEqual(delays, [1, 2, 4, 8])
        self.assertEqual(len(logs.output), 1)
        self.assertFalse(stream.running())
        self.assertFalse(stream.pending())
        stream.stop()


class IntervalChange(FakeExecutable):
    """更新頻率改變時：只有實際間隔改變才重新啟動，而且不等待重啟的退避時間。"""

    def looping(self):
        return self.script(
            f"with open({str(self.dir/'spawns')!r}, 'a') as log:\n"
            "    log.write(' '.join(sys.argv[1:]) + '\\n')\n"
            "while True:\n"
            "    sys.stdout.write('0, Example GPU A, 1, 2, 3, 4, 5, 6.5\\n')\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(0.05)\n")

    def arguments(self):
        return (self.dir/"spawns").read_text().splitlines()

    def test_new_interval_restarts_the_child_at_once(self):
        delays = []
        stream = NvidiaStream(command=self.looping(), interval_ms=2000,
                              wait=lambda delay: delays.append(delay) or False)
        stream.start()
        self.addCleanup(stream.stop)
        self.assertTrue(wait_until(lambda: stream.readings()))
        self.assertTrue(stream.set_interval_ms(10000))
        self.assertTrue(wait_until(lambda: self.spawns() == 2))
        self.assertTrue(self.arguments()[0].endswith("-lms 2000"))
        self.assertTrue(self.arguments()[1].endswith("-lms 10000"))
        self.assertEqual(delays, [])
        self.assertEqual(stream.stale_after, 10 * 3)
        self.assertTrue(stream.running())

    def test_same_interval_does_not_restart(self):
        stream = NvidiaStream(command=self.looping(), interval_ms=2000)
        stream.start()
        self.addCleanup(stream.stop)
        self.assertTrue(wait_until(lambda: stream.readings()))
        self.assertFalse(stream.set_interval_ms(2000))
        time.sleep(0.3)
        self.assertEqual(self.spawns(), 1)

    def test_change_while_the_child_is_being_started_is_not_lost(self):
        """間隔在組好命令列之後、程序登記之前改變：程序登記時發現不一致，立即換成新的間隔。"""
        delays = []

        class Racing(NvidiaStream):
            raced = False

            def command_line(self):
                line = super().command_line()
                if not Racing.raced:
                    Racing.raced = True
                    self.set_interval_ms(10000)     # 此時還沒有登記中的程序
                return line
        stream = Racing(command=self.looping(), interval_ms=2000,
                        wait=lambda delay: delays.append(delay) or False)
        stream.start()
        self.addCleanup(stream.stop)
        # 第一個程序在登記時就被結束，可能來不及寫下啟動紀錄；只確認最後跑的是新的間隔
        self.assertTrue(wait_until(lambda: self.spawns() and
                                   self.arguments()[-1].endswith("-lms 10000")))
        self.assertTrue(wait_until(lambda: stream.readings()))
        time.sleep(0.3)
        self.assertTrue(self.arguments()[-1].endswith("-lms 10000"))
        self.assertLessEqual(self.spawns(), 2)
        self.assertEqual(delays, [])

    def test_change_before_start_only_updates_the_arguments(self):
        stream = NvidiaStream(command=str(self.dir/"missing"), interval_ms=2000)
        self.assertTrue(stream.set_interval_ms(5000))
        self.assertEqual(stream.args()[-1], "5000")
        self.assertFalse(stream.running())


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class AmdSysfs(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.drm = self.root/"sys/class/drm"

    def card(self, name, vendor="0x1002", **files):
        device = self.drm/name/"device"
        write(device/"vendor", vendor + "\n")
        for key, value in files.items():
            write(device/key.replace("__", "/"), str(value) + "\n")
        return device

    def test_apu_with_ppt_is_integrated_and_keeps_raw_value_only(self):
        self.card("card1", gpu_busy_percent=3, mem_info_vram_used=26157056,
                  mem_info_vram_total=2147483648, hwmon__hwmon3__name="amdgpu",
                  hwmon__hwmon3__power1_label="PPT", hwmon__hwmon3__power1_input=20000,
                  hwmon__hwmon3__temp1_input=39000)
        (reading,) = read_amd_gpus(self.root, now=5.0)
        self.assertTrue(reading.integrated)
        self.assertIsNone(reading.watts)
        self.assertEqual(reading.ppt_raw, 20000)
        self.assertEqual((reading.usage, reading.temp_c, reading.vram_used),
                         (3.0, 39.0, 26157056))
        self.assertEqual((reading.vendor, reading.index, reading.stamp), ("amd", 0, 5.0))

    def test_discrete_gpu_power_average_in_microwatts(self):
        self.card("card0", mem_info_vram_total=17163091968, mem_info_vram_used=1073741824,
                  hwmon__hwmon1__power1_average=150000000, hwmon__hwmon1__power1_input=1,
                  hwmon__hwmon1__pwm1=128, hwmon__hwmon1__pwm1_max=255)
        (reading,) = read_amd_gpus(self.root, now=1.0)
        self.assertFalse(reading.integrated)
        self.assertEqual(reading.watts, 150.0)
        self.assertIsNone(reading.ppt_raw)
        self.assertAlmostEqual(reading.fan_pct, 50.2, places=1)

    def test_discrete_gpu_falls_back_to_power_input(self):
        self.card("card0", mem_info_vram_total=8589934592, hwmon__hwmon1__power1_input=95500000)
        self.assertEqual(read_amd_gpus(self.root, now=1.0)[0].watts, 95.5)

    def test_small_vram_without_label_is_integrated(self):
        self.card("card0", mem_info_vram_total=536870912, hwmon__hwmon1__power1_input=9000000)
        (reading,) = read_amd_gpus(self.root, now=1.0)
        self.assertTrue(reading.integrated)
        self.assertIsNone(reading.watts)
        self.assertEqual(reading.ppt_raw, 9000000)

    def test_missing_files_give_none_fields(self):
        self.card("card0")
        (reading,) = read_amd_gpus(self.root, now=1.0)
        fields = (reading.usage, reading.vram_used, reading.vram_total, reading.temp_c,
                  reading.fan_pct, reading.watts, reading.ppt_raw)
        self.assertEqual(fields, (None,) * 7)
        self.assertFalse(reading.integrated)

    def test_connectors_and_other_vendors_are_skipped(self):
        self.card("card2", vendor="0x10de", mem_info_vram_total=1)
        self.card("card1-DP-1", vendor="0x1002")
        self.card("card1", gpu_busy_percent=200, mem_info_vram_total="nan")
        (reading,) = read_amd_gpus(self.root, now=1.0)
        self.assertIsNone(reading.usage)
        self.assertIsNone(reading.vram_total)

    def test_missing_tree_gives_nothing(self):
        self.assertEqual(read_amd_gpus(self.root/"absent", now=1.0), ())


class Primary(unittest.TestCase):
    def reading(self, vendor, index, integrated=False):
        return GpuReading(vendor=vendor, index=index, name="Example", integrated=integrated)

    def test_first_nvidia_then_amd_discrete(self):
        nvidia1, nvidia0 = self.reading("nvidia", 1), self.reading("nvidia", 0)
        apu, dgpu = self.reading("amd", 0, True), self.reading("amd", 1)
        self.assertIs(primary_gpu((apu, nvidia1, nvidia0, dgpu)), nvidia0)
        self.assertIs(primary_gpu((apu, dgpu)), dgpu)
        self.assertIsNone(primary_gpu((apu,)))
        self.assertIsNone(primary_gpu(()))


if __name__ == "__main__":
    unittest.main()
