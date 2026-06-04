"""通用无水印资源提取器。

用于在抖音/小红书专用解析失败或遇到未知平台时，
尝试基于页面通用信息（og:video、<video>、直链等）获取无水印视频。
"""

from __future__ import annotations

import html
import logging
import re
import time
from typing import Dict

import requests

from .diagnostics import parse_log, short_text

logger = logging.getLogger(__name__)


GENERIC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


URL_PATTERN = re.compile(
    r"http[s]?://(?:[a-zA-Z0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F]{2}))+",
    flags=re.IGNORECASE,
)

MEDIA_PATTERNS = [
    # 优先解析 og:video / og:video:url 等标准元信息
    re.compile(
        r'<meta[^>]+(?:property|name)=["\']og:(?:video|video:url)["\'][^>]+content=["\'](?P<url>[^"\']+)["\']',
        flags=re.IGNORECASE,
    ),
    # 兼容 Twitter 的播放器元标签
    re.compile(
        r'<meta[^>]+(?:property|name)=["\']twitter:player["\'][^>]+content=["\'](?P<url>[^"\']+)["\']',
        flags=re.IGNORECASE,
    ),
    # video/source 标签直链
    re.compile(r'<video[^>]+src=["\'](?P<url>[^"\']+)["\']', flags=re.IGNORECASE),
    re.compile(r'<source[^>]+src=["\'](?P<url>[^"\']+)["\']', flags=re.IGNORECASE),
    # 页面内裸露的 mp4/m3u8 链接
    re.compile(
        r'(?P<url>https?://[^\s"\']+?\.(?:mp4|m3u8)(?:\?[^"\']*)?)',
        flags=re.IGNORECASE,
    ),
]


def _extract_first_url(text: str) -> str:
    match = URL_PATTERN.search(text)
    if not match:
        raise ValueError("未找到有效链接")
    return match.group(0)


def _extract_meta(html_text: str, key: str) -> str | None:
    pattern = re.compile(
        rf'<meta[^>]+(?:name|property)=["\']{re.escape(key)}["\'][^>]+content=["\'](.*?)["\']',
        flags=re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(html_text)
    if match:
        return html.unescape(match.group(1)).strip()
    return None


def _find_media_url(html_text: str) -> str | None:
    for pattern in MEDIA_PATTERNS:
        match = pattern.search(html_text)
        if match:
            media_url = html.unescape(match.group("url")).strip()
            if media_url:
                parse_log(logger, "通用解析命中媒体提取模式：%s", pattern.pattern[:40] + "...")
                return media_url
    return None


def extract_generic_media(share_text: str) -> Dict[str, str]:
    """尝试从任意链接中提取无水印视频信息。

    返回:
        dict: 包含 platform/type/url/title/caption 的基础信息。

    失败时抛出 ValueError，便于调用方根据需要返回原始错误。
    """

    share_url = _extract_first_url(share_text)
    flow_start = time.perf_counter()
    parse_log(logger, "通用解析开始：分享链接=%s", short_text(share_url))

    step = time.perf_counter()
    response = requests.get(share_url, headers=GENERIC_HEADERS, timeout=10, allow_redirects=True)
    response.raise_for_status()
    parse_log(
        logger,
        "通用解析页面请求完成：HTTP %s，最终地址=%s，HTML长度=%d",
        response.status_code,
        short_text(response.url),
        len(response.text or ""),
        step_start=step,
        flow_start=flow_start,
    )

    final_url = response.url
    html_text = response.text

    step = time.perf_counter()
    media_url = _find_media_url(html_text)
    if not media_url:
        raise ValueError("未从页面中发现可用的视频直链")
    parse_log(logger, "通用解析媒体直链提取完成：%s", short_text(media_url), step_start=step, flow_start=flow_start)

    step = time.perf_counter()
    title = (
        _extract_meta(html_text, "og:title")
        or _extract_meta(html_text, "twitter:title")
        or final_url
    )

    caption = _extract_meta(html_text, "og:description") or _extract_meta(html_text, "description")
    parse_log(
        logger,
        "通用解析元信息提取完成：标题长度=%d，文案长度=%d",
        len(title or ""),
        len(caption or ""),
        step_start=step,
        flow_start=flow_start,
    )

    return {
        "status": "success",
        "type": "video",
        "platform": "generic",
        "title": title.strip() if title else None,
        "caption": caption.strip() if caption else None,
        "url": media_url,
        "source_url": final_url,
    }
