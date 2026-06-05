"""Shared media fetching helpers.

This module keeps the anti-hotlinking headers, redirect handling, and basic
SSRF checks in one place so WebUI proxy and local download/transcription paths
behave consistently.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Dict, Optional
from urllib.parse import urljoin, urlparse

import requests


MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

MAX_REDIRECTS = 5
FAKE_IP_NET = ipaddress.ip_network("198.18.0.0/15")
MEDIA_HOST_WHITELIST = (
    "douyin.com", "iesdouyin.com", "amemv.com", "snssdk.com",
    "douyinpic.com", "douyinvod.com", "byteimg.com", "bytecdn.com",
    "ixigua.com", "ixiguavideo.com", "pstatp.com", "zjcdn.com",
    "xiaohongshu.com", "xhscdn.com", "xhslink.com",
)


class FetchError(Exception):
    """Media fetch failed with an HTTP-like status code."""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


def site_headers(host: str) -> Dict[str, str]:
    """Return UA/Referer headers for a media host."""
    host = (host or "").lower()
    headers: Dict[str, str] = {"Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9"}
    if any(k in host for k in ("douyin", "iesdouyin", "amemv", "bytecdn", "douyinpic", "douyinvod", "ixigua")):
        headers["User-Agent"] = MOBILE_UA
        headers["Referer"] = "https://www.douyin.com/"
    elif any(k in host for k in ("xhscdn.com", "xiaohongshu.com")):
        headers["User-Agent"] = DESKTOP_UA
        headers["Referer"] = "https://www.xiaohongshu.com/"
    else:
        headers["User-Agent"] = DESKTOP_UA
    return headers


def is_safe_public_url(url: str) -> bool:
    """Allow only http(s) URLs whose resolved addresses are not private."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False

    try:
        literal = ipaddress.ip_address(host)
        return not _addr_blocked(literal)
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    if not infos:
        return False

    whitelisted = _host_in_whitelist(host)
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if ip.version == 4 and ip in FAKE_IP_NET:
            if not whitelisted:
                return False
            continue
        if _addr_blocked(ip):
            return False
    return True


def fetch_media_stream(
    url: str,
    *,
    range_header: Optional[str] = None,
    timeout: int = 30,
    max_redirects: int = MAX_REDIRECTS,
    max_retries: int = 0,
) -> requests.Response:
    """Fetch a media URL with anti-hotlinking headers and safe redirects.

    The returned response is opened with ``stream=True``; callers must close it.
    ``max_retries`` retries transport-level failures on the same URL before
    surfacing a FetchError.
    """
    current = url
    redirects = 0
    while redirects <= max_redirects:
        if not is_safe_public_url(current):
            msg = "非法或不被允许的资源地址" if redirects == 0 else "重定向目标不被允许"
            raise FetchError(msg, 400)

        headers = site_headers(urlparse(current).hostname or "")
        if range_header:
            headers["Range"] = range_header

        resp = _request_with_retries(current, headers, timeout, max_retries)
        if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
            next_url = urljoin(current, resp.headers["Location"])
            resp.close()
            current = next_url
            redirects += 1
            continue

        if resp.status_code >= 400:
            code = resp.status_code
            resp.close()
            raise FetchError(f"上游资源返回 {code}", code)
        return resp

    raise FetchError("重定向次数过多", 502)


def _request_with_retries(url: str, headers: Dict[str, str], timeout: int, max_retries: int) -> requests.Response:
    last_error: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            return requests.get(
                url,
                headers=headers,
                stream=True,
                timeout=timeout,
                allow_redirects=False,
            )
        except requests.RequestException as e:
            last_error = e
            if attempt >= max_retries:
                break
    raise FetchError(f"上游资源请求失败: {last_error}", 502)


def _host_in_whitelist(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in MEDIA_HOST_WHITELIST)


def _addr_blocked(ip) -> bool:
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )
