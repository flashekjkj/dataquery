"""
可观测性基础设施测试：trace 生命周期/幂等、span 记录、回调、指标、中间件。

运行：python -m unittest test.test_observability -v 或 python test/test_observability.py
"""
from __future__ import annotations
import json
import sys
import tempfile
import unittest
from pathlib import Path

BASE_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_BACKEND))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from observability.tracer import (
    configure_trace_dir, start_trace, end_trace, current_trace, record_span,
    GraphCallbackHandler, trace_var,
)
from observability.middleware import ObservabilityMiddleware
from observability.context import request_id_var


class TraceTests(unittest.TestCase):

    def test_trace_span_lifecycle(self):
        trace = start_trace(session_id="sess-1")
        self.assertIs(current_trace(), trace)
        record_span("llm:route", "llm", 120.5, metadata={"tokens": 10})
        snapshot = end_trace()
        self.assertIsNone(current_trace())
        self.assertEqual(len(snapshot["spans"]), 1)
        span = snapshot["spans"][0]
        self.assertEqual(span["name"], "llm:route")
        self.assertAlmostEqual(span["duration_ms"], 120.5, delta=0.01)

    def test_end_trace_idempotent(self):
        """中间件与路由可能各调用一次 end_trace：第二次应为 no-op"""
        trace = start_trace()
        first = end_trace()
        second = end_trace()
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_trace_flush_to_jsonl(self):
        """span 按 JSONL 落盘，每行含 trace_id 便于关联"""
        with tempfile.TemporaryDirectory() as tmp:
            configure_trace_dir(Path(tmp))
            # 测试结束后把落盘目录复位为 None，避免后续用例写已删除的临时目录
            self.addCleanup(configure_trace_dir, None)
            trace = start_trace(session_id="sess-x")
            record_span("answer_node", "node", 30.0)
            end_trace()
            files = list(Path(tmp).glob("traces-*.jsonl"))
            self.assertEqual(len(files), 1)
            line = json.loads(files[0].read_text(encoding="utf-8").strip())
            self.assertEqual(line["trace_id"], trace.trace_id)
            self.assertEqual(line["name"], "answer_node")
            self.assertEqual(line["session_id"], "sess-x")

    def test_callback_handler_records_node_spans(self):
        """模拟 LangGraph 的 on_chain_start/end 回调序列"""
        trace = start_trace()
        handler = GraphCallbackHandler()
        handler.on_chain_start({"name": "route_node"}, {}, run_id="r1")
        handler.on_chain_end({}, run_id="r1")
        # ChannelWrite 等内部事件被过滤
        handler.on_chain_start({"name": "ChannelWrite<sql>"}, {}, run_id="r2")
        handler.on_chain_end({}, run_id="r2")
        snapshot = end_trace()
        names = [s["name"] for s in snapshot["spans"]]
        self.assertIn("route_node", names)
        self.assertNotIn("ChannelWrite<sql>", names)

    def test_trace_var_isolation(self):
        """新 trace 不继承旧 trace（contextvar 每次显式 set）"""
        start_trace()
        end_trace()
        trace2 = start_trace()
        self.assertIs(current_trace(), trace2)
        end_trace()


class MiddlewareTests(unittest.TestCase):

    def setUp(self):
        app = FastAPI()

        @app.get("/ping")
        def ping():
            return {"ok": True}

        app.add_middleware(ObservabilityMiddleware)
        self.client = TestClient(app)

    def test_request_id_passthrough_and_generation(self):
        # 客户端传入的 X-Request-ID 原样透传
        resp = self.client.get("/ping", headers={"X-Request-ID": "my-rid-123"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["X-Request-ID"], "my-rid-123")
        # 未传入时自动生成
        resp2 = self.client.get("/ping")
        self.assertTrue(resp2.headers["X-Request-ID"])

    def test_unhandled_exception_returns_request_id(self):
        app = FastAPI()

        @app.get("/boom")
        def boom():
            raise RuntimeError("boom")

        app.add_middleware(ObservabilityMiddleware)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/boom", headers={"X-Request-ID": "rid-boom"})
        self.assertEqual(resp.status_code, 500)
        self.assertIn("rid-boom", resp.json()["detail"])


if __name__ == "__main__":
    unittest.main()
