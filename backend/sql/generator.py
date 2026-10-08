from __future__ import annotations
import re
from typing import List,Dict,Any,Optional
from pydantic import BaseModel
from app.model_client import ModelClient
from .executor import SQLExecutor,SqlExecuteError,QueryResult
from .sql_prompts import (
    GENERATE_SQL_SYSTEM_PROMPT,
    GENERATE_SQL_USER_TPL,
    REPAIR_SQL_SYSTEM_PROMPT,
    REPAIR_SQL_USER_TPL
)

class AttemptRecord(BaseModel):
    sql:str
    error:Optional[str]

class SqlGenerateResult(BaseModel):
    success:bool
    final_sql:Optional[str]
    query_result:Optional[QueryResult]
    attempts:List[AttemptRecord]

def extract_sql(text:str) -> str:
    """
    从LLM返回文本提取可执行SQL
    支持：```sql围栏、混杂解释文字、无分号、大小写SELECT、mock格式"模拟SQL:xxx"
    """

    # 1.优先匹配```sql ...```代码块
    pattern_code = re.compile(r"```sql\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
    match_code = pattern_code.search(text)
    if match_code:
        sql = match_code.group(1).strip()
        return _normalize_sql(sql)

    # 2.匹配mock场景：模拟SQL: xxx
    pattern_mock = re.compile(r"模拟SQL[:：]\s*(SELECT.*?)(?=\n|$)", re.DOTALL | re.IGNORECASE)
    match_mock = pattern_mock.search(text)
    if match_mock:
        sql = match_mock.group(1).strip()
        return _normalize_sql(sql)

    # 3.提取SELECT开头语句
    pattern_select = re.compile(r"(SELECT\b.*?)(?=;|```|$)",re.DOTALL | re.IGNORECASE)
    match_select = pattern_select.search(text)
    if match_select:
        sql = match_select.group(1).strip()
        return _normalize_sql(sql)

    return ""

def _normalize_sql(sql:str) -> str:
    """SQL标准化，清理换行空格，补末尾分号"""
    sql = re.sub(r"\s+"," ",sql).strip()
    if sql and not sql.endswith(";"):
        sql += ";"
    return sql

def _format_field_docs(field_docs:List[Any]) -> str:
    """把field_docs转换为文本，填入prompt，FieldDocument有semantic_text属性"""
    lines = []
    for doc in field_docs:
        lines.append(f"- {doc.semantic_text}")
    return "\n".join(lines)

def _format_relations(relations:List[str]) -> str:
    """表关系转换为文本"""
    if not relations:
        return "无表关联信息"
    return "\n".join([f"- {r}" for r in relations])

def _format_value_samples(value_samples:Dict[str,List[Any]]) -> str:
    """
    value_samples: key=表.字段，value=样例列表
    例如 {"orders.status":["已支付","已取消"]}
    """
    if not value_samples:
        return "无字段样例值"
    lines = []
    for key,samples in value_samples.items():
        lines.append(f"- {key}: {samples}")
    return "\n".join(lines)

def generate_sql(
        question:str,
        field_docs:List[Any],
        relations:List[str],
        value_samples:Dict[str,List[Any]],
        model_client:ModelClient
) -> str:
    """组prompt -> llm调用 -> extract_sql提取SQL"""
    field_text = _format_field_docs(field_docs)
    rel_text = _format_relations(relations)
    val_text = _format_value_samples(value_samples)

    user_prompt = GENERATE_SQL_USER_TPL.format(
        question=question,
        field_docs_text=field_text,
        relations_text=rel_text,
        value_samples_text=val_text
    )

    resp = model_client.llm_invoke(
        prompt=user_prompt,
        system_prompt=GENERATE_SQL_SYSTEM_PROMPT
    )
    sql = extract_sql(resp.content)
    return sql

def generate_repair_sql(
        question:str,
        field_docs:List[Any],
        relations:List[str],
        value_samples:Dict[str,List[Any]],
        old_sql:str,
        error_msg:Optional[str],
        model_client:ModelClient
) -> str:
    """组修复prompt -> llm调用 -> extract_sql提取修复后的SQL"""
    field_text = _format_field_docs(field_docs)
    rel_text = _format_relations(relations)
    val_text = _format_value_samples(value_samples)

    user_prompt = REPAIR_SQL_USER_TPL.format(
        question=question,
        field_docs_text=field_text,
        relations_text=rel_text,
        value_samples_text=val_text,
        old_sql=old_sql,
        error_msg=error_msg
    )

    resp = model_client.llm_invoke(
        prompt=user_prompt,
        system_prompt=REPAIR_SQL_SYSTEM_PROMPT
    )
    return extract_sql(resp.content)

def generate_sql_with_repair(
        question:str,
        field_docs:List[Any],
        relations:List[str],
        value_samples:Dict[str,List[Any]],
        model_client:ModelClient,
        executor:SQLExecutor,
        max_attempts:int = 3
) -> SqlGenerateResult:
    """
    带自动修复循环：生成 -> 执行 -> 报错回灌修复prompt，最多max_attempts次
    返回包含每一轮attempt记录，失败不编造结果
    """
    attempts:List[AttemptRecord] = []
    final_result:Optional[QueryResult] = None
    final_sql:Optional[str] = None

    for attempt_idx in range(max_attempts):
        if attempt_idx == 0:
            sql = generate_sql(question,field_docs,relations,value_samples,model_client)
        else:
            field_text = _format_field_docs(field_docs)
            rel_text = _format_relations(relations)
            val_text = _format_value_samples(value_samples)
            last_attempt = attempts[-1]

            user_prompt = REPAIR_SQL_USER_TPL.format(
                question=question,
                field_docs_text=field_text,
                relations_text=rel_text,
                value_samples_text=val_text,
                old_sql=last_attempt.sql,
                error_msg=last_attempt.error
            )
            resp = model_client.llm_invoke(
                prompt=user_prompt,
                system_prompt=REPAIR_SQL_SYSTEM_PROMPT
            )
            sql=extract_sql(resp.content)

        if not sql:
            attempts.append(AttemptRecord(sql=sql,error="extract_sql未能提取到SQL"))
            continue

        try:
            # 执行SQL
            q_result = executor.execute(sql)
            attempts.append(AttemptRecord(sql=sql,error=None))
            final_sql = sql
            final_result = q_result
            return SqlGenerateResult(
                success=True,
                final_sql=final_sql,
                query_result=final_result,
                attempts=attempts
            )
        except SqlExecuteError as e:
            attempts.append(AttemptRecord(sql=sql,error=e.error_msg))

    return SqlGenerateResult(
        success=False,
        final_sql=None,
        query_result=None,
        attempts=attempts
    )

