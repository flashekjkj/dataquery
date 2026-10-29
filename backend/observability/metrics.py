"""
Prometheus 业务指标定义与记录入口。

指标命名规范：askdata_<领域>_<指标>_<单位/类型>，标签维度保持克制
（高基数标签如 trace_id 只进 trace 落盘，不进指标）。

关键业务指标（面试可直接展示）：
- askdata_llm_calls_total / askdata_llm_retries_total：
  失败治理的直接效果数据（重试多少次、挽救多少次）
- askdata_llm_tokens_total{operation}：每个 Agent 步骤的成本拆解
- askdata_node_duration_seconds{node}：图节点 P50/P95 延迟
- askdata_sql_executions_total{status}：Text2SQL 一次成功率
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram

METRICS_PREFIX = "askdata"

chat_outcomes_total = Counter("askdata_chat_outcomes_total", "业务结果（与HTTP完成分开）", ["outcome"])


def record_chat_outcome(outcome):
    chat_outcomes_total.labels(outcome=outcome).inc()

# ===== HTTP 层 =====
http_requests_total = Counter(
    f"{METRICS_PREFIX}_http_requests_total",
    "HTTP 请求总数", ["method", "path", "status"],
)
http_request_duration_seconds = Histogram(
    f"{METRICS_PREFIX}_http_request_duration_seconds",
    "HTTP 请求耗时（秒）", ["method", "path"],
)

# ===== Agent 层 =====
chat_requests_total = Counter(
    f"{METRICS_PREFIX}_chat_requests_total",
    "Agent 问答请求结果", ["status"],   # success / failed
)
node_duration_ms = Histogram(
    f"{METRICS_PREFIX}_node_duration_ms",
    "LangGraph 节点耗时（毫秒）", ["node"],
)
sql_executions_total = Counter(
    f"{METRICS_PREFIX}_sql_executions_total",
    "SQL 执行尝试结果", ["status"],     # success / error
)

# ===== 检索降级 =====
rerank_degraded_total = Counter(
    f"{METRICS_PREFIX}_rerank_degraded_total",
    "本地 rerank 失败降级为原序返回的次数",
)

# ===== LLM 调用层 =====
llm_calls_total = Counter(
    f"{METRICS_PREFIX}_llm_calls_total",
    "LLM 调用结果", ["operation", "model", "status"],   # status: success / error
)
llm_retries_total = Counter(
    f"{METRICS_PREFIX}_llm_retries_total",
    "LLM 调用重试次数", ["operation"],
)
llm_tokens_total = Counter(
    f"{METRICS_PREFIX}_llm_tokens_total",
    "LLM Token 消耗", ["operation", "direction"],       # direction: input / output
)
llm_call_duration_ms = Histogram(
    f"{METRICS_PREFIX}_llm_call_duration_ms",
    "LLM 单次调用耗时（毫秒）", ["operation", "model"],
)


# ===== 记录函数（业务代码只调用这些入口，不直接摸指标对象） =====

def record_http_request(method: str, path: str, status: int, duration_s: float) -> None:
    http_requests_total.labels(method=method, path=path, status=str(status)).inc()
    http_request_duration_seconds.labels(method=method, path=path).observe(duration_s)


def record_chat_request(ok: bool) -> None:
    chat_requests_total.labels(status="success" if ok else "failed").inc()


def record_node_duration(node: str, duration_ms: float) -> None:
    node_duration_ms.labels(node=node).observe(duration_ms)


def record_sql_execution(ok: bool) -> None:
    sql_executions_total.labels(status="success" if ok else "error").inc()


def record_llm_call(operation: str, model: str, ok: bool, duration_ms: float,
                    input_tokens: int = 0, output_tokens: int = 0,
                    retries: int = 0, error_kind: str = "") -> None:
    """LLM 调用统一记录：结果计数 + 耗时 + Token + 重试次数"""
    status = "success" if ok else "error"
    llm_calls_total.labels(operation=operation, model=model, status=status).inc()
    llm_call_duration_ms.labels(operation=operation, model=model).observe(duration_ms)
    if retries:
        llm_retries_total.labels(operation=operation).inc(retries)
    if input_tokens or output_tokens:
        llm_tokens_total.labels(operation=operation, direction="input").inc(input_tokens)
        llm_tokens_total.labels(operation=operation, direction="output").inc(output_tokens)


# ===== 冷启动预置 =====
# 带标签的指标只有在出现首个样本后才会被 /metrics 导出。
# 预置常用标签组合（inc(0) 不改变数值），保证无流量时监控看板也能渲染完整指标清单。
COMMON_NODES = [
    "load_memory_node", "rewrite_node", "skill_node", "route_node", "chat_node",
    "retrieve_node", "generate_sql_node", "execute_node", "repair_node",
    "answer_node", "save_memory_node", "tool_plan_node", "tool_execute_node",
    "supervisor_node", "query_agent_node", "review_agent_node",
    "collect_task_node", "revise_task_node", "supervisor_answer_node",
]
COMMON_OPERATIONS = [
    "route", "rewrite", "chat", "answer", "tool_answer",
    "generate_sql", "repair_sql", "memory_summary", "memory_extract",
    "supervisor_plan", "supervisor_summary", "review",
    "chat_with_tools", "embedding", "stream", "invoke",
]


def init_metrics(model_id: str = "mock") -> None:
    """
    服务启动时调用：用 inc(0) 预置 Counter 的零值样本（幂等，可重复调用）。
    Histogram 不做预置——observe(0) 会污染分位数统计，等首个真实样本自然出现。
    """
    for op in COMMON_OPERATIONS:
        llm_retries_total.labels(operation=op).inc(0)
        for status in ("success", "error"):
            llm_calls_total.labels(operation=op, model=model_id, status=status).inc(0)
        for direction in ("input", "output"):
            llm_tokens_total.labels(operation=op, direction=direction).inc(0)
    for status in ("success", "failed"):
        chat_requests_total.labels(status=status).inc(0)
    for status in ("success", "error"):
        sql_executions_total.labels(status=status).inc(0)
    http_requests_total.labels(method="POST", path="/api/chat", status="200").inc(0)


def render_latest() -> bytes:
    """导出当前 Prometheus 文本格式（/metrics 端点用）"""
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    return generate_latest()


def metrics_content_type() -> str:
    from prometheus_client import CONTENT_TYPE_LATEST
    return CONTENT_TYPE_LATEST
