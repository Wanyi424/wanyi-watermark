#!/usr/bin/env python3
"""
百分百一键去水印 - MCP 服务器

该服务器为"百分百一键去水印"小程序提供媒体资源解析与提取服务：
1. 智能解析抖音/小红书分享链接，自动识别内容类型（视频/图文）
2. 提取无水印视频直链，支持高清画质下载
3. 提取无水印图片资源，支持多格式输出（WebP/PNG）
4. 视频文本转写功能，从视频中提取语音内容（基于阿里云百炼API）
5. 通用平台兜底机制，支持未适配平台的链接解析

────────────────────────────────────────────────────────────────────
⚠️ 重要设计约定（务必遵守，请勿改回"工具直接返回纯文本"）
────────────────────────────────────────────────────────────────────
本服务器所有解析类工具（parse_xhs_link / parse_douyin_link /
parse_generic_link）一律【返回完整 JSON 字符串】，包含全部结构化字段
（platform / type / title / caption / url / images 等），不得在工具
内部就把结果拍平成纯文本。

"纯文本（标题 / 文案 / 视频图片链接，禁止 Markdown、不省略不截断）"是
【LLM 回复最终用户时的展示格式】，由各工具 docstring 与
watermark_removal_guide 提示词指示 LLM 完成，不是工具的返回值格式。

    数据流：工具 ──返回完整 JSON──▶ LLM ──整理成纯文本──▶ 用户

即：传给 LLM 的始终是包含全部数据的 JSON；只有 LLM 面向用户答复时才转纯文本。
注：曾有改动把工具返回值直接改成纯文本（_format_plain_result），会丢失结构化
字段、且与兜底/报错返回的 JSON 不一致，已回退；后续请保持工具返回 JSON。

────────────────────────────────────────────────────────────────────
架构说明：解析编排逻辑统一收敛在 resolver.py（单一事实源）
────────────────────────────────────────────────────────────────────
"按平台分发 + 自动识别视频/图文 + 通用兜底"的逻辑现位于 resolver.py，
由 MCP 工具、CLI、WebUI、Skill 共同复用。下方工具均为薄包装：
调用 resolver.resolve_* 取得结构化 dict，再 json.dumps 返回（行为不变）。
"""

import os
import json
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp import Context

from .resolver import resolve_douyin, resolve_xiaohongshu, resolve_generic
from .douyin_processor import DouyinProcessor

# 创建 MCP 服务器实例
mcp = FastMCP("百分百一键去水印",
              dependencies=["requests", "ffmpeg-python", "tqdm", "dashscope"])


# ──────────────────────────────────────────────────────────────────
# 输出格式约定（详见模块顶部 docstring，务必遵守）：
#   • 下方所有 @mcp.tool 一律返回【完整 JSON】，不在工具内拍平成纯文本；
#   • "纯文本展示（标题/文案/链接，禁止 Markdown、不截断）"是 LLM 回复
#     用户时的格式，由各工具 docstring 与 watermark_removal_guide 指示
#     LLM 完成，不是工具的返回值格式；
#   • 数据流：工具 → 返回 JSON → LLM → 整理成纯文本 → 用户。
#   • 编排逻辑在 resolver.py，本层仅 json.dumps 包装。
# ──────────────────────────────────────────────────────────────────
@mcp.tool()
def parse_xhs_link(share_link: str) -> str:
    """
    解析小红书分享链接，自动识别视频或图文类型并返回无水印资源

    参数:
    - share_link: 小红书分享链接或包含链接的文本

    返回:
    - 包含资源链接和信息的JSON字符串
    - 自动识别类型（video/image）并返回相应格式
    - 调用完成后，请将结果整理为以下纯文本格式并反馈给用户（禁止使用Markdown）：
      标题（如无则留空）：
      文案：
      视频/图片链接：
    - 返回时请保留完整的标题和文案，不要省略或截断任何内容
    - 若专用解析失败，将自动尝试 generic 兜底逻辑；调用方需同样按上述格式反馈结果
    - 抖音仅返回 caption 字段，标题需由调用方自行按需补充
    """
    return json.dumps(resolve_xiaohongshu(share_link), ensure_ascii=False, indent=2)


@mcp.tool()
async def extract_douyin_text(
    share_link: str,
    model: Optional[str] = None,
    ctx: Context = None
) -> str:
    """
    从抖音分享链接提取视频中的文本内容

    参数:
    - share_link: 抖音分享链接或包含链接的文本
    - model: 语音识别模型（可选，默认使用paraformer-v2）

    返回:
    - 提取的文本内容

    注意: 需要设置环境变量 DASHSCOPE_API_KEY
    """
    try:
        # 从环境变量获取API密钥
        api_key = os.getenv('DASHSCOPE_API_KEY')
        if not api_key:
            raise ValueError("未设置环境变量 DASHSCOPE_API_KEY，请在配置中添加阿里云百炼API密钥")

        processor = DouyinProcessor(api_key, model)

        # 解析视频链接
        ctx.info("正在解析抖音分享链接...")
        video_info = processor.parse_share_url(share_link)

        # 直接使用视频URL进行文本提取
        ctx.info("正在从视频中提取文本...")
        text_content = processor.extract_text_from_video_url(video_info['url'])

        ctx.info("文本提取完成!")
        return text_content

    except Exception as e:
        ctx.error(f"处理过程中出现错误: {str(e)}")
        raise Exception(f"提取抖音视频文本失败: {str(e)}")


# 注意：当前阶段仅在内部开发使用，尚无客户端依赖旧工具名，因此只保留统一的 parse_* 接口。
# 若后续对外发布或有现网依赖，请考虑恢复旧名称的兼容包装以避免破坏现有集成。
@mcp.tool()
def parse_douyin_link(share_link: str) -> str:
    """
    解析抖音分享链接，自动识别视频或图文类型并返回无水印资源

    参数:
    - share_link: 抖音分享链接或包含链接的文本

    返回:
    - 包含资源链接和信息的JSON字符串
    - 自动识别类型（video/image）并返回相应格式
    - 调用完成后，请将结果整理为以下纯文本格式并反馈给用户（禁止使用Markdown）：
      标题（如无则留空）：
      文案：
      视频/图片链接：
    - 返回时请保留完整的标题和文案，不要省略或截断任何内容
    - 若专用解析失败，将自动尝试 generic 兜底逻辑；调用方需同样按上述格式反馈结果
    - 抖音仅返回 caption 字段，标题需由调用方自行按需补充
    """
    return json.dumps(resolve_douyin(share_link), ensure_ascii=False, indent=2)


@mcp.tool()
def parse_generic_link(share_link: str) -> str:
    """解析任意短视频/图文链接，直接启用 generic 兜底逻辑。

    参数:
    - share_link: 任意平台的分享链接或包含链接的文本（抖音/小红书亦可传入）

    返回:
    - 包含资源链接和信息的JSON字符串
    - 输出字段与其它工具一致：platform/title/caption/url
    - 调用完成后，请将结果整理为以下纯文本格式并反馈给用户（禁止使用Markdown）：
      标题（如无则留空）：
      文案：
      视频/图片链接：
    - 请完整保留标题与文案的全部内容，不要省略或截断
    - 若未能解析，将返回错误说明（可能原因：页面无直链、需要登录等）
    """
    return json.dumps(resolve_generic(share_link), ensure_ascii=False, indent=2)


@mcp.prompt()
def watermark_removal_guide() -> str:
    """百分百一键去水印使用指南 - 视频链接解析与媒体资源提取"""
    return """
# 百分百一键去水印 - MCP 服务使用指南

## 功能概述
本 MCP 服务器为"百分百一键去水印"小程序提供核心技术支持，实现多平台视频/图片链接的智能解析与媒体资源提取。

### 核心能力
- 🔗 智能链接解析：自动识别抖音/小红书分享链接，解析真实视频地址
- 📹 无水印视频提取：获取高清无水印视频直链，支持直接下载
- 🖼️ 图片资源提取：支持图文笔记解析，提供多格式图片（WebP轻量/PNG高清）
- 📝 视频文本转写：基于AI语音识别，从视频中提取文字内容
- 🌐 通用平台兜底：遇到未适配平台时，自动尝试通用解析机制

## 环境变量配置
视频文本转写功能需要设置以下环境变量：
- `DASHSCOPE_API_KEY`: 阿里云百炼API密钥（仅文本转写功能需要，链接解析无需密钥）

## 使用步骤
1. 复制抖音/小红书的分享链接（或包含链接的文本）
2. 使用相应的工具进行解析
3. 对于文本转写功能，需在 Claude Desktop 配置中设置环境变量

## 工具说明

### 主要工具（自动识别类型）
- `parse_douyin_link`: 解析抖音链接，自动识别视频/图文并返回无水印资源，失败时自动尝试通用解析
- `parse_xhs_link`: 解析小红书链接，自动识别视频/图文并返回无水印资源，失败时自动尝试通用解析

### 兜底工具
- `parse_generic_link`: 通用平台链接解析，适用于未明确支持的平台或作为备用方案

### 特殊功能
- `extract_douyin_text`: 从抖音视频中提取语音文本内容（需要 API 密钥）

## Claude Desktop 配置示例
```json
{
  "mcpServers": {
    "wanyi-watermark": {
      "command": "uvx",
      "args": ["wanyi-watermark"],
      "env": {
        "DASHSCOPE_API_KEY": "your-dashscope-api-key-here"
      }
    }
  }
}
```

## 返回格式

### 统一输出要求（禁止使用任何Markdown语法）
在工具执行结束后，请按下面的顺序组织最终回复：
标题（如无则留空）：<可选的简短标题>
文案：<完整文案或说明，抖音等平台可直接使用唯一的文本内容>
视频/图片链接：<逐项列出所有资源链接，多个链接可分行>
请完整保留标题与文案的全部内容，不要省略或截断。
如果获得的是通用解析结果（platform=generic），标题可能来自网页 og:title，文案可能为空，也请按格式显式告知。

## 技术特点
- ✅ 链接解析无需密钥：视频/图片资源提取完全免费，无需任何 API 配置
- ✅ 智能类型识别：自动判断内容类型（视频/图文），无需手动指定
- ✅ 多格式支持：小红书图文提供 WebP（快速预览）和 PNG（高清编辑）双格式
- ✅ 高精度文本转写：使用阿里云百炼 paraformer-v2 模型，识别准确率高
- ✅ 多平台兼容：支持抖音、小红书，并提供通用解析兜底机制
"""

def main():
    """启动MCP服务器"""
    mcp.run()

if __name__ == "__main__":
    main()
