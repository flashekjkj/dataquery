from __future__ import annotations
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent.runtime import build_agent
from server.routes import router

@asynccontextmanager
async def lifespan(app:FastAPI):
    """
    服务生命周期：启动时构建一次Agent（建混合检索索引、加载技能、MCP），
    挂在app.state上供所有请求复用；关闭时打印日志    
    """
    print("⏳ 正在构建Agent（建索引/加载技能/MCP，约需几十秒）...")
    graph,conv_memory,skill_registry = build_agent()
    app.state.graph = graph
    app.state.conv_memory = conv_memory
    app.state.skill_registry = skill_registry
    print("✅ Agent就绪，等待请求")
    yield
    print("🛑 服务关闭")

app = FastAPI(title="AskData API",version="0.1.0",lifespan=lifespan)

# 前端Vite dev server跑在5173端口，跨域放行；开发期走Vite代理时这里其实用不上，
# 属于保险配置（前端若直接访问8000也能通）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173","http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)