import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import duckdb
from sql.executor import SQLExecutor, SqlExecuteError
from sql.generator import extract_sql, parse_sql_response
from agent.outcomes import classify_outcome
from observability.context import accumulate_tokens, get_request_tokens
from observability.middleware import ObservabilityMiddleware
from evaluation.run import matches, score, normalize
from evaluation.prepare import ROOT
from observability.tracer import start_trace, end_trace, GraphCallbackHandler


class SqlSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/'fixture.duckdb'
        with duckdb.connect(str(self.db)) as con:
            con.execute('CREATE TABLE orders AS SELECT i AS order_id FROM range(180) t(i)')
        self.executor=SQLExecutor(str(self.db))

    def test_system_limit_and_explicit_limit(self):
        self.assertTrue(self.executor.execute('SELECT * FROM orders').is_truncated)
        self.assertFalse(self.executor.execute('SELECT * FROM orders LIMIT 100').is_truncated)
        self.assertEqual(self.executor.execute('SELECT * FROM orders LIMIT 0').rows,[])

    def test_inner_limit_does_not_disable_system_limit(self):
        result=self.executor.execute('SELECT * FROM orders WHERE order_id IN (SELECT order_id FROM orders LIMIT 150)')
        self.assertEqual(result.row_count,100)
        self.assertTrue(result.is_truncated)

    def test_cte_and_literal_preserved(self):
        sql="WITH t AS (SELECT 'A  B; DROP' AS x) SELECT x FROM t"
        self.assertEqual(self.executor.execute(extract_sql(sql)).rows,[['A  B; DROP']])
        self.assertEqual(parse_sql_response(json.dumps({'sql':"SELECT 'A  B'"})),"SELECT 'A  B';")

    def test_multistatement_and_external_file_rejected(self):
        for sql in ('SELECT 1; SELECT 2', 'DROP TABLE orders', "SELECT * FROM read_csv('C:/Windows/win.ini')"):
            with self.subTest(sql=sql), self.assertRaises(SqlExecuteError):
                self.executor.execute(sql)

    def test_line_comment_keeps_following_clause(self):
        sql='SELECT order_id -- comment\nFROM orders ORDER BY order_id DESC LIMIT 1'
        self.assertEqual(self.executor.execute(parse_sql_response(json.dumps({'sql':sql}))).rows,[[179]])

    def test_query_deadline_interrupts_execution(self):
        executor=SQLExecutor(str(self.db),timeout_seconds=.03)
        with self.assertRaisesRegex(SqlExecuteError,'查询超时'):
            executor.execute('SELECT SUM(SIN(i)) FROM range(10000000000) t(i)')


class MeasurementTests(unittest.TestCase):
    def test_real_langgraph_callback_names(self):
        from langgraph.graph import StateGraph, START, END
        from typing import TypedDict
        class State(TypedDict):
            answer: str
        graph=StateGraph(State)
        graph.add_node('probe_node',lambda state:{'answer':'ok'})
        graph.add_edge(START,'probe_node')
        graph.add_edge('probe_node',END)
        start_trace()
        graph.compile().invoke({},config={'callbacks':[GraphCallbackHandler()]})
        snapshot=end_trace()
        self.assertIn('probe_node',{span['name'] for span in snapshot['spans']})

    def test_concurrent_requests_have_separate_token_counters(self):
        results=[]
        async def app(scope,receive,send):
            count=scope['count']
            accumulate_tokens(count,count*2)
            await asyncio.sleep(.02)
            results.append(dict(get_request_tokens()))
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'ok'})
        middleware=ObservabilityMiddleware(app)
        async def receive():
            return {'type':'http.request','body':b''}
        async def send(message):
            pass
        async def run():
            await asyncio.gather(*(middleware({'type':'http','method':'GET','path':'/','headers':[], 'count':i},receive,send) for i in (3,7)))
        asyncio.run(run())
        self.assertCountEqual(results,[{'input':3,'output':6},{'input':7,'output':14}])

    def test_stream_wall_clock_covers_body(self):
        timings=[]
        async def app(scope,receive,send):
            await send({'type':'http.response.start','status':200,'headers':[]})
            await asyncio.sleep(.03)
            await send({'type':'http.response.body','body':b'ok'})
        async def noop(*args):
            pass
        with patch('observability.middleware.record_http_request',side_effect=lambda *a:timings.append(a[-1])):
            asyncio.run(ObservabilityMiddleware(app)({'type':'http','method':'GET','path':'/','headers':[]},noop,noop))
        self.assertGreaterEqual(timings[0],.025)

    def test_business_outcomes(self):
        for state,expected in [({'route':'data','success':False},'failed'),
                               ({'collaboration_status':'partial'},'partial'),
                               ({'collaboration_status':'clarification'},'clarification'),
                               ({'route':'tool','tool_success':False},'failed'),
                               ({'route':'tool','tool_success':True},'success'),
                               ({'route':'data','success':True,'degraded':True},'degraded')]:
            self.assertEqual(classify_outcome(state),expected)


class ScorerTests(unittest.TestCase):
    def test_temporal_role_is_retained_if_initial_retrieval_misses_it(self):
        from agent.query_context import retrieve_schema_context
        from retrieval.store import SchemaStore
        from data.database import SCHEMA
        docs=SchemaStore(SCHEMA).build_field_documents()
        class Retriever:
            def retrieve(self,*args,**kwargs):
                return [(d,1) for d in docs if d.table_name=='orders' and d.role!='time'][:8]
        class Executor:
            def sample_values(self,*args,**kwargs):
                return []
        selected,_,_=retrieve_schema_context(Retriever(),Executor(),'2027年1月的订单数量是多少？')
        self.assertIn('orders.order_date',{d.doc_id for d in selected})

    def test_comparison_duplicates_order_and_columns(self):
        self.assertTrue(matches([[1,2],[3,4]],[[4,3],[2,1]]))
        self.assertFalse(matches([[1],[1]],[[1],[2]]))
        self.assertFalse(matches([[1],[2]],[[2],[1]],ordered=True))
        self.assertFalse(matches([[None]],[[0]]))

    def test_truncated_or_unapproved_result_is_not_correct(self):
        case={'expected_action':'query','required_fields':[], 'expected_results':[[[1]]],'ordered':False}
        self.assertFalse(score(case,{'success':True,'query_result':{'rows':[[1]],'is_truncated':True}})['correct'])

    def test_dataset_reference_results_match_independent_execution(self):
        with duckdb.connect(str(ROOT/'fixture.duckdb'),read_only=True) as con:
            for path in (ROOT/'datasets').glob('*.jsonl'):
                for line in path.read_text(encoding='utf-8').splitlines():
                    case=json.loads(line)
                    for sql,expected in zip(case['reference_sql'],case['expected_results']):
                        actual=[[float(v) if hasattr(v,'as_tuple') else v.isoformat() if hasattr(v,'isoformat') else v for v in row] for row in con.execute(sql).fetchall()]
                        self.assertEqual(normalize(actual,case['ordered']),normalize(expected,case['ordered']),case['id'])
