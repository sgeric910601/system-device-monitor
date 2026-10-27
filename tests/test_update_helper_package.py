"""helper/sdm-update 的套件檢查：dpkg-deb -f／-c 的解析、允許的安裝位置、真正的 dpkg-deb 輸出、
本專案實際打包的內容，以及 dpkg 前端鎖（真的 fcntl 記錄鎖）。"""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from tests.fixtures.helpers import ROOT, load_helper, load_script
from tests.fixtures.update_listing import LISTING, entry

helper = load_helper("sdm-update")
needs_dpkg_deb = unittest.skipUnless(shutil.which("dpkg-deb"), "needs dpkg-deb")


def accepted(text):
    return helper.contents_allowed(text.encode("utf-8"))


class Listing(unittest.TestCase):
    def test_fixture_is_accepted(self):
        self.assertTrue(accepted(LISTING))

    def test_special_files_and_permissions_are_refused(self):
        path = "./usr/share/system-device-monitor/x"
        for line in (f"-rwsr-xr-x root/root 1 1970-01-01 00:00 {path}",
                     f"-rwxr-sr-x root/root 1 1970-01-01 00:00 {path}",
                     f"-rwSr--r-- root/root 1 1970-01-01 00:00 {path}",
                     "drwxrwxrwt root/root 0 1970-01-01 00:00 ./usr/share/system-device-monitor/t/",
                     f"-rw-rw-r-- root/root 1 1970-01-01 00:00 {path}",
                     f"-rw-r--rw- root/root 1 1970-01-01 00:00 {path}",
                     f"lrwxrwxrwx root/root 0 1970-01-01 00:00 {path} -> /etc/shadow",
                     f"hrw-r--r-- root/root 0 1970-01-01 00:00 {path} link to ./usr/bin/x",
                     f"crw-r--r-- root/root 1,3 1970-01-01 00:00 {path}",
                     f"brw-r--r-- root/root 8,0 1970-01-01 00:00 {path}",
                     f"prw-r--r-- root/root 0 1970-01-01 00:00 {path}",
                     f"srw-r--r-- root/root 0 1970-01-01 00:00 {path}",
                     f"-rw-r--r-- eric/eric 1 1970-01-01 00:00 {path}",
                     f"-rw-r--r-- root/adm 1 1970-01-01 00:00 {path}",
                     f"--w-r--r-- root/root 1 1970-01-01 00:00 {path}"):
            with self.subTest(line=line):
                self.assertFalse(accepted(LISTING + line + "\n"))

    def test_paths_outside_the_allowlist_are_refused(self):
        for kind, path in (("f", "etc/sudoers.d/x"), ("f", "usr/bin/other"),
                           ("f", "usr/bin/system-device-monitor-evil"), ("d", "etc/"),
                           ("d", "usr/local/"), ("f", "usr/share/system-device-monitor"),
                           ("f", "usr/share/system-device-monitor/../../../etc/x"),
                           ("f", "usr/share/system-device-monitor/./x"),
                           ("f", "usr/lib/systemd/system/other.service"),
                           ("f", "usr/lib/systemd/user/system-device-monitor.service"),
                           ("f", "usr/share/polkit-1/rules.d/tw.local.system-device-monitor.rules"),
                           ("f", "usr/share/polkit-1/actions/org.evil.policy"),
                           ("f", "usr/share/icons/hicolor/48x48/apps/other.png"),
                           ("f", "usr/share/icons/hicolor/48x48/system-device-monitor.png"),
                           ("f", "usr/share/applications/evil.desktop"),
                           ("f", "usr/share/system-device-monitor/with space.py"),
                           ("f", "usr/share/system-device-monitor/ünicode.py"),
                           ("f", "usr/share/system-device-monitor/dir/"),
                           ("d", "usr/share/system-device-monitor/file"),
                           ("d", "usr/lib/udev/"), ("f", "lib/systemd/system/x.service")):
            with self.subTest(path=path):
                self.assertFalse(accepted(LISTING + entry(kind, path, 1)))

    def test_allowed_variants(self):
        for kind, path in (("x", "usr/libexec/system-device-monitor/sdm-charge-limit"),
                           ("f", "usr/share/polkit-1/actions/"
                                 "tw.local.system-device-monitor.unattended.policy"),
                           ("f", "usr/lib/systemd/system/system-device-monitor-wake@.service"),
                           ("d", "usr/share/icons/hicolor/scalable/"),
                           ("d", "usr/share/icons/hicolor/scalable/apps/"),
                           ("f", "usr/share/icons/hicolor/512x512/apps/system-device-monitor.png"),
                           ("f", "usr/share/doc/system-device-monitor/copyright")):
            with self.subTest(path=path):
                self.assertTrue(accepted(LISTING + entry(kind, path, 1)))

    def test_listing_shape(self):
        self.assertFalse(accepted(""))
        self.assertFalse(accepted(entry("d", "") + entry("d", "usr/")))     # 沒有任何檔案
        self.assertFalse(accepted(LISTING.replace("\n", "\r\n")))
        self.assertFalse(helper.contents_allowed(LISTING.encode() + "é".encode()))
        many = entry("d", "") + entry("d", "usr/") + entry("d", "usr/share/") + \
            entry("d", "usr/share/system-device-monitor/") + "".join(
                entry("f", f"usr/share/system-device-monitor/{n}.py", 1)
                for n in range(helper.MAX_ENTRIES))
        self.assertFalse(accepted(many))


class Fields(unittest.TestCase):
    GOOD = b"Package: system-device-monitor\nArchitecture: all\nVersion: 1.2.3\n"

    def test_parse(self):
        self.assertEqual(helper.parse_fields(self.GOOD),
                         {"Package": "system-device-monitor", "Architecture": "all",
                          "Version": "1.2.3"})
        for data in (self.GOOD + b"Version: 1.2.4\n", self.GOOD + b" continued\n",
                     self.GOOD.replace(b": ", b":"), b"Depends: x\n", "Version: ١\n".encode(),
                     self.GOOD.replace(b"1.2.3", b"1.2.3 extra")):
            with self.subTest(data=data):
                self.assertIsNone(helper.parse_fields(data))


@needs_dpkg_deb
class RealDpkgDeb(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)

    def build(self, stage):
        output = self.base/"package.deb"
        subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(stage),
                        str(output)], check=True, capture_output=True)
        return output

    def test_real_output_of_a_tiny_package(self):
        stage = self.base/"stage"
        (stage/"DEBIAN").mkdir(parents=True)
        (stage/"DEBIAN/control").write_text(
            "Package: system-device-monitor\nVersion: 9.9.9~beta.1\nArchitecture: all\n"
            "Maintainer: Example <nobody@example.invalid>\nDescription: test\n test\n")
        launcher = stage/"usr/bin/system-device-monitor"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("#!/bin/sh\n")
        for path in [stage, *stage.rglob("*")]:
            path.chmod(0o755 if path.is_dir() or path == launcher else 0o644)
        deb = str(self.build(stage))
        fields = helper.run_command([helper.DPKG_DEB, "-f", deb, "Package", "Architecture",
                                     "Version"], capture=True)
        self.assertEqual(helper.parse_fields(fields.output),
                         {"Package": "system-device-monitor", "Architecture": "all",
                          "Version": "9.9.9~beta.1"})
        listing = helper.run_command([helper.DPKG_DEB, "-c", deb], capture=True)
        self.assertEqual(listing.code, 0)
        self.assertTrue(helper.contents_allowed(listing.output))

    def test_the_shipped_package_passes_the_contents_check(self):
        build = load_script("build_deb")
        manifest = json.loads((ROOT/"package-files.json").read_text(encoding="utf-8"))
        stage = self.base/"stage"
        stage.mkdir()
        build.stage_package(manifest, stage, root=ROOT, version="9.9.9", epoch=0)
        listing = helper.run_command([helper.DPKG_DEB, "-c", str(self.build(stage))],
                                     capture=True)
        entries = helper.listing_entries(listing.output)
        self.assertIsNotNone(entries)
        refused = [path for kind, path in entries if not helper.path_allowed(kind, path)]
        self.assertEqual(refused, [])


class RunApt(unittest.TestCase):
    """apt-get 的執行器：自己的程序群組；超過時限只送 SIGTERM 給整個群組，絕不送 SIGKILL。"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def alive(self, pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def child_pid(self):
        path = self.dir/"child.pid"
        for _ in range(100):
            if path.exists() and path.read_text().strip():
                return int(path.read_text())
            time.sleep(0.02)
        raise AssertionError("child did not start")

    def test_exit_code_within_the_budget(self):
        result = helper.run_apt(["/bin/sh", "-c", "exit 7"], helper.APT_ENV, 5)
        self.assertEqual(result, helper.AptResult(7, False))

    def test_budget_sends_sigterm_to_the_whole_group(self):
        script = f"sleep 30 & echo $! > {self.dir}/child.pid; wait"
        started = time.monotonic()
        result = helper.run_apt(["/bin/sh", "-c", script], helper.APT_ENV, 0.5, grace=5)
        self.assertEqual(result, helper.AptResult(-1, True))
        child = self.child_pid()
        for _ in range(100):
            if not self.alive(child):
                break
            time.sleep(0.02)
        self.assertFalse(self.alive(child))
        self.assertLess(time.monotonic() - started, 5)

    def test_a_process_ignoring_sigterm_is_never_killed(self):
        script = f"trap '' TERM; echo $$ > {self.dir}/child.pid; sleep 3; exit 0"
        result = helper.run_apt(["/bin/sh", "-c", script], helper.APT_ENV, 0.3, grace=0.3)
        self.assertEqual(result, helper.AptResult(-1, True))
        pid = self.child_pid()
        self.assertTrue(self.alive(pid))        # 沒有被強制結束
        os.killpg(pid, signal.SIGKILL)          # 測試自己清理

    def test_missing_program(self):
        self.assertEqual(helper.run_apt(["/nonexistent/apt-get"], helper.APT_ENV, 1),
                         helper.AptResult(-1, False))


class DpkgLock(unittest.TestCase):
    """真的 fcntl 記錄鎖：由另一個程序持有（同一個程序的記錄鎖不會互斥）。"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root/helper.DPKG_LOCK
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"")

    def hold(self):
        code = ("import fcntl, os, sys, time\n"
                "fd = os.open(sys.argv[1], os.O_RDWR)\n"
                "fcntl.lockf(fd, fcntl.LOCK_EX)\n"
                "print('locked', flush=True)\n"
                "sys.stdin.read()\n")
        process = subprocess.Popen([sys.executable, "-I", "-c", code, str(self.path)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.addCleanup(process.wait)
        self.addCleanup(process.stdin.close)
        self.assertEqual(process.stdout.readline(), b"locked\n")
        return process

    def test_free_held_and_missing(self):
        self.assertTrue(helper.lock_free(str(self.path)))
        process = self.hold()
        self.assertFalse(helper.lock_free(str(self.path)))
        process.stdin.close()
        process.wait()
        self.assertTrue(helper.lock_free(str(self.path)))
        self.assertTrue(helper.lock_free(str(self.path.with_name("missing"))))

    def test_symlinked_lock_is_not_followed(self):
        link = self.path.with_name("link")
        link.symlink_to(self.path)
        self.assertFalse(helper.lock_free(str(link)))

    def test_waiting_gives_up_after_the_timeout(self):
        process = self.hold()
        now = [0.0]
        context = helper.Context(root=str(self.root), owner=os.getuid(), run=None, euid=0,
                                 user_exists=None, helper_path="",
                                 clock=lambda: now[0],
                                 sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
        started = time.monotonic()
        with self.assertRaises(helper.Failed) as caught:
            helper.wait_for_lock(context)
        self.assertEqual(caught.exception.reason, "busy")
        self.assertEqual(now[0], helper.LOCK_TIMEOUT_S)
        self.assertLess(time.monotonic() - started, 5)
        process.stdin.close()


if __name__ == "__main__":
    unittest.main()
