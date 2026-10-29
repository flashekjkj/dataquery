"""纯 ASGI 请求生命周期：覆盖完整流响应，逐请求隔离可变 Token 计数。"""
import time
import uuid
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from observability import logger
from observability.context import request_id_var, request_tokens_var, session_id_var, get_request_tokens
from observability.metrics import record_http_request
from observability.tracer import start_trace, end_trace, trace_var


class ObservabilityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        request_id = headers.get(b"x-request-id", b"").decode("latin-1") or uuid.uuid4().hex
        rid_token = request_id_var.set(request_id)
        usage_token = request_tokens_var.set({"input": 0, "output": 0})
        sid_token = session_id_var.set(None)
        trace_token = trace_var.set(None)
        trace = start_trace()
        started = time.perf_counter()
        status, response_started = 500, False

        async def send_with_id(message):
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                response_started = True
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            status = 500
            logger.exception("请求处理异常 request_id={}", request_id)
            if response_started:
                raise
            response = JSONResponse(status_code=500, content={"detail": f"服务内部错误，request_id={request_id}"})
            await response(scope, receive, send_with_id)
        finally:
            duration = time.perf_counter() - started
            route = scope.get("route")
            route_path = getattr(route, "path", None) or "unmatched"
            record_http_request(scope["method"], route_path, status, duration)
            usage = get_request_tokens()
            session_id_var.set(trace.session_id)
            logger.info("{} {} -> {} | {:.0f}ms | tokens in={} out={}",
                        scope["method"], route_path, status, duration*1000, usage["input"], usage["output"])
            end_trace()
            trace_var.reset(trace_token)
            session_id_var.reset(sid_token)
            request_tokens_var.reset(usage_token)
            request_id_var.reset(rid_token)
