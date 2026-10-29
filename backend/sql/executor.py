from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
import duckdb
from threading import Timer
from pydantic import BaseModel
from typing import List,Any

def _json_safe(value:Any) -> Any:
    """把DuckDB返回的特殊类型(DECIMAL/DATE/DATETIME等)转为JSON可序列化的原生类型"""
    if isinstance(value,Decimal):
        return float(value)
    if isinstance(value,(datetime,date)):
        return value.isoformat()
    return value

class QueryResult(BaseModel):
    """SQL执行结果结构化返回"""
    columns:List[str]
    rows:List[List[Any]]
    row_count:int
    is_truncated:bool

class SqlExecuteError(Exception):
    """结构化SQL执行异常，用于generator捕获做自修复"""
    def __init__(self,sql:str,error_msg:str):
        self.sql = sql
        self.error_msg = error_msg
        super().__init__(f"SQL执行失败：{error_msg}")

class SQLExecutor:
    """DuckDB 原生 parser 校验 + 只读/禁外部访问 + 外层结果上限。"""
    def __init__(self, db_path: str, max_rows: int = 100, timeout_seconds: float = 5):
        self.db_path = db_path
        self.max_rows = max_rows
        self.timeout_seconds = timeout_seconds

    def _connect(self):
        return duckdb.connect(self.db_path, read_only=True,
                              config={"enable_external_access": "false", "memory_limit": "256MB", "threads": "2"})

    def _check_safe(self, sql: str) -> None:
        try:
            with duckdb.connect(":memory:") as parser:
                statements = parser.extract_statements(sql)
            if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
                raise ValueError("仅允许单条 SELECT/WITH 查询")
        except Exception as exc:
            raise SqlExecuteError(sql, "安全拦截：" + str(exc)) from exc

    def _append_limit_if_missing(self, sql: str) -> str:
        # 不改写用户内层 LIMIT；第 max_rows+1 行只用于判断是否发生系统截断。
        query = sql.strip().rstrip(";")
        return f"SELECT * FROM (\n{query}\n) AS _askdata_result LIMIT {self.max_rows + 1}"

    def execute(self, sql: str) -> QueryResult:
        self._check_safe(sql)
        try:
            with self._connect() as conn:
                timer = Timer(self.timeout_seconds, conn.interrupt)
                timer.daemon = True
                timer.start()
                try:
                    res = conn.execute(self._append_limit_if_missing(sql))
                    columns = [c[0] for c in res.description]
                    raw_rows = res.fetchmany(self.max_rows + 1)
                finally:
                    timer.cancel()
                truncated = len(raw_rows) > self.max_rows
                rows = [[_json_safe(v) for v in row] for row in raw_rows[:self.max_rows]]
                return QueryResult(columns=columns, rows=rows, row_count=len(rows),
                                   is_truncated=truncated)
        except Exception as exc:
            message = "查询超时：执行已中断" if isinstance(exc, duckdb.InterruptException) else str(exc)
            raise SqlExecuteError(sql, message) from exc

    def sample_values(self,table:str,column:str,n:int = 5) -> List[Any]:
        """
        对指定表的字段采样DISTINCT样例值，用于填充prompt
        :param table: 表名
        :param column: 字段名
        :param n: 最多采样条数
        :return: 样例列表
        """
        sample_sql = f"""
        SELECT DISTINCT "{column}" FROM "{table}" LIMIT {n}
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(sample_sql).fetchall()
                return [row[0] for row in rows]
        except Exception as e:
            raise SqlExecuteError(sql=sample_sql,error_msg=str(e)) from e
