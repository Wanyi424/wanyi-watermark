"""解析链路诊断日志工具。"""

from __future__ import annotations

import contextvars
import logging
import time
from typing import Tuple


_TRACE_ID = contextvars.ContextVar("parse_trace_id", default="")
_TRACE_START = contextvars.ContextVar("parse_trace_start", default=None)


def set_parse_trace(trace_id: str) -> Tuple[contextvars.Token, contextvars.Token]:
    """设置当前解析请求的追踪 ID 与起始时间。"""
    return (
        _TRACE_ID.set(trace_id),
        _TRACE_START.set(time.perf_counter()),
    )


def reset_parse_trace(tokens: Tuple[contextvars.Token, contextvars.Token]) -> None:
    """恢复解析追踪上下文。"""
    trace_token, start_token = tokens
    _TRACE_ID.reset(trace_token)
    _TRACE_START.reset(start_token)


def elapsed_ms(start: float) -> str:
    """把 perf_counter 起点转换成毫秒字符串。"""
    return f"{(time.perf_counter() - start) * 1000:.0f}ms"


def short_text(value: str, limit: int = 140) -> str:
    """日志里展示短文本，避免整段分享文案刷屏。"""
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def parse_log(
    logger: logging.Logger,
    message: str,
    *args,
    step_start: float | None = None,
    flow_start: float | None = None,
    level: int = logging.INFO,
) -> None:
    """输出带追踪 ID、单步耗时、累计耗时的中文解析日志。"""
    trace_id = _TRACE_ID.get()
    prefix = f"[解析流程 {trace_id}]" if trace_id else "[解析流程]"
    timings = []
    if step_start is not None:
        timings.append(f"本步 {elapsed_ms(step_start)}")
    if flow_start is not None:
        timings.append(f"累计 {elapsed_ms(flow_start)}")
    else:
        started = _TRACE_START.get()
        if started is not None:
            timings.append(f"累计 {elapsed_ms(started)}")
    suffix = f"（{'，'.join(timings)}）" if timings else ""
    logger.log(level, f"{prefix} {message}{suffix}", *args)
