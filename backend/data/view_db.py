import duckdb
from pathlib import Path

DB_PATH = Path(__file__).parent / "demo_ecom.duckdb"

con = duckdb.connect(str(DB_PATH))

print("==== customers 客户表（前10行）====")
res = con.execute("SELECT * FROM customers LIMIT 10;").fetchall()
for row in res:
    print(row)

print("\n==== products 商品表（前10行）====")
res = con.execute("SELECT * FROM products LIMIT 10;").fetchall()
for row in res:
    print(row)

print("\n==== orders 订单表（前10行）====")
res = con.execute("SELECT * FROM orders LIMIT 10;").fetchall()
for row in res:
    print(row)

# 查看表结构（字段+类型）
print("\n==== 表结构示例 DESCRIBE customers ====")
print(con.execute("DESCRIBE customers;").fetchall())

con.close()
