"""發布簽章的驗證（與維護者的 macOS 版相同、類似 Sparkle 的 Ed25519 簽章）。

- SHA256SUMS.sig：一行 base64（可以有結尾換行），解出剛好 64 位元組的原始 Ed25519 簽章，
  簽的是 SHA256SUMS 檔案的原始位元組。
- 公鑰：Update.PUBLIC_KEYS（base64 的 32 位元組原始公鑰）；組成 PEM 格式的
  SubjectPublicKeyInfo（DER 前綴 302a300506032b6570032100 ＋ 32 位元組）。陣列中任何一把
  驗證通過即有效（換金鑰時新舊並存）；陣列空的或沒有任何一把格式正確時一律無效，也不執行 openssl。
- 以 /usr/bin/openssl pkeyutl -verify -pubin -inkey <公鑰> -rawin -in <SHA256SUMS> -sigfile <簽章>
  驗證：公鑰與簽章寫在呼叫者私人目錄底下新建的暫存目錄（0700、檔案 0600、O_EXCL），用完即刪；
  只以參數清單執行、不經過 shell，環境變數只有固定的 PATH 與 LANG。
helper/sdm-update 有同樣的實作（以 root 執行、不載入本套件）；測試以同一組案例確認兩者一致。
"""
import base64
import binascii
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from sdm.constants import Update

SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")
KEY_BYTES = 32
SIGNATURE_BYTES = 64
PEM_LINE = 64
SAFE_ENV = {"PATH": Update.SAFE_PATH, "LANG": "C"}
FILE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
PRIVATE_FILE = 0o600
PRIVATE_DIRECTORY = 0o700


def strict_base64(text):
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError, TypeError):
        return None


def decode_key(text):
    """base64 的 32 位元組原始公鑰；格式不對時回傳 None。"""
    if not isinstance(text, str) or not text.isascii():
        return None
    raw = strict_base64(text)
    return raw if raw is not None and len(raw) == KEY_BYTES else None


def decode_signature(data):
    """SHA256SUMS.sig 的內容 → 64 位元組簽章；不是剛好一行 base64 時回傳 None。"""
    if not isinstance(data, bytes):
        return None
    for ending in (b"\r\n", b"\n"):
        if data.endswith(ending):
            data = data[:-len(ending)]
            break
    if not data or not data.isascii() or b"\n" in data or b"\r" in data:
        return None
    raw = strict_base64(data)
    return raw if raw is not None and len(raw) == SIGNATURE_BYTES else None


def public_key_pem(raw):
    """原始公鑰 → PEM 格式的 SubjectPublicKeyInfo。"""
    body = base64.b64encode(SPKI_PREFIX + raw)
    lines = [body[start:start + PEM_LINE] for start in range(0, len(body), PEM_LINE)]
    return b"-----BEGIN PUBLIC KEY-----\n" + b"\n".join(lines) + b"\n-----END PUBLIC KEY-----\n"


def raw_public_key(der):
    """DER 格式的 Ed25519 SubjectPublicKeyInfo → 32 位元組原始公鑰；不是時回傳 None。"""
    if len(der) != len(SPKI_PREFIX) + KEY_BYTES or not der.startswith(SPKI_PREFIX):
        return None
    return der[len(SPKI_PREFIX):]


def usable_keys(keys):
    return [raw for raw in (decode_key(key) for key in keys) if raw is not None]


def run_openssl(argv):
    """結束碼；無法執行或逾時時為 -1。"""
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, env=SAFE_ENV,
                              timeout=Update.COMMAND_TIMEOUT, check=False).returncode
    except (OSError, subprocess.SubprocessError):
        return -1


def write_private(path, data):
    fd = os.open(path, FILE_FLAGS, PRIVATE_FILE)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def verify_argv(pem, sums, signature):
    return [Update.OPENSSL, "pkeyutl", "-verify", "-pubin", "-inkey", str(pem), "-rawin",
            "-in", str(sums), "-sigfile", str(signature)]


def verify(sums_path, signature_data, keys, workdir, run=run_openssl):
    """SHA256SUMS（sums_path）的簽章是否由 keys 中的任何一把簽出。"""
    signature, raw_keys = decode_signature(signature_data), usable_keys(keys)
    if signature is None or not raw_keys:
        return False
    work = Path(tempfile.mkdtemp(prefix="verify-", dir=workdir))
    try:
        os.chmod(work, PRIVATE_DIRECTORY)
        signature_path = work/"signature.bin"
        write_private(signature_path, signature)
        for index, raw in enumerate(raw_keys):
            pem = work/f"key-{index}.pem"
            write_private(pem, public_key_pem(raw))
            if run(verify_argv(pem, sums_path, signature_path)) == 0:
                return True
        return False
    finally:
        shutil.rmtree(work, ignore_errors=True)
