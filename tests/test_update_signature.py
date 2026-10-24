"""發布簽章的驗證（程式端）：一次性金鑰簽 SHA256SUMS，再以內建公鑰陣列驗證。

金鑰都在暫存目錄以 /usr/bin/openssl 產生；陣列由測試注入，不使用 Update.PUBLIC_KEYS。
"""
import base64
import os
from pathlib import Path
import stat
import unittest

from sdm.constants import Update
from sdm.services import update_verify as verify
from tests.fixtures.signing import TestKey, needs_openssl, private_dir, sums_text

SUMS = sums_text(("ab" * 32, "system-device-monitor_9.9.9_all.deb"), ("cd" * 32, "x.tar.gz"))


class Decoding(unittest.TestCase):
    def test_keys_must_be_32_bytes_of_strict_base64(self):
        good = base64.b64encode(bytes(range(32))).decode()
        self.assertEqual(verify.decode_key(good), bytes(range(32)))
        for text in ("", "!!!", good[:-4], base64.b64encode(bytes(33)).decode(), good + "\n",
                     " " + good, good.replace("A", "-"), None, b"bytes", 5):
            with self.subTest(text=text):
                self.assertIsNone(verify.decode_key(text))

    def test_signature_file_is_one_line_of_64_bytes(self):
        line = base64.b64encode(bytes(64))
        for data in (line, line + b"\n", line + b"\r\n"):
            with self.subTest(data=data):
                self.assertEqual(verify.decode_signature(data), bytes(64))
        for data in (b"", b"\n", b"!!!\n", base64.b64encode(bytes(63)) + b"\n",
                     base64.b64encode(bytes(65)) + b"\n", line + b"\n\n", line + b"\nextra\n",
                     b" " + line, line[:40] + b" " + line[40:], line + b" \n", "text",
                     base64.urlsafe_b64encode(bytes([251] * 64)), b"\xff" * 88):
            with self.subTest(data=data):
                self.assertIsNone(verify.decode_signature(data))

    def test_public_key_pem_wraps_the_raw_key(self):
        pem = verify.public_key_pem(bytes(range(32)))
        self.assertTrue(pem.startswith(b"-----BEGIN PUBLIC KEY-----\n"))
        self.assertTrue(pem.endswith(b"-----END PUBLIC KEY-----\n"))
        body = b"".join(pem.splitlines()[1:-1])
        self.assertEqual(base64.b64decode(body), verify.SPKI_PREFIX + bytes(range(32)))

    def test_raw_public_key_from_der(self):
        self.assertEqual(verify.raw_public_key(verify.SPKI_PREFIX + bytes(32)), bytes(32))
        for der in (b"", verify.SPKI_PREFIX + bytes(31), bytes(44), verify.SPKI_PREFIX + bytes(33)):
            with self.subTest(der=der):
                self.assertIsNone(verify.raw_public_key(der))

    def test_shipped_key_list_is_well_formed(self):
        self.assertIsInstance(Update.PUBLIC_KEYS, tuple)
        for key in Update.PUBLIC_KEYS:
            self.assertIsNotNone(verify.decode_key(key))
        self.assertEqual(len(set(Update.PUBLIC_KEYS)), len(Update.PUBLIC_KEYS))


@needs_openssl
class Verification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys_dir = private_dir(cls, for_class=True)
        cls.key = TestKey(cls.keys_dir, "release")
        cls.old = TestKey(cls.keys_dir, "old")
        cls.other = TestKey(cls.keys_dir, "other")

    def setUp(self):
        self.dir = private_dir(self)
        self.sums = self.dir/"SHA256SUMS"
        self.sums.write_bytes(SUMS)
        self.work = self.dir/"work"
        self.work.mkdir(mode=0o700)
        self.calls = []

    def check(self, signature, keys, sums=None):
        return verify.verify(sums or self.sums, signature, keys, self.work)

    def test_valid_signature(self):
        self.assertTrue(self.check(self.key.signature_file(SUMS), (self.key.public,)))

    def test_public_key_pem_matches_openssl(self):
        self.assertEqual(verify.public_key_pem(self.key.raw), self.key.pem)

    def test_tampered_checksums_fail(self):
        signature = self.key.signature_file(SUMS)
        self.sums.write_bytes(SUMS.replace(b"ab", b"ac", 1))
        self.assertFalse(self.check(signature, (self.key.public,)))

    def test_wrong_key_fails(self):
        self.assertFalse(self.check(self.key.signature_file(SUMS), (self.other.public,)))

    def test_missing_or_garbage_signature_fails(self):
        for signature in (b"", b"not base64!\n", base64.b64encode(bytes(64)) + b"\n",
                          base64.b64encode(self.key.sign(SUMS)[:63]) + b"\n",
                          self.key.signature_file(SUMS)[:-10] + b"\n"):
            with self.subTest(signature=signature):
                self.assertFalse(self.check(signature, (self.key.public,)))

    def test_rotation_accepts_any_listed_key(self):
        signature = self.key.signature_file(SUMS)
        for keys in ((self.old.public, self.key.public), (self.key.public, self.old.public),
                     ("garbage", self.key.public), (self.other.public, "", self.key.public)):
            with self.subTest(keys=keys):
                self.assertTrue(self.check(signature, keys))
        old_signature = self.old.signature_file(SUMS)
        self.assertTrue(self.check(old_signature, (self.old.public, self.key.public)))
        self.assertFalse(self.check(old_signature, (self.key.public,)))

    def test_empty_key_list_refuses_without_running_openssl(self):
        def recorder(argv):
            self.calls.append(argv)
            return 0
        for keys in ((), ("garbage", "")):
            with self.subTest(keys=keys):
                self.assertFalse(verify.verify(self.sums, self.key.signature_file(SUMS), keys,
                                               self.work, run=recorder))
        self.assertEqual(self.calls, [])

    def test_openssl_failure_is_a_failed_verification(self):
        self.assertFalse(verify.verify(self.sums, self.key.signature_file(SUMS),
                                       (self.key.public,), self.work, run=lambda argv: -1))

    def test_openssl_gets_fixed_arguments_and_private_files(self):
        seen = []

        def recorder(argv):
            pem, signature = Path(argv[argv.index("-inkey") + 1]), Path(argv[-1])
            seen.append((argv, stat.S_IMODE(pem.stat().st_mode),
                         stat.S_IMODE(pem.parent.stat().st_mode),
                         stat.S_IMODE(signature.stat().st_mode), signature.read_bytes()))
            return 1
        verify.verify(self.sums, self.key.signature_file(SUMS), (self.key.public,), self.work,
                      run=recorder)
        argv, pem_mode, dir_mode, signature_mode, raw = seen[0]
        self.assertEqual(argv[:4], [Update.OPENSSL, "pkeyutl", "-verify", "-pubin"])
        self.assertEqual(argv[6:11], ["-rawin", "-in", str(self.sums), "-sigfile", argv[-1]])
        self.assertEqual((pem_mode, dir_mode, signature_mode), (0o600, 0o700, 0o600))
        self.assertEqual(raw, self.key.sign(SUMS))
        self.assertEqual(os.listdir(self.work), [])          # 暫存的公鑰與簽章都已刪除


if __name__ == "__main__":
    unittest.main()
