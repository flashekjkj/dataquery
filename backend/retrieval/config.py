"""检索参数同时用于在线链路与消融评测。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RetrievalConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: Literal["bm25", "vector", "hybrid", "weighted", "adaptive", "rerank"] = "adaptive"
    recall_k: int = Field(default=24, ge=1)
    rerank_k: int = Field(default=48, ge=1)
    rrf_k: int = Field(default=60, ge=1)
    lexical_weight: float = Field(default=0.35, ge=0)
    table_k: int = Field(default=3, ge=1, le=6)
    field_budget: int = Field(default=12, ge=8, le=64)
