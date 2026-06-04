import html
import re
import json
import logging
from typing import List, Optional, Tuple, Dict
from urllib.parse import parse_qs, urlparse

import requests

from .diagnostics import parse_log, short_text


# 专用于小红书页面抓取的 UA（桌面端优先，可避免强制 App 跳转）
HEADERS_XHS_PC = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.xiaohongshu.com/",
}

# 备用：移动端 UA（个别情况下可尝试回退）
HEADERS_XHS_MOBILE = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.xiaohongshu.com/",
}


logger = logging.getLogger(__name__)


class XiaohongshuProcessor:
    """小红书视频解析器

    功能：
    - 解析分享链接 HTML，提取视频候选直链
    - 依据启发式规则挑选“无水印”版本
    """

    def __init__(self, timeout: int = 12):
        self.timeout = timeout

    @staticmethod
    def _extract_first_url(text: str) -> str:
        urls = re.findall(
            r"http[s]?://(?:[a-zA-Z0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F]{2}))+",
            text,
        )
        if not urls:
            raise ValueError("未找到有效的小红书链接")
        return urls[0]

    @staticmethod
    def _extract_note_id_from_path(url: str) -> Optional[str]:
        # 形如 /explore/{note_id} 或 /discovery/item/{note_id}
        m = re.search(r"/(?:explore|discovery/item)/([a-z0-9]+)", url, re.IGNORECASE)
        return m.group(1) if m else None

    @staticmethod
    def _extract_meta(content: str, name_or_property: str, key: str = "content") -> Optional[str]:
        # 同时兼容 name="og:video" 与 property="og:video"
        pattern = (
            rf"<meta[^>]+(?:name|property)=[\"']{re.escape(name_or_property)}[\"'][^>]+{key}=[\"'](.*?)[\"']"
        )
        m = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
        return m.group(1) if m else None

    @staticmethod
    def _extract_all_video_src(content: str) -> List[str]:
        # 提取所有 <video src="..."></video>
        return list(
            {m.group(1) for m in re.finditer(r"<video[^>]+src=\"(.*?)\"", content, re.IGNORECASE)}
        )

    @staticmethod
    def _score_candidate(url: str, source: str) -> int:
        """为候选直链打分，分数越高越优先。
        规则依据页面结构：
        - 优先来自 <video> DOM 与 og:video 的链接（更贴近浏览器实际播放）
        - 其次使用 __INITIAL_STATE__ 中的 masterUrl / backupUrls
        - 可扩展更多特征（如显式 wm 标识的负权重等）
        """
        score = 0
        if source == "video":
            score += 100
        elif source == "og":
            score += 90
        elif "master" in source:
            score += 80
        elif "backup" in source:
            score += 60
        elif source == "fallback":
            score += 40
        if url.startswith("https://"):
            score += 10
        # 一些经验性负向特征（可按需扩展）
        if re.search(r"(?:wm|watermark)", url, re.IGNORECASE):
            score -= 40
        return score

    @staticmethod
    def _extract_quality_code(url: str) -> Optional[int]:
        # 从路径或文件名中提取质量码：/.../<q>/... 或 ..._<q>.mp4
        m = re.search(r"/([0-9]{2,4})/", url)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
        m = re.search(r"_([0-9]{2,4})\.mp4(?:$|\?)", url)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
        return None

    def get_watermark_free_url(self, candidates: List[Tuple[str, str]]) -> str:
        import time
        flow_start = time.perf_counter()
        parse_log(logger, "小红书视频开始筛选页面候选直链：候选数量=%d", len(candidates))
        if not candidates:
            raise ValueError("未从页面中发现可用视频直链")
        # 先规范协议，再按来源可靠性排序；不再进行域名/质量码改写和阻塞式二次网络探测。
        step = time.perf_counter()
        normalized: List[Tuple[str, str, Optional[int]]] = []
        for url, source in candidates:
            u = self._ensure_https(self._normalize_media_url(url))
            q = self._extract_quality_code(u)
            normalized.append((u, source, q))
        parse_log(logger, "小红书视频候选规范化完成：%d 个", len(normalized), step_start=step, flow_start=flow_start)

        step = time.perf_counter()
        best = sorted(
            normalized,
            key=lambda item: (self._score_candidate(item[0], item[1]) * -1, len(item[0])),
        )[0]
        parse_log(
            logger,
            "小红书视频候选筛选完成：来源=%s，质量码=%s",
            best[1],
            best[2],
            step_start=step,
            flow_start=flow_start,
        )
        return best[0]

    def _fetch_page(self, url: str) -> Tuple[str, str]:
        import time
        # 先尝试桌面 UA
        step = time.perf_counter()
        parse_log(logger, "小红书页面开始请求桌面 UA：%s", short_text(url))
        resp = requests.get(url, headers=HEADERS_XHS_PC, timeout=self.timeout, allow_redirects=True)
        parse_log(
            logger,
            "小红书桌面 UA 请求完成：HTTP %s，最终地址=%s，HTML长度=%d",
            resp.status_code,
            short_text(resp.url),
            len(resp.text or ""),
            step_start=step,
        )
        # 某些风控场景会返回 404 页，但仍含 SSR 内容；仅在完全失败时切换 UA
        if resp.status_code >= 500 or not resp.text:
            step = time.perf_counter()
            parse_log(logger, "小红书桌面 UA 结果不可用，开始移动 UA 回退")
            resp = requests.get(url, headers=HEADERS_XHS_MOBILE, timeout=self.timeout, allow_redirects=True)
            parse_log(
                logger,
                "小红书移动 UA 回退完成：HTTP %s，最终地址=%s，HTML长度=%d",
                resp.status_code,
                short_text(resp.url),
                len(resp.text or ""),
                step_start=step,
            )
        resp.raise_for_status()
        return resp.url, resp.text

    def _fetch_html(self, url: str) -> str:
        return self._fetch_page(url)[1]

    def _extract_initial_state(self, html: str) -> Optional[dict]:
        """从 HTML 中提取 window.__INITIAL_STATE__ 数据"""
        pattern = r'<script>\s*window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*(?:</script>|$)'
        match = re.search(pattern, html, re.DOTALL)

        if not match:
            return None

        json_str = match.group(1)
        # 处理 JavaScript 的 undefined 值（JSON 不支持）
        json_str = re.sub(r':\s*undefined\s*([,}])', r': null\1', json_str)

        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _share_type_from_url(url: str) -> str:
        """从当次分享重定向 URL 中读取内容类型提示。"""
        try:
            values = parse_qs(urlparse(url).query).get("type", [])
        except Exception:
            return ""
        return values[0].lower() if values else ""

    def _note_from_state(self, state: Optional[dict], note_id: Optional[str]) -> Tuple[str, Dict]:
        """从 __INITIAL_STATE__ 中取笔记详情；note_id 为空时取首个笔记。"""
        note_map = ((state or {}).get("note") or {}).get("noteDetailMap") or {}
        if not note_map:
            raise ValueError("无法从页面中提取笔记数据")

        if note_id and note_id in note_map:
            chosen_id = note_id
        else:
            chosen_id = next(iter(note_map.keys()))

        try:
            note_info = note_map[chosen_id]["note"]
        except (KeyError, TypeError) as e:
            raise ValueError(f"解析笔记数据失败: {e}") from e
        return chosen_id, note_info

    @staticmethod
    def _normalize_media_url(url: str) -> str:
        return html.unescape(str(url or "")).replace("\\/", "/").strip()

    @staticmethod
    def _is_video_media_url(url: str) -> bool:
        return bool(re.search(r"\.(?:mp4|m3u8)(?:\?|$)", url or "", re.IGNORECASE))

    def _collect_video_candidates(
        self,
        html_text: str,
        state: Optional[dict],
        note_id: Optional[str],
    ) -> List[Tuple[str, str]]:
        """从同一份 HTML / state 中收集视频候选直链。"""
        candidates: List[Tuple[str, str]] = []

        for v in self._extract_all_video_src(html_text):
            candidates.append((v, "video"))

        ogv = self._extract_meta(html_text, "og:video") or self._extract_meta(html_text, "og:video:url")
        if ogv:
            candidates.append((ogv, "og"))

        try:
            resolved_note_id, note_info = self._note_from_state(state, note_id)
            stream = (((note_info.get("video") or {}).get("media") or {}).get("stream") or {})
            for codec, items in stream.items():
                if not isinstance(items, list):
                    continue
                for idx, item in enumerate(items):
                    if not isinstance(item, dict):
                        continue
                    master = item.get("masterUrl")
                    if master:
                        candidates.append((master, f"state_master_{codec}_{idx}"))
                    for backup_idx, backup in enumerate(item.get("backupUrls") or []):
                        candidates.append((backup, f"state_backup_{codec}_{idx}_{backup_idx}"))
            parse_log(logger, "小红书 state 视频候选读取完成：note_id=%s", resolved_note_id)
        except Exception:
            pass

        if not candidates:
            for m in re.finditer(r"https?://[^\s'\"]+?\.(?:mp4|m3u8)(?:\?[^\s'\"]*)?", html_text, re.IGNORECASE):
                if "xhscdn.com" in m.group(0):
                    candidates.append((m.group(0), "fallback"))

        seen = set()
        normalized: List[Tuple[str, str]] = []
        for url, source in candidates:
            u = self._normalize_media_url(url)
            if not self._is_video_media_url(u) or u in seen:
                continue
            seen.add(u)
            normalized.append((u, source))
        return normalized

    def _build_image_note(self, note_id: str, note_info: Dict) -> Dict[str, any]:
        """从已解析的笔记详情中构造图文结果。"""
        import time
        image_list = note_info.get("imageList", [])
        if not image_list:
            raise ValueError("笔记中没有找到图片")
        parse_log(logger, "小红书图文数据读取完成：图片原始数量=%d", len(image_list))

        step = time.perf_counter()
        images = []
        for img in image_list:
            webp_url = None
            if "infoList" in img:
                for info in img["infoList"]:
                    if info.get("imageScene") == "WB_DFT":
                        webp_url = info.get("url")
                        break

            if not webp_url:
                webp_url = img.get("urlDefault")

            if webp_url:
                webp_url = self._ensure_https(webp_url)
                png_url = self._convert_image_url_to_png(webp_url)
                images.append({
                    "url_webp": webp_url,
                    "url_png": png_url if png_url else webp_url,
                    "width": img.get("width"),
                    "height": img.get("height"),
                })

        if not images:
            raise ValueError("无法提取图片URL")

        title = note_info.get("title", f"xhs_{note_id}")
        title = re.sub(r"[\\/:*?\"<>|]", "_", title).strip()
        parse_log(logger, "小红书图文图片 URL 转换完成：有效图片=%d 张", len(images), step_start=step)

        return {
            "note_id": note_id,
            "title": title,
            "desc": note_info.get("desc", ""),
            "type": "image",
            "images": images,
        }

    def _build_video_note(
        self,
        html_text: str,
        state: Optional[dict],
        note_id: Optional[str],
        note_info: Optional[Dict],
    ) -> Dict[str, str]:
        """从同一份页面数据中构造视频结果。"""
        import time
        step = time.perf_counter()
        title = (
            (note_info or {}).get("title")
            or self._extract_meta(html_text, "og:title")
            or self._extract_meta(html_text, "og:description", key="content")
            or (f"xhs_{note_id}" if note_id else "xhs")
        )
        title = re.sub(r"[\\/:*?\"<>|]", "_", title).strip()
        parse_log(logger, "小红书视频标题提取完成：标题长度=%d", len(title), step_start=step)

        step = time.perf_counter()
        candidates = self._collect_video_candidates(html_text, state, note_id)
        parse_log(logger, "小红书视频候选直链扫描完成：%d 个", len(candidates), step_start=step)

        step = time.perf_counter()
        final_url = self.get_watermark_free_url(candidates)
        parse_log(logger, "小红书视频最终直链筛选完成：%s", short_text(final_url), step_start=step)

        return {
            "url": final_url,
            "title": title,
            "note_id": note_id or "",
            "desc": (note_info or {}).get("desc", ""),
        }

    def parse_media(self, share_text: str) -> Dict[str, any]:
        """一次抓取页面后自动识别小红书视频或图文笔记。"""
        import time
        start_time = time.perf_counter()

        share_url = self._extract_first_url(share_text)
        parse_log(logger, "小红书统一解析开始：分享链接=%s", short_text(share_url))

        step = time.perf_counter()
        final_url, html_text = self._fetch_page(share_url)
        parse_log(
            logger,
            "小红书统一解析页面获取完成：最终地址=%s，HTML长度=%d",
            short_text(final_url),
            len(html_text or ""),
            step_start=step,
        )

        note_id = self._extract_note_id_from_path(final_url) or self._extract_note_id_from_path(share_url)
        share_type = self._share_type_from_url(final_url)
        parse_log(logger, "小红书统一解析基础信息：note_id=%s，type参数=%s", note_id or "未识别", share_type or "无")

        step = time.perf_counter()
        state = self._extract_initial_state(html_text)
        parse_log(logger, "小红书统一解析 __INITIAL_STATE__ 处理完成：%s", "成功" if state else "未找到", step_start=step)

        note_info: Optional[Dict] = None
        if state:
            try:
                note_id, note_info = self._note_from_state(state, note_id)
                parse_log(logger, "小红书统一解析笔记详情定位完成：note_id=%s", note_id)
            except Exception as e:
                parse_log(logger, "小红书统一解析笔记详情定位失败：%s", str(e), level=logging.WARNING)

        stream = (((note_info or {}).get("video") or {}).get("media") or {}).get("stream") or {}
        has_state_video = any(isinstance(items, list) and bool(items) for items in stream.values())
        has_html_video = bool(self._extract_meta(html_text, "og:video") or self._extract_all_video_src(html_text))
        has_images = bool((note_info or {}).get("imageList"))

        prefer_video = share_type == "video" or (not share_type and (has_state_video or has_html_video))
        prefer_image = share_type in ("normal", "image") or (not prefer_video and has_images)
        parse_log(
            logger,
            "小红书统一解析类型判断完成：prefer_video=%s，prefer_image=%s，state_video=%s，html_video=%s，images=%s",
            prefer_video,
            prefer_image,
            has_state_video,
            has_html_video,
            has_images,
        )

        errors = []
        if prefer_video:
            try:
                result = self._build_video_note(html_text, state, note_id, note_info)
                result["type"] = "video"
                parse_log(logger, "小红书统一解析完成：视频", flow_start=start_time)
                return result
            except Exception as e:
                errors.append(f"视频解析失败: {e}")
                parse_log(logger, "小红书统一视频路径失败，尝试图文路径：%s", str(e), level=logging.WARNING)

        if prefer_image or note_info:
            try:
                result = self._build_image_note(note_id or "", note_info or {})
                parse_log(logger, "小红书统一解析完成：图文，图片=%d 张", len(result.get("images", [])), flow_start=start_time)
                return result
            except Exception as e:
                errors.append(f"图文解析失败: {e}")
                parse_log(logger, "小红书统一图文路径失败：%s", str(e), level=logging.WARNING)

        if not prefer_video:
            try:
                result = self._build_video_note(html_text, state, note_id, note_info)
                result["type"] = "video"
                parse_log(logger, "小红书统一解析完成：视频", flow_start=start_time)
                return result
            except Exception as e:
                errors.append(f"视频解析失败: {e}")

        raise ValueError("；".join(errors) if errors else "未从页面中发现可用资源")

    @staticmethod
    def _ensure_https(url: str) -> str:
        """确保 URL 使用 HTTPS 协议

        小红书 CDN 同时支持 HTTP 和 HTTPS，但为了避免混合内容问题，
        统一使用 HTTPS（现代 Web 应用的最佳实践）
        """
        if url and url.startswith('http://'):
            return url.replace('http://', 'https://', 1)
        return url

    @staticmethod
    def _convert_image_url_to_png(webp_url: str) -> Optional[str]:
        """将 WebP CDN 链接转换为 PNG 图片服务链接

        借鉴油猴脚本 XHS-Downloader 的 URL 转换逻辑：
        从 CDN URL 中提取图片 ID，转换为 ci.xiaohongshu.com 的 PNG 链接

        示例转换：
        输入: http://sns-webpic-qc.xhscdn.com/202510042121/15b1bc2cb.../1040g2sg31bs6p8sb0kdg5o3q72pg8rvklgbf230!nd_dft_wlteh_webp_3
        输出: https://ci.xiaohongshu.com/1040g2sg31bs6p8sb0kdg5o3q72pg8rvklgbf230?imageView2/format/png

        优势：
        - PNG 格式（无损）vs WebP
        - URL 更稳定，不依赖 CDN 节点和时间戳
        - 使用小红书官方图片处理服务
        """
        # 提取图片 ID（感叹号前的部分）- 支持 http 和 https
        pattern = r'https?://sns-webpic-qc\.xhscdn\.com/\d+/[0-9a-z]+/(\S+?)!'
        match = re.search(pattern, webp_url)

        if match:
            image_id = match.group(1)
            # 转换为 ci.xiaohongshu.com 的 PNG 链接（强制 HTTPS）
            return f'https://ci.xiaohongshu.com/{image_id}?imageView2/format/png'

        return None

    def parse_image_note(self, share_text: str) -> Dict[str, any]:
        """解析小红书图文笔记，返回图片列表和笔记信息

        返回格式：
        {
            "note_id": str,
            "title": str,
            "desc": str,
            "type": "image",
            "images": [
                {
                    "url_webp": str,  # WebP 格式（体积小，适合预览）
                    "url_png": str,   # PNG 格式（无损高清，支持透明）
                    "width": int,
                    "height": int
                },
                ...
            ]
        }
        """
        data = self.parse_media(share_text)
        if data.get("type") != "image":
            raise ValueError("该链接不是图文笔记")
        return data

    def parse_share_url(self, share_text: str) -> Dict[str, str]:
        """解析小红书分享链接，返回视频信息：url/title/note_id

        解析策略：
        - 从 HTML 中抓取 <video src> 与 <meta og:video>
        - 通过启发式评分选择无水印直链
        """
        data = self.parse_media(share_text)
        if data.get("type") != "video":
            raise ValueError("该链接不是视频笔记")
        return data

if __name__ == "__main__":
    # 便捷测试：
    #   python -m wanyi_watermark.xiaohongshu_processor "<xhs_url_or_text>"
    #   python -m wanyi_watermark.xiaohongshu_processor "<xhs_url_or_text>" --image
    import sys

    if len(sys.argv) < 2:
        print("用法: python -m wanyi_watermark.xiaohongshu_processor <小红书链接或文本> [--image]")
        print("  默认: 解析视频笔记")
        print("  --image: 解析图文笔记")
        sys.exit(1)

    share = sys.argv[1]
    is_image = "--image" in sys.argv

    p = XiaohongshuProcessor()

    if is_image:
        # 解析图文笔记
        data = p.parse_image_note(share)
        print(f"\n{'='*60}")
        print(f"标题: {data['title']}")
        print(f"笔记 ID: {data['note_id']}")
        print(f"类型: {data['type']}")
        print(f"{'='*60}")
        print(f"\n正文内容:\n{data['desc']}")
        print(f"\n{'='*60}")
        print(f"图片数量: {len(data['images'])}\n")
        for i, img in enumerate(data['images'], 1):
            print(f"图片 {i}: {img['width']}x{img['height']}")
            print(f"  WebP (轻量): {img['url_webp'][:80]}...")
            print(f"  PNG  (高清): {img['url_png']}\n")
    else:
        # 解析视频笔记
        data = p.parse_share_url(share)
        print(json.dumps(data, ensure_ascii=False, indent=2))
