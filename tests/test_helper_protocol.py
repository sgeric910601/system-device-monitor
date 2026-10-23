"""CPU 功耗取樣服務的通訊協定：以暫存目錄裡真正的 Unix socket 執行服務迴圈。

時間限制縮短（閒置、連線上限、最短間隔），讓測試在一秒內完成；
能量計數器以每次加 1 焦耳的假讀值代替，精確的瓦數計算在 test_helper_service。
"""
import itertools
import json
import os
import socket
import tempfile
import threading
import time
import unittest

from tests.fixtures.helpers import load_helper

service = load_helper("sdm-cpu-power-service")
J = 1_000_000
WAIT = 3.0                  # 測試等待回應或斷線的上限（秒）
LIMITS = service.Limits(clients=3, idle_exit=0.6, client_idle=0.4, min_spacing=0.05)


class Protocol(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = os.path.join(temp.name, "cpu-power.sock")
        self.peer_uid = os.getuid()
        self.result = []
        self.thread = None

    def start(self, limits=LIMITS, peer=None):
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(self.path)
        listener.listen()
        energy = itertools.count(1)
        take = lambda: service.Sample(time.monotonic(), next(energy) * J, None)  # noqa: E731
        self.server = service.Server(listener, take, service.Ranges(None, None), os.getuid(),
                                     limits=limits, peer=peer or (lambda sock: self.peer_uid))
        self.thread = threading.Thread(target=lambda: self.result.append(self.server.run()),
                                       daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.thread.join(WAIT + 1)
        self.server.close()

    def connect(self):
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(WAIT)
        client.connect(self.path)
        self.addCleanup(client.close)
        return client

    @staticmethod
    def read_line(client):
        data = b""
        while not data.endswith(b"\n"):
            chunk = client.recv(256)
            if not chunk:
                return None
            data += chunk
        return json.loads(data)

    def request(self, client):
        client.sendall(b"r\n")
        return self.read_line(client)

    def assert_closed(self, client):
        try:
            self.assertEqual(client.recv(256), b"")
        except ConnectionResetError:
            pass

    def test_first_request_samples_then_reports_watts(self):
        self.start()
        client = self.connect()
        self.assertEqual(self.request(client)["status"], "sampling")
        time.sleep(0.1)
        reply = self.request(client)
        self.assertEqual(reply["status"], "ok")
        self.assertGreater(reply["package_w"], 0)
        self.assertGreater(reply["interval_s"], 0)
        self.assertIsNone(reply["core_w"])

    def test_partial_request_is_completed_later(self):
        self.start()
        client = self.connect()
        client.sendall(b"r")
        time.sleep(0.05)
        client.sendall(b"\n")
        self.assertEqual(self.read_line(client)["v"], 1)

    def test_malformed_input_closes_the_connection(self):
        self.start()
        for payload in (b"x\n", b"r\nr\n", b"rr", b"GET / HTTP/1.0\r\n\r\n", b"\x00" * 64):
            with self.subTest(payload=payload):
                client = self.connect()
                client.sendall(payload)
                self.assert_closed(client)

    def test_client_limit(self):
        self.start()
        clients = [self.connect() for _ in range(LIMITS.clients)]
        for client in clients:
            self.assertIsNotNone(self.request(client))
        extra = self.connect()
        self.assert_closed(extra)
        clients[0].close()
        time.sleep(0.1)
        self.assertIsNotNone(self.request(self.connect()))

    def test_other_users_are_refused(self):
        self.start(peer=lambda sock: self.peer_uid + 1)
        self.assert_closed(self.connect())

    def test_root_may_connect(self):
        self.start(peer=lambda sock: 0)
        self.assertIsNotNone(self.request(self.connect()))

    def test_silent_client_is_dropped(self):
        self.start()
        client = self.connect()
        started = time.monotonic()
        self.assert_closed(client)
        self.assertGreaterEqual(time.monotonic() - started, LIMITS.client_idle - 0.1)

    def test_exits_after_being_idle(self):
        self.start()
        self.thread.join(WAIT)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.result, [service.EXIT_OK])

    def test_stays_while_a_client_keeps_asking(self):
        self.start()
        client = self.connect()
        deadline = time.monotonic() + LIMITS.idle_exit * 2
        while time.monotonic() < deadline:
            self.assertIsNotNone(self.request(client))
            time.sleep(0.1)
        self.assertTrue(self.thread.is_alive())
        client.close()
        self.thread.join(WAIT)
        self.assertEqual(self.result, [service.EXIT_OK])


if __name__ == "__main__":
    unittest.main()
