"""有界串行协作：规划 → 查询子图 → 独立审核 → 汇总。

子任务不写会话记忆；仅父图在结束时保存一次用户对话。
"""
import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from app.clock import business_now

from agent.contracts import TaskPlan, QueryTaskResult, parse_contract
from agent.specialists.query_agent import build_query_agent
from agent.specialists.review_agent import review_task
from data.database import SCHEMA, RELATIONS

logger = logging.getLogger(__name__)

from agent.prompts import PLAN_PROMPT, SUMMARY_PROMPT


def should_collaborate(question, mode):
    if mode == "multi":
        return True
    if mode == "single":
        return False
    # 无模型开销的保守入口；也可在 UI/API 显式选择 multi。
    return bool(re.search(r"分析|归因|为什么|原因|贡献|分别|同时|并且|以及|并找|对比.*再", question))


def build_collaboration_nodes(client, nodes, max_attempts=3, skill_registry=None):
    query_agent = build_query_agent(nodes, max_attempts)

    def skill_context(state):
        skill = skill_registry.get(state["active_skill"]) if skill_registry and state.get("active_skill") else None
        return skill.model_dump() if skill else None

    def plan_node(state):
        question = state.get("rewritten_question") or state["user_question"]
        if not should_collaborate(question, state.get("execution_mode", "auto")):
            return {"collaboration_status": "single"}
        try:
            response = client.llm_invoke(prompt=json.dumps({
                "question": question, "user_profile": state.get("user_profile", ""),
                "now": business_now(),
                "schema": SCHEMA, "relations": RELATIONS,
                "skill": skill_context(state),
            }, ensure_ascii=False), system_prompt=PLAN_PROMPT,
                operation="supervisor_plan")
            plan = parse_contract(TaskPlan, response.content)
        except Exception:
            logger.exception("Supervisor planning failed")
            return {"collaboration_status": "failed", "success": False,
                    "answer": "任务规划失败：模型未返回有效计划或服务不可用，请重试或选择单智能体模式。"}
        return {"task_plan": plan.model_dump(), "task_results": [], "task_index": 0,
                "review_round": 0, "review_feedback": [], "current_task_result": None,
                "collaboration_status": "clarification" if plan.clarification else "running",
                "answer": plan.clarification, "success": False}

    def query_node(state, config=None):
        task = state["task_plan"]["tasks"][state["task_index"]]
        completed = {r["task_id"]: r for r in state["task_results"]}
        missing = [key for key in task["depends_on"] if completed[key]["status"] != "approved"]
        base = {"task_id": task["task_id"], "question": task["question"],
                "expected_output": task["expected_output"], "revision": state["review_round"],
                "sql": None, "query_result": None, "attempts": [], "retrieved_docs": [],
                "reviews": (state.get("current_task_result") or {}).get("reviews", [])}
        if missing:
            return {"current_task_result": {**base, "status": "skipped",
                    "error": "前置任务未通过审核：" + ", ".join(missing)}}
        context = {
            "question": task["question"], "expected_output": task["expected_output"],
            "assumptions": state["task_plan"]["assumptions"],
            "dependencies": [completed[key] for key in task["depends_on"]],
            "review_feedback": state["review_feedback"],
        }
        try:
            # 子图继承父图 config 中的 callbacks，保证子图节点也进 trace/指标；
            # 递归上限收窄到子图规模（32）
            sub_config = {"recursion_limit": 32}
            if config is not None:
                sub_config = {**config, "recursion_limit": 32}
            result = query_agent.invoke({
                "user_question": json.dumps(context, ensure_ascii=False),
                "attempts": 0, "attempts_log": [],
                "user_profile": state.get("user_profile", ""),
                "active_skill": state.get("active_skill"),
            }, config=sub_config)
            output = {**base, "status": "queried" if result["success"] else "failed",
                      "sql": result.get("sql"),
                      "query_result": result["query_result"].model_dump(mode="json") if result.get("query_result") else None,
                      "attempts": [{"sql": s, "error": e} for s, e in result["attempts_log"]],
                      "retrieved_docs": [d.doc_id for d in result.get("field_docs", [])],
                      "error": result.get("last_error")}
        except Exception:
            logger.exception("Query agent failed for %s", task["task_id"])
            output = {**base, "status": "failed", "error": "查询 Agent 执行失败，请重试"}
        # 返工历史累积；结果仅保留最新版本，防止旧结果被当作有效证据。
        previous = state.get("current_task_result")
        if state["review_round"] and previous:
            output["attempts"] = previous["attempts"] + output["attempts"]
        return {"current_task_result": QueryTaskResult.model_validate(output).model_dump(mode="json")}

    def review_node(state):
        result = dict(state["current_task_result"])
        if result["status"] != "queried":
            return {"current_task_result": result, "review_feedback": []}
        try:
            decision = review_task(client, {
                "original_question": state.get("rewritten_question") or state["user_question"],
                "task": state["task_plan"]["tasks"][state["task_index"]],
                "assumptions": state["task_plan"]["assumptions"],
                "user_profile": state.get("user_profile", ""),
                "schema": SCHEMA, "relations": RELATIONS, "result": result,
                "skill": skill_context(state),
            })
        except Exception:
            logger.exception("Review agent failed for %s", result["task_id"])
            result.update(status="unverified", error="审核失败，结果未被采信")
            return {"current_task_result": result, "review_feedback": []}
        result["reviews"] = result["reviews"] + [decision.model_dump()]
        if decision.verdict == "clarify":
            result["status"] = "unverified"
            return {"current_task_result": result, "collaboration_status": "clarification",
                    "answer": decision.clarification, "review_feedback": []}
        if decision.verdict == "approve":
            result["status"] = "approved"
        elif state["review_round"] < 1:
            result["status"] = "revise"
        else:
            result.update(status="unverified", error="返工后仍未通过审核")
        return {"current_task_result": result, "review_feedback": decision.issues}

    def collect_node(state):
        return {"task_results": state["task_results"] + [state["current_task_result"]],
                "task_index": state["task_index"] + 1, "review_round": 0,
                "review_feedback": [], "current_task_result": None}

    def revise_node(state):
        return {"review_round": state["review_round"] + 1}

    def summarize_node(state):
        if state["collaboration_status"] == "clarification":
            return {"success": False}
        results = state["task_results"]
        approved = [r for r in results if r["status"] == "approved"]
        status = "completed" if len(approved) == len(results) else "partial" if approved else "failed"
        # 未审核结果不传给汇总模型。
        payload = {"question": state.get("rewritten_question") or state["user_question"],
                   "user_profile": state.get("user_profile", ""), "skill": skill_context(state),
                   "assumptions": state["task_plan"]["assumptions"], "evidence": approved,
                   "missing": [{"task_id": r["task_id"], "error": r.get("error") or r["status"]}
                               for r in results if r["status"] != "approved"]}
        if not approved:
            answer = "本次分析没有通过审核的查询结果，无法给出可靠结论。请查看任务记录后重试。"
        else:
            try:
                response = client.llm_invoke(prompt=json.dumps(payload, ensure_ascii=False),
                                             system_prompt=SUMMARY_PROMPT,
                                             operation="supervisor_summary")
                answer = (response.content or "").strip()
                if not answer:
                    raise ValueError("empty summary")
            except Exception:
                logger.exception("Supervisor summarization failed")
                return {"collaboration_status": "failed", "success": False,
                        "answer": "查询已完成，但分析汇总失败。可查看任务结果后重试。"}
        if status == "partial":
            answer = "部分任务未完成，以下结论仅覆盖审核通过的部分。\n" + answer
        return {"collaboration_status": status, "success": status == "completed", "answer": answer}

    return {"supervisor_node": plan_node, "query_agent_node": query_node,
            "review_agent_node": review_node, "collect_task_node": collect_node,
            "revise_task_node": revise_node, "supervisor_answer_node": summarize_node}
