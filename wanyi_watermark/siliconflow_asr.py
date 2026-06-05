"""硅基流动 SenseVoice ASR 后端。

链路：下载视频 → ffmpeg 提取音频(MP3) → 检查时长/大小 → (超限则分段) → 逐段 POST → 合并文本。

大文件策略（参考上游 douyin-mcp-server-origin）：
  - 时长 > 3600s 或 文件 > 50MB 时自动分段
  - 每段 540s（9min，为 API 限制留余量）
  - 分段后逐段转写，拼接全文

环境变量：
  SILICONFLOW_API_KEY — 硅基流动 API 密钥
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import requests

from .media_fetch import FetchError, fetch_media_stream

SILICONFLOW_API_URL = "https://api.siliconflow.cn/v1/audio/transcriptions"
SILICONFLOW_DEFAULT_MODEL = "FunAudioLLM/SenseVoiceSmall"

MAX_DURATION = 3600
MAX_SIZE = 50 * 1024 * 1024
SEGMENT_DURATION = 540


def transcribe_video_url_siliconflow(
    video_url: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    show_progress: bool = False,
) -> str:
    """从视频直链提取语音文案（硅基流动 SenseVoice）。

    完整流程：下载视频 → 提取音频 → (大文件自动分段) → 调用 API 转写 → 合并。

    参数:
        video_url:     已解析出的视频直链
        api_key:       硅基流动 API 密钥；缺省时读取 SILICONFLOW_API_KEY
        model:         ASR 模型，缺省 FunAudioLLM/SenseVoiceSmall
        show_progress: 是否打印进度信息（CLI 场景）

    返回:
        识别出的文本。

    失败时抛出异常。
    """
    api_key = api_key or os.getenv("SILICONFLOW_API_KEY")
    if not api_key:
        raise ValueError(
            "未设置 SILICONFLOW_API_KEY 环境变量，请配置硅基流动 API 密钥后再使用"
        )
    model = model or SILICONFLOW_DEFAULT_MODEL

    tmp_dir = Path(tempfile.mkdtemp(prefix="wanyi_sf_"))
    try:
        video_path = _download_video(video_url, tmp_dir / "video.mp4", show_progress)
        audio_path = _extract_audio(video_path, show_progress)
        text = _transcribe_audio(audio_path, api_key, model, tmp_dir, show_progress)
        return text
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _download_video(url: str, dest: Path, show_progress: bool) -> Path:
    try:
        resp = fetch_media_stream(url, timeout=60, max_retries=2)
    except FetchError as e:
        raise RuntimeError(
            "下载源视频失败，已按平台补充 Referer/UA 并重试；"
            f"请检查资源链接是否过期或源站是否临时拦截：{e}"
        ) from e

    try:
        total = int(resp.headers.get("content-length", 0))
        done = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    done += len(chunk)
                    if show_progress and total > 0:
                        print(f"\r  [siliconflow] 下载视频: {done / total * 100:.1f}%", end="", flush=True)
        if show_progress:
            print(f"\r  [siliconflow] 视频下载完成 ({done / 1024 / 1024:.1f} MB)        ")
    finally:
        resp.close()
    return dest


def _extract_audio(video_path: Path, show_progress: bool) -> Path:
    audio_path = video_path.with_suffix(".mp3")
    if show_progress:
        print("  [siliconflow] 正在提取音频...")
    try:
        _run_ffmpeg([
            "-y",
            "-i", str(video_path),
            "-vn",
            "-acodec", "libmp3lame",
            "-q:a", "0",
            str(audio_path),
        ], "提取音频")
    except Exception as e:
        raise RuntimeError(f"ffmpeg 提取音频失败: {e}") from e
    if show_progress:
        print(f"  [siliconflow] 音频提取完成: {audio_path.stat().st_size / 1024 / 1024:.1f} MB")
    return audio_path


def _get_audio_info(audio_path: Path) -> tuple:
    """返回 (duration_seconds, size_bytes)。"""
    size = audio_path.stat().st_size
    try:
        duration = _probe_duration(audio_path)
    except Exception:
        duration = 0
    return duration, size


def _split_audio(audio_path: Path, tmp_dir: Path, show_progress: bool) -> list:
    """按 SEGMENT_DURATION 切割，返回分段路径列表。"""
    duration, _ = _get_audio_info(audio_path)
    if duration <= SEGMENT_DURATION:
        return [audio_path]

    segments = []
    idx = 0
    current = 0.0
    total_seg = int(duration / SEGMENT_DURATION) + 1
    if show_progress:
        print(f"  [siliconflow] 音频时长 {duration:.0f}s，分为 {total_seg} 段处理...")

    while current < duration:
        seg_path = tmp_dir / f"segment_{idx}.mp3"
        try:
            _run_ffmpeg([
                "-y",
                "-ss", str(current),
                "-t", str(SEGMENT_DURATION),
                "-i", str(audio_path),
                "-acodec", "libmp3lame",
                "-q:a", "0",
                str(seg_path),
            ], f"分割音频段 {idx}")
        except Exception as e:
            raise RuntimeError(f"分割音频段 {idx} 失败: {e}") from e
        segments.append(seg_path)
        current += SEGMENT_DURATION
        idx += 1

    return segments


def _ffmpeg_exe() -> str:
    """返回可执行 ffmpeg 路径，优先系统安装，缺省使用 imageio-ffmpeg 内置二进制。"""
    configured = os.getenv("WANYI_FFMPEG_BINARY")
    if configured:
        path = Path(configured)
        if path.exists():
            return str(path)
        raise RuntimeError(f"WANYI_FFMPEG_BINARY 指向的文件不存在: {configured}")

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    try:
        import imageio_ffmpeg
    except ImportError as e:
        raise RuntimeError(
            "未找到 ffmpeg 可执行文件。请安装 ffmpeg 并加入 PATH，或安装 imageio-ffmpeg 依赖。"
        ) from e
    return imageio_ffmpeg.get_ffmpeg_exe()


def _ffprobe_exe() -> Optional[str]:
    configured = os.getenv("WANYI_FFPROBE_BINARY")
    if configured:
        return configured if Path(configured).exists() else None
    return shutil.which("ffprobe")


def _run_ffmpeg(args: list[str], action: str) -> subprocess.CompletedProcess:
    cmd = [_ffmpeg_exe(), *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if len(stderr) > 1200:
            stderr = stderr[-1200:]
        raise RuntimeError(f"{action}失败: {stderr or 'ffmpeg 返回非零退出码'}")
    return result


def _probe_duration(audio_path: Path) -> float:
    ffprobe = _ffprobe_exe()
    if ffprobe:
        result = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout or "{}")
            return float((data.get("format") or {}).get("duration") or 0)

    # imageio-ffmpeg 只提供 ffmpeg，没有 ffprobe；从 ffmpeg -i 输出里解析时长。
    result = subprocess.run([_ffmpeg_exe(), "-i", str(audio_path)], capture_output=True, text=True)
    text = (result.stderr or "") + "\n" + (result.stdout or "")
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not match:
        return 0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _transcribe_single(audio_path: Path, api_key: str, model: str) -> str:
    """调用硅基流动 API 转写单个音频文件。"""
    with open(audio_path, "rb") as f:
        files = {
            "file": (audio_path.name, f, "audio/mpeg"),
            "model": (None, model),
        }
        headers = {"Authorization": f"Bearer {api_key}"}
        resp = requests.post(SILICONFLOW_API_URL, files=files, headers=headers, timeout=120)

    resp.raise_for_status()
    result = resp.json()
    return result.get("text", resp.text)


def _transcribe_audio(
    audio_path: Path, api_key: str, model: str, tmp_dir: Path, show_progress: bool
) -> str:
    """转写音频：小文件直接调用，大文件自动分段后合并。"""
    duration, size = _get_audio_info(audio_path)
    need_split = duration > MAX_DURATION or size > MAX_SIZE

    if not need_split:
        if show_progress:
            print("  [siliconflow] 正在转写...")
        return _transcribe_single(audio_path, api_key, model)

    if show_progress:
        print(f"  [siliconflow] 音频较大（{duration:.0f}s / {size / 1024 / 1024:.1f}MB），将自动分段")

    segments = _split_audio(audio_path, tmp_dir, show_progress)
    texts = []
    for i, seg in enumerate(segments):
        if show_progress:
            print(f"  [siliconflow] 转写第 {i + 1}/{len(segments)} 段...")
        texts.append(_transcribe_single(seg, api_key, model))

    if show_progress:
        print(f"  [siliconflow] 转写完成，共 {len(segments)} 段")
    return "".join(texts)
