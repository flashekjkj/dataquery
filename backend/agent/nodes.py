from __future__ import annotations
import json
from typing import Any, Dict

from agent.state import AskDataState
from data.database import RELATIONS
from sql.executor import SqlExecuteError
from sql.generator import generate_sql, generate_repair_sql
from sql.sql_prompts import (
    ANSWER_FROM_RESULT_SYSTEM_PROMPT,
    ANSWER_FROM_RESULT_USER_TPL,
    CHAT_SYSTEM_PROMPT,
    ROUTE_SYSTEM_PROMPT,
    ROUTE_USER_TPL,
)

# 混合检索召回的字段文档数量（喂给SQL生成的schema上下文）
TOP_K_SCHEMA = 8
# 需要采样实际取值的字段角色：维度/过滤/时间字段的枚举值对写WHERE条件最有价值
SAMPLE_ROLES = {"dimension", "filter", "time"}
# 最多采样多少个字段的取值，控制prompt长度
MAX_SAMPLE_FIELDS = 6

# ===================== 业务逻辑函数 =====================

def route_question(model_client, question: str) -> str:
    """
    意图路由：判断用户问题是闲聊/常识（chat）还是需要查数据库（data）
    返回 "data" 或 "chat"
    """
    resp = model_client.llm_invoke(
        prompt=ROUTE_USER_TPL.format(question=question),
        system_prompt=ROUTE_SYSTEM_PROMPT,
    )
    content = (resp.content or "").strip().lower()
    if "data" in content:
        return "data"
    if "chat" in content:
        return "chat"
    # 解析失败默认走数据查询
    return "data"


def answer_directly(model_client, question: str) -> str:
    """闲聊/常识问题：LLM直接回答，不碰数据库"""
    resp = model_client.llm_invoke(
        prompt=question,
        system_prompt=CHAT_SYSTEM_PROMPT,
    )
    return (resp.content or "").strip()


def retrieve_schema_context(hybrid, executor, question: str):
    """
    为SQL生成准备上下文：
    1. 混合检索召回top_k个字段文档
    2. 提取命中表之间的关联关系
    3. 对维度/过滤/时间字段采样实际取值（value retrieval）
    返回 (field_docs, relations, value_samples)
    """
    ranked = hybrid.retrieve(question, top_k=TOP_K_SCHEMA)
    field_docs = [doc for doc, _score in ranked]

    # 表关联：只保留与命中的表相关的条目，避免无关关系干扰LLM
    hit_tables = {doc.table_name for doc in field_docs}
    relations = [
        f"{r['left_table']}.{r['left_field']} = {r['right_table']}.{r['right_field']}"
        f"（{r['description']}）"
        for r in RELATIONS
        if r["left_table"] in hit_tables or r["right_table"] in hit_tables
    ]

    # 字段样例值：采样失败（如字段类型不支持）就跳过，不阻塞主流程
    value_samples: Dict[str, Any] = {}
    sampled = 0
    for doc in field_docs:
        if doc.role not in SAMPLE_ROLES or sampled >= MAX_SAMPLE_FIELDS:
            continue
        try:
            samples = executor.sample_values(doc.table_name, doc.field_name, n=5)
            value_samples[f"{doc.table_name}.{doc.field_name}"] = samples
            sampled += 1
        except SqlExecuteError:
            continue
    return field_docs, relations, value_samples


def format_query_result_for_llm(query_result) -> str:
    """把QueryResult对象转换为LLM可读的JSON字符串"""
    if query_result is None:
        return "无查询结果"
    content = {
        "columns": query_result.columns,
        "rows": query_result.rows[:20],
        "row_count": query_result.row_count,
        "is_truncated": query_result.is_truncated,
    }
    return json.dumps(content, ensure_ascii=False, indent=2)


def generate_natural_answer(model_client, user_question: str, final_sql: str, query_result) -> str:
    """调用LLM，把SQL执行结果整理成自然语言回答"""
    result_str = format_query_result_for_llm(query_result)
    user_prompt = ANSWER_FROM_RESULT_USER_TPL.format(
        user_question=user_question,
        sql=final_sql,
        query_result=result_str,
    )
    resp = model_client.llm_invoke(
        prompt=user_prompt,
        system_prompt=ANSWER_FROM_RESULT_SYSTEM_PROMPT,
    )
    return (resp.content or "").strip()


# ===================== 节点构建函数：闭包注入依赖 =====================

def build_nodes(model_client, hybrid, executor):
    """
    构建所有的LangGraph节点，闭包捕获外部依赖 model_client / hybrid / executor
    返回字典 {节点名：节点函数}
    """
    def route_node(state: AskDataState) -> dict:
        """意图路由节点：判断是chat还是data查询"""
        return {"route": route_question(model_client, state["user_question"])}

    def chat_node(state: AskDataState) -> dict:
        """闲聊回答节点，直接返回自然语言答案"""
        return {"answer": answer_directly(model_client, state["user_question"])}

    def retrieve_node(state: AskDataState) -> dict:
        """检索节点：召回schema字段、表关联、字段样例值"""
        field_docs, relations, value_samples = retrieve_schema_context(
            hybrid, executor, state["user_question"]
        )
        return {
            "field_docs": field_docs,
            "relations": relations,
            "value_samples": value_samples
        }

    def generate_sql_node(state: AskDataState) -> dict:
        """
        SQL生成节点
        首次尝试(attempts=0)：使用基础生成prompt
        重试(attempts>0)：使用修复prompt，传入上一轮错误+旧SQL
        """
        question = state["user_question"]
        if state["attempts"] == 0:
            sql = generate_sql(
                question=question,
                field_docs=state["field_docs"],
                relations=state["relations"],
                value_samples=state["value_samples"],
                model_client=model_client
            )
        else:
            sql = generate_repair_sql(
                question=question,
                field_docs=state["field_docs"],
                relations=state["relations"],
                value_samples=state["value_samples"],
                old_sql=state["sql"],
                error_msg=state["last_error"],
                model_client=model_client
            )
        return {"sql": sql}

    def execute_node(state: AskDataState) -> dict:
        """
        执行SQL节点：捕获执行异常，写入状态
        成功/失败都追加一条记录到attempts_log（reducer自动累加）
        """
        sql_text = state["sql"]
        try:
            query_result = executor.execute(sql_text)
            return {
                "query_result": query_result,
                "success": True,
                "last_error": None,
                "attempts_log": [(sql_text, None)]
            }
        except SqlExecuteError as e:
            return {
                "query_result": None,
                "success": False,
                "last_error": e.error_msg,
                "attempts_log": [(sql_text, e.error_msg)]
            }

    def repair_node(state: AskDataState) -> dict:
        """
        重试簿记节点：
        1. attempts计数+1（attempts=已完成的修复次数）
        随后环回generate_sql_node
        """
        return {"attempts": state["attempts"] + 1}

    def answer_node(state: AskDataState) -> dict:
        """最终回答节点：成功则LLM整理结果；失败返回诚实报错文本，不编造"""
        if state["success"]:
            ans = generate_natural_answer(
                model_client,
                state["user_question"],
                state["sql"],
                state["query_result"],
            )
        else:
            log = state["attempts_log"] or []
            last_error = log[-1][1] if log else "未知错误"
            ans = (
                f"抱歉，尝试了 {len(log)} 次后仍未生成可执行的SQL。"
                f"最后一次错误：{last_error}"
            )
        return {"answer": ans}

    return {
        "route_node": route_node,
        "chat_node": chat_node,
        "retrieve_node": retrieve_node,
        "generate_sql_node": generate_sql_node,
        "execute_node": execute_node,
        "repair_node": repair_node,
        "answer_node": answer_node,
    }
