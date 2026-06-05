"""离线测试 wanyi_watermark.media_fetch — 不依赖真实网络。"""

import ipaddress
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from wanyi_watermark.media_fetch import (
    site_headers,
    is_safe_public_url,
    fetch_media_stream,
    download_file,
    FetchError,
    MOBILE_UA,
    DESKTOP_UA,
)


# ──────────────────── site_headers ────────────────────


class TestSiteHeaders:
    def test_douyin_domain(self):
        h = site_headers("v3-dy.ixigua.com")
        assert h["User-Agent"] == MOBILE_UA
        assert h["Referer"] == "https://www.douyin.com/"

    def test_douyin_keywords(self):
        for host in ("cdn.iesdouyin.com", "video.douyinvod.com", "p3.bytecdn.com"):
            h = site_headers(host)
            assert h["Referer"] == "https://www.douyin.com/", f"failed for {host}"

    def test_xhs_domain(self):
        h = site_headers("ci.xhscdn.com")
        assert h["User-Agent"] == DESKTOP_UA
        assert h["Referer"] == "https://www.xiaohongshu.com/"

    def test_xhs_main(self):
        h = site_headers("www.xiaohongshu.com")
        assert h["Referer"] == "https://www.xiaohongshu.com/"

    def test_generic_domain(self):
        h = site_headers("example.com")
        assert h["User-Agent"] == DESKTOP_UA
        assert "Referer" not in h

    def test_empty_host(self):
        h = site_headers("")
        assert h["User-Agent"] == DESKTOP_UA


# ──────────────────── is_safe_public_url ────────────────────


class TestIsSafePublicUrl:
    def test_blocks_private_ip(self):
        assert is_safe_public_url("http://192.168.1.1/video.mp4") is False
        assert is_safe_public_url("http://10.0.0.1/x") is False
        assert is_safe_public_url("http://127.0.0.1/x") is False

    def test_blocks_non_http_scheme(self):
        assert is_safe_public_url("ftp://example.com/file") is False
        assert is_safe_public_url("file:///etc/passwd") is False

    def test_blocks_empty_or_invalid(self):
        assert is_safe_public_url("") is False
        assert is_safe_public_url("not-a-url") is False

    @patch("wanyi_watermark.media_fetch.socket.getaddrinfo")
    def test_allows_public_domain(self, mock_dns):
        mock_dns.return_value = [(None, None, None, None, ("93.184.216.34", 0))]
        assert is_safe_public_url("https://example.com/video.mp4") is True

    @patch("wanyi_watermark.media_fetch.socket.getaddrinfo")
    def test_blocks_domain_resolving_to_private(self, mock_dns):
        mock_dns.return_value = [(None, None, None, None, ("192.168.0.1", 0))]
        assert is_safe_public_url("https://evil.com/x") is False

    @patch("wanyi_watermark.media_fetch.socket.getaddrinfo")
    def test_fakeip_whitelisted_domain(self, mock_dns):
        mock_dns.return_value = [(None, None, None, None, ("198.18.0.5", 0))]
        assert is_safe_public_url("https://cdn.douyinvod.com/video.mp4") is True

    @patch("wanyi_watermark.media_fetch.socket.getaddrinfo")
    def test_fakeip_non_whitelisted_domain(self, mock_dns):
        mock_dns.return_value = [(None, None, None, None, ("198.18.0.5", 0))]
        assert is_safe_public_url("https://evil.example.com/x") is False


# ──────────────────── fetch_media_stream ────────────────────


class TestFetchMediaStream:
    @patch("wanyi_watermark.media_fetch.is_safe_public_url", return_value=True)
    @patch("wanyi_watermark.media_fetch.requests.get")
    def test_direct_200(self, mock_get, mock_safe):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "video/mp4"}
        mock_get.return_value = mock_resp

        result = fetch_media_stream("https://cdn.douyinvod.com/v.mp4")
        assert result is mock_resp
        call_headers = mock_get.call_args[1]["headers"]
        assert call_headers["Referer"] == "https://www.douyin.com/"

    @patch("wanyi_watermark.media_fetch.is_safe_public_url", return_value=True)
    @patch("wanyi_watermark.media_fetch.requests.get")
    def test_follows_redirect(self, mock_get, mock_safe):
        redirect_resp = MagicMock()
        redirect_resp.status_code = 302
        redirect_resp.headers = {"Location": "https://cdn.douyinvod.com/final.mp4"}
        redirect_resp.close = MagicMock()

        final_resp = MagicMock()
        final_resp.status_code = 200
        final_resp.headers = {"Content-Type": "video/mp4"}

        mock_get.side_effect = [redirect_resp, final_resp]

        result = fetch_media_stream("https://www.douyin.com/redirect")
        assert result is final_resp
        assert mock_get.call_count == 2

    @patch("wanyi_watermark.media_fetch.is_safe_public_url")
    @patch("wanyi_watermark.media_fetch.requests.get")
    def test_blocks_redirect_to_internal(self, mock_get, mock_safe):
        mock_safe.side_effect = [True, False]

        redirect_resp = MagicMock()
        redirect_resp.status_code = 302
        redirect_resp.headers = {"Location": "http://192.168.1.1/internal"}
        redirect_resp.close = MagicMock()
        mock_get.return_value = redirect_resp

        with pytest.raises(FetchError, match="非法或不被允许"):
            fetch_media_stream("https://cdn.douyinvod.com/start")

    @patch("wanyi_watermark.media_fetch.is_safe_public_url", return_value=True)
    @patch("wanyi_watermark.media_fetch.requests.get")
    def test_raises_on_upstream_4xx(self, mock_get, mock_safe):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.headers = {}
        mock_resp.close = MagicMock()
        mock_get.return_value = mock_resp

        with pytest.raises(FetchError, match="上游资源返回 403"):
            fetch_media_stream("https://cdn.xhscdn.com/img.jpg")

    @patch("wanyi_watermark.media_fetch.is_safe_public_url", return_value=True)
    @patch("wanyi_watermark.media_fetch.requests.get")
    def test_too_many_redirects(self, mock_get, mock_safe):
        redirect_resp = MagicMock()
        redirect_resp.status_code = 302
        redirect_resp.headers = {"Location": "https://cdn.douyinvod.com/loop"}
        redirect_resp.close = MagicMock()
        mock_get.return_value = redirect_resp

        with pytest.raises(FetchError, match="重定向次数过多"):
            fetch_media_stream("https://cdn.douyinvod.com/start")

    @patch("wanyi_watermark.media_fetch.is_safe_public_url", return_value=True)
    @patch("wanyi_watermark.media_fetch.requests.get")
    def test_range_header_forwarded(self, mock_get, mock_safe):
        mock_resp = MagicMock()
        mock_resp.status_code = 206
        mock_resp.headers = {"Content-Type": "video/mp4", "Content-Range": "bytes 0-99/200"}
        mock_get.return_value = mock_resp

        result = fetch_media_stream("https://cdn.douyinvod.com/v.mp4", range_header="bytes=0-99")
        call_headers = mock_get.call_args[1]["headers"]
        assert call_headers["Range"] == "bytes=0-99"
        assert result.status_code == 206


# ──────────────────── download_file ────────────────────


class TestDownloadFile:
    @patch("wanyi_watermark.media_fetch.fetch_media_stream")
    def test_writes_content(self, mock_fetch):
        content = b"fake video content here"
        mock_resp = MagicMock()
        mock_resp.headers = {"content-length": str(len(content))}
        mock_resp.iter_content.return_value = [content[:10], content[10:]]
        mock_resp.close = MagicMock()
        mock_fetch.return_value = mock_resp

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "sub" / "video.mp4"
            result = download_file("https://cdn.douyinvod.com/v.mp4", dest, show_progress=False)
            assert result == dest
            assert dest.read_bytes() == content

    @patch("wanyi_watermark.media_fetch.fetch_media_stream")
    def test_raises_on_fetch_error(self, mock_fetch):
        mock_fetch.side_effect = FetchError("上游资源返回 403", 403)

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "video.mp4"
            with pytest.raises(FetchError):
                download_file("https://cdn.xhscdn.com/v.mp4", dest, show_progress=False)
