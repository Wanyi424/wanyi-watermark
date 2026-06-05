"""Offline tests for shared media fetching and siliconflow downloads."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from wanyi_watermark.media_fetch import FetchError, fetch_media_stream, site_headers


class MediaFetchTests(unittest.TestCase):
    def test_xhs_headers_include_referer(self):
        headers = site_headers("sns-video-hs.xhscdn.com")

        self.assertEqual(headers["Referer"], "https://www.xiaohongshu.com/")
        self.assertIn("Windows NT", headers["User-Agent"])

    @patch("wanyi_watermark.media_fetch.is_safe_public_url", return_value=True)
    @patch("wanyi_watermark.media_fetch.requests.get")
    def test_fetch_retries_transport_error(self, mock_get, _mock_safe):
        final = MagicMock()
        final.status_code = 200
        final.headers = {"Content-Type": "video/mp4"}
        mock_get.side_effect = [
            requests.exceptions.SSLError("unexpected eof"),
            final,
        ]

        result = fetch_media_stream("https://sns-video-hs.xhscdn.com/a.mp4", max_retries=1)

        self.assertIs(result, final)
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(mock_get.call_args.kwargs["headers"]["Referer"], "https://www.xiaohongshu.com/")

    @patch("wanyi_watermark.media_fetch.is_safe_public_url")
    @patch("wanyi_watermark.media_fetch.requests.get")
    def test_redirect_target_is_checked(self, mock_get, mock_safe):
        mock_safe.side_effect = [True, False]
        redirect = MagicMock()
        redirect.status_code = 302
        redirect.headers = {"Location": "http://127.0.0.1/private"}
        mock_get.return_value = redirect

        with self.assertRaisesRegex(FetchError, "重定向目标不被允许"):
            fetch_media_stream("https://sns-video-hs.xhscdn.com/start.mp4")

        redirect.close.assert_called_once()


class SiliconflowDownloadTests(unittest.TestCase):
    @patch("wanyi_watermark.siliconflow_asr.shutil.which", return_value=None)
    def test_ffmpeg_falls_back_to_imageio_binary(self, _mock_which):
        from wanyi_watermark.siliconflow_asr import _ffmpeg_exe

        self.assertTrue(Path(_ffmpeg_exe()).exists())

    @patch("wanyi_watermark.siliconflow_asr.fetch_media_stream")
    def test_siliconflow_download_uses_shared_fetcher(self, mock_fetch):
        from wanyi_watermark.siliconflow_asr import _download_video

        response = MagicMock()
        response.headers = {"content-length": "11"}
        response.iter_content.return_value = [b"hello ", b"video"]
        mock_fetch.return_value = response

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "video.mp4"
            _download_video("https://sns-video-hs.xhscdn.com/a.mp4", dest, False)

            self.assertEqual(dest.read_bytes(), b"hello video")

        mock_fetch.assert_called_once_with(
            "https://sns-video-hs.xhscdn.com/a.mp4",
            timeout=60,
            max_retries=2,
        )
        response.close.assert_called_once()

    @patch("wanyi_watermark.siliconflow_asr.fetch_media_stream")
    def test_siliconflow_download_wraps_fetch_error(self, mock_fetch):
        from wanyi_watermark.siliconflow_asr import _download_video

        mock_fetch.side_effect = FetchError("上游资源请求失败: unexpected eof", 502)

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "下载源视频失败"):
                _download_video("https://sns-video-hs.xhscdn.com/a.mp4", Path(tmp) / "video.mp4", False)


if __name__ == "__main__":
    unittest.main()
