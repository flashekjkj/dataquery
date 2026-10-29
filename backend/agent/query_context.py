from __future__ import annotations
import json
import re
from typing import Any, Dict,List,Optional

from data.database import RELATIONS
from sql.executor import SqlExecuteError
from sql.sql_prompts import ANSWER_FROM_RESULT_SYSTEM_PROMPT, ANSWER_FROM_RESULT_USER_TPL, CHAT_SYSTEM_PROMPT, ROUTE_SYSTEM_PROMPT, ROUTE_USER_TPL
from observability import logger

# 长期记忆提炼的触发词：问题含这些词才调用LLM提炼偏好，否则跳过（省token+防误记）
MEMORY_TRIGGER_WORDS = ["以后", "总是", "一直", "默认", "记住", "偏好", "习惯", "通常"]

# 混合检索召回的字段文档数量（喂给SQL生成的schema上下文）
TOP_K_SCHEMA = 8
# 需要采样实际取值的字段角色：维度/过滤/时间字段的枚举值对写WHERE条件最有价值
SAMPLE_ROLES = {"dimension", "filter", "time"}
# 最多采样多少个字段的取值，控制prompt长度
MAX_SAMPLE_FIELDS = 6

# ===================== 业务逻辑函数 =====================

def route_question(model_client, question: str) -> str:
    """
    意图路由：判断用户问题是闲聊/常识（chat）、查数据库（data）还是需要外部工具（tool）
    返回 "data" / "tool" / "chat"；LLM 不可用时降级为 "data"（数据查询链路有
    自己的失败治理与诚实报错，比路由层抛异常更能保住用户体验）
    """
    try:
        resp = model_client.llm_invoke(
            prompt=ROUTE_USER_TPL.format(question=question),
            system_prompt=ROUTE_SYSTEM_PROMPT,
            operation="route",
        )
    except Exception as e:
        logger.warning("意图路由LLM调用失败，降级为data：{}", e)
        return "data"
    content = (resp.content or "").strip().lower()
    if "tool" in content:
        return "tool"
    if "data" in content:
        return "data"
    if "chat" in content:
        return "chat"
    # 解析失败默认走数据查询
    return "data"


def answer_directly(model_client, question: str, extra_instructions:str = "") -> str:
    """闲聊/常识问题：LLM直接回答，不碰数据库；extra_instructions为技能指令"""
    system_prompt=CHAT_SYSTEM_PROMPT
    if extra_instructions:
        system_prompt = CHAT_SYSTEM_PROMPT + f"\n\n[当前技能要求]\n{extra_instructions}"
    try:
        resp = model_client.llm_invoke(
            prompt=question,
            system_prompt=system_prompt,
            operation="chat",
        )
    except Exception as e:
        logger.warning("闲聊回答LLM调用失败：{}", e)
        return "抱歉，模型服务暂时不可用，请稍后重试。"
    return (resp.content or "").strip()


def retrieve_schema_context(hybrid, executor, question: str,table_scope:Optional[List[str]] = None):
    """
    为SQL生成准备上下文：
    1. 混合检索召回top_k个字段文档
    2. 提取命中表之间的关联关系
    3. 对维度/过滤/时间字段采样实际取值（value retrieval）
    返回 (field_docs, relations, value_samples)
    """
    recall_k = TOP_K_SCHEMA * 2 if table_scope else TOP_K_SCHEMA
    # use_rerank=True：RRF融合后交给本地CrossEncoder精排，提高schema命中精度
    ranked = hybrid.retrieve(question,top_k=recall_k,use_rerank=True)
    if table_scope:
        ranked = [item for item in ranked if item[0].table_name in table_scope]
    field_docs = [doc for doc,_score in ranked[:TOP_K_SCHEMA]]

    # 日期约束不能因召回缺失时间字段而无声丢弃，只从命中表元数据补足时间角色。
    if re.search(r"\d{4}[-年]|\d{1,2}月|上月|本月|去年|今年|日期|时间", question):
        from retrieval.store import SchemaStore
        from data.database import SCHEMA
        hit_tables = {doc.table_name for doc in field_docs}
        time_docs = [doc for doc in SchemaStore(SCHEMA).build_field_documents()
                     if doc.table_name in hit_tables and doc.role == "time"
                     and (not table_scope or doc.table_name in table_scope)]
        for doc in time_docs:
            if not any(d.doc_id == doc.doc_id for d in field_docs):
                field_docs = field_docs[:TOP_K_SCHEMA - 1] + [doc]

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
        operation="answer",
    )
    return (resp.content or "").strip()


# ===================== 记忆相关辅助函数 =====================

def _has_memory_trigger(question: str) -> bool:
    """规则预筛：问题含触发词才做长期记忆提炼，省token+防止误记"""
    return any(w in question for w in MEMORY_TRIGGER_WORDS)


# ===================== 节点构建函数：闭包注入依赖 =====================

