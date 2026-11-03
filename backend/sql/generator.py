from __future__ import annotations
import json
import re
from typing import List,Dict,Any,Optional
from pydantic import BaseModel
from app.config import settings
from app.clock import business_now
from app.model_client import ModelClient
from observability import logger
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
    从LLM返回文本提取可执行SQL（结构化输出不可用时的兜底解析）
    支持：```sql围栏、混杂解释文字、无分号、大小写SELECT、mock格式"模拟SQL:xxx"
    """

    # 1.优先匹配```sql ...```代码块
    pattern_code = re.compile(r"```sql\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
    match_code = pattern_code.search(text)
    if match_code:
        sql = match_code.group(1).strip()
        return _normalize_sql(sql)

    # 保留整个语句，不能在分号处截断（分号可能位于字面量中）。
    # 也不能截掉 WITH；执行器使用 DuckDB parser 校验是否为单条 SELECT。
    match = re.search(r"\b(?:WITH|SELECT)\b", text, re.IGNORECASE)
    return _normalize_sql(text[match.start():]) if match else ""


def parse_sql_response(text: str) -> str:
    """
    LLM 返回内容 → 可执行 SQL（解析策略：结构化优先，正则兜底）。

    首选按 JSON 对象 {"sql": "..."} 解析（配合 response_format=json_object）；
    模型未遵循 JSON 协议时逐级降级：代码围栏包裹的 JSON → 纯文本 SQL 提取。
    解析失败返回空串，由上游进入自修复/失败路径。
    """
    if not text:
        return ""

    # 1. 尝试直接 JSON 解析（允许外层被 ```json / ``` 围栏包裹）
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*\n?(.*?)```", candidate, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            value = parsed.get("sql")
            sql = value.strip() if isinstance(value, str) else ""
            if sql:
                return _normalize_sql(sql)
    except (json.JSONDecodeError, TypeError):
        pass

    # 2. 兜底：正则从纯文本提取 SQL
    sql = extract_sql(text)
    if not sql:
        logger.warning("SQL解析失败，模型返回内容前120字符：{}", (text or "")[:120])
    return sql

def _normalize_sql(sql:str) -> str:
    """仅清理边界空白，保留字符串、注释与内部换行语义。"""
    sql = sql.strip()
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

def semantic_prompt(context, question, docs):
    tables={d.table_name for d in docs}
    metrics={k:v for k,v in context.semantics.get('metrics',{}).items() if set(v['tables'])<=tables}
    selected=sorted(metrics,key=lambda k:(k not in question,k))[:4]
    return "\n业务语义（命中指标必须遵守表达式、过滤条件、时间口径；JOIN只能使用批准的键和粒度）：" + json.dumps({
        'metrics':{k:metrics[k] for k in selected},
        'joins':{k:v for k,v in context.semantics.get('joins',{}).items() if {v['left'],v['right']}<=tables},
        'time_fields':{k:v for k,v in context.semantics.get('time_fields',{}).items() if k in tables},
        'policy_version':context.policy_version,'semantic_version':context.semantic_version},ensure_ascii=False)


def generate_sql(
        question:str,
        field_docs:List[Any],
        relations:List[str],
        value_samples:Dict[str,List[Any]],
        model_client:ModelClient,
        user_profile:str = "",
        skill_instructions: str = "",
        query_context=None
) -> str:
    """组prompt -> llm调用 -> extract_sql提取SQL"""
    field_text = _format_field_docs(field_docs)
    rel_text = _format_relations(relations)
    val_text = _format_value_samples(value_samples)
    profile_text = (user_profile or "无用户偏好") + "\n当前业务时间：" + business_now() + "。相对日期请转换为日期字面量，不使用 CURRENT_DATE/NOW。"

    dialect = "PostgreSQL" if query_context and query_context.dialect == "postgres" else "DuckDB"
    user_prompt = GENERATE_SQL_USER_TPL.replace("DuckDB", dialect).format(
        question=question,
        user_profile=profile_text,
        field_docs_text=field_text,
        relations_text=rel_text,
        value_samples_text=val_text
    )

    system_prompt=GENERATE_SQL_SYSTEM_PROMPT
    if query_context and query_context.dialect == "postgres":
        system_prompt = system_prompt.replace("DuckDB", "PostgreSQL")
    if query_context:
        user_prompt += "\n查询上下文：" + json.dumps({"source_id": query_context.source_id, "dialect": query_context.dialect, "schema_version": query_context.snapshot.version}) + "\nSQL必须使用目录中的表限定名和当前方言。"
        user_prompt += semantic_prompt(query_context,question,field_docs)
    if skill_instructions:
        system_prompt = system_prompt + f"\n\n【当前技能要求】\n{skill_instructions}"
    resp = model_client.llm_invoke(
        prompt=user_prompt,
        system_prompt=system_prompt,
        operation="generate_sql",
        json_mode=settings.LLM_JSON_MODE,
    )
    return parse_sql_response(resp.content)

def generate_repair_sql(
        question:str,
        field_docs:List[Any],
        relations:List[str],
        value_samples:Dict[str,List[Any]],
        old_sql:str,
        error_msg:Optional[str],
        model_client:ModelClient,
        user_profile:str = "",
        skill_instructions: str = "",
        query_context=None
) -> str:
    """组修复prompt -> llm调用 -> extract_sql提取修复后的SQL"""
    field_text = _format_field_docs(field_docs)
    rel_text = _format_relations(relations)
    val_text = _format_value_samples(value_samples)
    profile_text = (user_profile or "无用户偏好") + "\n当前业务时间：" + business_now() + "。相对日期请转换为日期字面量。"

    dialect = "PostgreSQL" if query_context and query_context.dialect == "postgres" else "DuckDB"
    user_prompt = REPAIR_SQL_USER_TPL.replace("DuckDB", dialect).format(
        question=question,
        user_profile=profile_text,
        field_docs_text=field_text,
        relations_text=rel_text,
        value_samples_text=val_text,
        old_sql=old_sql,
        error_msg=error_msg
    )


    system_prompt=REPAIR_SQL_SYSTEM_PROMPT
    if query_context:
        user_prompt += "\n查询上下文：" + json.dumps({"source_id": query_context.source_id, "dialect": query_context.dialect, "schema_version": query_context.snapshot.version})
        user_prompt += semantic_prompt(query_context,question,field_docs)
    if query_context and query_context.dialect == "postgres":
        system_prompt = system_prompt.replace("DuckDB", "PostgreSQL")
    if skill_instructions:
        system_prompt = system_prompt + f"\n\n【当前技能要求】\n{skill_instructions}"
    resp = model_client.llm_invoke(
        prompt=user_prompt,
        system_prompt=system_prompt,
        operation="repair_sql",
        json_mode=settings.LLM_JSON_MODE,
    )
    return parse_sql_response(resp.content)

def generate_sql_with_repair(
        question:str,
        field_docs:List[Any],
        relations:List[str],
        value_samples:Dict[str,List[Any]],
        model_client:ModelClient,
        executor:SQLExecutor,
        max_attempts:int = 3,
        query_context=None
) -> SqlGenerateResult:
    """
    带自动修复循环：生成 -> 执行 -> 报错回灌修复prompt，最多max_attempts次
    返回包含每一轮attempt记录，失败不编造结果
    """
    if query_context is None and hasattr(executor, "inspect_schema"):
        from datasources.models import QueryContext
        query_context = QueryContext(snapshot=executor.inspect_schema())
    attempts:List[AttemptRecord] = []
    final_result:Optional[QueryResult] = None
    final_sql:Optional[str] = None

    for attempt_idx in range(max_attempts):
        if attempt_idx == 0:
            sql = generate_sql(question,field_docs,relations,value_samples,model_client,query_context=query_context)
        else:
            last_attempt = attempts[-1]
            sql = generate_repair_sql(question,field_docs,relations,value_samples,
                last_attempt.sql,last_attempt.error,model_client,query_context=query_context)

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

