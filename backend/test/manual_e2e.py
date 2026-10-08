from __future__ import annotations
import sys
from pathlib import Path

BASE_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_BACKEND))

from main import build_agent, ask_data_agent

QUESTIONS = [
    "你好，介绍一下你自己",
    "华东地区销售额是多少",
    "每个客户等级的订单数量",
    "各商品品类的销售额排名",
    "退款订单的总金额",
]


def main():
    graph = build_agent()

    for q in QUESTIONS:
        print("=" * 60)
        print(f"问题：{q}")
        try:
            out = ask_data_agent(graph, q)
        except Exception as e:
            print(f"❌ 异常：{type(e).__name__}: {e}")
            continue

        print(f"路由：{out['route']} 成功：{out['success']}")
        if out["route"] == "data":
            print(f"命中字段：{out['retrieved_docs']}")
            print(f"SQL：{out['final_sql']}")
            if out["query_result"]:
                qr = out["query_result"]
                print(f"结果：{qr.row_count} 行 列={qr.columns} 截断={qr.is_truncated}")
                print(f"前5行：{qr.rows[:5]}")
            for i, a in enumerate(out["attempts"], 1):
                print(f"  第{i}轮：{a['sql']}")
                if a["error"]:
                    print(f"  第{i}轮错误：{a['error']}")
        print(f"回答：{out['answer']}")


if __name__ == "__main__":
    main()
