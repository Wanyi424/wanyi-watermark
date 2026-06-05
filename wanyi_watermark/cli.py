#!/usr/bin/env python3
"""
百分百一键去水印 - 命令行工具

支持抖音 / 小红书 / 通用平台的无水印资源解析、下载与文案提取：
1. 解析链接（自动识别平台与 视频/图文 类型）
2. 下载无水印视频 / 图集
3. 提取视频文案（阿里云百炼，需 DASHSCOPE_API_KEY）

与 MCP 工具、WebUI、Skill 共用同一套解析门面（resolver.py）与转写服务（transcription.py）。

使用示例:
  # 解析并打印结构化信息（无需 API 密钥，支持抖音/小红书/通用）
  python -m wanyi_watermark.cli -l "<分享链接>" -a info

  # 下载无水印视频 / 图集到指定目录
  python -m wanyi_watermark.cli -l "<分享链接>" -a download -o ./output

  # 提取视频文案并保存为 Markdown（需 DASHSCOPE_API_KEY）
  export DASHSCOPE_API_KEY="sk-xxx"
  python -m wanyi_watermark.cli -l "<分享链接>" -a extract -o ./output

  # 提取文案并同时保存视频
  python -m wanyi_watermark.cli -l "<分享链接>" -a extract -o ./output --save-video
"""

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional

from .media_fetch import download_file as _download_file


def _resource_name(data: dict) -> str:
    """为下载文件取一个稳定的名字：优先 video_id / note_id。"""
    return data.get("video_id") or data.get("note_id") or "media"


def _print_human_summary(data: dict) -> None:
    """人类可读的简要信息（不替代 JSON，仅辅助阅读）。"""
    print("\n" + "=" * 56)
    print(f"平台: {data.get('platform', '-')}    类型: {data.get('type', '-')}")
    if data.get("title"):
        print(f"标题: {data['title']}")
    if data.get("caption"):
        print(f"文案: {data['caption']}")
    if data.get("type") == "video":
        print(f"视频直链: {data.get('url', '')}")
    elif data.get("type") == "image":
        print(f"图片数量: {data.get('image_count', len(data.get('images', [])))}")
    print("=" * 56)


def cmd_info(link: str) -> int:
    from .resolver import resolve_media
    data = resolve_media(link)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if data.get("status") == "error":
        return 1
    _print_human_summary(data)
    return 0


def cmd_download(link: str, output: str) -> int:
    from .resolver import resolve_media
    data = resolve_media(link)
    if data.get("status") == "error":
        print(f"解析失败: {data.get('error')}", file=sys.stderr)
        return 1

    out_base = Path(output)
    name = _resource_name(data)

    if data.get("type") == "video":
        print(f"正在下载视频: {data.get('title') or data.get('caption') or name}")
        _download_file(data["url"], out_base / f"{name}.mp4")

    elif data.get("type") == "image":
        images = data.get("images", [])
        folder = out_base / name
        print(f"正在下载图集（{len(images)} 张）到: {folder}")
        for idx, img in enumerate(images, 1):
            # 抖音图片为 {url}; 小红书图片为 {url_webp, url_png}（优先 PNG 高清）
            url = img.get("url_png") or img.get("url") or img.get("url_webp")
            if not url:
                continue
            ext = ".png" if img.get("url_png") else (".webp" if img.get("url_webp") else ".jpg")
            _download_file(url, folder / f"{idx:02d}{ext}")
    else:
        print(f"未知的资源类型: {data.get('type')}", file=sys.stderr)
        return 1

    print("下载完成。")
    return 0


def cmd_extract(link: str, output: Optional[str], save_video: bool, model: Optional[str]) -> int:
    from .resolver import resolve_media
    from .transcription import transcribe_video_url

    data = resolve_media(link)
    if data.get("status") == "error":
        print(f"解析失败: {data.get('error')}", file=sys.stderr)
        return 1
    if data.get("type") != "video":
        print("文案提取仅支持视频类型链接（当前为图文）。", file=sys.stderr)
        return 1

    print("正在从视频中提取文案（阿里云百炼）...")
    text = transcribe_video_url(data["url"], model=model)

    name = _resource_name(data)
    print("\n" + "=" * 56)
    print("提取完成！")
    print("=" * 56)

    if output:
        folder = Path(output) / name
        folder.mkdir(parents=True, exist_ok=True)
        transcript_path = folder / "transcript.md"
        title = data.get("title") or data.get("caption") or name
        with open(transcript_path, "w", encoding="utf-8") as f:
            f.write(f"# {title}\n\n")
            f.write("| 属性 | 值 |\n")
            f.write("|------|----|\n")
            f.write(f"| 平台 | {data.get('platform', '-')} |\n")
            f.write(f"| ID | `{name}` |\n")
            f.write(f"| 提取时间 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |\n")
            f.write(f"| 下载链接 | [点击下载]({data.get('url', '')}) |\n\n")
            f.write("---\n\n")
            f.write("## 文案内容\n\n")
            f.write(text)
        print(f"文案已保存到: {transcript_path}")

        if save_video:
            print("正在保存视频...")
            _download_file(data["url"], folder / f"{name}.mp4")

    print("\n文案内容:\n")
    print(text[:500] + "..." if len(text) > 500 else text)
    print("\n" + "=" * 56)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="百分百一键去水印 - 抖音/小红书/通用平台无水印资源命令行工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--link", "-l", required=True, help="分享链接或包含链接的文本（抖音/小红书/通用）")
    parser.add_argument("--action", "-a", choices=["info", "download", "extract"],
                        default="info", help="操作类型: info(解析信息) / download(下载资源) / extract(提取文案)")
    parser.add_argument("--output", "-o", default="./output", help="输出目录 (默认 ./output)")
    parser.add_argument("--model", "-m", help="语音识别模型 (可选，默认 paraformer-v2)")
    parser.add_argument("--save-video", "-v", action="store_true", help="提取文案时同时保存视频")

    args = parser.parse_args()

    try:
        if args.action == "info":
            code = cmd_info(args.link)
        elif args.action == "download":
            code = cmd_download(args.link, args.output)
        else:  # extract
            code = cmd_extract(args.link, args.output, args.save_video, args.model)
        sys.exit(code)
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"\n错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
