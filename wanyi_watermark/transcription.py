"""共享视频文案转写服务。

支持两种后端（通过 backend 参数选择）：

1. **dashscope**（默认）：阿里云百炼 paraformer-v2，URL 直传，无需本地下载/ffmpeg。
   百炼服务端异步转写，对长音频有较好的原生支持。
   环境变量：DASHSCOPE_API_KEY

2. **siliconflow**：硅基流动 SenseVoice，需要本地 ffmpeg。
   流程：下载视频 → 提取音频 → 大文件自动分段（>1h 或 >50MB 按 9min/段切割）→ 逐段上传转写。
   环境变量：SILICONFLOW_API_KEY
"""

import os
import logging
import tempfile
import shutil
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# SiliconFlow 默认配置
_SF_API_URL = "https://api.siliconflow.cn/v1/audio/transcriptions"
_SF_DEFAULT_MODEL = "FunAudioLLM/SenseVoiceSmall"

# 分段阈值
_SPLIT_MAX_DURATION = 3600  # 1 小时
_SPLIT_MAX_SIZE = 50 * 1024 * 1024  # 50 MB
_SEGMENT_DURATION = 540  # 9 分钟（留 1 分钟余量）

# UA 常量（与 web/app.py _site_headers 保持一致）
_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _site_headers(url: str) -> dict:
    """按视频 URL 域名选择 UA 与 Referer（防盗链）。"""
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    host = host.lower()
    headers = {"Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9"}
    if any(k in host for k in ("douyin", "iesdouyin", "amemv", "bytecdn", "douyinpic", "douyinvod", "ixigua")):
        headers["User-Agent"] = _MOBILE_UA
        headers["Referer"] = "https://www.douyin.com/"
    elif any(k in host for k in ("xhscdn", "xiaohongshu")):
        headers["User-Agent"] = _DESKTOP_UA
        headers["Referer"] = "https://www.xiaohongshu.com/"
    else:
        headers["User-Agent"] = _DESKTOP_UA
    return headers


def transcribe_video_url(
    video_url: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    backend: str = "dashscope",
) -> str:
    """从视频直链中提取语音文案。

    参数:
        video_url: 已解析出的视频直链
        api_key:   API 密钥；缺省时根据 backend 读取对应环境变量
        model:     语音识别模型（缺省值取决于 backend）
        backend:   转写后端，"dashscope"（默认）或 "siliconflow"

    返回:
        识别出的文本内容。
    """
    if backend == "siliconflow":
        return _transcribe_siliconflow(video_url, api_key, model)
    return _transcribe_dashscope(video_url, api_key, model)


# ─────────────────────────────────────────────────────────────────────
# DashScope 后端（URL 直传，无需本地下载）
# ─────────────────────────────────────────────────────────────────────


def _transcribe_dashscope(
    video_url: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise ValueError(
            "未设置环境变量 DASHSCOPE_API_KEY，请配置阿里云百炼 API 密钥后再使用文案提取功能"
        )

    from .douyin_processor import DouyinProcessor

    processor = DouyinProcessor(api_key, model)
    return processor.extract_text_from_video_url(video_url)


# ─────────────────────────────────────────────────────────────────────
# SiliconFlow 后端（本地下载 + ffmpeg 抽音频 + 大文件分段）
# ─────────────────────────────────────────────────────────────────────


def _transcribe_siliconflow(
    video_url: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    api_key = api_key or os.getenv("SILICONFLOW_API_KEY")
    if not api_key:
        raise ValueError(
            "未设置环境变量 SILICONFLOW_API_KEY，请配置硅基流动 API 密钥后再使用 SiliconFlow 转写"
        )
    model = model or _SF_DEFAULT_MODEL

    tmp_dir = Path(tempfile.mkdtemp(prefix="wanyi_sf_"))
    try:
        video_path = _download_video(video_url, tmp_dir)
        audio_path = _extract_audio(video_path, tmp_dir)
        text = _extract_text_from_audio(audio_path, api_key, model, tmp_dir)
        return text
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _download_video(video_url: str, tmp_dir: Path) -> Path:
    """下载视频到临时目录（按域名补 Referer/UA 防 403）。"""
    video_path = tmp_dir / "video.mp4"
    headers = _site_headers(video_url)
    resp = requests.get(video_url, headers=headers, stream=True, timeout=60, allow_redirects=True)
    resp.raise_for_status()

    with open(video_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    logger.debug(f"视频已下载: {video_path} ({video_path.stat().st_size} bytes)")
    return video_path


def _extract_audio(video_path: Path, tmp_dir: Path) -> Path:
    """使用 ffmpeg 从视频中提取音频（MP3）。"""
    import ffmpeg

    audio_path = tmp_dir / "audio.mp3"
    (
        ffmpeg
        .input(str(video_path))
        .output(str(audio_path), acodec="libmp3lame", q=0)
        .run(capture_stdout=True, capture_stderr=True, overwrite_output=True)
    )
    logger.debug(f"音频已提取: {audio_path}")
    return audio_path


def _get_audio_info(audio_path: Path) -> dict:
    """获取音频时长和文件大小。"""
    import ffmpeg

    try:
        probe = ffmpeg.probe(str(audio_path))
        duration = float(probe["format"].get("duration", 0))
    except Exception:
        duration = 0
    return {"duration": duration, "size": audio_path.stat().st_size}


def _split_audio(audio_path: Path, tmp_dir: Path, segment_duration: int = _SEGMENT_DURATION) -> list:
    """将音频按固定时长切割为多段 MP3。"""
    import ffmpeg

    info = _get_audio_info(audio_path)
    duration = info["duration"]

    if duration <= segment_duration:
        return [audio_path]

    segments = []
    idx = 0
    current = 0.0

    while current < duration:
        seg_path = tmp_dir / f"segment_{idx}.mp3"
        (
            ffmpeg
            .input(str(audio_path), ss=current, t=segment_duration)
            .output(str(seg_path), acodec="libmp3lame", q=0)
            .run(capture_stdout=True, capture_stderr=True, overwrite_output=True)
        )
        segments.append(seg_path)
        current += segment_duration
        idx += 1

    logger.debug(f"音频已分割为 {len(segments)} 段")
    return segments


def _transcribe_single_audio(audio_path: Path, api_key: str, model: str) -> str:
    """调用 SiliconFlow API 转写单个音频文件。"""
    headers = {"Authorization": f"Bearer {api_key}"}
    with open(audio_path, "rb") as f:
        files = {
            "file": (audio_path.name, f, "audio/mpeg"),
            "model": (None, model),
        }
        response = requests.post(_SF_API_URL, files=files, headers=headers, timeout=120)

    response.raise_for_status()
    result = response.json()
    if "text" in result:
        return result["text"]
    raise ValueError(f"SiliconFlow API 返回格式异常: {response.text[:200]}")


def _extract_text_from_audio(audio_path: Path, api_key: str, model: str, tmp_dir: Path) -> str:
    """编排：判断是否需要分段，逐段转写并拼接。"""
    info = _get_audio_info(audio_path)
    need_split = info["duration"] > _SPLIT_MAX_DURATION or info["size"] > _SPLIT_MAX_SIZE

    if not need_split:
        return _transcribe_single_audio(audio_path, api_key, model)

    segments = _split_audio(audio_path, tmp_dir)
    texts = []
    for seg in segments:
        text = _transcribe_single_audio(seg, api_key, model)
        texts.append(text)
        if seg != audio_path:
            seg.unlink(missing_ok=True)

    return "".join(texts)
