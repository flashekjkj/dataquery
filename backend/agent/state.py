from __future__ import annotations
import operator
from typing import TypedDict,Annotated,Optional,List,Dict,Tuple,Any
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

    # 每一轮尝试日志
    attempts_log:Annotated[List[Tuple[str,Optional[str]]],operator.add]

    # 最终返回给用户的自然语言回答
    answer:Optional[str]