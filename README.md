# 抖音/小红书无水印资源提取 - 百分百一键去水印 - MCP 服务器

[![PyPI version](https://badge.fury.io/py/wanyi-watermark.svg)](https://badge.fury.io/py/wanyi-watermark)
[![Python version](https://img.shields.io/pypi/pyversions/wanyi-watermark.svg)](https://pypi.org/project/wanyi-watermark/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

基于 Model Context Protocol (MCP) 的视频链接解析与媒体资源提取服务器,可以从抖音和小红书分享链接中提取无水印视频和图片,并支持文本转写功能。

<a href="https://glama.ai/mcp/servers/@Ryan7t/wanyi-watermark">
  <img width="380" height="200" src="https://glama.ai/mcp/servers/@Ryan7t/wanyi-watermark/badge" alt="Wanyi Watermark Remover MCP server" />
</a>

## 📋 项目声明

**官方文档地址:** https://github.com/Ryan7t/wanyi-watermark

请以本项目的 [README.md](https://github.com/Ryan7t/wanyi-watermark) 文件为准,了解项目的功能特性、使用方法、API 配置说明等详细信息。

**重要提醒:** 第三方平台如因自身 MCP Server 功能支持度限制而无法正常使用,请联系相应平台方。本项目不提供任何形式的技术支持或保证,用户需自行承担使用本项目可能产生的任何损失或损害。

**法律声明:**
1. 本项目基于 Apache 2.0 协议发布
2. 本项目仅供学习和研究使用,不得用于任何违法或违规目的
3. 本项目的使用必须遵守相关法律法规
4. 本项目的作者和贡献者不对项目的任何部分承担法律责任

## ✨ 功能特性

### 核心能力

- 🔗 **智能链接解析** - 自动识别抖音/小红书分享链接,解析真实视频地址
- 📹 **无水印视频提取** - 获取高清无水印视频直链,支持直接下载
- 🖼️ **图片资源提取** - 支持图文笔记解析,提供多格式图片(WebP轻量/PNG高清)
- 📝 **视频文本转写** - 基于AI语音识别,从视频中提取文字内容（支持阿里云百炼 / 硅基流动 SenseVoice 双后端）
- 🌐 **通用平台兜底** - 遇到未适配平台时,自动尝试通用解析机制

### 技术特点

- ✅ **链接解析无需密钥** - 视频/图片资源提取完全免费,无需任何 API 配置
- ✅ **智能类型识别** - 自动判断内容类型(视频/图文),无需手动指定
- ✅ **多格式支持** - 小红书图文提供 WebP(快速预览)和 PNG(高清编辑)双格式
- ✅ **高精度文本转写** - 支持阿里云百炼 (paraformer-v2) 与硅基流动 (SenseVoice) 双后端,通过环境变量或参数切换
- ✅ **多平台兼容** - 支持抖音、小红书,并提供通用解析兜底机制

## 🚀 快速开始

### 步骤 1:获取 API 密钥

**方案 A（默认）**：前往 [阿里云百炼 API](https://help.aliyun.com/zh/model-studio/get-api-key?) 获取 `DASHSCOPE_API_KEY`

**方案 B（可选）**：前往 [硅基流动](https://cloud.siliconflow.cn/) 获取 `SILICONFLOW_API_KEY`（需本地有 ffmpeg）

> 链接解析和资源提取无需任何 API 密钥,仅文本转写功能需要。

### 步骤 2:配置环境变量

在 Claude Desktop、Cherry Studio 等支持 MCP Server 的应用配置文件中添加以下配置:

```json
{
  "mcpServers": {
    "wanyi-watermark": {
      "command": "uvx",
      "args": ["wanyi-watermark"],
      "env": {
        "DASHSCOPE_API_KEY": "sk-xxxx",
        "SILICONFLOW_API_KEY": "sk-xxxx (可选)",
        "ASR_BACKEND": "dashscope"
      }
    }
  }
}
```

> `ASR_BACKEND` 可选 `dashscope`（默认,URL 直传无需 ffmpeg）或 `siliconflow`（需下载+ffmpeg 提取音频）。

### 步骤 3:开始使用

配置完成后,您就可以在支持的应用中正常调用 MCP 工具了。

## ⚙️ API 配置说明

### 转写后端选择（>= 1.2.0）

| 后端 | 环境变量 | 优势 | 要求 |
|------|----------|------|------|
| **dashscope**（默认） | `DASHSCOPE_API_KEY` | URL 直传、无需本地 ffmpeg、服务端异步长音频 | 阿里云百炼 API |
| siliconflow | `SILICONFLOW_API_KEY` | 支持离线模型、大文件自动分段(>1h/50MB) | 硅基流动 API + 本地 ffmpeg |

通过 `ASR_BACKEND` 环境变量（或 CLI `--backend` / WebUI 请求字段）切换。优先级：显式参数 > 环境变量 > 默认 dashscope。


> **注意**: API 密钥仅用于视频文本转写功能,链接解析和资源提取无需密钥。

## 🛠️ 工具说明

### 主要工具

#### `parse_douyin_link`

解析抖音分享链接,自动识别视频/图文类型并返回无水印资源。

**参数:**
- `share_link` (string): 抖音分享链接或包含链接的文本

**返回:**
- 视频类型:JSON 格式的无水印视频下载链接和视频信息
- 图文类型:JSON 格式的图片列表和笔记信息

**特点:**
- ✅ 无需 API 密钥
- ✅ 自动识别内容类型
- ✅ 失败时自动尝试通用解析

#### `parse_xhs_link`

解析小红书分享链接,自动识别视频/图文类型并返回无水印资源。

**参数:**
- `share_link` (string): 小红书分享链接或包含链接的文本

**返回:**
- 视频类型:JSON 格式的无水印视频下载链接和视频信息
- 图文类型:JSON 格式的图片列表(同时提供 WebP 和 PNG 两种格式)

**特点:**
- ✅ 无需 API 密钥
- ✅ 自动识别内容类型
- ✅ 图文笔记提供双格式图片(WebP轻量 + PNG高清)

#### `parse_generic_link`

通用平台链接解析,适用于未明确支持的平台或作为备用方案。

**参数:**
- `share_link` (string): 任意平台的分享链接或包含链接的文本

**返回:**
- 包含资源链接和信息的 JSON 字符串

### 特殊功能

#### `extract_douyin_text`

从抖音视频中提取语音文本内容。

**参数:**
- `share_link` (string): 抖音分享链接或包含链接的文本
- `model` (string, 可选): 语音识别模型,默认按后端自动选择

**环境变量要求:**
- `DASHSCOPE_API_KEY`: 阿里云百炼 API 密钥（默认后端）
- `SILICONFLOW_API_KEY`: 硅基流动 API 密钥（可选后端）
- `ASR_BACKEND`: 切换后端,可选 `dashscope` / `siliconflow`

## 📦 系统要求

### 运行环境
- **Python**: 3.10 或更高版本

### 依赖库
- `mcp` - Model Context Protocol 支持
- `requests` - HTTP 请求处理
- `ffmpeg-python` - 音视频处理
- `tqdm` - 进度条显示
- `dashscope` - 阿里云百炼 API 客户端

## 🔧 本地开发

### 克隆仓库

```bash
git clone https://github.com/Ryan7t/wanyi-watermark.git
cd wanyi-watermark
```

### 安装依赖(开发模式)

```bash
pip install -e .
```

### 运行测试

```bash
# 启动服务器
python -m wanyi_watermark

# 测试抖音链接解析
python -m wanyi_watermark.douyin_processor "<抖音分享链接>"

# 测试小红书链接解析
python -m wanyi_watermark.xiaohongshu_processor "<小红书分享链接>"
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
        "DASHSCOPE_API_KEY": "your-api-key-here",
        "SILICONFLOW_API_KEY": "your-key-here (可选)",
        "ASR_BACKEND": "dashscope"
      }
    }
  }
}
```

## ⚠️ 免责声明

### 使用风险
- 使用者对本项目的使用完全自主决定,并自行承担所有风险
- 作者对使用者因使用本项目而产生的任何损失、责任或风险概不负责

### 法律合规
- 使用者必须自行研究相关法律法规,确保使用行为合法合规
- 任何违反法律法规导致的法律责任和风险,均由使用者自行承担
- 禁止使用本工具从事任何侵犯知识产权的行为
- 开发者不参与、不支持、不认可任何非法内容的获取或分发

### 责任限制
- 使用者不得将项目作者、贡献者或相关方与使用行为联系起来
- 不得要求作者对使用项目产生的任何损失或损害负责
- 基于本项目的二次开发、修改或编译程序与原作者无关

**⚠️ 重要提醒**:在使用本项目前,请认真阅读并完全理解上述免责声明。如有疑问或不同意任何条款,请勿使用本项目。继续使用即视为完全接受上述声明并自愿承担所有风险和后果。

## 📄 许可证

Apache License 2.0

## 👨‍💻 作者

- **wanyi**
- Email: 2368077712@qq.com
- GitHub: [@Ryan7t](https://github.com/Ryan7t)

## 🤝 贡献

欢迎提交 Issue 和 Pull Request!

## 🙏 致谢

本项目基于 [douyin-mcp-server](https://github.com/yzfly/douyin-mcp-server) 进行二次开发,感谢原作者的贡献。

## 📝 更新日志

### v1.0.0 (2025-01-23)

- 🎉 首次发布
- ✨ 支持抖音视频/图文解析
- ✨ 支持小红书视频/图文解析
- ✨ 通用平台兜底解析机制
- ✨ 视频文本转写功能
- 🔧 品牌化为"百分百一键去水印"