---
name: wanyi-watermark-skill
description: "抖音/小红书/通用平台无水印视频与图文下载、文案提取工具. 从分享链接自动识别视频或图文, 获取无水印视频直链或图集(小红书图文提供 WebP/PNG 双格式), 下载资源到本地, 并可用语音识别提取视频文案保存到文件. 适用场景包括获取抖音/小红书视频信息, 下载无水印视频或图片, 批量提取视频文案. 当用户需要处理抖音/小红书等平台分享链接或提取视频内容时触发."
---

# 抖音/小红书无水印资源下载与文案提取

从抖音、小红书(及未适配平台的通用兜底)分享链接获取无水印视频/图文资源, 下载到本地,
并可使用语音识别提取视频文案保存为 Markdown 文件.

## 功能概述

- **智能解析**: 自动识别平台(抖音/小红书/通用)与内容类型(视频/图文), 无需手动指定
- **无水印视频**: 解析出无水印视频直链, 可直接下载
- **图文资源**: 解析图文笔记的全部图片; 小红书图片提供 WebP(轻量)与 PNG(高清)双格式
- **下载资源**: 将视频或图集下载到本地指定目录
- **提取文案**: 通过阿里云百炼语音识别从视频中提取文字内容 (需要 DASHSCOPE_API_KEY)
- **通用兜底**: 遇到未适配平台时, 自动基于页面 og:video / video 标签等通用信息尝试提取

## 环境要求

### 依赖安装

本技能依赖 `wanyi-watermark` 包及其声明的运行依赖. 在 `server/mcp-server` 目录下:

```bash
pip install -e .
```

### API 密钥配置 (仅"提取文案"需要)

文案提取支持两种后端:

**百炼后端(默认):** 阿里云百炼 API, URL 直传, 无需本地 ffmpeg:

```bash
export DASHSCOPE_API_KEY="your-dashscope-api-key"
```

获取 API 密钥: https://help.aliyun.com/zh/model-studio/get-api-key

**SiliconFlow 后端(可选):** 硅基流动 SenseVoice, 需要本地 ffmpeg, 支持大文件自动分段:

```bash
export SILICONFLOW_API_KEY="your-siliconflow-api-key"
```

获取 API 密钥: https://cloud.siliconflow.cn/

> 链接解析、资源下载均无需 API 密钥; 仅"提取文案"需要.

## 使用方法

### 方法一: 使用命令行 (推荐)

技能脚本 `scripts/media_cli.py` 是对包内 CLI 的轻量封装, 可直接运行:

```bash
# 获取信息和无水印直链 (无需 API 密钥, 支持抖音/小红书/通用)
python scripts/media_cli.py --link "分享链接" --action info

# 下载视频或图集到指定目录
python scripts/media_cli.py --link "分享链接" --action download --output ./output

# 提取视频文案并保存为 Markdown (默认百炼, 需要 DASHSCOPE_API_KEY)
python scripts/media_cli.py --link "分享链接" --action extract --output ./output

# 使用硅基流动后端提取文案 (需要 SILICONFLOW_API_KEY + 本地 ffmpeg)
python scripts/media_cli.py --link "分享链接" --action extract --output ./output --backend siliconflow

# 提取文案并同时保存视频
python scripts/media_cli.py --link "分享链接" --action extract --output ./output --save-video
```

若已 `pip install -e .` 安装本包, 亦可使用模块或控制台入口:

```bash
python -m wanyi_watermark.cli --link "分享链接" --action info
# 或
wanyi-watermark-cli --link "分享链接" --action info
```

### 输出目录结构

```
output/
├── 7600361826030865707/        # 视频ID / 笔记ID 为文件夹名
│   ├── transcript.md           # extract 时生成的 Markdown 文案
│   └── 7600361826030865707.mp4 # 使用 --save-video 时保存
├── <note_id>/                  # 图文笔记: 图集逐张保存
│   ├── 01.png
│   └── 02.png
└── ...
```

### Markdown 文案格式

```markdown
# 视频标题

| 属性 | 值 |
|------|----|
| 平台 | douyin |
| ID | `7600361826030865707` |
| 提取时间 | 2026-05-29 14:19:00 |
| 下载链接 | [点击下载](url) |

---

## 文案内容

(语音识别的文字内容)
```

## 工作流程

### 解析与下载

1. 从分享文本中提取链接, 按域名识别平台(抖音/小红书/通用)
2. 自动判断视频或图文类型
3. 视频: 解析无水印直链; 图文: 解析全部图片(小红书提供 WebP/PNG 双格式)
4. 专用解析失败时, 自动回退到通用兜底逻辑

### 提取视频文案

1. 解析链接得到无水印视频直链
2. 调用转写后端进行语音识别:
   - **百炼(默认):** paraformer-v2, URL 直传, 无需本地下载
   - **SiliconFlow:** 下载视频 → 提取音频 → 大文件自动分段(>1h/>50MB) → 逐段上传转写
3. 返回识别文本, 可保存为 Markdown

## 常见问题

### 无法解析链接

- 确保链接是有效的抖音/小红书分享链接
- 抖音链接通常形如 `https://v.douyin.com/xxxxx/`; 小红书形如 `https://xhslink.com/xxxxx`
- 未适配平台会自动尝试通用兜底, 但可能因页面无直链或需要登录而失败

### 提取文案失败

- 检查 `DASHSCOPE_API_KEY`(百炼)或 `SILICONFLOW_API_KEY`(硅基流动)环境变量是否已设置且有效
- 使用 SiliconFlow 后端时需确保本地已安装 ffmpeg
- 文案提取仅支持视频类型(图文笔记无音频可转写)

### 下载失败 / 403

- 个别 CDN 对直链有 Referer / 防盗链校验. 当前版本未接入服务端下载代理(已登记后续优化)

## 注意事项

- 本工具仅供学习和研究使用
- 使用时需遵守相关法律法规, 请勿用于任何侵犯版权或违法的目的
