"""
请求级 Trace：记录一次 Agent 执行中的全部 span（图节点 / LLM 调用 / 工具调用）。

设计取舍（面向面试可讲）：
- 不引入 Langfuse/LangSmith 等外部服务：本地 JSONL 落盘，离线可复现，
  trace 结构保持标准（trace_id + spans + metadata），后续可无缝对接远端后端
- Trace 对象挂在 contextvars 上，跨线程池/协程边界安全；
  end_trace 幂等（finish 标记），避免中间件与路由重复 flush
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.callbacks import BaseCallbackHandler

from observability import logger
from observability.context import get_request_id, get_session_id
from observability.metrics import record_node_duration

# 当前请求的 Trace（中间件 start_trace 写入，路由/节点通过 current_trace 读取）
trace_var: ContextVar[Optional["Trace"]] = ContextVar("trace", default=None)

# 落盘目录：data/traces/traces-YYYYMMDD.jsonl，每行一个 span
_TRACE_ROOT: Optional[Path] = None
# 落盘锁：多请求并发写同一文件
_WRITE_LOCK = threading.Lock()


def configure_trace_dir(root: Optional[Path]) -> None:
    """设置 trace 落盘目录（服务启动时调用一次；传 None 表示关闭落盘）"""
    global _TRACE_ROOT
    _TRACE_ROOT = root
    if root is not None:
        root.mkdir(parents=True, exist_ok=True)


@dataclass
class Span:
    """单次执行单元：图节点或一次 LLM 调用"""
    name: str                      # 节点名或 "llm:<operation>"
    kind: str                      # "node" / "llm" / "tool"
    start_ms: float                # perf_counter 毫秒（同进程内可比较）
    end_ms: Optional[float] = None
    duration_ms: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class Trace:
    """一次 Agent 请求的完整记录"""

    def __init__(self, session_id: Optional[str] = None):
        self.trace_id = uuid.uuid4().hex
        self.request_id = get_request_id() or self.trace_id
        self.session_id = session_id or get_session_id()
        self.started_at = datetime.now().isoformat(timespec="milliseconds")
        self._spans: List[Span] = []
        self._lock = threading.Lock()   # 节点回调可能跨线程触发
        self.finished = False

    def add_span(self, name: str, kind: str, start_ms: float,
                 end_ms: float, metadata: Optional[Dict[str, Any]] = None) -> Span:
        # start_ms/end_ms 已统一为毫秒（相对 perf_counter 起点），直接相减
        span = Span(
            name=name, kind=kind, start_ms=start_ms,
            end_ms=end_ms, duration_ms=round(end_ms - start_ms, 2),
            metadata=metadata or {},
        )
        with self._lock:
            self._spans.append(span)
        return span

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            spans = [
                {
                    "name": s.name, "kind": s.kind,
                    "duration_ms": s.duration_ms, "metadata": s.metadata,
                }
                for s in sorted(self._spans, key=lambda x: x.start_ms)
            ]
        return {
            "trace_id": self.trace_id,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "started_at": self.started_at,
            "spans": spans,
        }


def start_trace(session_id: Optional[str] = None) -> Trace:
    """创建并挂载当前请求的 Trace（幂等：已存在则复用）"""
    trace = trace_var.get()
    if trace is None:
        trace = Trace(session_id=session_id)
        trace_var.set(trace)
    return trace


def current_trace() -> Optional[Trace]:
    return trace_var.get()


def end_trace(flush: bool = True) -> Optional[Dict[str, Any]]:
    """
    结束当前请求的 Trace：从 contextvar 摘除、置 finished 标记、可选落盘。
    幂等：同一 Trace 只会 flush 一次（中间件与路由可能各调一次，且跨线程池
    context 副本共享同一 Trace 对象）。
    """
    trace = trace_var.get()
    if trace is None:
        return None
    trace_var.set(None)
    snapshot = trace.snapshot() if flush else None
    if not trace.finished and flush:
        trace.finished = True
        _flush_to_disk(snapshot)
    else:
        trace.finished = True
    return snapshot


def _flush_to_disk(snapshot: Dict[str, Any]) -> None:
    """把 span 列表追加写盘 + 打一条汇总日志（trace 摘要）"""
    spans = snapshot["spans"]
    total_ms = sum(s["duration_ms"] or 0 for s in spans)
    logger.info(
        "trace结束 | {} 个 span，累计耗时 {:.0f}ms | trace_id={}",
        len(spans), total_ms, snapshot["trace_id"],
    )
    if _TRACE_ROOT is None:
        return
    file_name = f"traces-{datetime.now():%Y%m%d}.jsonl"
    try:
        with _WRITE_LOCK:
            _TRACE_ROOT.mkdir(parents=True, exist_ok=True)   # 目录被外部清理时自愈
            with (_TRACE_ROOT / file_name).open("a", encoding="utf-8") as f:
                for span in spans:
                    line = {
                        "trace_id": snapshot["trace_id"],
                        "request_id": snapshot["request_id"],
                        "session_id": snapshot["session_id"],
                        "started_at": snapshot["started_at"],
                        **span,
                    }
                    f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("trace 落盘失败：{}", e)


def record_span(name: str, kind: str, duration_ms: float,
                metadata: Optional[Dict[str, Any]] = None) -> None:
    """
    非回调场景（LLM 调用等）的 span 记录入口。
    当前没有 Trace（如模型预热阶段）时静默跳过。
    """
    trace = current_trace()
    if trace is None:
        return
    now_ms = time.perf_counter() * 1000
    trace.add_span(name, kind, now_ms - duration_ms, now_ms, metadata)


class GraphCallbackHandler(BaseCallbackHandler):
    """
    LangGraph 节点级回调：on_chain_start/end 记录每个节点的耗时 span，
    同时喂给 Prometheus 的节点耗时直方图。

    接入方式：invoke/astream 的 config 中传 {"callbacks": [handler]}。
    注意按请求创建实例（构造时捕获当前 Trace）。
    """

    def __init__(self):
        self._trace = current_trace()
        # run_id -> (name, start_seconds)。on_chain_end 回调不携带节点名，
        # 必须在 on_chain_start 时先记录名字
        self._span_starts: Dict[str, tuple] = {}

    @staticmethod
    def _extract_name(serialized) -> str:
        """从回调的 serialized 参数提取节点名，兼容 dict 与 str 两种形态"""
        if isinstance(serialized, dict):
            return serialized.get("name") or serialized.get("id") or "unknown"
        if isinstance(serialized, str):
            return serialized
        return "unknown"

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None,
                       tags=None, metadata=None, **kwargs) -> None:
        name = self._extract_name(serialized)
        # LangGraph 内部通道写入事件（ChannelWrite<...>）不产生业务价值，跳过
        if "ChannelWrite" in name or name.startswith("__"):
            return
        self._span_starts[run_id] = (name, time.perf_counter())

    def on_chain_end(self, outputs, *, run_id, **kwargs) -> None:
        self._close_span(run_id, error=None)

    def on_chain_error(self, error, *, run_id, **kwargs) -> None:
        self._close_span(run_id, error=error)

    def _close_span(self, run_id: str, error: Optional[BaseException]) -> None:
        record = self._span_starts.pop(run_id, None)
        if record is None:
            return
        name, start = record
        end = time.perf_counter()
        metadata = {}
        if error is not None:
            metadata["error"] = type(error).__name__
        if self._trace is not None:
            self._trace.add_span(name, "node", start * 1000, end * 1000, metadata)
        # 指标只统计业务节点，过滤图本体等噪声
        if name.endswith("_node"):
            record_node_duration(name, (end - start) * 1000)
