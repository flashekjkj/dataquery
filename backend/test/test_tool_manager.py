from __future__ import annotations
import sys
from pathlib import Path

BASE_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_BACKEND))

from mcp_client.manager import MCPToolManager

CALC_SERVER = {
    "name": "calculator",
    "command": sys.executable,
    "args": [str(BASE_BACKEND / "mcp_client" / "demo_servers" / "calculator_server.py")],
}

def test_list_tools():
    manager = MCPToolManager(servers=[CALC_SERVER])
    tools = manager.list_tools()
    names = [t["name"] for t in tools["calculator"]]
    assert "calculate" in names, f"工具不符：{names}"
    print("✅ test_list_tools 通过！发现calculator的calculate工具")

def test_call_tool():
    manager = MCPToolManager(servers=[CALC_SERVER])
    text = manager.call_tool("calculator", "calculate", {"expression": "3856*0.85"})
    assert "3277.6" in text, f"计算结果错误：{text}"
    print(f"✅ test_call_tool 通过！3856*0.85 = {text.strip()}")

def test_to_openai_tools():
    manager = MCPToolManager(servers=[CALC_SERVER])
    schema = manager.to_openai_tools()
    assert schema[0]["function"]["name"] == "calculator__calculate"
    filtered = manager.to_openai_tools(allowed_tools=["calculate"])
    assert len(filtered) == 1
    filtered_none = manager.to_openai_tools(allowed_tools=["not_exist"])
    assert len(filtered_none) == 0
    print("✅ test_to_openai_tools 通过！schema转换与技能过滤正确")

if __name__ == "__main__":
    test_list_tools()
    test_call_tool()
    test_to_openai_tools()
    print("\n🎉 全部MCP工具管理器测试通过！")
