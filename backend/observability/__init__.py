"""
AskData 可观测性基础设施（Observability）。

三层设计，全部本地运行、不依赖外部服务：
1. 结构化日志（loguru）：控制台彩色 + JSON Lines 文件滚动落盘，
   通过 contextvars patcher 自动为每条日志附加 request_id / session_id
2. 指标（Prometheus）：/metrics 端点暴露业务指标
   （请求量、节点耗时、LLM 调用/重试/Token、SQL 尝试结果）
3. 请求级 Trace：每个请求生成一条 trace，记录图节点与 LLM 调用 span，
   结束时汇总落盘到 data/traces/*.jsonl，便于离线分析

典型用法：
    from observability import setup_logging, logger, get_request_id
    from observability.middleware import ObservabilityMiddleware
    from observability.tracer import start_trace, end_trace, GraphCallbackHandler
"""
from observability.logging_setup import setup_logging, logger
from observability.context import get_request_id, get_session_id, get_request_tokens

__all__ = [
    "setup_logging",
    "logger",
    "get_request_id",
    "get_session_id",
    "get_request_tokens",
]
