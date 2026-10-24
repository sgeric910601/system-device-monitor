"""GitHub 的 HTTPS 用戶端：一鍵更新唯一的網路存取（只在工作執行緒呼叫，不碰 GTK）。

- 第一個網址、每一次轉址（最多 5 次）與最後的網址都必須是 update_logic.is_trusted 接受的
  HTTPS GitHub 主機；不可信的轉址在送出請求之前就拒絕。
- 只裝 HTTPS（憑證與主機名稱都驗證）、系統代理伺服器設定與上述的轉址處理：不處理 http、file、
  ftp、data，沒有 cookie、帳號密碼或其他驗證；請求只帶 User-Agent（system-device-monitor/<版本>）
  與 Accept 兩個標頭。
- 每次連線與讀取 15 秒逾時，另有整個請求的上限；大小上限：宣告的 Content-Length 超過就不讀，
  實際內容超過上限或超過宣告的長度就停止。只接受 HTTP 200。
- 任何失敗都拋出 FetchError，訊息是固定的中文說明，不含網址或伺服器回傳的內容。
"""
import hashlib
import http.client
import os
import ssl
import time
import urllib.error
import urllib.request

from sdm.constants import Update
from sdm.core.update_logic import is_trusted

CHUNK_BYTES = 64 * 1024
JSON = "application/vnd.github+json"
BINARY = "application/octet-stream"
OK = 200
CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
PRIVATE_FILE = 0o600
UNTRUSTED = "下載連結不是 GitHub 的 HTTPS 網址，已停止"
NETWORK = "無法連線到 GitHub"
TOO_LARGE = "下載的內容超過大小上限"
TIMEOUT = "連線逾時"
SAVE = "無法寫入暫存檔"


class FetchError(Exception):
    """下載失敗；訊息給使用者看（固定的中文說明）。"""


def status_text(code):
    return f"GitHub 回應異常（HTTP {code}）"


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    """每一次轉址都重新確認目的地；不可信時在送出請求之前停止。"""
    max_redirections = Update.MAX_REDIRECTS
    max_repeats = 2

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not is_trusted(newurl):
            raise FetchError(UNTRUSTED)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def https_handler():
    return urllib.request.HTTPSHandler(context=ssl.create_default_context())


def build_opener(https=None):
    """只有 HTTPS、代理伺服器與安全轉址的 opener（不用 urllib 預設的處理器組合）。"""
    opener = urllib.request.OpenerDirector()
    for handler in (urllib.request.ProxyHandler(), https or https_handler(), SafeRedirect(),
                    urllib.request.HTTPErrorProcessor(), urllib.request.HTTPDefaultErrorHandler(),
                    urllib.request.UnknownHandler()):
        opener.add_handler(handler)
    opener.addheaders = []
    return opener


def declared_length(response, limit):
    """Content-Length（沒有時為 limit）；不合理或超過上限時拋出 FetchError。"""
    text = response.headers.get("Content-Length")
    if text is None:
        return limit
    if not text.isascii() or not text.isdigit():
        raise FetchError(NETWORK)
    if int(text) > limit:
        raise FetchError(TOO_LARGE)
    return int(text)


class GitHubClient:
    def __init__(self, version, opener=None, clock=time.monotonic):
        self.agent = f"{Update.PACKAGE}/{version}"
        self.opener = opener or build_opener()
        self.clock = clock

    def open(self, url, accept):
        if not is_trusted(url):
            raise FetchError(UNTRUSTED)
        request = urllib.request.Request(url, headers={"User-Agent": self.agent,
                                                       "Accept": accept})
        response = self.opener.open(request, timeout=Update.REQUEST_TIMEOUT)
        if response is None:
            raise FetchError(NETWORK)
        if response.status != OK or not is_trusted(response.geturl()):
            response.close()
            raise FetchError(status_text(response.status) if response.status != OK
                             else UNTRUSTED)
        return response

    def transfer(self, url, accept, limit, deadline_s, sink):
        """下載 url，每一段交給 sink；回傳總長度。"""
        deadline = self.clock() + deadline_s
        try:
            with self.open(url, accept) as response:
                cap = declared_length(response, limit)
                total = 0
                while True:
                    if self.clock() > deadline:
                        raise FetchError(TIMEOUT)
                    chunk = response.read(min(CHUNK_BYTES, cap + 1 - total))
                    if not chunk:
                        return total
                    total += len(chunk)
                    if total > cap:
                        raise FetchError(TOO_LARGE)
                    sink(chunk)
        except FetchError:
            raise
        except urllib.error.HTTPError as error:
            raise FetchError(status_text(error.code)) from None
        except (TimeoutError, ssl.SSLError, urllib.error.URLError, http.client.HTTPException,
                OSError, ValueError):
            raise FetchError(NETWORK) from None

    def get(self, url, limit, deadline_s=Update.API_DEADLINE, accept=JSON):
        parts = []
        self.transfer(url, accept, limit, deadline_s, parts.append)
        return b"".join(parts)

    def download(self, url, path, limit, deadline_s=Update.DOWNLOAD_DEADLINE):
        """下載到新建立的私人檔案（0600、O_EXCL、O_NOFOLLOW）並回傳 SHA-256；失敗時刪掉。"""
        try:
            fd = os.open(path, CREATE, PRIVATE_FILE)
        except OSError:
            raise FetchError(SAVE) from None
        digest = hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as handle:
                def sink(chunk):
                    digest.update(chunk)
                    handle.write(chunk)
                self.transfer(url, BINARY, limit, deadline_s, sink)
        except BaseException:
            os.unlink(path)
            raise
        return digest.hexdigest()
