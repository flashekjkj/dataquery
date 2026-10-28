from __future__ import annotations
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from agent.runtime import build_agent
from app.config import settings
from observability import logger
from observability.metrics import render_latest, metrics_content_type
from observability.middleware import ObservabilityMiddleware
from server.routes import router

@asynccontextmanager
async def lifespan(app:FastAPI):
    """
    服务生命周期：启动时构建一次Agent（建混合检索索引、加载技能、MCP），
    挂在app.state上供所有请求复用；关闭时打印日志
    """
    logger.info("正在构建Agent（建索引/加载技能/MCP，约需几十秒）...")
    graph,conv_memory,skill_registry = build_agent()
    app.state.graph = graph
    app.state.conv_memory = conv_memory
    app.state.skill_registry = skill_registry
    logger.info("Agent就绪，等待请求")
    yield
    logger.info("服务关闭")

app = FastAPI(title="AskData API",version="0.1.0",lifespan=lifespan)

# 前端Vite dev server跑在5173端口，跨域放行；开发期走Vite代理时这里其实用不上，
# 属于保险配置（前端若直接访问8000也能通）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173","http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 可观测性中间件：request_id / 访问日志 / HTTP指标 / 请求级trace
# （add_middleware 后添加者先执行，CORS 保持最外层）
app.add_middleware(ObservabilityMiddleware)

app.include_router(router)


@app.get("/metrics", include_in_schema=False)
def metrics():
    """Prometheus 指标端点（/metrics 在 /api 前缀之外，行业惯例）"""
    if not settings.METRICS_ENABLED:
        raise HTTPException(status_code=404, detail="指标端点未启用（METRICS_ENABLED=false）")
    return Response(content=render_latest(), media_type=metrics_content_type())
