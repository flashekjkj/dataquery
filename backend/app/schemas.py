from __future__ import annotations
from pydantic import BaseModel
from typing import List,Dict

class LLMResponse(BaseModel):
    """LLM调用返回结构"""
    content:str
    usage:Dict

class EmbeddingResponse(BaseModel):
    """向量嵌入返回结构"""
    vector:List[float]

class RerankItem(BaseModel):
    """单条重排结果"""
    index:int
    text:str
    score:float

class RerankResponse(BaseModel):
    """重排整体返回"""
    results:List[RerankItem]