#!/usr/bin/env python3
"""
百分百一键去水印 - WebUI

浏览器可视化界面，复用包内统一解析门面（resolver）与转写服务（transcription），
支持抖音 / 小红书 / 通用平台的链接解析、图集预览与视频文案提取。

启动方式:
    cd server/mcp-server
    export DASHSCOPE_API_KEY="sk-xxx"   # 仅"提取文案"需要
    python web/app.py
    # 访问 http://localhost:8080
"""

import os
import sys
import logging
import time
import uuid
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.parse import quote

# 将包根目录（mcp-server/）加入路径，便于直接 `python web/app.py` 运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wanyi_watermark.diagnostics import (
    parse_log,
    reset_parse_trace,
    set_parse_trace,
    short_text,
)

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn

from wanyi_watermark.media_fetch import (
    FetchError,
    fetch_media_stream,
    is_safe_public_url as _is_safe_public_url,
)

_LOG_LEVEL = getattr(logging, os.getenv("WANYI_WEB_LOG_LEVEL", "INFO").upper(), logging.INFO)
logging.basicConfig(
    level=_LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("wanyi_watermark").setLevel(_LOG_LEVEL)
logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent

app = FastAPI(title="百分百一键去水印", version="1.2.0")
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))


class ParseRequest(BaseModel):
    """解析请求：任意平台分享链接或包含链接的文本。"""
    url: str


class ExtractRequest(BaseModel):
    """文案提取请求。"""
    url: str
    api_key: str = ""          # 可选，前端传入；缺省时用环境变量
    model: Optional[str] = None
    backend: Optional[str] = None  # 'dashscope' | 'siliconflow'；缺省读 ASR_BACKEND 环境变量


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """主页面。"""
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/health")
async def health_check() -> Dict[str, Any]:
    """健康检查 + 是否已在服务端配置 API Key。"""
    ds = bool(os.getenv("DASHSCOPE_API_KEY", ""))
    sf = bool(os.getenv("SILICONFLOW_API_KEY", ""))
    return {
        "status": "ok",
        "api_key_configured": ds or sf,
        "asr_backend_default": os.getenv("ASR_BACKEND", "dashscope"),
        "dashscope_api_key_configured": ds,
        "siliconflow_api_key_configured": sf,
    }


def _resource_summary(data: Dict[str, Any]) -> str:
    """生成解析结果资源摘要，仅用于日志。"""
    if not isinstance(data, dict):
        return "未知"
    images = data.get("images")
    if isinstance(images, list):
        return f"{len(images)} 张图片"
    if data.get("url"):
        return "1 个视频/媒体直链"
    return "0 个资源"


@app.post("/api/parse")
async def parse_link(req: ParseRequest, request: Request) -> Dict[str, Any]:
    """解析任意平台链接（无需 API Key），返回统一结构化结果。

    返回 resolver 的原始 dict（含 status / platform / type / title /
    caption / url / images 等），前端据此渲染。
    """
    from wanyi_watermark.resolver import resolve_media
    trace_id = request.headers.get("X-Parse-Trace-Id") or uuid.uuid4().hex[:10]
    tokens = set_parse_trace(trace_id)
    flow_start = time.perf_counter()
    parse_log(
        logger,
        "收到前端解析请求：输入长度=%d，内容预览=%s",
        len(req.url or ""),
        short_text(req.url),
        flow_start=flow_start,
    )
    try:
        step = time.perf_counter()
        data = resolve_media(req.url)
        parse_log(
            logger,
            "统一解析返回：status=%s，platform=%s，type=%s，资源=%s",
            data.get("status"),
            data.get("platform"),
            data.get("type"),
            _resource_summary(data),
            step_start=step,
            flow_start=flow_start,
        )
        return data
    except Exception as e:
        parse_log(
            logger,
            "解析接口异常：%s",
            str(e),
            flow_start=flow_start,
            level=logging.ERROR,
        )
        return {"status": "error", "error": str(e)}
    finally:
        parse_log(logger, "/api/parse 请求结束", flow_start=flow_start)
        reset_parse_trace(tokens)


@app.post("/api/extract")
async def extract_text(req: ExtractRequest) -> Dict[str, Any]:
    """提取视频文案（需要对应后端的 API Key）。

    流程：先解析链接得到视频直链 → 按 backend 选择转写后端。
    """
    effective_backend = (req.backend or os.getenv("ASR_BACKEND", "dashscope")).lower()

    if effective_backend == "siliconflow":
        api_key = req.api_key or os.getenv("SILICONFLOW_API_KEY", "")
        if not api_key:
            return {"status": "error", "error": "请先配置 SILICONFLOW_API_KEY（硅基流动）"}
    else:
        api_key = req.api_key or os.getenv("DASHSCOPE_API_KEY", "")
        if not api_key:
            return {"status": "error", "error": "请先配置 DASHSCOPE_API_KEY（阿里云百炼）"}

    from wanyi_watermark.resolver import resolve_media
    from wanyi_watermark.transcription import transcribe_video_url

    data = resolve_media(req.url)
    if data.get("status") == "error":
        return data
    if data.get("type") != "video":
        return {"status": "error", "error": "文案提取仅支持视频类型链接"}

    try:
        text = transcribe_video_url(
            data["url"], api_key=api_key, model=req.model, backend=req.backend
        )
    except Exception as e:
        return {"status": "error", "error": f"文案提取失败: {e}"}

    return {
        "status": "success",
        "platform": data.get("platform"),
        "title": data.get("title", ""),
        "caption": data.get("caption", ""),
        "url": data.get("url", ""),
        "text": text,
    }


# ──────────────────────────────────────────────────────────────────
# 媒体代理 /api/proxy —— 解决跨域 + 防盗链（图片 403、强制下载、视频内嵌播放）
# ──────────────────────────────────────────────────────────────────
# 背景：抖音/小红书等 CDN 对图片/视频直链有 Referer 防盗链校验，浏览器从本站
# 直接加载会 403；且跨域资源无法用前端 download 属性强制保存、视频也可能被拦。
# 方案：由服务端按目标域名补正确的 Referer/UA 去取资源，再以【同源】方式回传给
# 浏览器。这样图片能显示、download 属性可触发真实下载、<video> 可稳定内嵌播放
# （透传 Range 以支持拖动）。
#
# 注：此前 UPSTREAM_SYNC.md 曾把"服务端下载代理"标为延后；因实测图片直接 403
# 无法显示（前提已变），现按产品决策正式落地本端点（与硅基流动等其它延后项无关）。
# ──────────────────────────────────────────────────────────────────
# 透传的响应头（其余一律丢弃，避免泄漏上游 Set-Cookie 等）
_PASS_THROUGH_HEADERS = (
    "Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Cache-Control",
)


def _err(msg: str, status: int) -> PlainTextResponse:
    """统一错误响应：PlainTextResponse 自带 charset=utf-8，避免中文乱码。"""
    return PlainTextResponse(msg, status_code=status)


@app.get("/api/proxy")
def media_proxy(request: Request, url: str, download: int = 0, filename: str = ""):
    """流式代理媒体资源：补 Referer 取源，再同源回传。

    参数:
        url:      原始资源直链（图片/视频）
        download: 1 时附加 Content-Disposition 触发浏览器下载
        filename: 下载时的文件名（可选）
    """
    if not _is_safe_public_url(url):
        return _err("非法或不被允许的资源地址", 400)

    rng = request.headers.get("range")

    try:
        upstream = fetch_media_stream(url, range_header=rng)
    except FetchError as e:
        return _err(str(e), e.status_code or 502)
    except Exception as e:
        return _err(f"上游资源请求失败: {e}", 502)

    resp_headers = {}
    for h in _PASS_THROUGH_HEADERS:
        if h in upstream.headers:
            resp_headers[h] = upstream.headers[h]
    resp_headers.setdefault("Accept-Ranges", "bytes")

    if download:
        safe_name = filename or "download"
        # RFC 5987：用 filename* 携带 UTF-8 文件名，兼容中文
        resp_headers["Content-Disposition"] = (
            "attachment; filename=\"download\"; filename*=UTF-8''" + quote(safe_name)
        )

    media_type = upstream.headers.get("Content-Type", "application/octet-stream")

    def _iter():
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return StreamingResponse(
        _iter(),
        status_code=upstream.status_code,   # 透传 200 / 206
        headers=resp_headers,
        media_type=media_type,
    )


def main():
    """启动服务。"""
    port = int(os.getenv("PORT", "8080"))
    print(f"🚀 启动「百分百一键去水印」WebUI: http://localhost:{port}")
    print(f"📝 DASHSCOPE_API_KEY 配置状态: {'已配置' if os.getenv('DASHSCOPE_API_KEY') else '未配置（仅影响文案提取）'}")
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
