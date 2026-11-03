"""数据源配置不进入QueryContext；快照是可序列化的元数据。"""
import hashlib
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class DataSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: str = Field(min_length=1, pattern=r'^[A-Za-z0-9_-]+$')
    dialect: Literal["duckdb", "postgres"]
    path: str | None = None
    url: SecretStr | None = Field(default=None, exclude=True)
    schemas: tuple[str, ...] = ()
    demo_catalog: bool = False
    max_rows: int = Field(default=100, ge=1, le=10000)
    timeout_seconds: float = Field(default=5, gt=0)
    pool_size: int = Field(default=5, ge=1, le=50)
    policy_file: str | None = None
    semantic_files: tuple[str, ...] = ()
    query_mode: Literal['independent','consistent'] = 'independent'
    snapshot_seconds: int = Field(default=120,ge=1,le=300)

    @model_validator(mode="after")
    def check_connection(self):
        if self.dialect == "duckdb" and not self.path:
            raise ValueError("DuckDB需要path")
        if self.dialect == "postgres" and not self.url:
            raise ValueError("PostgreSQL需要url")
        if self.demo_catalog and self.dialect != "duckdb":
            raise ValueError("演示目录仅用于电商DuckDB")
        return self


class ColumnSchema(BaseModel):
    name: str
    type: str
    nullable: bool = True
    label: str = ""
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    role: str = "dimension"


class TableSchema(BaseModel):
    id: str
    name: str
    schema_name: str
    description: str = ""
    primary_key: list[str] = Field(default_factory=list)
    fields: list[ColumnSchema]


class RelationSchema(BaseModel):
    left_table: str
    left_field: str
    right_table: str
    right_field: str
    description: str = ""


class SchemaSnapshot(BaseModel):
    source_id: str
    dialect: Literal["duckdb", "postgres"]
    tables: list[TableSchema]
    relations: list[RelationSchema] = Field(default_factory=list)

    @property
    def version(self):
        payload=json.dumps(self.model_dump(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    @property
    def schema_tables(self):
        return [t.model_dump() for t in self.tables]


class QueryContext(BaseModel):
    model_config = ConfigDict(frozen=True)
    snapshot: SchemaSnapshot
    policy_version: str = ''
    semantic_version: str = ''
    semantics: dict = Field(default_factory=dict)

    @property
    def source_id(self):
        return self.snapshot.source_id

    @property
    def dialect(self):
        return self.snapshot.dialect

    @property
    def schema_version(self):
        return self.snapshot.version

    def prompt_metadata(self, tables=None):
        selected=[t for t in self.snapshot.tables if tables is None or t.id in tables]
        names={t.id for t in selected}
        return {"source_id": self.source_id, "dialect": self.dialect,
                "schema_version": self.snapshot.version,
                "policy_version":self.policy_version,"semantic_version":self.semantic_version,
                "schema": [t.model_dump() for t in selected],
                "semantics": dict([(k,v) for k,v in self.semantics.get('metrics',{}).items() if set(v['tables']) <= names][:4]),
                "legal_joins": {k:v for k,v in self.semantics.get('joins',{}).items() if {v['left'],v['right']}<=names},
                "time_fields": {k:v for k,v in self.semantics.get('time_fields',{}).items() if k in names},
                "relations": [r.model_dump() for r in self.snapshot.relations if r.left_table in names and r.right_table in names]}
