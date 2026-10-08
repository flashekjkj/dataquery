from __future__ import annotations
import re
from datetime import date, datetime
from decimal import Decimal
import duckdb
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
    """危险操作关键字"""
    DANGER_KEYWORDS = [
        "DROP","INSERT","UPDATE","DELETE","ALTER","CREATE","TRUNCATE","ATTACH","COPY","PRAGMA"
    ]
    # 正则：词边界匹配大写关键字
    DANGER_PATTERN = re.compile(
        r"\b(" + "|".join(DANGER_KEYWORDS) + r")\b",
        re.IGNORECASE
    )
    # 检测是否已有LIMIT
    LIMIT_PATTERN = re.compile(r"\bLIMIT\s+\d+",re.IGNORECASE)

    def __init__(self,db_path:str):
        self.db_path = db_path

    def _check_safe(self,sql:str) -> None:
        """安全检查：拦截写操作语句，不允许DDL/DML"""
        if self.DANGER_PATTERN.search(sql):
            raise SqlExecuteError(
                sql=sql,
                error_msg="安全拦截：禁止执行DDL/DML写操作语句"
            )

    def _append_limit_if_missing(self,sql:str) -> str:
        """没有LIMIT则追加 LIMIT 100"""
        if not self.LIMIT_PATTERN.search(sql):
            sql = sql.strip().rstrip(";") + " LIMIT 100;"
        return sql

    def execute(self,sql:str) -> QueryResult:
        """
        执行SQL,只读DuckDB连接
        :param sql:待执行SELECT语句
        :return:QueryResult
        :raises SqlExecuteError:安全校验失败 / duckdb执行错误
        """
        sql_raw = sql
        self._check_safe(sql_raw)
        sql_to_run = self._append_limit_if_missing(sql_raw)

        try:
            with duckdb.connect(self.db_path,read_only=True) as conn:
                res = conn.execute(sql_to_run)
                columns = [c[0] for c in res.description]
                rows = [[_json_safe(v) for v in row] for row in res.fetchmany(100)]
                # 判断是否截断
                is_truncated = bool(res.fetchone())
                row_count = len(rows)
                return QueryResult(
                    columns=columns,
                    rows=rows,
                    row_count=row_count,
                    is_truncated=is_truncated
                )
        except Exception as e:
            raise SqlExecuteError(sql=sql_raw,error_msg=str(e)) from e

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
            with duckdb.connect(self.db_path,read_only=True) as conn:
                rows = conn.execute(sample_sql).fetchall()
                return [row[0] for row in rows]
        except Exception as e:
            raise SqlExecuteError(sql=sample_sql,error_msg=str(e)) from e
