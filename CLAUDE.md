# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

这是一个基于 Model Context Protocol (MCP) 的服务器,用于从抖音和小红书分享链接中获取无水印视频并提取文本内容。

## 核心架构

### 1. 处理器模式 (Processor Pattern)

项目采用处理器模式,每个平台有独立的处理器类:

- **DouyinProcessor** (`douyin_processor.py`): 处理抖音视频
  - 解析分享链接获取视频 ID 和无水印 URL
  - 使用阿里云百炼 API (dashscope) 进行语音识别
  - 直接从视频 URL 进行转录(无需下载)

- **XiaohongshuProcessor** (`xiaohongshu_processor.py`): 处理小红书视频
  - 解析 HTML 页面提取视频直链
  - 启发式评分算法选择无水印版本(优先 114 质量码)
  - 域名规范化和 HEAD 探测验证

### 1.5 解析门面 (`resolver.py`) —— 单一事实源

"按平台分发 + 自动识别 视频/图文 + 通用兜底" 的编排逻辑统一收敛在 `resolver.py`，
由 **MCP 工具 / CLI / WebUI / Skill 四个消费方共同复用**：

- `resolve_douyin` / `resolve_xiaohongshu` / `resolve_generic`：返回结构化 **dict**（与各 MCP 工具输出逐字一致）
- `resolve_media`：顶层入口，按链接域名自动选平台（供 CLI/WebUI/Skill 的"单输入框"使用）
- `detect_platform`：按域名判定 douyin / xiaohongshu / generic

`server.py` 的工具均为薄包装：`json.dumps(resolve_*(...))`。
转写逻辑统一在 `transcription.py`（`transcribe_video_url`，dashscope）。
> 处理器在 resolver 内部**延迟导入**，保持 `import resolver` 轻量（不强依赖 dashscope/ffmpeg）。

### 2. MCP 服务器 (`server.py`)

使用 FastMCP 框架提供统一的 MCP 接口:

**工具 (Tools):**
- `parse_douyin_link`: 解析抖音链接，自动识别视频/图文并返回无水印资源(无需 API 密钥)
- `parse_xhs_link`: 解析小红书链接，自动识别视频/图文并返回无水印资源(无需 API 密钥)
- `extract_douyin_text`: 从抖音视频中提取文本内容(需要 API 密钥)

资源接口已在当前版本移除，统一通过上述工具完成解析。

**提示词 (Prompts):**
- `watermark_removal_guide`: 使用指南（含面向用户的纯文本展示格式约定）

**输出格式约定（重要，勿改）:**
- 所有解析工具（`parse_xhs_link` / `parse_douyin_link` / `parse_generic_link`）一律返回【完整 JSON 字符串】（含 platform/type/title/caption/url/images 等全部字段），**不在工具内部拍平成纯文本**。
- “纯文本（标题/文案/视频图片链接，禁止 Markdown、不省略不截断）”是 **LLM 回复最终用户时的展示格式**，由各工具 docstring 与 `watermark_removal_guide` 提示词指示 LLM 完成，**不是工具的返回值格式**。
- 数据流：工具 ──返回完整 JSON──▶ LLM ──整理成纯文本──▶ 用户。
- 注：曾把工具返回值直接改成纯文本（`_format_plain_result`）会丢失结构化字段、且与报错 JSON 不一致，已回退，后续请保持 JSON 返回。

### 3. 关键技术实现

**视频 URL 解析:**
- 抖音: 通过 `window._ROUTER_DATA` 提取 JSON 数据,替换 `playwm` 为 `play` 去水印
- 小红书: 多重策略(video 标签、og:video meta、正则回退),启发式评分选择最优链接

**文本提取（双 ASR 后端）:**
- **dashscope（默认）**: 阿里云百炼 paraformer-v2，URL 直传，无需本地下载
- **siliconflow**: 硅基流动 SenseVoice，需下载视频 + ffmpeg 提取音频；大文件（>1h 或 >50MB）自动按 540s/段分割
- 统一入口: `transcription.transcribe_video_url(backend=...)`
- 后端选择优先级: 显式参数 > 环境变量 `ASR_BACKEND` > 默认 `dashscope`

## 常用命令

### 本地开发测试

```bash
# 启动 MCP 服务器(需要设置 DASHSCOPE_API_KEY 环境变量)
python -m wanyi_watermark

# 测试抖音链接解析(无需 API 密钥)
python -m wanyi_watermark.douyin_processor "<抖音分享链接>"

# 测试小红书链接解析(无需 API 密钥)
python -m wanyi_watermark.xiaohongshu_processor "<小红书分享链接>"
```

### 交付渠道（CLI / WebUI / Skill）

除 MCP 工具外，另提供三种面向人/脚本的交付渠道，均统一架在 `resolver` + `transcription` 之上：

```bash
# CLI：解析 / 下载 / 提取文案（支持抖音/小红书/通用，自动识别）
python -m wanyi_watermark.cli -l "<分享链接>" -a info
python -m wanyi_watermark.cli -l "<分享链接>" -a download -o ./output
export DASHSCOPE_API_KEY="sk-xxx"
python -m wanyi_watermark.cli -l "<分享链接>" -a extract -o ./output

# 使用硅基流动 SenseVoice 后端提取文案（需 ffmpeg）
export SILICONFLOW_API_KEY="sk-xxx"
python -m wanyi_watermark.cli -l "<分享链接>" -a extract -b siliconflow -o ./output

# WebUI：浏览器界面，默认 http://localhost:8080
python web/app.py

# Skill：见 wanyi-watermark-skill/SKILL.md（脚本 scripts/media_cli.py 为 CLI 薄封装）
```

### 安装依赖

```bash
# 开发模式安装
pip install -e .

# 使用 uv 运行(生产环境)
uvx wanyi-watermark
```

### Claude Desktop 本地开发配置

```json
{
  "mcpServers": {
    "wanyi-watermark": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/path/to/wanyi-watermark",
        "python",
        "-m",
        "wanyi_watermark"
      ],
      "env": {
        "DASHSCOPE_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

## 环境变量

- `DASHSCOPE_API_KEY`: 阿里云百炼 API 密钥（dashscope 后端文本提取必需）
  - 获取地址: https://help.aliyun.com/zh/model-studio/get-api-key
- `SILICONFLOW_API_KEY`: 硅基流动 API 密钥（siliconflow 后端文本提取必需）
  - 获取地址: https://cloud.siliconflow.cn/
- `ASR_BACKEND`: 默认 ASR 后端，可选 `dashscope`（默认）或 `siliconflow`

## 重要注意事项

### 模块化设计
- 避免在包初始化时导入重量级依赖(如 ffmpeg)
- 支持独立运行各处理器模块进行测试

### API 密钥使用
- 获取下载链接功能无需 API 密钥
- 文本提取功能需要有效的 DASHSCOPE_API_KEY
- API 密钥通过环境变量传入,不硬编码

### 文件名处理
- 使用正则表达式清理非法文件名字符: `r'[\\/:*?"<>|]'`
- 避免文件名过长导致的错误

### 临时文件管理
- DouyinProcessor 使用 tempfile.mkdtemp() 创建临时目录
- 在 `__del__` 方法中自动清理临时文件
- 捕获 ImportError 避免 Python 关闭时的清理错误

### 请求头配置
- 抖音使用移动端 UA (iPhone iOS 17_2)
- 小红书优先使用桌面端 UA (Windows Chrome),失败时回退到移动端

## 上游跟踪与延后/待回迁

本仓基于上游 `douyin-mcp-server` 二开，采用 **「二开为主干 + 跟踪上游」** 策略。
上游同步、文件映射表、待回迁 backlog、技术债清单统一记录在 **[`UPSTREAM_SYNC.md`](./UPSTREAM_SYNC.md)**。

**本阶段【暂不实现】、已在代码内留 `TODO(upstream-backport, ...)` 的延后项：**
- ~~硅基流动 SenseVoice 可选 ASR 后端 → `transcription.py`~~ ✅ 已实现（`siliconflow_asr.py`）
- ~~大文件自动分段转写 → `transcription.py`~~ ✅ 已实现（siliconflow 后端自动分段，>1h 或 >50MB）
- 服务端下载代理（带 Referer 解决 403）→ `web/app.py`、`cli.py`

改动上述方向前，请先阅读 `UPSTREAM_SYNC.md`。

## 代码结构设计原则

1. **职责分离**: 处理器类专注于平台特定逻辑,MCP 服务器负责协议适配
2. **错误处理与输出格式**: 所有异常向上抛出,由 MCP 工具层统一捕获;**所有工具(成功与失败)统一返回 JSON 字符串**。纯文本仅为 LLM 面向用户的展示格式(详见上文"输出格式约定"),切勿改成工具直接返回纯文本
3. **异步支持**: 下载操作使用异步接口,支持进度报告 (ctx.report_progress)
4. **灵活配置**: 通过构造函数参数和环境变量配置,避免硬编码
