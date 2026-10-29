"""
模型统一客户端（可靠性版本）。

失败治理策略：
1. 错误分类（classify_llm_error）：把 OpenAI SDK 异常映射为「可重试/不可重试」两类。
   可重试：限流、超时、连接错误、服务端 5xx；不可重试：鉴权、参数错误等（快速失败）。
2. 指数退避重试（tenacity）：wait_random_exponential + 抖动，
   避免「惊群」式重试风暴；单次等待有上限，总等待可控。
3. 结构化输出降级：json_mode 请求被服务端拒绝（bad_request）时自动降级为
   普通文本模式重试一次，兼容不支持 response_format 的 OpenAI 兼容服务。
4. 流式调用不做自动重试：重试会向调用方重复输出已产生的 token，
   首字节前失败的场景由调用方决定是否整体重试（见 llm_stream 注释）。

可观测性：每次调用统一记录耗时/Token/重试次数到 Prometheus 指标与请求级 trace，
operation 标签区分调用用途（route/generate_sql/answer/...），
形成「每个 Agent 步骤的成本与延迟」视图。
"""
from __future__ import annotations

import json
import random
import time
from typing import Any, Dict, Iterator, List, Optional

import openai
from openai import OpenAI
from tenacity import (
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

from app.config import settings
from app.schemas import LLMResponse, EmbeddingResponse, RerankItem, RerankResponse
from observability import logger
from observability.context import accumulate_tokens
from observability.metrics import record_llm_call, rerank_degraded_total
from observability.tracer import current_trace, record_span

# ===================== 异常模型 =====================

class LLMCallError(Exception):
    """
    LLM 调用失败（重试耗尽后抛出）。
    kind：错误类别（rate_limited/timeout/connection/server_error/auth/bad_request/...）
    retriable：是否可重试（分类结果）
    attempts：实际尝试次数（含首次），供上层日志/指标使用
    """

    def __init__(self, message: str, kind: str = "unknown",
                 retriable: bool = False, attempts: int = 1):
        super().__init__(message)
        self.kind = kind
        self.retriable = retriable
        self.attempts = attempts


def classify_llm_error(e: BaseException) -> LLMCallError:
    """
    把 OpenAI SDK 异常映射为带「可重试性」的业务异常。
    原则：瞬时性/服务端问题可重试；客户端确定性错误（参数、鉴权）快速失败。
    """
    if isinstance(e, openai.RateLimitError):
        return LLMCallError(f"触发限流：{e}", kind="rate_limited", retriable=True)
    if isinstance(e, openai.APITimeoutError):
        return LLMCallError(f"请求超时：{e}", kind="timeout", retriable=True)
    if isinstance(e, openai.APIConnectionError):
        return LLMCallError(f"网络连接错误：{e}", kind="connection", retriable=True)
    if isinstance(e, openai.InternalServerError):
        return LLMCallError(f"服务端错误：{e}", kind="server_error", retriable=True)
    if isinstance(e, openai.AuthenticationError):
        return LLMCallError(f"API 鉴权失败：{e}", kind="auth", retriable=False)
    if isinstance(e, openai.PermissionDeniedError):
        return LLMCallError(f"无权限访问：{e}", kind="permission", retriable=False)
    if isinstance(e, openai.NotFoundError):
        return LLMCallError(f"模型/资源不存在：{e}", kind="not_found", retriable=False)
    if isinstance(e, openai.BadRequestError):
        return LLMCallError(f"请求参数错误：{e}", kind="bad_request", retriable=False)
    if isinstance(e, openai.OpenAIError):
        return LLMCallError(f"OpenAI 协议错误：{e}", kind="api_error", retriable=False)
    return LLMCallError(str(e), kind="unknown", retriable=False)


# ===================== 客户端 =====================

class ModelClient:
    def __init__(self, mock: bool = True, client: Optional[OpenAI] = None):
        """
        模型统一客户端
        :param mock: True=模拟模式；False=真实API
        :param client: 可选注入 OpenAI 客户端（依赖注入，便于测试与多实例）
        """
        self.mock = mock
        self._client: Optional[OpenAI] = client
        self._reranker = None   # 本地CrossEncoder精排模型，首次调用时懒加载
        self.max_retries = max(1, settings.LLM_MAX_RETRIES)
        self.retry_base_delay = settings.LLM_RETRY_BASE_DELAY
        self.retry_max_delay = settings.LLM_RETRY_MAX_DELAY
        self.model_id = settings.LLM_MODEL_ID
        if not self.mock and self._client is None:
            self._client = OpenAI(
                api_key=settings.LLM_API_KEY,
                base_url=settings.LLM_BASE_URL,
                timeout=settings.LLM_TIMEOUT,
                max_retries=0,
            )

    # ---------- 内部工具 ----------

    @staticmethod
    def _build_messages(prompt: str, system_prompt: Optional[str]) -> List[Dict[str, str]]:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return messages

    @staticmethod
    def _parse_usage(usage_obj: Any) -> Dict[str, int]:
        """把 OpenAI Usage 对象归一化为 dict（兼容缺失字段与 None）"""
        if usage_obj is None:
            return {}
        get = lambda name: int(getattr(usage_obj, name, None) or 0)
        return {
            "input_tokens": get("prompt_tokens"),
            "output_tokens": get("completion_tokens"),
            "total_tokens": get("total_tokens"),
        }

    def _run_with_retry(self, fn, operation: str, *args, **kwargs):
        """
        tenacity 重试包装：仅对 classify 后 retriable=True 的 LLMCallError 重试。
        返回 (结果, 重试次数)；重试耗尽时抛出带 attempts 计数的 LLMCallError。
        """
        stats = {"attempts": 1}

        def _before_sleep(retry_state):
            # attempt_number 是从 1 起的「已失败尝试」计数，+1 即即将进行的尝试序号
            stats["attempts"] = retry_state.attempt_number + 1
            exc: LLMCallError = retry_state.outcome.exception()
            logger.warning(
                "LLM调用失败（operation={} kind={}），{}秒后重试（第{}/{}次）：{}",
                operation, exc.kind, round(retry_state.next_action.sleep, 1),
                retry_state.attempt_number + 1, self.max_retries, exc,
            )

        retryer = Retrying(
            stop=stop_after_attempt(self.max_retries),
            wait=wait_random_exponential(
                multiplier=self.retry_base_delay, max=self.retry_max_delay),
            retry=retry_if_exception(lambda e: isinstance(e, LLMCallError) and e.retriable),
            before_sleep=_before_sleep,
            reraise=True,
        )
        try:
            result = retryer(fn, *args, **kwargs)
            return result, stats["attempts"] - 1
        except LLMCallError as e:
            e.attempts = stats["attempts"]
            raise

    def _record_call(self, operation: str, ok: bool, duration_ms: float,
                     usage: Optional[Dict[str, int]] = None, retries: int = 0,
                     error: Optional[LLMCallError] = None) -> None:
        """统一记录入口：指标 + Token 累计 + trace span + 日志"""
        usage = usage or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        model = self.model_id if not self.mock else "mock"
        record_llm_call(
            operation=operation, model=model, ok=ok, duration_ms=duration_ms,
            input_tokens=input_tokens, output_tokens=output_tokens,
            retries=retries, error_kind=error.kind if error else "",
        )
        accumulate_tokens(input_tokens, output_tokens)
        if current_trace() is not None:
            record_span(
                f"llm:{operation}", "llm", duration_ms,
                metadata={
                    "model": model, "ok": ok, "retries": retries,
                    "input_tokens": input_tokens, "output_tokens": output_tokens,
                    **({"error_kind": error.kind} if error else {}),
                },
            )
        logger.debug(
            "LLM调用完成 operation={} ok={} 耗时{:.0f}ms tokens(in={} out={}) retries={}",
            operation, ok, duration_ms, input_tokens, output_tokens, retries,
        )

    def _chat_once(self, messages: List[Dict[str, str]], **kwargs):
        """单次 chat.completions 调用：异常统一转 LLMCallError 后上抛"""
        try:
            return self._client.chat.completions.create(
                model=self.model_id, messages=messages, temperature=settings.LLM_TEMPERATURE, **kwargs)
        except LLMCallError:
            raise
        except Exception as e:
            raise classify_llm_error(e)

    # ---------- LLM 调用入口 ----------

    def llm_invoke(self, prompt: str, system_prompt: Optional[str] = None,
                   operation: str = "invoke", json_mode: bool = False) -> LLMResponse:
        """
        LLM 单轮调用（带重试/退避/可观测性）
        :param operation: 调用用途标签（route/generate_sql/answer/...），
                          用于指标与 trace 的按步骤拆解
        :param json_mode: 请求 response_format=json_object；
                          服务端拒绝时自动降级为普通文本模式重试
        """
        if self.mock:
            mock_answer = f"【Mock LLM】用户提问：{prompt}\n模拟SQL：SELECT * FROM customers LIMIT 10;"
            self._record_call(operation, True, 0.0,
                              usage={"input_tokens": 100, "output_tokens": 50})
            return LLMResponse(content=mock_answer,
                               usage={"input_tokens": 100, "output_tokens": 50})

        messages = self._build_messages(prompt, system_prompt)
        start = time.perf_counter()
        try:
            try:
                # json_mode 关闭时不传 response_format（而不是传 None），
                # 便于在测试/日志中区分「未启用」与「显式降级」
                extra = {"response_format": {"type": "json_object"}} if json_mode else {}
                resp, retries = self._run_with_retry(
                    self._chat_once, operation, messages, **extra)
            except LLMCallError as e:
                # JSON 模式不被该服务支持：降级普通模式重试一次（不改变重试预算语义）
                if json_mode and e.kind == "bad_request":
                    logger.warning(
                        "operation={} 的 json_mode 被服务端拒绝，降级为普通文本模式",
                        operation,
                    )
                    resp, retries = self._run_with_retry(
                        self._chat_once, operation, messages)
                else:
                    raise
            usage = self._parse_usage(getattr(resp, "usage", None))
            content = resp.choices[0].message.content or ""
            self._record_call(operation, True, (time.perf_counter() - start) * 1000,
                              usage=usage, retries=retries)
            return LLMResponse(content=content, usage=usage)
        except LLMCallError as e:
            self._record_call(operation, False, (time.perf_counter() - start) * 1000,
                              retries=e.attempts - 1, error=e)
            raise

    def llm_stream(self, prompt: str, system_prompt: Optional[str] = None,
                   operation: str = "stream") -> Iterator[str]:
        """
        token 级流式调用（生成器）。
        设计说明：流式不做自动重试——重试会向调用方重复输出已产生的 token；
        若在首字节前连接失败，生成器抛 LLMCallError，由调用方决定是否整体重试。
        """
        if self.mock:
            content = f"【Mock LLM】用户提问：{prompt}"
            for i in range(0, len(content), 8):
                yield content[i:i + 8]
            self._record_call(operation, True, 0.0,
                              usage={"input_tokens": 100, "output_tokens": 50})
            return

        messages = self._build_messages(prompt, system_prompt)
        start = time.perf_counter()
        usage: Dict[str, int] = {}
        ok = True
        error: Optional[LLMCallError] = None
        try:
            stream = self._client.chat.completions.create(
                model=self.model_id, messages=messages, stream=True)
            for chunk in stream:
                # 部分兼容服务在最后一个 chunk 携带 usage，能拿则拿
                if getattr(chunk, "usage", None) is not None:
                    usage = self._parse_usage(chunk.usage)
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except LLMCallError:
            raise
        except Exception as e:
            ok = False
            error = classify_llm_error(e)
            raise error
        finally:
            self._record_call(operation, ok, (time.perf_counter() - start) * 1000,
                              usage=usage, error=error)

    def chat_with_tools(self, prompt: str, system_prompt: Optional[str] = None,
                        tools: Optional[List[dict]] = None) -> dict:
        """
        带工具调用的LLM入口（function calling）
        返回 {"content": str, "tool_calls": [{"name": str, "arguments": dict}]}
        mock模式：问题含"计算"时返回calculator工具调用，用于离线联调
        """
        if self.mock:
            tool_calls = []
            if "计算" in prompt:
                tool_calls.append({
                    "name": "calculator__calculate",
                    "arguments": {"expression": prompt.split("计算")[-1].strip()},
                })
            self._record_call("chat_with_tools", True, 0.0,
                              usage={"input_tokens": 100, "output_tokens": 30})
            return {"content": "", "tool_calls": tool_calls}

        messages = self._build_messages(prompt, system_prompt)
        start = time.perf_counter()
        try:
            resp, retries = self._run_with_retry(
                self._chat_once, "chat_with_tools", messages,
                tools=tools or [], tool_choice="auto",
            )
            msg = resp.choices[0].message
            tool_calls = []
            for tc in (msg.tool_calls or []):
                try:
                    args = json.loads(tc.function.arguments)
                except Exception:
                    args = {"expression": tc.function.arguments}
                tool_calls.append({"name": tc.function.name, "arguments": args})
            usage = self._parse_usage(getattr(resp, "usage", None))
            self._record_call("chat_with_tools", True, (time.perf_counter() - start) * 1000,
                              usage=usage, retries=retries)
            return {"content": msg.content or "", "tool_calls": tool_calls}
        except LLMCallError as e:
            self._record_call("chat_with_tools", False, (time.perf_counter() - start) * 1000,
                              retries=e.attempts - 1, error=e)
            raise

    def embedding(self, text: str) -> EmbeddingResponse:
        """文本转向量（带重试与可观测性）"""
        if self.mock:
            mock_vector = [random.random() for _ in range(1024)]
            self._record_call("embedding", True, 0.0,
                              usage={"input_tokens": len(text)})
            return EmbeddingResponse(vector=mock_vector)

        start = time.perf_counter()
        try:
            resp, retries = self._run_with_retry(
                lambda: self._client.embeddings.create(
                    model=settings.LLM_EMBEDDING_MODEL, input=text),
                "embedding",
            )
            usage = self._parse_usage(getattr(resp, "usage", None))
            self._record_call("embedding", True, (time.perf_counter() - start) * 1000,
                              usage=usage, retries=retries)
            return EmbeddingResponse(vector=resp.data[0].embedding)
        except LLMCallError as e:
            self._record_call("embedding", False, (time.perf_counter() - start) * 1000,
                              retries=e.attempts - 1, error=e)
            raise

    # ---------- 本地精排（非 LLM，降级路径 + 可观测性） ----------

    def _get_reranker(self):
        """
        懒加载本地CrossEncoder精排模型
        torch/sentence-transformers的import和模型加载都很慢（数秒~十几秒），
        所以延迟到首次真正rerank时才加载，不影响服务启动速度
        """
        if self._reranker is None:
            import os
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            from sentence_transformers import CrossEncoder
            logger.info("首次加载本地rerank模型：{} ...", settings.RERANK_MODEL_ID)
            self._reranker = CrossEncoder(settings.RERANK_MODEL_ID, max_length=512)
            logger.info("rerank模型加载完成")
        return self._reranker

    def rerank(self, query: str, documents: List[str]) -> RerankResponse:
        """
        搜索结果重排
        真实模式：本地CrossEncoder（bge-reranker）对(query, doc)对做相关性打分，
        index保持原输入列表位置（调用方靠index回映射原始文档）
        加载/推理失败时降级为按原顺序返回，不阻塞检索主链路（降级计入指标）
        """
        if self.mock:
            rerank_items = []
            for idx, doc in enumerate(documents):
                score = 0.9 - idx * 0.05
                rerank_items.append(RerankItem(index=idx, text=doc, score=score))
            return RerankResponse(results=rerank_items)

        if not documents:
            return RerankResponse(results=[])

        start = time.perf_counter()
        try:
            model = self._get_reranker()
            pairs = [[query, doc] for doc in documents]
            scores = model.predict(pairs)
            rerank_items = [
                RerankItem(index=i, text=doc, score=float(s))
                for i, (doc, s) in enumerate(zip(documents, scores))
            ]
            # 按分数降序；index仍是原位置，排序只影响返回顺序
            rerank_items.sort(key=lambda x: x.score, reverse=True)
            record_span("rerank", "tool", (time.perf_counter() - start) * 1000,
                        metadata={"docs": len(documents)})
            return RerankResponse(results=rerank_items)
        except Exception as e:
            rerank_degraded_total.inc()
            logger.warning("本地rerank失败，降级为按原顺序返回：{}", e)
            return RerankResponse(results=[
                RerankItem(index=i, text=doc, score=0.0)
                for i, doc in enumerate(documents)
            ])
