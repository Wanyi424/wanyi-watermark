"""共享视频文案转写服务 — 双后端调度门面。

支持两种 ASR 后端：
  1. dashscope（默认）—— 阿里云百炼 paraformer-v2，URL 直传，无需本地下载/ffmpeg
  2. siliconflow —— 硅基流动 SenseVoice，需下载视频+ffmpeg 提取音频，大文件自动分段

后端选择优先级：显式参数 > 环境变量 ASR_BACKEND > 默认 'dashscope'。

供 CLI / WebUI / MCP 工具复用。
"""

import os
from typing import Optional


def transcribe_video_url(
    video_url: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    backend: Optional[str] = None,
    show_progress: bool = False,
) -> str:
    """从视频直链中提取语音文案。

    参数:
        video_url:     已解析出的视频直链（平台无关）
        api_key:       API 密钥；缺省时按后端从对应环境变量读取
        model:         ASR 模型（缺省各后端有默认值）
        backend:       'dashscope' | 'siliconflow'；缺省读 ASR_BACKEND 环境变量，再缺省 dashscope
        show_progress: 是否打印进度（CLI 场景有用，MCP/WebUI 一般关闭）

    返回:
        识别出的文本内容。

    失败时抛出异常，由调用方按需处理。
    """
    backend = (backend or os.getenv("ASR_BACKEND", "dashscope")).lower().strip()

    if backend == "siliconflow":
        return _transcribe_siliconflow(video_url, api_key, model, show_progress)
    elif backend == "dashscope":
        return _transcribe_dashscope(video_url, api_key, model)
    else:
        raise ValueError(
            f"不支持的 ASR 后端: '{backend}'，可选值: dashscope, siliconflow"
        )


def _transcribe_dashscope(
    video_url: str, api_key: Optional[str], model: Optional[str]
) -> str:
    """百炼 URL 直传转写（保持原有逻辑不变）。"""
    api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise ValueError(
            "未设置环境变量 DASHSCOPE_API_KEY，请配置阿里云百炼 API 密钥后再使用文案提取功能"
        )
    from .douyin_processor import DouyinProcessor

    processor = DouyinProcessor(api_key, model)
    return processor.extract_text_from_video_url(video_url)


def _transcribe_siliconflow(
    video_url: str, api_key: Optional[str], model: Optional[str], show_progress: bool
) -> str:
    """硅基流动 SenseVoice 转写（下载+ffmpeg+分段）。"""
    from .siliconflow_asr import transcribe_video_url_siliconflow

    return transcribe_video_url_siliconflow(
        video_url, api_key=api_key, model=model, show_progress=show_progress
    )
