"""CPU 功耗取樣的 systemd 單元與服務小程式：強化設定、兩種安裝方式一致、常數與 sdm 相同。"""
import ast
from pathlib import Path
import stat
import sys
import unittest

from sdm.constants import CpuPower, Power
from tests.fixtures.helpers import HELPERS, load_helper

service = load_helper("sdm-cpu-power-service")
UNITS = HELPERS/"systemd"
DEB = "system-device-monitor-cpu-power"
LOCAL = "system-device-monitor-local-cpu-power"
HARDENING = {
    "CapabilityBoundingSet": "CAP_DAC_READ_SEARCH CAP_SETUID CAP_SETGID",
    "NoNewPrivileges": "yes", "PrivateNetwork": "yes", "PrivateDevices": "yes",
    "PrivateTmp": "yes", "ProtectSystem": "strict", "ProtectHome": "yes",
    "ProtectKernelTunables": "yes", "ProtectKernelModules": "yes", "ProtectKernelLogs": "yes",
    "ProtectControlGroups": "yes", "ProtectClock": "yes", "ProtectHostname": "yes",
    "ProtectProc": "invisible", "RestrictAddressFamilies": "AF_UNIX",
    "RestrictNamespaces": "yes", "SystemCallFilter": "@system-service",
    "SystemCallArchitectures": "native", "MemoryMax": "64M", "TasksMax": "4",
    "LockPersonality": "yes", "RestrictRealtime": "yes", "RestrictSUIDSGID": "yes",
    "UMask": "0077",
}
SERVICE_KEYS = set(HARDENING) | {"Type", "ExecStart"}
SOCKET = {"SocketMode": "0600", "DirectoryMode": "0755", "RemoveOnStop": "yes", "Accept": "no"}


def parse_unit(path):
    """（區段、鍵）→ 值的清單；systemd 允許同一個鍵出現多次，所以保留每一次。"""
    entries, section = {}, None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        key, separator, value = line.partition("=")
        if not separator or section is None:
            raise ValueError("Unexpected unit line: " + line)
        entries.setdefault((section, key.strip()), []).append(value.strip())
    return entries


def imported_modules(path):
    """檔案匯入的最上層模組名稱；不允許相對匯入。"""
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise AssertionError("relative import in " + path.name)
            names.add(node.module.split(".")[0])
    return names


def single(entries, section, key):
    values = entries.get((section, key), [])
    if len(values) != 1:
        raise AssertionError(f"{section}.{key} should appear once: {values}")
    return values[0]


class ServiceUnits(unittest.TestCase):
    def check_service(self, name, helper, socket_unit):
        entries = parse_unit(UNITS/(name + ".service"))
        for key, value in HARDENING.items():
            with self.subTest(unit=name, key=key):
                self.assertEqual(single(entries, "Service", key), value)
        keys = {key for section, key in entries if section == "Service"}
        self.assertEqual(sorted(keys - SERVICE_KEYS), [])
        self.assertEqual(single(entries, "Service", "ExecStart"), helper)
        self.assertEqual(single(entries, "Service", "Type"), "simple")
        self.assertEqual(single(entries, "Unit", "Requires"), socket_unit)
        self.assertEqual(single(entries, "Unit", "After"), socket_unit)
        self.assertNotIn(("Install", "WantedBy"), entries)

    def test_deb_service(self):
        self.check_service(DEB, CpuPower.DEB_SERVICE_HELPER, DEB + ".socket")

    def test_local_service(self):
        self.check_service(LOCAL, CpuPower.LOCAL_SERVICE_HELPER, LOCAL + ".socket")

    def check_socket(self, name, path):
        entries = parse_unit(UNITS/(name + ".socket"))
        self.assertEqual(single(entries, "Socket", "ListenStream"), path)
        for key, value in SOCKET.items():
            with self.subTest(unit=name, key=key):
                self.assertEqual(single(entries, "Socket", key), value)
        self.assertEqual(single(entries, "Socket", "Service"), name + ".service")
        for key in ("SocketUser", "SocketGroup", "ListenDatagram", "ListenSequentialPacket",
                    "Symlinks", "ExecStartPre", "ExecStartPost"):
            self.assertNotIn(("Socket", key), entries)
        self.assertEqual(single(entries, "Install", "WantedBy"), "sockets.target")

    def test_deb_socket(self):
        self.check_socket(DEB, CpuPower.DEB_SOCKET)

    def test_local_socket(self):
        self.check_socket(LOCAL, CpuPower.LOCAL_SOCKET)

    def test_local_units_only_differ_in_names_and_paths(self):
        replacements = ((DEB, LOCAL), ("/usr/libexec/", "/usr/local/libexec/"),
                        (CpuPower.DEB_SOCKET, CpuPower.LOCAL_SOCKET))
        for suffix in (".service", ".socket"):
            with self.subTest(suffix=suffix):
                text = (UNITS/(DEB + suffix)).read_text(encoding="utf-8")
                for old, new in replacements:
                    text = text.replace(old, new)
                self.assertEqual(text, (UNITS/(LOCAL + suffix)).read_text(encoding="utf-8"))

    def test_unit_directory_has_only_these_units(self):
        self.assertEqual(sorted(path.name for path in UNITS.iterdir()),
                         sorted(name + suffix for name in (DEB, LOCAL)
                                for suffix in (".service", ".socket")))


class ServiceHelper(unittest.TestCase):
    path = HELPERS/"sdm-cpu-power-service"

    def test_runs_isolated_without_bytecode(self):
        first = self.path.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(first, "#!/usr/bin/python3 -IB")
        self.assertTrue(self.path.stat().st_mode & stat.S_IXUSR)
        self.assertFalse(self.path.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH))

    def test_helpers_import_only_the_standard_library(self):
        forbidden = {"sdm-cpu-power-service": ("subprocess", "ctypes", "importlib", "shutil",
                                               "pickle"),
                     "sdm-cpu-power": ("ctypes", "importlib", "shutil", "pickle")}
        self.assertEqual(sorted(path.name for path in HELPERS.glob("sdm-*")), sorted(forbidden))
        for name, refused in forbidden.items():
            with self.subTest(helper=name):
                names = imported_modules(HELPERS/name)
                self.assertEqual(sorted(names - set(sys.stdlib_module_names)), [])
                self.assertEqual(sorted(names & set(refused)), [])

    def test_constants_match_the_application(self):
        self.assertEqual(service.PROTOCOL, CpuPower.PROTOCOL)
        self.assertEqual(service.MAX_CLIENTS, CpuPower.MAX_CLIENTS)
        self.assertEqual(service.IDLE_EXIT_S, CpuPower.IDLE_EXIT)
        self.assertEqual(service.CLIENT_IDLE_S, CpuPower.CLIENT_IDLE)
        self.assertEqual(service.PERIOD_S, CpuPower.PERIOD)
        self.assertEqual(service.ACCEPTS_PER_WINDOW, CpuPower.ACCEPTS_PER_SECOND)
        self.assertEqual(CpuPower.MAX_CLIENTS, 2)
        self.assertEqual(service.MAX_WINDOW_S, CpuPower.MAX_WINDOW)
        self.assertEqual(service.WATTS_DIGITS, CpuPower.WATTS_DIGITS)
        self.assertEqual(service.REQUEST, CpuPower.REQUEST)
        self.assertEqual(service.MAX_WATTS, Power.COMPONENT_MAX_W)
        self.assertEqual((service.EXIT_OK, service.EXIT_USAGE, service.EXIT_FAILED,
                          service.EXIT_NOT_PERMITTED), CpuPower.EXIT_CODES)

    def test_helper_paths_are_in_the_two_install_locations(self):
        for helper, directory in ((CpuPower.DEB_SERVICE_HELPER, CpuPower.DEB_HELPER_DIR),
                                  (CpuPower.LOCAL_SERVICE_HELPER, CpuPower.LOCAL_HELPER_DIR)):
            self.assertEqual(str(Path(helper).parent), directory)
            self.assertEqual(Path(helper).name, self.path.name)


if __name__ == "__main__":
    unittest.main()
