"""共享视频文案转写服务（阿里云百炼 dashscope）。

供 CLI / WebUI / MCP 工具复用同一套转写实现。
当前后端：阿里云百炼 paraformer-v2，采用 URL 直传（无需本地下载/ffmpeg），
百炼服务端异步转写，对长音频有较好的原生支持。

────────────────────────────────────────────────────────────────────
TODO(upstream-backport, siliconflow-asr / large-file-split)：可选 ASR 后端与大文件分段
────────────────────────────────────────────────────────────────────
上游 douyin-mcp-server 在其 CLI/Web 链路改用「硅基流动 SenseVoice」并实现了
"客户端大文件自动分段转写"。本阶段按产品决策【暂不接入】，仅保留百炼直传。
后续若要纳入，请参考：
    • 硅基流动转写 + 大文件分段：
      douyin-mcp-server-origin/douyin-video/scripts/douyin_downloader.py
          transcribe_single_audio()      （单段转写）
          split_audio()                  （ffmpeg 按 9min/段分割）
          extract_text_from_audio()      （>1h 或 >50MB 自动分段）
    • 建议设计：用环境变量在 dashscope / siliconflow 间切换，
      paraformer 直传路径无需分段（先验证百炼长音频上限再决定是否实现分段）。
详见 UPSTREAM_SYNC.md「待回迁 backlog」。
"""

import os
from typing import Optional


def transcribe_video_url(
    video_url: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """从视频直链中提取语音文案（阿里云百炼）。

    参数:
        video_url: 已解析出的视频直链（抖音/小红书/通用皆可，与平台无关）
        api_key:   百炼 API 密钥；缺省时读取环境变量 DASHSCOPE_API_KEY
        model:     语音识别模型，缺省 paraformer-v2

    返回:
        识别出的文本内容。

    失败时抛出异常，由调用方按需处理。
    """
    api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise ValueError(
            "未设置环境变量 DASHSCOPE_API_KEY，请配置阿里云百炼 API 密钥后再使用文案提取功能"
        )

    # 延迟导入：复用 DouyinProcessor 已实现并经过验证的 dashscope 转写逻辑，
    # 同时避免在仅做解析时就加载 dashscope。
    from .douyin_processor import DouyinProcessor

    processor = DouyinProcessor(api_key, model)
    return processor.extract_text_from_video_url(video_url)
