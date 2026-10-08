from __future__ import annotations
import math
from typing import List,Tuple

from .store import FieldDocument,SchemaStore
from app.model_client import ModelClient
from data.database import SCHEMA
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
    1. 读取SCHEMA，通过SchemaStore生成所有字段文档
    2. 调用model_client embedding，为每个文档生成向量，存入内存
    3. 用户输入query，向量化后在内存做余弦相似度检索，返回top-k
    """
    def __init__(self,model_client:ModelClient):
        self.model_client = model_client
        self.documents:List[FieldDocument] = []
        self.vectors:List[List[float]] = []
        self._build = False

    def build(self):
        """构建索引：生成字段文档，批量embedding存入内存"""
        # 1.从database SCHEMA生成字段文档
        schema_store = SchemaStore(SCHEMA)
        self.documents = schema_store.build_field_documents()

        # 2.循环调用embeddings，得到向量
        self.vectors = []
        for doc in self.documents:
            emb_result = self.model_client.embedding(doc.semantic_text)
            self.vectors.append(emb_result.vector)

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

def build_hybrid_index(model_client:ModelClient) -> HybridRetriever:
    # 构建向量索引
    vec_index = SchemaIndex(model_client)
    vec_index.build()
    all_docs = vec_index.documents
    # 构建BM25索引
    bm25_index = BM25Index()
    bm25_index.build(all_docs)

    hybrid_retriever = HybridRetriever(vec_index,bm25_index,model_client)
    return hybrid_retriever
