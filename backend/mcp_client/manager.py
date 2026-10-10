from __future__ import annotations
import asyncio
from typing import Any,List,Dict,Optional

from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

class MCPToolManager:
    """
    管理多个MCP stdio server的连接与工具调用
    servers配置：[{"name":"calculator","command":"python","args":[".../calculator_server.py"]}]
    LangGraph节点是同步函数，这里用asyncio.run包裹异步MCP客户端
    （每次调用建立短连接，demo规模足够；后续优化可改为常驻连接）    
    """
    def __init__(self,servers:List[Dict[str,Any]]):
        self.servers = servers

    def _get_server(self,server_name:str) -> Dict[str,Any]:
        for s in self.servers:
            if s["name"] == server_name:
                return s
        raise ValueError(f"MCP server {server_name} 未配置，可用：{[s['name'] for s in self.servers]}")

    def list_tools(self) -> Dict[str,List[Dict[str,Any]]]:
        """返回全部工具：{server_name: [{name, description, input_schema}]}"""
        async def _run():
            result = {}
            for server in self.servers:
                params = StdioServerParameters(command=server["command"],args=server["args"])
                async with stdio_client(params) as (read,write):
                    async with ClientSession(read,write) as session:
                        await session.initialize()
                        resp = await session.list_tools()
                        result[server["name"]] = [
                            {"name":t.name,"description":t.description or "","input_schema":t.input_schema} for t in resp.tools
                        ]
            return result
        return asyncio.run(_run())

    def to_openai_tools(self,allowed_tools:Optional[List[str]] = None) -> List[Dict[str,Any]]:
        """
        把MCP工具转成OpenAI function calling的tools schema
        allowed_tools：只保留这些裸工具名（技能联动用），None=全部
        工具名加server前缀（calculator__calculate），避免不同server工具重名
        """        
        tools_schema = []
        for server_name, tools in self.list_tools().items():
            for t in tools:
                if allowed_tools is not None and t["name"] not in allowed_tools:
                    continue
                tools_schema.append({
                    "type": "function",
                    "function": {
                        "name": f"{server_name}__{t['name']}",
                        "description": t["description"],
                        "parameters": t["input_schema"],
                    }
                })
        return tools_schema

    def call_tool(self,server_name:str,tool_name:str,arguments:Dict[str,Any]) -> str:
        """调用指定server的工具，返回文本结果"""
        async def _run():
            server = self._get_server(server_name)
            params = StdioServerParameters(command=server["command"],args=server["args"])
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as session:
                    await session.initialize()
                    res = await session.call_tool(tool_name,arguments=arguments)
                    return "\n".join(c.text for c in res.content if c.type == "text")
        return asyncio.run(_run())


    