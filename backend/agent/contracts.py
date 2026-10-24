"""Agent 交接协议；模型输出先校验，再驱动工作流。"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sql.executor import QueryResult


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class QueryTask(Contract):
    task_id: str = Field(pattern=r"^T[1-3]$")
    question: str = Field(min_length=1, max_length=2000)
    expected_output: str = Field(min_length=1, max_length=1000)
    depends_on: list[str] = Field(default_factory=list, max_length=2)


class TaskPlan(Contract):
    clarification: str = Field(default="", max_length=2000)
    assumptions: list[str] = Field(default_factory=list, max_length=10)
    tasks: list[QueryTask] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def validate_tasks(self):
        if bool(self.clarification.strip()) == bool(self.tasks):
            raise ValueError("必须选择澄清或任务计划之一")
        seen = set()
        for task in self.tasks:
            if task.task_id in seen or len(set(task.depends_on)) != len(task.depends_on):
                raise ValueError("任务或依赖重复")
            if not set(task.depends_on) <= seen:
                raise ValueError("依赖必须引用前面的任务，禁止循环依赖")
            seen.add(task.task_id)
        return self


class ReviewDecision(Contract):
    verdict: Literal["approve", "revise", "clarify"]
    issues: list[str] = Field(default_factory=list, max_length=10)
    clarification: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_feedback(self):
        if self.verdict == "revise" and not any(s.strip() for s in self.issues):
            raise ValueError("返工必须给出具体意见")
        if self.verdict == "clarify" and not self.clarification.strip():
            raise ValueError("澄清必须给出问题")
        return self


class QueryAttempt(Contract):
    sql: str
    error: str | None = None


class QueryTaskResult(Contract):
    task_id: str
    question: str
    expected_output: str
    status: Literal["queried", "revise", "approved", "failed", "unverified", "skipped"]
    revision: int = Field(ge=0, le=1)
    sql: str | None = None
    query_result: QueryResult | None = None
    attempts: list[QueryAttempt] = Field(default_factory=list)
    retrieved_docs: list[str] = Field(default_factory=list)
    reviews: list[ReviewDecision] = Field(default_factory=list)
    error: str | None = None


def parse_contract(model, content):
    text = (content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1])
    return model.model_validate_json(text)
