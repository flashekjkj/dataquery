from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChartSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: Literal[1] = 1
    source_id: str
    type: Literal['line','bar','horizontal_bar','pie','card','scatter','histogram','stacked_bar']
    title: str = Field(max_length=100)
    subtitle: str = Field(default='',max_length=160)
    columns: list[str]
    rows: list[list[Any]] = Field(max_length=100)
    x: int | None = None
    y: list[int]
    series: int | None = None
    unit: str = ''

    @model_validator(mode='after')
    def check_mapping(self):
        indices = self.y + [index for index in (self.x,self.series) if index is not None]
        if not self.rows or not self.y or any(index<0 or index>=len(self.columns) for index in indices):
            raise ValueError('图表字段映射无效')
        if any(len(row)!=len(self.columns) for row in self.rows):
            raise ValueError('图表行宽不一致')
        return self
