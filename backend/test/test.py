from __future__ import annotations
import json
from pathlib import Path
from typing import Any,Dict,List,Tuple

from app.model_client import ModelClient
from data.database import RELATIONS
from retrieval.ranker import HybridRetriever
from retrieval.service import build_hybrid_index
from sql.executor import SQLExecutor,SqlExecuteError
from sql.generator import generate_sql_with_repair
from sql.sql_prompts import (
    ANSWER_FROM_RESULT_SYSTEM_PROMPT,
    ANSWER_FROM_RESULT_USER_TPL,
    CHAT_SYSTEM_PROMPT,
    ROUTE_SYSTEM_PROMPT,
    ROUTE_USER_TPL
)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "demo_ecom.duckdb"

# 混合检索召回的字段文档数量
TOP_K_SCHEMA = 8
SAMPLE_ROLES = {"dimension","filter","time"}
# 最多采样多少个字段的取值，控制prompt长度
MAX_SAMPLE_FIELDS = 6

def route_question(model_client:ModelClient,question:str) -> str:
    """
    意图路由：判断用户问题是闲聊/常识（chat）还是需要查数据库（data）
    返回 "data" 或 "chat"
    """
    resp = model_client.llm_invoke(
        prompt=ROUTE_USER_TPL.format(question=question),
        system_prompt=ROUTE_SYSTEM_PROMPT
    )    
    content = (resp.content or "").strip().lower()
    if "data" in content:
        return "data"
    if "chat" in content:
        return "chat"
    return "data"

def answer_directly(model_client:ModelClient,question:str) -> str:
    """闲聊/常识问题：LLM直接回答，不碰数据库"""
    resp = model_client.llm_invoke(
        prompt=question,
        system_prompt=CHAT_SYSTEM_PROMPT
    )
    return (resp.content or "").strip()

def retrieve_schema_content(
        hybrid:HybridRetriever,
        executor:SQLExecutor,
        question:str,
) -> Tuple[List[Any],List[str],Dict[str,List[Any]]]:
    """
    为SQL生成准备上下文：
    1. 混合检索召回top_k个字段文档
    2. 提取命中表之间的关联关系
    3. 对维度/过滤/时间字段采样实际取值（value retrieval）
    """
    ranked = hybrid.retrieve(question,top_k=TOP_K_SCHEMA)    
    field_docs = [doc for doc,_score in ranked]

    hit_tables = {doc.table_name for doc in field_docs}
    relations = [
        f"{r['left_table']}.{r['left_field']} = {r['right_table']}.{r['right_field']}"
        f"（{r['description']}）"
        for r in RELATIONS
        if r["left_table"] in hit_tables or r["right_table"] in hit_tables        
    ]

    value_samples:Dict[str,List[Any]] = {}
    sampled = 0
    for doc in field_docs:
        if doc.role not in SAMPLE_ROLES or sampled >= MAX_SAMPLE_FIELDS:
            continue
        try:
            samples = executor.sample_values(doc.table_name,doc.field_name,n=5)
            value_samples[f"{doc.table_name}.{doc.field_name}"] = samples
            samples += 1
        except SqlExecuteError:
            continue
    return field_docs,relations,value_samples

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

def generate_natural_answer(
    model_client: ModelClient,
    user_question: str,
    final_sql: str,
    query_result,
) -> str:
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

def ask_data_agent(
        model_client:ModelClient,
        hybrid:HybridRetriever,
        executor:SQLExecutor,
        user_question:str,
        max_attempt:int = 3
) -> dict:
    """
    基础Agent主逻辑：
    1. 意图路由：闲聊直接回答；数据问题走查询链路
    2. 混合检索schema -> 生成SQL -> 执行 -> 失败自修复（最多max_attempts轮）
    3. 查询结果 -> LLM整理成自然语言回答
    """
    route = route_question(model_client,user_question)

    if route == "chat":
        return {
            "success":True,
            "route":"chat",
            "user_question":user_question,
            "retrieved_docs":[],
            "final_sql":None,
            "query_result":None,
            "answer":answer_directly(model_client,user_question),
            "attempt":[],
        }

    field_docs,relations,value_samples = retrieve_schema_content(
        hybrid,executor,user_question
    )

    gen_result = generate_sql_with_repair(
        question=user_question,
        field_docs=field_docs,
        relations=relations,
        value_samples=value_samples,
        model_client=model_client,
        executor=executor,
        max_attempts=max_attempt
    )

    if gen_result.success:
        answer = generate_sql_with_repair(
            model_client=model_client,
            user_question=user_question,
            final_sql=gen_result.final_sql,
            query_result=gen_result.query_result
        )
    else:
        # 自修复轮次耗尽：诚实告知失败原因，不编造结果
        last_error = gen_result.attempts[-1].error if gen_result.attempts else "未知错误"
        answer = (
            f"抱歉，尝试了 {len(gen_result.attempts)} 次后仍未生成可执行的SQL。"
            f"最后一次错误：{last_error}"
        )

    return {
        "success":gen_result.success,
        "route":"data",
        "user_question":user_question,
        "retrieved_docs":[doc.doc_id for doc in field_docs],
        "final_sql":gen_result.final_sql,
        "query_result":gen_result.query_result,
        "answer":answer,
        "attempt":[a.model_dump() for a in gen_result.attempts]
    }