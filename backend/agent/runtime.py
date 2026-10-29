from __future__ import annotations
from pathlib import Path
import sys
from typing import Optional

from agent.graph import build_graph
from agent.outcomes import classify_outcome
from observability.context import request_tokens_var
from app.config import settings
from app.model_client import ModelClient
from memory.conversation import ConversationMemory
from memory.long_term import LongTermMemoryStore
from observability import setup_logging, logger
from observability.metrics import init_metrics
from observability.tracer import (
    configure_trace_dir, start_trace, end_trace, current_trace, GraphCallbackHandler,
)
from retrieval.service import build_hybrid_index
from sql.executor import SQLExecutor
from skill.registry import SkillRegistry
from mcp_client.manager import MCPToolManager

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "demo_ecom.duckdb"
DATA_DIR = BASE_DIR / "data"


def build_agent(max_attempts: int = 3):
    """
    构建Agent全链路：模型客户端 + 混合检索 + SQL执行器 + 两种记忆 + 技能注册表 + LangGraph图
    返回 (graph, conv_memory,skill_registry)：图用于问答，conv_memory用于CLI展示会话历史
    """
    # 日志与 trace 初始化（幂等；CLI 与 FastAPI 两条入口都经过这里）
    setup_logging(level=settings.OBS_LOG_LEVEL)
    trace_dir = Path(settings.OBS_TRACE_DIR)
    if not trace_dir.is_absolute():
        trace_dir = BASE_DIR / trace_dir
    if settings.OBS_TRACE_ENABLED:
        configure_trace_dir(trace_dir)
    init_metrics(model_id=settings.LLM_MODEL_ID)

    model_client = ModelClient(mock=False)
    logger.info("正在构建混合检索索引（对所有字段文档生成embedding）...")
    hybrid = build_hybrid_index(model_client)
    executor = SQLExecutor(str(DB_PATH))
    conv_memory = ConversationMemory(data_root=DATA_DIR)
    long_term_memory = LongTermMemoryStore(data_root=DATA_DIR)
    skill_registry = SkillRegistry(DATA_DIR / "skills")
    # MCP工具管理器：接入calculator演示server（第四步技能联动时按skill声明过滤工具）
    mcp_manager = MCPToolManager(
        servers=[
            {
                "name": "calculator",
                "command": sys.executable,
                "args": [str(BASE_DIR / "mcp_client" / "demo_servers" / "calculator_server.py")],
            }
        ]
    )
    graph = build_graph(
        model_client, hybrid, executor,
        conv_memory, long_term_memory,
        max_attempts=max_attempts,
        skill_registry=skill_registry,
        mcp_manager=mcp_manager
    )
    logger.info("Agent构建完成")
    return graph, conv_memory, skill_registry


def initial_agent_state(question, forced_skill=None, execution_mode="auto"):
    if execution_mode not in ("auto", "single", "multi"):
        raise ValueError("execution_mode 必须是 auto/single/multi")
    return {"user_question": question, "attempts": 0, "attempts_log": [],
            "forced_skill": forced_skill, "execution_mode": execution_mode}


def ask_data_agent(graph, user_question: str, thread_id: str,forced_skill:Optional[str] = None,
                   execution_mode: str = "auto") -> dict:
    """
    LangGraph版Agent入口：一次invoke运行整张图
    - thread_id必传：由会话层管理，同一会话内多轮共用，实现多轮记忆
    - forced_skill：强制指定技能名（/skill命令），None=自动匹配
    - 初始state必须带上attempts和attempts_log（attempts参与加法运算，
      attempts_log每次显式传[]覆盖重置，防止跨轮累积）
    - 可观测性：无 trace 时自行创建（CLI 场景），invoke 挂节点回调；
      由本函数创建的 trace 在返回前 flush（HTTP 场景的 trace 归中间件/路由管理）
    """
    owned_trace = current_trace() is None
    usage_token = request_tokens_var.set({"input": 0, "output": 0}) if owned_trace else None
    trace = start_trace(session_id=thread_id)
    handler = GraphCallbackHandler()
    logger.info("Agent调用开始（thread_id={} trace_id={}）", thread_id, trace.trace_id)
    try:
        final_state = graph.invoke(initial_agent_state(user_question, forced_skill, execution_mode),
            config={"configurable": {"thread_id": thread_id}, "recursion_limit": 64,
                    "callbacks": [handler]},
        )
    finally:
        if owned_trace:
            end_trace()
            request_tokens_var.reset(usage_token)

    route = final_state.get("route") or "data"
    field_docs = final_state.get("field_docs") or []
    attempts_log = final_state.get("attempts_log") or []

    result = {
        "success": classify_outcome(final_state) == "success",
        "outcome": classify_outcome(final_state),
        "route": route,
        "user_question": user_question,
        "rewritten_question": final_state.get("rewritten_question"),
        "retrieved_docs": [doc.doc_id for doc in field_docs],
        "final_sql": final_state.get("sql"),
        "query_result": final_state.get("query_result"),
        "answer": final_state.get("answer") or "",
        "attempts": [{"sql": s, "error": e} for s, e in attempts_log],
        "active_skill":final_state.get("active_skill"),
        "tool_calls": [c["name"] for c in (final_state.get("tool_calls") or [])],
        "tool_result": final_state.get("tool_result"),
        "collaboration_status": final_state.get("collaboration_status", "single"),
        "task_plan": final_state.get("task_plan"),
        "task_results": final_state.get("task_results") or [],
        "trace_id": trace.trace_id,
    }
    return result
