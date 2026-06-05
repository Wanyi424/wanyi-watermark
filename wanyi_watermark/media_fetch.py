"""
共享媒体获取模块 — 防盗链 UA/Referer、SSRF 安全校验、重定向跟踪。

供 WebUI 代理端点和 CLI 下载复用。
"""

import socket
import ipaddress
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse, urljoin

import requests

# ──────────────────────── UA 常量 ────────────────────────

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ──────────────────────── 域名白名单 ────────────────────────

MEDIA_HOST_WHITELIST = (
    # 抖音 / 字节系
    "douyin.com", "iesdouyin.com", "amemv.com", "snssdk.com",
    "douyinpic.com", "douyinvod.com", "byteimg.com", "bytecdn.com",
    "ixigua.com", "ixiguavideo.com", "pstatp.com", "zjcdn.com",
    # 小红书
    "xiaohongshu.com", "xhscdn.com", "xhslink.com",
)

# ──────────────────────── 内部常量 ────────────────────────

_FAKE_IP_NET = ipaddress.ip_network("198.18.0.0/15")
_MAX_REDIRECTS = 5


# ──────────────────────── 公开函数 ────────────────────────

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


def is_safe_public_url(url: str) -> bool:
    """基础 SSRF 防护：仅允许 http/https，目标不得指向内网/环回。

    兼容 Clash/sing-box Fake-IP 模式：域名解析到 198.18/15 时以白名单放行。
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

    # 字面 IP 直连：严格拦截内网/保留段（含 Fake-IP 段）
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
        if ip.version == 4 and ip in _FAKE_IP_NET:
            if not whitelisted:
                return False
            continue
        if _addr_blocked(ip):
            return False
    return True


class FetchError(Exception):
    """媒体获取失败（含 HTTP 状态码信息）。"""
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


def fetch_media_stream(url: str, range_header: Optional[str] = None) -> requests.Response:
    """手动跟随重定向获取媒体流，每跳 SSRF 校验 + 按域名补 Referer/UA。

    返回 stream=True 的 requests.Response（调用方负责关闭）。
    遇错抛 FetchError。
    """
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        if not is_safe_public_url(current):
            raise FetchError("非法或不被允许的资源地址", 400)
        req_headers = site_headers(urlparse(current).hostname)
        if range_header:
            req_headers["Range"] = range_header
        resp = requests.get(
            current, headers=req_headers, stream=True, timeout=30, allow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
            nxt = urljoin(current, resp.headers["Location"])
            resp.close()
            current = nxt
            continue
        if resp.status_code >= 400:
            code = resp.status_code
            resp.close()
            raise FetchError(f"上游资源返回 {code}", code)
        return resp

    raise FetchError("重定向次数过多", 502)


def download_file(url: str, dest: Path, show_progress: bool = True) -> Path:
    """流式下载单个文件到 dest，使用完整的防盗链处理和安全校验。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = fetch_media_stream(url)
    try:
        total = int(resp.headers.get("content-length", 0))
        done = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    done += len(chunk)
                    if show_progress and total > 0:
                        print(f"\r  下载进度: {done / total * 100:.1f}%", end="", flush=True)
        if show_progress:
            print(f"\r  已保存: {dest}                ")
    finally:
        resp.close()
    return dest


# ──────────────────────── 内部辅助 ────────────────────────

def _host_in_whitelist(host: str) -> bool:
    """域名后缀是否命中媒体白名单。"""
    host = (host or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in MEDIA_HOST_WHITELIST)


def _addr_blocked(ip) -> bool:
    """该 IP 是否属于应拦截的内网/环回/保留地址段。"""
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )
