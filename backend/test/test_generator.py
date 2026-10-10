# test_generator.py
from __future__ import annotations
import sys
from pathlib import Path

BASE_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_BACKEND))

from sql.generator import generate_sql_with_repair
from sql.executor import SQLExecutor
from app.model_client import ModelClient

def main():
    db_path = r"E:\\desktop\\LLM_Project\\askdata\\backend\\data\\demo_ecom.duckdb"
    executor = SQLExecutor(db_path)
    model_client = ModelClient(mock=True)

    # ========== 模拟输入（对接检索模块时会替换成真实召回结果） ==========
    question = "查询订单总金额"
    # 模拟召回得到的FieldDocument列表，这里mock占位，mock LLM不会读取内部内容
    field_docs = []
    relations = []
    value_samples = {}

    res = generate_sql_with_repair(
        question=question,
        field_docs=field_docs,
        relations=relations,
        value_samples=value_samples,
        model_client=model_client,
        executor=executor,
        max_attempts=3
    )

    print("success:", res.success)
    print("final_sql:", res.final_sql)
    print("attempts记录：")
    for idx, item in enumerate(res.attempts):
        print(f"第{idx+1}轮 sql={item.sql}, error={item.error}")
    if res.success:
        print("查询结果：", res.query_result)

if __name__ == "__main__":
    main()
