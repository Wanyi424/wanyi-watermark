#!/usr/bin/env python3
"""百分百一键去水印 - Skill 命令行封装。

对包内 `wanyi_watermark.cli` 的轻量封装：把 mcp-server 目录加入 sys.path，
使本脚本无需 `pip install` 即可直接运行（仍需安装 requests / dashscope 等第三方依赖）。

用法见同目录 SKILL.md，例如：
    python scripts/media_cli.py --link "<分享链接>" --action info
"""

import sys
import pathlib

# scripts/ -> wanyi-watermark-skill/ -> mcp-server/（包根目录）
_PKG_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PKG_ROOT))

from wanyi_watermark.cli import main

if __name__ == "__main__":
    main()
