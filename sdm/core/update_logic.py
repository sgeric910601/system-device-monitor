"""一鍵更新的版本邏輯（純邏輯，不載入 GTK、不連網路）：移植參考專案的 Model/Update.swift。

- GitHub 的 tag 是 v1.7.1、v1.8.0-beta.1；.deb 的版本是 1.7.1、1.8.0~beta.1（dpkg 把 ~ 排在
  正式版之前，所以 1.8.0~beta.1 < 1.8.0）。同一個版本號的正式版比它的任何 Beta 都新。
- 版本號只接受 ASCII 數字（每段最多 9 位、不接受正負號、空白或空的段）；Beta 序號是正整數、
  不能有前導 0。比參考專案嚴格：參考專案的 split 會略過空的段，Int() 也接受「+1」。
- .deb 檔名固定為 system-device-monitor_<Debian 版本>_all.deb，只有三段的版本才有對應的檔名。
- 下載連結只接受 HTTPS 的 github.com、api.github.com 與 *.githubusercontent.com（以「.」為界的
  子網域），不接受帳號密碼、其他連接埠、反斜線或空白。
- SHA256SUMS 每行「<64 位十六進位> <空白><可選的 *><檔名>」，檔名必須完全相同；同一個檔名
  出現兩次而且雜湊不同時視為沒有（參考專案取第一個）。
"""
from dataclasses import dataclass
import json
import re
from typing import NamedTuple, Optional
import unicodedata
from urllib.parse import urlsplit

from sdm.constants import Update

NUMBER = re.compile(r"[0-9]{1,%d}" % Update.MAX_NUMBER_DIGITS)
BETA_NUMBER = re.compile(r"[1-9][0-9]{0,%d}" % (Update.MAX_NUMBER_DIGITS - 1))
BETA_PREFIX = "beta."
DEBIAN_BETA = "~beta."
TAG_BETA = "-beta."
DEBIAN = re.compile(r"([0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9})(?:~beta\.([1-9][0-9]{0,8}))?")
HOST = re.compile(r"[a-z0-9.-]+")
HEX_DIGEST = re.compile(r"[0-9a-fA-F]{64}")
BULLET = re.compile(r"^[-*+]\s+")
HEADING = re.compile(r"^#{1,6}\s*")
ELLIPSIS = "…"
POINT = "・"


def version(text):
    """「v1.4.1」「1.4.1」→（1, 4, 1）；任何一段不是數字就回傳 None。"""
    if not isinstance(text, str):
        return None
    trimmed = text[1:] if text.startswith("v") else text
    parts = trimmed.split(".")
    if not all(NUMBER.fullmatch(part) for part in parts):
        return None
    return tuple(int(part) for part in parts)


def is_newer(candidate, current):
    """逐段比較，缺的段當 0（1.4 ＝ 1.4.0）。"""
    for index in range(max(len(candidate), len(current))):
        left = candidate[index] if index < len(candidate) else 0
        right = current[index] if index < len(current) else 0
        if left != right:
            return left > right
    return False


def should_notify(candidate, last_notified):
    """比上次通知過的版本新才通知（同一個版本只通知一次）；紀錄壞掉時當作沒通知過。"""
    last = version(last_notified)
    return True if last is None else is_newer(candidate, last)


@dataclass(frozen=True)
class AppVersion:
    """版本號與 Beta 序號（None 是正式版）。"""
    numbers: tuple
    beta: Optional[int] = None

    @property
    def is_beta(self):
        return self.beta is not None

    @property
    def text(self):
        """畫面上顯示的版本：1.7.1、1.8.0 beta 1。"""
        base = ".".join(str(number) for number in self.numbers)
        return base if self.beta is None else f"{base} beta {self.beta}"

    @classmethod
    def parse(cls, text):
        """「v1.7.1」「1.7.1」「v1.8.0-beta.1」；其他格式回傳 None。"""
        if not isinstance(text, str):
            return None
        base, separator, suffix = text.partition("-")
        numbers = version(base)
        if numbers is None:
            return None
        if not separator:
            return cls(numbers)
        if not suffix.startswith(BETA_PREFIX) or not BETA_NUMBER.fullmatch(suffix[len(BETA_PREFIX):]):
            return None
        return cls(numbers, int(suffix[len(BETA_PREFIX):]))

    @classmethod
    def current(cls, text):
        """執行中的版本（VERSION 檔：1.7.0 或 1.7.0~beta.1）；看不懂時只取數字部分，再不行為 0。"""
        text = str(text).strip()
        found = from_debian(text) or cls.parse(text)
        if found is not None:
            return found
        return cls(version(text.split("~", 1)[0]) or (0,))

    def __lt__(self, other):
        if is_newer(other.numbers, self.numbers):
            return True
        if is_newer(self.numbers, other.numbers):
            return False
        if self.beta is None:
            return False
        return other.beta is None or self.beta < other.beta


def debian_version(app_version):
    """1.8.0 beta 1 → 「1.8.0~beta.1」；不是三段的版本沒有對應的 .deb，回傳 None。"""
    if len(app_version.numbers) != Update.VERSION_PARTS:
        return None
    base = ".".join(str(number) for number in app_version.numbers)
    return base if app_version.beta is None else f"{base}{DEBIAN_BETA}{app_version.beta}"


def from_debian(text):
    """「1.8.0~beta.1」→ AppVersion；其他格式（含 epoch、修訂號）回傳 None。"""
    match = DEBIAN.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        return None
    beta = int(match.group(2)) if match.group(2) else None
    return AppVersion(version(match.group(1)), beta)


def tag(app_version):
    base = "v" + ".".join(str(number) for number in app_version.numbers)
    return base if app_version.beta is None else f"{base}{TAG_BETA}{app_version.beta}"


def asset_name(app_version):
    debian = debian_version(app_version)
    return None if debian is None else f"{Update.PACKAGE}_{debian}_all.deb"


def is_trusted(url):
    """只接受 HTTPS 的 GitHub 主機（見模組說明）。"""
    if not isinstance(url, str) or not url.isprintable() or any(c in url for c in " \\"):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    host = parts.hostname or ""
    if parts.scheme != "https" or "@" in parts.netloc or port not in (None, Update.HTTPS_PORT):
        return False
    if not HOST.fullmatch(host) or ".." in host or host.startswith("."):
        return False
    return host in Update.TRUSTED_HOSTS or host.endswith(Update.TRUSTED_SUFFIX)


def checksum(text, name):
    """SHA256SUMS 中 name 的雜湊（小寫）；找不到、格式不對或互相矛盾時回傳 None。"""
    found = set()
    for line in text.splitlines():
        parts = [part for part in line.split(" ") if part]
        if len(parts) != 2 or not HEX_DIGEST.fullmatch(parts[0]):
            continue
        listed = parts[1][1:] if parts[1].startswith("*") else parts[1]
        if listed == name:
            found.add(parts[0].lower())
    return found.pop() if len(found) == 1 else None


@dataclass(frozen=True)
class ReleaseInfo:
    """GitHub Releases 上的一個版本與它的下載資訊。"""
    tag: str
    numbers: tuple
    page_url: str
    asset_name: str
    asset_url: str
    asset_size: int
    checksum_url: Optional[str] = None      # SHA256SUMS（一鍵更新必要）
    signature_url: Optional[str] = None     # SHA256SUMS.sig（一鍵更新必要）
    beta: Optional[int] = None
    notes: str = ""

    @property
    def app_version(self):
        return AppVersion(self.numbers, self.beta)

    @property
    def is_beta(self):
        return self.beta is not None

    @property
    def version_text(self):
        return self.app_version.text

    @property
    def debian_version(self):
        return debian_version(self.app_version)

    @property
    def signed(self):
        return self.checksum_url is not None and self.signature_url is not None


def find_asset(assets, name):
    """（網址、大小）；找不到、還沒上傳完成或網址不可信時回傳 None。"""
    for item in assets:
        if not isinstance(item, dict) or item.get("name") != name:
            continue
        url, size = item.get("browser_download_url"), item.get("size")
        if item.get("state", Update.UPLOADED) != Update.UPLOADED or not is_trusted(url):
            return None
        return url, size if type(size) is int and size >= 0 else 0
    return None


def release(root):
    """一個 Release（API 回應中的一筆）；不完整或不可信時回傳 None。

    預先發行（prerelease）的必須是 v1.8.0-beta.1 這種 tag，正式版的 tag 不能帶 Beta 序號；
    草稿一律略過。SHA256SUMS 與 .sig 可以沒有（列出版本但不能一鍵更新）。
    """
    if not isinstance(root, dict):
        return None
    parsed = AppVersion.parse(root.get("tag_name"))
    assets, page = root.get("assets"), root.get("html_url")
    if parsed is None or root.get("draft") is not False or not isinstance(assets, list) \
            or (root.get("prerelease") is True) != parsed.is_beta or not is_trusted(page):
        return None
    name = asset_name(parsed)
    package = find_asset(assets, name) if name else None
    if package is None:
        return None
    sums, signature = find_asset(assets, Update.CHECKSUMS), find_asset(assets, Update.SIGNATURE)
    body = root.get("body")
    return ReleaseInfo(tag=root["tag_name"], numbers=parsed.numbers, page_url=page,
                       asset_name=name, asset_url=package[0], asset_size=package[1],
                       checksum_url=sums[0] if sums else None,
                       signature_url=signature[0] if signature else None,
                       beta=parsed.beta, notes=body if isinstance(body, str) else "")


def load_json(data):
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, AttributeError):
        return None


def releases(data):
    """GET /repos/{owner}/{repo}/releases 的清單（包含 Beta），格式不對或找不到下載檔的略過。"""
    items = load_json(data)
    if not isinstance(items, list):
        return ()
    return tuple(found for found in (release(item) for item in items) if found is not None)


def latest_release(data):
    """單一 Release（GET …/releases/latest 的回應）：只接受正式版。"""
    found = release(load_json(data))
    return None if found is None or found.is_beta else found


class Choice(NamedTuple):
    stable: Optional[ReleaseInfo]
    beta: Optional[ReleaseInfo]


def newest(candidates):
    best = None
    for candidate in candidates:
        if best is None or best.app_version < candidate.app_version:
            best = candidate
    return best


def choose(found, current):
    """正式版取比目前新的最新一個；Beta 只有比目前版本、也比最新正式版新時才列出
    （是否顯示 Beta 由呼叫的地方決定：只有使用者手動檢查時才找 Beta，也不發通知）。"""
    newer = [item for item in found if current < item.app_version]
    stable = newest(item for item in newer if not item.is_beta)
    beta = newest(item for item in newer if item.is_beta
                  and (stable is None or stable.app_version < item.app_version))
    return Choice(stable, beta)


def plain_line(line):
    """去掉控制字元、標題記號，項目符號改成「・」，合併連續空白。"""
    printable = "".join(char for char in line if not unicodedata.category(char).startswith("C"))
    text = " ".join(printable.split())
    text = HEADING.sub("", text)
    return BULLET.sub(POINT, text) if BULLET.match(text) else text


def notes_excerpt(body, limit=Update.NOTES_MAX_CHARS, max_lines=Update.NOTES_MAX_LINES):
    """更新內容的純文字摘要：去掉空行，最多 max_lines 行、limit 個字元，截斷時以「…」結尾。"""
    if not isinstance(body, str):
        return ""
    lines = [text for text in (plain_line(line) for line in body.splitlines()) if text]
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + [ELLIPSIS]
    text = "\n".join(lines)
    return text if len(text) <= limit else text[:limit - 1] + ELLIPSIS
