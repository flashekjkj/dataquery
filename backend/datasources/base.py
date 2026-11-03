from abc import ABC, abstractmethod
from datasources.models import DataSourceConfig, SchemaSnapshot
from sql.executor import QueryResult
from contextlib import contextmanager


def quote_identifier(value):
    return '"' + value.replace('"', '""') + '"'


def table_id(schema, name):
    return quote_identifier(schema) + '.' + quote_identifier(name)


def column_role(name, type_name, primary_key):
    if name in primary_key:
        return "identifier"
    if any(t in type_name.upper() for t in ("DATE", "TIME")):
        return "time"
    return "dimension"


class BaseConnector(ABC):
    def __init__(self, config: DataSourceConfig):
        self.config = config

    @abstractmethod
    def test_connection(self) -> bool: ...

    @abstractmethod
    def inspect_schema(self) -> SchemaSnapshot: ...

    @abstractmethod
    def execute_readonly(self, sql: str) -> QueryResult: ...

    def execute(self, sql: str) -> QueryResult:
        return self.execute_readonly(sql)

    @contextmanager
    def query_session(self):
        # 独立模式每条查询独立连接，不保证跨查询一致性。
        if self.config.query_mode == 'consistent':
            raise NotImplementedError('该连接器不支持一致快照')
        yield None

    @abstractmethod
    def sample_values(self, table: str, column: str, n: int = 5) -> list: ...

    @abstractmethod
    def close(self): ...

    def sample_sql(self, table, column, n):
        if not 1 <= n <= 10000:
            raise ValueError("采样数量超出范围")
        n = min(n, self.config.max_rows)
        snapshot = self.inspect_schema()
        entry = next((t for t in snapshot.tables if t.id == table), None)
        if entry is None or column not in {f.name for f in entry.fields}:
            raise ValueError("未知表或字段")
        qualified = table_id(entry.schema_name, entry.name)
        return f"SELECT DISTINCT {quote_identifier(column)} FROM {qualified} LIMIT {n}"
