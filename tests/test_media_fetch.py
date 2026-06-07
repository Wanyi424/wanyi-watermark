"""离线测试 media_fetch 模块及其在 WebUI / CLI 中的集成 —— mock HTTP + DNS，无需真实链接。"""

import socket
from pathlib import Path
from unittest.mock import patch

import pytest
import responses

from wanyi_watermark.media_fetch import (
    MediaFetchError,
    fetch_media,
    host_in_whitelist,
    is_safe_public_url,
    site_headers,
)


# ──────────────────────────────────────────────────────────────────
# DNS mock：所有域名解析到公网 IP，避免真实网络依赖
# ──────────────────────────────────────────────────────────────────
_FAKE_PUBLIC_ADDR = [
    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
]


@pytest.fixture(autouse=True)
def _mock_dns(monkeypatch):
    """全局 mock getaddrinfo，域名一律解析到公网 IP。"""
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port, *a, **kw: _FAKE_PUBLIC_ADDR,
    )


# ──────────────────────────────────────────────────────────────────
# site_headers
# ──────────────────────────────────────────────────────────────────
class TestSiteHeaders:
    def test_douyin_domain(self):
        h = site_headers("v3-dy.douyinvod.com")
        assert "iPhone" in h["User-Agent"]
        assert h["Referer"] == "https://www.douyin.com/"

    def test_xhs_domain(self):
        h = site_headers("ci.xiaohongshu.com")
        assert "Windows" in h["User-Agent"]
        assert h["Referer"] == "https://www.xiaohongshu.com/"

    def test_generic_domain(self):
        h = site_headers("cdn.example.com")
        assert "Windows" in h["User-Agent"]
        assert "Referer" not in h


# ──────────────────────────────────────────────────────────────────
# host_in_whitelist
# ──────────────────────────────────────────────────────────────────
class TestHostInWhitelist:
    def test_exact_match(self):
        assert host_in_whitelist("douyin.com") is True

    def test_subdomain_match(self):
        assert host_in_whitelist("v3-dy.douyinvod.com") is True
        assert host_in_whitelist("ci.xiaohongshu.com") is True

    def test_no_match(self):
        assert host_in_whitelist("evil.com") is False
        assert host_in_whitelist("notdouyin.com") is False


# ──────────────────────────────────────────────────────────────────
# is_safe_public_url （字面 IP 不走 DNS，域名走 mock DNS）
# ──────────────────────────────────────────────────────────────────
class TestIsSafePublicUrl:
    def test_blocks_private_ips(self):
        assert is_safe_public_url("http://127.0.0.1/foo") is False
        assert is_safe_public_url("http://10.0.0.1/bar") is False
        assert is_safe_public_url("http://192.168.1.1/baz") is False

    def test_blocks_non_http(self):
        assert is_safe_public_url("ftp://example.com/file") is False
        assert is_safe_public_url("file:///etc/passwd") is False

    def test_allows_public_ip(self):
        assert is_safe_public_url("https://8.8.8.8/resource") is True

    def test_allows_domain_resolving_public(self):
        assert is_safe_public_url("https://v3-dy.douyinvod.com/video.mp4") is True

    def test_blocks_domain_resolving_private(self, monkeypatch):
        monkeypatch.setattr(
            socket, "getaddrinfo",
            lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0))],
        )
        assert is_safe_public_url("https://evil.internal/x") is False


# ──────────────────────────────────────────────────────────────────
# fetch_media 核心
# ──────────────────────────────────────────────────────────────────
class TestFetchMedia:
    @responses.activate
    def test_success_direct(self):
        url = "https://v3-dy.douyinvod.com/video.mp4"
        responses.add(responses.GET, url, body=b"fake-video", status=200,
                      headers={"Content-Type": "video/mp4"})
        resp = fetch_media(url)
        assert resp.status_code == 200
        assert resp.content == b"fake-video"
        resp.close()

    @responses.activate
    def test_redirect_safe(self):
        url1 = "https://v3-dy.douyinvod.com/redirect"
        url2 = "https://v3-dy.douyinvod.com/video.mp4"
        responses.add(responses.GET, url1, status=302,
                      headers={"Location": url2})
        responses.add(responses.GET, url2, body=b"ok", status=200)
        resp = fetch_media(url1)
        assert resp.status_code == 200
        assert resp.content == b"ok"
        resp.close()

    def test_blocks_literal_private_ip(self):
        with pytest.raises(MediaFetchError) as exc_info:
            fetch_media("http://127.0.0.1/internal")
        assert exc_info.value.status_code == 400

    @responses.activate
    def test_redirect_to_ssrf(self):
        """第一跳合法，第二跳重定向到内网 → 拦截。"""
        url1 = "https://v3-dy.douyinvod.com/open-redir"
        url2 = "http://10.0.0.1/secret"
        responses.add(responses.GET, url1, status=302,
                      headers={"Location": url2})
        with pytest.raises(MediaFetchError) as exc_info:
            fetch_media(url1)
        assert exc_info.value.status_code == 400

    @responses.activate
    def test_upstream_403(self):
        url = "https://v3-dy.douyinvod.com/forbidden"
        responses.add(responses.GET, url, status=403)
        with pytest.raises(MediaFetchError) as exc_info:
            fetch_media(url)
        assert exc_info.value.status_code == 403

    @responses.activate
    def test_too_many_redirects(self):
        url = "https://v3-dy.douyinvod.com/loop"
        responses.add(responses.GET, url, status=302,
                      headers={"Location": url})
        with pytest.raises(MediaFetchError) as exc_info:
            fetch_media(url)
        assert "重定向" in exc_info.value.message

    @responses.activate
    def test_range_header_passed(self):
        url = "https://v3-dy.douyinvod.com/video.mp4"
        responses.add(responses.GET, url, body=b"partial", status=206,
                      headers={"Content-Range": "bytes 0-6/100"})
        resp = fetch_media(url, range_header="bytes=0-6")
        assert resp.status_code == 206
        assert responses.calls[0].request.headers["Range"] == "bytes=0-6"
        resp.close()


# ──────────────────────────────────────────────────────────────────
# WebUI /api/proxy 回归测试
# ──────────────────────────────────────────────────────────────────
class TestWebUIProxy:
    """验证 /api/proxy 端点行为不退化。"""

    @pytest.fixture(autouse=True)
    def _client(self):
        import sys, importlib.util
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        spec = importlib.util.spec_from_file_location(
            "web_app", str(Path(__file__).resolve().parent.parent / "web" / "app.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        from starlette.testclient import TestClient
        self.client = TestClient(mod.app)

    @responses.activate
    def test_proxy_success_streams_content(self):
        url = "https://ci.xiaohongshu.com/img.jpg"
        responses.add(responses.GET, url, body=b"\x89PNG", status=200,
                      headers={"Content-Type": "image/png", "Content-Length": "4"})
        resp = self.client.get("/api/proxy", params={"url": url})
        assert resp.status_code == 200
        assert resp.content == b"\x89PNG"
        assert resp.headers["content-type"] == "image/png"

    @responses.activate
    def test_proxy_range_passthrough(self):
        """Range 请求透传至上游并返回 206。"""
        url = "https://v3-dy.douyinvod.com/video.mp4"
        responses.add(responses.GET, url, body=b"partial", status=206,
                      headers={"Content-Type": "video/mp4", "Content-Range": "bytes 0-6/1000"})
        resp = self.client.get("/api/proxy", params={"url": url},
                              headers={"Range": "bytes=0-6"})
        assert resp.status_code == 206
        assert resp.headers.get("content-range") == "bytes 0-6/1000"
        assert responses.calls[0].request.headers["Range"] == "bytes=0-6"

    @responses.activate
    def test_proxy_download_disposition(self):
        """download=1 时返回 Content-Disposition 强制下载。"""
        url = "https://v3-dy.douyinvod.com/video.mp4"
        responses.add(responses.GET, url, body=b"data", status=200,
                      headers={"Content-Type": "video/mp4"})
        resp = self.client.get("/api/proxy",
                              params={"url": url, "download": 1, "filename": "test.mp4"})
        assert resp.status_code == 200
        assert "attachment" in resp.headers["content-disposition"]
        assert "test.mp4" in resp.headers["content-disposition"]

    @responses.activate
    def test_proxy_upstream_403(self):
        """上游 403 → 代理返回 403 错误文本。"""
        url = "https://v3-dy.douyinvod.com/blocked"
        responses.add(responses.GET, url, status=403)
        resp = self.client.get("/api/proxy", params={"url": url})
        assert resp.status_code == 403
        assert "403" in resp.text

    def test_proxy_ssrf_blocked(self):
        """内网地址直接拦截 400。"""
        resp = self.client.get("/api/proxy", params={"url": "http://192.168.1.1/secret"})
        assert resp.status_code == 400

    @responses.activate
    def test_proxy_redirect_to_ssrf_blocked(self):
        """合法域名重定向到内网 → 拦截。"""
        url = "https://v3-dy.douyinvod.com/evil-redir"
        responses.add(responses.GET, url, status=302,
                      headers={"Location": "http://10.0.0.1/internal"})
        resp = self.client.get("/api/proxy", params={"url": url})
        assert resp.status_code == 400

    @responses.activate
    def test_proxy_accept_ranges_header(self):
        """响应始终包含 Accept-Ranges: bytes。"""
        url = "https://ci.xiaohongshu.com/img.jpg"
        responses.add(responses.GET, url, body=b"ok", status=200,
                      headers={"Content-Type": "image/jpeg"})
        resp = self.client.get("/api/proxy", params={"url": url})
        assert resp.headers.get("accept-ranges") == "bytes"


# ──────────────────────────────────────────────────────────────────
# CLI _download_file 回归测试
# ──────────────────────────────────────────────────────────────────
class TestCLIDownload:
    """验证 CLI 复用 media_fetch 的取源逻辑与错误处理。"""

    @responses.activate
    def test_download_file_success(self, tmp_path):
        from wanyi_watermark.cli import _download_file
        url = "https://v3-dy.douyinvod.com/video.mp4"
        responses.add(responses.GET, url, body=b"video-bytes", status=200,
                      headers={"Content-Length": "11", "Content-Type": "video/mp4"})
        dest = tmp_path / "out.mp4"
        result = _download_file(url, dest, show_progress=False)
        assert result == dest
        assert dest.read_bytes() == b"video-bytes"

    @responses.activate
    def test_download_file_uses_correct_referer(self, tmp_path):
        """抖音域名下载应携带 douyin Referer。"""
        from wanyi_watermark.cli import _download_file
        url = "https://v3-dy.douyinvod.com/video.mp4"
        responses.add(responses.GET, url, body=b"ok", status=200)
        _download_file(url, tmp_path / "v.mp4", show_progress=False)
        assert responses.calls[0].request.headers["Referer"] == "https://www.douyin.com/"

    @responses.activate
    def test_download_file_xhs_referer(self, tmp_path):
        """小红书域名下载应携带 xiaohongshu Referer。"""
        from wanyi_watermark.cli import _download_file
        url = "https://ci.xiaohongshu.com/img.png"
        responses.add(responses.GET, url, body=b"img", status=200)
        _download_file(url, tmp_path / "img.png", show_progress=False)
        assert responses.calls[0].request.headers["Referer"] == "https://www.xiaohongshu.com/"

    @responses.activate
    def test_download_file_403_raises(self, tmp_path):
        """上游 403 → MediaFetchError 抛出。"""
        from wanyi_watermark.cli import _download_file
        url = "https://ci.xiaohongshu.com/blocked.png"
        responses.add(responses.GET, url, status=403)
        with pytest.raises(MediaFetchError) as exc_info:
            _download_file(url, tmp_path / "x.png", show_progress=False)
        assert exc_info.value.status_code == 403

    def test_download_file_ssrf_blocked(self, tmp_path):
        """内网地址直接拦截。"""
        from wanyi_watermark.cli import _download_file
        with pytest.raises(MediaFetchError) as exc_info:
            _download_file("http://127.0.0.1/evil", tmp_path / "x", show_progress=False)
        assert exc_info.value.status_code == 400
