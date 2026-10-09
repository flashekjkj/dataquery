from __future__ import annotations
import sys
import uuid
from pathlib import Path

BASE_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_BACKEND))

from main import build_agent, ask_data_agent

# 同一会话内的多轮追问，验证指代消解与上下文连贯
QUESTIONS = [
    "华东地区销售额是多少",
    "那华南呢？",
    "按商品品类拆一下",
]


def main():
    graph, conv_memory = build_agent()
    thread_id = uuid.uuid4().hex
    print(f"会话ID：{thread_id}\n")

    for q in QUESTIONS:
        print("=" * 60)
        print(f"问题：{q}")
        try:
            out = ask_data_agent(graph, q, thread_id)
        except Exception as e:
            print(f"❌ 异常：{type(e).__name__}: {e}")
            continue

        print(f"路由：{out['route']} 成功：{out['success']}")
        if out["rewritten_question"] and out["rewritten_question"] != q:
            print(f"指代消解：{q} → {out['rewritten_question']}")
        if out["route"] == "data":
            print(f"命中字段：{out['retrieved_docs']}")
            print(f"SQL：{out['final_sql']}")
            if out["query_result"]:
                qr = out["query_result"]
                print(f"结果：{qr.row_count} 行 列={qr.columns}")
                print(f"前5行：{qr.rows[:5]}")
            for i, a in enumerate(out["attempts"], 1):
                if a["error"]:
                    print(f"  第{i}轮错误：{a['error']}")
        print(f"回答：{out['answer']}")

    print("=" * 60)
    print("会话历史：")
    print(conv_memory.recent_context(thread_id, n=20))


if __name__ == "__main__":
    main()
