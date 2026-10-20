from __future__ import annotations
from typing import List,Tuple,Dict
import jieba
import math
#from rank_bm25 import BM25Okapi

from retrieval.store import FieldDocument
from app.model_client import ModelClient

class MyBM25Okapi:
    def __init__(self,corpus:List[List[str]],k1:float = 1.5,b:float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus = corpus #分词后的语料
        self.N = len(corpus) #文档总数

        self.doc_len:List[int] = [] #每个文档的词语长度
        self.avgdl:float = 0.0 #全部文档的平均长度
        self.word_df:Dict[str,int] = {} #df：词在多少篇文档n_t
        self.word_idf:Dict[str,float] = {} #预计每个词的IDF
        self.doc_word_freq:List[Dict[str,int]] = [] #每个文档的内部词频

        self._build()

    def _build(self):
        # 1.遍历所有文档，统计词频，文档长度
        total_len = 0
        for doc_words in self.corpus:
            d_len = len(doc_words)
            self.doc_len.append(d_len)
            total_len += d_len

            # 单文档词频统计
            freq = {}
            for w in doc_words:
                freq[w] = freq.get(w,0) + 1 
            self.doc_word_freq.append(freq)

            # 统计df:词在多少不同的文档出现（一篇文档重复出现多次只算一次）
            unique_words = set(doc_words)
            for w in unique_words:
                self.word_df[w] = self.word_df.get(w,0) + 1

        # 平均文档长度
        if self.N > 0:
            self.avgdl = total_len / self.N

        # 2. 预计算所有词的IDF
        for word,nt in self.word_df.items():
            idf = math.log( ((self.N - nt + 0.5) / (nt + 0.5)) + 1)
            self.word_idf[word] = idf

    def get_scores(self,query_words:List[str]) -> List[float]:
        """输入分词后的query，返回所有文档的bm25分数列表"""
        scores = []
        for doc_idx in range(self.N):
            score = 0.0
            d_len = self.doc_len[doc_idx]
            doc_freq = self.doc_word_freq[doc_idx]

            for q_word in query_words:
                if q_word not in self.word_idf:
                    continue
                idf = self.word_idf[q_word]
                f_td = doc_freq.get(q_word,0)

                numerator = f_td * (self.k1 + 1)
                denominator = f_td + self.k1 * (1-self.b+self.b*d_len/self.avgdl)
                score += idf * numerator / denominator

            scores.append(score)

        return scores


class BM25Index:
    """BM25稀疏索引检索"""
    def __init__(self):
        self.docs:List[FieldDocument] = []
        self.tokenized_corpus:List[List[str]] = []
        self.bm25:MyBM25Okapi | None = None
        self._built = False

    def build(self,documents:List[FieldDocument]):
        """构建BM25索引"""
        self.docs = documents
        # 中文分词
        self.tokenized_corpus = [jieba.lcut(doc.keyword_text) for doc in self.docs]
        self.bm25 = MyBM25Okapi(self.tokenized_corpus)
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

def rerank_candidates(
        model_client:ModelClient,
        query:str,
        candidates:List[Tuple[FieldDocument,float]]
) -> List[Tuple[FieldDocument,float]]:
        """
        对RRF输出的候选集合执行重排
        :param model_client: 模型客户端，调用rerank接口
        :param query: 用户原始query
        :param candidates: RRF输出的候选列表 [(FieldDocument, rrf_score)]
        :return: 按rerank分数重新排序后的列表
        """
        doc_texts = [item[0].semantic_text for item in candidates]
        rerank_resp = model_client.rerank(query=query,documents=doc_texts)
        rerank_result:List[Tuple[FieldDocument,float]] = []

        for item in rerank_resp.results:
            original_doc = candidates[item.index][0]
            rerank_result.append((original_doc,item.score))
        rerank_result.sort(key=lambda x: x[1],reverse=True)
        return rerank_result


class HybridRetriever:
    """混合检索主类：BM25+向量检索-RRF融合-rerank精排"""
    def __init__(self,vector_index,bm25_index:BM25Index,model_client:ModelClient):
        self.vector_index = vector_index
        self.bm25_index = bm25_index
        self.model_client = model_client

    def retrieve(
            self,
            query:str,
            top_k:int = 3,
            recall_top_k:int = 5,
            use_rerank:bool = False,
            rerank_candidate_num:int = 8
        ) -> List[Tuple[FieldDocument,float]]:
        """
        :param query: 用户自然语言查询
        :param top_k: 最终返回给Agent的文档数量
        :param recall_top_k: 两路各自召回的候选数量（RRF输入候选，建议5~8）
        :param use_rerank: 是否开启rerank精排开关
        :param rerank_candidate_num: RRF融合后送入rerank的候选条数（6~10）
        """
        # 两路并行召回
        bm25_candidates = self.bm25_index.retrieve(query,top_k=recall_top_k)
        vector_candidates = self.vector_index.retrieve(query,top_k=recall_top_k)

        fused = reciprocal_rank_fusion(bm25_candidates,vector_candidates)

        if use_rerank and len(fused) > 0:
            candidates_for_rerank = fused[:rerank_candidate_num]
            fused = rerank_candidates(self.model_client,query,candidates_for_rerank)

        return fused[:top_k]
