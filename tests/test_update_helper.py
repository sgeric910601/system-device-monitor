"""一鍵更新的管理員小程式（helper/sdm-update）：參數、權限、位置、常數，以及與程式端相同的
簽章與 SHA256SUMS 解析。

以假根目錄（擁有者＝執行測試的使用者）代替 /；不需要 root，也不執行 apt-get 或 dpkg。
"""
import base64
import os
import stat
import sys
import unittest
from unittest import mock

from sdm.constants import CpuPower, Update
from sdm.core import update_logic
from sdm.services import update_verify
from tests.fixtures.fakeroot import FakeRoot
from tests.fixtures.helpers import HELPERS, git_mode, load_helper
from tests.fixtures.signing import TestKey, needs_openssl, private_dir, sums_text
from tests.test_system_units import imported_modules

helper = load_helper("sdm-update")
USER = os.getuid()
DEB = "/var/tmp/x/system-device-monitor_9.9.9_all.deb"
SUMS = "/var/tmp/x/SHA256SUMS"
SIG = "/var/tmp/x/SHA256SUMS.sig"
ENV = {"PKEXEC_UID": str(USER)}


def recorder(calls):
    def run(argv, env=None, timeout=None, capture=False):
        calls.append(tuple(argv))
        return helper.Result(1, b"")
    return run


class Base(unittest.TestCase):
    def setUp(self):
        self.root = FakeRoot(self)
        self.root.file(Update.HELPER.lstrip("/"), mode=0o755)
        self.root.directory("var/cache")
        self.calls, self.output, self.logged = [], [], []

    def context(self, euid=0, known=lambda uid: True, helper_path=Update.HELPER, keys=()):
        return helper.Context(root=str(self.root.path), owner=USER, run=recorder(self.calls),
                              euid=euid, user_exists=known, helper_path=helper_path,
                              uid_range=(1, 2 ** 31), keys=keys)

    def main(self, argv, env=ENV, **options):
        return helper.main(argv, dict(env), self.context(**options), emit=self.output.append,
                           report=self.logged.append)


class Arguments(Base):
    def test_bad_arguments_exit_two(self):
        cases = ([], [""], ["bogus"], ["install"], ["install", DEB], ["install", DEB, SUMS],
                 ["install", DEB, SUMS, SIG, "extra"], ["INSTALL", DEB, SUMS, SIG],
                 ["install", DEB, SIG, SUMS], ["install", SUMS, DEB, SIG],
                 ["--protocol", "extra"], ["-h"], ["--help"], ["status"])
        for argv in cases:
            with self.subTest(argv=argv):
                self.output.clear()
                self.assertEqual(self.main(argv), helper.EXIT_USAGE)
                self.assertEqual(self.output, [{"ok": False, "error": "usage"}])
        self.assertEqual(self.calls, [])

    def test_file_names_must_match_exactly(self):
        base = "/var/tmp/y/"
        for deb in ("system-device-monitor_9.9.9_amd64.deb", "system-device-monitor_9.9_all.deb",
                    "system-device-monitor_9.9.9-1_all.deb", "system-device-monitor_9.9.9~rc1_all.deb",
                    "other_9.9.9_all.deb", "system-device-monitor_9.9.9_all.deb.part",
                    "system-device-monitor_٩.9.9_all.deb", "system-device-monitor_9.9.9~beta._all.deb",
                    "system-device-monitor_9.9.9_all.DEB", " system-device-monitor_9.9.9_all.deb"):
            with self.subTest(deb=deb):
                self.assertEqual(self.main(["install", base + deb, SUMS, SIG]), helper.EXIT_USAGE)
        for sums, sig in ((base + "SHA256SUMS.txt", SIG), (SUMS, base + "SHA256SUMS.asc"),
                          (base + "sha256sums", SIG)):
            with self.subTest(sums=sums, sig=sig):
                self.assertEqual(self.main(["install", DEB, sums, sig]), helper.EXIT_USAGE)

    def test_paths_must_be_absolute_and_normalized(self):
        name = "system-device-monitor_9.9.9_all.deb"
        for deb in (name, "x/" + name, "/x/../" + name, "/x/./" + name, "//x/" + name,
                    "/x//" + name, "/x/\n/" + name):
            with self.subTest(deb=deb):
                self.assertEqual(self.main(["install", deb, SUMS, SIG]), helper.EXIT_USAGE)

    def test_beta_names_are_accepted(self):
        request = helper.parse_args(["install", "/x/system-device-monitor_1.2.3~beta.4_all.deb",
                                     "/x/SHA256SUMS", "/x/SHA256SUMS.sig"])
        self.assertEqual(request.version, "1.2.3~beta.4")

    def test_protocol_needs_no_privileges(self):
        self.assertEqual(self.main(["--protocol"], env={}, euid=1000), helper.EXIT_OK)
        self.assertEqual(self.output, [{"protocol": Update.PROTOCOL, "version": helper.VERSION}])
        self.assertEqual(self.logged, [])


class Permissions(Base):
    ARGV = ["install", DEB, SUMS, SIG]

    def assert_refused(self, **options):
        self.output.clear()
        self.assertEqual(self.main(self.ARGV, **options), helper.EXIT_NOT_PERMITTED)
        self.assertEqual(self.output, [{"ok": False, "error": "not-permitted"}])

    def test_needs_root_and_pkexec(self):
        self.assert_refused(euid=1000)
        self.assert_refused(env={})
        for value in ("", "abc", "0", "-1", " 1000", "01000", "65534", "4294967295", "1e3", "١٠٠٠"):
            with self.subTest(value=value):
                self.assert_refused(env={"PKEXEC_UID": value})
        self.assert_refused(known=lambda uid: False)
        self.assertEqual(self.calls, [])
        self.assertEqual(set(self.logged), {"install not-permitted"})

    def test_keys_never_come_from_the_environment(self):
        key = base64.b64encode(bytes(32)).decode()
        env = dict(ENV, PUBLIC_KEYS=key, SDM_PUBLIC_KEYS=key, SDM_UPDATE_KEYS=key)
        with mock.patch.dict(os.environ, env):
            self.assertIs(helper.production_context().keys, helper.PUBLIC_KEYS)


class Location(Base):
    def failed(self, **options):
        self.output.clear()
        self.assertEqual(self.main(Permissions.ARGV, **options), helper.EXIT_FAILED)
        return self.output[0]

    def test_only_the_package_location_is_accepted(self):
        for path in ("/usr/local/libexec/system-device-monitor/sdm-update", "/tmp/sdm-update",
                     "usr/libexec/system-device-monitor/sdm-update",
                     "/usr/libexec/system-device-monitor//sdm-update",
                     "/usr/libexec/system-device-monitor/../system-device-monitor/sdm-update"):
            with self.subTest(path=path):
                self.assertEqual(self.failed(helper_path=path)["reason"], "location")

    def test_helper_and_its_directories_must_be_trusted(self):
        self.root.path.joinpath(Update.HELPER.lstrip("/")).chmod(0o775)
        self.assertEqual(self.failed()["reason"], "location")
        self.root.path.joinpath(Update.HELPER.lstrip("/")).chmod(0o755)
        os.chmod(self.root.path/"usr/libexec", 0o777)
        self.assertEqual(self.failed()["reason"], "location")

    def test_helper_must_be_a_regular_file(self):
        installed = self.root.path/Update.HELPER.lstrip("/")
        real = self.root.file("usr/libexec/system-device-monitor/real", mode=0o755)
        installed.unlink()
        installed.symlink_to(real)
        self.assertEqual(self.failed()["reason"], "location")
        installed.unlink()
        self.assertEqual(self.failed()["reason"], "location")

    def test_failures_are_logged_without_details(self):
        self.failed(helper_path="/tmp/sdm-update")
        self.assertEqual(self.logged, ["install failed"])


class Constants(unittest.TestCase):
    path = HELPERS/"sdm-update"

    def test_runs_isolated_without_bytecode(self):
        self.assertEqual(self.path.read_text(encoding="utf-8").splitlines()[0],
                         "#!/usr/bin/python3 -IB")
        self.assertTrue(self.path.stat().st_mode & stat.S_IXUSR)
        self.assertIn(git_mode(self.path), (None, "100755"))

    def test_imports_only_the_standard_library(self):
        names = imported_modules(self.path)
        self.assertEqual(sorted(names - set(sys.stdlib_module_names)), [])
        self.assertEqual(sorted(names & {"ctypes", "importlib", "shutil", "pickle", "urllib",
                                         "socket", "http"}), [])

    def test_constants_match_the_application(self):
        self.assertEqual(helper.PUBLIC_KEYS, Update.PUBLIC_KEYS)
        self.assertEqual(helper.PROTOCOL, Update.PROTOCOL)
        self.assertEqual(helper.HELPER_PATH, Update.HELPER)
        self.assertEqual(helper.HELPER_DIR, CpuPower.DEB_HELPER_DIR)
        self.assertEqual(helper.PACKAGE, Update.PACKAGE)
        self.assertEqual((helper.CHECKSUMS_NAME, helper.SIGNATURE_NAME),
                         (Update.CHECKSUMS, Update.SIGNATURE))
        self.assertEqual((helper.DEB_MAX_BYTES, helper.CHECKSUMS_MAX_BYTES,
                          helper.SIGNATURE_MAX_BYTES),
                         (Update.DEB_MAX_BYTES, Update.CHECKSUMS_MAX_BYTES,
                          Update.SIGNATURE_MAX_BYTES))
        self.assertEqual(helper.OPENSSL, Update.OPENSSL)
        self.assertEqual(helper.SPKI_PREFIX, update_verify.SPKI_PREFIX)
        self.assertEqual((helper.EXIT_OK, helper.EXIT_USAGE, helper.EXIT_FAILED,
                          helper.EXIT_NOT_PERMITTED), CpuPower.EXIT_CODES)
        self.assertEqual(helper.APT_TIMEOUT_S, 300)
        self.assertEqual(helper.LOCK_TIMEOUT_S, 120)

    def test_reasons_are_fixed_words(self):
        for reason in helper.REASONS:
            self.assertRegex(reason, r"^[a-z-]+$")


class SameAsTheApplication(unittest.TestCase):
    """小程式自己的解析與程式端（sdm.core.update_logic、sdm.services.update_verify）相同。"""
    NAME = "system-device-monitor_1.5.0_all.deb"

    def test_checksum_lookup(self):
        digest = "a1" * 32
        cases = (f"{digest}  {self.NAME}\n", f"{digest.upper()} *{self.NAME}\r\n",
                 f"{digest}  {self.NAME}.evil\n", f"{digest}  ./{self.NAME}\n",
                 f"{digest}  {self.NAME}\n{'b2' * 32}  {self.NAME}\n",
                 f"{digest}  {self.NAME}\n" * 2, "zzzz  " + self.NAME, "", f"{digest}\t{self.NAME}")
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(helper.checksum(text, self.NAME),
                                 update_logic.checksum(text, self.NAME))

    def test_key_and_signature_decoding(self):
        key = base64.b64encode(bytes(range(32))).decode()
        for text in (key, "", "!!!", key[:-4], key + "\n", None):
            with self.subTest(key=text):
                self.assertEqual(helper.decode_key(text), update_verify.decode_key(text))
        line = base64.b64encode(bytes(64))
        for data in (line, line + b"\n", line + b"\r\n", b"", b"!!!\n", line + b"\n\n",
                     base64.b64encode(bytes(63)) + b"\n", b" " + line):
            with self.subTest(signature=data):
                self.assertEqual(helper.decode_signature(data),
                                 update_verify.decode_signature(data))
        self.assertEqual(helper.public_key_pem(bytes(range(32))),
                         update_verify.public_key_pem(bytes(range(32))))

    def test_file_name_pattern_matches_the_release_asset_names(self):
        for tag in ("v0.3.0", "v0.3.0-beta.1", "v10.20.30-beta.12"):
            with self.subTest(tag=tag):
                name = update_logic.asset_name(update_logic.AppVersion.parse(tag))
                match = helper.DEB_PATTERN.fullmatch(name)
                self.assertIsNotNone(match)
                self.assertEqual(match.group(1), update_logic.debian_version(
                    update_logic.AppVersion.parse(tag)))


@needs_openssl
class Signatures(unittest.TestCase):
    """小程式的簽章驗證：與程式端相同的案例（有效、竄改、錯的金鑰、壞簽章、換金鑰、沒有金鑰）。"""

    @classmethod
    def setUpClass(cls):
        keys = private_dir(cls, for_class=True)
        cls.key, cls.old = TestKey(keys, "release"), TestKey(keys, "old")

    def setUp(self):
        self.dir = private_dir(self)
        self.sums = self.dir/"SHA256SUMS"
        self.data = sums_text(("ab" * 32, "system-device-monitor_9.9.9_all.deb"))
        self.sums.write_bytes(self.data)
        self.work = os.open(self.dir, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.work)

    def check(self, signature, keys, run=None):
        raw = [value for value in (helper.decode_key(key) for key in keys) if value is not None]
        stage = helper.Stage(self.work, str(self.dir))
        return helper.verify_signature(run or helper.run_command, str(self.sums), signature,
                                       raw, stage)

    def test_cases(self):
        good = self.key.signature_file(self.data)
        cases = ((good, (self.key.public,), True), (good, (self.old.public,), False),
                 (good, (self.old.public, self.key.public), True), (good, (), False),
                 (b"", (self.key.public,), False), (b"garbage\n", (self.key.public,), False),
                 (base64.b64encode(bytes(64)) + b"\n", (self.key.public,), False))
        for signature, keys, expected in cases:
            with self.subTest(signature=signature[:12], keys=len(keys)):
                self.assertEqual(self.check(signature, keys), expected)
                self.assertEqual(sorted(os.listdir(self.dir)), ["SHA256SUMS"])

    def test_tampered_checksums(self):
        good = self.key.signature_file(self.data)
        self.sums.write_bytes(self.data.replace(b"ab", b"ac", 1))
        self.assertFalse(self.check(good, (self.key.public,)))

    def test_openssl_arguments(self):
        calls = []
        self.check(self.key.signature_file(self.data), (self.key.public,), run=recorder(calls))
        argv = calls[0]
        self.assertEqual(argv[:5], (Update.OPENSSL, "pkeyutl", "-verify", "-pubin", "-inkey"))
        self.assertEqual(argv[6:9], ("-rawin", "-in", str(self.sums)))
        self.assertEqual(argv[9], "-sigfile")


if __name__ == "__main__":
    unittest.main()
