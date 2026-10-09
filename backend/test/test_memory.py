from __future__ import annotations
import sys
import tempfile
from pathlib import Path

BASE_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_BACKEND))

from app.schemas import LLMResponse
from agent.graph import build_graph
from memory.conversation import ConversationMemory
from memory.long_term import LongTermMemoryStore
from retrieval.service import build_hybrid_index
from sql.executor import SQLExecutor
from sql.sql_prompts import (
    GENERATE_SQL_SYSTEM_PROMPT,
    MEMORY_EXTRACT_SYSTEM_PROMPT,
    MEMORY_SUMMARY_SYSTEM_PROMPT,
    REPAIR_SQL_SYSTEM_PROMPT,
    REWRITE_QUERY_SYSTEM_PROMPT,
)
from test.test_graph import DB_PATH, FakeClient


class MemoryFakeClient(FakeClient):
    """
    在FakeClient基础上支持记忆相关调用的可编程假客户端：
    - rewrite/extract/summary 返回可编程内容并计数
    - 记录SQL生成收到的prompt，用于断言指代消解/偏好注入是否生效
    """
    def __init__(self, sql_sequence, route_answer="data",
                 rewrite_answer=None, extract_answer="NONE",
                 summary_answer="测试摘要"):
        super().__init__(sql_sequence, route_answer)
        self.rewrite_answer = rewrite_answer
        self.extract_answer = extract_answer
        self.summary_answer = summary_answer
        self.rewrite_calls = 0
        self.extract_calls = 0
        self.summary_calls = 0
        self.generate_prompts = []

    def llm_invoke(self, prompt, system_prompt=None):
        if system_prompt == REWRITE_QUERY_SYSTEM_PROMPT:
            self.rewrite_calls += 1
            content = self.rewrite_answer if self.rewrite_answer is not None else "默认改写问题"
            return LLMResponse(content=content, usage={})
        if system_prompt == MEMORY_EXTRACT_SYSTEM_PROMPT:
            self.extract_calls += 1
            return LLMResponse(content=self.extract_answer, usage={})
        if system_prompt == MEMORY_SUMMARY_SYSTEM_PROMPT:
            self.summary_calls += 1
            return LLMResponse(content=self.summary_answer, usage={})
        if system_prompt in (GENERATE_SQL_SYSTEM_PROMPT, REPAIR_SQL_SYSTEM_PROMPT):
            self.generate_prompts.append(prompt)
        return super().llm_invoke(prompt, system_prompt)


def _build_with_memories(client, tmp_root: Path, max_attempts: int = 3):
    """构建图并返回记忆存储，供测试检查记忆内容"""
    hybrid = build_hybrid_index(client)
    executor = SQLExecutor(str(DB_PATH))
    conv_memory = ConversationMemory(data_root=tmp_root)
    long_term_memory = LongTermMemoryStore(data_root=tmp_root)
    graph = build_graph(
        client, hybrid, executor,
        conv_memory, long_term_memory,
        max_attempts=max_attempts
    )
    return graph, conv_memory, long_term_memory


def _invoke(graph, question: str, thread_id: str) -> dict:
    return graph.invoke(
        {"user_question": question, "attempts": 0, "attempts_log": []},
        config={"configurable": {"thread_id": thread_id}},
    )


# ===================== ConversationMemory 单元测试 =====================

def test_conversation_memory_basic():
    root = Path(tempfile.mkdtemp())
    cm = ConversationMemory(data_root=root)
    cm.append_turn("t1", {"role": "user", "content": "华东销售额多少", "sql": None, "answer": None})
    cm.append_turn("t1", {"role": "assistant", "content": "879万", "sql": "SELECT SUM(paid_amount)", "answer": None})
    cm.append_turn("t2", {"role": "user", "content": "另一会话的问题", "sql": None, "answer": None})

    # 落盘往返一致
    data = cm.load("t1")
    assert len(data["turns"]) == 2, f"t1应有2条记录，实际{len(data['turns'])}"
    # 会话隔离
    assert len(cm.load("t2")["turns"]) == 1
    # recent_context格式：含用户问题和助手回答与SQL
    ctx = cm.recent_context("t1", n=4)
    assert "用户：华东销售额多少" in ctx
    assert "助手：879万" in ctx
    assert "SELECT SUM(paid_amount)" in ctx
    # 无summary时无摘要前缀
    assert "【会话摘要】" not in ctx
    # 不存在的会话返回空
    assert cm.recent_context("不存在", n=4) == ""
    print("✅ test_conversation_memory_basic 通过！")


def test_conversation_memory_summarize():
    root = Path(tempfile.mkdtemp())
    cm = ConversationMemory(data_root=root)
    # 塞10条记录（>threshold=8）
    for i in range(10):
        cm.append_turn("t1", {"role": "user", "content": f"问题{i}", "sql": None, "answer": None})

    calls = []
    def fake_llm(system_prompt, user_prompt):
        calls.append(user_prompt)
        return "摘要：前6轮讨论销售数据"

    cm.maybe_summarize("t1", fake_llm, keep=4, threshold=8,
                       summary_system_prompt="s", summary_user_tpl="{history}")
    data = cm.load("t1")
    assert data["summary"] == "摘要：前6轮讨论销售数据"
    assert len(data["turns"]) == 4, f"应截断到4条，实际{len(data['turns'])}"
    assert data["turns"][0]["content"] == "问题6", "应保留最近4条"
    assert len(calls) == 1

    # 摘要失败降级：LLM抛异常→保留旧摘要，仍截断
    def broken_llm(s, u):
        raise RuntimeError("LLM挂了")
    # 再塞5条：总数9 > threshold=8，触发第二次摘要（这次LLM会挂）
    for i in range(10, 15):
        cm.append_turn("t1", {"role": "user", "content": f"问题{i}", "sql": None, "answer": None})
    cm.maybe_summarize("t1", broken_llm, keep=4, threshold=8,
                       summary_system_prompt="s", summary_user_tpl="{history}")
    data = cm.load("t1")
    assert data["summary"] == "摘要：前6轮讨论销售数据", "失败时应保留旧摘要"
    assert len(data["turns"]) == 4, f"仍应截断到4条，实际{len(data['turns'])}"
    assert data["turns"][0]["content"] == "问题11", "应保留最近4条"
    print("✅ test_conversation_memory_summarize 通过！含失败降级")


# ===================== LongTermMemory 单元测试 =====================

def test_long_term_memory():
    root = Path(tempfile.mkdtemp())
    lm = LongTermMemoryStore(data_root=root)
    # 空记忆
    assert lm.load("default") == "无用户偏好"
    # 添加两条
    lm.add("default", "用户希望金额默认指实付金额")
    lm.add("default", "用户关注华东区域")
    text = lm.load("default")
    assert "实付金额" in text and "华东" in text
    # 去重：新fact被已有fact包含 → 不新增，只更新
    lm.add("default", "金额默认指实付金额")
    data = lm._load_raw()
    assert len(data["users"]["default"]["facts"]) == 2, "包含式去重后应仍为2条"
    assert data["users"]["default"]["facts"][0]["fact"] == "金额默认指实付金额", "应更新为新的陈述"
    # 空串不写入
    lm.add("default", "   ")
    assert len(lm._load_raw()["users"]["default"]["facts"]) == 2
    print("✅ test_long_term_memory 通过！含去重")


# ===================== 记忆与图的集成测试 =====================

def test_multi_turn_rewrite():
    """同一会话两轮：第二轮触发指代消解，改写结果被SQL生成使用"""
    root = Path(tempfile.mkdtemp())
    client = MemoryFakeClient(
        ["SELECT customer_id FROM customers LIMIT 1"],
        rewrite_answer="华南地区销售额是多少"
    )
    graph, conv_memory, _ = _build_with_memories(client, root)

    state1 = _invoke(graph, "华东地区销售额是多少", thread_id="mt")
    assert client.rewrite_calls == 0, "首轮无历史，不应调用指代消解"
    assert "华东地区销售额是多少" in client.generate_prompts[0]

    state2 = _invoke(graph, "那华南呢？", thread_id="mt")
    assert client.rewrite_calls == 1, "第二轮有历史，应调用指代消解"
    assert "华南地区销售额是多少" in client.generate_prompts[1], "改写后的问题应被SQL生成使用"
    assert state2["rewritten_question"] == "华南地区销售额是多少"

    # 会话历史已保存：2轮×2条记录
    assert len(conv_memory.load("mt")["turns"]) == 4
    print("✅ test_multi_turn_rewrite 通过！指代消解生效")


def test_attempts_log_reset_within_session():
    """同一会话连续两轮：attempts_log每轮重置，不跨轮累积（回归测试）"""
    root = Path(tempfile.mkdtemp())
    client = MemoryFakeClient(["SELECT customer_id FROM customers LIMIT 1"])
    graph, _, _ = _build_with_memories(client, root)

    s1 = _invoke(graph, "问题A", thread_id="same")
    s2 = _invoke(graph, "问题B", thread_id="same")
    assert len(s1["attempts_log"]) == 1, f"第1轮日志异常：{s1['attempts_log']}"
    assert len(s2["attempts_log"]) == 1, f"第2轮日志跨轮残留：{s2['attempts_log']}"
    print("✅ test_attempts_log_reset_within_session 通过！")


def test_memory_extraction_and_injection():
    """触发词问题提炼偏好入库；后续问题prompt注入偏好；无触发词不调LLM"""
    root = Path(tempfile.mkdtemp())
    fact = "用户希望金额默认指实付金额"
    client = MemoryFakeClient(
        ["SELECT customer_id FROM customers LIMIT 1"],
        extract_answer=fact
    )
    graph, _, long_term_memory = _build_with_memories(client, root)

    # 含触发词"默认"→提炼偏好
    _invoke(graph, "以后金额都默认看实付金额", thread_id="mem")
    assert client.extract_calls == 1, "含触发词应调用提炼"
    assert fact in long_term_memory.load("default"), "偏好应写入长期记忆"

    # 无触发词→不调提炼
    _invoke(graph, "华东地区销售额是多少", thread_id="mem")
    assert client.extract_calls == 1, "无触发词不应再调用提炼"

    # 偏好注入：后续问题的SQL生成prompt应包含该偏好
    assert fact in client.generate_prompts[-1], "长期偏好应注入SQL生成prompt"
    print("✅ test_memory_extraction_and_injection 通过！规则预筛+提炼+注入全链路")


if __name__ == "__main__":
    test_conversation_memory_basic()
    test_conversation_memory_summarize()
    test_long_term_memory()
    test_multi_turn_rewrite()
    test_attempts_log_reset_within_session()
    test_memory_extraction_and_injection()
    print("\n🎉 全部记忆模块测试通过！")
