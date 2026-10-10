from __future__ import annotations
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from data.database import SCHEMA
from sql.executor import SQLExecutor,SqlExecuteError

# 数据库绝对路径
DB_PATH = Path(__file__).resolve().parents[1] / "data" / "demo_ecom.duckdb"
executor = SQLExecutor(DB_PATH)

mcp = MCPServer("askdata")

@mcp.tool()
def list_tables() -> list[dict]:
    """列出数据库全部表：表ID、中文名、业务说明"""
    return [
        {"id":t["id"],"label":t["label"],"description":t["description"]} for t in SCHEMA
    ]

@mcp.tool()
def describe_table(table_id:str) -> dict:
    """查看指定表的全部字段：字段名、中文标签、类型、角色、说明"""
    for t in SCHEMA:
        if t["id"] == table_id:
            return t
    raise ValueError(f"表{table_id}不存在，可用表：{[t['id'] for t in SCHEMA]}")

@mcp.tool()
def query_database(sql:str) -> dict:
    """对数据库执行只读SELECT查询（自动追加LIMIT，禁止任何写操作）。返回列名、行数据、行数"""
    try:
        result = executor.execute(sql)
    except SqlExecuteError as e:
        raise ValueError(e.error_msg)
    return {
        "columns":result.columns,
        "rows":result.rows,
        "row_count":result.row_count,
    }