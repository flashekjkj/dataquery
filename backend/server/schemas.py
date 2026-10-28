from __future__ import annotations
from typing import Any,List,Optional,Literal

from pydantic import BaseModel,Field
from agent.contracts import TaskPlan, QueryTaskResult


class ChatRequest(BaseModel):
    """POST /api/chat 请求体"""
    question:str = Field(...,min_length=1,max_length=2000)
    session_id:Optional[str] = None
    forced_skill:Optional[str] = None
    execution_mode: Literal["auto", "single", "multi"] = "auto"

class AttemptRecord(BaseModel):
    """SQL自修复单轮尝试记录"""
    sql:str
    error:Optional[str] = None

class QueryResultOut(BaseModel):
    """查询结果（executor以保证行值是JSON安全类型）"""
    columns:List[str]
    rows:List[List[Any]]
    row_count:int
    is_truncated:bool

class ChatResponse(BaseModel):
    """POST /api/chat 响应体：ask_data_agent返回的dict + session_id"""
    session_id:str
    # 本次执行的 trace_id（与 request_id 同源），用于按 trace 文件/日志排查
    trace_id:Optional[str] = None
    success:bool
    route:str
    user_question:str
    rewritten_question:Optional[str] = None
    retrieved_docs:List[str]
    final_sql:Optional[str] = None
    query_result:Optional[QueryResultOut] = None
    answer:str
    attempts:List[AttemptRecord]
    active_skill:Optional[str] = None
    tool_calls:List[str]
    tool_result:Optional[str] = None
    collaboration_status: str = "single"
    task_plan: Optional[TaskPlan] = None
    task_results: List[QueryTaskResult] = Field(default_factory=list)

class SkillInfo(BaseModel):
    """GET /api/skills 单条技能信息"""
    name:str
    display_name:str
    description:str
    trigger_keywords:List[str]
    example_questions:List[str]

class SessionSummary(BaseModel):
    """GET /api/sessions 列表项"""
    session_id:str
    title:str
    turn_count:int
    created_at:Optional[str] = None
    updated_at:Optional[str] = None
    summary:str

class SessionDetail(BaseModel):
    """GET /api/sessions/{id} 单会话完整历史"""
    session_id:str
    turns:List[dict]
    summary:str

class HealthResponse(BaseModel):
    """GET /api/health"""
    status:str
    agent_ready:bool

