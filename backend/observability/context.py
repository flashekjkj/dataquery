"""
请求级上下文（contextvars）。

contextvars 保证并发安全：每个请求（HTTP 请求线程 / asyncio task / 线程池任务）
拥有独立副本，互不污染。FastAPI 的 sync 端点跑在线程池中，Starlette 的
run_in_threadpool 会复制 context，因此路由内部与中间件看到的值一致。
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

# 请求 ID：优先取客户端传入的 X-Request-ID，否则中间件生成
request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
# 会话 ID：/chat 请求的 session_id，用于日志与 trace 关联
session_id_var: ContextVar[Optional[str]] = ContextVar("session_id", default=None)
# 请求级 Token 用量累计：ModelClient 每次调用后累加，请求结束时写访问日志
request_tokens_var: ContextVar[dict] = ContextVar("request_tokens", default={"input": 0, "output": 0})


def get_request_id(default: str = "-") -> str:
    return request_id_var.get() or default


def get_session_id(default: str = "-") -> str:
    return session_id_var.get() or default


def get_request_tokens() -> dict:
    return request_tokens_var.get()


def accumulate_tokens(input_tokens: int, output_tokens: int) -> None:
    """LLM 调用后累加本请求的 Token 用量（dict 原地修改，context 副本间共享同一对象）"""
    tokens = request_tokens_var.get()
    tokens["input"] += int(input_tokens or 0)
    tokens["output"] += int(output_tokens or 0)
