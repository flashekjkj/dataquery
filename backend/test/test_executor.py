# test/test_executor.py
from sql.executor import SQLExecutor, SqlExecuteError

def main():
    db_path = r"E:\\desktop\\LLM_Project\\askdata\\backend\\data\\demo_ecom.duckdb"
    executor = SQLExecutor(db_path)

    print("===== 测试1：普通SELECT查询（无LIMIT，自动补LIMIT 100） =====")
    try:
        result = executor.execute("SELECT * FROM customers;")
        print("列名：", result.columns)
        print("行数：", result.row_count)
        print("是否截断：", result.is_truncated)
        print("前2行数据：", result.rows[:5])
    except SqlExecuteError as e:
        print("查询失败：", e.error_msg)

    print("\n===== 测试2：危险语句拦截测试(DROP) =====")
    try:
        executor.execute("DROP TABLE customers;")
    except SqlExecuteError as e:
        print("预期拦截成功：", e.error_msg)

    print("\n===== 测试3：字段采样 sample_values =====")
    try:
        samples = executor.sample_values("orders", "status", n=5)
        print("status字段样例值：", samples)
    except SqlExecuteError as e:
        print("采样失败：", e.error_msg)

if __name__ == "__main__":
    main()
