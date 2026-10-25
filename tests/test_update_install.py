"""一鍵更新的下載與驗證（程式端，工作執行緒）：私人暫存目錄、先驗證簽章、再比對 .deb 的雜湊、
dpkg-deb 欄位與版本，以及更新元件的通訊協定。網路以假的用戶端代替，簽章用一次性金鑰。"""
import hashlib
import os
from pathlib import Path
import stat
import unittest

from sdm.constants import Update
from sdm.core.update_logic import ReleaseInfo
from sdm.data.github import FetchError
from sdm.services import update_install as install
from sdm.services import update_verify
from tests.fixtures.signing import TestKey, needs_openssl, private_dir, sums_text

SITE = "https://github.com/sgeric910601/system-device-monitor/releases/download/v9.9.9/"
NAME = "system-device-monitor_9.9.9_all.deb"
PACKAGE = b"synthetic package\n" * 100


def release(signed=True, size=len(PACKAGE)):
    return ReleaseInfo(tag="v9.9.9", numbers=(9, 9, 9), page_url=SITE, asset_name=NAME,
                       asset_url=SITE + NAME, asset_size=size,
                       checksum_url=SITE + "SHA256SUMS" if signed else None,
                       signature_url=SITE + "SHA256SUMS.sig" if signed else None)


class FakeClient:
    def __init__(self, files):
        self.files, self.requested = files, []

    def get(self, url, limit, deadline_s=None, accept=None):
        self.requested.append(url)
        data = self.files[url]
        if isinstance(data, Exception):
            raise data
        if len(data) > limit:
            raise FetchError("too large")
        return data

    def download(self, url, path, limit, deadline_s=None):
        data = self.get(url, limit)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        return hashlib.sha256(data).hexdigest()


class Workdir(unittest.TestCase):
    def setUp(self):
        self.runtime = private_dir(self)

    def test_private_directory_under_the_runtime_dir(self):
        work = install.private_workdir({"XDG_RUNTIME_DIR": str(self.runtime)}, os.getuid())
        self.assertEqual(work.parent, self.runtime)
        self.assertEqual(stat.S_IMODE(work.stat().st_mode), 0o700)

    def test_paths_handed_to_the_helper_are_normalized(self):
        for value in (str(self.runtime) + "/", str(self.runtime) + "/.", "/" + str(self.runtime)):
            with self.subTest(value=value):
                work = install.private_workdir({"XDG_RUNTIME_DIR": value}, os.getuid())
                self.assertEqual(os.path.normpath(work), str(work))
                self.assertFalse(str(work).startswith("//"))

    def test_refuses_without_a_private_runtime_dir(self):
        shared = self.runtime/"shared"
        shared.mkdir(mode=0o777)
        shared.chmod(0o777)
        for environ in ({}, {"XDG_RUNTIME_DIR": ""}, {"XDG_RUNTIME_DIR": "relative"},
                        {"XDG_RUNTIME_DIR": str(shared)},
                        {"XDG_RUNTIME_DIR": str(self.runtime/"missing")}):
            with self.subTest(environ=environ):
                with self.assertRaises(install.UpdateError):
                    install.private_workdir(environ, os.getuid())
        with self.assertRaises(install.UpdateError):
            install.private_workdir({"XDG_RUNTIME_DIR": str(self.runtime)}, os.getuid() + 1)


@needs_openssl
class Prepare(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        keys = private_dir(cls, for_class=True)
        cls.key, cls.other = TestKey(keys, "release"), TestKey(keys, "other")

    def setUp(self):
        self.work = private_dir(self)
        self.sums = sums_text((hashlib.sha256(PACKAGE).hexdigest(), NAME), ("cd" * 32, "x.tar.gz"))
        self.client = FakeClient({SITE + "SHA256SUMS": self.sums,
                                  SITE + "SHA256SUMS.sig": self.key.signature_file(self.sums),
                                  SITE + NAME: PACKAGE})
        self.fields = {"Package": "system-device-monitor", "Architecture": "all",
                       "Version": "9.9.9"}
        self.newer, self.protocol, self.compared = True, Update.PROTOCOL, []

    def tools(self):
        def newer(candidate, installed):
            self.compared.append((candidate, installed))
            return self.newer
        return install.Tools(verify=update_verify.verify, fields=lambda deb: self.fields,
                             newer=newer, helper_protocol=lambda: self.protocol)

    def prepare(self, item=None, keys=None):
        return install.prepare(item or release(), self.client, self.work,
                               (self.key.public,) if keys is None else keys, "0.2.0",
                               self.tools())

    def refused(self, **options):
        with self.assertRaises(install.UpdateError) as caught:
            self.prepare(**options)
        return str(caught.exception)

    def test_success(self):
        prepared = self.prepare()
        self.assertEqual(prepared.deb, self.work/NAME)
        self.assertEqual(prepared.deb.read_bytes(), PACKAGE)
        self.assertEqual(prepared.sums.read_bytes(), self.sums)
        self.assertEqual(prepared.signature.name, "SHA256SUMS.sig")
        self.assertEqual(prepared.version, "9.9.9")
        self.assertEqual(self.compared, [("9.9.9", "0.2.0")])
        for path in (prepared.deb, prepared.sums, prepared.signature):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_signature_is_verified_before_the_package_is_downloaded(self):
        self.client.files[SITE + "SHA256SUMS.sig"] = self.other.signature_file(self.sums)
        self.assertIn("簽章", self.refused())
        self.assertNotIn(SITE + NAME, self.client.requested)

    def test_no_keys_means_no_network(self):
        self.assertEqual(self.refused(keys=()), install.NO_KEYS)
        self.assertEqual(self.refused(keys=("garbage",)), install.NO_KEYS)
        self.assertEqual(self.client.requested, [])

    def test_unsigned_releases_are_refused(self):
        self.assertEqual(self.refused(item=release(signed=False)), install.UNSIGNED)
        self.assertEqual(self.client.requested, [])

    def test_declared_size_over_the_cap(self):
        self.refused(item=release(size=Update.DEB_MAX_BYTES + 1))
        self.assertEqual(self.client.requested, [])

    def test_package_must_be_listed_and_match(self):
        self.client.files[SITE + NAME] = PACKAGE + b"tampered"
        self.assertIn("檢查碼", self.refused())
        sums = sums_text((hashlib.sha256(PACKAGE).hexdigest(), "./" + NAME))
        self.client.files[SITE + "SHA256SUMS"] = sums
        self.client.files[SITE + "SHA256SUMS.sig"] = self.key.signature_file(sums)
        self.work = private_dir(self)
        self.assertIn("SHA256SUMS", self.refused())

    def test_package_fields(self):
        for changes in ({"Package": "other"}, {"Architecture": "amd64"}, {"Version": "9.9.8"}):
            with self.subTest(changes=changes):
                self.work = private_dir(self)
                self.fields = dict(self.fields, **changes)
                self.refused()
        self.work = private_dir(self)
        self.fields = None
        self.refused()

    def test_not_newer(self):
        self.newer = False
        self.refused()

    def test_helper_protocol_must_match(self):
        for protocol in (None, Update.PROTOCOL + 1):
            with self.subTest(protocol=protocol):
                self.work = private_dir(self)
                self.protocol = protocol
                self.assertIn("重新安裝", self.refused())

    def test_download_failures_carry_the_client_message(self):
        self.client.files[SITE + "SHA256SUMS"] = FetchError("無法連線到 GitHub")
        self.assertEqual(self.refused(), "無法連線到 GitHub")


class Commands(unittest.TestCase):
    def test_deb_fields(self):
        calls = []

        def run(argv):
            calls.append(argv)
            return 0, b"Package: system-device-monitor\nArchitecture: all\nVersion: 1.2.3\n"
        self.assertEqual(install.deb_fields(Path("/x/a.deb"), run=run),
                         {"Package": "system-device-monitor", "Architecture": "all",
                          "Version": "1.2.3"})
        self.assertEqual(calls, [[Update.DPKG_DEB, "-f", "/x/a.deb", "Package", "Architecture",
                                  "Version"]])
        self.assertIsNone(install.deb_fields(Path("/x/a.deb"), run=lambda argv: (2, b"")))
        self.assertIsNone(install.deb_fields(Path("/x/a.deb"),
                                             run=lambda argv: (0, b"Package: a\nPackage: b\n")))

    def test_newer_uses_dpkg(self):
        calls = []

        def run(argv):
            calls.append(argv)
            return 0, b""
        self.assertTrue(install.dpkg_newer("1.0.0~beta.1", "0.9.0", run=run))
        self.assertEqual(calls, [[Update.DPKG, "--compare-versions", "1.0.0~beta.1", "gt",
                                  "0.9.0"]])
        self.assertFalse(install.dpkg_newer("1.0.0", "-1", run=run))
        self.assertFalse(install.dpkg_newer("1.0.0", "1.0.0", run=lambda argv: (1, b"")))

    def test_installed_version(self):
        self.assertEqual(install.installed_version(
            "0.2.0", run=lambda argv: (0, b"install ok installed\n0.2.5\n")), "0.2.5")
        for result in ((1, b""), (0, b"deinstall ok config-files\n0.2.5\n"),
                       (0, b"install ok installed\n-x\n")):
            with self.subTest(result=result):
                self.assertEqual(install.installed_version("0.2.0", run=lambda argv: result),
                                 "0.2.0")

    def test_helper_protocol(self):
        self.assertIsNone(install.helper_protocol(trusted=lambda path: False,
                                                  run=lambda argv: (0, b'{"protocol":1}')))
        self.assertEqual(install.helper_protocol(trusted=lambda path: True,
                                                 run=lambda argv: (0, b'{"protocol":1}\n')), 1)
        self.assertIsNone(install.helper_protocol(trusted=lambda path: True,
                                                  run=lambda argv: (2, b'{"protocol":1}')))


if __name__ == "__main__":
    unittest.main()
