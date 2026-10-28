"""离线行为回归：python -m unittest discover -s test -p test_collaboration.py -v"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import duckdb
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.contracts import TaskPlan, ReviewDecision, parse_contract
from agent.graph import build_graph
from agent.runtime import ask_data_agent
from agent.supervisor import PLAN_PROMPT, SUMMARY_PROMPT
from agent.specialists.review_agent import REVIEW_PROMPT
from app.schemas import LLMResponse
from memory.conversation import ConversationMemory
from memory.long_term import LongTermMemoryStore
from retrieval.service import build_hybrid_index
from server.routes import router
from test.test_graph import FakeClient


def task(key="T1", depends_on=None):
    return {"task_id": key, "question": "统计客户数量", "expected_output": "客户数",
            "depends_on": depends_on or []}


def approve():
    return {"verdict": "approve", "issues": [], "clarification": ""}


class TeamClient(FakeClient):
    def __init__(self, plan=None, reviews=None, sql=None):
        super().__init__(sql or ["SELECT COUNT(*) AS total FROM customers"])
        self.plan = plan or {"clarification": "", "assumptions": [], "tasks": [task()]}
        self.reviews = list(reviews or [approve()])
        self.plan_calls = 0
        self.review_calls = 0
        self.summary_payloads = []
        self.query_prompts = []
        self.fail_review = False

    def llm_invoke(self, prompt, system_prompt=None, **kwargs):
        if system_prompt == PLAN_PROMPT:
            self.plan_calls += 1
            return LLMResponse(content=self.plan if isinstance(self.plan, str) else json.dumps(self.plan), usage={})
        if system_prompt == REVIEW_PROMPT:
            if self.fail_review:
                raise RuntimeError("offline reviewer failure")
            review = self.reviews[min(self.review_calls, len(self.reviews) - 1)]
            self.review_calls += 1
            return LLMResponse(content=json.dumps(review), usage={})
        if system_prompt == SUMMARY_PROMPT:
            self.summary_payloads.append(json.loads(prompt))
            return LLMResponse(content="依据[T1]，客户总数为2。", usage={})
        self.query_prompts.append(prompt)
        return super().llm_invoke(prompt, system_prompt)


class CollaborationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db_path = self.root / "test.duckdb"
        with duckdb.connect(str(self.db_path)) as db:
            db.execute("CREATE TABLE customers(customer_id INTEGER)")
            db.execute("INSERT INTO customers VALUES (1), (2)")
        self.memory = ConversationMemory(self.root)

    def build(self, client):
        from sql.executor import SQLExecutor
        return build_graph(client, build_hybrid_index(client), SQLExecutor(str(self.db_path)),
                           self.memory, LongTermMemoryStore(self.root))

    def ask(self, client, question="统计客户", mode="multi"):
        return ask_data_agent(self.build(client), question, "session", execution_mode=mode)

    def test_multiple_tasks_are_isolated_and_saved_once(self):
        client = TeamClient(plan={"tasks": [task(), task("T2", ["T1"])]})
        result = self.ask(client)
        self.assertTrue(result["success"])
        self.assertEqual(result["collaboration_status"], "completed")
        self.assertEqual([r["task_id"] for r in result["task_results"]], ["T1", "T2"])
        self.assertEqual([len(r["attempts"]) for r in result["task_results"]], [1, 1])
        self.assertIsNone(result["final_sql"])
        turns = self.memory.load("session")["turns"]
        self.assertEqual(len(turns), 2)
        self.assertEqual(len(turns[-1]["task_results"]), 2)
        self.assertTrue(any('dependencies' in p and 'T1' in p for p in client.query_prompts))

    def test_review_feedback_causes_real_requery(self):
        client = TeamClient(reviews=[{"verdict": "revise", "issues": ["客户应去重"]}, approve()])
        result = self.ask(client)
        self.assertTrue(result["success"])
        self.assertEqual(client.generate_calls, 2)
        record = result["task_results"][0]
        self.assertEqual(len(record["attempts"]), 2)
        self.assertEqual(len(record["reviews"]), 2)
        self.assertTrue(any("客户应去重" in p for p in client.query_prompts))

    def test_repeated_rejection_is_bounded_and_skips_dependents(self):
        client = TeamClient(plan={"tasks": [task(), task("T2", ["T1"])]},
                            reviews=[{"verdict": "revise", "issues": ["口径不符"]}])
        result = self.ask(client)
        self.assertFalse(result["success"])
        self.assertEqual(client.generate_calls, 2)
        self.assertEqual([r["status"] for r in result["task_results"]], ["unverified", "skipped"])
        self.assertEqual(client.summary_payloads, [])

    def test_query_sql_repair_remains_available(self):
        client = TeamClient(sql=["SELECT nope FROM customers", "SELECT COUNT(*) FROM customers"])
        result = self.ask(client)
        self.assertTrue(result["success"])
        self.assertEqual(len(result["task_results"][0]["attempts"]), 2)

    def test_sql_failure_stops_after_three_attempts(self):
        client = TeamClient(sql=["SELECT nope FROM customers"])
        result = self.ask(client)
        self.assertFalse(result["success"])
        self.assertEqual(client.generate_calls, 3)
        self.assertEqual(client.review_calls, 0)

    def test_safety_rejection_does_not_retry(self):
        client = TeamClient(sql=["DROP TABLE customers"])
        result = self.ask(client)
        self.assertFalse(result["success"])
        self.assertEqual(client.generate_calls, 1)

    def test_partial_results_exclude_unverified_evidence(self):
        client = TeamClient(plan={"tasks": [task(), task("T2")]},
                            reviews=[approve(), {"verdict": "revise", "issues": ["错误"]}])
        result = self.ask(client)
        self.assertEqual(result["collaboration_status"], "partial")
        self.assertFalse(result["success"])
        self.assertEqual([r["task_id"] for r in client.summary_payloads[0]["evidence"]], ["T1"])
        self.assertEqual(client.summary_payloads[0]["missing"][0]["task_id"], "T2")

    def test_invalid_plan_and_reviewer_fail_closed(self):
        result = self.ask(TeamClient(plan="not json"))
        self.assertFalse(result["success"])
        client = TeamClient()
        client.fail_review = True
        result = self.ask(client)
        self.assertEqual(result["task_results"][0]["status"], "unverified")
        self.assertFalse(result["success"])

    def test_planner_clarification_does_not_query(self):
        client = TeamClient(plan={"clarification": "按哪个月份？", "tasks": []})
        result = self.ask(client)
        self.assertEqual(result["collaboration_status"], "clarification")
        self.assertEqual(result["answer"], "按哪个月份？")
        self.assertEqual(client.generate_calls, 0)

    def test_three_tasks_with_revisions_finish_within_graph_limit(self):
        client = TeamClient(plan={"tasks": [task(), task("T2"), task("T3")]},
                            reviews=[{"verdict": "revise", "issues": ["需修正"]}])
        result = self.ask(client)
        self.assertFalse(result["success"])
        self.assertEqual(client.generate_calls, 6)
        self.assertEqual(len(result["task_results"]), 3)

    def test_summary_failure_preserves_task_results(self):
        client = TeamClient()
        original = client.llm_invoke

        def invoke(prompt, system_prompt=None, **kwargs):
            if system_prompt == SUMMARY_PROMPT:
                raise RuntimeError("summary service unavailable")
            return original(prompt, system_prompt, **kwargs)

        client.llm_invoke = invoke
        result = self.ask(client)
        self.assertFalse(result["success"])
        self.assertEqual(result["collaboration_status"], "failed")
        self.assertEqual(result["task_results"][0]["status"], "approved")

    def test_review_clarification_stops_remaining_tasks(self):
        client = TeamClient(plan={"tasks": [task(), task("T2")]},
                            reviews=[{"verdict": "clarify", "clarification": "按哪个口径？"}])
        result = self.ask(client)
        self.assertEqual(result["answer"], "按哪个口径？")
        self.assertEqual(client.generate_calls, 1)
        self.assertFalse(result["success"])

    def test_auto_fast_path_and_force_single(self):
        for mode, question in [("auto", "客户有多少"), ("single", "分析客户变化")]:
            client = TeamClient()
            result = self.ask(client, question, mode)
            self.assertEqual(result["collaboration_status"], "single")
            self.assertEqual(client.plan_calls, 0)
            self.assertTrue(result["success"])

    def test_auto_complex_question_enters_collaboration(self):
        result = self.ask(TeamClient(), "分析客户变化", "auto")
        self.assertEqual(result["collaboration_status"], "completed")

    def test_same_session_switching_modes_does_not_leak_outputs(self):
        client = TeamClient()
        graph = self.build(client)
        first = ask_data_agent(graph, "客户数量", "session", execution_mode="single")
        self.assertIsNotNone(first["final_sql"])
        second = ask_data_agent(graph, "分析客户", "session", execution_mode="multi")
        self.assertIsNone(second["final_sql"])
        self.assertEqual(second["attempts"], [])
        client.route_answer = "chat"
        third = ask_data_agent(graph, "你好", "session")
        self.assertEqual(third["task_results"], [])
        self.assertIsNone(third["task_plan"])
        self.assertIsNone(third["query_result"])

    def test_rest_and_stream_interfaces(self):
        app = FastAPI()
        app.include_router(router)
        app.state.graph = self.build(TeamClient())
        with TestClient(app) as http:
            invalid = http.post("/api/chat", json={"question": "test", "execution_mode": "wrong"})
            self.assertEqual(invalid.status_code, 422)
            response = http.post("/api/chat", json={"question": "统计客户", "execution_mode": "multi"})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["success"])
            self.assertEqual(len(response.json()["task_results"]), 1)
            stream = http.post("/api/chat/stream", json={"question": "统计客户", "execution_mode": "multi"})
            events = [json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith("data: ")]
            self.assertFalse(any(event["type"] == "error" for event in events), events)
            self.assertEqual(events[-1]["type"], "done")
            self.assertTrue(any(event.get("node") == "review_agent_node" for event in events))
            self.assertTrue(any(event.get("task_results") for event in events))

    def test_plan_contract_rejects_unsafe_task_graphs(self):
        bad_plans = [
            {"tasks": [task(), task()]},
            {"tasks": [task("T1", ["T2"]), task("T2")]},
            {"tasks": [task(), task("T2"), task("T3"), task()]},
            {"clarification": "哪天？", "tasks": [task()]},
            {"tasks": []},
        ]
        for plan in bad_plans:
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                TaskPlan.model_validate(plan)
        with self.assertRaises(ValueError):
            ReviewDecision.model_validate({"verdict": "revise", "issues": []})
        self.assertEqual(parse_contract(TaskPlan, '```json\n' + json.dumps({"tasks": [task()]}) + '\n```').tasks[0].task_id, "T1")


if __name__ == "__main__":
    unittest.main()
