from __future__ import annotations
import uuid
from typing import Optional

from agent.runtime import build_agent,ask_data_agent


def main() -> None:
    graph, conv_memory, skill_registry = build_agent()
    # 会话管理：当前会话的thread_id，/new开启新会话
    thread_id = uuid.uuid4().hex
    # 技能锁定：/skill命令写入，None=自动匹配
    forced_skill: Optional[str] = None
    execution_mode = "auto"

    print("==== AskData 数据问答Agent（LangGraph + 记忆版）====")
    print("输入问题提问；命令：/new 新会话，/history 查看历史，/exit 退出 /skills /skill 技能\n")
    print("协作模式：/mode auto（默认）| single | multi\n")

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
        if user_input.startswith("/mode"):
            parts = user_input.split(maxsplit=1)
            mode = parts[1].strip() if len(parts) > 1 else ""
            if mode in ("auto", "single", "multi"):
                execution_mode = mode
                print(f"当前协作模式：{execution_mode}\n")
            else:
                print("用法：/mode auto|single|multi\n")
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
            output = ask_data_agent(graph, user_input, thread_id, forced_skill=forced_skill,
                                    execution_mode=execution_mode)
        except Exception as e:
            print(f"\n❌ 系统异常：{e}")
            print("-" * 60)
            continue

        print(f"\n🤖 Agent回答：{output['answer']}")
        if output["collaboration_status"] != "single":
            print(f"[协作] {output['collaboration_status']}")
            for task in output["task_results"]:
                print(f"[{task['task_id']}] {task['status']}：{task['question']}")
                print(f"  SQL：{task['sql']}")
                if task.get("error"):
                    print(f"  错误：{task['error']}")
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
