"""开发集检索组件诊断；只用已有Embedding缓存，不调用远程API。"""
import hashlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'backend'))
from app.config import settings
from app.model_client import ModelClient
from app.schemas import EmbeddingResponse
from retrieval.service import build_hybrid_index
from observability import logger


def main():
    logger.remove()
    cache=json.loads((ROOT/'embedding_cache.json').read_text())

    class CacheOnlyClient(ModelClient):
        def __init__(self):
            super().__init__(mock=True)
        def embedding(self,text):
            key=hashlib.sha256((settings.LLM_BASE_URL+settings.LLM_EMBEDDING_MODEL+text).encode()).hexdigest()
            if key not in cache:
                raise KeyError('query embedding not cached')
            return EmbeddingResponse(**cache[key])
        def rerank(self,query,documents):
            # 真正本地重排，mock只用于保证绝不创建远程客户端。
            self.mock=False
            try:
                return super().rerank(query,documents)
            finally:
                self.mock=True

    hybrid=build_hybrid_index(CacheOnlyClient())
    records=[]
    for line in (ROOT/'datasets'/'dev.jsonl').read_text(encoding='utf-8').splitlines():
        case=json.loads(line)
        # 多任务/多轮真实检索输入是子任务/改写结果，单独诊断仅用首轮单查询。
        if case['mode']!='single' or case.get('turns'):
            continue
        query=case['question']
        required=set(case['required_fields'])
        try:
            runs={
                'bm25':hybrid.bm25_index.retrieve(query,8),
                'vector':hybrid.vector_index.retrieve(query,8),
                'hybrid':hybrid.retrieve(query,top_k=8,use_rerank=False),
                'rerank':hybrid.retrieve(query,top_k=8,use_rerank=True),
            }
        except KeyError:
            continue
        for variant,result in runs.items():
            found={doc.doc_id for doc,_ in result}
            records.append({'id':case['id'],'variant':variant,
                            'recall':len(required & found)/len(required),
                            'recall_at_3':len(required & {d.doc_id for d,_ in result[:3]})/len(required),
                            'mrr':next((1/i for i,(d,_) in enumerate(result,1) if d.doc_id in required),0),
                            'all_fields':required<=found,'docs':sorted(found)})
    summary={}
    for variant in ('bm25','vector','hybrid','rerank'):
        rows=[r for r in records if r['variant']==variant]
        summary[variant]={'n':len(rows),'recall_at_8':sum(r['recall'] for r in rows)/len(rows) if rows else None,
                          'recall_at_3':sum(r['recall_at_3'] for r in rows)/len(rows) if rows else None,
                          'mrr':sum(r['mrr'] for r in rows)/len(rows) if rows else None,
                          'all_fields_rate':sum(r['all_fields'] for r in rows)/len(rows) if rows else None}
    (ROOT/'retrieval-diagnostic.json').write_text(json.dumps({'summary':summary,'rows':records},ensure_ascii=False,indent=2),encoding='utf-8')
    print(summary)


if __name__=='__main__':
    main()
