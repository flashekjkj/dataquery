"""真实模型批量评测；题目输入与金标准严格分开。"""
import argparse
import hashlib
import json
import math
import sys
import time
import uuid
import zipfile
from collections import Counter
from itertools import permutations
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from evaluation.prepare import ROOT


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes():
    files = set(BASE.rglob('*.py'))
    files.update((BASE/'data'/'skills').rglob('*.json'))
    files.update(BASE.glob('requirements*'))
    files.update((BASE.parent/'frontend'/'src').rglob('*'))
    files.update(BASE.parent.glob('compose.yaml'))
    files.update((BASE.parent/'frontend').glob('package*.json'))
    return {str(p.relative_to(BASE.parent)).replace('\\', '/'): digest(p)
            for p in sorted(files) if p.is_file() and '__pycache__' not in p.parts}


def freeze(name):
    from app.config import settings
    import importlib.metadata
    target = ROOT / "frozen" / name
    target.mkdir(parents=True, exist_ok=False)
    manifest = {"name": name, "sources": source_hashes(),
                "fixture_sha256": digest(ROOT / 'fixture.duckdb'),
                "datasets": {p.name: digest(p) for p in (ROOT/'datasets').glob('*.jsonl')},
                "settings": {k: v for k,v in settings.model_dump().items()
                             if 'KEY' not in k and 'URL' not in k},
                "provider_hash": hashlib.sha256(settings.LLM_BASE_URL.encode()).hexdigest(),
                "packages": sorted(f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions()),
                "label_review": "assistant-authored, executable references checked; no independent human label audit",
                "created_at": __import__('datetime').datetime.now().astimezone().isoformat()}
    (target/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    with zipfile.ZipFile(target/'snapshot.zip','w',zipfile.ZIP_DEFLATED) as z:
        for path in manifest['sources']:
            z.write(BASE.parent/path,path)
        for p in (ROOT/'datasets').glob('*.jsonl'):
            z.write(p, 'evaluation/datasets/'+p.name)
        z.write(ROOT/'fixture.duckdb','evaluation/fixture.duckdb')
    print(target)


def normalize(rows, ordered=False):
    def cell(v):
        if isinstance(v, (int,float)) and not isinstance(v,bool):
            return round(float(v),6)
        return v
    values = [json.dumps([cell(v) for v in row],ensure_ascii=False) for row in rows]
    return values if ordered else sorted(values)


def matches(expected, actual, ordered=False):
    if len(expected) != len(actual):
        return False
    if not expected:
        return True
    width = len(expected[0])
    if not actual or len(actual[0]) < width or len(actual[0]) > 8:
        return False
    # 模型允许调换列或在同一个聚合查询中返回多个所需事实。
    for columns in permutations(range(len(actual[0])),width):
        if normalize(expected,ordered) == normalize([[r[i] for i in columns] for r in actual],ordered):
            return True
    return False


def score(case, result):
    if case['expected_action'] == 'clarification':
        return {"correct": result.get('collaboration_status')=='clarification', 'field_recall': None}
    candidates = []
    if result.get('query_result') and result.get('success'):
        candidates.append(result['query_result'])
    candidates += [r['query_result'] for r in result.get('task_results',[])
                   if r['status']=='approved' and r.get('query_result')]
    required = set(case['required_fields'])
    found = set(result.get('retrieved_docs', []))
    for task in result.get('task_results',[]):
        found.update(task.get('retrieved_docs',[]))
    # doc_id 是 table.field；若后续协议改变，必须升级评分器。
    fact_scores = [any(not c['is_truncated'] and matches(gold,c['rows'],case['ordered']) for c in candidates)
                   for gold in case['expected_results']]
    return {'correct': bool(result.get('success')) and bool(fact_scores) and all(fact_scores),
            'facts': fact_scores, 'field_recall': len(required & found)/len(required) if required else None}


def run(args):
    from app.config import settings
    from app.model_client import ModelClient
    from app.schemas import EmbeddingResponse
    from agent.graph import build_graph
    from agent.runtime import ask_data_agent
    from memory.conversation import ConversationMemory
    from memory.long_term import LongTermMemoryStore
    from retrieval.service import build_hybrid_index
    from skill.registry import SkillRegistry
    from sql.executor import SQLExecutor
    from observability.tracer import start_trace, end_trace, configure_trace_dir
    from observability.context import request_tokens_var
    from app.clock import evaluation_now
    from observability import logger
    logger.remove()
    logger.add(sys.stderr,level='ERROR')
    cases = [json.loads(s) for s in (ROOT/'datasets'/f'{args.split}.jsonl').read_text(encoding='utf-8').splitlines()]
    if args.limit:
        cases = cases[:args.limit]
    if args.case_ids:
        cases = [c for c in cases if c['id'] in args.case_ids.split(',')]
    out = ROOT/'runs'/args.name
    out.mkdir(parents=True,exist_ok=True)
    if args.freeze:
        manifest = json.loads((ROOT/'frozen'/args.freeze/'manifest.json').read_text(encoding='utf-8'))
        assert manifest['sources']==source_hashes(), 'Source differs from frozen version'
        assert manifest['fixture_sha256']==digest(ROOT/'fixture.duckdb'), 'Fixture changed'
        assert manifest['datasets'][f'{args.split}.jsonl']==digest(ROOT/'datasets'/f'{args.split}.jsonl'), 'Dataset changed'
        assert manifest['settings']=={k:v for k,v in settings.model_dump().items() if 'KEY' not in k and 'URL' not in k}, 'Settings changed'
    metadata = {"name": args.name,"split":args.split,"freeze":args.freeze,"sources":source_hashes(),
                "fixture":digest(ROOT/'fixture.duckdb'),"dataset":digest(ROOT/'datasets'/f'{args.split}.jsonl'),
                "model":settings.LLM_MODEL_ID,"embedding":settings.LLM_EMBEDDING_MODEL,
                "mode_override":args.mode,"retrieval":args.retrieval,"started":time.time(),
                "selected_case_ids":[c['id'] for c in cases],
                "label_audit":"assistant-only", "answer_text_scored":False}
    if (out/'predictions.jsonl').exists():
        old=json.loads((out/'manifest.json').read_text(encoding='utf-8'))
        for key in ('sources','fixture','dataset','model','embedding','mode_override','retrieval','freeze','selected_case_ids'):
            if old.get(key)!=metadata.get(key):
                raise ValueError('Resume configuration mismatch: '+key)
    (out/'manifest.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    configure_trace_dir(out/'traces')
    cache_path = ROOT/'embedding_cache.json'
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    class RecordedClient(ModelClient):
        def __init__(self):
            super().__init__(mock=False)
            self.events=[]
        def _record_call(self,operation,ok,duration_ms,usage=None,retries=0,error=None):
            self.events.append(dict(operation=operation,ok=ok,duration_ms=duration_ms,
                                    usage=usage or {},retries=retries,error=error.kind if error else None))
            return super()._record_call(operation,ok,duration_ms,usage,retries,error)
        def embedding(self,text):
            key = hashlib.sha256((settings.LLM_BASE_URL+settings.LLM_EMBEDDING_MODEL+text).encode()).hexdigest()
            if key not in cache:
                cache[key]=super().embedding(text).model_dump()
                cache_path.write_text(json.dumps(cache),encoding='utf-8')
            return EmbeddingResponse(**cache[key])

    client=RecordedClient()
    started=time.perf_counter()
    hybrid=build_hybrid_index(client)
    class Retrieval:
        def retrieve(self,query,top_k=8,**kwargs):
            if args.retrieval=='bm25':
                return hybrid.bm25_index.retrieve(query,top_k)
            if args.retrieval=='vector':
                return hybrid.vector_index.retrieve(query,top_k)
            return hybrid.retrieve(query,top_k=top_k,use_rerank=args.retrieval=='rerank')
    retriever=Retrieval()
    if args.retrieval=='rerank':
        client.rerank('预热', ['字段预热'])
    warmup_seconds=time.perf_counter()-started
    completed=[]
    predictions=out/'predictions.jsonl'
    if predictions.exists():
        completed=[json.loads(s) for s in predictions.read_text(encoding='utf-8').splitlines()]
    done={r['id'] for r in completed}
    for case in cases:
        if case['id'] in done:
            continue
        memory_root=out/'memory'/case['id']/uuid.uuid4().hex
        memory=ConversationMemory(memory_root)
        graph=build_graph(client,retriever,SQLExecutor(str(ROOT/'fixture.duckdb')),memory,
                          LongTermMemoryStore(memory_root),skill_registry=SkillRegistry(BASE/'data'/'skills'))
        events_start=len(client.events)
        now_token=evaluation_now.set(case['now'])
        trace=start_trace(case['id'])
        request_tokens_var.set({'input':0,'output':0})
        t=time.perf_counter()
        result={}
        error=None
        try:
            for question in case.get('turns',[case['question']]):
                result=ask_data_agent(graph,question,case['id'],execution_mode=args.mode or case['mode'])
            if result.get('query_result'):
                result['query_result']=result['query_result'].model_dump(mode='json')
        except Exception as exc:
            error=type(exc).__name__+': '+str(exc)[:200]
        finally:
            end_trace()
            evaluation_now.reset(now_token)
        record={'id':case['id'],'category':case['category'],'seconds':time.perf_counter()-t,
                'trace_id':trace.trace_id,'result':result,'error':error,'calls':client.events[events_start:]}
        record['score']=score(case,result)
        with predictions.open('a',encoding='utf-8') as f:
            f.write(json.dumps(record,ensure_ascii=False,default=str)+'\n')
        completed.append(record)
        print(f"{case['id']} correct={record['score']['correct']} seconds={record['seconds']:.1f}",flush=True)
    rows=[r for r in completed if r['id'] in {c['id'] for c in cases}]
    durations=sorted(r['seconds'] for r in rows)
    quantile=lambda q: durations[min(len(durations)-1,math.ceil(q*len(durations))-1)] if durations else None
    tokens={direction:sum(call['usage'].get(direction+'_tokens',0) for r in rows for call in r['calls']) for direction in ('input','output')}
    summary={'n':len(rows),'correct':sum(r['score']['correct'] for r in rows),
             'accuracy':sum(r['score']['correct'] for r in rows)/len(rows) if rows else None,
             'p50_seconds':quantile(.5),'p95_seconds':quantile(.95),'tokens':tokens,'warmup_seconds':warmup_seconds,
             'unknown_usage_calls':sum(not call['usage'] for r in rows for call in r['calls']),
             'calls':sum(len(r['calls']) for r in rows),'errors':sum(r['error'] is not None for r in rows),
             'source_unchanged':metadata['sources']==source_hashes()}
    (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--freeze-version')
    parser.add_argument('--freeze')
    parser.add_argument('--split',choices=['smoke','dev','holdout'],default='smoke')
    parser.add_argument('--name',default='smoke-baseline')
    parser.add_argument('--mode',choices=['single','multi','auto'])
    parser.add_argument('--retrieval',choices=['bm25','vector','hybrid','rerank'],default='rerank')
    parser.add_argument('--limit',type=int)
    parser.add_argument('--case-ids')
    parser.add_argument('--api-timeout',type=int)
    args=parser.parse_args()
    if args.freeze_version:
        if args.api_timeout:
            from app.config import settings
            settings.LLM_TIMEOUT=args.api_timeout
        freeze(args.freeze_version)
    else:
        if args.api_timeout:
            from app.config import settings
            settings.LLM_TIMEOUT=args.api_timeout
        run(args)
