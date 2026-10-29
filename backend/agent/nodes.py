from __future__ import annotations
import json
from typing import List, Optional

from agent.state import AskDataState
from sql.executor import SqlExecuteError
from sql.generator import generate_sql, generate_repair_sql
from sql.sql_prompts import MEMORY_EXTRACT_SYSTEM_PROMPT, MEMORY_EXTRACT_USER_TPL, MEMORY_SUMMARY_SYSTEM_PROMPT, MEMORY_SUMMARY_USER_TPL, REWRITE_QUERY_SYSTEM_PROMPT, REWRITE_QUERY_USER_TPL, TOOL_ANSWER_SYSTEM_PROMPT, TOOL_ANSWER_USER_TPL, TOOL_PLAN_SYSTEM_PROMPT
from app.model_client import LLMCallError
from observability import logger
from observability.metrics import record_sql_execution

from agent.query_context import (
    route_question, answer_directly, retrieve_schema_context,
    format_query_result_for_llm, generate_natural_answer, _has_memory_trigger,
)

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
        resp = model_client.llm_invoke(prompt=user_prompt, system_prompt=system_prompt,
                                       operation="memory_summary")
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
        if skill:
            logger.info("技能命中：{}", skill.name)
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
            return {"tool_calls":[], "degraded": True}
        effective = state.get("rewritten_question") or state["user_question"]
        allowed = _allowed_mcp_tools(state)
        tools_schema = mcp_manager.to_openai_tools(allowed_tools=allowed)
        if not tools_schema:
            return {"tool_calls":[], "degraded": True}
        try:
            resp = model_client.chat_with_tools(
                prompt=effective,
                system_prompt=TOOL_PLAN_SYSTEM_PROMPT,
                tools=tools_schema
            )
        except Exception as e:
            # 工具规划LLM失败 → 降级闲聊（下游 has_tool_call_router 走 chat_node）
            logger.warning("工具规划LLM调用失败，降级闲聊：{}", e)
            return {"tool_calls":[], "degraded": True}
        return {"tool_calls": resp["tool_calls"], "degraded": not bool(resp["tool_calls"])}

    def tool_execute_node(state: AskDataState) -> dict:
        """
        工具执行节点：依次执行规划好的工具调用，结果拼成文本写入tool_result
        """
        results = []
        tool_success = bool(state.get("tool_calls"))
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
                tool_success = False
                results.append(f"[{full_name}] 调用失败：{e}")
        return {"tool_result": "\n".join(results), "tool_success": tool_success}


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
            # 每轮重置产物；Checkpoint 不能把上一轮 SQL/工具结果带入本轮。
            "rewritten_question": None, "sql": None, "query_result": None,
            "success": False, "answer": "", "last_error": None, "llm_error": None,
            "tool_success": False, "degraded": False,
            "field_docs": [], "relations": [], "value_samples": {},
            "tool_calls": [], "tool_result": None, "attempts": 0, "attempts_log": [],
            "task_plan": None, "task_results": [], "task_index": 0,
            "review_round": 0, "review_feedback": [], "current_task_result": None,
            "collaboration_status": "single",
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
                system_prompt=REWRITE_QUERY_SYSTEM_PROMPT,
                operation="rewrite",
            )
            rewritten = (resp.content or "").strip()
        except Exception as e:
            logger.warning("指代消解LLM调用失败，降级使用原问题：{}", e)
            rewritten = ""
        return {"rewritten_question": rewritten or state["user_question"]}

    def route_node(state: AskDataState) -> dict:
        """意图路由节点：基于消解后的问题判断chat还是data查询（首轮无该键，用.get防御）"""
        effective = state.get("rewritten_question") or state["user_question"]
        route = route_question(model_client, effective)
        logger.info("意图路由：{} -> {}", effective[:40], route)
        return {"route": route}

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

        失败治理：LLM 调用重试耗尽后不向上抛异常（否则整图失败→HTTP 500），
        而是把故障写入状态（sql置空 + llm_error置位），
        由 execute_node 的防御分支与 after_execute_router 终止修复循环
        （修复同样依赖 LLM，继续循环只会浪费调用预算），
        最终 answer_node 给用户诚实报错。
        """
        question = state.get("rewritten_question") or state["user_question"]
        profile = state.get("user_profile") or ""
        skill_instructions = _skill_instructions(state)
        prev_log = list(state.get("attempts_log") or [])
        try:
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
        except LLMCallError as e:
            msg = f"模型服务不可用（{e.kind}，已尝试{e.attempts}次）"
            logger.error("SQL生成LLM调用失败：{}", e)
            return {
                "sql": "",
                "llm_error": msg,
                "attempts_log": prev_log + [("", msg)],
            }
        except Exception as e:
            msg = f"SQL生成异常：{e}"
            logger.error("SQL生成异常：{}", e)
            return {
                "sql": "",
                "llm_error": msg,
                "attempts_log": prev_log + [("", msg)],
            }
        return {"sql": sql}

    def execute_node(state: AskDataState) -> dict:
        """
        执行SQL节点：捕获执行异常，写入状态
        attempts_log是普通键（覆盖语义），这里读改写返回完整新列表：
        单轮内多次尝试（失败→修复→成功）的记录完整保留

        防御：SQL为空（LLM故障标记）时不再调用执行器，直接失败，
        错误信息取 llm_error，供 answer_node 诚实告知用户。
        """
        sql_text = state["sql"] or ""
        prev_log = list(state.get("attempts_log") or [])
        if not sql_text:
            err = state.get("llm_error") or "SQL生成结果为空"
            record_sql_execution(ok=False)
            return {
                "query_result": None,
                "success": False,
                "last_error": err,
                "attempts_log": prev_log + [("", err)]
            }
        try:
            query_result = executor.execute(sql_text)
            record_sql_execution(ok=True)
            return {
                "query_result": query_result,
                "success": True,
                "last_error": None,
                "attempts_log": prev_log + [(sql_text, None)]
            }
        except SqlExecuteError as e:
            record_sql_execution(ok=False)
            logger.info("SQL执行失败（第{}次尝试）：{}", len(prev_log) + 1, e.error_msg[:120])
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
            try:
                resp = model_client.llm_invoke(
                    prompt=user_prompt,
                    system_prompt=TOOL_ANSWER_SYSTEM_PROMPT,
                    operation="tool_answer",
                )
                return {"answer": (resp.content or "").strip()}
            except Exception as e:
                logger.error("工具回答LLM调用失败：{}", e)
                # 降级：直接展示工具原始结果，保证工具链路仍有产出
                raw = state.get("tool_result") or ""
                return {"answer": f"模型服务暂时不可用，以下是工具原始结果：\n{raw}", "degraded": True}
        if state["success"]:
            try:
                ans = generate_natural_answer(
                    model_client,
                    state["user_question"],
                    state["sql"],
                    state["query_result"],
                )
            except Exception as e:
                logger.error("结果整理LLM调用失败：{}", e)
                # 降级：SQL 执行成功但无法润色，直接给用户可读性差但真实的结果
                rows = state["query_result"].rows[:5] if state["query_result"] else []
                ans = (
                    "查询已成功执行，但模型服务暂时不可用，无法生成自然语言总结。"
                    f"SQL：{state['sql']}\n结果前5行：{rows}"
                )
                return {"answer": ans, "degraded": True}
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
                "answer": answer,
                "collaboration_status": state.get("collaboration_status", "single"),
                "task_plan": state.get("task_plan"),
                "task_results": state.get("task_results", []),
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
                    system_prompt=MEMORY_EXTRACT_SYSTEM_PROMPT,
                    operation="memory_extract",
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
