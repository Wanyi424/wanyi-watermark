# 上游同步与待回迁记录 (UPSTREAM_SYNC)

本仓库 (`wanyi-watermark`) 基于上游 [`douyin-mcp-server`](https://github.com/yzfly/douyin-mcp-server)
二次开发，采用 **「二开为主干 + 跟踪上游」** 策略：二开继续作为主干演进，
上游作为"灵感与改进来源"，**按需、有选择地回迁**，而非整体合并/重建。

> 本文件是上游跟踪的单一入口：记录①已对齐的上游版本、②文件映射表、③待回迁 backlog、④本仓技术债。
> 评估节奏建议：上游打 tag 或每季度 diff 一次本表所列文件，而非实时跟随。

---

## 1. 已对齐上游版本

| 项 | 值 |
|----|----|
| 上游仓库 | yzfly/douyin-mcp-server |
| 已参考版本 | **v1.4.0**（含 WebUI / Claude Skill / CLI / 硅基流动 ASR） |
| 本地参考副本 | `server/douyin-mcp-server-origin/` |
| 本仓版本 | wanyi-watermark v1.2.0 |
| 本次整合范围 | 引入 **CLI / WebUI / Skill** 三交付渠道（架在二开处理器之上） |

---

## 2. 文件映射表（上游 ↔ 二开）

上游更新时，只需 diff 下列对应文件，判断是否需要回迁。

| 上游文件 | 二开对应 | 跟踪要点 |
|----------|----------|----------|
| `douyin_mcp_server/server.py`（处理器+工具，dashscope） | `wanyi_watermark/douyin_processor.py` + `server.py` + `resolver.py` | 抖音 `window._ROUTER_DATA` 解析若上游因页面改版而修复，需同步到 `douyin_processor.py`。二开已额外支持 `parse_media` 一次判断视频/图文、超时与通用兜底，**领先于上游**。 |
| `douyin-video/scripts/douyin_downloader.py`（CLI，硅基流动 + 大文件分段，仅抖音） | `wanyi_watermark/cli.py` + `transcription.py` + `siliconflow_asr.py` + `media_fetch.py` | 硅基流动后端与大文件分段已回迁并多平台化；硅基流动本地下载复用共享媒体取源逻辑。普通 CLI 下载仍保留为后续治理项。 |
| `web/app.py`（FastAPI WebUI） | `web/app.py`（端点改为统一走 `resolver`，多平台单输入框） | 已额外落地 `/api/proxy` 媒体代理、解析链路中文耗时日志，并复用 `media_fetch.py` 的安全取源逻辑。 |
| `web/templates/index.html` | `web/templates/index.html`（多平台 + 图集 + 已做专业 UI/UX 重设计） | 已额外支持前端耗时日志、图集拖拽缩放灯箱、缩略图即时打开 + 高清图后台替换。功能仍须与上游对齐：解析/转写/下载/复制导出。 |
| `douyin-video/SKILL.md` + `scripts/` | `wanyi-watermark-skill/SKILL.md` + `scripts/media_cli.py` | 已多平台化；脚本改为对包内 CLI 的薄封装。 |
| `pyproject.toml`（fastapi/uvicorn/jinja2） | `pyproject.toml`（已同步增加同款依赖 + CLI 入口） | — |

**二开独有（上游没有，勿被上游覆盖）：**
`xiaohongshu_processor.py`、`generic_extractor.py`、`resolver.py`、`transcription.py`、`diagnostics.py`、`siliconflow_asr.py`、`media_fetch.py`、
抖音/小红书 `parse_media` 一次抓取后自动识别 video/image、工具精简合并（`parse_*_link` 自动识别 video/image + 兜底）。

---

## 3. 待回迁 backlog（本阶段【暂不实现】，已在代码内留 TODO）

> 多数延后；其中 3.1/3.2 已落地、3.3「服务端下载代理」已在 WebUI 落地（见下）。每项标注：上游来源、价值、前置验证、代码内 TODO 位置。

### 3.1 硅基流动 SenseVoice 作为可选 ASR 后端 —— ✅ 已落地
- **上游来源**：`douyin-video/scripts/douyin_downloader.py` → `transcribe_single_audio()`
- **已实现**：`wanyi_watermark/siliconflow_asr.py`（独立模块）+ `transcription.py`（双后端调度门面）
- **切换方式**：环境变量 `ASR_BACKEND=siliconflow`，或 CLI `--backend`、WebUI 请求字段、MCP 也读取 `ASR_BACKEND`
- **环境变量**：`SILICONFLOW_API_KEY`（硅基流动 API 密钥）

### 3.2 大文件自动分段转写 —— ✅ 已落地（siliconflow 后端）
- **上游来源**：`douyin-video/scripts/douyin_downloader.py` → `split_audio()` / `extract_text_from_audio()`
- **已实现**：`siliconflow_asr.py` 内，>1h 或 >50MB 时自动按 540s/段 ffmpeg 分割 → 逐段转写 → 合并；系统无 ffmpeg 时使用 `imageio-ffmpeg` 内置二进制兜底
- **备注**：dashscope 后端仍为 URL 直传（百炼服务端原生支持长音频，无需客户端分段）

### 3.3 服务端下载代理（带 Referer，解决 403/跨域）—— ✅ 已落地（WebUI 侧）
- **状态变更**：原标记延后；因实测小红书图片直链 `sns-webpic-qc.xhscdn.com` 直接 403 无法显示（前提已变），现已在 WebUI 正式落地通用媒体代理。
- **已实现**：`web/app.py` → `GET /api/proxy?url=&download=&filename=`，按目标域名补 Referer/UA、透传 Range（支持视频拖动）、含基础 SSRF 防护（仅 http/https、拒绝内网/环回/链路本地等）。前端图片显示、视频内嵌播放、PNG/WebP 真实下载统一走该代理；核心取源逻辑已抽到 `wanyi_watermark/media_fetch.py`，供 WebUI 与 siliconflow 本地下载共用。
- **Fake-IP 兼容（Clash/sing-box）**：Fake-IP 模式下所有公网域名解析到 `198.18.0.0/15`，原 SSRF 逻辑会误杀。现策略：① 字面 IP 直连仍严格拦截内网/保留段（含 `198.18/15`，故 `http://198.18.x.x` 原始 IP 仍拒绝）；② 域名解析落在 Fake-IP 段时改用 `MEDIA_HOST_WHITELIST`（抖音/字节系 + 小红书系，后缀匹配）放行，真实内网段仍拦截。
- **重定向安全**：改为手动跟随（`allow_redirects=False` + `MAX_REDIRECTS`），**每一跳重新执行 SSRF 校验**，防开放重定向→SSRF；按当前跳域名重选 Referer/UA。
- **错误编码**：错误响应统一用 `PlainTextResponse`（自带 `charset=utf-8`），消除"非法或不被允许的资源地址"中文乱码。
- **上游参考**：`web/app.py` → `GET /api/video/download`（流式 + Referer 头）。
- **待跟进**：`wanyi_watermark/cli.py` 的下载仍直连源站（`DOWNLOAD_HEADERS`）；如遇 403 可改走相同的按域名 Referer 逻辑。

### 3.4 解析诊断与 WebUI 图集预览 —— ✅ 已落地（二开侧）
- **解析诊断**：`web/templates/index.html` 点击解析后生成 `X-Parse-Trace-Id`，浏览器 Console 输出中文耗时表；`web/app.py` 与 `wanyi_watermark/diagnostics.py` 在后端输出同一追踪 ID 的单步/累计耗时。
- **平台解析优化**：抖音/小红书均新增 `parse_media`，已适配平台一次抓取页面后基于页面数据结构判断视频/图文，避免图文场景重复请求。
- **小红书视频候选**：移除旧版 114 质量码改写与阻塞式 HEAD 探测；候选只做协议规范化与来源评分，优先使用页面提供的 `og:video` / `masterUrl`。
- **图集灯箱**：支持拖拽、滚轮缩放、双指缩放和复位；打开时先复用已加载缩略图 `currentSrc`，后台加载 PNG/原图后无感替换。

---

## 4. 本仓技术债（整合窗口期可顺手治理，与上游无关）

| 项 | 说明 | 位置 |
|----|------|------|
| 抖音/小红书输出字段不一致 | 抖音仅 `caption` 无 `title`，小红书有 `title`；属历史产品约定，如需统一须评估影响 | `resolver.py` |
| `ffmpeg-python` 仍有历史依赖面 | siliconflow 后端已改用 `imageio-ffmpeg` 兜底执行 ffmpeg；`douyin_processor.extract_audio()` 仍保留 `ffmpeg-python` 历史路径，dashscope 链路不依赖本地 ffmpeg | `pyproject.toml` / `douyin_processor.py` / `siliconflow_asr.py` |

---

## 5. 架构备忘：为什么三渠道不照搬上游 `douyin_downloader.py`

上游 WebUI/CLI/Skill 均依赖独立脚本 `douyin_downloader.py`，而它是
**①硅基流动后端、②仅抖音、③自带一套与包重复的 DouyinProcessor**。照搬会同时违背
"二开为主干""硅基流动延后"并引入重复处理器。故本次将三渠道统一架在
`resolver.py`（解析门面）+ `transcription.py`（dashscope 转写）之上，自动获得
抖音/小红书/通用/图文全部能力。
