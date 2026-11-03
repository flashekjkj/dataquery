from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from datasources.config import load_source_config
from datasources import DataSourceRegistry
import atexit
from sql.executor import SqlExecuteError

registry = DataSourceRegistry()
config = load_source_config()
executor = registry.register(config)
atexit.register(registry.close)

mcp = MCPServer("askdata")

@mcp.tool()
def list_tables() -> list[dict]:
    """列出数据库全部表：表ID、中文名、业务说明"""
    return [
        {"id":t["id"],"label":t["name"],"description":t["description"]} for t in executor.inspect_schema().schema_tables
    ]

@mcp.tool()
def describe_table(table_id:str) -> dict:
    """查看指定表的全部字段：字段名、中文标签、类型、角色、说明"""
    for t in executor.inspect_schema().schema_tables:
        if t["id"] == table_id:
            return t
    raise ValueError(f"表{table_id}不存在，可用表：{[t['id'] for t in executor.inspect_schema().schema_tables]}")

@mcp.tool()
def query_database(sql:str) -> dict:
    """对数据库执行只读SELECT查询（自动追加LIMIT，禁止任何写操作）。返回列名、行数据、行数"""
    try:
        with executor.query_session() as snapshot:
            result = executor.execute(sql)
    except SqlExecuteError as e:
        raise ValueError(e.error_msg)
    return {
        "columns":result.columns,
        "rows":result.rows,
        "row_count":result.row_count,
        "snapshot":snapshot,
    }
