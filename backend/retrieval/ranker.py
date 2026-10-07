from __future__ import annotations
from typing import List,Tuple,Dict
import jieba
from rank_bm25 import BM25Okapi

from retrieval.store import FieldDocument

class BM25Index:
    """BM25稀疏索引检索"""
    def __init__(self):
        self.docs:List[FieldDocument] = []
        self.tokenized_corpus:List[List[str]] = []
        self.bm25:BM25Okapi | None = None
        self._built = False

    def build(self,documents:List[FieldDocument]):
        """构建BM25索引"""
        self.docs = documents
        # 中文分词
        self.tokenized_corpus = [jieba.lcut(doc.keyword_text) for doc in self.docs]
        self.bm25 = BM25Okapi(self.tokenized_corpus)
        self._built = True

    def retrieve(self,query:str,top_k:int = 3) -> List[Tuple[FieldDocument,float]]:
        """BM25检索，返回[(doc,scores)]"""
        if not self._built or self.bm25 is None:
            raise RuntimeError("BM25Index未build，请先调用build()")
        tokenized_query = jieba.lcut(query)
        scores = self.bm25.get_scores(tokenized_query)
        scored_docs = list(zip(self.docs,scores))
        scored_docs.sort(key=lambda x:x[1],reverse=True)
        return scored_docs[:top_k]

def reciprocal_rank_fusion(
        bm25_results:List[Tuple[FieldDocument,float]],
        vector_results:List[Tuple[FieldDocument,float]],
        k:int = 60
) -> List[Tuple[FieldDocument,float]]:
    """
    RRF倒数排名融合
    输入两路召回结果，输出融合后的列表，按RRF分数降序
    """
    rrf_map:Dict[str,float] = {}
    doc_map:Dict[str,FieldDocument] = {}

    # 处理BM25结果
    for rank,(doc,_score) in enumerate(bm25_results):
        doc_id = doc.doc_id
        doc_map[doc_id] = doc
        rrf_score = 1.0 / (k + rank + 1)
        if doc_id in rrf_map:
            rrf_map[doc_id] += rrf_score
        else:
            rrf_map[doc_id] = rrf_score

    # 处理向量检索结果
    for rank,(doc,_score) in enumerate(vector_results):
        doc_id = doc.doc_id
        doc_map[doc_id] = doc
        rrf_score = 1.0 / (k + rank + 1)
        if doc_id in rrf_map:
            rrf_map[doc_id] += rrf_score
        else:
            rrf_map[doc_id] = rrf_score

    final_list = [(doc_map[did],score) for did,score in rrf_map.items()]
    final_list.sort(key=lambda x: x[1],reverse=True)
    return final_list

class HybridRetriever:
    """混合检索主类：BM25+向量检索-RRF融合"""
    def __init__(self,vector_index,bm25_index:BM25Index):
        self.vector_index = vector_index
        self.bm25_index = bm25_index

    def retrieve(self,query:str,top_k:int = 3,recall_top_k:int = 5) -> List[Tuple[FieldDocument,float]]:
        """
        :param query: 用户自然语言查询
        :param top_k: 最终返回给Agent的文档数量
        :param recall_top_k: 两路各自召回的候选数量（RRF输入候选，建议5~8）
        """
        # 两路并行召回
        bm25_candidates = self.bm25_index.retrieve(query,top_k=recall_top_k)
        vector_candidates = self.vector_index.retrieve(query,top_k=recall_top_k)

        fused = reciprocal_rank_fusion(bm25_candidates,vector_candidates)
        return fused[:top_k]