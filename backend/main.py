from __future__ import annotations
import uuid
from pathlib import Path

from agent.graph import build_graph
from app.model_client import ModelClient
from retrieval.service import build_hybrid_index
from sql.executor import SQLExecutor

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "demo_ecom.duckdb"


def build_agent(max_attempts: int = 3):
    """
    构建Agent全链路：模型客户端 + 混合检索 + SQL执行器 + LangGraph图
    返回编译好的图（图内部已通过闭包持有全部依赖）
    """
    model_client = ModelClient(mock=False)
    print("正在构建混合检索索引（对所有字段文档生成embedding）...")
    hybrid = build_hybrid_index(model_client)
    executor = SQLExecutor(str(DB_PATH))
    graph = build_graph(model_client, hybrid, executor, max_attempts=max_attempts)
    return graph


def ask_data_agent(graph, user_question: str, thread_id: str = None) -> dict:
    """
    LangGraph版Agent入口：一次invoke运行整张图
    - 初始state必须带上attempts和attempts_log：attempts参与加法运算，
      attempts_log是reducer键，两者都需要显式初始值
    - 默认每次提问使用全新thread_id：checkpointer会把输入归并进旧检查点，
      共用线程会导致attempts_log等状态在问题之间残留。
      阶段4做多轮会话时改为由调用方传入会话级thread_id
    """
    if thread_id is None:
        thread_id = uuid.uuid4().hex

    final_state = graph.invoke(
        {"user_question": user_question, "attempts": 0, "attempts_log": []},
        config={"configurable": {"thread_id": thread_id}},
    )

    route = final_state.get("route") or "data"
    field_docs = final_state.get("field_docs") or []
    attempts_log = final_state.get("attempts_log") or []

    return {
        "success": bool(final_state.get("success")) if route == "data" else True,
        "route": route,
        "user_question": user_question,
        "retrieved_docs": [doc.doc_id for doc in field_docs],
        "final_sql": final_state.get("sql"),
        "query_result": final_state.get("query_result"),
        "answer": final_state.get("answer") or "",
        "attempts": [{"sql": s, "error": e} for s, e in attempts_log],
    }


def main() -> None:
    graph = build_agent()
    print("==== AskData 数据问答Agent（LangGraph版）====")
    print("输入问题提问，输入 exit 退出，Ctrl+C 也可退出\n")

    while True:
        try:
            user_input = input("👤 用户：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 退出程序")
            break

        if user_input.lower() in ("exit", "quit", "q"):
            print("👋 退出程序")
            break
        if not user_input:
            continue

        try:
            output = ask_data_agent(graph, user_input)
        except Exception as e:
            print(f"\n❌ 系统异常：{e}")
            print("-" * 60)
            continue

        print(f"\n🤖 Agent回答：{output['answer']}")
        if output["route"] == "data":
            print(f"[DEBUG] 命中字段：{', '.join(output['retrieved_docs'])}")
            print(f"[DEBUG] 执行SQL：{output['final_sql']}")
            if not output["success"]:
                for i, att in enumerate(output["attempts"], 1):
                    print(f"[DEBUG] 第{i}轮SQL：{att['sql']}")
                    if att["error"]:
                        print(f"[DEBUG] 第{i}轮错误：{att['error']}")
            elif output["query_result"] is not None:
                qr = output["query_result"]
                flag = "（已截断）" if qr.is_truncated else ""
                print(f"[DEBUG] 返回 {qr.row_count} 行{flag}")
        print("-" * 60)


if __name__ == "__main__":
    main()
