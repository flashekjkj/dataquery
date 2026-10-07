from __future__ import annotations
import random
from datetime import date,timedelta
from pathlib import Path
import duckdb

# 文件配置
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "demo_ecom.duckdb"

REGIONS = ["华东","华南","华北","西南"]
CUSTOMER_LEVELS = ["战略客户","重点客户","普通客户"]
CATEGORIES = ["数据产品","企业服务","智能硬件","安全服务"]

def seed_demo_data(seed:int = 20260815) -> dict[str,int]:
    """
    主函数：生成模拟电商数据，写入duckdb数据库
    :param seed:随机种子
    :return 返回每张表的行数
    """
    rng = random.Random(seed)

    # 1.生成3张表的原始数据
    customers = _gen_customers(rng)
    products = _gen_products()
    orders = _gen_orders(rng,customers,products,count=600)

    # 2.链接DuchDB，建表+插入数据
    con = duckdb.connect(str(DB_PATH))
    # 删除旧表
    con.execute("DROP TABLE IF EXISTS customers;")
    con.execute("DROP TABLE IF EXISTS products;")
    con.execute("DROP TABLE IF EXISTS orders;")
    # 创建新表
    con.execute("""
    CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    customer_name VARCHAR,
    customer_level VARCHAR,
    region VARCHAR
    );    
    """)
    con.execute("""
    CREATE TABLE products (
    product_id INTEGER PRIMARY KEY,
    product_name VARCHAR,
    category VARCHAR
    );    
    """)
    con.execute("""
    CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    region VARCHAR,
    category VARCHAR,
    order_amount DECIMAL(12,2),
    paid_amount DECIMAL(12,2),
    status VARCHAR,
    order_date DATE,
    customer_id INTEGER,
    product_id INTEGER,
    FOREIGN KEY(customer_id) REFERENCES customers(customer_id),
    FOREIGN KEY(product_id) REFERENCES products(product_id)
    );    
    """)

    # 批量插入数据
    con.executemany("INSERT INTO customers VALUES (?,?,?,?)",customers)
    con.executemany("INSERT INTO products VALUES (?,?,?)",products)
    con.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)",orders)

    # 查询每张表行数，返回统计信息
    cnt_cust = con.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    cnt_prod = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    cnt_order = con.execute("SELECT COUNT(*) FROM orders").fetchone()[0]

    con.close()
    return {
        "customers":cnt_cust,
        "products":cnt_prod,
        "orders":cnt_order
    }

def _gen_customers(rng:random.Random) -> list[tuple]:
    """生成客户表数据"""
    prefixes = [
        "远景", "云帆", "星河", "海岳", "北辰", "南岭", "新程", "华耀", "澄明", "启元",
        "锐达", "安澜", "青禾", "鼎盛", "卓远", "同创", "恒信", "博源", "嘉禾", "光启",
        "飞鸿", "中科", "朗新", "合众", "德润", "天成", "金石", "智联", "融通", "万象",        
    ]
    suffixes = ["科技", "数据", "制造", "零售", "网络", "集团"]
    rows = []
    for idx,prefix in enumerate(prefixes,101):
        level = rng.choices(CUSTOMER_LEVELS,weights=[2,4,6],k=1)[0]
        region = REGIONS[(idx-101)%len(REGIONS)]
        rows.append((idx,f"{prefix}{rng.choice(suffixes)}",level,region))
    return rows

def _gen_products() -> list[tuple]:
    """生成商品表数据"""
    product_list = [
        ("智能分析平台", "数据产品"),
        ("实时指标中心", "数据产品"),
        ("经营驾驶舱", "数据产品"),
        ("企业协同套件", "企业服务"),
        ("客户运营服务", "企业服务"),
        ("数据治理咨询", "企业服务"),
        ("边缘计算终端", "智能硬件"),
        ("智能采集网关", "智能硬件"),
        ("工业传感套件", "智能硬件"),
        ("零信任接入", "安全服务"),
        ("数据脱敏服务", "安全服务"),
        ("安全审计平台", "安全服务"),        
    ]
    return [(idx,name,category) for idx,(name,category) in enumerate(product_list,101)]

def _gen_orders(rng:random.Random,customers:list[tuple],products:list[tuple],count:int) -> list[tuple]:
    """生成订单表数据"""
    rows = []
    start_id = 10001
    start_date = date(2026,6,1)
    day_span = 90

    for offset in range(count):
        # 随机选一个客户、一个商品
        customer_id,_,customer_level,region = rng.choice(customers)
        product_id,_,category = rng.choice(products)
        # 客户等级影响订单金额
        level_multiplier = {"战略客户": 1.8, "重点客户": 1.25, "普通客户": 0.8}[customer_level]
        order_amount = rng.uniform(8_000,120_000) * level_multiplier
        # 偶尔生成超大订单
        if offset and offset % 79 == 0:
            order_amount *= 3.5

        # 订单状态
        status = rng.choices(["已支付", "已取消", "已退款"],weights=[84,9,7],k=1)[0]
        if status == "已支付":
            paid_amount = round(order_amount * rng.uniform(0.86,1.0),2)
        else:
            paid_amount = 0.0

        order_date = start_date + timedelta(days=rng.randrange(day_span))
        rows.append((
            start_id+offset,
            region,
            category,
            round(order_amount,2),
            paid_amount,
            status,
            order_date,
            customer_id,
            product_id
        ))
    return rows

if __name__ == "__main__":
    table_counts = seed_demo_data()
    print("DuckDB演示数据生成完成！")
    for table_name,row_count in table_counts.items():
        print(f" - {table_name}:{row_count}行")
    print(f"\n数据库文件路径：{DB_PATH}")