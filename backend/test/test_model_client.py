"""
ModelClient 可靠性测试：重试/退避、错误分类、JSON模式降级、流式、用量记录。

不依赖真实 API：通过构造器注入 FakeOpenAI 客户端（依赖注入设计使单测可行）。
运行：python -m unittest test.test_model_client -v  或  python test/test_model_client.py
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

BASE_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_BACKEND))

import httpx
import openai
from prometheus_client import REGISTRY

from app.model_client import ModelClient, LLMCallError, classify_llm_error
from observability.metrics import record_llm_call


def _req():
    return httpx.Request("POST", "http://fake.local")


def _api_error(cls, status: int):
    """构造带 httpx Response 的 OpenAI API 异常"""
    resp = httpx.Response(status, request=_req())
    return cls("fake api error", response=resp, body=None)


def _completion(content: str = "你好", prompt_tokens: int = 10, completion_tokens: int = 5):
    """构造一次 chat.completions 的返回对象"""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


class FakeCompletions:
    """按脚本序列返回结果或抛异常的 chat.completions 假对象"""

    def __init__(self, items):
        self.items = list(items)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.items.pop(0) if self.items else None
        if item is None:
            return _completion()
        if isinstance(item, BaseException):
            raise item
        return item


class FakeOpenAI:
    def __init__(self, completion_items=None):
        self.chat = SimpleNamespace(completions=FakeCompletions(completion_items or []))


def _client(items, retries: int = 3) -> ModelClient:
    c = ModelClient(mock=False, client=FakeOpenAI(items))
    c.max_retries = retries
    c.retry_base_delay = 0.001   # 测试中退避几乎为0，不拖慢用例
    c.model_id = "fake-model"   # 固定指标 label，便于断言
    return c


class RetryTests(unittest.TestCase):

    def test_retry_then_success(self):
        """可重试错误（超时×2）→ 第三次成功：重试2次，指标如实记录"""
        client = _client([openai.APITimeoutError(request=_req()),
                          openai.APITimeoutError(request=_req()),
                          _completion()])
        resp = client.llm_invoke("问题", operation="t_retry_ok")
        self.assertEqual(resp.content, "你好")
        self.assertEqual(len(client._client.chat.completions.calls), 3)
        retries = REGISTRY.get_sample_value(
            "askdata_llm_retries_total", {"operation": "t_retry_ok"})
        self.assertEqual(retries, 2)
        calls = REGISTRY.get_sample_value(
            "askdata_llm_calls_total",
            {"operation": "t_retry_ok", "model": "fake-model", "status": "success"})
        self.assertEqual(calls, 1)

    def test_retry_exhausted_raises(self):
        """全部可重试错误 → 重试耗尽抛 LLMCallError，attempts 计数正确"""
        client = _client([openai.APITimeoutError(request=_req())] * 3, retries=3)
        with self.assertRaises(LLMCallError) as ctx:
            client.llm_invoke("问题", operation="t_retry_exhaust")
        self.assertEqual(ctx.exception.attempts, 3)
        self.assertEqual(ctx.exception.kind, "timeout")
        self.assertEqual(len(client._client.chat.completions.calls), 3)

    def test_non_retriable_fails_fast(self):
        """鉴权失败（不可重试）→ 只尝试1次立即抛出"""
        client = _client([_api_error(openai.AuthenticationError, 401)])
        with self.assertRaises(LLMCallError) as ctx:
            client.llm_invoke("问题", operation="t_auth_fail")
        self.assertFalse(ctx.exception.retriable)
        self.assertEqual(ctx.exception.kind, "auth")
        self.assertEqual(len(client._client.chat.completions.calls), 1)

    def test_classify_taxonomy(self):
        """错误分类表：限流/服务端5xx可重试，参数/鉴权不可重试"""
        cases = [
            (openai.RateLimitError("m", response=httpx.Response(429, request=_req()), body=None), True),
            (openai.InternalServerError("m", response=httpx.Response(500, request=_req()), body=None), True),
            (openai.APIConnectionError(request=_req()), True),
            (_api_error(openai.BadRequestError, 400), False),
            (_api_error(openai.PermissionDeniedError, 403), False),
            (_api_error(openai.NotFoundError, 404), False),
        ]
        for exc, retriable in cases:
            err = classify_llm_error(exc)
            self.assertEqual(err.retriable, retriable, f"{type(exc).__name__} 分类错误")


class JsonModeTests(unittest.TestCase):

    def test_json_mode_fallback_on_bad_request(self):
        """json_object 被服务端拒绝 → 自动降级普通模式重试一次并成功"""
        client = _client([_api_error(openai.BadRequestError, 400), _completion()])
        resp = client.llm_invoke("问题", operation="t_json_fb", json_mode=True)
        self.assertEqual(resp.content, "你好")
        calls = client._client.chat.completions.calls
        self.assertEqual(len(calls), 2)
        # 第一次带 response_format，第二次降级后不带
        self.assertEqual(calls[0]["response_format"], {"type": "json_object"})
        self.assertNotIn("response_format", calls[1])

    def test_json_mode_happy_path(self):
        client = _client([_completion()])
        resp = client.llm_invoke("问题", operation="t_json_ok", json_mode=True)
        self.assertEqual(client._client.chat.completions.calls[0]["response_format"],
                         {"type": "json_object"})
        self.assertEqual(resp.usage["input_tokens"], 10)


class StreamTests(unittest.TestCase):

    def test_stream_yields_tokens_and_records_usage(self):
        """流式：逐 token 产出；末 chunk 携带 usage 时如实记录"""
        chunks = [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="你"))], usage=None),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="好"))], usage=None),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="")),
                                     ], usage=SimpleNamespace(prompt_tokens=8, completion_tokens=2, total_tokens=10)),
        ]
        client = _client([])
        client._client.chat.completions.create = lambda **kw: iter(chunks)
        out = list(client.llm_stream("问题", operation="t_stream"))
        self.assertEqual("".join(out), "你好")
        tokens = REGISTRY.get_sample_value(
            "askdata_llm_tokens_total", {"operation": "t_stream", "direction": "output"})
        self.assertEqual(tokens, 2)

    def test_stream_connection_error_raises(self):
        """流式首字节前连接失败 → 抛 LLMCallError（流式不自动重试，见实现注释）"""
        client = _client([])
        client._client.chat.completions.create = lambda **kw: (_ for _ in ()).throw(
            openai.APIConnectionError(request=_req()))
        with self.assertRaises(LLMCallError):
            list(client.llm_stream("问题", operation="t_stream_err"))


class MockModeTests(unittest.TestCase):

    def test_mock_mode_unchanged(self):
        """mock 模式行为回归：内容格式、工具调用、流式均与改造前一致"""
        client = ModelClient(mock=True)
        resp = client.llm_invoke("销售额", operation="t_mock")
        self.assertIn("模拟SQL", resp.content)
        tools = client.chat_with_tools("帮我计算 3*5")
        self.assertEqual(tools["tool_calls"][0]["name"], "calculator__calculate")
        out = "".join(client.llm_stream("你好"))
        self.assertIn("Mock LLM", out)


class UsageRecordingTests(unittest.TestCase):

    def test_usage_recorded_on_failure_too(self):
        """失败调用也要有指标（错误状态），这是告警与容量评估的基础"""
        client = _client([openai.APITimeoutError(request=_req())], retries=1)
        with self.assertRaises(LLMCallError):
            client.llm_invoke("问题", operation="t_fail_metric")
        err_calls = REGISTRY.get_sample_value(
            "askdata_llm_calls_total",
            {"operation": "t_fail_metric", "model": "fake-model", "status": "error"})
        self.assertEqual(err_calls, 1)


if __name__ == "__main__":
    unittest.main()
