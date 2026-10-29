"""生成确定性夹具和人工可审阅的标注题；不通过模型生成标准答案。"""
import json
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parents[2] / "evaluation"


def prepare():
    ROOT.mkdir(exist_ok=True)
    (ROOT / "datasets").mkdir(exist_ok=True)
    db = ROOT / "fixture.duckdb"
    if not db.exists():
        with duckdb.connect(str(db)) as con:
            con.execute("CREATE TABLE customers(customer_id INTEGER PRIMARY KEY,customer_name VARCHAR,customer_level VARCHAR,region VARCHAR)")
            con.execute("CREATE TABLE products(product_id INTEGER PRIMARY KEY,product_name VARCHAR,category VARCHAR)")
            con.execute("CREATE TABLE orders(order_id INTEGER PRIMARY KEY,order_date DATE,region VARCHAR,category VARCHAR,order_amount DECIMAL(12,2),paid_amount DECIMAL(12,2),status VARCHAR,customer_id INTEGER,product_id INTEGER)")
            regions = ["华东", "华南", "华北", "西部"]
            levels = ["战略客户", "重点客户", "普通客户"]
            categories = ["数码", "家居", "食品"]
            for i in range(1, 13):
                con.execute("INSERT INTO customers VALUES (?,?,?,?)", [i, f"客户{i:02}", levels[i % 3], regions[i % 4]])
            for i in range(1, 7):
                con.execute("INSERT INTO products VALUES (?,?,?)", [i, f"商品{i:02}", categories[i % 3]])
            for i in range(1, 181):
                customer, product = i % 12 + 1, (i * 5) % 6 + 1
                amount = 100 + (i * 37) % 1700
                con.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)", [
                    i, f"2026-{8 + (i // 3) % 3:02}-{i % 27 + 1:02}", regions[customer % 4],
                    categories[product % 3], amount, round(amount * .9, 2),
                    ["已支付", "已支付", "已支付", "已退款", "已取消"][i % 5], customer, product])
    cases = []

    def add(split, category, question, sql=None, fields=(), **extra):
        cases.append(dict(id=f"{split}-{sum(c['split']==split for c in cases)+1:03}", split=split,
                          family=category + '-' + str(sum(c['split']==split for c in cases)+1),
                          category=category, question=question, reference_sql=sql or [],
                          required_fields=list(fields), expected_action="query", mode="single",
                          ordered=False, now="2026-10-29T12:00:00+08:00", **extra))

    dev = [
        ("basic", "客户表一共有多少客户？", "SELECT COUNT(*) FROM customers", ["customers.customer_id"]),
        ("basic", "订单表一共有多少条订单？", "SELECT COUNT(*) FROM orders", ["orders.order_id"]),
        ("basic", "商品表一共有多少种商品？", "SELECT COUNT(*) FROM products", ["products.product_id"]),
        ("filter", "华东地区共有多少客户？", "SELECT COUNT(*) FROM customers WHERE region='华东'", ["customers.region"]),
        ("filter", "已支付的订单一共有多少条？", "SELECT COUNT(*) FROM orders WHERE status='已支付'", ["orders.status"]),
        ("metric", "所有订单的订单总金额合计多少？不要使用实付金额。", "SELECT SUM(order_amount) FROM orders", ["orders.order_amount"]),
        ("metric", "所有已支付订单的实付金额合计多少？", "SELECT SUM(paid_amount) FROM orders WHERE status='已支付'", ["orders.paid_amount", "orders.status"]),
        ("metric", "所有订单的实付金额平均值是多少？不按客户去重。", "SELECT AVG(paid_amount) FROM orders", ["orders.paid_amount"]),
        ("group", "按客户等级统计客户人数，返回等级和人数。", "SELECT customer_level,COUNT(*) FROM customers GROUP BY customer_level", ["customers.customer_level"]),
        ("group", "按地区统计订单数量，返回地区和订单数。", "SELECT region,COUNT(*) FROM orders GROUP BY region", ["orders.region"]),
        ("group", "按品类统计已支付订单的实付金额合计，返回品类和金额。", "SELECT category,SUM(paid_amount) FROM orders WHERE status='已支付' GROUP BY category", ["orders.category", "orders.paid_amount", "orders.status"]),
        ("time", "2026年9月下单的订单共有多少条？", "SELECT COUNT(*) FROM orders WHERE order_date>='2026-09-01' AND order_date<'2026-10-01'", ["orders.order_date"]),
        ("time", "2026年8月已支付订单实付金额合计多少？", "SELECT SUM(paid_amount) FROM orders WHERE status='已支付' AND order_date>='2026-08-01' AND order_date<'2026-09-01'", ["orders.order_date", "orders.status", "orders.paid_amount"]),
        ("distinct", "所有订单中有下单记录的不同客户数是多少？", "SELECT COUNT(DISTINCT customer_id) FROM orders", ["orders.customer_id"]),
        ("join", "按客户等级统计已支付订单实付金额合计，返回等级和金额。", "SELECT c.customer_level,SUM(o.paid_amount) FROM orders o JOIN customers c ON o.customer_id=c.customer_id WHERE o.status='已支付' GROUP BY c.customer_level", ["customers.customer_level", "customers.customer_id", "orders.customer_id", "orders.paid_amount", "orders.status"]),
        ("join", "按商品名称统计订单数量，返回商品名称和订单数。", "SELECT p.product_name,COUNT(*) FROM orders o JOIN products p ON o.product_id=p.product_id GROUP BY p.product_name", ["products.product_name", "products.product_id", "orders.product_id"]),
        ("empty", "查询华中地区客户的客户编号和客户名称。", "SELECT customer_id,customer_name FROM customers WHERE region='华中'", ["customers.customer_id", "customers.customer_name", "customers.region"]),
        ("empty", "2027年1月的订单数量是多少？", "SELECT COUNT(*) FROM orders WHERE order_date>='2027-01-01' AND order_date<'2027-02-01'", ["orders.order_date"]),
        ("ranking", "实付金额最高的3笔订单，返回订单编号和实付金额；金额相同按订单编号升序。", "SELECT order_id,paid_amount FROM orders ORDER BY paid_amount DESC,order_id LIMIT 3", ["orders.order_id", "orders.paid_amount"]),
        ("ratio", "所有订单中已退款订单所占比例是多少？返回0到1的小数。", "SELECT COUNT(*) FILTER(WHERE status='已退款')*1.0/COUNT(*) FROM orders", ["orders.status"]),
        ("having", "订单数量大于10的客户编号及订单数有哪些？", "SELECT customer_id,COUNT(*) FROM orders GROUP BY customer_id HAVING COUNT(*)>10", ["orders.customer_id"]),
        ("extreme", "订单最早和最晚下单日期分别是多少？返回两列。", "SELECT MIN(order_date),MAX(order_date) FROM orders", ["orders.order_date"]),
        ("metric", "所有订单中最大和最小订单总金额分别是多少？返回两列。", "SELECT MAX(order_amount),MIN(order_amount) FROM orders", ["orders.order_amount"]),
        ("filter", "华东地区已支付订单实付金额合计多少？", "SELECT SUM(paid_amount) FROM orders WHERE region='华东' AND status='已支付'", ["orders.region", "orders.status", "orders.paid_amount"]),
    ]
    holdout = [
        ("range", "订单总金额至少500且小于1000的订单数量是多少？", "SELECT COUNT(*) FROM orders WHERE order_amount>=500 AND order_amount<1000", ["orders.order_amount"]),
        ("conditional", "按订单状态返回订单数量和订单总金额合计，共三列。", "SELECT status,COUNT(*),SUM(order_amount) FROM orders GROUP BY status", ["orders.status", "orders.order_amount"]),
        ("join_distinct", "按客户等级统计有已支付订单的去重客户数量，返回等级和客户数。", "SELECT c.customer_level,COUNT(DISTINCT o.customer_id) FROM orders o JOIN customers c ON c.customer_id=o.customer_id WHERE o.status='已支付' GROUP BY c.customer_level", ["customers.customer_level", "customers.customer_id", "orders.customer_id", "orders.status"]),
        ("weighted", "每个购买客户的已支付实付总金额的平均值是多少？不是每笔订单的平均金额。", "SELECT SUM(paid_amount)/COUNT(DISTINCT customer_id) FROM orders WHERE status='已支付'", ["orders.customer_id", "orders.paid_amount", "orders.status"]),
        ("date_group", "按2026年的自然月份统计已支付订单实付总额，返回月份数字和金额。", "SELECT MONTH(order_date),SUM(paid_amount) FROM orders WHERE YEAR(order_date)=2026 AND status='已支付' GROUP BY MONTH(order_date)", ["orders.order_date", "orders.status", "orders.paid_amount"]),
        ("cross_filter", "战略客户在2026年10月的所有订单实付金额合计多少？包含所有订单状态。", "SELECT SUM(o.paid_amount) FROM orders o JOIN customers c ON o.customer_id=c.customer_id WHERE c.customer_level='战略客户' AND o.order_date>='2026-10-01' AND o.order_date<'2026-11-01'", ["customers.customer_level", "customers.customer_id", "orders.customer_id", "orders.order_date", "orders.paid_amount"]),
        ("net", "2026年9月订单的订单总金额与实付金额之差合计多少？包含所有订单状态。", "SELECT SUM(order_amount-paid_amount) FROM orders WHERE order_date>='2026-09-01' AND order_date<'2026-10-01'", ["orders.order_date", "orders.order_amount", "orders.paid_amount"]),
        ("top_group", "按已支付订单实付总额降序列出前2个品类及金额，相同按品类升序。", "SELECT category,SUM(paid_amount) AS total FROM orders WHERE status='已支付' GROUP BY category ORDER BY total DESC,category LIMIT 2", ["orders.category", "orders.paid_amount", "orders.status"]),
        ("anti_join", "哪些商品没有已支付订单？返回商品编号和名称。", "SELECT p.product_id,p.product_name FROM products p WHERE NOT EXISTS(SELECT 1 FROM orders o WHERE o.product_id=p.product_id AND o.status='已支付')", ["products.product_id", "products.product_name", "orders.product_id", "orders.status"]),
        ("distinct_pair", "订单中不同地区与品类组合共有多少种？", "SELECT COUNT(*) FROM (SELECT DISTINCT region,category FROM orders)", ["orders.region", "orders.category"]),
        ("conditional_sum", "分别返回所有已支付订单和已退款订单的实付金额合计，输出两列。", "SELECT SUM(CASE WHEN status='已支付' THEN paid_amount ELSE 0 END),SUM(CASE WHEN status='已退款' THEN paid_amount ELSE 0 END) FROM orders", ["orders.status", "orders.paid_amount"]),
        ("date_boundary", "2026年9月1日至9月15日（含两端）的订单数量是多少？", "SELECT COUNT(*) FROM orders WHERE order_date BETWEEN '2026-09-01' AND '2026-09-15'", ["orders.order_date"]),
    ]
    for split, rows in [("dev", dev), ("holdout", holdout)]:
        for category, question, sql, fields in rows:
            add(split, category, question, [sql], fields)
    # Multi 和多轮按事实结果打分，不强制任务数量。
    for split, region in [("dev", "华东"), ("holdout", "华南")]:
        add(split, "multi", f"分别统计{region}的客户数量与该地区已支付订单实付总额，清楚标注两个数字。",
            [f"SELECT COUNT(*) FROM customers WHERE region='{region}'",
             f"SELECT SUM(paid_amount) FROM orders WHERE region='{region}' AND status='已支付'"],
            ["customers.region", "orders.region", "orders.status", "orders.paid_amount"],
            execution_mode="multi")
        add(split, "dialogue", "那已退款的呢？返回数量。",
            [f"SELECT COUNT(*) FROM orders WHERE region='{region}' AND status='已退款'"],
            ["orders.region", "orders.status"],
            turns=[f"{region}地区已支付订单有多少条？返回数量。", "那已退款的呢？返回数量。"])
        add(split, "clarification", "分析客户表现好不好。" if split == "dev" else "帮我看看经营状况是否达标。",
            execution_mode="multi", action="clarification")
    for case in cases:
        case["mode"] = case.pop("execution_mode", "single")
        case["expected_action"] = case.pop("action", "query")
        case["ordered"] = case["category"] in ("ranking", "top_group")
    # 金标准由独立裸 DuckDB 执行，不经过被测执行器。
    with duckdb.connect(str(db), read_only=True) as con:
        for case in cases:
            case["expected_results"] = [
                [[float(v) if hasattr(v, "as_tuple") else v.isoformat() if hasattr(v, "isoformat") else v for v in row]
                 for row in con.execute(sql).fetchall()] for sql in case["reference_sql"]]
    for split in ("dev", "holdout"):
        path = ROOT / "datasets" / f"{split}.jsonl"
        path.write_text(''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in cases if c['split']==split), encoding="utf-8")
    smoke = [c for c in cases if c["id"] in ("dev-001", "dev-007", "dev-012", "dev-015", "dev-019", "dev-025")]
    (ROOT / "datasets" / "smoke.jsonl").write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in smoke),encoding="utf-8")
    print(f"Prepared {len(cases)} cases (smoke subset={len(smoke)}) in {ROOT}")


if __name__ == "__main__":
    prepare()
