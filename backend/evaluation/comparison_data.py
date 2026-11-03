"""单/多智能体配对实验：不依赖上一次留出集成绩。"""
import json
import random
from pathlib import Path
import duckdb

ROOT=Path(__file__).resolve().parents[2]/'evaluation'/'single-multi'


def prepare():
    ROOT.mkdir(parents=True,exist_ok=True)
    definitions=[]
    def add(id,group,question,sqls=(),turns=None):
        if group=='compound':
            question+=' 结果可用多个表、带明确结果类型标签的联合表，或包含各结果的JSON；保留名称、月份和数值的对应关系。'
        definitions.append(dict(id=id,group=group,question=question,reference_sql=list(sqls),
                                turns=turns or [question],now='2026-11-02T12:00:00+08:00'))
    paid="status='已支付'"
    sep="order_date>='2026-09-01' AND order_date<'2026-10-01'"
    oct="order_date>='2026-10-01' AND order_date<'2026-11-01'"
    add('S1','simple','2026年9月已支付订单有多少条？', [f'SELECT COUNT(*) FROM orders WHERE {paid} AND {sep}'])
    add('S2','simple','按商品名称统计所有订单数量，返回商品名称和数量。', ['SELECT p.product_name,COUNT(*) FROM orders o JOIN products p ON o.product_id=p.product_id GROUP BY p.product_name'])
    add('S3','simple','按地区统计2026年10月已支付订单的去重客户数和实付总额，返回地区、客户数、金额三列。',[f'SELECT region,COUNT(DISTINCT customer_id),SUM(paid_amount) FROM orders WHERE {paid} AND {oct} GROUP BY region'])
    add('C1','compound','分析2026年9月与10月的已支付订单：分别返回每月实付总额、订单数和去重客户数，并列出各品类10月减9月的实付总额差值。月份用数字表示，变化是描述性比较，不要求解释因果。',[
        f'SELECT MONTH(order_date),SUM(paid_amount),COUNT(*),COUNT(DISTINCT customer_id) FROM orders WHERE {paid} AND order_date>=\'2026-09-01\' AND order_date<\'2026-11-01\' GROUP BY MONTH(order_date)',
        f"SELECT category,SUM(CASE WHEN {oct} THEN paid_amount ELSE 0 END)-SUM(CASE WHEN {sep} THEN paid_amount ELSE 0 END) FROM orders WHERE {paid} GROUP BY category"])
    add('C2','compound','请分别回答三个问题：全部客户有多少人；2026年10月已支付实付总额最高的两个商品名称和金额（同额按商品名称升序）；各地区所有订单中退款订单的比例（0到1小数）。',[
        'SELECT COUNT(*) FROM customers',f"SELECT p.product_name,SUM(o.paid_amount) total FROM orders o JOIN products p ON o.product_id=p.product_id WHERE {paid} AND {oct} GROUP BY p.product_name ORDER BY total DESC,p.product_name LIMIT 2",
        "SELECT region,COUNT(*) FILTER(WHERE status='已退款')*1.0/COUNT(*) FROM orders GROUP BY region"])
    add('C3','compound','针对2026年10月已支付订单，按客户等级返回去重购买客户数、实付总额和人均实付金额（实付总额除以去重购买客户数），明确区分客户数和订单数。',[
        f'SELECT c.customer_level,COUNT(DISTINCT o.customer_id),SUM(o.paid_amount),SUM(o.paid_amount)/COUNT(DISTINCT o.customer_id) FROM orders o JOIN customers c ON o.customer_id=c.customer_id WHERE {paid} AND {oct} GROUP BY c.customer_level'])
    add('C4','compound','对每个地区比较2026年9月与10月的已支付实付总额，返回地区、9月金额、10月金额和差值（10月减9月），没有订单的月份按0计；再返回两个自然月合计的已支付订单数量。',[
        f"SELECT region,SUM(CASE WHEN {sep} THEN paid_amount ELSE 0 END),SUM(CASE WHEN {oct} THEN paid_amount ELSE 0 END),SUM(CASE WHEN {oct} THEN paid_amount ELSE 0 END)-SUM(CASE WHEN {sep} THEN paid_amount ELSE 0 END) FROM orders WHERE {paid} GROUP BY region",
        f"SELECT COUNT(*) FROM orders WHERE {paid} AND order_date>='2026-09-01' AND order_date<'2026-11-01'"])
    add('C5','compound','流失客户定义为2026年9月有已支付订单而10月没有已支付订单。列出这些客户的编号和名称，并分别给出9月和10月的去重购买客户数，月份用数字表示。',[
        f"SELECT c.customer_id,c.customer_name FROM customers c WHERE EXISTS(SELECT 1 FROM orders o WHERE o.customer_id=c.customer_id AND {paid} AND {sep}) AND NOT EXISTS(SELECT 1 FROM orders o WHERE o.customer_id=c.customer_id AND {paid} AND {oct})",
        f"SELECT MONTH(order_date),COUNT(DISTINCT customer_id) FROM orders WHERE {paid} AND order_date>='2026-09-01' AND order_date<'2026-11-01' GROUP BY MONTH(order_date)"])
    q='换成已退款的，仍按地区返回数量和实付总额。'
    add('D1','dialogue',q,[f"SELECT region,COUNT(*),SUM(paid_amount) FROM orders WHERE status='已退款' AND {oct} GROUP BY region"],
        ['按地区统计2026年10月已支付订单数量和实付总额，返回地区、数量和金额。',q])
    q='改成10月，仍只看华东已支付订单，并返回去重客户数和实付金额合计。'
    add('D2','dialogue',q,[f"SELECT COUNT(DISTINCT customer_id),SUM(paid_amount) FROM orders WHERE region='华东' AND {paid} AND {oct}"],
        ['2026年9月华东地区已支付订单的实付金额合计多少？',q])
    add('A1','ambiguous','请评价我们的销售额是否达标。')
    add('A2','ambiguous','哪些客户表现不好，帮我找出来。')
    cases=[]
    for variant,seed in [('A',817),('B',1949)]:
        db=ROOT/f'fixture-{variant}.duckdb'
        if not db.exists():
            rng=random.Random(seed)
            with duckdb.connect(str(db)) as con:
                con.execute('CREATE TABLE customers(customer_id INTEGER,customer_name VARCHAR,customer_level VARCHAR,region VARCHAR)')
                con.execute('CREATE TABLE products(product_id INTEGER,product_name VARCHAR,category VARCHAR)')
                con.execute('CREATE TABLE orders(order_id INTEGER,order_date DATE,region VARCHAR,category VARCHAR,order_amount DECIMAL(12,2),paid_amount DECIMAL(12,2),status VARCHAR,customer_id INTEGER,product_id INTEGER)')
                regions=['华东','华南','华北','西部']; levels=['普通客户','重点客户','战略客户']; categories=['数码','家居','食品']
                customers={i:(f'客户{i:02}',rng.choice(levels),rng.choice(regions)) for i in range(1,19)}
                products={i:(f'商品{i:02}',rng.choice(categories)) for i in range(1,9)}
                con.executemany('INSERT INTO customers VALUES (?,?,?,?)',[(i,*v) for i,v in customers.items()])
                con.executemany('INSERT INTO products VALUES (?,?,?)',[(i,*v) for i,v in products.items()])
                rows=[]
                for i in range(1,222):
                    month=rng.choices([8,9,10],[2,5,3])[0]
                    # 部分客户只在9月出现，使流失查询不是空结果巧合。
                    cid=rng.randint(1,18 if month==9 else 14); pid=rng.randint(1,8)
                    amount=rng.randint(1300,199900)/100
                    rows.append((i,f'2026-{month:02}-{rng.randint(1,28):02}',customers[cid][2],products[pid][1],amount,
                                 round(amount*rng.choice([.75,.9,1]),2),rng.choices(['已支付','已退款','已取消'],[6,2,2])[0],cid,pid))
                con.executemany('INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)',rows)
        with duckdb.connect(str(db),read_only=True) as con:
            for definition in definitions:
                c={**definition,'variant':variant,'pair_id':variant+'-'+definition['id'],'database':db.name}
                c['expected_results']=[[[float(v) if hasattr(v,'as_tuple') else v for v in row] for row in con.execute(sql).fetchall()] for sql in c['reference_sql']]
                cases.append(c)
    (ROOT/'cases.jsonl').write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in cases),encoding='utf-8')
    print(ROOT, len(cases),'paired cases')
    return cases


if __name__=='__main__':
    prepare()
