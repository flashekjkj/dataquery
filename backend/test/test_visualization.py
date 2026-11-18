import json
import tempfile
import unittest
from pathlib import Path

from visualization.service import choose_chart, build_visualizations, query_hint


def result(columns,rows,truncated=False):
    return {'columns':columns,'rows':rows,'row_count':len(rows),'is_truncated':truncated}


class ChartTests(unittest.TestCase):
    def chart(self,question,columns,rows):
        chart,reason=choose_chart(question,result(columns,rows),'main_result')
        self.assertIsNotNone(chart,reason)
        return chart

    def test_time_sort_and_null_gap(self):
        chart=self.chart('按月销售额变化',['month','sales'],[['2026-10',30],['2026-08',10],['2026-09',None]])
        self.assertEqual(chart['type'],'line')
        self.assertEqual(chart['rows'],[['2026-08',10],['2026-09',None],['2026-10',30]])

    def test_comparison_ranking_share_and_card(self):
        self.assertEqual(self.chart('地区销售额',['地区','销售额'],[['华东',10],['华南',20]])['type'],'bar')
        self.assertEqual(self.chart('商品前2名',['商品','金额'],[['A',20],['B',10]])['type'],'horizontal_bar')
        self.assertEqual(self.chart('地区占比',['地区','金额'],[['A',10],['B',20]])['type'],'pie')
        self.assertEqual(self.chart('用指标卡片展示总销售额',['销售额'],[[123.45]])['type'],'card')
        self.assertNotEqual(self.chart('地区占比',['地区','金额'],[['A',-10],['B',20]])['type'],'pie')

    def test_scalar_defaults_to_text_and_explicit_card_is_preserved(self):
        payload=result(['sales'],[[2759616.82]])
        for question in ['华东地区6月的销售额是多少', '简要回答华东地区6月的销售额是多少', '销售额是多少，请画图']:
            with self.subTest(question=question):
                self.assertIsNone(choose_chart(question,payload,'main')[0])
        self.assertEqual(choose_chart('用指标卡展示华东6月销售额',payload,'main')[0]['type'],'card')

    def test_text_only_overrides_automatic_charts_and_explicit_card(self):
        payload=result(['region','sales'],[['A',10],['B',20]])
        for preference in ['只用文字回答', '仅文字回答', '不要图表', '不需要生成图片', '不用画图', '不要指标卡片']:
            with self.subTest(preference=preference):
                self.assertIsNone(choose_chart('地区销售额，'+preference,payload,'main')[0])
        self.assertIsNone(choose_chart('指标卡片，不要图表',result(['sales'],[[10]]),'main')[0])

    def test_original_preference_survives_rewrite_and_task_decomposition(self):
        payload=result(['region','sales'],[['A',10],['B',20]])
        state={'route':'data','success':True,'user_question':'只用文字回答各地区销售额',
               'rewritten_question':'各地区销售额','query_result':payload,
               'task_results':[{'task_id':'1','question':'地区销售额','query_result':payload,'status':'approved'}]}
        self.assertEqual(build_visualizations(state)['visualizations'],[])
        state.update(user_question='用指标卡片展示销售额',rewritten_question='销售额',
                     task_results=[{'task_id':'1','question':'销售额','query_result':result(['sales'],[[10]]),'status':'approved'}])
        self.assertEqual(build_visualizations(state)['visualizations'][0]['type'],'card')

    def test_multiseries_scatter_and_distribution(self):
        chart=self.chart('各地区每月构成',['month','region','sales'],[['2026-08','A',1],['2026-08','B',2],['2026-09','A',3]])
        self.assertEqual(chart['type'],'stacked_bar')
        self.assertEqual(chart['series'],1)
        self.assertEqual(self.chart('两个金额的相关关系',['a','b'],[[1,2],[2,4]])['type'],'scatter')
        self.assertEqual(self.chart('金额分布',['区间','频数'],[['0-100',3],['100-200',4]])['type'],'histogram')

    def test_incomplete_ambiguous_and_invalid_data_suppressed(self):
        invalid=[result(['d','v'],[['2026-08',1]],True),result(['d','v'],[]),
                 result(['d','v'],[['2026-08',1],['2026-08',2]]),
                 result(['x','x'],[[1,2]]),result(['a','b'],[['A',float('nan')]])]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertIsNone(choose_chart('趋势',payload,'main')[0])
        self.assertIsNone(choose_chart('订单明细',result(['order_id','amount'],[[1,10],[2,20]]),'main')[0])

    def test_only_approved_tasks_and_two_chart_limit(self):
        payload=result(['region','sales'],[['A',10],['B',20]])
        state={'route':'data','user_question':'销售额','task_results':[
            {'task_id':str(i),'question':'地区销售额','query_result':payload,'status':'approved' if i else 'unverified'} for i in range(4)]}
        charts=build_visualizations(state)['visualizations']
        self.assertEqual([c['source_id'] for c in charts],['1','2'])
        self.assertEqual(build_visualizations({'route':'chat'})['visualizations'],[])
        self.assertIn('时间桶',query_hint('最近三个月销售额变化'))

    def test_metrics_split_and_nonadditive_values_do_not_become_pie(self):
        state={'route':'data','success':True,'user_question':'每月金额和人数变化',
               'query_result':result(['month','amount','people'],[['2026-06',100,3],['2026-07',200,5]])}
        charts=build_visualizations(state)['visualizations']
        self.assertEqual([chart['y'] for chart in charts],[[1],[2]])
        self.assertNotEqual(charts[0]['source_id'],charts[1]['source_id'])
        self.assertNotEqual(self.chart('各地区平均金额占比',['地区','均价'],[['A',10],['B',20]])['type'],'pie')


class ChartEntryTests(unittest.TestCase):
    def setUp(self):
        from agent.graph import build_graph
        from memory.conversation import ConversationMemory
        from memory.long_term import LongTermMemoryStore
        from retrieval.service import build_hybrid_index
        from sql.executor import SQLExecutor
        from test.test_graph import FakeClient
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.memory=ConversationMemory(root)
        self.client=FakeClient(["SELECT strftime(order_date,'%Y-%m') AS month,SUM(paid_amount) AS sales FROM orders GROUP BY month ORDER BY month"])
        self.graph=build_graph(self.client,build_hybrid_index(self.client),
            SQLExecutor(str(Path(__file__).resolve().parents[2]/'evaluation/fixture.duckdb')),
            self.memory,LongTermMemoryStore(root))

    def test_graph_history_and_next_chat_have_no_stale_chart(self):
        from agent.runtime import ask_data_agent
        output=ask_data_agent(self.graph,'按月销售额变化','visual-test',execution_mode='single')
        self.assertTrue(output['success'])
        self.assertEqual(output['visualizations'][0]['type'],'line')
        saved=self.memory.load('visual-test')['turns'][-1]
        self.assertEqual(saved['visualizations'],output['visualizations'])
        self.assertEqual(saved['query_result']['rows'],output['query_result'].model_dump(mode='json')['rows'])
        self.client.route_answer='chat'
        output=ask_data_agent(self.graph,'你好','visual-test',execution_mode='single')
        self.assertEqual(output['visualizations'],[])

    def test_rest_and_sse_expose_charts_and_history(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from server.routes import router
        app=FastAPI()
        app.include_router(router)
        app.state.graph=self.graph
        app.state.conv_memory=self.memory
        with TestClient(app) as client:
            response=client.post('/api/chat',json={'question':'按月销售额变化','session_id':'rest-chart'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['visualizations'][0]['type'],'line')
            history=client.get('/api/sessions/rest-chart').json()['turns'][-1]
            self.assertEqual(history['visualizations'],response.json()['visualizations'])
            response=client.post('/api/chat/stream',json={'question':'按月销售额变化','session_id':'stream-chart'})
            events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            chart_event=next(event for event in events if event.get('node')=='visualization_node')
            self.assertEqual(chart_event['visualizations'][0]['type'],'line')
            self.assertEqual(events[-1]['type'],'done')


if __name__=='__main__':
    unittest.main()
