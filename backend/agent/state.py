from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple, TypedDict

from sql.executor import QueryResult
from retrieval.store import FieldDocument

class AskDataState(TypedDict):
    """
    LangGraph Agent 状态定义
    """
    # 用户原始输入
    user_question:str

    # 意图路由结果
    route:Optional[str]

    # 检索得到的schema上下文
    field_docs:Optional[List[FieldDocument]]
    relations:Optional[List[str]]
    value_samples:Optional[Dict[str,List[Any]]]

    # 当前生成的SQL文本
    sql:Optional[str]

    # SQL执行结果对象
    query_result:Optional[QueryResult]

    # SQL执行是否成功
    success:Optional[bool]

    # 最近一次SQL报错信息
    last_error:Optional[str]

    # 已重试次数
    attempts:int

    # 本轮尝试日志（普通键，无reducer）：
    # 每次invoke显式传[]覆盖重置；节点内读改写返回完整列表，
    # 既保证单轮内多次尝试完整记录，又不会跨轮累积
    attempts_log:List[Tuple[str,Optional[str]]]

    # 最终返回给用户的自然语言回答
    answer:Optional[str]

    # 短期记忆：本会话历史文本（load_memory_node写入）
    conversation_context:Optional[str]

    # 长期记忆：用户偏好文本（load_memory_node写入）
    user_profile:Optional[str]

    # 指代消解后的独立问题（rewrite_node写入，首轮为空）
    rewritten_question:Optional[str]

    # 技能相关
    forced_skill:Optional[str]  # 用户强制指定技能
    active_skill:Optional[str]  # 本轮实际生效技能

    # 工具调用相关
    tool_calls:Optional[List[Dict[str,Any]]]   # 工具规划节点产出的调用列表
    tool_result:Optional[str]                  # 工具执行结果文本    