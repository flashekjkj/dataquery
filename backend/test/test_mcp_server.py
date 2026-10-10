from __future__ import annotations
import asyncio
import sys
from pathlib import Path

BASE_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(BASE_BACKEND))

from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

RUNNER = str(BASE_BACKEND / "mcp_server" / "run_server.py")

def _run_with_server(coro_factory):
    """连接stdio MCP server，把session交给coro_factory执行"""
    async def _run():
        params = StdioServerParameters(command=sys.executable,args=[RUNNER])
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                return await coro_factory(session)
    return asyncio.run(_run())

def test_list_tools():
    async def _check(session):
        resp = await session.list_tools()
        return {t.name for t in resp.tools}
    names = _run_with_server(_check)
    assert names == {"list_tables", "describe_table", "query_database"}, f"工具集不符：{names}"
    print("✅ test_list_tools 通过！3个MCP工具已注册")

def test_query_database():
    async def _check(session):
        return await session.call_tool("query_database", {"sql": "SELECT COUNT(*) AS cnt FROM orders"})
    res = _run_with_server(_check)
    assert not res.is_error, f"查询失败：{res}"
    print(f"✅ test_query_database 通过！返回：{res.content[0].text[:120]}")

def test_blocks_write_sql():
    async def _check(session):
        return await session.call_tool("query_database", {"sql": "DROP TABLE orders"})
    res = _run_with_server(_check)
    assert res.is_error, "写操作应被拦截"
    print(f"✅ test_blocks_write_sql 通过！被拦截：{res.content[0].text[:80]}")

if __name__ == "__main__":
    test_list_tools()
    test_query_database()
    test_blocks_write_sql()
    print("\n🎉 全部MCP Server测试通过！")