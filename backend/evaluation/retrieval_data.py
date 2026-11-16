"""检索消融使用的固定标注，开发与留出问题在调参前一并生成。"""
import json
from pathlib import Path

import duckdb

from datasources.demo import demo_query_context
from datasources.models import ColumnSchema, QueryContext, SchemaSnapshot, TableSchema
from datasources.semantics import demo_semantics

ROOT = Path(__file__).resolve().parents[2] / 'evaluation' / 'retrieval-v2'


def prepare():
    ROOT.mkdir(parents=True, exist_ok=True)
    if (ROOT/'datasets.json').exists():
        raise ValueError('标注已存在，请使用新的实验目录')
    ecommerce = demo_query_context()
    ecommerce = ecommerce.model_copy(update={'semantics':demo_semantics(ecommerce.snapshot).model_dump()})
    definitions = [
        ('hr','applications','招聘申请，每个求职者提交的岗位申请记录',[
            ('application_id','申请编号','identifier'),('vacancy_id','岗位编号外键','foreign_key'),
            ('pipeline_stage','招聘阶段 offer/interview/rejected','filter'),('applied_at','申请日期','time')]),
        ('hr','vacancies','正在招聘的岗位职位，关联部门',[
            ('vacancy_id','岗位编号','identifier'),('role_title','岗位职位名称','dimension'),
            ('department_id','部门编号外键','foreign_key')]),
        ('org','departments','企业组织部门',[
            ('department_id','部门编号','identifier'),('department_name','部门名称','dimension')]),
        ('finance','billing','财务收款发票，与招聘无关',[
            ('invoice_id','发票编号','identifier'),('paid_amount','收款金额','metric')]),
        ('finance','refunds','财务退款，与招聘无关',[
            ('refund_id','退款编号','identifier'),('refund_amount','退款金额','metric')]),
    ]
    for i in range(20):
        definitions.append(('analytics',f'activity_{i:02}',f'网站活动日志分区{i}，业务状态和活动金额，非招聘记录',[
            ('event_id','活动编号','identifier'),('status','活动状态','filter'),
            ('created_at','活动日期','time'),('amount','活动金额','metric')]))
    tables = []
    statements = ['CREATE SCHEMA hr','CREATE SCHEMA org','CREATE SCHEMA finance','CREATE SCHEMA analytics']
    for schema,name,description,fields in definitions:
        columns = []
        sql_fields = []
        for column,label,role in fields:
            dtype = 'INTEGER' if column.endswith('_id') else 'DATE' if role=='time' else 'DOUBLE' if role=='metric' else 'VARCHAR'
            columns.append(ColumnSchema(name=column,type=dtype,label=label,description=label,role=role))
            sql_fields.append(f'"{column}" {dtype}')
        tables.append(TableSchema(id=f'"{schema}"."{name}"',name=name,schema_name=schema,
                                  description=description,primary_key=[fields[0][0]],fields=columns))
        statements.append(f'CREATE TABLE "{schema}"."{name}" ({",".join(sql_fields)})')
    statements.extend([
        "INSERT INTO org.departments VALUES (1,'Engineering'),(2,'Operations')",
        "INSERT INTO hr.vacancies VALUES (1,'Engineer',1),(2,'Analyst',2),(3,'Designer',1)",
        "INSERT INTO hr.applications VALUES (1,1,'offer','2026-09-02'),(2,1,'interview','2026-09-10'),(3,2,'offer','2026-10-01'),(4,3,'rejected','2026-10-12'),(5,1,'offer','2026-10-20')",
        'INSERT INTO finance.billing VALUES (1,200),(2,300)',
        'INSERT INTO finance.refunds VALUES (1,50)',
    ])
    fixture_sql = ';\n'.join(statements)+';\n'
    (ROOT/'company.sql').write_text(fixture_sql,encoding='utf-8')
    with duckdb.connect(str(ROOT/'company.duckdb')) as connection:
        connection.execute(fixture_sql)
    company = QueryContext(snapshot=SchemaSnapshot(source_id='company',dialect='duckdb',tables=tables),semantics={
        'joins':{
            'application_vacancy':{'left':'"hr"."applications"','right':'"hr"."vacancies"','keys':[['vacancy_id','vacancy_id']]},
            'vacancy_department':{'left':'"hr"."vacancies"','right':'"org"."departments"','keys':[['department_id','department_id']]},
        },'time_fields':{'"hr"."applications"':'applied_at'}})
    cases = []
    def add(split,source,category,question,sql,fields,e2e=False):
        cases.append({'id':f'{split}-{source}-{len(cases)+1:03}', 'split':split,'source':source,
                      'category':category,'question':question,'sql':sql,'required_fields':fields,'e2e':e2e})
    legacy = ROOT.parent/'datasets/dev.jsonl'
    for line in legacy.read_text(encoding='utf-8').splitlines():
        case = json.loads(line)
        if case['mode']=='single' and not case.get('turns'):
            add('dev','demo','legacy-'+case['category'],case['question'],case['reference_sql'][0],case['required_fields'])
    for split,status,month in [('dev','已支付',9),('holdout','已退款',10)]:
        ecom_rows = [
            ('identifier',f'orders表按paid_amount降序取前两笔，给出order_id和paid_amount。',
             'SELECT order_id,paid_amount FROM orders ORDER BY paid_amount DESC,order_id LIMIT 2',['orders.order_id','orders.paid_amount']),
            ('synonym',f'收银实际到账款项一共多少？只包含状态为{status}的订单。',
             f"SELECT SUM(paid_amount) FROM orders WHERE status='{status}'",['orders.paid_amount','orders.status']),
            ('join',f'每种商品名称对应的{status}订单有多少条？给出商品名称和笔数。',
             f"SELECT p.product_name,COUNT(*) FROM orders o JOIN products p ON o.product_id=p.product_id WHERE o.status='{status}' GROUP BY p.product_name",['products.product_name','products.product_id','orders.product_id','orders.status']),
            ('time',f'2026年{month}月{status}订单平均实际付款多少？',
             f"SELECT AVG(paid_amount) FROM orders WHERE status='{status}' AND order_date>='2026-{month:02}-01' AND order_date<'2026-{month+1:02}-01'",['orders.paid_amount','orders.status','orders.order_date']),
            ('ambiguous',f'商品档案中每个品类有多少种商品？不要统计订单。',
             'SELECT category,COUNT(*) FROM products GROUP BY category',['products.category']),
            ('count','客户信息档案的总记录数是多少？','SELECT COUNT(*) FROM customers',[]),
            ('join',f'分客户等级列出{status}订单的实付金额总和。',
             f"SELECT c.customer_level,SUM(o.paid_amount) FROM orders o JOIN customers c ON o.customer_id=c.customer_id WHERE o.status='{status}' GROUP BY c.customer_level",['customers.customer_level','customers.customer_id','orders.customer_id','orders.status','orders.paid_amount']),
            ('metric','订单原价总额与实际支付总额分别是多少？两列返回。',
             'SELECT SUM(order_amount),SUM(paid_amount) FROM orders',['orders.order_amount','orders.paid_amount']),
        ]
        if split == 'holdout':
            ecom_rows[0] = ('identifier','orders按paid_amount升序取三条，列出order_id、paid_amount。',
                            'SELECT order_id,paid_amount FROM orders ORDER BY paid_amount,order_id LIMIT 3',['orders.order_id','orders.paid_amount'])
            ecom_rows[4] = ('ambiguous','客户信息中各等级分别有多少人？',
                            'SELECT customer_level,COUNT(*) FROM customers GROUP BY customer_level',['customers.customer_level'])
            ecom_rows[5] = ('count','客户档案内华南地区共有几条记录？',
                            "SELECT COUNT(*) FROM customers WHERE region='华南'",['customers.region'])
            ecom_rows[7] = ('metric','分别求订单原价金额与实际支付金额的平均值，返回两列。',
                            'SELECT AVG(order_amount),AVG(paid_amount) FROM orders',['orders.order_amount','orders.paid_amount'])
        for i,(category,question,sql,fields) in enumerate(ecom_rows):
            add(split,'demo',category,question,sql,fields,e2e=split=='holdout' and i in (1,2,3,6))
        stage = 'offer' if split=='dev' else 'interview'
        app = '"hr"."applications"'
        job = '"hr"."vacancies"'
        department = '"org"."departments"'
        company_rows = [
            ('identifier','按hr.applications.pipeline_stage分组统计申请记录数，给出阶段和数量。',
             'SELECT pipeline_stage,COUNT(*) FROM hr.applications GROUP BY pipeline_stage',[app+'.pipeline_stage']),
            ('new_domain',f'招聘流程处于{stage}阶段的申请有多少条？',
             f"SELECT COUNT(*) FROM hr.applications WHERE pipeline_stage='{stage}'",[app+'.pipeline_stage']),
            ('cross_schema',f'每个部门名称下有多少个招聘岗位？给出部门名称和岗位数。',
             'SELECT d.department_name,COUNT(*) FROM hr.vacancies v JOIN org.departments d ON v.department_id=d.department_id GROUP BY d.department_name',[job+'.department_id',department+'.department_id',department+'.department_name']),
            ('renamed_join',f'每个岗位职位名称对应多少条{stage}申请？返回岗位名称和申请数。',
             f"SELECT v.role_title,COUNT(*) FROM hr.applications a JOIN hr.vacancies v ON a.vacancy_id=v.vacancy_id WHERE a.pipeline_stage='{stage}' GROUP BY v.role_title",[app+'.vacancy_id',app+'.pipeline_stage',job+'.vacancy_id',job+'.role_title']),
            ('time',f'2026年{month}月提交的招聘申请数是多少？',
             f"SELECT COUNT(*) FROM hr.applications WHERE applied_at>='2026-{month:02}-01' AND applied_at<'2026-{month+1:02}-01'",[app+'.applied_at']),
            ('multi_hop',f'按部门名称统计{stage}申请数量，返回部门名称和申请数量。',
             f"SELECT d.department_name,COUNT(*) FROM hr.applications a JOIN hr.vacancies v ON a.vacancy_id=v.vacancy_id JOIN org.departments d ON v.department_id=d.department_id WHERE a.pipeline_stage='{stage}' GROUP BY d.department_name",[app+'.vacancy_id',app+'.pipeline_stage',job+'.vacancy_id',job+'.department_id',department+'.department_id',department+'.department_name']),
            ('distractor','财务收款发票的收款金额合计多少？',
             'SELECT SUM(paid_amount) FROM finance.billing',['"finance"."billing".paid_amount']),
            ('distractor','财务退款金额总和是多少？',
             'SELECT SUM(refund_amount) FROM finance.refunds',['"finance"."refunds".refund_amount']),
        ]
        if split == 'holdout':
            company_rows[0] = ('identifier','对hr.applications按vacancy_id分组，返回岗位编号和申请条数。',
                               'SELECT vacancy_id,COUNT(*) FROM hr.applications GROUP BY vacancy_id',[app+'.vacancy_id'])
            company_rows[2] = ('cross_schema','Engineering部门名下有几个招聘岗位？',
                               "SELECT COUNT(*) FROM hr.vacancies v JOIN org.departments d ON v.department_id=d.department_id WHERE d.department_name='Engineering'",[job+'.department_id',department+'.department_id',department+'.department_name'])
            company_rows[6] = ('distractor','财务发票平均每笔到账金额是多少？',
                               'SELECT AVG(paid_amount) FROM finance.billing',['"finance"."billing".paid_amount'])
            company_rows[7] = ('distractor','财务退款中最大一笔金额为多少？',
                               'SELECT MAX(refund_amount) FROM finance.refunds',['"finance"."refunds".refund_amount'])
        for i,(category,question,sql,fields) in enumerate(company_rows):
            add(split,'company',category,question,sql,fields,e2e=split=='holdout' and i in (1,3,4,5))
    (ROOT/'contexts.json').write_text(json.dumps({'demo':ecommerce.model_dump(),'company':company.model_dump()},ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'datasets.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'prepared {len(cases)} cases',flush=True)
