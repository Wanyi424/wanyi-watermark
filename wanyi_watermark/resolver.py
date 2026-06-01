"""统一解析门面（单一事实源）。

把"按平台分发 + 自动识别 视频/图文 + 通用兜底"的编排逻辑收敛到这里，
供四个消费方共同复用，避免各自重复实现：
    • MCP 工具层 (server.py 的 parse_douyin_link / parse_xhs_link / parse_generic_link)
    • 命令行工具 (cli.py)
    • WebUI (web/app.py)
    • Claude Skill (wanyi-watermark-skill/)

设计约定（与 server.py 模块顶部、CLAUDE.md 保持一致）：
    • 本模块所有 resolve_* 函数一律返回【完整结构化 dict】（含 status / platform /
      type / title / caption / url / images 等字段），不在此拍平成纯文本；
    • MCP 工具层负责 json.dumps；CLI / WebUI 直接消费 dict；
    • "纯文本展示"是 LLM 面向最终用户时的格式，由工具 docstring 与
      watermark_removal_guide 提示词指示 LLM 完成，不是本层的职责。

注意：处理器（douyin / xiaohongshu / generic）均在函数内部延迟导入，
保持 `import resolver` 本身轻量，不强依赖 dashscope / ffmpeg 等重型库。
"""

import re
import logging
from typing import Dict
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 提取分享文本中的第一个 URL（兼容中文标点/空白边界）
_URL_RE = re.compile(r'http[s]?://[^\s"\'，。、）)】>]+')

# 平台域名特征
_DOUYIN_HOSTS = ("douyin.com", "iesdouyin.com")
_XHS_HOSTS = ("xiaohongshu.com", "xhslink.com")


def _first_url(text: str) -> str:
    """从任意分享文本中提取第一个 URL。"""
    match = _URL_RE.search(text or "")
    if not match:
        raise ValueError("未找到有效的分享链接")
    return match.group(0)


def detect_platform(text: str) -> str:
    """根据链接域名判定平台：douyin / xiaohongshu / generic。"""
    try:
        host = (urlparse(_first_url(text)).hostname or "").lower()
    except ValueError:
        return "generic"
    if any(h in host for h in _DOUYIN_HOSTS):
        return "douyin"
    if any(h in host for h in _XHS_HOSTS):
        return "xiaohongshu"
    return "generic"


def _generic_fallback(share_link: str, reason: str) -> Dict:
    """通用兜底逻辑：在专用解析失败时尝试通用提取，返回结构化 dict。"""
    from .generic_extractor import extract_generic_media
    try:
        fallback_data = extract_generic_media(share_link)
        fallback_data.setdefault("fallback_reason", reason)
        return fallback_data
    except Exception as fallback_error:
        return {
            "status": "error",
            "error": f"{reason}；兜底解析失败：{fallback_error}",
        }


def resolve_douyin(share_link: str) -> Dict:
    """解析抖音链接，自动识别视频/图文，失败时回退通用兜底。

    返回 dict 结构与 MCP 工具 parse_douyin_link 完全一致（行为不变）。
    """
    from .douyin_processor import DouyinProcessor
    try:
        processor = DouyinProcessor("")  # 获取资源不需要 API 密钥

        # 先尝试解析视频
        try:
            video_info = processor.parse_share_url(share_link)
            # 仅输出 caption 和资源链接（沿用既有约定）
            return {
                "status": "success",
                "type": "video",
                "platform": "douyin",
                "video_id": video_info["video_id"],
                "caption": video_info.get("caption", ""),
                "url": video_info["url"],
            }
        except Exception as video_error:
            # 视频解析失败，检查是否为图文笔记
            error_msg = str(video_error)
            if "这是图文笔记" in error_msg:
                try:
                    note_data = processor.parse_image_note(share_link)
                    return {
                        "status": "success",
                        "type": "image",
                        "platform": "douyin",
                        "note_id": note_data["note_id"],
                        "caption": note_data.get("caption", ""),
                        "image_count": len(note_data["images"]),
                        "images": note_data["images"],
                    }
                except Exception as image_error:
                    return _generic_fallback(share_link, f"抖音图文解析失败: {image_error}")
            return _generic_fallback(share_link, f"抖音视频解析失败: {video_error}")

    except Exception as e:
        return _generic_fallback(share_link, f"解析抖音链接失败: {e}")


def resolve_xiaohongshu(share_link: str) -> Dict:
    """解析小红书链接，自动识别视频/图文，失败时回退通用兜底。

    返回 dict 结构与 MCP 工具 parse_xhs_link 完全一致（行为不变）。
    """
    from .xiaohongshu_processor import XiaohongshuProcessor
    try:
        processor = XiaohongshuProcessor()

        # 先尝试解析视频
        try:
            video_info = processor.parse_share_url(share_link)
            return {
                "status": "success",
                "type": "video",
                "platform": "xiaohongshu",
                "note_id": video_info.get("note_id", ""),
                "title": video_info["title"],
                "caption": video_info.get("desc", ""),
                "url": video_info["url"],
                "description": f"视频标题: {video_info['title']}",
            }
        except Exception as video_error:
            # 视频解析失败，尝试图文解析
            # NOTE: 此处依赖错误信息子串匹配判断回退，较脆弱；
            #       已登记到 UPSTREAM_SYNC.md 的技术债清单，后续以"页面数据结构判定"替代。
            error_msg = str(video_error).lower()
            if "未从页面中发现可用视频直链" in error_msg or "video" in error_msg or "候选" in error_msg:
                try:
                    note_data = processor.parse_image_note(share_link)
                    return {
                        "status": "success",
                        "type": "image",
                        "platform": "xiaohongshu",
                        "note_id": note_data["note_id"],
                        "title": note_data["title"],
                        "desc": note_data["desc"],
                        "caption": note_data.get("desc", ""),
                        "image_count": len(note_data["images"]),
                        "images": note_data["images"],
                        "format_info": {
                            "webp": "轻量格式，体积小（约160KB），适合快速预览和节省带宽",
                            "png": "无损格式，高质量（约1.8MB），支持透明背景，适合编辑和打印",
                        },
                    }
                except Exception as image_error:
                    return _generic_fallback(share_link, f"小红书图文解析失败: {image_error}")
            return _generic_fallback(share_link, f"小红书视频解析失败: {video_error}")

    except Exception as e:
        return _generic_fallback(share_link, f"解析小红书链接失败: {e}")


def resolve_generic(share_link: str) -> Dict:
    """直接启用通用兜底解析，返回结构化 dict。

    返回 dict 结构与 MCP 工具 parse_generic_link 完全一致。
    """
    from .generic_extractor import extract_generic_media
    try:
        result = extract_generic_media(share_link)
        result.setdefault("fallback_reason", "generic_tool_invocation")
        return result
    except Exception as e:
        return {
            "status": "error",
            "error": f"通用解析失败: {e}",
        }


def resolve_media(share_link: str) -> Dict:
    """顶层入口：根据链接域名自动选择平台处理器并解析。

    供 CLI / WebUI / Skill 使用（单输入框，任意平台链接皆可）。
    抖音 / 小红书内部已带通用兜底，未知平台直接走 generic。
    """
    platform = detect_platform(share_link)
    logger.debug("[resolver] 识别平台: %s", platform)

    if platform == "douyin":
        return resolve_douyin(share_link)
    if platform == "xiaohongshu":
        return resolve_xiaohongshu(share_link)
    return resolve_generic(share_link)
