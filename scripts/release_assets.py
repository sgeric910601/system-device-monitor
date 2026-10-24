#!/usr/bin/python3
"""Create source archives from a clean Git HEAD, plus SHA256 checksums and their signature.

發布簽章：寫出 SHA256SUMS 之後，以 --signing-key（或環境變數 SDM_RELEASE_SIGNING_KEY）指定的
Ed25519 私鑰簽 SHA256SUMS 的原始位元組，寫成一行 base64 的 SHA256SUMS.sig。私鑰必須在專案資料夾
以外、是只有自己能讀寫的一般檔案（不能是符號連結）。簽完先確認這把金鑰的公鑰列在
Update.PUBLIC_KEYS 裡，並以程式端相同的驗證確認簽章，通過後才寫出 .sig；任何一步失敗都不留下
.sig（舊的也會先刪掉，避免上傳不相符的一對）。完整發布（含 .deb）一定要簽章；--source-only 沒有
指定金鑰時只產生來源壓縮包與 SHA256SUMS。
"""
import argparse
import base64
import gzip
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sdm.constants import Update  # noqa: E402
from sdm.paths import VERSION  # noqa: E402
from sdm.services import update_verify  # noqa: E402

OPENSSL = Update.OPENSSL
KEY_VARIABLE = "SDM_RELEASE_SIGNING_KEY"
NO_KEY = ("完整發布需要簽章：以 --signing-key <私鑰> 或環境變數 SDM_RELEASE_SIGNING_KEY 指定"
          "專案資料夾以外的 Ed25519 私鑰（見 docs/BUILD.md）。")


def signing_key(option, environ):
    value = option or environ.get(KEY_VARIABLE) or None
    return Path(value).expanduser() if value else None


def check_key_file(path, root):
    """私鑰在專案資料夾以外，是自己擁有、只有自己能讀寫的一般檔案。"""
    target = Path(os.path.abspath(path))
    project = Path(root).resolve()
    for candidate in (target, target.resolve()):
        if candidate == project or project in candidate.parents:
            raise SystemExit("私鑰不能放在專案資料夾裡：" + str(target))
    try:
        info = os.lstat(target)
    except OSError:
        raise SystemExit("讀不到私鑰：" + str(target)) from None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() \
            or stat.S_IMODE(info.st_mode) & 0o077:
        raise SystemExit("私鑰必須是自己擁有、權限 0600 的一般檔案（不能是符號連結）：" + str(target))
    return target


def openssl(argv):
    result = subprocess.run([OPENSSL, *argv], stdin=subprocess.DEVNULL, capture_output=True,
                            check=False)
    if result.returncode != 0:
        raise SystemExit("openssl 執行失敗：" + argv[0])
    return result.stdout


def openssl_sign(key, sums):
    """SHA256SUMS 原始位元組的 64 位元組 Ed25519 簽章。"""
    signature = openssl(["pkeyutl", "-sign", "-inkey", str(key), "-rawin", "-in", str(sums)])
    if len(signature) != update_verify.SIGNATURE_BYTES:
        raise SystemExit("簽章長度不符；私鑰必須是 Ed25519")
    return signature


def public_key_of(key):
    raw = update_verify.raw_public_key(openssl(["pkey", "-in", str(key), "-pubout",
                                                "-outform", "DER"]))
    if raw is None:
        raise SystemExit("私鑰不是 Ed25519")
    return base64.b64encode(raw).decode("ascii")


def self_verify(sums, data, keys):
    with tempfile.TemporaryDirectory(prefix="sdm-release-verify-") as work:
        return update_verify.verify(sums, data, keys, work)


def sign_checksums(sums, key, keys=Update.PUBLIC_KEYS, root=ROOT):
    """簽 SHA256SUMS 並以內建公鑰驗證，通過後寫出旁邊的 SHA256SUMS.sig 並回傳它的位置。"""
    output = Path(sums).with_name(Update.SIGNATURE)
    output.unlink(missing_ok=True)
    key = check_key_file(key, root)
    if public_key_of(key) not in keys:
        raise SystemExit("這把私鑰的公鑰不在 Update.PUBLIC_KEYS 裡；先把公鑰加進 sdm/constants.py "
                         "與 helper/sdm-update，提交後再發布。")
    data = base64.b64encode(openssl_sign(key, sums)) + b"\n"
    if not self_verify(sums, data, keys):
        raise SystemExit("簽章無法以內建公鑰驗證，沒有寫出 SHA256SUMS.sig。")
    output.write_bytes(data)
    return output


def finish_signing(sums, key, keys, root, source_only):
    """有私鑰就簽；沒有時完整發布拒絕、只發布來源時略過（兩種情況都不留下舊的 .sig）。"""
    if key is not None:
        return sign_checksums(sums, key, keys, root)
    Path(sums).with_name(Update.SIGNATURE).unlink(missing_ok=True)
    if not source_only:
        raise SystemExit(NO_KEY)
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--signing-key", help="Ed25519 private key outside the project folder")
    args = parser.parse_args()
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT):
        raise SystemExit("Commit tracked changes before creating release assets.")
    dist = ROOT/"dist"
    dist.mkdir(exist_ok=True)
    name = f"system-device-monitor-{VERSION}"
    archive = dist/(name+".tar.gz")
    content = subprocess.check_output(["git", "archive", "--format=tar", "--prefix="+name+"/", "HEAD"], cwd=ROOT)
    archive.write_bytes(gzip.compress(content, mtime=0))
    outputs = [archive]
    if not args.source_only:
        package = dist/f"system-device-monitor_{VERSION}_all.deb"
        if not package.is_file(): raise SystemExit("Run make deb first.")
        outputs.append(package)
    sums = dist/Update.CHECKSUMS
    sums.write_text("".join(
        hashlib.sha256(path.read_bytes()).hexdigest()+"  "+path.name+"\n" for path in outputs))
    signed = finish_signing(sums, signing_key(args.signing_key, os.environ), Update.PUBLIC_KEYS,
                            ROOT, args.source_only)
    print("Signed:", signed.name if signed else "no (source only)")


if __name__ == "__main__":
    main()
