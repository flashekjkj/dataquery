from __future__ import annotations
import math
from typing import List,Tuple

from .store import FieldDocument,SchemaStore
from app.model_client import ModelClient
from datasources.demo import demo_query_context
from retrieval.ranker import BM25Index,HybridRetriever

def cosine_similarity(vec_a:List[float],vec_b:List[float]) -> float:
    """计算余弦相似度"""
    dot_product = sum(x*y for x,y in zip(vec_a,vec_b))
    norm_a = math.sqrt(sum(x*x for x in vec_a))
    norm_b = math.sqrt(sum(x*x for x in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot_product / (norm_a*norm_b)

class SchemaIndex:
    """
    字段级Schema向量索引
    功能：
    1. 读取QueryContext快照，通过SchemaStore生成字段文档
    2. 调用model_client embedding，为每个文档生成向量，存入内存
    3. 用户输入query，向量化后在内存做余弦相似度检索，返回top-k
    """
    def __init__(self,model_client:ModelClient, query_context=None, cache=None):
        self.query_context = query_context or demo_query_context()
        self.cache = cache
        from datasources.secured import version
        from app.config import settings
        self.namespace = version([self.query_context.source_id,self.query_context.policy_version, self.query_context.semantic_version,settings.LLM_EMBEDDING_MODEL,settings.LLM_BASE_URL,type(model_client).__name__])
        self.model_client = model_client
        self.documents:List[FieldDocument] = []
        self.vectors:List[List[float]] = []
        self._build = False

    def build(self):
        """构建索引：生成字段文档，批量embedding存入内存"""
        # 1.使用当前数据源快照生成字段文档
        schema_store = SchemaStore(self.query_context.snapshot.schema_tables)
        self.documents = schema_store.build_field_documents()

        # 2.循环调用embeddings，得到向量
        self.vectors = []
        for doc in self.documents:
            vector = self.cache.embedding(self.namespace,doc.doc_id,doc.semantic_text,self.model_client) if self.cache else self.model_client.embedding(doc.semantic_text).vector
            self.vectors.append(vector)

        self._build = True

    def retrieve(self,query:str,top_k:int = 3) -> List[Tuple[FieldDocument,float]]:
        """
        向量检索入口
        :param query: 用户查询文本（例如：销售额）
        :param top_k: 返回最相似前k条
        :return: list[(FieldDocument, 相似度分数)]，分数从高到低排序
        """        
        if not self._build:
            raise RuntimeError("SchemaIndex尚未build，请先调用.build()")
        if len(self.documents) == 0:
            return []

        # query文本转向量
        query_emb = self.model_client.embedding(query)
        query_vec = query_emb.vector

        # 计算余弦相似度
        score_list:List[Tuple[FieldDocument,float]] = []
        for doc,vec in zip(self.documents,self.vectors):
            score = cosine_similarity(query_vec,vec)
            score_list.append((doc,score))

        # 按相似度排列，取top_k
        score_list.sort(key=lambda x: x[1],reverse=True)
        return score_list[:top_k]

def build_hybrid_index(model_client:ModelClient, query_context=None, cache=None) -> HybridRetriever:
    # 构建向量索引
    vec_index = SchemaIndex(model_client, query_context, cache)
    vec_index.build()
    all_docs = vec_index.documents
    # 构建BM25索引
    bm25_index = BM25Index()
    bm25_index.build(all_docs)

    hybrid_retriever = CandidateRetriever(vec_index,bm25_index,model_client)
    hybrid_retriever.query_context = vec_index.query_context
    return hybrid_retriever


class CandidateRetriever(HybridRetriever):
    """先以表向量召回，再在候选内混合召回字段；有限补桥，不转为全目录提示。"""
    def __init__(self,vector_index,bm25_index,client):
        super().__init__(vector_index,bm25_index,client)
        self.query_context=vector_index.query_context
        self.tables=self.query_context.snapshot.tables
        self.table_vectors=[]
        for table in self.tables:
            content=table.id+' '+table.description+' '+ ' '.join(f.name+' '+f.description for f in table.fields)
            cache=vector_index.cache
            vector=cache.embedding(vector_index.namespace,'table:'+table.id,content,client) if cache else client.embedding(content).vector
            self.table_vectors.append(vector)
        if vector_index.cache:
            vector_index.cache.prune(vector_index.namespace,{d.doc_id for d in vector_index.documents}|{'table:'+t.id for t in self.tables})

    def candidate_tables(self,question,top_k=3):
        if not self.tables:
            return set()
        vector=self.model_client.embedding(question).vector
        ranked=sorted(zip(self.tables,self.table_vectors),key=lambda x:cosine_similarity(vector,x[1]),reverse=True)
        selected={t.id for t,_ in ranked[:top_k]}
        for name,metric in self.query_context.semantics.get('metrics',{}).items():
            if name in question:
                selected.update(metric['tables'])
        # 连接两端都在候选中的桥接表（最多二跳，整体最多6张表）。
        links={t.id:set() for t in self.tables}
        for rule in self.query_context.semantics.get('joins',{}).values():
            links[rule['left']].add(rule['right']);links[rule['right']].add(rule['left'])
        for a in sorted(selected):
            for b in sorted(selected):
                for bridge in sorted(links.get(a,set()) & links.get(b,set())):
                    if len(selected)<6:
                        selected.add(bridge)
        return set(sorted(selected)[:6])

    def retrieve(self,query,top_k=3,use_rerank=False,**kwargs):
        from retrieval.ranker import reciprocal_rank_fusion,rerank_candidates
        selected=self.candidate_tables(query)
        total=len(self.vector_index.documents)
        bm=[x for x in self.bm25_index.retrieve(query,top_k=total) if x[0].table_name in selected][:max(top_k,8)]
        vec=[x for x in self.vector_index.retrieve(query,top_k=total) if x[0].table_name in selected][:max(top_k,8)]
        result=reciprocal_rank_fusion(bm,vec)
        if use_rerank and result:
            result=rerank_candidates(self.model_client,query,result[:max(top_k,8)])
        return result[:top_k]

    def prompt_metadata(self,question):
        selected=self.candidate_tables(question)
        payload=self.query_context.prompt_metadata(selected)
        ranked=self.vector_index.retrieve(question,top_k=len(self.vector_index.documents))
        for table in payload['schema']:
            # 按字段向量排名裁剪每张候选表，保留键。
            names=[d.field_name for d,_ in ranked if d.table_name==table['id']][:12]
            names+=table['primary_key']
            for r in payload['relations']:
                if r['left_table']==table['id']: names.append(r['left_field'])
                if r['right_table']==table['id']: names.append(r['right_field'])
            table['fields']=[f for f in table['fields'] if f['name'] in names]
        return payload
