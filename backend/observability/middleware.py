"""
Observability 中间件：每个 HTTP 请求的生命周期治理。

职责：
1. 生成/透传 X-Request-ID，挂到 contextvars（日志、trace、错误响应共用）
2. 启动请求级 Trace
3. 记录 HTTP 层指标与访问日志（含本请求累计的 LLM Token 用量）
4. 未捕获异常统一兜底为带 request_id 的 500 响应（企业内部排查约定）
5. 结束时 flush Trace（SSE 流式响应除外——trace 由路由在流结束后 flush）

关于 SSE 的说明：BaseHTTPMiddleware 在响应头就绪后即返回 dispatch，
此时流式 body 尚未消费完。因此对 text/event-stream 响应中间件不结束 trace，
改由 /chat/stream 的生成器在 done/error 后调用 end_trace()。
"""
from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from observability import logger
from observability.context import (
    request_id_var, request_tokens_var, get_request_tokens,
)
from observability.metrics import record_http_request
from observability.tracer import start_trace, end_trace


class ObservabilityMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        # session_id 由 /chat 路由在拿到解析后的请求体后设置（middleware 阶段
        # 不读 body，避免消耗流式请求体）
        rid_token = request_id_var.set(request_id)
        start_trace()
        start = time.perf_counter()
        response = None
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception:
            logger.exception("请求处理异常，兜底返回500（request_id={}）", request_id)
            return JSONResponse(
                status_code=500,
                content={"detail": f"服务内部错误，请携带 request_id={request_id} 反馈排查"},
                headers={"X-Request-ID": request_id},
            )
        finally:
            duration_s = time.perf_counter() - start
            status = response.status_code if response is not None else 500
            # 指标用路由模板（如 /api/sessions/{session_id}）而非原始 URL，
            # 否则真实 id 进入 path 标签会造成指标基数爆炸
            route_path = (request.scope.get("route") and request.scope["route"].path) \
                or request.url.path
            record_http_request(request.method, route_path, status, duration_s)
            tokens = get_request_tokens()
            logger.info(
                "{} {} -> {} | {:.0f}ms | tokens in={} out={}",
                request.method, route_path, status,
                duration_s * 1000, tokens["input"], tokens["output"],
            )
            # SSE 流式响应：生成器可能仍在运行，trace 交给路由在流结束后 flush；
            # 其余响应在中间件收尾。end_trace 幂等，重复调用安全。
            if response is None or not (
                response.headers.get("content-type", "").startswith("text/event-stream")
            ):
                end_trace()
            # 清理 contextvars，防止 task 复用导致的值泄漏
            request_id_var.reset(rid_token)
            request_tokens_var.set({"input": 0, "output": 0})
