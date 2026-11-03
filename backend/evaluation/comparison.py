"""有硬时间上限的配对实验；生产Agent保持原样。"""
import argparse
import hashlib
import json
import multiprocessing as mp
import sys
import time
import uuid
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from evaluation.comparison_data import ROOT
from evaluation.run import digest, source_hashes, matches


def fatal_provider_error(error):
    text=str(error).lower()
    return any(code in text for code in ('arrearage','overdue-payment','insufficient_quota')) or getattr(error,'kind',None) in ('auth','permission','not_found')


def evidence_tables(result):
    """支持single的JSON/带标签联合结果，不要求multi特有的多结果协议。"""
    tables=[]
    base=[]
    if result.get('query_result'):
        base.append(result['query_result'])
    base += [t['query_result'] for t in result.get('task_results',[]) if t['status']=='approved' and t.get('query_result')]
    def expand(value):
        if isinstance(value,str):
            try:
                value=json.loads(value)
            except (ValueError,TypeError):
                return
        if isinstance(value,dict):
            scalar={k:v for k,v in value.items() if not isinstance(v,(dict,list))}
            if scalar:
                tables.append({'columns':list(scalar),'rows':[list(scalar.values())],'is_truncated':False})
            for item in value.values():
                if isinstance(item,(dict,list)):
                    expand(item)
        elif isinstance(value,list) and value:
            if all(isinstance(x,dict) for x in value):
                keys=list(value[0])
                if all(set(x)==set(keys) for x in value):
                    tables.append({'columns':keys,'rows':[[x[k] for k in keys] for x in value],'is_truncated':False})
            elif all(isinstance(x,list) for x in value):
                tables.append({'columns':[],'rows':value,'is_truncated':False})
    for table in base:
        tables.append(table)
        if table.get('is_truncated'):
            continue
        for row in table['rows']:
            for cell in row:
                expand(cell)
        for i,name in enumerate(table.get('columns',[])):
            if any(word in name.lower() for word in ('section','result_type','query_type','metric','结果类型','结果类别','查询项','指标','任务')):
                groups={}
                for row in table['rows']:
                    groups.setdefault(str(row[i]),[]).append(row[:i]+row[i+1:])
                if len(groups)>1:
                    for rows in groups.values():
                        keep=[j for j in range(len(rows[0])) if any(r[j] is not None for r in rows)]
                        tables.append({'rows':[[r[j] for j in keep] for r in rows],'is_truncated':False})
    return tables


def fact_coverage(case,candidates):
    return [any(not c.get('is_truncated') and matches(expected,c['rows'],False) for c in candidates)
            for expected in case['expected_results']]


def assess(case,result):
    approved=evidence_tables(result)
    if case['group']=='ambiguous':
        import re
        answer=result.get('answer','')
        # 允许single通过自然语言澄清，避免评分依赖multi特有的状态字段。
        text_clarification=bool(re.search(r'请|需要|明确|提供|哪个|什么|定义|标准|目标|口径',answer)) and bool(re.search(r'目标|达标|标准|口径|表现|指标|时间|周期',answer))
        clarification=result.get('collaboration_status')=='clarification' or (text_clarification and not approved)
        return {'action_correct_heuristic':clarification,'coverage':None,'all_facts':None,
                'strict':clarification,'fact_flags':[]}
    flags=fact_coverage(case,approved)
    return {'strict':bool(result.get('success')) and bool(flags) and all(flags),
            'all_facts':bool(flags) and all(flags), 'coverage':sum(flags)/len(flags),
            'fact_flags':flags, 'action_correct_heuristic':None}


def worker(pipe,out,api_timeout,api_attempts):
    from app.config import settings
    settings.LLM_TIMEOUT=api_timeout
    settings.LLM_MAX_RETRIES=api_attempts
    from app.model_client import ModelClient
    from app.schemas import EmbeddingResponse
    from retrieval.service import build_hybrid_index
    from agent.graph import build_graph
    from agent.runtime import ask_data_agent
    from memory.conversation import ConversationMemory
    from memory.long_term import LongTermMemoryStore
    from skill.registry import SkillRegistry
    from sql.executor import SQLExecutor
    from observability import logger
    from observability.context import request_tokens_var
    from observability.tracer import start_trace,end_trace,configure_trace_dir
    from app.clock import evaluation_now
    logger.remove()
    logger.add(sys.stderr,level='ERROR')
    out=Path(out)
    cache_path=ROOT.parent/'embedding_cache.json'
    cache=json.loads(cache_path.read_text()) if cache_path.exists() else {}

    class Client(ModelClient):
        def __init__(self):
            super().__init__(mock=False)
            self.events=[]
            self.building=True
            self.progress=None
        def _record_call(self,operation,ok,duration_ms,usage=None,retries=0,error=None):
            event=dict(operation=operation,ok=ok,duration_ms=duration_ms,usage=usage or {},retries=retries,error=error.kind if error else None)
            event['fatal_provider_error']=bool(error and fatal_provider_error(error))
            self.events.append(event)
            if self.progress:
                with self.progress.open('a',encoding='utf-8') as f:
                    f.write(json.dumps(event)+'\n')
            return super()._record_call(operation,ok,duration_ms,usage,retries,error)
        def embedding(self,text):
            key=hashlib.sha256((settings.LLM_BASE_URL+settings.LLM_EMBEDDING_MODEL+text).encode()).hexdigest()
            if self.building and key in cache:
                return EmbeddingResponse(**cache[key])
            started=time.perf_counter()
            try:
                result=super().embedding(text)
            except Exception as exc:
                # 原客户端的Embedding裸SDK异常可能不进入_record_call。
                # 在评测器补齐可观测性，以免把服务故障误记为Agent推理失败。
                from app.model_client import LLMCallError,classify_llm_error
                if not isinstance(exc,LLMCallError):
                    self._record_call('embedding',False,(time.perf_counter()-started)*1000,error=classify_llm_error(exc))
                raise
            if self.building:
                cache[key]=result.model_dump()
                cache_path.write_text(json.dumps(cache),encoding='utf-8')
            return result

    start=time.perf_counter()
    client=Client()
    hybrid=build_hybrid_index(client)
    client.building=False
    client.rerank('预热',['字段预热'])
    configure_trace_dir(out/'traces')
    pipe.send({'ready':True,'warmup_seconds':time.perf_counter()-start})
    while True:
        request=pipe.recv()
        if request is None:
            return
        case,mode,key=request
        client.events=[]
        client.progress=out/'calls'/f'{key}.jsonl'
        memory=out/'memory'/key/uuid.uuid4().hex
        graph=build_graph(client,hybrid,SQLExecutor(str(ROOT/case['database'])),ConversationMemory(memory),
                          LongTermMemoryStore(memory),skill_registry=SkillRegistry(BASE/'data'/'skills'))
        now_token=evaluation_now.set(case['now'])
        trace=start_trace(key)
        token=request_tokens_var.set({'input':0,'output':0})
        started=time.perf_counter()
        result={}; error=None
        try:
            for question in case['turns']:
                result=ask_data_agent(graph,question,key,execution_mode=mode)
            if result.get('query_result'):
                result['query_result']=result['query_result'].model_dump(mode='json')
        except Exception as exc:
            error=type(exc).__name__+': '+str(exc)[:300]
        finally:
            end_trace(); evaluation_now.reset(now_token);request_tokens_var.reset(token)
        elapsed=time.perf_counter()-started
        pipe.send({'key':key,'pair_id':case['pair_id'],'case_id':case['id'],'variant':case['variant'],
                   'group':case['group'],'mode':mode,'seconds':elapsed,'result':result,
                   'error':error,'trace_id':trace.trace_id,'calls':client.events,'deadline':False,
                   'fatal_provider_error':any(e.get('fatal_provider_error') for e in client.events),
                   'score':assess(case,result)})
        client.progress=None


def run(name,deadline,api_timeout=90,api_attempts=1):
    from app.config import settings
    cases=[json.loads(s) for s in (ROOT/'cases.jsonl').read_text(encoding='utf-8').splitlines()]
    out=ROOT/'runs'/name
    out.mkdir(parents=True,exist_ok=True)
    (out/'calls').mkdir(exist_ok=True)
    manifest={'sources':source_hashes(),'dataset':digest(ROOT/'cases.jsonl'),
              'databases':{p.name:digest(p) for p in ROOT.glob('*.duckdb')},
              'model':settings.LLM_MODEL_ID,'provider_hash':hashlib.sha256(settings.LLM_BASE_URL.encode()).hexdigest(),
              'temperature':settings.LLM_TEMPERATURE,'embedding':settings.LLM_EMBEDDING_MODEL,
              'rerank':settings.RERANK_MODEL_ID,'deadline_seconds':deadline,'api_timeout_seconds':api_timeout,'api_attempts':api_attempts,
              'plan':'24 paired case-data instances, balanced order; same cold-free per-case wall budget',
              'label_audit':'assistant-authored; independent SQL references, no independent human review'}
    metadata=out/'manifest.json'
    if metadata.exists():
        if json.loads(metadata.read_text(encoding='utf-8'))!=manifest:
            raise ValueError('Resume inputs/source differ')
    else:
        metadata.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    predictions=out/'predictions.jsonl'
    rows=[json.loads(s) for s in predictions.read_text(encoding='utf-8').splitlines()] if predictions.exists() else []
    done={r['key'] for r in rows}
    proc=None;pipe=None;warmups=[]
    def stop():
        nonlocal proc,pipe
        if proc:
            proc.terminate();proc.join(10)
            if proc.is_alive():
                proc.kill();proc.join()
            proc=None
        if pipe:
            pipe.close();pipe=None
    def start_worker():
        nonlocal proc,pipe
        pipe,child=mp.get_context('spawn').Pipe()
        proc=mp.get_context('spawn').Process(target=worker,args=(child,str(out),api_timeout,api_attempts))
        proc.start();child.close()
        if not pipe.poll(180):
            stop();raise RuntimeError('Worker warmup timed out')
        warmups.append(pipe.recv())
    try:
        for i,case in enumerate(cases):
            # A/B 数据实例反转同一题的先后顺序；同组比较在相邻时段完成。
            order=['single','multi'] if (i+(case['variant']=='B'))%2==0 else ['multi','single']
            for mode in order:
                key=case['pair_id']+'-'+mode
                if key in done:
                    continue
                if proc is None:
                    start_worker()
                pipe.send((case,mode,key))
                started=time.perf_counter()
                if pipe.poll(deadline):
                    record=pipe.recv()
                else:
                    stop()
                    progress=out/'calls'/f'{key}.jsonl'
                    calls=[json.loads(s) for s in progress.read_text(encoding='utf-8').splitlines()] if progress.exists() else []
                    record={'key':key,'pair_id':case['pair_id'],'case_id':case['id'],'variant':case['variant'],
                            'group':case['group'],'mode':mode,'seconds':time.perf_counter()-started,
                            'result':{},'error':'case_deadline; unfinished API usage unknown',
                            'calls':calls,'deadline':True,'score':assess(case,{})}
                with predictions.open('a',encoding='utf-8') as f:
                    f.write(json.dumps(record,ensure_ascii=False,default=str)+'\n')
                rows.append(record)
                print(f"{len(rows)}/48 {key} strict={record['score']['strict']} coverage={record['score']['coverage']} seconds={record['seconds']:.1f}",flush=True)
                if record.get('fatal_provider_error'):
                    (out/'blocked.json').write_text(json.dumps({'reason':'provider_account_or_credentials_unavailable','last_key':key,'records':len(rows)},indent=2),encoding='utf-8')
                    print('BLOCKED: restore provider account before a new named comparison',flush=True)
                    return
    finally:
        stop()
    assert source_hashes()==manifest['sources'],'Source changed during comparison'
    (out/'complete.json').write_text(json.dumps({'records':len(rows),'source_unchanged':True,'warmups':warmups},indent=2),encoding='utf-8')
    print('COMPLETE',out,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--name',default='paired-main')
    p.add_argument('--deadline',type=int,default=240)
    p.add_argument('--api-timeout',type=int,default=90)
    p.add_argument('--api-attempts',type=int,default=1)
    args=p.parse_args()
    run(args.name,args.deadline,args.api_timeout,args.api_attempts)
