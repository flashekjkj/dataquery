"""离线连接器契约与可选真实PostgreSQL集成测试。"""
import json
import os
from pathlib import Path
import tempfile
import unittest
import duckdb
from pydantic import ValidationError
from datasources import DataSourceConfig, DataSourceRegistry, QueryContext
from datasources.duckdb import DuckDBConnector
from datasources.postgres import PostgresConnector
from datasources.base import table_id
from sql.executor import SqlExecuteError
from evaluation.run import matches

ROOT = Path(__file__).resolve().parents[2]


class DuckDBSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name)/'source.duckdb')
        with duckdb.connect(self.path) as c:
            c.execute('CREATE SCHEMA sales')
            c.execute('CREATE SCHEMA crm')
            c.execute('CREATE TABLE crm.customers(id INTEGER PRIMARY KEY, name VARCHAR)')
            c.execute('CREATE TABLE sales.customers(id INTEGER, created DATE)')
            c.execute("INSERT INTO crm.customers VALUES (1,'Alice'),(2,'Bob')")
        self.connector = DuckDBConnector(DataSourceConfig(source_id='test',dialect='duckdb',path=self.path,max_rows=1))

    def test_reflection_sampling_and_isolation(self):
        snapshot = self.connector.inspect_schema()
        self.assertEqual(len(snapshot.tables),2)
        self.assertEqual(len({t.id for t in snapshot.tables}),2)
        self.assertEqual(self.connector.sample_values(table_id('crm','customers'),'name',1),['Alice'])
        self.assertTrue(self.connector.execute_readonly('SELECT * FROM crm.customers').is_truncated)
        self.assertTrue(self.connector.test_connection())
        with self.assertRaises(ValueError):
            self.connector.sample_values('customers','name',1)
        with self.assertRaises(ValueError):
            self.connector.sample_values(table_id('crm','customers'),'name"; DROP TABLE x;--',1)
        with self.assertRaises(SqlExecuteError):
            self.connector.execute_readonly('DELETE FROM crm.customers')

    def test_config_registry_and_credentials(self):
        with self.assertRaises(ValidationError):
            DataSourceConfig(source_id='x',dialect='postgres')
        config = DataSourceConfig(source_id='pg',dialect='postgres',url='postgresql://user:secret@localhost/db')
        self.assertNotIn('secret',config.model_dump_json())
        self.assertNotIn('secret',repr(config))
        registry = DataSourceRegistry()
        self.addCleanup(registry.close)
        registry.register(self.connector.config)
        with self.assertRaises(ValueError):
            registry.register(self.connector.config)
        ctx = registry.context('test')
        self.assertEqual(ctx.source_id,'test')
        self.assertEqual(ctx.snapshot.version,QueryContext.model_validate_json(ctx.model_dump_json()).snapshot.version)

    def test_all_ecommerce_reference_cases(self):
        connector = DuckDBConnector(DataSourceConfig(source_id='demo',dialect='duckdb',
            path=str(ROOT/'evaluation/fixture.duckdb'),demo_catalog=True))
        snapshot = connector.inspect_schema()
        self.assertIn('orders',{t.id for t in snapshot.tables})
        count = 0
        for path in (ROOT/'evaluation/datasets').glob('*.jsonl'):
            if path.stem not in ('dev','holdout'):
                continue
            for case in map(json.loads,path.read_text(encoding='utf-8').splitlines()):
                for sql, expected in zip(case['reference_sql'],case['expected_results']):
                    with self.subTest(case=case['id']):
                        result = connector.execute_readonly(sql)
                        self.assertTrue(matches(expected,result.rows,case['ordered']))
                count += 1
        self.assertEqual(count,42)

    def test_ecommerce_agent_fixture_replay(self):
        # 参考SQL替代LLM输出，验证42条夹具在新上下文链路的兼容性；不衡量模型准确率。
        from test.test_collaboration import TeamClient, task
        from agent.graph import build_graph
        from agent.runtime import ask_data_agent
        from retrieval.service import build_hybrid_index
        from memory.conversation import ConversationMemory
        from memory.long_term import LongTermMemoryStore
        from evaluation.run import score
        connector = DuckDBConnector(DataSourceConfig(source_id='demo',dialect='duckdb',
            path=str(ROOT/'evaluation/fixture.duckdb'),demo_catalog=True))
        context = QueryContext(snapshot=connector.inspect_schema())
        for path in (ROOT/'evaluation/datasets').glob('*.jsonl'):
            if path.stem not in ('dev','holdout'):
                continue
            for case in map(json.loads,path.read_text(encoding='utf-8').splitlines()):
                with self.subTest(case=case['id']), tempfile.TemporaryDirectory() as tmp:
                    plan = {'tasks':[task('T'+str(i+1)) for i in range(len(case['reference_sql']))]}
                    if case['expected_action']=='clarification':
                        plan = {'tasks':[], 'clarification':'请提供判断指标与目标。'}
                    client = TeamClient(plan=plan, sql=case['reference_sql'] or None)
                    graph = build_graph(client,build_hybrid_index(client,context),connector,
                        ConversationMemory(Path(tmp)),LongTermMemoryStore(Path(tmp)),query_context=context)
                    result = ask_data_agent(graph,case['question'],case['id'],execution_mode=case['mode'])
                    if result['query_result'] is not None:
                        result['query_result']=result['query_result'].model_dump()
                    self.assertTrue(score(case,result)['correct'])


class PostgresParserTests(unittest.TestCase):
    def test_readonly_syntax(self):
        connector = PostgresConnector(DataSourceConfig(source_id='p',dialect='postgres',url='postgresql://u:p@localhost/d'))
        self.addCleanup(connector.close)
        for sql in ('SELECT 1; SELECT 2','DELETE FROM x','SELECT * INTO x FROM y',
                    'WITH x AS (DELETE FROM y RETURNING *) SELECT * FROM x','SELECT * FROM x FOR UPDATE'):
            with self.subTest(sql=sql), self.assertRaises(SqlExecuteError):
                connector._check_safe(sql)
        self.assertIn('SELECT',connector._check_safe("SELECT ';' AS value"))


@unittest.skipUnless(os.environ.get('ASKDATA_TEST_PG_URL'),'设置ASKDATA_TEST_PG_URL运行真实PG集成测试')
class PostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sqlalchemy import create_engine
        cls.admin = create_engine(os.environ['ASKDATA_TEST_PG_URL'])
        # 仅对专用临时测试库运行；不删除调用者的已有schema。
        import uuid
        cls.sales='sales_'+uuid.uuid4().hex[:8]
        cls.crm='crm_'+uuid.uuid4().hex[:8]
        with cls.admin.begin() as c:
            c.exec_driver_sql(f'CREATE SCHEMA {cls.sales}')
            c.exec_driver_sql(f'CREATE SCHEMA {cls.crm}')
            c.exec_driver_sql(f'CREATE TABLE {cls.crm}.customers(id INTEGER PRIMARY KEY, name TEXT)')
            c.exec_driver_sql(f'CREATE TABLE {cls.sales}.customers(id INTEGER)')
            c.exec_driver_sql(f'CREATE TABLE {cls.sales}.orders(id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES {cls.crm}.customers(id), amount NUMERIC(10,2), created DATE)')
            c.exec_driver_sql(f"INSERT INTO {cls.crm}.customers VALUES(1,'Alice'),(2,'Bob')")
            c.exec_driver_sql(f"INSERT INTO {cls.sales}.orders VALUES(1,1,12.30,'2026-11-03'),(2,2,4.50,'2026-11-02')")
        cls.connector=PostgresConnector(DataSourceConfig(source_id='pg-test',dialect='postgres',
            url=os.environ['ASKDATA_TEST_PG_URL'],schemas=(cls.sales,cls.crm),max_rows=1,timeout_seconds=.2,pool_size=1))

    @classmethod
    def tearDownClass(cls):
        cls.connector.close()
        with cls.admin.begin() as c:
            c.exec_driver_sql(f'DROP SCHEMA {cls.sales} CASCADE')
            c.exec_driver_sql(f'DROP SCHEMA {cls.crm} CASCADE')
        cls.admin.dispose()

    def test_metadata_and_cross_schema_join(self):
        connector=self.connector
        self.assertTrue(connector.test_connection())
        snapshot=connector.inspect_schema()
        self.assertEqual(len(snapshot.tables),3)
        self.assertEqual(snapshot.relations[0].right_table,table_id(self.crm,'customers'))
        self.assertEqual(connector.sample_values(table_id(self.crm,'customers'),'name',1).__len__(),1)
        result=connector.execute_readonly(f'SELECT c.name,o.amount FROM {self.sales}.orders o JOIN {self.crm}.customers c ON o.customer_id=c.id ORDER BY c.id')
        self.assertEqual(result.rows,[['Alice',12.3]])
        self.assertTrue(result.is_truncated)

    def test_timeout_rollback_pool_reuse_and_readonly(self):
        connector=self.connector
        with self.assertRaises(SqlExecuteError):
            connector.execute_readonly('SELECT pg_sleep(1)')
        self.assertEqual(connector.execute_readonly('SELECT 42').rows,[[42]])
        with connector._connection() as c:
            self.assertEqual(c.exec_driver_sql('SHOW transaction_read_only').scalar_one(),'on')
            with self.assertRaises(Exception):
                c.exec_driver_sql(f'INSERT INTO {self.crm}.customers VALUES (3,\'No\')')
        self.assertEqual(connector.execute_readonly('SELECT 42').rows,[[42]])
        self.assertEqual(connector.engine.pool.checkedout(),0)
        with connector.engine.connect() as c:
            self.assertEqual(c.exec_driver_sql('SHOW statement_timeout').scalar_one(),'0')

    def test_agent_switch_without_node_changes(self):
        from test.test_collaboration import TeamClient
        from app.schemas import LLMResponse
        from agent.graph import build_graph
        from agent.runtime import ask_data_agent
        from retrieval.service import build_hybrid_index
        from memory.conversation import ConversationMemory
        from memory.long_term import LongTermMemoryStore
        class Client(TeamClient):
            def llm_invoke(client,prompt,system_prompt=None,**kwargs):
                if kwargs.get('operation') in ('generate_sql','repair_sql'):
                    self.assertIn('PostgreSQL',system_prompt)
                    self.assertNotIn('DuckDB',system_prompt)
                    self.assertIn(self.crm,prompt)
                    return LLMResponse(content=f'SELECT COUNT(*) FROM {self.crm}.customers',usage={})
                if kwargs.get('operation') in ('supervisor_plan','review'):
                    self.assertEqual(json.loads(prompt)['source_id'],'pg-test')
                    self.assertIn(self.crm,prompt)
                return super(Client,client).llm_invoke(prompt,system_prompt,**kwargs)
        context=QueryContext(snapshot=self.connector.inspect_schema())
        client=Client([])
        with tempfile.TemporaryDirectory() as tmp:
            graph=build_graph(client,build_hybrid_index(client,context),self.connector,
                ConversationMemory(Path(tmp)),LongTermMemoryStore(Path(tmp)),query_context=context)
            result=ask_data_agent(graph,'客户数量','pg-session')
            self.assertTrue(result['success'])
            self.assertEqual(result['query_result'].rows,[[2]])
            result=ask_data_agent(graph,'分别统计客户数量','pg-multi',execution_mode='multi')
            self.assertTrue(result['success'])
            self.assertEqual(result['task_results'][0]['query_result']['rows'],[[2]])

    def test_consistent_and_independent_mvcc(self):
        from datasources.postgres import PostgresConnector
        config=self.connector.config.model_copy(update={'query_mode':'consistent','pool_size':2,'snapshot_seconds':10})
        connector=PostgresConnector(config)
        try:
            with connector.query_session():
                before=connector.execute(f'SELECT COUNT(*) FROM {self.crm}.customers').rows[0][0]
                with self.admin.begin() as c:
                    c.exec_driver_sql(f"INSERT INTO {self.crm}.customers VALUES(91,'Later')")
                self.assertEqual(connector.execute(f'SELECT COUNT(*) FROM {self.crm}.customers').rows[0][0],before)
                with self.assertRaises(SqlExecuteError):connector.execute('SELECT missing_column')
                self.assertEqual(connector.execute('SELECT 42').rows,[[42]])
            self.assertEqual(connector.execute(f'SELECT COUNT(*) FROM {self.crm}.customers').rows[0][0],before+1)
            with self.admin.begin() as c:c.exec_driver_sql(f'DELETE FROM {self.crm}.customers WHERE id=91')
        finally:
            connector.close()

    def test_consistent_deadline_releases_pool(self):
        import time
        from datasources.postgres import PostgresConnector
        connector=PostgresConnector(self.connector.config.model_copy(update={'query_mode':'consistent','snapshot_seconds':1}))
        try:
            with self.assertRaises(Exception):
                with connector.query_session():time.sleep(1.2)
            self.assertEqual(connector.engine.pool.checkedout(),0)
            self.assertEqual(connector.execute('SELECT 1').rows,[[1]])
        finally:connector.close()

    def test_pg_policy_and_async_consistent_agent(self):
        import asyncio
        from datasources.base import table_id
        from test.test_collaboration import TeamClient
        from app.schemas import LLMResponse
        from agent.graph import build_graph
        from agent.runtime import initial_agent_state
        from retrieval.service import build_hybrid_index
        from memory.conversation import ConversationMemory
        from memory.long_term import LongTermMemoryStore
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);policy=root/'policy.json'
            policy.write_text(json.dumps({'tables':{table_id(self.crm,'customers'):['id']}}))
            registry=DataSourceRegistry()
            try:
                connector=registry.register(self.connector.config.model_copy(update={
                    'policy_file':str(policy),'query_mode':'consistent','snapshot_seconds':10}))
                with self.assertRaises(SqlExecuteError):connector.execute(f'SELECT name FROM {self.crm}.customers')
                self.assertEqual(connector.execute(f'SELECT * FROM {self.crm}.customers').columns,['id'])
                owner=self
                class Client(TeamClient):
                    def llm_invoke(client,prompt,system_prompt=None,**kwargs):
                        if kwargs.get('operation')=='generate_sql':
                            return LLMResponse(content=f'SELECT COUNT(*) FROM {owner.crm}.customers',usage={})
                        return super(Client,client).llm_invoke(prompt,system_prompt,**kwargs)
                context=registry.context(connector.config.source_id);client=Client()
                graph=build_graph(client,build_hybrid_index(client,context),connector,
                    ConversationMemory(root),LongTermMemoryStore(root),query_context=context)
                async def run():
                    last={}
                    with graph.query_session() as state:
                        self.assertIsNotNone(state['snapshot_id'])
                        async for update in graph.astream(initial_agent_state('客户数量'),
                            config={'configurable':{'thread_id':'stream-pg'}},stream_mode='updates'):
                            for value in update.values():
                                if value:last.update(value)
                    return last
                self.assertTrue(asyncio.run(run())['success'])
                self.assertEqual(connector.raw.engine.pool.checkedout(),0)
            finally:registry.close()
