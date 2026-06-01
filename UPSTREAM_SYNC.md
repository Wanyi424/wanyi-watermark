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
| 本仓版本 | wanyi-watermark v1.1.0 |
| 本次整合范围 | 引入 **CLI / WebUI / Skill** 三交付渠道（架在二开处理器之上） |

---

## 2. 文件映射表（上游 ↔ 二开）

上游更新时，只需 diff 下列对应文件，判断是否需要回迁。

| 上游文件 | 二开对应 | 跟踪要点 |
|----------|----------|----------|
| `douyin_mcp_server/server.py`（处理器+工具，dashscope） | `wanyi_watermark/douyin_processor.py` + `server.py` + `resolver.py` | 抖音 `window._ROUTER_DATA` 解析若上游因页面改版而修复，需同步到 `douyin_processor.py`。二开已额外支持图文/超时，**领先于上游**。 |
| `douyin-video/scripts/douyin_downloader.py`（CLI，硅基流动 + 大文件分段，仅抖音） | `wanyi_watermark/cli.py`（已重平台化：抖音/小红书/通用 + dashscope） | **未照搬**。其硅基流动后端与大文件分段属待回迁 backlog（见 §3）。 |
| `web/app.py`（FastAPI WebUI） | `web/app.py`（端点改为统一走 `resolver`，多平台单输入框） | 上游 `/api/video/download` 服务端下载代理属待回迁 backlog（见 §3）。 |
| `web/templates/index.html` | `web/templates/index.html`（多平台 + 图集 + 已做专业 UI/UX 重设计） | 功能须与上游对齐：解析/转写/下载/复制导出。 |
| `douyin-video/SKILL.md` + `scripts/` | `wanyi-watermark-skill/SKILL.md` + `scripts/media_cli.py` | 已多平台化；脚本改为对包内 CLI 的薄封装。 |
| `pyproject.toml`（fastapi/uvicorn/jinja2） | `pyproject.toml`（已同步增加同款依赖 + CLI 入口） | — |

**二开独有（上游没有，勿被上游覆盖）：**
`xiaohongshu_processor.py`、`generic_extractor.py`、`resolver.py`、`transcription.py`、
抖音图文解析（`douyin_processor.parse_image_note`）、工具精简合并（`parse_*_link` 自动识别 video/image + 兜底）。

---

## 3. 待回迁 backlog（本阶段【暂不实现】，已在代码内留 TODO）

> 多数延后；其中 3.3「服务端下载代理」已在 WebUI 落地（见下）。每项标注：上游来源、价值、前置验证、代码内 TODO 位置。

### 3.1 硅基流动 SenseVoice 作为可选 ASR 后端
- **上游来源**：`douyin-video/scripts/douyin_downloader.py` → `transcribe_single_audio()`
- **价值**：多后端可切换，便于降本/容灾。
- **前置验证**：设计 env 切换（dashscope ↔ siliconflow）。
- **TODO 位置**：`wanyi_watermark/transcription.py` 顶部。

### 3.2 大文件自动分段转写
- **上游来源**：`douyin-video/scripts/douyin_downloader.py` → `split_audio()` / `extract_text_from_audio()`（>1h 或 >50MB 按 9min/段分割）
- **价值**：长音频转写稳健性。
- **前置验证**：**先确认阿里云百炼 paraformer-v2 的长音频上限**——百炼 URL 直传为服务端异步转写，很可能原生支持长音频，则本项对二开**可能并不需要**；仅在切到硅基流动时才必要。
- **TODO 位置**：`wanyi_watermark/transcription.py` 顶部。

### 3.3 服务端下载代理（带 Referer，解决 403/跨域）—— ✅ 已落地（WebUI 侧）
- **状态变更**：原标记延后；因实测小红书图片直链 `sns-webpic-qc.xhscdn.com` 直接 403 无法显示（前提已变），现已在 WebUI 正式落地通用媒体代理。
- **已实现**：`web/app.py` → `GET /api/proxy?url=&download=&filename=`，按目标域名补 Referer/UA、透传 Range（支持视频拖动）、含基础 SSRF 防护（仅 http/https、拒绝内网/环回/链路本地等）。前端图片显示、视频内嵌播放、PNG/WebP 真实下载统一走该代理。
- **Fake-IP 兼容（Clash/sing-box）**：Fake-IP 模式下所有公网域名解析到 `198.18.0.0/15`，原 SSRF 逻辑会误杀。现策略：① 字面 IP 直连仍严格拦截内网/保留段（含 `198.18/15`，故 `http://198.18.x.x` 原始 IP 仍拒绝）；② 域名解析落在 Fake-IP 段时改用 `_MEDIA_HOST_WHITELIST`（抖音/字节系 + 小红书系，后缀匹配）放行，真实内网段仍拦截。
- **重定向安全**：改为手动跟随（`allow_redirects=False` + `_MAX_REDIRECTS`），**每一跳重新执行 SSRF 校验**，防开放重定向→SSRF；按当前跳域名重选 Referer/UA。
- **错误编码**：错误响应统一用 `PlainTextResponse`（自带 `charset=utf-8`），消除"非法或不被允许的资源地址"中文乱码。
- **上游参考**：`web/app.py` → `GET /api/video/download`（流式 + Referer 头）。
- **待跟进**：`wanyi_watermark/cli.py` 的下载仍直连源站（`DOWNLOAD_HEADERS`）；如遇 403 可改走相同的按域名 Referer 逻辑。

---

## 4. 本仓技术债（整合窗口期可顺手治理，与上游无关）

| 项 | 说明 | 位置 |
|----|------|------|
| xhs 视频→图文回退靠错误文案子串匹配 | 较脆弱，建议改为"基于页面数据结构判定" | `resolver.py: resolve_xiaohongshu`（已留 NOTE） |
| 抖音/小红书输出字段不一致 | 抖音仅 `caption` 无 `title`，小红书有 `title`；属历史产品约定，如需统一须评估影响 | `resolver.py` |
| `ffmpeg-python` 对当前工具面基本为死依赖 | 转写走 URL 直传，`extract_audio`/`download_video` 未被工具调用；如不接入硅基流动可考虑移除 | `pyproject.toml` / `douyin_processor.py` |

---

## 5. 架构备忘：为什么三渠道不照搬上游 `douyin_downloader.py`

上游 WebUI/CLI/Skill 均依赖独立脚本 `douyin_downloader.py`，而它是
**①硅基流动后端、②仅抖音、③自带一套与包重复的 DouyinProcessor**。照搬会同时违背
"二开为主干""硅基流动延后"并引入重复处理器。故本次将三渠道统一架在
`resolver.py`（解析门面）+ `transcription.py`（dashscope 转写）之上，自动获得
抖音/小红书/通用/图文全部能力。
