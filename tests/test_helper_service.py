"""CPU 功耗取樣服務（helper/sdm-cpu-power-service）：能量差、每個連線的回應與啟動檢查。

不需要 root：能量計數器用暫存檔（pread 一樣可用），監聽 socket 用暫存目錄裡的 Unix socket，
放棄權限的系統呼叫以假的物件記錄。通訊協定的整合測試見 test_helper_protocol。
"""
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest

from tests.fixtures.helpers import load_helper

service = load_helper("sdm-cpu-power-service")
J = 1_000_000                       # 1 焦耳＝10⁶ 微焦耳
BIG_RANGE = 65_532_610_987          # 實機常見的 max_energy_range_uj（約 65.5 kJ）
RANGES = service.Ranges(BIG_RANGE, BIG_RANGE)


def sample(stamp, package, core=None):
    return service.Sample(stamp, package, core)


def decode(payload):
    return json.loads(payload.decode("ascii"))


class Measure(unittest.TestCase):
    """移植參考專案 EnergyDelta 的案例，加上 RAPL 的計數上限與四捨五入。"""

    def test_two_joules_over_one_second_is_two_watts(self):
        self.assertEqual(service.measure(sample(1, 10 * J), sample(2, 12 * J), RANGES),
                         (2.0, None, 1.0))

    def test_same_timestamp_gives_nothing(self):
        self.assertIsNone(service.measure(sample(1, 10 * J), sample(1, 12 * J), RANGES))

    def test_earlier_timestamp_gives_nothing(self):
        self.assertIsNone(service.measure(sample(2, 10 * J), sample(1, 12 * J), RANGES))

    def test_counter_rollback_is_rejected(self):
        self.assertIsNone(service.measure(sample(1, 10 * J), sample(2, 2 * J), RANGES))

    def test_wrap_uses_the_counter_range(self):
        before = sample(1, BIG_RANGE - J, BIG_RANGE - J // 2)
        after = sample(2, J, J // 2)
        self.assertEqual(service.measure(before, after, RANGES), (2.0, 1.0, 1.0))

    def test_wrap_without_a_known_range_is_rejected(self):
        before, after = sample(1, BIG_RANGE - J), sample(2, J)
        self.assertIsNone(service.measure(before, after, service.Ranges(None, None)))
        self.assertIsNone(service.measure(before, after, service.Ranges(BIG_RANGE - 2 * J, None)))

    def test_values_are_rounded_to_a_tenth_of_a_watt(self):
        result = service.measure(sample(10, 0, 0), sample(12, 5_260_000, 1_340_000), RANGES)
        self.assertEqual(result, (2.6, 0.7, 2.0))

    def test_interval_is_rounded(self):
        self.assertEqual(service.measure(sample(1.0, 0), sample(2.3456, 2 * J), RANGES)[2], 1.35)

    def test_core_above_package_is_dropped(self):
        self.assertEqual(service.measure(sample(1, 0, 0), sample(2, 2 * J, 3 * J), RANGES),
                         (2.0, None, 1.0))

    def test_missing_core_keeps_the_package(self):
        self.assertEqual(service.measure(sample(1, 0, None), sample(2, 3 * J, 5), RANGES),
                         (3.0, None, 1.0))

    def test_missing_package_gives_nothing(self):
        self.assertIsNone(service.measure(sample(1, None, 0), sample(2, None, J), RANGES))

    def test_window_longer_than_the_limit_is_rejected(self):
        limit = service.MAX_WINDOW_S
        self.assertIsNotNone(service.measure(sample(0, 0), sample(limit, J), RANGES))
        self.assertIsNone(service.measure(sample(0, 0), sample(limit + 0.01, J), RANGES))

    def test_implausible_watts_are_rejected(self):
        self.assertIsNone(service.measure(sample(0, 0), sample(1, service.MAX_WATTS * J), RANGES))
        self.assertEqual(service.measure(sample(0, 0), sample(1, 999 * J), RANGES)[0], 999.0)

    def test_unchanged_counter_is_zero_watts(self):
        self.assertEqual(service.measure(sample(1, 5 * J, J), sample(2, 5 * J, J), RANGES),
                         (0.0, 0.0, 1.0))


class Counters(unittest.TestCase):
    def test_parse_counter_accepts_only_plain_decimal(self):
        cases = {b"123\n": 123, b"0": 0, b"65532610987\n": 65532610987, b"": None, b"\n": None,
                 b"-1": None, b"12a": None, b"1.5": None, b" 7 ": 7, b"\xff": None,
                 b"1" * 21: None}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(service.parse_counter(raw), expected)

    def test_pread_sees_new_values_on_the_same_descriptor(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"energy_uj"
            path.write_text("100\n")
            fd = os.open(path, os.O_RDONLY)
            self.addCleanup(os.close, fd)
            self.assertEqual(service.read_counter(fd), 100)
            path.write_text("250\n")
            self.assertEqual(service.read_counter(fd), 250)

    def test_closed_descriptor_reads_nothing(self):
        fd = os.open(os.devnull, os.O_RDONLY)
        os.close(fd)
        self.assertIsNone(service.read_counter(fd))


class Zones(unittest.TestCase):
    """假的 /sys/class/powercap：package 用固定的 intel-rapl:0，core 必須是唯一的子區域。"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)

    def zone(self, name, label, energy="1000\n", max_range="5000\n"):
        directory = self.base/name
        directory.mkdir()
        (directory/"name").write_text(label + "\n")
        if energy is not None:
            (directory/"energy_uj").write_text(energy)
        if max_range is not None:
            (directory/"max_energy_range_uj").write_text(max_range)
        return directory

    def opened(self):
        channels = service.open_channels(str(self.base))
        for channel in channels:
            if channel is not None:
                self.addCleanup(os.close, channel.fd)
        return channels

    def test_package_and_core(self):
        self.zone("intel-rapl:0", "package-0")
        self.zone("intel-rapl:0:0", "core", energy="40\n", max_range="900\n")
        self.zone("intel-rapl:0:1", "uncore")
        package, core = self.opened()
        self.assertEqual((service.read_counter(package.fd), package.max_range), (1000, 5000))
        self.assertEqual((service.read_counter(core.fd), core.max_range), (40, 900))

    def test_wrong_package_name_gives_no_channels(self):
        self.zone("intel-rapl:0", "psys")
        self.zone("intel-rapl:0:0", "core")
        self.assertEqual(self.opened(), (None, None))

    def test_duplicate_core_is_rejected(self):
        self.zone("intel-rapl:0", "package-0")
        self.zone("intel-rapl:0:0", "core")
        self.zone("intel-rapl:0:1", "core")
        package, core = self.opened()
        self.assertIsNotNone(package)
        self.assertIsNone(core)

    def test_other_packages_and_unrelated_entries_are_ignored(self):
        self.zone("intel-rapl:0", "package-0")
        self.zone("intel-rapl:1:0", "core")
        self.zone("intel-rapl-mmio:0", "package-0")
        (self.base/"intel-rapl:0:x").mkdir()
        package, core = self.opened()
        self.assertIsNotNone(package)
        self.assertIsNone(core)

    def test_unreadable_range_keeps_the_channel_without_wrap(self):
        self.zone("intel-rapl:0", "package-0", max_range="bogus\n")
        package, _ = self.opened()
        self.assertEqual((service.read_counter(package.fd), package.max_range), (1000, None))

    def test_missing_energy_file_gives_no_channel(self):
        self.zone("intel-rapl:0", "package-0", energy=None)
        self.assertEqual(self.opened(), (None, None))

    def test_energy_symlink_is_not_followed(self):
        directory = self.zone("intel-rapl:0", "package-0", energy=None)
        (self.base/"elsewhere").write_text("7\n")
        (directory/"energy_uj").symlink_to(self.base/"elsewhere")
        self.assertEqual(self.opened(), (None, None))

    def test_missing_powercap_gives_no_channels(self):
        self.assertEqual(service.open_channels(str(self.base/"absent")), (None, None))


class FakeSampler:
    """依序回傳樣本；記錄被讀了幾次（客戶端的請求不應該觸發讀取）。"""

    def __init__(self, *samples):
        self.samples, self.calls = list(samples), 0

    def __call__(self):
        self.calls += 1
        return self.samples.pop(0)


class Windows(unittest.TestCase):
    """全部連線共用的固定 1 秒刻度（start + k 秒）；回覆一律是最近一個完成的視窗。"""

    def sampler(self, *samples, start=100.0):
        self.take = FakeSampler(*samples)
        return service.WindowSampler(self.take, RANGES, start, period=1.0)

    def test_first_tick_only_sets_the_baseline(self):
        windows = self.sampler(sample(100, 10 * J, 4 * J))
        windows.advance(100.0)
        self.assertEqual(decode(windows.reply), {"v": 1, "status": "sampling", "package_w": None,
                                                 "core_w": None, "interval_s": None})
        self.assertEqual(windows.next_tick, 101.0)

    def test_completed_window_is_reported(self):
        windows = self.sampler(sample(100, 10 * J, 4 * J), sample(101, 12 * J, 5 * J))
        windows.advance(100.0)
        windows.advance(101.0)
        self.assertEqual(decode(windows.reply), {"v": 1, "status": "ok", "package_w": 2.0,
                                                 "core_w": 1.0, "interval_s": 1.0})

    def test_nothing_is_read_between_ticks(self):
        windows = self.sampler(sample(100, 10 * J), sample(101, 12 * J))
        for now in (100.0, 100.2, 100.5, 100.999):
            windows.advance(now)
        self.assertEqual(self.take.calls, 1)
        first = windows.reply
        windows.advance(101.0)
        self.assertNotEqual(windows.reply, first)
        for now in (101.1, 101.6):
            windows.advance(now)
            self.assertEqual(decode(windows.reply)["package_w"], 2.0)
        self.assertEqual(self.take.calls, 2)

    def test_ticks_stay_on_the_grid(self):
        windows = self.sampler(sample(100, 0), sample(103.7, 27 * J), sample(104.0, 30 * J))
        windows.advance(100.0)
        windows.advance(103.7)
        self.assertEqual(windows.next_tick, 104.0)
        self.assertEqual(decode(windows.reply)["interval_s"], 3.7)
        windows.advance(104.0)
        self.assertEqual(decode(windows.reply)["package_w"], 10.0)

    def test_timeout_until_the_next_tick(self):
        windows = self.sampler(sample(100, 0))
        windows.advance(100.0)
        self.assertAlmostEqual(windows.timeout(100.25), 0.75)
        self.assertEqual(windows.timeout(101.5), 0.0)

    def test_unreadable_package_is_an_error_until_two_good_ticks(self):
        windows = self.sampler(sample(100, 10 * J), sample(101, None), sample(102, 12 * J),
                               sample(103, 13 * J))
        windows.advance(100.0)
        windows.advance(101.0)
        self.assertEqual(decode(windows.reply)["status"], "error")
        windows.advance(102.0)
        self.assertEqual(decode(windows.reply)["status"], "sampling")
        windows.advance(103.0)
        self.assertEqual(decode(windows.reply)["package_w"], 1.0)

    def test_rollback_reports_sampling(self):
        windows = self.sampler(sample(100, 10 * J), sample(101, 2 * J), sample(102, 4 * J))
        windows.advance(100.0)
        windows.advance(101.0)
        self.assertEqual(decode(windows.reply)["status"], "sampling")
        windows.advance(102.0)
        self.assertEqual(decode(windows.reply)["package_w"], 2.0)

    def test_reply_is_one_compact_json_line(self):
        payload = service.reply("ok", 12.3, 4.5, 2.0)
        self.assertEqual(payload, b'{"v":1,"status":"ok","package_w":12.3,"core_w":4.5,'
                                  b'"interval_s":2.0}\n')


class Accepts(unittest.TestCase):
    def test_at_most_four_accepts_per_second(self):
        limiter = service.AcceptLimiter(4, 1.0)
        for now in (10.0, 10.1, 10.2, 10.3):
            self.assertTrue(limiter.allowed(now))
            limiter.record(now)
        self.assertFalse(limiter.allowed(10.5))
        self.assertAlmostEqual(limiter.wait(10.5), 0.5)
        self.assertTrue(limiter.allowed(11.0))
        self.assertEqual(limiter.wait(11.0), 0.0)


class Shared(unittest.TestCase):
    """假時鐘驅動伺服器：兩個連線在同一個視窗內拿到相同的回覆，請求不會多讀計數器。"""

    def setUp(self):
        self.now = [100.0]
        self.take = FakeSampler(sample(100, 10 * J), sample(101, 12 * J), sample(102, 15 * J))
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        self.server = service.Server(listener, self.take, RANGES, os.getuid(),
                                     clock=lambda: self.now[0])
        self.ends = []
        for _ in range(2):
            ours, theirs = socket.socketpair()
            self.addCleanup(ours.close)
            self.addCleanup(theirs.close)
            theirs.setblocking(False)
            self.server.clients.append(service.Client(theirs, self.now[0]))
            self.ends.append(ours)

    def ask(self, index):
        self.ends[index].sendall(b"r\n")
        self.server.handle(self.server.clients[index])
        return self.ends[index].recv(256)

    def test_clients_share_the_last_completed_window(self):
        self.server.sampler.advance(100.0)
        self.now[0] = 101.2
        first, second = self.ask(0), self.ask(1)
        self.assertEqual(first, second)
        self.assertEqual(decode(first)["package_w"], 2.0)
        self.now[0] = 101.9
        self.assertEqual(self.ask(0), first)
        self.assertEqual(self.take.calls, 2)
        self.now[0] = 102.0
        self.assertEqual(decode(self.ask(1))["package_w"], 3.0)
        self.assertEqual(self.take.calls, 3)


class Sampler(unittest.TestCase):
    def test_reads_both_channels_with_one_stamp(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = [Path(temp)/name for name in ("package", "core")]
            for path, value in zip(paths, ("500\n", "70\n")):
                path.write_text(value)
            fds = [os.open(path, os.O_RDONLY) for path in paths]
            for fd in fds:
                self.addCleanup(os.close, fd)
            channels = (service.Channel(fds[0], None), service.Channel(fds[1], None))
            take = service.Sampler(channels, clock=lambda: 42.0)
            self.assertEqual(take(), sample(42.0, 500, 70))
            self.assertEqual(service.Sampler((None, None), clock=lambda: 1.0)(),
                             sample(1.0, None, None))


class Requests(unittest.TestCase):
    def test_only_a_single_r_line_is_a_request(self):
        cases = {b"r\n": service.COMPLETE, b"r": service.INCOMPLETE, b"": service.INCOMPLETE,
                 b"x\n": service.INVALID, b"r\nr\n": service.INVALID, b"rr": service.INVALID,
                 b"R\n": service.INVALID, b"r\r\n": service.INVALID, b"\n": service.INVALID,
                 b"r\n\x00": service.INVALID}
        for buffer, expected in cases.items():
            with self.subTest(buffer=buffer):
                self.assertEqual(service.parse_request(buffer), expected)


class Listener(unittest.TestCase):
    """systemd 交來的監聽 socket：LISTEN_PID、LISTEN_FDS 與 fd 本身都要符合。"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = os.path.join(temp.name, "cpu.sock")

    def listening(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(self.path)
        sock.listen()
        return sock

    def env(self, **extra):
        return {"LISTEN_PID": "4321", "LISTEN_FDS": "1", **extra}

    def check(self, sock, env, pid=4321):
        fd = os.dup(sock.fileno())
        sock.close()
        try:
            return service.inherited_listener(env, pid, fd)
        except service.Failure:
            os.close(fd)
            raise

    def test_valid_listener_is_accepted_and_the_environment_cleared(self):
        env = self.env(LISTEN_FDNAMES="cpu")
        listener = self.check(self.listening(), env)
        self.addCleanup(listener.close)
        self.assertEqual((listener.family, listener.type), (socket.AF_UNIX, socket.SOCK_STREAM))
        self.assertEqual(env, {})
        self.assertFalse(os.get_inheritable(listener.fileno()))

    def test_wrong_pid_or_count_is_refused(self):
        for env, pid in ((self.env(), 1), (self.env(LISTEN_FDS="2"), 4321),
                         ({"LISTEN_FDS": "1"}, 4321), ({}, 4321)):
            with self.subTest(env=env, pid=pid):
                with self.assertRaises(service.Failure):
                    self.check(self.listening(), env, pid)
                os.unlink(self.path)

    def test_non_listening_or_datagram_sockets_are_refused(self):
        stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with self.assertRaises(service.Failure):
            self.check(stream, self.env())
        datagram = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        datagram.bind(self.path)
        with self.assertRaises(service.Failure):
            self.check(datagram, self.env())

    def test_regular_file_descriptor_is_refused(self):
        fd = os.open(os.devnull, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        with self.assertRaises(service.Failure):
            service.inherited_listener(self.env(), 4321, fd)

    def test_socket_owner_comes_from_the_socket_file(self):
        listener = self.listening()
        self.addCleanup(listener.close)
        self.assertEqual(service.socket_owner(listener), os.getuid())


class FakeSystem:
    """記錄放棄權限時的系統呼叫；setuid(0) 預設會失敗（代表能力已經消失）。"""

    def __init__(self, regain=False, groups_after=(), resuid=None):
        self.calls, self.regain = [], regain
        self.groups, self.groups_after, self.resuid = [27], list(groups_after), resuid

    def setgroups(self, groups):
        self.calls.append(("setgroups", tuple(groups)))
        self.groups = list(self.groups_after)

    def setresgid(self, *ids):
        self.calls.append(("setresgid", ids))

    def setresuid(self, *ids):
        self.calls.append(("setresuid", ids))
        self.resuid = self.resuid or ids

    def getresuid(self):
        return self.resuid

    def getresgid(self):
        return (65534,) * 3

    def getgroups(self):
        return self.groups

    def setuid(self, uid):
        self.calls.append(("setuid", uid))
        if not self.regain:
            raise PermissionError("not permitted")


STATUS = "Name:\tpython3\nCapInh:\t0000000000000000\nCapPrm:\t{0}\nCapEff:\t{0}\n"


class Privileges(unittest.TestCase):
    def test_capabilities_are_parsed_from_proc_status(self):
        self.assertEqual(service.capabilities(STATUS.format("0000000000000000")), 0)
        self.assertEqual(service.capabilities(STATUS.format("00000000000000c4")), 0xc4)
        self.assertIsNone(service.capabilities("Name:\tx\n"))
        self.assertIsNone(service.capabilities("CapPrm:\t0\nCapEff:\tzz\n"))

    def test_drop_order_and_checks(self):
        system = FakeSystem()
        service.drop_privileges(system, lambda: STATUS.format("0" * 16))
        self.assertEqual(system.calls, [("setgroups", ()), ("setresgid", (65534,) * 3),
                                        ("setresuid", (65534,) * 3), ("setuid", 0)])

    def test_failed_drops_are_fatal(self):
        cases = (
            (FakeSystem(regain=True), STATUS.format("0" * 16)),
            (FakeSystem(), STATUS.format("0000000000000004")),
            (FakeSystem(), "Name:\tx\n"),
            (FakeSystem(resuid=(65534, 0, 65534)), STATUS.format("0" * 16)),
            (FakeSystem(groups_after=(4,)), STATUS.format("0" * 16)),
        )
        for system, status in cases:
            with self.subTest(status=status, calls=system.calls):
                with self.assertRaises(service.Failure):
                    service.drop_privileges(system, lambda status=status: status)

    def test_unreadable_status_is_fatal(self):
        def unreadable():
            raise OSError("no proc")
        with self.assertRaises(service.Failure):
            service.drop_privileges(FakeSystem(), unreadable)


class Main(unittest.TestCase):
    def run_main(self, argv, euid=0, env=None, start=None):
        output = []
        code = service.main(argv, env or {}, geteuid=lambda: euid, start=start or (lambda env: 0),
                            emit=output.append, report=lambda message: None)
        return code, output

    def test_arguments_are_refused(self):
        for argv in (["--protocol"], ["start"], [""], ["--help"], ["-h"]):
            with self.subTest(argv=argv):
                code, output = self.run_main(argv)
                self.assertEqual((code, output), (2, [{"ok": False, "error": "usage"}]))

    def test_needs_root(self):
        self.assertEqual(self.run_main([], euid=1000), (4, [{"ok": False,
                                                             "error": "not-permitted"}]))

    def test_missing_socket_fails(self):
        code, output = self.run_main([], start=lambda env: service.start(env, pid=1))
        self.assertEqual((code, output), (3, [{"ok": False, "error": "failed"}]))

    def test_start_result_is_returned(self):
        self.assertEqual(self.run_main([], start=lambda env: 0), (0, []))


class Prepare(unittest.TestCase):
    """啟動順序：先確認 socket、開好計數器，再放棄權限；放棄失敗就不服務。"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)/"powercap"
        zone = self.base/"intel-rapl:0"
        zone.mkdir(parents=True)
        (zone/"name").write_text("package-0\n")
        (zone/"energy_uj").write_text("10\n")
        (zone/"max_energy_range_uj").write_text("100\n")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(os.path.join(temp.name, "s.sock"))
        listener.listen()
        self.fd = os.dup(listener.fileno())
        listener.close()
        self.env = {"LISTEN_PID": "77", "LISTEN_FDS": "1"}

    def test_channels_are_open_before_the_drop(self):
        seen = []

        def drop():
            seen.append("drop")

        server = service.prepare(self.env, 77, str(self.base), drop, fd=self.fd)
        self.addCleanup(server.close)
        self.assertEqual(seen, ["drop"])
        self.assertEqual(server.take().package, 10)
        self.assertEqual(server.ranges, service.Ranges(100, None))
        self.assertEqual(server.owner, os.getuid())

    def test_failed_drop_closes_everything(self):
        def drop():
            raise service.Failure("drop")

        with self.assertRaises(service.Failure):
            service.prepare(self.env, 77, str(self.base), drop, fd=self.fd)
        with self.assertRaises(OSError):
            os.fstat(self.fd)


if __name__ == "__main__":
    unittest.main()
