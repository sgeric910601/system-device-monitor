"""helper/sdm-update install：輸入檔、暫存複本、簽章、SHA256SUMS、dpkg-deb 檢查、dpkg 鎖與 apt-get。

假根目錄代替 /；dpkg-query、dpkg-deb、apt-get 由 FakeSystem 回答（openssl 與
dpkg --compare-versions 用真的），簽章用暫存目錄裡的一次性金鑰，經由 Context 注入。
"""
import hashlib
import os
from pathlib import Path
import shutil
import stat
import unittest

from sdm.constants import Update
from tests.fixtures.fakeroot import FakeRoot
from tests.fixtures.helpers import load_helper
from tests.fixtures.signing import TestKey, needs_openssl, private_dir, sums_text
from tests.fixtures.update_listing import LISTING

helper = load_helper("sdm-update")
USER = os.getuid()
NAME = "system-device-monitor_9.9.9_all.deb"
PACKAGE = b"synthetic package\n" * 512
STAGING = "var/cache/system-device-monitor/staging"
needs_dpkg = unittest.skipUnless(shutil.which("dpkg"), "needs dpkg --compare-versions")


class FakeSystem:
    """dpkg-query、dpkg-deb、apt-get 的假回應；openssl 與 dpkg 比較版本用真的。"""

    def __init__(self):
        self.calls, self.hooks = [], {}
        self.status, self.installed = "install ok installed", "0.2.0"
        self.fields = "Package: system-device-monitor\nArchitecture: all\nVersion: 9.9.9\n"
        self.listing, self.apt_code, self.apt_installs = LISTING, 0, "9.9.9"
        self.apt_timed_out, self.staged = False, None

    def __call__(self, argv, env=None, timeout=None, capture=False):
        self.calls.append((tuple(argv), dict(env or {}), timeout))
        tool = argv[0]
        if tool in self.hooks:
            self.hooks.pop(tool)(argv)
        if tool in (helper.OPENSSL, helper.DPKG):
            return helper.run_command(argv, env or helper.SAFE_ENV, timeout or 30, capture)
        if tool == helper.DPKG_QUERY:
            text = f"{self.status}\n{self.installed}\n"
            return helper.Result(0 if self.status else 1, text.encode() if self.status else b"")
        if tool == helper.DPKG_DEB:
            return helper.Result(0, (self.fields if argv[1] == "-f" else self.listing).encode())
        raise AssertionError("unexpected command " + tool)

    def run_apt(self, argv, env, budget):
        """apt-get 只經由 run_apt 執行（自己的程序群組、長的時限）。"""
        self.calls.append((tuple(argv), dict(env), budget))
        self.staged = Path(argv[-1]).read_bytes()
        if self.apt_code == 0 and not self.apt_timed_out:
            self.installed = self.apt_installs
        return helper.AptResult(self.apt_code, self.apt_timed_out)

    def tools(self):
        return [argv[0] for argv, _, _ in self.calls]


@needs_openssl
@needs_dpkg
class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        keys = private_dir(cls, for_class=True)
        cls.key, cls.other = TestKey(keys, "release"), TestKey(keys, "other")

    def setUp(self):
        self.root = FakeRoot(self)
        self.root.file(Update.HELPER.lstrip("/"), mode=0o755)
        self.root.directory("var/cache")
        self.root.file("var/lib/dpkg/lock-frontend", text="")
        self.user = private_dir(self)
        self.deb, self.sums, self.sig = (self.user/NAME, self.user/"SHA256SUMS",
                                         self.user/"SHA256SUMS.sig")
        self.write_release(PACKAGE)
        self.system, self.output, self.logged = FakeSystem(), [], []
        self.lock_states, self.slept = [], []

    def write_release(self, package, listed=NAME, key=None):
        self.deb.write_bytes(package)
        sums = sums_text((hashlib.sha256(package).hexdigest(), listed), ("cd" * 32, "x.tar.gz"))
        self.sums.write_bytes(sums)
        self.sig.write_bytes((key or self.key).signature_file(sums))

    def context(self, keys=None):
        clock = iter(range(0, 10 ** 6, 10))
        return helper.Context(root=str(self.root.path), owner=USER, run=self.system, euid=0,
                              user_exists=lambda uid: True, helper_path=Update.HELPER,
                              uid_range=(1, 2 ** 31),
                              keys=(self.key.public,) if keys is None else keys,
                              clock=lambda: next(clock), sleep=self.slept.append,
                              lock_free=self.lock_free, run_apt=self.system.run_apt)

    def lock_free(self, path):
        return self.lock_states.pop(0) if self.lock_states else True

    def run_helper(self, keys=None, uid=USER, paths=None):
        argv = ["install", *(str(path) for path in (paths or (self.deb, self.sums, self.sig)))]
        return helper.main(argv, {"PKEXEC_UID": str(uid)}, self.context(keys),
                           emit=self.output.append, report=self.logged.append)

    def failed(self, reason, **options):
        self.assertEqual(self.run_helper(**options), helper.EXIT_FAILED)
        self.assertEqual(self.output[-1], {"ok": False, "error": "failed", "reason": reason})
        self.assertEqual(self.logged[-1], "install failed")
        self.assertNotIn(helper.APT_GET, self.system.tools())
        self.assert_staging_empty()

    def assert_staging_empty(self):
        staging = self.root.path/STAGING
        if staging.exists():
            self.assertEqual(os.listdir(staging), [])


class Success(Base):
    def test_installs_the_staged_copy(self):
        self.assertEqual(self.run_helper(), helper.EXIT_OK)
        self.assertEqual(self.output, [{"ok": True, "action": "install", "version": "9.9.9"}])
        self.assertEqual(self.logged, ["install ok"])
        self.assertEqual(self.system.staged, PACKAGE)
        apt = [call for call in self.system.calls if call[0][0] == helper.APT_GET][0]
        argv, env, timeout = apt
        staged = argv[-1]
        self.assertTrue(staged.startswith(str(self.root.path/STAGING) + "/"))
        self.assertTrue(staged.endswith("/" + NAME))
        self.assertEqual(argv[:-1], (helper.APT_GET, "install", "-y", "-q",
                                     "--no-install-recommends", "--no-remove", "-o",
                                     "DPkg::Lock::Timeout=120"))
        self.assertEqual(env["DEBIAN_FRONTEND"], "noninteractive")
        self.assertEqual(env["PATH"], helper.SAFE_PATH)
        self.assertEqual(timeout, helper.APT_TIMEOUT_S)
        self.assertEqual(helper.APT_TIMEOUT_S, 30 * 60)
        self.assert_staging_empty()
        self.assertEqual(stat.S_IMODE(os.stat(self.root.path/STAGING).st_mode), 0o700)

    def test_commands_are_absolute_with_a_fixed_environment(self):
        self.run_helper()
        for argv, env, _ in self.system.calls:
            with self.subTest(tool=argv[0]):
                self.assertTrue(argv[0].startswith("/usr/bin/"))
                self.assertLessEqual(set(env), set(helper.APT_ENV))
                self.assertNotIn("PKEXEC_UID", env)
        order = [tool for tool in self.system.tools() if tool != helper.OPENSSL]
        self.assertEqual(order, [helper.DPKG_QUERY, helper.DPKG_DEB, helper.DPKG,
                                 helper.DPKG_DEB, helper.APT_GET, helper.DPKG_QUERY])
        self.assertLess(self.system.tools().index(helper.OPENSSL),
                        self.system.tools().index(helper.DPKG_DEB))

    def test_changing_the_original_after_the_copy_has_no_effect(self):
        self.system.hooks[helper.DPKG_DEB] = lambda argv: self.deb.write_bytes(b"evil")
        self.assertEqual(self.run_helper(), helper.EXIT_OK)
        self.assertEqual(self.system.staged, PACKAGE)

    def test_beta_packages(self):
        name = "system-device-monitor_9.9.9~beta.2_all.deb"
        self.deb = self.user/name
        self.write_release(PACKAGE, listed=name)
        self.system.fields = self.system.fields.replace("9.9.9", "9.9.9~beta.2")
        self.system.apt_installs = "9.9.9~beta.2"
        self.assertEqual(self.run_helper(), helper.EXIT_OK)

    def test_stale_runs_are_cleaned_and_other_entries_kept(self):
        stale = self.root.directory(STAGING + "/0123456789abcdef")
        (stale/NAME).write_bytes(b"old")
        os.chmod(self.root.path/STAGING, 0o700)
        keep = self.root.path/STAGING/"not-ours"
        keep.write_text("x")
        self.assertEqual(self.run_helper(), helper.EXIT_OK)
        self.assertEqual(os.listdir(self.root.path/STAGING), ["not-ours"])


class Inputs(Base):
    def test_symlinks_are_refused(self):
        real = self.user/"real.deb"
        self.deb.rename(real)
        self.deb.symlink_to(real)
        self.failed("input")

    def test_files_must_belong_to_the_caller(self):
        self.failed("input", uid=USER + 1)

    def test_only_regular_files(self):
        self.sums.unlink()
        os.mkfifo(self.sums)
        self.failed("input")
        os.unlink(self.sums)
        self.sums.mkdir()
        self.failed("input")

    def test_size_limits(self):
        with open(self.deb, "r+b") as handle:
            handle.truncate(Update.DEB_MAX_BYTES + 1)
        self.failed("input")
        self.write_release(PACKAGE)
        self.sig.write_bytes(b"A" * 1025)
        self.failed("input")
        self.write_release(PACKAGE)
        self.deb.write_bytes(b"")
        self.failed("input")

    def test_missing_files(self):
        self.sig.unlink()
        self.failed("input")

    def test_inputs_are_checked_before_the_keys(self):
        self.sig.unlink()
        self.failed("input", keys=())
        self.write_release(PACKAGE)
        self.failed("no-keys", keys=())
        self.failed("no-keys", keys=("garbage",))


class Installation(Base):
    def test_package_must_be_installed_from_a_deb(self):
        for status, version in (("", "0.2.0"), ("deinstall ok config-files", "0.2.0"),
                                ("install ok installed", "-1"),
                                ("install ok installed", "0.2.0 --force")):
            with self.subTest(status=status, version=version):
                self.system.status, self.system.installed = status, version
                self.failed("not-deb")

    def test_staging_must_be_private(self):
        self.root.directory(STAGING)
        os.chmod(self.root.path/STAGING, 0o755)
        self.failed("staging")
        os.chmod(self.root.path/STAGING, 0o700)
        os.chmod(self.root.path/"var/cache", 0o777)
        self.failed("staging")

    def test_staging_symlink_is_refused(self):
        target = self.root.directory("elsewhere")
        self.root.directory("var/cache/system-device-monitor")
        os.symlink(target, self.root.path/STAGING)
        self.failed("staging")
        self.assertEqual(os.listdir(target), [])


class Verification(Base):
    def test_signature_by_another_key(self):
        self.write_release(PACKAGE, key=self.other)
        self.failed("signature")

    def test_garbage_signature(self):
        self.sig.write_bytes(b"not base64\n")
        self.failed("signature")

    def test_signature_checked_before_any_package_command(self):
        self.write_release(PACKAGE, key=self.other)
        self.failed("signature")
        self.assertNotIn(helper.DPKG_DEB, self.system.tools())

    def test_checksum_must_list_this_exact_file(self):
        for listed in ("other.deb", "./" + NAME, NAME + ".part"):
            with self.subTest(listed=listed):
                self.write_release(PACKAGE, listed=listed)
                self.failed("checksum")

    def test_package_content_must_match_the_signed_hash(self):
        self.write_release(PACKAGE)
        self.deb.write_bytes(PACKAGE + b"tampered")
        self.failed("checksum")


class PackageFields(Base):
    def test_fields_must_match(self):
        good = self.system.fields
        for fields in (good.replace("system-device-monitor", "other"),
                       good.replace("all", "amd64"), good.replace("9.9.9", "9.9.8"),
                       good + "Version: 9.9.9\n", good.replace("Version: 9.9.9\n", ""),
                       good + " continued\n", ""):
            with self.subTest(fields=fields):
                self.system.fields = fields
                self.failed("package")

    def test_version_must_be_newer_than_the_installed_one(self):
        for installed in ("9.9.9", "10.0.0", "9.9.10~beta.1"):
            with self.subTest(installed=installed):
                self.system.installed = installed
                self.failed("not-newer")

    def test_contents_outside_the_allowlist(self):
        self.system.listing = LISTING + ("-rw-r--r-- root/root 10 1970-01-01 00:00 "
                                         "./etc/sudoers.d/x\n")
        self.failed("contents")


class LockAndApt(Base):
    def test_waits_for_the_dpkg_lock(self):
        self.lock_states = [False, False, True]
        self.assertEqual(self.run_helper(), helper.EXIT_OK)
        self.assertEqual(self.slept, [helper.LOCK_POLL_S, helper.LOCK_POLL_S])

    def test_lock_timeout_fails_cleanly(self):
        self.lock_states = [False] * 1000
        self.failed("busy")
        self.assertGreaterEqual(len(self.slept), 1)

    def test_apt_failure(self):
        self.system.apt_code = 100
        self.assertEqual(self.run_helper(), helper.EXIT_FAILED)
        self.assertEqual(self.output[-1]["reason"], "apt")
        self.assert_staging_empty()

    def test_apt_running_past_its_budget_is_reported(self):
        self.system.apt_timed_out = True
        self.assertEqual(self.run_helper(), helper.EXIT_FAILED)
        self.assertEqual(self.output[-1]["reason"], "apt-timeout")
        self.assert_staging_empty()

    def test_installed_version_is_checked_after_apt(self):
        self.system.apt_installs = "0.2.0"
        self.assertEqual(self.run_helper(), helper.EXIT_FAILED)
        self.assertEqual(self.output[-1]["reason"], "apt")
        self.assert_staging_empty()


if __name__ == "__main__":
    unittest.main()
