"""新结构、新领域、漂移、权限变体的微型可复现验收包。"""
def fixtures():
    orders='"main"."orders"'; payments='"main"."payments"'
    init='''CREATE TABLE orders(id INTEGER PRIMARY KEY, created DATE);
    CREATE TABLE payments(id INTEGER PRIMARY KEY, order_id INTEGER, net_receipt DECIMAL(12,2));
    INSERT INTO orders VALUES(1,'2026-11-01'),(2,'2026-11-02');
    INSERT INTO payments VALUES(1,1,30),(2,2,70);'''
    semantic={'metrics':{'sales':{'tables':[payments],'expression':'SUM(net_receipt)','description':'实收金额'}},
        'joins':{'payment_order':{'left':payments,'right':orders,'keys':[['order_id','id']],'cardinality':'many_to_one'}},
        'time_fields':{orders:'created'}}
    base={'init':init,'tables':{orders:None,payments:None},'semantics':semantic}
    renamed={**base,'cases':[
        {'id':'split-total','question':'实收金额','reference_sql':'SELECT SUM(net_receipt) FROM payments','expected_tables':[payments]},
        {'id':'split-join','question':'每天实收金额','reference_sql':'SELECT o.created,SUM(p.net_receipt) FROM payments p JOIN orders o ON p.order_id=o.id GROUP BY o.created','expected_tables':[orders,payments],'expected_edges':[[orders,payments]]},
        {'id':'old-column','question':'旧字段不可用','reference_sql':'SELECT paid_amount FROM payments','expected_action':'deny'},
        {'id':'wrong-join','question':'错误关联','reference_sql':'SELECT * FROM payments p JOIN orders o ON p.id=o.id','expected_action':'deny'}]}
    jobs='"main"."jobs"'; applications='"main"."applications"'
    new_domain={'init':'''CREATE TABLE jobs(id INTEGER PRIMARY KEY,title VARCHAR);
    CREATE TABLE applications(id INTEGER PRIMARY KEY, job_id INTEGER, stage VARCHAR);
    INSERT INTO jobs VALUES(1,'Engineer'),(2,'Analyst');
    INSERT INTO applications VALUES(1,1,'offer'),(2,1,'interview'),(3,2,'offer');''',
    'tables':{jobs:None,applications:None},'semantics':{'metrics':{'offer_count':{'tables':[applications],'expression':'COUNT(*)','filters':["stage='offer'"]}},
    'joins':{'application_job':{'left':applications,'right':jobs,'keys':[['job_id','id']]}}},
    'cases':[{'id':'offers','question':'offer数量','reference_sql':"SELECT COUNT(*) FROM applications WHERE stage='offer'",'expected_tables':[applications]},
        {'id':'job-path','question':'岗位申请数','reference_sql':'SELECT j.title,COUNT(*) FROM applications a JOIN jobs j ON a.job_id=j.id GROUP BY j.title','expected_tables':[jobs,applications],'expected_edges':[[jobs,applications]]}]}
    drift={**base,'cases':[{'id':'stale-added','question':'新增字段后旧上下文拒绝执行','reference_sql':'SELECT COUNT(*) FROM orders','mutation_sql':['ALTER TABLE orders ADD COLUMN note VARCHAR'],'expected_action':'stale'},
        {'id':'stale-renamed','question':'字段改名后旧上下文失效','reference_sql':'SELECT SUM(net_receipt) FROM payments','mutation_sql':['ALTER TABLE payments RENAME COLUMN net_receipt TO amount'],'expected_action':'stale'}]}
    staff='"main"."staff"'; secrets='"main"."secrets"'
    permissions={'init':'''CREATE TABLE staff(id INTEGER PRIMARY KEY,name VARCHAR,salary INTEGER);
    CREATE TABLE secrets(token VARCHAR);INSERT INTO staff VALUES(1,'A',100),(2,'B',200);INSERT INTO secrets VALUES('hidden');''',
    'tables':{staff:['id','name']},'cases':[
        {'id':'allowed','question':'可见员工','reference_sql':'SELECT id,name FROM staff','expected_tables':[staff]},
        {'id':'star','question':'星号只展开可见列','reference_sql':'SELECT id,name FROM staff','replay_sql':'SELECT * FROM staff','expected_tables':[staff]},
        {'id':'deny-column','question':'隐藏薪酬','reference_sql':'SELECT salary FROM staff','expected_action':'deny'},
        {'id':'deny-table','question':'禁止表','reference_sql':'SELECT * FROM secrets','expected_action':'deny'},
        {'id':'deny-cte','question':'CTE不能绕过列权限','reference_sql':'WITH x AS (SELECT salary FROM staff) SELECT * FROM x','expected_action':'deny'},
        {'id':'deny-subquery','question':'子查询不能绕过','reference_sql':'SELECT id FROM staff WHERE EXISTS(SELECT 1 FROM secrets)','expected_action':'deny'}]}
    return {'renamed_split':renamed,'new_domain':new_domain,'schema_drift':drift,'permissions':permissions}
