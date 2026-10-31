"""CPU 功耗取樣服務（helper/sdm-cpu-power-service）：能量差、共用視窗、systemd 交來的描述子
與權限斷言。

不需要 root：能量計數器用暫存檔（pread 一樣可用），監聽 socket 用暫存目錄裡的 Unix socket，
systemd 以 OpenFile= 交來的描述子由測試自己開啟；/proc/self/status 以文字代替。
通訊協定的整合測試見 test_helper_protocol。
"""
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import types
import unittest
from unittest import mock

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


def close_quietly(fd):
    try:
        os.close(fd)
    except OSError:
        pass


def is_open(fd):
    try:
        os.fstat(fd)
    except OSError:
        return False
    return True


class Zones(unittest.TestCase):
    """假的 /sys/class/powercap：systemd 交來的描述子必須正是該區域的 energy_uj，
    package 區域名稱為 package-0，core 必須是 intel-rapl:0:0 而且是唯一名為 core 的子區域。"""

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

    def fd(self, path):
        fd = os.open(path, os.O_RDONLY)
        self.addCleanup(close_quietly, fd)
        return fd

    def channels(self, package=True, core=True):
        named = {}
        if package:
            named["package"] = self.fd(self.base/"intel-rapl:0/energy_uj")
        if core:
            named["core"] = self.fd(self.base/"intel-rapl:0:0/energy_uj")
        return named, service.channels_from(named, str(self.base))

    def test_package_and_core(self):
        self.zone("intel-rapl:0", "package-0")
        self.zone("intel-rapl:0:0", "core", energy="40\n", max_range="900\n")
        self.zone("intel-rapl:0:1", "uncore")
        _, (package, core) = self.channels()
        self.assertEqual((service.read_counter(package.fd), package.max_range), (1000, 5000))
        self.assertEqual((service.read_counter(core.fd), core.max_range), (40, 900))

    def test_wrong_package_name_is_rejected_and_closed(self):
        self.zone("intel-rapl:0", "psys")
        self.zone("intel-rapl:0:0", "core")
        named, (package, core) = self.channels()
        self.assertIsNone(package)
        self.assertFalse(is_open(named["package"]))
        self.assertIsNotNone(core)

    def test_descriptor_of_another_file_is_rejected(self):
        self.zone("intel-rapl:0", "package-0")
        (self.base/"elsewhere").write_text("7\n")
        named = {"package": self.fd(self.base/"elsewhere")}
        self.assertEqual(service.channels_from(named, str(self.base)), (None, None))
        self.assertFalse(is_open(named["package"]))

    def test_duplicate_core_is_rejected(self):
        self.zone("intel-rapl:0", "package-0")
        self.zone("intel-rapl:0:0", "core")
        self.zone("intel-rapl:0:1", "core")
        _, (package, core) = self.channels()
        self.assertIsNotNone(package)
        self.assertIsNone(core)

    def test_core_must_be_the_first_subzone(self):
        self.zone("intel-rapl:0", "package-0")
        self.zone("intel-rapl:0:0", "uncore")
        self.zone("intel-rapl:0:1", "core")
        _, (_, core) = self.channels()
        self.assertIsNone(core)

    def test_unreadable_range_keeps_the_channel_without_wrap(self):
        self.zone("intel-rapl:0", "package-0", max_range="bogus\n")
        _, (package, _) = self.channels(core=False)
        self.assertEqual((service.read_counter(package.fd), package.max_range), (1000, None))

    def test_missing_descriptors_mean_no_channels(self):
        self.zone("intel-rapl:0", "package-0")
        self.assertEqual(service.channels_from({}, str(self.base)), (None, None))

    @staticmethod
    def stats(dev, ino=82397, mode=stat.S_IFREG | 0o400, uid=0, gid=0, size=4096, nlink=1):
        return types.SimpleNamespace(st_dev=dev, st_ino=ino, st_mode=mode, st_uid=uid, st_gid=gid,
                                     st_size=size, st_nlink=nlink)

    def test_remounted_sysfs_is_the_same_file(self):
        """PrivateNetwork=yes 讓服務的 /sys 重新掛載成另一個 sysfs：只有裝置的次編號不同。
        描述子是 systemd 在原本的 /sys 開的，仍然是同一個檔案。"""
        self.assertTrue(service.stats_match(self.stats(os.makedev(0, 25)),
                                            self.stats(os.makedev(0, 61))))

    def test_other_files_with_the_same_inode_do_not_match(self):
        """主編號 0 的檔案系統很多（tmpfs、proc、cgroup…），inode 也可能相同：其他欄位都要相符。"""
        expected = self.stats(os.makedev(0, 61))
        for changes in ({"ino": 82398}, {"dev": os.makedev(259, 2)}, {"mode": stat.S_IFREG | 0o644},
                        {"uid": 1000}, {"gid": 1000}, {"size": 0}, {"nlink": 2},
                        {"mode": stat.S_IFDIR | 0o400}):
            with self.subTest(changes=changes):
                fields = dict(changes)
                opened = self.stats(fields.pop("dev", os.makedev(0, 25)), **fields)
                self.assertFalse(service.stats_match(opened, expected))

    def test_hard_link_elsewhere_is_another_path(self):
        """同一個 inode 從別的路徑開啟（硬連結）：/proc/self/fd 記錄的開啟路徑不同，不接受。"""
        directory = self.zone("intel-rapl:0", "package-0")
        os.link(directory/"energy_uj", self.base/"elsewhere")
        fd = self.fd(self.base/"elsewhere")
        self.assertFalse(service.same_file(fd, str(directory/"energy_uj")))
        self.assertTrue(service.same_file(self.fd(directory/"energy_uj"), str(directory/"energy_uj")))

    def test_non_regular_descriptor_is_rejected(self):
        self.zone("intel-rapl:0", "package-0")
        reader, writer = os.pipe()
        self.addCleanup(close_quietly, writer)
        self.assertEqual(service.channels_from({"package": reader}, str(self.base)), (None, None))
        self.assertFalse(is_open(reader))


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


SAFE = ("Name:\tpython3\nUid:\t61234\t61234\t61234\t61234\nGid:\t61234\t61234\t61234\t61234\n"
        "CapInh:\t0000000000000000\nCapPrm:\t0000000000000000\nCapEff:\t0000000000000000\n"
        "CapBnd:\t0000000000000000\nCapAmb:\t0000000000000000\nNoNewPrivs:\t1\n")


class Privileges(unittest.TestCase):
    """執行時的防禦性斷言：不是 root、沒有任何能力（含繼承、邊界與環境能力）、禁止取得新權限。"""

    def test_dynamic_user_without_capabilities_is_accepted(self):
        self.assertIsNone(service.privilege_problem(SAFE))

    def test_root_is_refused(self):
        for uids in ("0\t0\t0\t0", "61234\t0\t61234\t61234", "61234\t61234\t61234\t0",
                     "61234\t61234\t61234"):
            with self.subTest(uids=uids):
                status = SAFE.replace("Uid:\t61234\t61234\t61234\t61234", "Uid:\t" + uids)
                self.assertIsNotNone(service.privilege_problem(status))

    def test_any_capability_is_refused(self):
        for field in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
            with self.subTest(field=field):
                status = SAFE.replace(f"{field}:\t0000000000000000", f"{field}:\t0000000000000004")
                self.assertIsNotNone(service.privilege_problem(status))
                missing = SAFE.replace(f"{field}:\t0000000000000000\n", "")
                self.assertIsNotNone(service.privilege_problem(missing))

    def test_malformed_or_privileged_status(self):
        for status in ("", SAFE.replace("NoNewPrivs:\t1", "NoNewPrivs:\t0"),
                       SAFE.replace("CapEff:\t0000000000000000", "CapEff:\tzz"),
                       SAFE.replace("Uid:\t61234", "Uid:\tabc")):
            with self.subTest(status=status[:40]):
                self.assertIsNotNone(service.privilege_problem(status))


class Descriptors(unittest.TestCase):
    """systemd 交來的描述子：LISTEN_PID、LISTEN_FDS 與 LISTEN_FDNAMES 必須一致。"""

    def env(self, names, pid="4321", count=None):
        return {"LISTEN_PID": pid, "LISTEN_FDS": str(len(names) if count is None else count),
                "LISTEN_FDNAMES": ":".join(names)}

    def test_names_map_to_descriptors_in_order(self):
        env = self.env(["package", "listen", "core"])
        named = service.inherited_fds(env, 4321, fds=[10, 11, 12], inherit=lambda fd: None)
        self.assertEqual(named, {"package": 10, "listen": 11, "core": 12})
        self.assertEqual(env, {})

    def test_default_descriptors_start_at_three(self):
        names = service.inherited_fds(self.env(["listen"]), 4321, fds=None, inherit=lambda fd: None)
        self.assertEqual(names, {"listen": 3})

    def test_inconsistent_environments_are_refused(self):
        cases = (self.env(["listen"], pid="1"), self.env(["listen", "package"], count=1),
                 self.env(["package"]), self.env(["listen", "listen"]),
                 self.env(["listen", "extra"]), self.env(["listen"], count="x"), {},
                 self.env(["listen", "package", "core", "core2"]))
        for env in cases:
            with self.subTest(env=env):
                with self.assertRaises(service.Failure):
                    service.inherited_fds(env, 4321, fds=range(100, 100 + 4))


class Listener(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = os.path.join(temp.name, "cpu.sock")

    def listening(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(self.path)
        sock.listen()
        return sock

    def check(self, sock):
        fd = os.dup(sock.fileno())
        sock.close()
        try:
            return service.listening_socket(fd)
        except service.Failure:
            os.close(fd)
            raise

    def test_valid_listener_is_accepted(self):
        listener = self.check(self.listening())
        self.addCleanup(listener.close)
        self.assertEqual((listener.family, listener.type), (socket.AF_UNIX, socket.SOCK_STREAM))

    def test_non_listening_or_datagram_sockets_are_refused(self):
        with self.assertRaises(service.Failure):
            self.check(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
        datagram = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        datagram.bind(self.path)
        with self.assertRaises(service.Failure):
            self.check(datagram)

    def test_regular_file_descriptor_is_refused(self):
        fd = os.open(os.devnull, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        with self.assertRaises(service.Failure):
            service.listening_socket(fd)

    def test_socket_owner_comes_from_the_socket_file(self):
        listener = self.listening()
        self.addCleanup(listener.close)
        self.assertEqual(service.socket_owner(listener), os.getuid())


class Main(unittest.TestCase):
    def run_main(self, argv, status=SAFE, env=None, start=None):
        output = []

        def read_status():
            if isinstance(status, BaseException):
                raise status
            return status
        code = service.main(argv, env or {}, read_status=read_status,
                            start=start or (lambda env: 0), emit=output.append,
                            report=lambda message: None)
        return code, output

    def test_arguments_are_refused(self):
        for argv in (["--protocol"], ["start"], [""], ["--help"], ["-h"]):
            with self.subTest(argv=argv):
                self.assertEqual(self.run_main(argv, status="Uid:\t0\t0\t0\t0\n"),
                                 (2, [{"ok": False, "error": "usage"}]))

    def test_privileged_or_unknown_context_is_refused(self):
        refused = (4, [{"ok": False, "error": "not-permitted"}])
        for status in (SAFE.replace("61234", "0"), SAFE.replace("CapBnd:\t0000000000000000",
                                                                "CapBnd:\t000001ffffffffff"),
                       OSError("no proc")):
            with self.subTest(status=str(status)[:30]):
                self.assertEqual(self.run_main([], status=status), refused)

    def test_missing_socket_fails(self):
        code, output = self.run_main([], start=lambda env: service.start(env, pid=1))
        self.assertEqual((code, output), (3, [{"ok": False, "error": "failed"}]))

    def test_start_result_is_returned(self):
        self.assertEqual(self.run_main([], start=lambda env: 0), (0, []))


class Prepare(unittest.TestCase):
    """啟動：依名稱找出監聽 socket 與兩個計數器；任何不符就關閉全部描述子。"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)/"powercap"
        for name, label, value in (("intel-rapl:0", "package-0", "10\n"),
                                   ("intel-rapl:0:0", "core", "4\n")):
            zone = self.base/name
            zone.mkdir(parents=True)
            (zone/"name").write_text(label + "\n")
            (zone/"energy_uj").write_text(value)
            (zone/"max_energy_range_uj").write_text("100\n")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(os.path.join(temp.name, "s.sock"))
        listener.listen()
        self.listen = os.dup(listener.fileno())
        listener.close()
        self.package = os.open(self.base/"intel-rapl:0/energy_uj", os.O_RDONLY)
        self.core = os.open(self.base/"intel-rapl:0:0/energy_uj", os.O_RDONLY)
        for fd in (self.listen, self.package, self.core):
            self.addCleanup(close_quietly, fd)

    def prepare(self, names, fds):
        env = {"LISTEN_PID": "77", "LISTEN_FDS": str(len(names)), "LISTEN_FDNAMES": ":".join(names)}
        return service.prepare(env, 77, str(self.base), fds=fds)

    def test_all_descriptors(self):
        server = self.prepare(["core", "listen", "package"], [self.core, self.listen, self.package])
        self.addCleanup(server.close)
        self.assertEqual((server.take().package, server.take().core), (10, 4))
        self.assertEqual(server.ranges, service.Ranges(100, 100))
        self.assertEqual(server.owner, os.getuid())

    def test_missing_counters_still_serve_errors(self):
        server = self.prepare(["listen"], [self.listen])
        self.addCleanup(server.close)
        server.sampler.advance(server.clock())
        self.assertEqual(decode(server.sampler.reply)["status"], "error")

    def test_bad_listener_closes_everything(self):
        with self.assertRaises(service.Failure):
            self.prepare(["listen", "package"], [self.package, self.listen])
        self.assertFalse(is_open(self.package))
        self.assertFalse(is_open(self.listen))


if __name__ == "__main__":
    unittest.main()
