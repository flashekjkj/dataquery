from __future__ import annotations
import json
from typing import Any, Dict,List,Optional

from agent.state import AskDataState
from data.database import RELATIONS
from sql.executor import SqlExecuteError
from sql.generator import generate_sql, generate_repair_sql
from sql.sql_prompts import (
    ANSWER_FROM_RESULT_SYSTEM_PROMPT,
    ANSWER_FROM_RESULT_USER_TPL,
    CHAT_SYSTEM_PROMPT,
    MEMORY_EXTRACT_SYSTEM_PROMPT,
    MEMORY_EXTRACT_USER_TPL,
    MEMORY_SUMMARY_SYSTEM_PROMPT,
    MEMORY_SUMMARY_USER_TPL,
    REWRITE_QUERY_SYSTEM_PROMPT,
    REWRITE_QUERY_USER_TPL,
    ROUTE_SYSTEM_PROMPT,
    ROUTE_USER_TPL,
    TOOL_ANSWER_SYSTEM_PROMPT,
    TOOL_ANSWER_USER_TPL, 
    TOOL_PLAN_SYSTEM_PROMPT
)

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
    返回 "data" / "tool" / "chat"
    """
    resp = model_client.llm_invoke(
        prompt=ROUTE_USER_TPL.format(question=question),
        system_prompt=ROUTE_SYSTEM_PROMPT,
    )
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
    resp = model_client.llm_invoke(
        prompt=question,
        system_prompt=system_prompt,
    )
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
    ranked = hybrid.retrieve(question,top_k=recall_k)
    if table_scope:
        ranked = [item for item in ranked if item[0].table_name in table_scope]
    field_docs = [doc for doc,_score in ranked[:TOP_K_SCHEMA]]

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


# ===================== 记忆相关辅助函数 =====================

def _has_memory_trigger(question: str) -> bool:
    """规则预筛：问题含触发词才做长期记忆提炼，省token+防止误记"""
    return any(w in question for w in MEMORY_TRIGGER_WORDS)


# ===================== 节点构建函数：闭包注入依赖 =====================

def build_nodes(model_client, hybrid, executor, conv_memory, long_term_memory,skill_registry=None,mcp_manager=None):
    """
    构建所有的LangGraph节点，闭包捕获外部依赖
    model_client / hybrid / executor / conv_memory / long_term_memory
    skill_registry / mcp_manager：可选，为None时技能/工具节点自动降级为空操作
    返回字典 {节点名：节点函数}
    """
    def _get_thread_id(config) -> str:
        return config["configurable"]["thread_id"]

    def _llm_summarize(system_prompt: str, user_prompt: str) -> str:
        """滚动摘要用的LLM调用封装，供ConversationMemory.maybe_summarize使用"""
        resp = model_client.llm_invoke(prompt=user_prompt, system_prompt=system_prompt)
        return resp.content or ""

    def _skill_instructions(state:AskDataState) -> str:
        """取当前生效技能的指令文本，无技能返回空串"""
        if skill_registry is None or not state.get("active_skill"):
            return ""
        skill = skill_registry.get(state["active_skill"])
        return skill.system_instructions if skill else ""

    def skill_node(state:AskDataState) -> dict:
        """
        技能匹配节点（在改写之后、路由之前）：
        1. 用户强制指定（forced_skill）优先，取不到则回退自动关键词匹配
        2. 无命中写None，下游节点据此跳过技能注入        
        """
        effective = state.get("rewritten_question") or state["user_question"]
        forced = state.get("forced_skill")
        skill = None
        if skill_registry is not None:
            if forced:
                skill = skill_registry.get(forced)
            else:
                skill = skill_registry.match(effective)
        return {"active_skill":skill.name if skill else None}

    def _allowed_mcp_tools(state:AskDataState) -> Optional[List[str]]:
        """
        技能×MCP联动：当前技能声明了mcp_tools时，只暴露这些工具；
        技能未声明（空列表）或无技能 → None = 全部工具可用
        """
        if skill_registry is None or not state.get("active_skill"):
            return None
        skill = skill_registry.get(state["active_skill"])
        if skill and skill.mcp_tools:
            return skill.mcp_tools
        return None 

    def tool_plan_node(state:AskDataState) -> dict:
        """
        工具规划节点：把问题与可用工具交给LLM（function calling），
        由LLM决定调用哪个工具、传什么参数；未选择工具则tool_calls为空（下游降级闲聊）
        """
        if mcp_manager is None:
            return {"tool_calls":[]}
        effective = state.get("rewritten_question") or state["user_question"]
        allowed = _allowed_mcp_tools(state)
        tools_schema = mcp_manager.to_openai_tools(allowed_tools=allowed)
        if not tools_schema:
            return {"tool_calls":[]}
        resp = model_client.chat_with_tools(
            prompt=effective,
            system_prompt=TOOL_PLAN_SYSTEM_PROMPT,
            tools=tools_schema
        )
        return {"tool_calls": resp["tool_calls"]}

    def tool_execute_node(state: AskDataState) -> dict:
        """
        工具执行节点：依次执行规划好的工具调用，结果拼成文本写入tool_result
        """
        results = []
        for call in state.get("tool_calls") or []:
            full_name = call["name"]
            # 工具名格式 server__tool
            if "__" in full_name:
                server_name, tool_name = full_name.split("__", 1)
            else:
                server_name, tool_name = full_name, full_name
            try:
                text = mcp_manager.call_tool(server_name, tool_name, call.get("arguments") or {})
                results.append(f"[{full_name}] {text}")
            except Exception as e:
                results.append(f"[{full_name}] 调用失败：{e}")
        return {"tool_result": "\n".join(results)}


    def load_memory_node(state: AskDataState, config) -> dict:
        """
        记忆加载节点（每轮入口）：
        1. 短期记忆：读取本会话历史，格式化成文本
        2. 长期记忆：读取用户偏好事实文本
        """
        thread_id = _get_thread_id(config)
        conversation_context = conv_memory.recent_context(thread_id, n=4)
        user_profile = long_term_memory.load("default")
        return {
            "conversation_context": conversation_context,
            "user_profile": user_profile
        }

    def rewrite_node(state: AskDataState) -> dict:
        """
        指代消解节点（仅在有历史时走此节点）：
        结合历史把"那华南呢"类问题改写成独立完整问题
        LLM失败/空输出时降级使用原问题，不阻塞主链路
        """
        try:
            user_prompt = REWRITE_QUERY_USER_TPL.format(
                history=state["conversation_context"],
                question=state["user_question"]
            )
            resp = model_client.llm_invoke(
                prompt=user_prompt,
                system_prompt=REWRITE_QUERY_SYSTEM_PROMPT
            )
            rewritten = (resp.content or "").strip()
        except Exception:
            rewritten = ""
        return {"rewritten_question": rewritten or state["user_question"]}

    def route_node(state: AskDataState) -> dict:
        """意图路由节点：基于消解后的问题判断chat还是data查询（首轮无该键，用.get防御）"""
        effective = state.get("rewritten_question") or state["user_question"]
        return {"route": route_question(model_client, effective)}

    def chat_node(state: AskDataState) -> dict:
        """闲聊回答节点，直接返回自然语言答案"""
        return {"answer": answer_directly(model_client, state["user_question"],_skill_instructions(state))}

    def retrieve_node(state: AskDataState) -> dict:
        """检索节点：基于消解后的问题召回schema字段、表关联、字段样例值"""
        effective = state.get("rewritten_question") or state["user_question"]
        table_scope = None
        if skill_registry is not None and state.get("active_skill"):
            skill = skill_registry.get(state["active_skill"])
            table_scope = skill.table_scope if skill else None
        field_docs, relations, value_samples = retrieve_schema_context(
            hybrid, executor, effective,table_scope=table_scope
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
        两路都注入用户长期偏好（如"金额默认指实付金额"）
        """
        question = state.get("rewritten_question") or state["user_question"]
        profile = state.get("user_profile") or ""
        skill_instructions = _skill_instructions(state)
        if state["attempts"] == 0:
            sql = generate_sql(
                question=question,
                field_docs=state["field_docs"],
                relations=state["relations"],
                value_samples=state["value_samples"],
                model_client=model_client,
                user_profile=profile,
                skill_instructions=skill_instructions
            )
        else:
            sql = generate_repair_sql(
                question=question,
                field_docs=state["field_docs"],
                relations=state["relations"],
                value_samples=state["value_samples"],
                old_sql=state["sql"],
                error_msg=state["last_error"],
                model_client=model_client,
                user_profile=profile,
                skill_instructions=skill_instructions
            )
        return {"sql": sql}

    def execute_node(state: AskDataState) -> dict:
        """
        执行SQL节点：捕获执行异常，写入状态
        attempts_log是普通键（覆盖语义），这里读改写返回完整新列表：
        单轮内多次尝试（失败→修复→成功）的记录完整保留
        """
        sql_text = state["sql"]
        prev_log = list(state.get("attempts_log") or [])
        try:
            query_result = executor.execute(sql_text)
            return {
                "query_result": query_result,
                "success": True,
                "last_error": None,
                "attempts_log": prev_log + [(sql_text, None)]
            }
        except SqlExecuteError as e:
            return {
                "query_result": None,
                "success": False,
                "last_error": e.error_msg,
                "attempts_log": prev_log + [(sql_text, e.error_msg)]
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
        if state.get("route") == "tool":
            user_prompt = TOOL_ANSWER_USER_TPL.format(
                user_question=state["user_question"],
                tool_result=state.get("tool_result") or "工具无返回结果"
            )
            resp = model_client.llm_invoke(
                prompt=user_prompt,
                system_prompt=TOOL_ANSWER_SYSTEM_PROMPT
            )
            return {"answer": (resp.content or "").strip()}        
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

    def save_memory_node(state: AskDataState, config) -> dict:
        """
        记忆保存节点（每轮收尾，所有路径汇聚于此）：
        1. 短期记忆：追加本轮对话（问题/SQL/回答）并落盘
        2. 长期记忆：规则预筛触发后，LLM提炼偏好事实写入长期存储
        3. 滚动摘要：历史超过阈值时压缩旧轮次
        所有子步骤失败都不阻塞主流程
        """
        thread_id = _get_thread_id(config)
        question = state["user_question"]
        sql = state.get("sql")
        answer = state.get("answer") or ""

        # 1. 短期记忆：每轮存两条记录（用户问题 + 助手回答），
        # 遵循ConversationMemory的字段约定：user记录存content，assistant记录存answer
        try:
            conv_memory.append_turn(thread_id, {
                "role": "user",
                "content": question,
                "sql": None,
                "answer": None
            })
            conv_memory.append_turn(thread_id, {
                "role": "assistant",
                "content": answer,
                "sql": sql,
                "answer": answer
            })
        except Exception:
            pass

        # 2. 长期记忆：规则预筛 + LLM提炼
        if _has_memory_trigger(question):
            try:
                user_prompt = MEMORY_EXTRACT_USER_TPL.format(
                    user_question=question,
                    answer=answer,
                    sql=sql or "无"
                )
                resp = model_client.llm_invoke(
                    prompt=user_prompt,
                    system_prompt=MEMORY_EXTRACT_SYSTEM_PROMPT
                )
                fact = (resp.content or "").strip()
                if fact and fact.upper() != "NONE":
                    long_term_memory.add("default", fact)
            except Exception:
                pass

        # 3. 滚动摘要检查
        try:
            conv_memory.maybe_summarize(
                thread_id,
                _llm_summarize,
                keep=4,
                threshold=8,
                summary_system_prompt=MEMORY_SUMMARY_SYSTEM_PROMPT,
                summary_user_tpl=MEMORY_SUMMARY_USER_TPL
            )
        except Exception:
            pass

        return {}

    return {
        "load_memory_node": load_memory_node,
        "route_node": route_node,
        "rewrite_node": rewrite_node,
        "chat_node": chat_node,
        "retrieve_node": retrieve_node,
        "generate_sql_node": generate_sql_node,
        "execute_node": execute_node,
        "repair_node": repair_node,
        "answer_node": answer_node,
        "save_memory_node": save_memory_node,
        "skill_node":skill_node,
        "tool_plan_node": tool_plan_node, 
        "tool_execute_node": tool_execute_node,
    }
