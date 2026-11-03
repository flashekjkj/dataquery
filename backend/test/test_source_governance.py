import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import duckdb
from datasources import DataSourceConfig,DataSourceRegistry
from datasources.base import table_id
from datasources.policy import authorize_sql
from sql.executor import SqlExecuteError


class GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=self.root/'test.duckdb'
        with duckdb.connect(str(self.db)) as conn:
            conn.execute('CREATE TABLE staff(id INTEGER PRIMARY KEY,name VARCHAR,salary INTEGER)')
            conn.execute('CREATE TABLE secrets(token VARCHAR)')
            conn.execute("INSERT INTO staff VALUES(1,'A',100)")
        self.table=table_id('main','staff')
        self.policy=self.root/'policy.json'
        self.policy.write_text(json.dumps({'tables':{self.table:['id','name']}}))
        self.registry=DataSourceRegistry();self.addCleanup(self.registry.close)
        self.connector=self.registry.register(DataSourceConfig(source_id='staff',dialect='duckdb',path=str(self.db),policy_file=str(self.policy)))

    def test_metadata_sampling_and_sql_use_same_scope(self):
        snapshot=self.connector.inspect_schema()
        self.assertEqual(len(snapshot.tables),1)
        self.assertEqual([f.name for f in snapshot.tables[0].fields],['id','name'])
        self.assertEqual(self.connector.execute('SELECT * FROM staff').columns,['id','name'])
        self.assertEqual(self.connector.sample_values(self.table,'name'),['A'])
        for sql in ['SELECT salary FROM staff','SELECT * FROM secrets',
            'WITH staff AS (SELECT * FROM secrets) SELECT * FROM staff',
            'SELECT id FROM staff WHERE id IN (SELECT LENGTH(token) FROM secrets)',
            "SELECT read_blob('local.txt')",'SELECT * FROM other.main.staff',
            'SELECT id FROM staff UNION SELECT salary FROM staff']:
            with self.subTest(sql=sql),self.assertRaises(SqlExecuteError):self.connector.execute(sql)
        with self.assertRaises(ValueError):self.connector.sample_values(self.table,'salary')
        self.assertEqual(self.connector.execute('WITH x AS (SELECT id FROM staff) SELECT * FROM x').rows,[[1]])

    def test_no_policy_denies_by_default(self):
        registry=DataSourceRegistry();self.addCleanup(registry.close)
        connector=registry.register(DataSourceConfig(source_id='deny',dialect='duckdb',path=str(self.db)))
        self.assertEqual(connector.inspect_schema().tables,[])
        with self.assertRaises(SqlExecuteError):connector.execute('SELECT * FROM staff')

    def test_refresh_requires_draining_requests(self):
        with self.connector.query_session():
            with self.assertRaises(ValueError):self.registry.refresh('staff')
        self.registry.refresh('staff')

    def test_semantic_override_and_hidden_metric(self):
        from datasources.semantics import load_semantics
        paths=[]
        for i,column in enumerate(('id','salary')):
            p=self.root/f'semantic{i}.json'
            p.write_text(json.dumps({'metrics':{'value':{'tables':[self.table],'expression':f'SUM({column})'}}}))
            paths.append(str(p))
        semantic=load_semantics(paths,self.connector.raw.inspect_schema())
        self.assertEqual(semantic.metrics['value'].expression,'SUM(salary)')
        registry=DataSourceRegistry();self.addCleanup(registry.close)
        connector=registry.register(self.connector.config.model_copy(update={'semantic_files':tuple(paths)}))
        self.assertEqual(connector.context.semantics['metrics'],{})
        p=Path(paths[-1]);p.write_text(json.dumps({'metrics':{'bad':{'tables':[self.table],'expression':'SUM(missing)'}}}))
        with self.assertRaises(ValueError):load_semantics(paths,self.connector.raw.inspect_schema())

    def test_cache_local_invalidation_and_policy_namespace(self):
        from retrieval.cache import VectorCache
        from retrieval.service import build_hybrid_index
        from test.test_graph import FakeClient
        client=FakeClient([]);cache=VectorCache(self.root/'vectors.sqlite')
        context=self.connector.context
        index=build_hybrid_index(client,context,cache)
        initial=cache.misses
        build_hybrid_index(client,context,cache)
        self.assertEqual(cache.misses,initial)
        snapshot=context.snapshot.model_copy(deep=True)
        snapshot.tables[0].fields[0].description='Changed'
        build_hybrid_index(client,context.model_copy(update={'snapshot':snapshot}),cache)
        self.assertEqual(cache.misses-initial,2)  # 一个字段及其表向量，不重算其余字段。
        self.assertTrue(cache.hits)
        self.assertNotEqual(index.vector_index.namespace,
            build_hybrid_index(client,context.model_copy(update={'policy_version':'revoked'}),cache).vector_index.namespace)

    def test_drift_and_policy_change_invalidate_old_graph(self):
        from agent.graph import build_graph
        from test.test_graph import FakeClient
        from retrieval.service import build_hybrid_index
        from memory.conversation import ConversationMemory
        from memory.long_term import LongTermMemoryStore
        context=self.connector.context;client=FakeClient(['SELECT id FROM staff'])
        graph=build_graph(client,build_hybrid_index(client,context),self.connector,
            ConversationMemory(self.root),LongTermMemoryStore(self.root),query_context=context)
        with duckdb.connect(str(self.db)) as c:c.execute('ALTER TABLE staff ADD COLUMN age INTEGER')
        with self.assertRaises(ValueError):
            with graph.query_session():pass
        self.registry.refresh('staff')
        # 被隐藏新增列不影响可见Schema，原始版本检查已经通过；撤权则必须重建上下文。
        self.policy.write_text('{"tables":{}}')
        self.registry.refresh('staff')
        with self.assertRaises(ValueError):
            with graph.query_session():pass

    def test_candidates_are_bounded_and_forbidden_not_embedded(self):
        from retrieval.service import build_hybrid_index
        from test.test_graph import FakeClient
        from datasources.models import TableSchema,ColumnSchema,QueryContext,SchemaSnapshot
        client=FakeClient([])
        tables=[TableSchema(id=f't{i}',name=f't{i}',schema_name='main',fields=[ColumnSchema(name='id',type='INTEGER')]) for i in range(30)]
        context=QueryContext(snapshot=SchemaSnapshot(source_id='big',dialect='duckdb',tables=tables))
        retriever=build_hybrid_index(client,context)
        self.assertLessEqual(len(retriever.prompt_metadata('id')['schema']),6)
        with patch.object(client,'embedding',wraps=client.embedding) as embedding:
            build_hybrid_index(client,self.connector.context)
            self.assertFalse(any('salary' in str(c) or 'secrets' in str(c) for c in embedding.call_args_list))

    def test_duckdb_consistent_session_reuses_connection(self):
        from datasources.duckdb import DuckDBConnector
        connector=DuckDBConnector(self.connector.config.model_copy(update={'query_mode':'consistent'}))
        with connector.query_session() as snapshot:
            connection=connector.active.get()
            self.assertIsNotNone(snapshot)
            self.assertEqual(connector.execute('SELECT COUNT(*) FROM staff').rows,[[1]])
            self.assertIs(connector.active.get(),connection)
        self.assertIsNone(connector.active.get())
        import time
        connector=DuckDBConnector(self.connector.config.model_copy(update={'query_mode':'consistent','snapshot_seconds':1}))
        with self.assertRaises(Exception):
            with connector.query_session():time.sleep(1.2)
        self.assertIsNone(connector.active.get())
        self.assertEqual(connector.execute('SELECT 1').rows,[[1]])

    def test_package_evaluation_and_wrong_prediction(self):
        from evaluation.packages import ROOT,evaluate
        for name in ('ecommerce','renamed_split','new_domain','schema_drift','permissions'):
            report=evaluate(ROOT/name,reference_replay=True)
            self.assertTrue(all(r['action_correct'] for r in report['cases']),name)
        report=evaluate(ROOT/'new_domain',{'offers':'SELECT 0','job-path':'SELECT 0'})
        self.assertEqual(report['sql_accuracy'],0)
        self.assertEqual(report['table_path_coverage'],0)
        report=evaluate(ROOT/'permissions',{key:'SELECT (' for key in
            ('allowed','star','deny-column','deny-table','deny-cte','deny-subquery')})
        self.assertEqual(report['denial_accuracy'],0)

    def test_joins_cannot_be_bypassed_with_exists(self):
        from evaluation.packages import ROOT
        from datasources.duckdb import DuckDBConnector
        # 当前staff没有合法关系，相关子查询即使字段可见也不能构造新关联。
        with self.assertRaises(SqlExecuteError):
            self.connector.execute('SELECT a.id FROM staff a WHERE EXISTS(SELECT 1 FROM staff b WHERE a.id=b.id)')
