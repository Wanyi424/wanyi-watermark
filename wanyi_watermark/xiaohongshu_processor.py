import re
import json
import logging
from typing import List, Optional, Tuple, Dict

import requests


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
        # 形如 /explore/{note_id}
        m = re.search(r"/explore/([a-z0-9]+)", url, re.IGNORECASE)
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
        规则依据本次样本与常见页面行为：
        - 优先来自 <video> DOM 的链接（更贴近实际播放流）
        - 偏好末尾为 _114.mp4 或路径中含 /114/ 的版本（样本中该版本为无水印）
        - 可扩展更多特征（如显式 wm 标识的负权重等）
        """
        score = 0
        if source == "video":
            score += 100
        if re.search(r"/(114)/", url) or re.search(r"_114\.mp4($|\?)", url):
            score += 50
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

    @staticmethod
    def _force_quality_url(url: str, target: str = "114") -> str:
        # 同时替换路径段与文件名中的质量码
        url2 = re.sub(r"/([0-9]{2,4})/", f"/{target}/", url)
        url2 = re.sub(r"_([0-9]{2,4})\.mp4", f"_{target}.mp4", url2)
        return url2

    @staticmethod
    def _prefer_hs_domain(url: str) -> str:
        # 将 sns-video-*.xhscdn.com 统一偏向 hs
        return re.sub(r"https://sns-video-[a-z]+\.xhscdn\.com", "https://sns-video-hs.xhscdn.com", url)

    def _probe_head_ok(self, url: str) -> bool:
        try:
            resp = requests.head(url, headers=HEADERS_XHS_PC, timeout=min(self.timeout, 6), allow_redirects=True)
            return 200 <= resp.status_code < 400
        except Exception:
            return False


    def get_watermark_free_url(self, candidates: List[Tuple[str, str]]) -> str:
        if not candidates:
            raise ValueError("未从页面中发现可用视频直链")
        # 1) 先规范域名，提取质量码
        normalized: List[Tuple[str, str, Optional[int]]] = []
        for url, source in candidates:
            u = self._prefer_hs_domain(url)
            q = self._extract_quality_code(u)
            normalized.append((u, source, q))

        # 2) 如果存在 114，直接返回
        for u, source, q in normalized:
            if q == 114:
                logger.debug(f"[小红书视频] 找到114质量码视频（无水印）")
                return u

        # 3) 否则选一个最靠谱的候选，尝试强制改成 114 并 HEAD 探测
        best = sorted(
            normalized,
            key=lambda item: (self._score_candidate(item[0], item[1]) * -1, len(item[0])),
        )[0]
        logger.debug(f"[小红书视频] 未找到114质量码，尝试转换URL（质量码: {best[2]}）")

        forced = self._force_quality_url(best[0], "114")
        if forced != best[0] and self._probe_head_ok(forced):
            logger.debug(f"[小红书视频] URL转换成功，使用114版本")
            return forced

        # 4) 退回原始 best
        logger.debug(f"[小红书视频] URL转换失败，使用原始候选（质量码: {best[2]}）")
        return best[0]

    def _fetch_html(self, url: str) -> str:
        # 先尝试桌面 UA
        resp = requests.get(url, headers=HEADERS_XHS_PC, timeout=self.timeout, allow_redirects=True)
        # 某些风控场景会返回 404 页，但仍含 SSR 内容；仅在完全失败时切换 UA
        if resp.status_code >= 500 or not resp.text:
            resp = requests.get(url, headers=HEADERS_XHS_MOBILE, timeout=self.timeout, allow_redirects=True)
        resp.raise_for_status()
        return resp.text

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
        import time
        start_time = time.time()

        share_url = self._extract_first_url(share_text)
        logger.debug(f"[小红书图文] 提取到的链接: {share_url}")

        # 先请求获取重定向后的真实 URL（短链接需要重定向）
        t1 = time.time()
        resp = requests.get(share_url, headers=HEADERS_XHS_PC, timeout=self.timeout, allow_redirects=True)
        logger.debug(f"[小红书图文] 页面请求耗时: {time.time()-t1:.2f}秒")

        final_url = resp.url
        html = resp.text

        # 从真实 URL 中提取 note_id（支持 /explore/ 和 /discovery/item/ 两种路径）
        note_match = re.search(r'/(?:explore|discovery/item)/([a-z0-9]+)', final_url)
        if not note_match:
            raise ValueError("无法从链接中提取笔记 ID")

        note_id = note_match.group(1)
        logger.debug(f"[小红书图文] Note ID: {note_id}")

        # 提取 __INITIAL_STATE__ 数据
        t2 = time.time()
        data = self._extract_initial_state(html)
        if not data:
            raise ValueError("无法从页面中提取笔记数据")
        logger.debug(f"[小红书图文] JSON解析耗时: {time.time()-t2:.2f}秒")

        # 导航到笔记详情
        try:
            note_map = data['note']['noteDetailMap']
            if note_id not in note_map:
                raise KeyError(f"笔记 {note_id} 不在数据中")

            note_info = note_map[note_id]['note']

            # 提取图片列表
            image_list = note_info.get('imageList', [])
            if not image_list:
                raise ValueError("笔记中没有找到图片")

            # 处理图片 URL（同时提供 WebP 和 PNG 两种格式）
            t3 = time.time()
            images = []
            for img in image_list:
                # 优先从 infoList 中选择 WB_DFT
                webp_url = None
                if 'infoList' in img:
                    for info in img['infoList']:
                        if info.get('imageScene') == 'WB_DFT':
                            webp_url = info.get('url')
                            break

                # 回退到 urlDefault
                if not webp_url:
                    webp_url = img.get('urlDefault')

                if webp_url:
                    # 确保 WebP URL 使用 HTTPS（避免混合内容问题）
                    webp_url = self._ensure_https(webp_url)

                    # 转换为 PNG URL（借鉴油猴脚本逻辑）
                    png_url = self._convert_image_url_to_png(webp_url)

                    # 同时保留两种格式
                    images.append({
                        'url_webp': webp_url,  # WebP 格式（体积小，适合预览）- HTTPS
                        'url_png': png_url if png_url else webp_url,  # PNG 格式（无损高清）- HTTPS
                        'width': img.get('width'),
                        'height': img.get('height')
                    })

            # 清理标题中的非法字符
            title = note_info.get('title', f'xhs_{note_id}')
            title = re.sub(r"[\\/:*?\"<>|]", "_", title).strip()

            logger.debug(f"[小红书图文] 图片URL处理耗时: {time.time()-t3:.2f}秒")

            total_time = time.time() - start_time
            logger.debug(f"[小红书图文] 解析完成，总耗时: {total_time:.2f}秒，图片数量: {len(images)}")
            logger.debug(f"{'='*60}\n")

            return {
                'note_id': note_id,
                'title': title,
                'desc': note_info.get('desc', ''),
                'type': 'image',
                'images': images
            }

        except (KeyError, TypeError) as e:
            raise ValueError(f"解析笔记数据失败: {str(e)}")

    def parse_share_url(self, share_text: str) -> Dict[str, str]:
        """解析小红书分享链接，返回视频信息：url/title/note_id

        解析策略：
        - 从 HTML 中抓取 <video src> 与 <meta og:video>
        - 通过启发式评分选择无水印直链
        """
        import time
        start_time = time.time()

        share_url = self._extract_first_url(share_text)
        logger.debug(f"[小红书视频] 提取到的链接: {share_url}")

        note_id = self._extract_note_id_from_path(share_url)

        t1 = time.time()
        html = self._fetch_html(share_url)
        logger.debug(f"[小红书视频] 页面请求耗时: {time.time()-t1:.2f}秒")

        # 标题：优先 og:title
        title = (
            self._extract_meta(html, "og:title")
            or self._extract_meta(html, "og:description", key="content")
            or (f"xhs_{note_id}" if note_id else "xhs")
        )
        # 清理非法文件名字符
        title = re.sub(r"[\\/:*?\"<>|]", "_", title).strip()

        # 候选直链
        candidates: List[Tuple[str, str]] = []
        # 1) video 标签
        for v in self._extract_all_video_src(html):
            candidates.append((v, "video"))
        # 2) og:video
        ogv = self._extract_meta(html, "og:video")
        if ogv:
            candidates.append((ogv, "og"))

        if not candidates:
            # 兜底：尝试在页面里扫所有以 xhscdn.com 结尾的 mp4
            for m in re.finditer(r"https?://[^\s'\"]+?\.mp4", html, re.IGNORECASE):
                if "xhscdn.com" in m.group(0):
                    candidates.append((m.group(0), "fallback"))

        logger.debug(f"[小红书视频] 找到 {len(candidates)} 个候选视频URL")

        t2 = time.time()
        final_url = self.get_watermark_free_url(candidates)
        logger.debug(f"[小红书视频] URL筛选耗时: {time.time()-t2:.2f}秒")

        total_time = time.time() - start_time
        logger.debug(f"[小红书视频] 解析完成，总耗时: {total_time:.2f}秒")
        logger.debug(f"{'='*60}\n")

        return {
            "url": final_url,
            "title": title,
            "note_id": note_id or "",
        }



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
