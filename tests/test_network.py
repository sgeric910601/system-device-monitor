"""網路：/proc/net/dev、主要介面（預設路由）、IPv4（ioctl）與主要介面速率。

位址只用文件範例網段 192.0.2.0/24；介面名稱都是虛構的。
不照搬：MonitorTests.testRateHandlesResetAndInterval 的「倒退時回傳 0」：
這個專案的計數器倒退時回傳 None（不顯示），沿用既有 Counters 的行為；間隔換算的數值照搬。
"""
from pathlib import Path
import struct
import tempfile
import unittest

from sdm.constants import Network
from sdm.core.counters import Counters
from sdm.core.network import PrimaryRates
from sdm.data import network
from tests.fixtures import procfs

ROUTES = ("Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
          "wlp2s0\t00000000\t010200C0\t0003\t0\t0\t600\t00000000\t0\t0\t0\n"
          "enp1s0\t00000000\t010200C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
          "docker0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
          "tun0\t00000000\t00000000\t0002\t0\t0\t10\t00000000\t0\t0\t0\n")


def ifreq_reply(name, address):
    """SIOCGIFADDR 的回應：介面名稱 16 位元組，接著 sockaddr_in（AF_INET、埠、位址）。"""
    head = name.encode().ljust(Network.IFNAMSIZ, b"\0")
    return (head + struct.pack("<H", 2) + b"\0\0" + bytes(address)).ljust(256, b"\0")


class Counting(unittest.TestCase):
    def test_rate_uses_the_interval(self):
        # MonitorTests.testRateHandlesResetAndInterval 的間隔案例
        counters = Counters()
        counters.rate("x", 1000, 0.0)
        self.assertEqual(counters.rate("x", 3000, 2.0), 1000)
        self.assertIsNone(counters.rate("x", 1000, 3.0))     # 倒退：不顯示

    def test_net_dev(self):
        counters = network.parse_net_dev(procfs.net_dev(0))
        self.assertEqual([c.name for c in counters], ["enp1s0", "wlp2s0", "veth1", "docker0"])
        self.assertEqual((counters[0].rx_bytes, counters[0].tx_bytes), (5000, 3000))
        self.assertEqual(network.parse_net_dev("h\nh\nbroken line\neth9: x y\n"), ())


class Primary(unittest.TestCase):
    def test_default_route_with_the_lowest_metric(self):
        self.assertEqual(network.primary_interface(ROUTES), "enp1s0")

    def test_no_usable_default_route(self):
        self.assertIsNone(network.primary_interface(""))
        self.assertIsNone(network.primary_interface(ROUTES.splitlines()[0] + "\n" +
                                                    ROUTES.splitlines()[3] + "\n"))
        self.assertIsNone(network.primary_interface("Iface\nbad\tline\n"))

    def test_interface_kinds(self):
        with tempfile.TemporaryDirectory() as temp:
            procfs.write_system(Path(temp))
            kinds = {name: network.interface_state(temp, name).kind
                     for name in ("enp1s0", "wlp2s0", "veth1")}
        self.assertEqual(kinds, {"enp1s0": "有線網路", "wlp2s0": "Wi-Fi", "veth1": "虛擬介面"})


class Address(unittest.TestCase):
    def test_ipv4_from_ioctl(self):
        calls = []
        def ioctl(fd, request, argument):
            calls.append((request, argument[:Network.IFNAMSIZ]))
            return ifreq_reply("enp1s0", (192, 0, 2, 15))
        self.assertEqual(network.ipv4_address("enp1s0", ioctl), "192.0.2.15")
        self.assertEqual(calls[0][0], Network.SIOCGIFADDR)
        self.assertTrue(calls[0][1].startswith(b"enp1s0\0"))

    def test_invalid_names_are_not_queried(self):
        def ioctl(*_):
            raise AssertionError("queried")
        for name in ("", None, "a" * 16, "../x", "e th0", "é0"):
            self.assertIsNone(network.ipv4_address(name, ioctl))

    def test_interface_without_an_address(self):
        def ioctl(*_):
            raise OSError("no address")
        self.assertIsNone(network.ipv4_address("enp1s0", ioctl))
        self.assertIsNone(network.ipv4_address("enp1s0", lambda *_: b"short"))

    def test_address_cache_refreshes_every_30_seconds_or_on_change(self):
        reads, clock = [], [100.0]
        def read(name):
            reads.append(name)
            return "192.0.2.%d" % len(reads)
        cache = network.AddressCache(read, lambda: clock[0])
        self.assertEqual(cache.get("enp1s0"), "192.0.2.1")
        clock[0] += Network.IPV4_INTERVAL - 1
        self.assertEqual(cache.get("enp1s0"), "192.0.2.1")
        self.assertEqual(cache.get("wlp2s0"), "192.0.2.2")
        clock[0] += Network.IPV4_INTERVAL
        self.assertEqual(cache.get("wlp2s0"), "192.0.2.3")
        self.assertIsNone(cache.get(None))


class PrimaryRate(unittest.TestCase):
    def test_baseline_resets_when_the_primary_interface_changes(self):
        rates = PrimaryRates()
        self.assertEqual(rates.sample("enp1s0", 1000, 500, 0.0), (None, None, True))
        self.assertEqual(rates.sample("enp1s0", 3000, 900, 2.0), (1000.0, 200.0, False))
        self.assertEqual(rates.sample("wlp2s0", 9000, 9000, 4.0), (None, None, True))
        self.assertEqual(rates.sample("wlp2s0", 9400, 9000, 6.0), (200.0, 0.0, False))
        self.assertEqual(rates.sample(None, 0, 0, 8.0), (None, None, True))

    def test_counter_reset_and_long_gap(self):
        rates = PrimaryRates()
        rates.sample("enp1s0", 1000, 1000, 0.0)
        self.assertEqual(rates.sample("enp1s0", 10, 2000, 2.0), (None, 500.0, False))
        self.assertEqual(rates.sample("enp1s0", 20, 2100, 500.0), (None, None, False))


if __name__ == "__main__":
    unittest.main()
