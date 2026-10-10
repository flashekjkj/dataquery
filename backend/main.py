from __future__ import annotations
import uuid
from pathlib import Path
import sys
from typing import Optional

from agent.graph import build_graph
from app.model_client import ModelClient
from memory.conversation import ConversationMemory
from memory.long_term import LongTermMemoryStore
from retrieval.service import build_hybrid_index
from sql.executor import SQLExecutor
from skill.registry import SkillRegistry
from mcp_client.manager import MCPToolManager

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "demo_ecom.duckdb"
DATA_DIR = BASE_DIR / "data"


def build_agent(max_attempts: int = 3):
    """
    构建Agent全链路：模型客户端 + 混合检索 + SQL执行器 + 两种记忆 + 技能注册表 + LangGraph图
    返回 (graph, conv_memory,skill_registry)：图用于问答，conv_memory用于CLI展示会话历史
    """
    model_client = ModelClient(mock=False)
    print("正在构建混合检索索引（对所有字段文档生成embedding）...")
    hybrid = build_hybrid_index(model_client)
    executor = SQLExecutor(str(DB_PATH))
    conv_memory = ConversationMemory(data_root=DATA_DIR)
    long_term_memory = LongTermMemoryStore(data_root=DATA_DIR)
    skill_registry = SkillRegistry(DATA_DIR / "skills")
    # MCP工具管理器：接入calculator演示server（第四步技能联动时按skill声明过滤工具）
    mcp_manager = MCPToolManager(
        servers=[
            {
                "name": "calculator",
                "command": sys.executable,
                "args": [str(BASE_DIR / "mcp_client" / "demo_servers" / "calculator_server.py")],
            }
        ]
    )
    graph = build_graph(
        model_client, hybrid, executor,
        conv_memory, long_term_memory,
        max_attempts=max_attempts,
        skill_registry=skill_registry,
        mcp_manager=mcp_manager
    )
    return graph, conv_memory, skill_registry


def ask_data_agent(graph, user_question: str, thread_id: str,forced_skill:Optional[str] = None) -> dict:
    """
    LangGraph版Agent入口：一次invoke运行整张图
    - thread_id必传：由会话层管理，同一会话内多轮共用，实现多轮记忆
    - forced_skill：强制指定技能名（/skill命令），None=自动匹配
    - 初始state必须带上attempts和attempts_log（attempts参与加法运算，
      attempts_log每次显式传[]覆盖重置，防止跨轮累积）
    """
    final_state = graph.invoke({
            "user_question": user_question, 
            "attempts": 0, 
            "attempts_log": [],
            "forced_skill":forced_skill,
        },
        config={"configurable": {"thread_id": thread_id}},
    )

    route = final_state.get("route") or "data"
    field_docs = final_state.get("field_docs") or []
    attempts_log = final_state.get("attempts_log") or []

    return {
        "success": bool(final_state.get("success")) if route == "data" else True,
        "route": route,
        "user_question": user_question,
        "rewritten_question": final_state.get("rewritten_question"),
        "retrieved_docs": [doc.doc_id for doc in field_docs],
        "final_sql": final_state.get("sql"),
        "query_result": final_state.get("query_result"),
        "answer": final_state.get("answer") or "",
        "attempts": [{"sql": s, "error": e} for s, e in attempts_log],
        "active_skill":final_state.get("active_skill"),
        "tool_calls": [c["name"] for c in (final_state.get("tool_calls") or [])],
        "tool_result": final_state.get("tool_result"),
    }


def main() -> None:
    graph, conv_memory, skill_registry = build_agent()
    # 会话管理：当前会话的thread_id，/new开启新会话
    thread_id = uuid.uuid4().hex
    # 技能锁定：/skill命令写入，None=自动匹配
    forced_skill: Optional[str] = None

    print("==== AskData 数据问答Agent（LangGraph + 记忆版）====")
    print("输入问题提问；命令：/new 新会话，/history 查看历史，/exit 退出 /skills /skill 技能\n")

    while True:
        try:
            user_input = input("👤 用户：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 退出程序")
            break

        if user_input.lower() in ("exit", "quit", "q", "/exit"):
            print("👋 退出程序")
            break
        if not user_input:
            continue
        if user_input == "/new":
            thread_id = uuid.uuid4().hex
            print("🆕 已开启新会话\n")
            continue
        if user_input == "/history":
            history_text = conv_memory.recent_context(thread_id, n=50)
            print(f"📜 当前会话历史：\n{history_text or '（空）'}\n")
            continue
        if user_input == "/skills":
            print("📚 可用技能：")
            for s in skill_registry.list_all():
                print(f"  - /skill {s.name}：{s.display_name}（{s.description}）")
            print()
            continue
        if user_input.startswith("/skill"):
            parts = user_input.split(maxsplit=1)
            name = (parts[1].strip() if len(parts) > 1 else "").lower()
            if name in ("off", "none", "关闭"):
                forced_skill = None
                print("🔓 已关闭技能锁定，恢复自动匹配\n")
            else:
                skill = skill_registry.get(name)
                if skill is None:
                    print(f"❌ 技能 {name} 不存在，用 /skills 查看可用技能\n")
                else:
                    forced_skill = name
                    print(f"🎯 已锁定技能：{skill.display_name}（{name}）\n")
            continue

        try:
            output = ask_data_agent(graph, user_input, thread_id, forced_skill=forced_skill)
        except Exception as e:
            print(f"\n❌ 系统异常：{e}")
            print("-" * 60)
            continue

        print(f"\n🤖 Agent回答：{output['answer']}")
        if output["route"] == "data":
            if output["rewritten_question"] and output["rewritten_question"] != user_input:
                print(f"[DEBUG] 指代消解：{user_input} → {output['rewritten_question']}")
            print(f"[DEBUG] 命中字段：{', '.join(output['retrieved_docs'])}")
            print(f"[DEBUG] 执行SQL：{output['final_sql']}")

            if output.get("active_skill"):
                print(f"[DEBUG] 生效技能：{output['active_skill']}")

            if not output["success"]:
                for i, att in enumerate(output["attempts"], 1):
                    print(f"[DEBUG] 第{i}轮SQL：{att['sql']}")
                    if att["error"]:
                        print(f"[DEBUG] 第{i}轮错误：{att['error']}")
            elif output["query_result"] is not None:
                qr = output["query_result"]
                flag = "（已截断）" if qr.is_truncated else ""
                print(f"[DEBUG] 返回 {qr.row_count} 行{flag}")
        elif output["route"] == "tool":
            print(f"[DEBUG] 工具调用：{', '.join(output['tool_calls']) or '无（降级闲聊）'}")
            print(f"[DEBUG] 工具结果：{output['tool_result']}")
        print("-" * 60)


if __name__ == "__main__":
    main()
