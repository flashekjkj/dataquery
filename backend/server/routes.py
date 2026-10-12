from __future__ import annotations
import json
import uuid
from typing import List

from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import StreamingResponse

from agent.runtime import ask_data_agent
from server.schemas import (
    ChatRequest,ChatResponse,HealthResponse,
    SessionDetail,SessionSummary,SkillInfo
)

router = APIRouter(prefix="/api")

@router.get("/health",response_model=HealthResponse)
def health(request:Request):
    """健康检查：agent是否已经随服务启动构建完成"""
    return HealthResponse(
        status="ok",
        agent_ready=hasattr(request.app.state,"graph")
    )

@router.post("/chat",response_model=ChatResponse)
def chat(req:ChatRequest,request:Request):
    """核心接口：提问，返回回答+SQL+查询结果+自修复过程"""
    session_id = req.session_id or uuid.uuid4().hex
    try:
        result = ask_data_agent(
            request.app.state.graph,
            req.question,
            session_id,
            forced_skill=req.forced_skill,
        )
    except Exception as e:
        raise HTTPException(status_code=500,detail=f"Agent执行失败：{e}")

    if result.get("query_result") is not None:
        result["query_result"] = result["query_result"].model_dump()
    return {**result,"session_id":session_id}

@router.get("/sessions",response_model=List[SessionSummary])
def list_sessions(request:Request):
    """会话列表（按最后更新时间倒序）"""
    return request.app.state.conv_memory.list_sessions()

@router.get("/sessions/{session_id}",response_model=SessionDetail)
def get_session(session_id:str,request:Request):
    """单会话完整历史：全部turns + 滚动摘要"""
    data = request.app.state.conv_memory.load(session_id)
    if not data.get("turns") and not data.get("summary"):
        raise HTTPException(status_code=404,detail="会话不存在")
    return {
        "session_id":session_id,
        "turns":data["turns"],
        "summary":data.get("summary") or "",
    }

@router.delete("/sessions/{session_id}")
def delete_session(session_id:str,request:Request):
    """删除会话（清空会话得对话历史文件"""
    request.app.state.conv_memory.clear(session_id)
    return {"ok":True}

@router.get("/skills",response_model=List[SkillInfo])
def list_skills(request:Request):
    """技能列表，前端展示可选技能"""
    registry = request.app.state.skill_registry
    return [SkillInfo(**s.model_dump()) for s in registry.list_all()]


@router.post("/chat/stream")
async def chat_stream(req:ChatRequest,request:Request):
    """SSE进度流：每完成一个图节点推送一次事件，前端实时显示执行阶段"""
    session_id = req.session_id or uuid.uuid4().hex
    graph = request.app.state.graph

    async def event_generator():
        initial_state = {
            "user_question": req.question,
            "attempts": 0,
            "attempts_log": [],
            "forced_skill": req.forced_skill,
        }
        config = {"configurable": {"thread_id": session_id}}
        try:
            # updates模式：每完成一个节点，yield一次该节点的状态增量
            async for update in graph.astream(initial_state,config=config,stream_mode="updates"):
                for node_name,node_output in update.items():
                    # updates模式下，返回空dict的节点会被LangGraph变成None，跳过即可
                    if node_output is None:
                        continue
                    event = {"type":"node","node":node_name}
                    # 只挑前端关心的字段
                    for key in ("rewritten_question","sql","answer","success","route","active_skill"):
                        if key in node_output:
                            event[key] = node_output[key]
                    if "field_docs" in node_output:
                        event["retrieved_docs"] = [d.doc_id for d in node_output["field_docs"]]
                    # 执行节点带查询结果：前端直接拿表格数据，无需再请求一次
                    if node_output.get("query_result") is not None:
                        event["query_result"] = node_output["query_result"].model_dump()
                    if "attempts_log" in node_output:
                        event["attempts_log"] = node_output["attempts_log"]
                    yield f"data: {json.dumps(event,ensure_ascii=False)}\n\n"
            yield f'data: {json.dumps({"type":"done","session_id":session_id},ensure_ascii=False)}\n\n'
        except Exception as e:
            yield f'data: {json.dumps({"type":"error","message":str(e)},ensure_ascii=False)}\n\n'

    return StreamingResponse(event_generator(),media_type="text/event-stream")
