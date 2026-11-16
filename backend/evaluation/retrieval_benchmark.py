"""检索开发集选型、冻结、留出集及真实Agent配对评测。"""
import argparse
import hashlib
import json
import math
import random
import statistics
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.config import settings
from app.model_client import ModelClient
from app.schemas import EmbeddingResponse
from datasources.duckdb import DuckDBConnector
from datasources.models import DataSourceConfig, QueryContext
from evaluation.packages import paths
from evaluation.retrieval_data import ROOT, prepare
from evaluation.run import digest, matches, source_hashes
from retrieval.config import RetrievalConfig
from retrieval.context import assemble_context
from retrieval.service import build_hybrid_index
from retrieval.store import SchemaStore


PROFILES = {name:RetrievalConfig(strategy=name) for name in ('bm25','vector','hybrid','weighted','adaptive','rerank')}
PROFILES['weighted_015'] = RetrievalConfig(strategy='weighted',lexical_weight=0.15)
PROFILES['weighted_065'] = RetrievalConfig(strategy='weighted',lexical_weight=0.65)


def write_json(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')


class CachedClient(ModelClient):
    def __init__(self, offline=False):
        super().__init__(mock=offline)
        self.offline = offline
        self.cache_path = ROOT.parent/'embedding_cache.json'
        self.cache = json.loads(self.cache_path.read_text()) if self.cache_path.exists() else {}
        self.lock = threading.Lock()
        self.calls = []

    def embedding(self,text):
        key = hashlib.sha256((settings.LLM_BASE_URL+settings.LLM_EMBEDDING_MODEL+text).encode()).hexdigest()
        with self.lock:
            cached = self.cache.get(key)
        if cached is not None:
            return EmbeddingResponse(**cached)
        if self.offline:
            raise ValueError('Embedding缓存缺失，离线模式禁止远程调用')
        response = super().embedding(text)
        with self.lock:
            self.cache[key] = response.model_dump()
            temporary = self.cache_path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(self.cache),encoding='utf-8')
            temporary.replace(self.cache_path)
        return response

    def rerank(self,query,documents):
        if self.offline and settings.RERANK_BACKEND == 'dashscope':
            raise ValueError('API重排需要联网，请去掉--offline')
        # 本地后端在离线模式下仍运行真实CrossEncoder。
        original = self.mock
        self.mock = False
        try:
            return super().rerank(query,documents)
        finally:
            self.mock = original

    def _record_call(self,operation,ok,duration_ms,usage=None,retries=0,error=None):
        with self.lock:
            self.calls.append({'operation':operation,'ok':ok,'milliseconds':duration_ms,
                               'usage':usage or {},'retries':retries,'error_kind':error.kind if error else None})
        return super()._record_call(operation,ok,duration_ms,usage,retries,error)


def load():
    contexts = {key:QueryContext.model_validate(value) for key,value in json.loads((ROOT/'contexts.json').read_text(encoding='utf-8')).items()}
    cases = json.loads((ROOT/'datasets.json').read_text(encoding='utf-8'))
    return contexts,cases


def connector(context):
    database = ROOT.parent/'fixture.duckdb' if context.source_id=='demo' else ROOT/'company.duckdb'
    return DuckDBConnector(DataSourceConfig(source_id=context.source_id,dialect='duckdb',path=str(database),
                                            demo_catalog=context.source_id=='demo'))


def validate_labels():
    contexts,cases = load()
    seen = set()
    for source,context in contexts.items():
        executor = connector(context)
        try:
            valid = {doc.doc_id for doc in SchemaStore(context.snapshot.schema_tables).build_field_documents()}
            for case in cases:
                if case['source']!=source:
                    continue
                if case['question'] in seen:
                    raise ValueError('重复问题 '+case['id'])
                seen.add(case['question'])
                if not set(case['required_fields'])<=valid:
                    raise ValueError('无效字段标注 '+case['id'])
                executor.execute(case['sql'])
                if not paths(case['sql'],context.dialect,context.snapshot)[0]:
                    raise ValueError('无效表路径 '+case['id'])
        finally:
            executor.close()


def indexes(client,contexts,cases):
    texts = set()
    for context in contexts.values():
        texts.update(doc.semantic_text for doc in SchemaStore(context.snapshot.schema_tables).build_field_documents())
        texts.update(table.id+' '+table.description+' '+' '.join(f.name+' '+f.description for f in table.fields) for table in context.snapshot.tables)
    texts.update(case['question'] for case in cases)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i,_ in enumerate(pool.map(client.embedding,sorted(texts)),1):
            if i%20==0:
                print(f'embeddings ready {i}/{len(texts)}',flush=True)
    return {source:build_hybrid_index(client,context) for source,context in contexts.items()}


def recall(required,found):
    return len(required & set(found))/len(required) if required else None


def mean(values):
    values = [value for value in values if value is not None]
    return statistics.mean(values) if values else None


def component(split,offline=False):
    if split=='holdout':
        verify_freeze()
    contexts,cases = load()
    cases = [case for case in cases if case['split']==split]
    output = ROOT/f'{split}.jsonl'
    manifest_file = ROOT/f'{split}-manifest.json'
    manifest = fingerprint()
    if manifest_file.exists():
        if json.loads(manifest_file.read_text(encoding='utf-8'))!=manifest:
            raise ValueError('断点续跑的源码或配置不同')
    else:
        if output.exists():
            raise ValueError('已有结果缺少运行清单')
        write_json(manifest_file,manifest)
    client = CachedClient(offline)
    startup = time.perf_counter()
    retrievers = indexes(client,contexts,cases)
    warmup = {'embedding_and_index_seconds':time.perf_counter()-startup}
    if settings.RERANK_BACKEND == 'local':
        started = time.perf_counter()
        client._get_reranker()
        warmup['reranker_load_seconds'] = time.perf_counter()-started
    else:
        warmup['reranker_load_seconds'] = 0
    write_json(ROOT/f'{split}-startup.json',warmup)
    rows = [json.loads(line) for line in output.read_text(encoding='utf-8').splitlines()] if output.exists() else []
    done = {(row['id'],row['profile']) for row in rows}
    for case in cases:
        context = contexts[case['source']]
        index = retrievers[case['source']]
        expected_tables,expected_edges = paths(case['sql'],context.dialect,context.snapshot)
        for name,profile in PROFILES.items():
            if (case['id'],name) in done:
                continue
            index.config = profile
            ranked,diagnostic = index.retrieve_with_diagnostics(case['question'],8)
            docs,_,structure = assemble_context(ranked,context,case['question'],profile.field_budget)
            required = set(case['required_fields'])
            found = [doc.doc_id for doc,_ in ranked]
            final = {doc.doc_id for doc in docs}
            gains = [int(doc in required) for doc in found]
            dcg = sum(gain/math.log2(i+2) for i,gain in enumerate(gains))
            ideal = sum(1/math.log2(i+2) for i in range(min(len(required),8)))
            completed_edges = []
            for join in context.semantics.get('joins',{}).values():
                keys = {key for a,b in join['keys'] for key in (join['left']+'.'+a,join['right']+'.'+b)}
                if keys<=final:
                    completed_edges.append(tuple(sorted((join['left'],join['right']))))
            row = {'id':case['id'],'profile':name,'source':case['source'],'category':case['category'],
                   'candidate_table_recall':recall(expected_tables,diagnostic['selected_tables']),
                   'union_recall':recall(required,[item['id'] for item in diagnostic['bm25']+diagnostic['vector']]),
                   'recall8':recall(required,found),'recall3':recall(required,found[:3]),
                   'ndcg8':dcg/ideal if ideal else None,
                   'context_recall':recall(required,final),'all_fields':required<=final,
                   'final_table_recall':recall(expected_tables,[doc.table_name for doc in docs]),
                   'join_path_recall':recall(expected_edges,completed_edges),
                   'context_characters':sum(len(doc.semantic_text) for doc in docs),
                   'diagnostic':diagnostic,'structure':structure}
            with output.open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(row,ensure_ascii=False)+'\n')
            rows.append(row)
        print(f'{split} {case["id"]} complete',flush=True)
    summaries = {}
    for name in PROFILES:
        selected = [row for row in rows if row['profile']==name]
        summaries[name] = {key:mean([row[key] for row in selected]) for key in (
            'candidate_table_recall','union_recall','recall8','recall3','ndcg8','context_recall',
            'all_fields','final_table_recall','join_path_recall','context_characters')}
        summaries[name]['n'] = len(selected)
        summaries[name]['rerank_degraded'] = sum(row['diagnostic']['rerank_degraded'] for row in selected)
        summaries[name]['p50_ms'] = statistics.median(row['diagnostic']['milliseconds'] for row in selected)
    write_json(ROOT/f'{split}-summary.json',summaries)
    write_json(ROOT/f'{split}-calls.json',client.calls)
    if split=='dev':
        eligible = [name for name in PROFILES if summaries[name]['rerank_degraded']==0]
        # 先保证SQL上下文字段完整，再比较排序。相同分数时优先较简单方案。
        selected = max(eligible,key=lambda name:(summaries[name]['context_recall'],
            summaries[name]['all_fields'],summaries[name]['ndcg8'],name=='vector'))
        write_json(ROOT/'selection.json',{'profile':selected,'config':PROFILES[selected].model_dump(),
            'rule':'maximize dev context_recall, all_fields, ndcg8; exact ties prefer vector',
            'summaries':summaries,'cases_sha256':digest(ROOT/'datasets.json')})
        print('selected '+selected,flush=True)
    else:
        verify_freeze()


def fingerprint():
    return {'sources':source_hashes(),'files':{name:digest(ROOT/name) for name in
        ('datasets.json','contexts.json','company.sql','company.duckdb')},
        'ecommerce_fixture':digest(ROOT.parent/'fixture.duckdb'),
        'settings':{key:value for key,value in settings.model_dump().items() if 'KEY' not in key and 'URL' not in key},
        'provider_hash':hashlib.sha256(settings.LLM_BASE_URL.encode()).hexdigest(),
        'rerank_provider_hash':hashlib.sha256(settings.RERANK_API_URL.encode()).hexdigest(),
        'profiles':{name:config.model_dump() for name,config in PROFILES.items()}}


def freeze():
    if (ROOT/'freeze.json').exists():
        raise ValueError('冻结已存在')
    selected = json.loads((ROOT/'selection.json').read_text(encoding='utf-8'))
    if settings.RETRIEVAL_STRATEGY != selected['config']['strategy']:
        raise ValueError('先将线上默认策略更新为开发集选定策略')
    if settings.RETRIEVAL_LEXICAL_WEIGHT != selected['config']['lexical_weight']:
        raise ValueError('线上关键词权重与开发集选定权重不同')
    manifest = fingerprint()
    development = json.loads((ROOT/'dev-manifest.json').read_text(encoding='utf-8'))
    if manifest['sources'] != development['sources']:
        raise ValueError('开发集运行后源码发生变化，请在新目录重测开发集')
    for key,value in development['settings'].items():
        if key not in ('RETRIEVAL_STRATEGY','RETRIEVAL_LEXICAL_WEIGHT') and manifest['settings'][key]!=value:
            raise ValueError('开发集运行后设置发生变化 '+key)
    manifest['selection_sha256'] = digest(ROOT/'selection.json')
    write_json(ROOT/'freeze.json',manifest)
    print('frozen',flush=True)


def verify_freeze():
    frozen = json.loads((ROOT/'freeze.json').read_text(encoding='utf-8'))
    amendment_file = ROOT/'agent-amendment.json'
    if amendment_file.exists():
        amendment = json.loads(amendment_file.read_text(encoding='utf-8'))
        if amendment['original_freeze_sha256'] != digest(ROOT/'freeze.json'):
            raise ValueError('Agent夹具修订与原冻结清单不同')
        if set(amendment['sources']) != {'backend/evaluation/retrieval_benchmark.py'}:
            raise ValueError('修订只允许修改Agent评测适配文件')
        frozen['sources'].update(amendment['sources'])
    current = fingerprint()
    current['selection_sha256'] = digest(ROOT/'selection.json')
    if frozen != current:
        raise ValueError('代码、数据、配置与冻结版本不同')


def agent_evaluation():
    verify_freeze()
    from agent.graph import build_graph
    from agent.runtime import ask_data_agent
    from memory.conversation import ConversationMemory
    from memory.long_term import LongTermMemoryStore
    from observability.tracer import configure_trace_dir, start_trace, end_trace
    contexts,cases = load()
    cases = [case for case in cases if case['split']=='holdout' and case['e2e']]
    selected = json.loads((ROOT/'selection.json').read_text(encoding='utf-8'))
    profiles = {'vector':PROFILES['vector'],'selected':RetrievalConfig.model_validate(selected['config'])}
    client = CachedClient()
    client.max_retries = 1
    retrievers = indexes(client,contexts,cases)
    configure_trace_dir(ROOT/'traces')
    output = ROOT/'agent.jsonl'
    completed = [json.loads(line) for line in output.read_text(encoding='utf-8').splitlines()] if output.exists() else []
    done = {(row['id'],row['profile']) for row in completed}
    with tempfile.TemporaryDirectory() as temporary:
        for i,case in enumerate(cases):
            context = contexts[case['source']]
            executor = connector(context)
            try:
                expected = executor.execute(case['sql']).rows
                order = list(profiles)
                if i%2:
                    order.reverse()
                for name in order:
                    if (case['id'],name) in done:
                        continue
                    index = retrievers[case['source']]
                    index.config = profiles[name]
                    memory = Path(temporary)/case['id']/name
                    graph = build_graph(client,index,executor,ConversationMemory(memory),LongTermMemoryStore(memory),
                                        max_attempts=2,query_context=context)
                    started = time.perf_counter()
                    call_start = len(client.calls)
                    result = {}
                    failure = None
                    trace = start_trace(case['id']+'-'+name)
                    try:
                        result = ask_data_agent(graph,case['question'],case['id']+'-'+name,execution_mode='single')
                        if result.get('query_result'):
                            result['query_result'] = result['query_result'].model_dump(mode='json')
                    except Exception as exc:
                        failure = type(exc).__name__
                    finally:
                        end_trace()
                    query_result = result.get('query_result')
                    correct = bool(result.get('success') and query_result and not query_result['is_truncated']
                                   and matches(expected,query_result['rows']))
                    row = {'id':case['id'],'profile':name,'source':case['source'],'category':case['category'],
                           'correct':correct,'seconds':time.perf_counter()-started,'result':result,
                           'error_type':failure,'trace_id':trace.trace_id,'calls':client.calls[call_start:]}
                    with output.open('a',encoding='utf-8') as stream:
                        stream.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
                    print(f'agent {case["id"]} {name} correct={correct} seconds={row["seconds"]:.1f}',flush=True)
            finally:
                executor.close()
    verify_freeze()


def report():
    verify_freeze()
    selection = json.loads((ROOT/'selection.json').read_text(encoding='utf-8'))
    holdout = json.loads((ROOT/'holdout-summary.json').read_text(encoding='utf-8'))
    lines = ['# 检索改进与配对评测','',f'开发集选定 `{selection["profile"]}`。',
        '', '开发集40题，留出集16题。电商目录3表16字段，招聘与财务目录25表93字段，含20张相似活动表。',
        '全部SQL在本地DuckDB夹具运行。标注由助手编写、参考SQL已执行验证，缺少独立人工复核。',
        '留出集与开发集共享业务结构和部分SQL任务模板，衡量当前场景泛化。旧留出集不参与选型。',
        '', '|方案|候选表覆盖|Recall@8|nDCG@8|上下文字段覆盖|全字段完整率|JOIN路径覆盖|检索P50毫秒|',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name,value in holdout.items():
        cells = [f'{value[key]:.2%}' if value[key] is not None else '—' for key in
                 ('candidate_table_recall','recall8','ndcg8','context_recall','all_fields','join_path_recall')]
        lines.append('|'+name+'|'+'|'.join(cells)+f'|{value["p50_ms"]:.1f}|')
    rows = [json.loads(line) for line in (ROOT/'holdout.jsonl').read_text(encoding='utf-8').splitlines()]
    vector = {row['id']:row for row in rows if row['profile']=='vector'}
    chosen = {row['id']:row for row in rows if row['profile']==selection['profile']}
    deltas = [chosen[key]['context_recall']-vector[key]['context_recall'] for key in vector
              if vector[key]['context_recall'] is not None]
    rng = random.Random(20261107)
    samples = sorted(statistics.mean(rng.choices(deltas,k=len(deltas))) for _ in range(2000))
    lines.extend(['',f'相对向量基线的上下文字段覆盖率差值为 {statistics.mean(deltas):+.2%}，配对Bootstrap 95%区间为 [{samples[50]:+.2%}, {samples[1949]:+.2%}]。',
        '小样本区间按题重采样，未按任务模板聚类，不能据此推断跨领域总体收益。',
        '', '两路各召回24，RRF k=60，重排最多48候选，语义字段8，包含JOIN和时间字段的总预算12。',
        '同一问题的各策略共用候选表筛选，评测分别记录候选并集、截断结果和结构补全结果。',
        '纯向量方案也使用相同的结构补全，比较融合组件时控制这个变量。',
        '检索延迟使用已缓存Embedding、已加载索引，包含重排API网络耗时或本地推理，启动开销见 startup.json。',
        f'重排后端为 `{settings.RERANK_BACKEND}`，API模型为 `{settings.RERANK_API_MODEL}`，本地模型为 `{settings.RERANK_MODEL_ID}`。',
        f'真实重排降级次数为 {holdout["rerank"]["rerank_degraded"]}。'])
    agent_file = ROOT/'agent.jsonl'
    if agent_file.exists():
        agent_rows = [json.loads(line) for line in agent_file.read_text(encoding='utf-8').splitlines()]
        lines.extend(['','|真实Agent方案|完成/预期|SQL结果正确|执行成功|耗时P50秒|耗时P95秒|','|---|---:|---:|---:|---:|---:|'])
        for name in ('vector','selected'):
            values = [row for row in agent_rows if row['profile']==name]
            if not values:
                lines.append(f'|{name}|0/8|—|—|—|—|')
                continue
            durations = sorted(row['seconds'] for row in values)
            lines.append(f'|{name}|{len(values)}/8|{sum(row["correct"] for row in values)}/{len(values)}|{sum(bool(row["result"].get("success")) for row in values)}/{len(values)}|{statistics.median(durations):.1f}|{durations[math.ceil(.95*len(durations))-1]:.1f}|')
        lines.extend(['','真实Agent使用同一模型、独立会话、固定single模式、最多2次SQL尝试，交替执行方案顺序。',
                      '单次模型调用最多尝试1次，配置超时30秒。评分检查实际SQL结果，回答文本未评分。',
                      '每方案8题为预先指定的分层验收子集。完整16题检索消融已完成。'])
    else:
        lines.extend(['','真实Agent评测尚未运行。'])
    lines.extend(['','详细阶段排名见 `dev.jsonl` / `holdout.jsonl`，真实Agent结果见 `agent.jsonl`。',
                  '冻结清单见 `freeze.json`，配置、源码和夹具在留出评测前后均校验哈希。'])
    if (ROOT/'agent-amendment.json').exists():
        lines.extend(['','Agent验收初次执行发现电商夹具短表名与限定表名不一致，采样阶段失败。',
                      '仅修复评测适配器的 demo_catalog 参数及部分完成报告处理，原检索、标注、参数保持原冻结哈希。',
                      '初次记录保存在 failed-agent-fixture-run，修订差异、哈希及原因见 agent-amendment.json 和 agent-harness-amendment.patch。',
                      'Agent验收采用修订后的同一适配器重新执行全部预选配对题。'])
    (ROOT/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(ROOT/'report.md',flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action',choices=['prepare','dev','freeze','holdout','agent','report'])
    parser.add_argument('--offline',action='store_true')
    args = parser.parse_args()
    from observability import logger
    logger.remove()
    logger.add(__import__('sys').stderr,level='ERROR')
    settings.LLM_TIMEOUT = 30
    settings.LLM_MAX_RETRIES = 1
    if args.action=='prepare':
        prepare()
        validate_labels()
    elif args.action in ('dev','holdout'):
        validate_labels()
        component(args.action,args.offline)
    elif args.action=='freeze':
        freeze()
    elif args.action=='agent':
        agent_evaluation()
    else:
        report()


if __name__=='__main__':
    main()
