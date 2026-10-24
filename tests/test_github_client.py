"""GitHub 的 HTTPS 用戶端：主機白名單、每次轉址都重新確認、大小上限、逾時與標頭。

以假的 HTTPS 處理器代替網路（依網址回傳設定好的狀態、標頭與內容），絕不連線。
"""
import email.message
import hashlib
import http.client
import io
import os
from pathlib import Path
import ssl
import stat
import unittest
import urllib.error
import urllib.request
import urllib.response

from sdm.constants import Update
from sdm.data import github
from tests.fixtures.signing import private_dir

API = Update.RELEASES_API
ASSET = "https://github.com/sgeric910601/system-device-monitor/releases/download/v9.9.9/a.deb"
CDN = "https://release-assets.githubusercontent.com/github-production-release-asset/1/a"


class FakeHttps(urllib.request.BaseHandler):
    """網址 →（狀態、標頭、內容）或例外；記錄每一個請求。"""

    def __init__(self, routes):
        self.routes, self.requests, self.responses = routes, [], []

    def https_open(self, request):
        self.requests.append(request)
        route = self.routes[request.full_url]
        if isinstance(route, BaseException):
            raise route
        status, headers, body = route
        message = email.message.Message()
        for key, value in headers.items():
            message[key] = value
        response = urllib.response.addinfourl(io.BytesIO(body), message, request.full_url,
                                               status)
        response.msg = "OK" if status == 200 else "Other"
        self.responses.append(response)
        return response


def ok(body, **headers):
    return 200, headers, body


def moved(location):
    return 302, {"Location": location}, b""


class Clock:
    def __init__(self, step=0.0):
        self.now, self.step = 0.0, step

    def __call__(self):
        self.now += self.step
        return self.now


class Base(unittest.TestCase):
    def client(self, routes, clock=None):
        self.https = FakeHttps(routes)
        return github.GitHubClient("9.9.9", opener=github.build_opener(self.https),
                                   clock=clock or Clock())

    def refused(self, client, url, limit=1024):
        with self.assertRaises(github.FetchError):
            client.get(url, limit)


class Requests(Base):
    def test_returns_the_body_with_fixed_headers_only(self):
        client = self.client({API: ok(b"[]", **{"Content-Length": "2"})})
        self.assertEqual(client.get(API, Update.API_MAX_BYTES), b"[]")
        request = self.https.requests[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(headers, {"user-agent": "system-device-monitor/9.9.9",
                                   "accept": "application/vnd.github+json"})

    def test_untrusted_first_urls_are_never_requested(self):
        client = self.client({})
        for url in ("http://api.github.com/x", "https://evil.example.com/x",
                    "https://github.com.evil.com/x", "file:///etc/passwd",
                    "https://user" + "@github.com/x", "ftp://github.com/x"):
            with self.subTest(url=url):
                self.refused(client, url)
        self.assertEqual(self.https.requests, [])

    def test_only_200_is_accepted(self):
        for status in (204, 206, 301, 403, 404, 500):
            with self.subTest(status=status):
                headers = {"Location": "https://evil.example.com/"} if status == 301 else {}
                self.refused(self.client({API: (status, headers, b"x")}), API)

    def test_refused_responses_are_closed(self):
        client = self.client({API: (204, {}, b"")})
        self.refused(client, API)
        self.assertTrue(self.https.responses[0].fp.closed)

    def test_network_failures_become_fetch_errors(self):
        for error in (urllib.error.URLError("down"), TimeoutError(), ssl.SSLError("bad cert"),
                      http.client.IncompleteRead(b""), ConnectionResetError(), ValueError()):
            with self.subTest(error=type(error).__name__):
                self.refused(self.client({API: error}), API)

    def test_error_messages_do_not_contain_urls_or_server_text(self):
        client = self.client({API: (500, {}, b"secret server text")})
        with self.assertRaises(github.FetchError) as caught:
            client.get(API, 1024)
        self.assertNotIn("github.com", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))


class Redirects(Base):
    def test_trusted_redirects_are_followed(self):
        client = self.client({ASSET: moved(CDN), CDN: ok(b"deb")})
        self.assertEqual(client.get(ASSET, 1024), b"deb")
        self.assertEqual([request.full_url for request in self.https.requests], [ASSET, CDN])
        self.assertEqual(self.https.requests[1].get_header("User-agent"),
                         "system-device-monitor/9.9.9")

    def test_every_hop_is_checked(self):
        for target in ("https://evil.example.com/a", "http://github.com/a",
                       "https://github.com.evil.com/a", "https://githubusercontent.com/a",
                       "https://objects.githubusercontent.com:8443/a", "file:///etc/shadow"):
            with self.subTest(target=target):
                hop = "https://objects.githubusercontent.com/hop"
                client = self.client({ASSET: moved(hop), hop: moved(target), target: ok(b"x")})
                self.refused(client, ASSET)
                self.assertNotIn(target, [request.full_url for request in self.https.requests])

    def test_redirect_loops_stop(self):
        first, second = CDN + "1", CDN + "2"
        self.refused(self.client({ASSET: moved(first), first: moved(second),
                                  second: moved(first)}), ASSET)
        self.assertLessEqual(len(self.https.requests), Update.MAX_REDIRECTS + 2)


class Limits(Base):
    def test_declared_length_over_the_limit_is_refused(self):
        self.refused(self.client({API: ok(b"x" * 10, **{"Content-Length": "2048"})}), API)
        self.refused(self.client({API: ok(b"x", **{"Content-Length": "-1"})}), API)

    def test_body_over_the_limit_is_refused_without_a_length(self):
        self.refused(self.client({API: ok(b"x" * 1025)}), API, limit=1024)
        self.assertEqual(self.client({API: ok(b"x" * 1024)}).get(API, 1024), b"x" * 1024)

    def test_body_longer_than_its_declared_length_is_refused(self):
        self.refused(self.client({API: ok(b"x" * 2000, **{"Content-Length": "10"})}), API)

    def test_caps_match_the_plan(self):
        self.assertEqual(Update.API_MAX_BYTES, 1024 * 1024)
        self.assertEqual(Update.CHECKSUMS_MAX_BYTES, 64 * 1024)
        self.assertEqual(Update.SIGNATURE_MAX_BYTES, 1024)
        self.assertEqual(Update.DEB_MAX_BYTES, 30 * 1024 * 1024)

    def test_deadline_stops_a_slow_transfer(self):
        body = b"x" * (github.CHUNK_BYTES * 4)
        client = self.client({API: ok(body)}, clock=Clock(step=Update.API_DEADLINE / 2))
        self.refused(client, API, limit=len(body))


class Download(Base):
    def setUp(self):
        self.dir = private_dir(self)
        self.target = self.dir/"a.deb"

    def test_writes_a_private_file_and_returns_its_hash(self):
        body = b"package" * 1000
        client = self.client({ASSET: moved(CDN), CDN: ok(body)})
        self.assertEqual(client.download(ASSET, self.target, len(body)),
                         hashlib.sha256(body).hexdigest())
        self.assertEqual(self.target.read_bytes(), body)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)

    def test_partial_files_are_removed(self):
        client = self.client({ASSET: ok(b"x" * 2048)})
        with self.assertRaises(github.FetchError):
            client.download(ASSET, self.target, 1024)
        self.assertFalse(self.target.exists())

    def test_existing_files_and_symlinks_are_never_overwritten(self):
        self.target.write_bytes(b"mine")
        client = self.client({ASSET: ok(b"new")})
        with self.assertRaises(github.FetchError):
            client.download(ASSET, self.target, 1024)
        self.assertEqual(self.target.read_bytes(), b"mine")
        link = self.dir/"link.deb"
        link.symlink_to(self.dir/"elsewhere")
        with self.assertRaises(github.FetchError):
            client.download(ASSET, link, 1024)
        self.assertFalse((self.dir/"elsewhere").exists())


class DefaultOpener(unittest.TestCase):
    def test_only_https_and_safe_redirects_are_installed(self):
        opener = github.build_opener()
        kinds = {type(handler) for handler in opener.handlers}
        for refused in (urllib.request.HTTPHandler, urllib.request.FileHandler,
                        urllib.request.FTPHandler, urllib.request.DataHandler,
                        urllib.request.HTTPCookieProcessor, urllib.request.HTTPBasicAuthHandler,
                        urllib.request.HTTPRedirectHandler):
            self.assertNotIn(refused, kinds)
        self.assertIn(github.SafeRedirect, kinds)
        https = next(h for h in opener.handlers if isinstance(h, urllib.request.HTTPSHandler))
        context = https._context
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(opener.addheaders, [])

    def test_plain_http_fails_before_any_connection(self):
        client = github.GitHubClient("9.9.9")
        with self.assertRaises(github.FetchError):
            client.get("http://127.0.0.1:9/", 10)


if __name__ == "__main__":
    unittest.main()
