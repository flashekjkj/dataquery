"""只读重放历史SQL，诊断已知事实在审核前后的变化；不调用模型。"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parents[1]/'backend'))
from sql.executor import SQLExecutor
from evaluation.comparison import evidence_tables


def main(name):
    spec=importlib.util.spec_from_file_location('comparison_report',ROOT/'report_comparison.py')
    scoring=importlib.util.module_from_spec(spec);spec.loader.exec_module(scoring)
    cases={c['pair_id']:c for c in (json.loads(s) for s in (ROOT/'cases.jsonl').read_text(encoding='utf-8').splitlines())}
    out=ROOT/'runs'/name
    records=[json.loads(s) for s in (out/'predictions.jsonl').read_text(encoding='utf-8').splitlines()]
    audits=[]
    for record in records:
        if record['mode']!='multi' or record['group']=='ambiguous':
            continue
        case=cases[record['pair_id']]
        executor=SQLExecutor(str(ROOT/case['database']))
        historical=[];initial=[];errors=[]
        tasks=record['result'].get('task_results',[])
        for task in tasks:
            first_success=True
            for attempt in task.get('attempts',[]):
                if attempt['error'] is not None or not attempt['sql']:
                    continue
                try:
                    result=executor.execute(attempt['sql']).model_dump(mode='json')
                    evidence=scoring.expanded_evidence({'query_result':result})
                    historical+=evidence
                    if first_success:
                        initial+=evidence
                        first_success=False
                except Exception as exc:
                    errors.append(type(exc).__name__)
        raw=[scoring.partition_match(gold,historical,ratio=case['id']=='C2' and i==2) for i,gold in enumerate(case['expected_results'])]
        first=[scoring.partition_match(gold,initial,ratio=case['id']=='C2' and i==2) for i,gold in enumerate(case['expected_results'])]
        final=scoring.rescore(case,record)['fact_flags']
        audits.append({'key':record['key'],'historical_reference_matches':raw,'initial_version_matches':first,'final_matches':final,
                       'lost_known_fact_indices':[i for i,(a,b) in enumerate(zip(raw,final)) if a and not b],
                       'review_count':sum(len(t.get('reviews',[])) for t in tasks),
                       'reviews':[{'task_id':t['task_id'],'status':t['status'],'reviews':t.get('reviews',[])} for t in tasks],
                       'replay_errors':errors})
    payload={'note':'历史成功SQL匹配参考事实不证明整个子任务语义正确；这不是审核precision/recall，也不计为最终交付。',
             'records':audits}
    (out/'review-audit.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print('review records',len(audits),'cases with lost known facts',sum(bool(a['lost_known_fact_indices']) for a in audits))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--name',default='paired-recovered-v1');args=p.parse_args();main(args.name)
