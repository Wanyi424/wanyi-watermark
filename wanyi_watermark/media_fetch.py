"""
媒体资源取源模块 —— 解决 CDN 防盗链（403）问题。

按目标域名自动选择 UA / Referer，手动跟随重定向并逐跳做 SSRF 校验。
供 CLI 下载和 WebUI /api/proxy 共用。
"""

import socket
import ipaddress
from urllib.parse import urlparse, urljoin
from typing import Dict, Optional

import requests

# ──────────────────────────────────────────────────────────────────
# UA / Referer
# ──────────────────────────────────────────────────────────────────
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ──────────────────────────────────────────────────────────────────
# 域名白名单（后缀匹配）
# ──────────────────────────────────────────────────────────────────
MEDIA_HOST_WHITELIST = (
    # 抖音 / 字节系
    "douyin.com", "iesdouyin.com", "amemv.com", "snssdk.com",
    "douyinpic.com", "douyinvod.com", "byteimg.com", "bytecdn.com",
    "ixigua.com", "ixiguavideo.com", "pstatp.com", "zjcdn.com",
    # 小红书
    "xiaohongshu.com", "xhscdn.com", "xhslink.com",
)

MAX_REDIRECTS = 5

# Clash / sing-box Fake-IP 网段（RFC 2544 基准测试段）
_FAKE_IP_NET = ipaddress.ip_network("198.18.0.0/15")


# ──────────────────────────────────────────────────────────────────
# 异常
# ──────────────────────────────────────────────────────────────────
class MediaFetchError(Exception):
    """媒体取源失败。"""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ──────────────────────────────────────────────────────────────────
# 公开函数
# ──────────────────────────────────────────────────────────────────
def site_headers(host: str) -> Dict[str, str]:
    """按目标域名选择合适的 UA 与 Referer（防盗链关键）。"""
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


def host_in_whitelist(host: str) -> bool:
    """域名后缀是否命中媒体白名单。"""
    host = (host or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in MEDIA_HOST_WHITELIST)


def is_safe_public_url(url: str) -> bool:
    """基础 SSRF 防护：仅允许 http/https，且目标不得指向内网/环回地址。

    兼容 Clash / sing-box 的 Fake-IP 模式：
    - 字面 IP → 严格拦截内网/保留段（含 198.18.0.0/15）；
    - 域名 → 解析后逐地址校验；若落在 Fake-IP 段则以白名单放行。
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False

    # 字面 IP
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

    whitelisted = host_in_whitelist(host)
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if ip.version == 4 and ip in _FAKE_IP_NET:
            if not whitelisted:
                return False
            continue
        if _addr_blocked(ip):
            return False
    return True


def fetch_media(
    url: str,
    *,
    range_header: Optional[str] = None,
    stream: bool = True,
    timeout: int = 30,
) -> requests.Response:
    """取源核心：安全重定向跟随 + 每跳 SSRF 校验 + 自动选 UA/Referer。

    成功返回 requests.Response（调用方负责读取和关闭）。
    失败抛 MediaFetchError。
    """
    if not is_safe_public_url(url):
        raise MediaFetchError("非法或不被允许的资源地址", status_code=400)

    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if not is_safe_public_url(current):
            raise MediaFetchError("重定向目标不被允许", status_code=400)
        req_headers = site_headers(urlparse(current).hostname)
        if range_header:
            req_headers["Range"] = range_header
        try:
            resp = requests.get(
                current, headers=req_headers, stream=stream,
                timeout=timeout, allow_redirects=False,
            )
        except Exception as e:
            raise MediaFetchError(f"上游资源请求失败: {e}", status_code=502) from e

        if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
            nxt = urljoin(current, resp.headers["Location"])
            resp.close()
            current = nxt
            continue

        if resp.status_code >= 400:
            code = resp.status_code
            resp.close()
            raise MediaFetchError(f"上游资源返回 {code}", status_code=code)

        return resp

    raise MediaFetchError("重定向次数过多", status_code=502)


# ──────────────────────────────────────────────────────────────────
# 内部
# ──────────────────────────────────────────────────────────────────
def _addr_blocked(ip) -> bool:
    """该 IP 是否属于应拦截的内网/环回/保留地址段。"""
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )
