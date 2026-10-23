"""CPU 功耗取樣服務的客戶端：回應解析、新鮮度、退避重試與只在需要時連線。

大部分測試以假的 socket 代替；最後一組與 helper/sdm-cpu-power-service 真正的服務迴圈
在暫存目錄的 Unix socket 上對接，確認雙方的通訊協定一致。
"""
import math
import os
import socket
import stat
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

from sdm.data import cpu_power_client as client_module
from sdm.data.cpu_power_client import CpuPowerClient, CpuPowerReading, fresh_watts, parse_reply
from tests.fixtures.helpers import load_helper

ME = 1000
PATH = "/run/system-device-monitor/cpu-power.sock"
OK = b'{"v":1,"status":"ok","package_w":42.5,"core_w":30.1,"interval_s":2.0}\n'
SAMPLING = b'{"v":1,"status":"sampling","package_w":null,"core_w":null,"interval_s":null}\n'
ERROR = b'{"v":1,"status":"error","package_w":null,"core_w":null,"interval_s":null}\n'


def socket_stat(uid=ME, mode=stat.S_IFSOCK | 0o600):
    return SimpleNamespace(st_mode=mode, st_uid=uid)


class FakeSocket:
    def __init__(self, replies):
        self.replies, self.sent, self.closed = list(replies), [], False

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, size):
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def close(self):
        self.closed = True


class Clock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value


class Parse(unittest.TestCase):
    def test_ok_reply(self):
        self.assertEqual(parse_reply(OK), ("ok", 42.5, 30.1))

    def test_sampling_and_error(self):
        self.assertEqual(parse_reply(SAMPLING), ("sampling", None, None))
        self.assertEqual(parse_reply(ERROR), ("error", None, None))

    def test_core_may_be_missing(self):
        line = b'{"v":1,"status":"ok","package_w":12.0,"core_w":null,"interval_s":1.0}\n'
        self.assertEqual(parse_reply(line), ("ok", 12.0, None))

    def test_invalid_replies(self):
        cases = (b"", b"\n", b"not json\n", b"[]\n", b'{"v":2,"status":"ok","package_w":1}\n',
                 b'{"v":1,"status":"weird"}\n', b'{"v":1,"status":"ok","package_w":null}\n',
                 b'{"v":1,"status":"ok","package_w":NaN}\n',
                 b'{"v":1,"status":"ok","package_w":Infinity}\n',
                 b'{"v":1,"status":"ok","package_w":-1}\n',
                 b'{"v":1,"status":"ok","package_w":1000}\n',
                 b'{"v":1,"status":"ok","package_w":"5"}\n',
                 b'{"v":1,"status":"ok","package_w":true}\n',
                 b'{"v":1,"status":"ok","package_w":5,"core_w":-2}\n',
                 b'{"v":true,"status":"ok","package_w":5}\n',
                 b"\xff\xfe\n")
        for line in cases:
            with self.subTest(line=line):
                self.assertIsNone(parse_reply(line))


class Freshness(unittest.TestCase):
    """移植參考專案 CPUPowerReading.freshWatts（預設最長 3 秒）。"""

    def test_reference_cases(self):
        reading = CpuPowerReading(17.5, None, 10.0)
        self.assertEqual(fresh_watts(reading, 12.9), 17.5)
        self.assertIsNone(fresh_watts(reading, 14.0))
        self.assertIsNone(fresh_watts(reading, 9.0))
        self.assertIsNone(fresh_watts(CpuPowerReading(math.nan, None, 10.0), 11.0))
        self.assertIsNone(fresh_watts(CpuPowerReading(-1.0, None, 10.0), 11.0))

    def test_longer_limit_and_missing_reading(self):
        reading = CpuPowerReading(4.2, None, 10.0)
        self.assertIsNone(fresh_watts(reading, 13.5))
        self.assertEqual(fresh_watts(reading, 13.5, max_age=4), 4.2)
        self.assertIsNone(fresh_watts(reading, 14.5, max_age=4))
        self.assertIsNone(fresh_watts(None, 10.0))


class Client(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.stat = socket_stat()
        self.sockets, self.connects = [], []
        self.client = CpuPowerClient(clock=self.clock, connect=self.connect,
                                     lstat=self.lstat, uid=lambda: ME)
        self.client.configure(PATH)

    def lstat(self, path):
        if self.stat is None:
            raise FileNotFoundError(path)
        return self.stat

    def connect(self, path, timeout):
        self.connects.append((path, timeout))
        if not self.sockets:
            raise ConnectionRefusedError()
        return self.sockets.pop(0)

    def test_inactive_client_never_connects(self):
        self.client.request()
        self.assertEqual(self.connects, [])
        self.assertIsNone(self.client.latest())

    def test_ok_reply_becomes_the_latest_reading(self):
        fake = FakeSocket([OK])
        self.sockets.append(fake)
        self.client.set_active(True)
        self.client.request()
        self.assertEqual(self.client.latest(), CpuPowerReading(42.5, 30.1, 100.0))
        self.assertEqual(fake.sent, [b"r\n"])
        self.assertEqual(self.connects, [(PATH, client_module.SOCKET_TIMEOUT)])

    def test_connection_is_kept_between_requests(self):
        fake = FakeSocket([SAMPLING, OK])
        self.sockets.append(fake)
        self.client.set_active(True)
        self.client.request()
        self.assertIsNone(self.client.latest())
        self.clock.value = 102.0
        self.client.request()
        self.assertEqual(len(self.connects), 1)
        self.assertEqual(self.client.latest().stamp, 102.0)

    def test_reply_split_over_several_reads(self):
        self.sockets.append(FakeSocket([OK[:10], OK[10:30], OK[30:]]))
        self.client.set_active(True)
        self.client.request()
        self.assertEqual(self.client.latest().package_w, 42.5)

    def test_socket_must_be_ours(self):
        self.client.set_active(True)
        for status in (socket_stat(uid=ME + 1), socket_stat(mode=stat.S_IFREG | 0o600), None):
            with self.subTest(status=status):
                self.stat = status
                self.clock.value += client_module.BACKOFF_MAX + 1
                self.client.request()
        self.assertEqual(self.connects, [])

    def test_failures_back_off_and_success_resets(self):
        self.client.set_active(True)
        delays = []
        for _ in range(8):
            before = len(self.connects)
            self.client.request()
            self.assertEqual(len(self.connects), before + 1)
            self.client.request()
            self.assertEqual(len(self.connects), before + 1)
            delays.append(self.client.retry_at - self.clock.value)
            self.clock.value = self.client.retry_at
        self.assertEqual(delays, [1, 2, 4, 8, 16, 32, 60, 60])
        self.sockets.append(FakeSocket([OK]))
        self.client.request()
        self.assertEqual(self.client.backoff, client_module.BACKOFF_INITIAL)

    def test_bad_or_missing_replies_close_the_connection(self):
        for replies in ([b"garbage\n"], [b""], [socket.timeout()], [ConnectionResetError()],
                        [ERROR], [b"x" * (client_module.MAX_REPLY_BYTES + 1)]):
            with self.subTest(replies=replies):
                fake = FakeSocket(replies)
                self.sockets.append(fake)
                self.client.set_active(True)
                self.clock.value += client_module.BACKOFF_MAX + 1
                self.client.request()
                self.assertTrue(fake.closed)
                self.assertGreater(self.client.retry_at, self.clock.value)

    def test_error_reply_clears_the_reading(self):
        self.sockets.extend([FakeSocket([OK]), FakeSocket([ERROR])])
        self.client.set_active(True)
        self.client.request()
        self.assertIsNotNone(self.client.latest())
        self.client.socket.replies.append(ERROR)
        self.clock.value += 5
        self.client.request()
        self.assertIsNone(self.client.latest())

    def test_deactivating_closes_and_forgets(self):
        fake = FakeSocket([OK])
        self.sockets.append(fake)
        self.client.set_active(True)
        self.client.request()
        self.client.set_active(False)
        self.client.request()
        self.assertTrue(fake.closed)
        self.assertIsNone(self.client.latest())

    def test_reactivating_does_not_wait_for_an_old_backoff(self):
        self.client.set_active(True)
        self.client.request()
        self.client.set_active(False)
        self.client.set_active(True)
        self.sockets.append(FakeSocket([OK]))
        self.client.request()
        self.assertIsNotNone(self.client.latest())

    def test_changing_the_path_closes_the_connection(self):
        fake = FakeSocket([OK])
        self.sockets.append(fake)
        self.client.set_active(True)
        self.client.request()
        self.client.configure("/run/system-device-monitor/cpu-power-local.sock")
        self.client.request()
        self.assertTrue(fake.closed)
        self.assertIsNone(self.client.latest())

    def test_unconfigured_client_does_nothing(self):
        self.client.configure(None)
        self.client.set_active(True)
        self.client.request()
        self.assertEqual(self.connects, [])


service = load_helper("sdm-cpu-power-service")


class RealService(unittest.TestCase):
    """與真正的服務迴圈（縮短時間限制）對接。"""

    def test_client_and_service_agree(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "cpu-power.sock")
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(path)
            listener.listen()
            energy = iter(range(1, 1000))
            take = lambda: service.Sample(time.monotonic(), next(energy) * 1_000_000,  # noqa
                                          None)
            limits = service.Limits(clients=2, idle_exit=0.5, client_idle=2.0, period=0.05)
            server = service.Server(listener, take, service.Ranges(None, None), os.getuid(),
                                    limits=limits)
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            client = CpuPowerClient()
            client.configure(path)
            client.set_active(True)
            for _ in range(20):
                client.request()
                if client.latest() is not None:
                    break
                time.sleep(0.03)
            reading = client.latest()
            self.assertIsNotNone(reading)
            self.assertGreater(reading.package_w, 0)
            client.set_active(False)
            client.request()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            server.close()


if __name__ == "__main__":
    unittest.main()
