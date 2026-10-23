"""原始碼安裝的系統元件（scripts/install_system.py，以 root 經 pkexec 執行）。

以假根目錄（擁有者＝執行測試的使用者）代替 /，dpkg-query 與 systemctl 以假函式代替；
暫存目錄的內容與雜湊和 install.py 產生的格式相同。
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from sdm.constants import CpuPower
from tests.fixtures.fakeroot import FakeRoot
from tests.fixtures.helpers import ROOT, load_helper, load_script

installer = load_script("install_system")
control = load_helper("sdm-cpu-power")
SOCKET = "system-device-monitor-local-cpu-power.socket"
SERVICE = "system-device-monitor-local-cpu-power.service"
TARGETS = {
    "usr/local/libexec/system-device-monitor/sdm-cpu-power": 0o755,
    "usr/local/libexec/system-device-monitor/sdm-cpu-power-service": 0o755,
    "usr/local/share/polkit-1/actions/tw.local.system-device-monitor.local.policy": 0o644,
    "etc/systemd/system/" + SOCKET: 0o644,
    "etc/systemd/system/" + SERVICE: 0o644,
}
SYSTEMCTL = "/usr/bin/systemctl"
DPKG_QUERY = "/usr/bin/dpkg-query"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class Runner:
    """假的 dpkg-query 與 systemctl：回傳（結束碼、標準輸出）並記錄呼叫。"""

    def __init__(self, package_status=None, enabled=False):
        self.calls, self.package_status, self.enabled = [], package_status, enabled

    def __call__(self, argv):
        self.calls.append(tuple(argv))
        if argv[0] == DPKG_QUERY:
            return (0, self.package_status) if self.package_status else (1, "")
        if argv[1] == "is-enabled":
            return (0 if self.enabled else 1), ""
        return 0, ""


class Base(unittest.TestCase):
    def setUp(self):
        self.root = FakeRoot(self)
        for directory in ("usr/local/share", "etc/systemd/system"):
            self.root.directory(directory)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.stage = Path(temp.name)/"stage"
        self.stage.mkdir(mode=0o700)
        self.contents = {source: (ROOT/source).read_bytes() for source, _, _ in installer.FILES}
        for name, data in self.contents.items():
            path = self.stage/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.digest = self.write_manifest({name: sha256(data)
                                           for name, data in self.contents.items()})
        self.runner = Runner()
        self.output = []
        self.uid = os.getuid()

    def write_manifest(self, files, extra=None):
        body = {"format": 1, "files": files, **(extra or {})}
        data = json.dumps(body, sort_keys=True).encode()
        (self.stage/"manifest.json").write_bytes(data)
        return sha256(data)

    def context(self, euid=0):
        return installer.Context(root=str(self.root.path), owner=os.getuid(), run=self.runner,
                                 euid=euid, user_exists=lambda uid: True)

    def main(self, action="install", digest=None, env=None, euid=0, argv=None):
        argv = argv if argv is not None else [action, "--stage", str(self.stage),
                                              "--manifest-sha256", digest or self.digest]
        env = {"PKEXEC_UID": str(self.uid)} if env is None else env
        return installer.main(argv, env, self.context(euid), emit=self.output.append)

    def installed(self):
        return sorted(str(path.relative_to(self.root.path)) for path in self.root.path.rglob("*")
                      if path.is_file() or path.is_symlink())

    def systemctl_calls(self):
        return [call[1:] for call in self.runner.calls if call[0] == SYSTEMCTL]


class Install(Base):
    def test_copies_every_file_with_its_mode(self):
        self.assertEqual(self.main(), installer.EXIT_OK)
        self.assertEqual(self.output, [{"ok": True, "action": "install"}])
        self.assertEqual(self.installed(), sorted(TARGETS))
        for target, mode in TARGETS.items():
            with self.subTest(target=target):
                self.assertEqual(self.root.mode(target), mode)
        for directory in ("usr/local/libexec", "usr/local/libexec/system-device-monitor",
                          "usr/local/share/polkit-1", "usr/local/share/polkit-1/actions"):
            self.assertEqual(self.root.mode(directory), 0o755)
        helper = self.root.path/"usr/local/libexec/system-device-monitor/sdm-cpu-power"
        self.assertEqual(helper.read_bytes(), self.contents["helper/sdm-cpu-power"])

    def test_reloads_systemd_without_enabling_anything(self):
        self.main()
        self.assertEqual(self.systemctl_calls(), [("daemon-reload",),
                                                  ("is-enabled", "--quiet", SOCKET)])

    def test_restarts_an_enabled_socket(self):
        self.runner.enabled = True
        self.main()
        self.assertEqual(self.systemctl_calls()[-2:], [("stop", SERVICE), ("restart", SOCKET)])

    def test_reinstall_replaces_older_files(self):
        old = self.root.file("etc/systemd/system/" + SOCKET, "old\n")
        self.assertEqual(self.main(), installer.EXIT_OK)
        self.assertNotEqual(old.read_text(), "old\n")

    def test_refuses_while_the_package_is_installed(self):
        for status in ("install ok installed", "install ok unpacked", "hold ok installed"):
            with self.subTest(status=status):
                self.runner.package_status = status
                self.assertEqual(self.main(), installer.EXIT_FAILED)
                self.assertEqual(self.output[-1], {"ok": False, "error": "deb-installed"})
                self.assertEqual(self.installed(), [])

    def test_removed_package_does_not_block(self):
        for status in ("deinstall ok config-files", "unknown ok not-installed"):
            with self.subTest(status=status):
                self.runner.package_status = status
                self.assertEqual(self.main(), installer.EXIT_OK)


class Verification(Base):
    def assert_refused(self, **options):
        self.assertEqual(self.main(**options), installer.EXIT_FAILED)
        self.assertEqual(self.installed(), [])
        self.assertEqual(self.systemctl_calls(), [])

    def test_manifest_hash_mismatch(self):
        self.assert_refused(digest="0" * 64)

    def test_file_hash_mismatch_writes_nothing(self):
        (self.stage/"helper/systemd"/SERVICE).write_bytes(b"[Service]\nExecStart=/bin/sh\n")
        self.assert_refused()

    def test_manifest_must_list_exactly_the_known_files(self):
        files = {name: sha256(data) for name, data in self.contents.items()}
        cases = (dict(files, **{"helper/extra": "0" * 64}),
                 {name: value for name, value in files.items() if name != "helper/sdm-cpu-power"},
                 dict(files, **{"helper/sdm-cpu-power": "ZZ" * 32}))
        for case in cases:
            with self.subTest(keys=sorted(case)):
                self.assert_refused(digest=self.write_manifest(case))

    def test_manifest_format_is_checked(self):
        files = {name: sha256(data) for name, data in self.contents.items()}
        self.assert_refused(digest=self.write_manifest(files, {"format": 2}))
        data = b"not json"
        (self.stage/"manifest.json").write_bytes(data)
        self.assert_refused(digest=sha256(data))

    def test_symlinked_file_in_the_stage(self):
        path = self.stage/"helper/sdm-cpu-power"
        real = self.stage.parent/"elsewhere"
        real.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(real)
        self.assert_refused()

    def test_symlinked_directory_in_the_stage(self):
        policy = self.stage/"helper/policy"
        moved = self.stage.parent/"policy"
        policy.rename(moved)
        policy.symlink_to(moved)
        self.assert_refused()

    def test_stage_must_be_private_and_owned_by_the_caller(self):
        self.stage.chmod(0o770)
        self.assert_refused()
        self.stage.chmod(0o700)
        self.uid += 1
        self.assert_refused()

    def test_symlinked_stage(self):
        link = self.stage.parent/"link"
        link.symlink_to(self.stage)
        self.assertEqual(installer.main(["install", "--stage", str(link), "--manifest-sha256",
                                         self.digest], {"PKEXEC_UID": str(self.uid)},
                                        self.context(), emit=self.output.append),
                         installer.EXIT_FAILED)
        self.assertEqual(self.installed(), [])

    def test_oversized_file(self):
        big = b"x" * (installer.MAX_FILE_BYTES + 1)
        (self.stage/"helper/sdm-cpu-power").write_bytes(big)
        files = {name: sha256(data) for name, data in self.contents.items()}
        files["helper/sdm-cpu-power"] = sha256(big)
        self.assert_refused(digest=self.write_manifest(files))

    def test_untrusted_destination(self):
        os.chmod(self.root.path/"etc/systemd/system", 0o775)
        self.assertEqual(self.main(), installer.EXIT_FAILED)
        self.assertEqual([name for name in self.installed() if name.startswith("etc/")], [])

    def test_destination_of_the_wrong_type(self):
        self.root.directory("etc/systemd/system/" + SOCKET)
        self.assertEqual(self.main(), installer.EXIT_FAILED)


class Remove(Base):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.main(), installer.EXIT_OK)
        self.runner.calls.clear()
        self.output.clear()

    def test_removes_exactly_the_allowlisted_files(self):
        keep = [self.root.file("usr/local/libexec/other-tool"),
                self.root.file("usr/local/share/polkit-1/actions/org.example.policy"),
                self.root.file("etc/systemd/system/other.service"),
                self.root.file("etc/systemd/system/" + SOCKET + ".d/admin.conf")]
        self.root.file("etc/systemd/system/" + SOCKET + ".d/owner.conf")
        wants = self.root.directory("etc/systemd/system/sockets.target.wants")/SOCKET
        wants.symlink_to("/etc/systemd/system/" + SOCKET)
        self.assertEqual(self.main("remove"), installer.EXIT_OK)
        self.assertEqual(self.output, [{"ok": True, "action": "remove"}])
        self.assertEqual(self.installed(), sorted(str(path.relative_to(self.root.path))
                                                  for path in keep))
        self.assertFalse((self.root.path/"usr/local/libexec/system-device-monitor").exists())
        self.assertEqual(self.systemctl_calls(), [("disable", "--now", SOCKET), ("stop", SERVICE),
                                                  ("daemon-reload",)])

    def test_keeps_the_helper_directory_when_it_holds_other_files(self):
        other = self.root.file("usr/local/libexec/system-device-monitor/notes.txt")
        self.assertEqual(self.main("remove"), installer.EXIT_OK)
        self.assertTrue(other.exists())

    def test_removal_works_when_nothing_is_installed(self):
        self.assertEqual(self.main("remove"), installer.EXIT_OK)
        self.assertEqual(self.main("remove"), installer.EXIT_OK)

    def test_removal_is_allowed_while_the_package_is_installed(self):
        self.runner.package_status = "install ok installed"
        self.assertEqual(self.main("remove"), installer.EXIT_OK)
        self.assertEqual(self.installed(), [])

    def test_wants_entry_that_is_not_a_symlink_is_left_alone(self):
        entry = self.root.file("etc/systemd/system/sockets.target.wants/" + SOCKET)
        self.main("remove")
        self.assertTrue(entry.exists())


class Arguments(Base):
    def test_bad_arguments_exit_two(self):
        stage = str(self.stage)
        cases = ([], ["install"], ["upgrade", "--stage", stage, "--manifest-sha256", self.digest],
                 ["install", "--stage", "relative", "--manifest-sha256", self.digest],
                 ["install", "--stage", stage + "/../x", "--manifest-sha256", self.digest],
                 ["install", "--stage", stage + "/", "--manifest-sha256", self.digest],
                 ["install", "--stage", stage, "--manifest-sha256", self.digest.upper()],
                 ["install", "--stage", stage, "--manifest-sha256", self.digest[:-1]],
                 ["install", "--manifest-sha256", self.digest, "--stage", stage],
                 ["install", "--stage", stage, "--manifest-sha256", self.digest, "extra"])
        for argv in cases:
            with self.subTest(argv=argv):
                self.output.clear()
                self.assertEqual(self.main(argv=argv), installer.EXIT_USAGE)
                self.assertEqual(self.output, [{"ok": False, "error": "usage"}])
        self.assertEqual(self.runner.calls, [])

    def test_needs_root_through_pkexec(self):
        for options in ({"euid": 1000}, {"env": {}}, {"env": {"PKEXEC_UID": "0"}},
                        {"env": {"PKEXEC_UID": "x"}}):
            with self.subTest(options=options):
                self.output.clear()
                self.assertEqual(self.main(**options), installer.EXIT_NOT_PERMITTED)
                self.assertEqual(self.output, [{"ok": False, "error": "not-permitted"}])
        self.assertEqual(self.installed(), [])


class Consistency(unittest.TestCase):
    def test_targets_match_the_helper_layout(self):
        local = control.layout_for(CpuPower.LOCAL_HELPER)
        self.assertEqual((installer.SOCKET, installer.SERVICE), (local.socket, local.service))
        self.assertEqual({target: mode for _, target, mode in installer.FILES}, TARGETS)
        self.assertEqual("/" + installer.HELPER_DIR, CpuPower.LOCAL_HELPER_DIR)

    def test_sources_exist_and_are_public(self):
        manifest = json.loads((ROOT/"package-files.json").read_text(encoding="utf-8"))
        for source, _, _ in installer.FILES:
            self.assertIn(source, manifest["source"])
            self.assertTrue((ROOT/source).is_file())

    def test_script_is_self_contained(self):
        text = (ROOT/"scripts/install_system.py").read_text(encoding="utf-8")
        self.assertNotIn("import sdm", text)
        self.assertNotIn("from sdm", text)
        self.assertNotIn("sys.path", text)


if __name__ == "__main__":
    unittest.main()
