"""測試用的一次性 Ed25519 金鑰：以 /usr/bin/openssl 在暫存目錄（0700）產生，測試結束就刪除。

絕不使用真正的發布金鑰，也不把私鑰寫進專案資料夾。
"""
import base64
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

OPENSSL = "/usr/bin/openssl"
SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")
available = os.access(OPENSSL, os.X_OK)
needs_openssl = unittest.skipUnless(available, "needs /usr/bin/openssl")


def private_dir(case, for_class=False):
    """新的 0700 暫存目錄；for_class 時在整個測試類別結束後才刪除（setUpClass 用）。"""
    temp = tempfile.TemporaryDirectory()
    (case.addClassCleanup if for_class else case.addCleanup)(temp.cleanup)
    path = Path(temp.name)
    path.chmod(0o700)
    return path


def run(argv, data=None):
    result = subprocess.run(argv, input=data, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(f"openssl failed: {argv[1]}")
    return result.stdout


class TestKey:
    """一把一次性金鑰：私鑰 PEM 的位置與 base64 的原始公鑰。"""

    def __init__(self, directory, name="key"):
        self.path = Path(directory)/(name + ".pem")
        run([OPENSSL, "genpkey", "-algorithm", "ED25519", "-out", str(self.path)])
        der = run([OPENSSL, "pkey", "-in", str(self.path), "-pubout", "-outform", "DER"])
        if not der.startswith(SPKI_PREFIX) or len(der) != len(SPKI_PREFIX) + 32:
            raise AssertionError("unexpected public key encoding")
        self.raw = der[len(SPKI_PREFIX):]
        self.public = base64.b64encode(self.raw).decode("ascii")
        self.pem = run([OPENSSL, "pkey", "-in", str(self.path), "-pubout"])

    def sign(self, data):
        """data 的原始 64 位元組簽章。"""
        source = self.path.with_suffix(".data")
        source.write_bytes(data)
        try:
            return run([OPENSSL, "pkeyutl", "-sign", "-inkey", str(self.path), "-rawin",
                        "-in", str(source)])
        finally:
            source.unlink()

    def signature_file(self, data):
        """SHA256SUMS.sig 的內容：一行 base64。"""
        return base64.b64encode(self.sign(data)) + b"\n"


def sums_text(*entries):
    """（雜湊、檔名）→ SHA256SUMS 的內容。"""
    return "".join(f"{digest}  {name}\n" for digest, name in entries).encode("ascii")
