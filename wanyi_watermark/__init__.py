"""百分百一键去水印 - MCP 服务器包

该包为"百分百一键去水印"提供 MCP 协议服务支持。
核心能力：多平台视频/图片链接解析、无水印资源提取、视频文本转写。
支持平台：抖音、小红书，并提供通用平台兜底解析机制。

注意：避免在包导入时引入重量级依赖（如 ffmpeg），
以便支持按模块运行（python -m wanyi_watermark.xiaohongshu_processor）。
"""

__version__ = "1.2.0"
__author__ = "wanyi"
__email__ = "2368077712@qq.com"

__all__ = []
