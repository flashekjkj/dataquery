"""检索退化、预算和结构补全的定向回归。"""
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from datasources.demo import demo_query_context
from datasources.semantics import demo_semantics
from retrieval.config import RetrievalConfig
from retrieval.context import assemble_context
from retrieval.ranker import BM25Index, reciprocal_rank_fusion, tokenize
from retrieval.service import build_hybrid_index
from retrieval.store import SchemaStore
from test.test_graph import FakeClient


class RetrievalRegressionTests(unittest.TestCase):
    def setUp(self):
        self.context = demo_query_context()
        self.context = self.context.model_copy(update={
            'semantics':demo_semantics(self.context.snapshot).model_dump()})
        self.docs = SchemaStore(self.context.snapshot.schema_tables).build_field_documents()

    def test_zero_matches_do_not_vote_and_identifiers_are_searchable(self):
        bm25 = BM25Index()
        bm25.build(self.docs)
        self.assertEqual(bm25.retrieve('zzzxqvnomatch',8), [])
        self.assertIn('paid_amount',tokenize('paid_amount'))
        self.assertIn('amount',tokenize('paid_amount'))
        self.assertNotIn(' ',tokenize('订单 金额，是多少？'))
        self.assertEqual(bm25.retrieve('paid_amount',1)[0][0].field_name,'paid_amount')

    def test_zero_lexical_weight_preserves_vector_order(self):
        bm25 = [(doc,1) for doc in self.docs[:8]]
        vector = [(doc,1) for doc in reversed(self.docs)]
        result = reciprocal_rank_fusion(bm25,vector,bm25_weight=0)
        self.assertEqual([d.doc_id for d,_ in result],[d.doc_id for d,_ in vector])

    def test_scoped_variants_share_candidates_and_embed_question_once(self):
        client = FakeClient([])
        index = build_hybrid_index(client,self.context)
        with patch.object(client,'embedding',wraps=client.embedding) as embedding:
            vector, first = index.retrieve_with_diagnostics('实付金额',8,table_scope=['orders'])
            self.assertEqual(embedding.call_count,1)
        index.config = RetrievalConfig(strategy='hybrid')
        hybrid, second = index.retrieve_with_diagnostics('实付金额',8,table_scope=['orders'])
        self.assertEqual(first['selected_tables'],second['selected_tables'])
        self.assertTrue(all(d.table_name=='orders' for d,_ in vector+hybrid))
        self.assertGreaterEqual(second['recall_k'],8)
        self.assertEqual(index.retrieve('实付金额',table_scope=[]),[])

    def test_join_keys_are_completed_inside_budget_and_scope(self):
        by_id = {d.doc_id:d for d in self.docs}
        ranked = [(by_id[key],1) for key in ('products.product_name','orders.order_id')]
        docs,relations,diagnostics = assemble_context(ranked,self.context,'按商品名称统计订单数')
        self.assertTrue({'products.product_id','orders.product_id'} <= {d.doc_id for d in docs})
        self.assertEqual(len(relations),1)
        self.assertLessEqual(len(docs),12)
        self.assertIn('orders.product_id',diagnostics['added_fields'])
        scoped,relations,_ = assemble_context(ranked,self.context,'商品名称',table_scope=['products'])
        self.assertEqual(relations,[])
        self.assertTrue(all(d.table_name=='products' for d in scoped))

    def test_composite_join_is_atomic_and_time_fields_do_not_replace_each_other(self):
        context = self.context.model_copy(deep=True)
        context.semantics['joins'] = {'composite':{'left':'orders','right':'customers',
            'keys':[('customer_id','customer_id'),('region','region')]}}
        by_id = {d.doc_id:d for d in self.docs}
        ranked = [(by_id[key],1) for key in ('orders.paid_amount','customers.customer_level')]
        docs,relations,_ = assemble_context(ranked,context,'2026年销售额',field_budget=4)
        self.assertEqual(relations,[])
        self.assertIn('orders.order_date',{d.doc_id for d in docs})

    def test_reranker_sees_union_and_reports_degradation(self):
        from app.schemas import RerankResponse, RerankItem
        client = FakeClient([])
        index = build_hybrid_index(client,self.context,config=RetrievalConfig(strategy='rerank'))
        def degrade(query,documents):
            return RerankResponse(degraded=True,results=[
                RerankItem(index=i,text=text,score=-i) for i,text in enumerate(documents)])
        with patch.object(client,'rerank',side_effect=degrade) as rerank:
            _,diagnostics = index.retrieve_with_diagnostics('订单客户商品',8)
        self.assertGreater(len(rerank.call_args.kwargs['documents']),8)
        self.assertTrue(diagnostics['rerank_degraded'])

    def test_api_rerank_preserves_input_indices_and_usage(self):
        from app.config import settings
        from app.model_client import ModelClient
        client = ModelClient(mock=True)
        client.mock = False
        payload = {'output':{'results':[{'index':1,'relevance_score':.9},
                                        {'index':0,'relevance_score':.1}]},'usage':{'total_tokens':23}}
        response = SimpleNamespace(status_code=200,json=lambda:payload)
        with patch.object(settings,'RERANK_BACKEND','dashscope'),patch('app.model_client.httpx.post',return_value=response) as post:
            result = client.rerank('amount',['name','paid_amount'])
        self.assertFalse(result.degraded)
        self.assertEqual([(item.index,item.text) for item in result.results],[(1,'paid_amount'),(0,'name')])
        self.assertEqual(post.call_args.kwargs['json']['parameters']['top_n'],2)

    def test_invalid_api_indices_degrade_without_dropping_candidates(self):
        from app.config import settings
        from app.model_client import ModelClient
        client = ModelClient(mock=True)
        client.mock = False
        payload = {'output':{'results':[{'index':0,'relevance_score':.9},{'index':0,'relevance_score':.1}]}}
        with patch.object(settings,'RERANK_BACKEND','dashscope'),patch('app.model_client.httpx.post',return_value=SimpleNamespace(status_code=200,json=lambda:payload)):
            result = client.rerank('amount',['name','paid_amount'])
        self.assertTrue(result.degraded)
        self.assertEqual([item.index for item in result.results],[0,1])


if __name__ == '__main__':
    unittest.main()
