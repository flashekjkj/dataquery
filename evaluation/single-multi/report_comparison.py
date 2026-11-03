"""模式无关的统一重评分和配对报告；保留运行器原始评分。"""
import argparse
import hashlib
import itertools
import json
import math
import random
import statistics
import sys
from collections import Counter,defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parents[1]/'backend'))
from evaluation.comparison import evidence_tables
from evaluation.run import normalize


def canonical(rows,prototype,ratio=False):
    result=[]
    for row in rows:
        values=[]
        for value,kind in zip(row,prototype):
            if isinstance(value,(int,float)) and not isinstance(value,bool):
                if isinstance(kind,int):
                    value=float(value) if float(value).is_integer() else ['noninteger',str(value)]
                elif isinstance(kind,float):
                    value=float(round(value,4 if ratio else 2))
            values.append(value)
        result.append(json.dumps(values,ensure_ascii=False))
    return sorted(result)


def expanded_evidence(result):
    tables=evidence_tables(result)
    for table in list(tables):
        columns=table.get('columns',[])
        names=[str(c).lower() for c in columns]
        metric=next((i for i,n in enumerate(names) if n in ('metric_name','metric','指标名称','指标')),None)
        value=next((i for i,n in enumerate(names) if n in ('metric_value','value','指标值','数值')),None)
        if metric is None or value is None or table.get('is_truncated'):
            continue
        identities=[i for i in range(len(columns)) if i not in (metric,value)]
        groups={};labels=[];conflict=False
        for row in table['rows']:
            key=tuple(row[i] for i in identities)
            label=str(row[metric])
            if label not in labels:
                labels.append(label)
            group=groups.setdefault(key,{})
            if label in group and group[label]!=row[value]:
                conflict=True
            group[label]=row[value]
        if groups and not conflict:
            tables.append({'columns':[columns[i] for i in identities]+labels,
                           'rows':[list(key)+[group.get(label) for label in labels] for key,group in groups.items()],
                           'is_truncated':False})
    return tables


def partition_match(expected,tables,ratio=False):
    """完整正确子表可拼接；金额2位、比例4位、计数严格整数。"""
    if not expected:
        return any(not t.get('is_truncated') and t['rows']==[] for t in tables)
    prototype=expected[0]
    target=Counter(canonical(expected,prototype,ratio));found=Counter()
    width=len(prototype)
    for table in tables:
        rows=table['rows']
        if table.get('is_truncated') or not rows or len(rows[0])<width or len(rows[0])>8:
            continue
        for columns in itertools.permutations(range(len(rows[0])),width):
            candidate=Counter(canonical([[row[i] for i in columns] for row in rows],prototype,ratio))
            if candidate and candidate<=target:
                found |= candidate
                if found==target:
                    return True
    return False


def rescore(case,record):
    if case['group']=='ambiguous':
        return record['score']
    tables=expanded_evidence(record['result'])
    flags=[partition_match(gold,tables,ratio=case['id']=='C2' and i==2) for i,gold in enumerate(case['expected_results'])]
    return {'fact_flags':flags,'coverage':sum(flags)/len(flags),'all_facts':all(flags),
            'strict':bool(record['result'].get('success')) and all(flags)}


def summarize(rows):
    answerable=[r for r in rows if r['group']!='ambiguous']
    durations=sorted(r['seconds'] for r in answerable)
    calls=[c for r in rows for c in r['calls']]
    llm=[c for c in calls if c['operation']!='embedding']
    return {'n':len(rows),'answerable':len(answerable),'strict':sum(r['evaluation']['strict'] for r in answerable),
            'all_facts':sum(r['evaluation']['all_facts'] for r in answerable),
            'coverage':statistics.mean(r['evaluation']['coverage'] for r in answerable) if answerable else None,
            'p50':statistics.median(durations) if durations else 0,
            'p95':durations[math.ceil(len(durations)*.95)-1] if durations else 0,
            'input_tokens':sum(c['usage'].get('input_tokens',0) for c in llm),
            'output_tokens':sum(c['usage'].get('output_tokens',0) for c in llm),
            'logical_calls':len(llm),'embedding_calls':sum(c['operation']=='embedding' for c in calls),
            'retries':sum(c['retries'] for c in calls),'review_calls':sum(c['operation']=='review' for c in calls),
            'deadlines':sum(r['deadline'] for r in rows),'record_errors':sum(r['error'] is not None for r in rows),
            'failed_calls':sum(not c['ok'] for c in calls),
            'unknown_usage_calls':sum(not c['usage'] for c in calls),
            'clarification_heuristic':sum(r['evaluation']['strict'] for r in rows if r['group']=='ambiguous')}


def generate(name,partial=False):
    out=ROOT/'runs'/name
    rows=[json.loads(s) for s in (out/'predictions.jsonl').read_text(encoding='utf-8').splitlines()]
    cases={c['pair_id']:c for c in (json.loads(s) for s in (ROOT/'cases.jsonl').read_text(encoding='utf-8').splitlines())}
    annotations=json.loads((ROOT/'service-audit.json').read_text()) if name=='paired-main' and (ROOT/'service-audit.json').exists() else {}
    if not partial:
        complete=json.loads((out/'complete.json').read_text())
        assert (complete['source_unchanged'] or complete.get('production_source_unchanged')) and len(rows)==48
    for row in rows:
        row['evaluation']=rescore(cases[row['pair_id']],row)
        row['service_affected']=bool(row['error']) or any(not c['ok'] for c in row['calls']) or row['key'] in annotations
    summary={mode:summarize([r for r in rows if r['mode']==mode]) for mode in ('single','multi')}
    bypair=defaultdict(dict)
    for row in rows:
        bypair[row['pair_id']][row['mode']]=row
    pairs=[v for v in bypair.values() if len(v)==2 and v['single']['group']!='ambiguous']
    clean_pairs=[p for p in pairs if not any(r['service_affected'] for r in p.values())]
    paired_keys={r['key'] for pair in clean_pairs for r in pair.values()}
    valid_summary={mode:summarize([r for r in rows if r['mode']==mode and r['key'] in paired_keys]) for mode in ('single','multi')}
    wins=sum(p['multi']['evaluation']['strict'] and not p['single']['evaluation']['strict'] for p in pairs)
    losses=sum(p['single']['evaluation']['strict'] and not p['multi']['evaluation']['strict'] for p in pairs)
    paired={'n':len(pairs),'multi_wins':wins,'single_wins':losses,'ties':len(pairs)-wins-losses,'clean_pairs':len(clean_pairs)}
    # 按意图族聚类，保留同一问题在两个数据库上的相关性。
    clusters=defaultdict(list)
    for p in pairs:
        clusters[p['single']['case_id']].append(p['multi']['evaluation']['coverage']-p['single']['evaluation']['coverage'])
    rng=random.Random(4417)
    values=list(clusters.values())
    samples=sorted(statistics.mean(x for group in rng.choices(values,k=len(values)) for x in group) for _ in range(3000)) if len(values)>=5 else []
    paired['coverage_diff_cluster_interval']=[samples[int(.025*len(samples))],samples[int(.975*len(samples))]] if samples else None
    payload={'summary_all_attempts':summary,'valid_summary':valid_summary,'pairs':paired,'rescored':rows,'scorer_version':'typed-numeric-longform-v3', 'scorer_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (out/'analysis.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 当前项目单智能体与多智能体配对评测','',
           f"状态：{'未完成，仅进度' if partial else '48次执行记录完整；源码一致性详情见续跑说明及complete.json'}。",
           f"\nAPI失败/预算受影响执行数：{sum(r['service_affected'] for r in rows)}/{len(rows)}。全体有答案配对{len(pairs)}组，无已观察调用故障的配对子集{len(clean_pairs)}组。",
           ('\n**本轮受账户故障污染，不能比较架构能力。**' if name=='paired-main' else '\n本轮报告固定预算内实际交付，超时纳入主指标；纯事实正确性与超时原因需结合逐题记录解释，不能推导理论上限。'),
           '', '协议见PROTOCOL.md；两份数据库结构相同，这不是跨Schema成绩。主指标排除澄清题。', '',
           '## 全部执行的预算内交付表现（包含请求超时和服务失败）', '',
           '| 模式 | 有答案题数 | 严格完成 | 全部事实具备 | 事实覆盖宏平均 | P50秒 | P95秒 | 已知LLM输入/输出Token |',
           '|---|---:|---:|---:|---:|---:|---:|---:|']
    if (out/'resume-provenance.json').exists():
        lines[3:3]=['续跑说明：前40条于前一日保存，后8条跨日续跑；业务时钟保持固定。仅backend/test/test.py发生变化，业务源码不变。原manifest与变更hash见manifest-before-resume.json、resume-provenance.json。沙箱联网失败的8条单独归档sandbox-failed-resume.jsonl，不与有效运行混合。完整性检查基于记录变更后的manifest。','']
    for mode,s in summary.items():
        coverage=f"{s['coverage']:.1%}" if s['coverage'] is not None else 'N/A'
        lines.append(f"| {mode} | {s['answerable']} | {s['strict']} | {s['all_facts']} | {coverage} | {s['p50']:.1f} | {s['p95']:.1f} | {s['input_tokens']:,}/{s['output_tokens']:,} |")
    lines+=['','这张表描述预算内实际交付，不将失败样本丢掉；若存在全局账户故障，不可解释为架构推理能力。', '',
            '## 无已观察服务故障的完整配对子集（条件诊断，不替代全体结果）','',
            '| 模式 | 有答案题数 | 严格完成 | 全部事实具备 | 事实覆盖 | P50秒 | P95秒 | 已知LLM输入/输出Token |',
            '|---|---:|---:|---:|---:|---:|---:|---:|']
    for mode,s in valid_summary.items():
        coverage=f"{s['coverage']:.1%}" if s['coverage'] is not None else 'N/A'
        lines.append(f"| {mode} | {s['answerable']} | {s['strict']} | {s['all_facts']} | {coverage} | {s['p50']:.1f} | {s['p95']:.1f} | {s['input_tokens']:,}/{s['output_tokens']:,} |")
    lines+=['','## 分组结果（全体执行，含超时）','','| 组别 | 模式 | 有答案题数 | 严格完成 | 事实覆盖 |','|---|---|---:|---:|---:|']
    for group in ('simple','compound','dialogue'):
        for mode in ('single','multi'):
            selected=[r for r in rows if r['group']==group and r['mode']==mode]
            if selected:
                s=summarize(selected)
                lines.append(f"| {group} | {mode} | {len(selected)} | {s['strict']} | {s['coverage']:.1%} |")
    lines+=['','## 配对差异','',f"完整可配对有答案案例{paired['n']}组：multi独赢{wins}组，single独赢{losses}组，平局{paired['ties']}组。",
            f"事实覆盖差值（multi-single）的意图族聚类bootstrap描述区间：{paired['coverage_diff_cluster_interval']}。小样本、人工设计题，不能据此宣称一般统计规律。",'',
            '## 成本与治理','']
    for mode,s in summary.items():
        tasks=[t for r in rows if r['mode']==mode for t in r['result'].get('task_results',[])]
        lines.append(f"- {mode}：LLM逻辑调用{s['logical_calls']}，Embedding调用{s['embedding_calls']}，已记录重试{s['retries']}，review调用{s['review_calls']}，硬超时{s['deadlines']}，案例异常{s['record_errors']}，失败调用{s['failed_calls']}，最终unverified任务{sum(t['status']=='unverified' for t in tasks)}，发生语义返工的任务{sum(t.get('revision',0)>0 for t in tasks)}。")
    lines+=['','延迟分位数按有答案案例统计，多轮按整段耗时；Token和调用总数包含澄清题。Token只表示已知用量，未核实货币单价；失败尝试与强制终止请求可能有未知用量。预热不进入查询延迟。时间上限相同但自然Token预算不同，不能宣称等Token上限比较。', '',
            '## 澄清（启发式，需单独审阅）','']
    blind=[]
    for row in rows:
        if row['group']=='ambiguous':
            key=hashlib.sha256(('blind-'+row['key']).encode()).hexdigest()[:12]
            blind.append({'blind_id':key,'question':cases[row['pair_id']]['question'],
                          'answer':row['result'].get('answer',''),'table':row['result'].get('query_result'),'unavailable':row['service_affected']})
            lines.append(f"- {row['key']}：自动动作判断={row['evaluation']['strict']}；原文见blind-clarification.json。")
    (out/'blind-clarification.json').write_text(json.dumps(sorted(blind,key=lambda r:r['blind_id']),ensure_ascii=False,indent=2),encoding='utf-8')
    review_path=ROOT/'clarification-review.json'
    if review_path.exists():
        reviews=json.loads(review_path.read_text(encoding='utf-8'))['reviews']
        lines+=['','响应盲审补充：由本助手隐藏模式标签后按固定口径复核，不是独立专家审阅；不参与主事实分数。','']
        for mode in ('single','multi'):
            selected=[r for r in rows if r['mode']==mode and r['group']=='ambiguous']
            judged=[reviews.get(hashlib.sha256(('blind-'+r['key']).encode()).hexdigest()[:12]) for r in selected]
            available=[j for j in judged if j is not None]
            lines.append(f"- {mode}：已审{len(available)}/{len(selected)}，避免武断判断且指出缺失口径{sum(j['safe_action'] for j in available)}，明确追问{sum(j['explicit_followup'] for j in available)}。")
    lines+=['','## 逐题结果','','| 案例 | 模式 | 严格完成 | 事实覆盖 | 业务状态 | 时间秒 |','|---|---|---|---|---|---:|']
    for row in rows:
        status=('预算超时，未交付' if row['deadline'] else 'API调用异常/超时' if row['service_affected'] else row['result'].get('outcome',row['error']))
        lines.append(f"| {row['pair_id']} | {row['mode']} | {row['evaluation']['strict']} | {row['evaluation'].get('coverage')} | {status} | {row['seconds']:.1f} |")
    lines+=['','## 解释边界','',
            '- 对比当前实现，不是证明所有多智能体或其理论上限。两个数据实例不是两个全新领域。',
            '- 当前single每轮主要生成一条SQL，multi可规划多次查询；这不是仅改变Agent数量、其余完全相同的因果实验。单个ReAct Agent也可被设计成多次查库，本轮未实现该额外基线。',
            '- multi规划/审核可见全量静态Schema，SQL生成主要使用检索字段；信息组织与自然调用预算也随模式改变，不能把全部差异单独归因于协作。',
            '- 参考结果由独立SQL生成，未提供给Agent；尚无独立人工标签审计或完整文字忠实性评分。',
            '- 评分使用同一中性归一化：允许JSON、显式标签联合表，以及正确子表合并、指标长表透视成参考表；金额规范到2位、比例到4位，计数严格为整数。此归一化修订来自输出格式审计并对两种模式全量应用。保留运行器原始分数，不挑选有利案例。',
            '- 澄清不以multi特有状态作为唯一正确标准，但自动规则可能漏掉提示信息SQL；该项不参与主事实分数。',
            '- 单次配置的配对观察不等于重复随机推理实验；网络/服务状态及小数据仍影响结论。',
            '- 超时按未交付计入，可能已有中间计算但未形成最终响应；不是把未知中间内容认定为错误。']
    (ROOT/('progress.md' if partial else 'report.md')).write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'valid_summary':valid_summary,'pairs':paired},ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--name',default='paired-main');p.add_argument('--partial',action='store_true')
    args=p.parse_args();generate(args.name,args.partial)
