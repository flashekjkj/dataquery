from __future__ import annotations
import hashlib
import sys
import tempfile
from pathlib import Path

BASE_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_BACKEND))

from app.schemas import LLMResponse, EmbeddingResponse, RerankItem, RerankResponse
from sql.sql_prompts import (
    GENERATE_SQL_SYSTEM_PROMPT,
    REPAIR_SQL_SYSTEM_PROMPT,
    ROUTE_SYSTEM_PROMPT,
)
from agent.graph import build_graph
from memory.conversation import ConversationMemory
from memory.long_term import LongTermMemoryStore
from retrieval.service import build_hybrid_index
from sql.executor import SQLExecutor

DB_PATH = BASE_BACKEND / "data" / "demo_ecom.duckdb"


class FakeClient:
    """
    可编程假LLM客户端（不依赖真实API）：
    - 路由：返回预设的 data/chat
    - SQL生成：按预设序列依次返回SQL（超出序列取最后一个），用来模拟"先错后对"
    - embedding：确定性哈希向量，保证检索在离线模式下可复现
    """
    def __init__(self, sql_sequence: list, route_answer: str = "data"):
        self.sql_sequence = list(sql_sequence)
        self.route_answer = route_answer
        self.generate_calls = 0

    def llm_invoke(self, prompt, system_prompt=None):
        if system_prompt == ROUTE_SYSTEM_PROMPT:
            return LLMResponse(content=self.route_answer, usage={})
        if system_prompt in (GENERATE_SQL_SYSTEM_PROMPT, REPAIR_SQL_SYSTEM_PROMPT):
            idx = min(self.generate_calls, len(self.sql_sequence) - 1)
            self.generate_calls += 1
            return LLMResponse(content=f"```sql\n{self.sql_sequence[idx]}\n```", usage={})
        # 回答类调用
        return LLMResponse(content="【测试回答】", usage={})

    def embedding(self, text):
        vec = [0.0] * 16
        for ch in text:
            h = int(hashlib.md5(ch.encode("utf-8")).hexdigest(), 16)
            vec[h % 16] += 1.0
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        return EmbeddingResponse(vector=[v / norm for v in vec])

    def rerank(self, query, documents):
        items = [
            RerankItem(index=i, text=doc, score=1.0 - 0.01 * i)
            for i, doc in enumerate(documents)
        ]
        return RerankResponse(results=items)


def _build_graph(client, max_attempts: int = 3):
    hybrid = build_hybrid_index(client)
    executor = SQLExecutor(str(DB_PATH))
    # 记忆存储在临时目录，测试互不干扰、不污染项目数据
    conv_memory = ConversationMemory(data_root=Path(tempfile.mkdtemp()))
    long_term_memory = LongTermMemoryStore(data_root=Path(tempfile.mkdtemp()))
    return build_graph(
        client, hybrid, executor,
        conv_memory, long_term_memory,
        max_attempts=max_attempts
    )


def _invoke(graph, question: str) -> dict:
    return graph.invoke(
        {"user_question": question, "attempts": 0, "attempts_log": []},
        config={"configurable": {"thread_id": "test-thread"}},
    )


def test_graph_structure():
    """图结构：7个节点齐全，repair→generate成环"""
    graph = _build_graph(FakeClient(["SELECT 1"]))
    node_names = set(graph.get_graph().nodes.keys()) - {"__start__", "__end__"}
    expected = {
        "load_memory_node", "rewrite_node", "route_node", "chat_node",
        "retrieve_node", "generate_sql_node", "execute_node", "repair_node",
        "answer_node", "save_memory_node",
    }
    assert node_names == expected, f"节点集合不符：{node_names}"
    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    assert ("repair_node", "generate_sql_node") in edges, "缺少自修复环边"
    print("✅ test_graph_structure 通过！节点与环边正确")


def test_first_try_success():
    """第一次生成的SQL就成功：1条日志，直接到answer"""
    graph = _build_graph(FakeClient(["SELECT customer_id FROM customers LIMIT 1"]))
    state = _invoke(graph, "有多少客户")
    assert state["route"] == "data"
    assert state["success"] is True
    assert len(state["attempts_log"]) == 1
    assert state["attempts_log"][0][1] is None
    assert state["query_result"] is not None
    assert state["answer"] == "【测试回答】"
    print("✅ test_first_try_success 通过！首次生成即成功")


def test_repair_cycle():
    """第一次生成坏SQL → 修复循环 → 第二次成功：2条日志，attempts=1"""
    graph = _build_graph(FakeClient([
        "SELECT nope FROM nope_table",
        "SELECT customer_id FROM customers LIMIT 2",
    ]))
    state = _invoke(graph, "有多少客户")
    assert state["success"] is True, f"修复后应成功，实际：{state['last_error']}"
    assert state["attempts"] == 1
    assert len(state["attempts_log"]) == 2
    assert state["attempts_log"][0][1] is not None, "第一条记录应带错误"
    assert state["attempts_log"][1][1] is None, "第二条记录应无错误"
    assert "customer_id" in state["sql"]
    print("✅ test_repair_cycle 通过！坏SQL被修复循环纠正")


def test_exhausted_attempts():
    """一直生成坏SQL：max_attempts轮后放弃，诚实失败，不编造"""
    graph = _build_graph(FakeClient(["SELECT nope FROM nope_table"]), max_attempts=3)
    state = _invoke(graph, "有多少客户")
    assert state["success"] is False
    assert len(state["attempts_log"]) == 3, f"应恰好尝试3次，实际{len(state['attempts_log'])}"
    assert state["attempts"] == 2
    assert "抱歉" in state["answer"]
    assert "最后一次错误" in state["answer"]
    print("✅ test_exhausted_attempts 通过！轮次耗尽后诚实失败")


def test_chat_route():
    """闲聊问题：走chat节点直接回答，不碰检索和SQL"""
    graph = _build_graph(FakeClient([], route_answer="chat"))
    state = _invoke(graph, "你好")
    assert state["route"] == "chat"
    assert state.get("sql") is None
    assert state.get("query_result") is None
    assert len(state.get("attempts_log") or []) == 0
    assert state["answer"] == "【测试回答】"
    print("✅ test_chat_route 通过！闲聊直达回答")


def test_ask_agent_thread_isolation():
    """两个不同会话（不同thread_id）：attempts_log互不残留"""
    from main import ask_data_agent
    graph = _build_graph(FakeClient(["SELECT customer_id FROM customers LIMIT 1"]))
    out1 = ask_data_agent(graph, "问题A", thread_id="sess-a")
    out2 = ask_data_agent(graph, "问题B", thread_id="sess-b")
    assert len(out1["attempts"]) == 1, f"问题A日志被污染：{out1['attempts']}"
    assert len(out2["attempts"]) == 1, f"问题B日志被污染：{out2['attempts']}"
    assert out1["answer"] == "【测试回答】"
    print("✅ test_ask_agent_thread_isolation 通过！问题间状态互不残留")


if __name__ == "__main__":
    test_graph_structure()
    test_first_try_success()
    test_repair_cycle()
    test_exhausted_attempts()
    test_chat_route()
    test_ask_agent_thread_isolation()
    print("\n🎉 全部LangGraph测试通过！")
