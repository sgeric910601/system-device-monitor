"""發布端：產生簽章金鑰（scripts/release_keys.py）、簽 SHA256SUMS 並以內建公鑰自我驗證
（scripts/release_assets.py），以及隱私檢查拒絕專案裡的私鑰。

金鑰一律是暫存目錄裡的一次性金鑰；拒絕的案例在建立任何檔案之前就停止。
"""
import base64
import os
from pathlib import Path
import stat
import unittest
from unittest import mock

from sdm.services import update_verify
from tests.fixtures.helpers import ROOT, load_script
from tests.fixtures.signing import TestKey, needs_openssl, private_dir, sums_text

keys_script = load_script("release_keys")
assets = load_script("release_assets")
privacy = load_script("privacy_check")
SUMS = sums_text(("ab" * 32, "system-device-monitor_9.9.9_all.deb"))
HEADER = b"-----BEGIN " + b"PRIVATE KEY-----"       # 分開寫，避免這個測試檔本身被當成私鑰


def mode(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


class KeyLocation(unittest.TestCase):
    def setUp(self):
        self.base = private_dir(self)
        self.repo = self.base/"repo"
        (self.repo/".git").mkdir(parents=True)

    def refused(self, out_dir, root=None):
        existed = Path(out_dir).exists()
        with self.assertRaises(keys_script.Refused):
            keys_script.check_location(Path(out_dir), root or self.repo)
        self.assertEqual(Path(out_dir).exists(), existed)

    def test_refuses_the_project_and_anything_inside_it(self):
        for out_dir in (self.repo, self.repo/"keys", self.repo/"a/b", self.repo/"x/../keys"):
            with self.subTest(out_dir=out_dir):
                self.refused(out_dir)

    def test_refuses_the_real_project_folder(self):
        target = ROOT/"release-keys-test"
        with self.assertRaises(keys_script.Refused):
            keys_script.check_location(target, ROOT)
        self.assertFalse(target.exists())

    def test_refuses_any_git_work_tree(self):
        other = self.base/"other"
        (other/".git").mkdir(parents=True)
        linked = self.base/"linked"
        linked.mkdir()
        (linked/".git").write_text("gitdir: /elsewhere\n")
        for out_dir in (other, other/"keys", linked/"deep/keys"):
            with self.subTest(out_dir=out_dir):
                self.refused(out_dir)

    def test_accepts_a_folder_outside_git(self):
        keys_script.check_location(self.base/"outside/keys", self.repo)


@needs_openssl
class Generate(unittest.TestCase):
    def setUp(self):
        self.base = private_dir(self)
        self.repo = self.base/"repo"
        (self.repo/".git").mkdir(parents=True)
        self.out = self.base/"keys"

    def test_creates_a_private_folder_and_key(self):
        public = keys_script.generate(self.out, self.repo)
        key = self.out/keys_script.KEY_NAME
        self.assertEqual((mode(self.out), mode(key)), (0o700, 0o600))
        self.assertTrue(key.read_bytes().startswith(HEADER))
        self.assertIsNotNone(update_verify.decode_key(public))
        self.assertEqual((self.out/keys_script.PUBLIC_NAME).read_text(), public + "\n")
        signature = assets.openssl_sign(key, self.write_sums())
        self.assertTrue(self.verify(signature, (public,)))

    def test_never_overwrites_an_existing_key(self):
        keys_script.generate(self.out, self.repo)
        before = (self.out/keys_script.KEY_NAME).read_bytes()
        with self.assertRaises(keys_script.Refused):
            keys_script.generate(self.out, self.repo)
        self.assertEqual((self.out/keys_script.KEY_NAME).read_bytes(), before)

    def test_existing_folder_must_already_be_private(self):
        self.out.mkdir(mode=0o755)
        self.out.chmod(0o755)
        with self.assertRaises(keys_script.Refused):
            keys_script.generate(self.out, self.repo)
        self.assertEqual(os.listdir(self.out), [])
        self.assertEqual(mode(self.out), 0o755)         # 不改變既有資料夾的權限

    def test_symlinked_folder_is_refused(self):
        target = self.base/"target"
        target.mkdir(mode=0o700)
        self.out.symlink_to(target)
        with self.assertRaises(keys_script.Refused):
            keys_script.generate(self.out, self.repo)

    def write_sums(self):
        path = self.base/"SHA256SUMS"
        path.write_bytes(SUMS)
        return path

    def verify(self, signature, keys):
        path = self.base/"check.sig"
        path.write_bytes(base64.b64encode(signature) + b"\n")
        work = self.base/"work"
        work.mkdir(mode=0o700, exist_ok=True)
        return update_verify.verify(self.base/"SHA256SUMS", path.read_bytes(), keys, work)


@needs_openssl
class SignChecksums(unittest.TestCase):
    def setUp(self):
        self.base = private_dir(self)
        self.repo = self.base/"repo"
        self.dist = self.repo/"dist"
        self.dist.mkdir(parents=True)
        self.sums = self.dist/"SHA256SUMS"
        self.sums.write_bytes(SUMS)
        self.keys = self.base/"keys"
        self.keys.mkdir(mode=0o700)
        self.key = TestKey(self.keys, "release")

    def sign(self, key=None, keys=None):
        return assets.sign_checksums(self.sums, key or self.key.path, keys or (self.key.public,),
                                     self.repo)

    def test_signs_and_self_verifies(self):
        signature = self.sign()
        self.assertEqual(signature, self.dist/"SHA256SUMS.sig")
        data = signature.read_bytes()
        self.assertEqual(data.count(b"\n"), 1)
        self.assertEqual(update_verify.decode_signature(data), self.key.sign(SUMS))
        work = self.base/"work"
        work.mkdir(mode=0o700)
        self.assertTrue(update_verify.verify(self.sums, data, (self.key.public,), work))

    def test_key_must_be_one_of_the_built_in_keys(self):
        other = TestKey(self.keys, "other")
        for keys in ((other.public,), ()):
            with self.subTest(keys=keys):
                with self.assertRaises(SystemExit):
                    assets.sign_checksums(self.sums, self.key.path, keys, self.repo)
                self.assertFalse((self.dist/"SHA256SUMS.sig").exists())
        self.sign(keys=(other.public, self.key.public))       # 換金鑰期間新舊並存

    def test_key_inside_the_project_is_refused(self):
        inside = self.repo/"release.pem"
        inside.write_bytes(self.key.path.read_bytes())
        inside.chmod(0o600)
        with self.assertRaises(SystemExit):
            self.sign(key=inside)
        self.assertFalse((self.dist/"SHA256SUMS.sig").exists())

    def test_unsafe_key_files_are_refused(self):
        link = self.keys/"link.pem"
        link.symlink_to(self.key.path)
        loose = self.keys/"loose.pem"
        loose.write_bytes(self.key.path.read_bytes())
        loose.chmod(0o640)
        for key in (link, loose, self.keys/"missing.pem", self.keys):
            with self.subTest(key=key.name):
                with self.assertRaises(SystemExit):
                    self.sign(key=key)

    def test_signing_key_comes_from_the_option_or_the_environment(self):
        self.assertEqual(assets.signing_key("/a.pem", {"SDM_RELEASE_SIGNING_KEY": "/b.pem"}),
                         Path("/a.pem"))
        self.assertEqual(assets.signing_key(None, {"SDM_RELEASE_SIGNING_KEY": "/b.pem"}),
                         Path("/b.pem"))
        self.assertIsNone(assets.signing_key(None, {"SDM_RELEASE_SIGNING_KEY": ""}))
        self.assertIsNone(assets.signing_key(None, {}))
        self.assertEqual(assets.signing_key("~/a.pem", {}), Path.home()/"a.pem")

    def test_full_release_needs_a_key_and_stale_signatures_are_removed(self):
        stale = self.dist/"SHA256SUMS.sig"
        stale.write_bytes(b"old\n")
        with self.assertRaises(SystemExit):
            assets.finish_signing(self.sums, None, (self.key.public,), self.repo,
                                  source_only=False)
        self.assertFalse(stale.exists())
        stale.write_bytes(b"old\n")
        self.assertIsNone(assets.finish_signing(self.sums, None, (self.key.public,), self.repo,
                                                source_only=True))
        self.assertFalse(stale.exists())

    def test_makefile_passes_the_signing_key_through(self):
        text = (ROOT/"Makefile").read_text(encoding="utf-8")
        self.assertIn("--signing-key", text)
        self.assertIn("SIGNING_KEY", text)


class PrivateKeysInTheProject(unittest.TestCase):
    PEM = HEADER + b"\nsynthetic\n-----END " + b"PRIVATE KEY-----\n"

    def setUp(self):
        self.repo = private_dir(self)
        (self.repo/".git").mkdir()

    def test_pem_files_fail_the_privacy_check(self):
        path = self.repo/"release.pem"
        path.write_bytes(self.PEM)
        self.assertEqual(set(privacy.findings(path)), {"non-public file type", "private key"})
        encrypted = self.repo/"notes.txt"
        encrypted.write_bytes(self.PEM.replace(b"PRIVATE", b"ENCRYPTED PRIVATE"))
        self.assertEqual(privacy.findings(encrypted), ["private key"])

    def test_private_keys_anywhere_under_the_project_are_reported(self):
        (self.repo/"dist").mkdir()
        (self.repo/"dist/release.pem").write_bytes(b"not even a key\n")
        (self.repo/"notes.txt").write_bytes(b"x\n" + self.PEM)
        (self.repo/"key.p12").write_bytes(b"\x00")
        (self.repo/".git/objects.pem").write_bytes(self.PEM)       # .git 由 gitleaks 歷史掃描負責
        (self.repo/"tests/__pycache__").mkdir(parents=True)
        (self.repo/"tests/__pycache__/a.pyc").write_bytes(self.PEM)  # 編譯時合併的字串常數
        (self.repo/"README.md").write_text("ok\n")
        self.assertEqual(privacy.stray_private_keys(self.repo),
                         ["dist/release.pem", "key.p12", "notes.txt"])

    def test_real_project_has_no_private_keys(self):
        with mock.patch.object(privacy, "MAX_SCAN_BYTES", 1024):
            self.assertEqual(privacy.stray_private_keys(ROOT), [])


if __name__ == "__main__":
    unittest.main()
