"""从落盘实测结果生成报告，不回写被测代码或重跑失败题。"""
import json
import math
from collections import defaultdict
from pathlib import Path
from evaluation.prepare import ROOT


def interval(k,n):
    if not n:
        return 'N/A'
    z=1.96
    p=k/n
    center=(p+z*z/(2*n))/(1+z*z/n)
    half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    return f'{max(0,center-half):.1%}–{min(1,center+half):.1%}'


def generate():
    lines=['# AskData v1 正式留出集评测报告', '',
           '结果来自本地固定合成数据和现有真实模型 API。该报告是范围受限的项目评测，不是公开基准成绩。', '',
           '## 版本与方法', '',
           '- 开发集27题，留出集15题；6道冒烟题来自开发集，不计作额外独立样本。',
           '- 参考SQL由开发助手编写，独立DuckDB执行核验；尚未经独立人员审计，存在标注偏差风险。',
           '- 主指标为查询结果事实匹配/澄清动作正确率；文字答案忠实性和澄清质量未自动评分。',
           '- 多任务结果仅采信审核通过且未截断的数据；多轮题只评分最终轮，耗时与Token含整段对话。',
           '- 数值保留6位小数；无排序要求按多重集合比较，允许列重排和结果投影，不比较SQL字面字符串。',
           '- 各配置只运行一轮，没有重复实验或统计显著性结论。Wilson区间仅展示小样本不确定性，不消除题目相关性。', '',
           '## 实测汇总', '',
           '| 运行 | 样本 | 事实/动作正确 | 正确率 | Wilson 95%区间 | P50秒 | P95秒 | 输入/输出Token |',
           '|---|---:|---:|---:|---|---:|---:|---:|']
    runs={}
    for directory in sorted((ROOT/'runs').glob('*')):
        if not (directory/'summary.json').exists():
            continue
        summary=json.loads((directory/'summary.json').read_text())
        rows=[json.loads(line) for line in (directory/'predictions.jsonl').read_text(encoding='utf-8').splitlines()]
        manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
        runs[directory.name]=(summary,rows,manifest)
        n,k=summary['n'],summary['correct']
        lines.append(f"| {directory.name} | {n} | {k} | {k/n:.1%} | {interval(k,n)} | {summary['p50_seconds']:.2f} | {summary['p95_seconds']:.2f} | {summary['tokens']['input']:,}/{summary['tokens']['output']:,} |")
    if 'holdout-v1' in runs:
        summary,rows,manifest=runs['holdout-v1']
        lines+=['','## 冻结版本与留出集结果','',f"- 冻结版本：`{manifest['freeze']}`；模型：`{manifest['model']}`。",
                f"- 运行期间源码未变化：`{summary['source_unchanged']}`。运行器在运行前核对源码、数据、Schema/Skill及配置hash。",
                '- 完整hash、安装包版本和源码归档见 frozen/；包括未提交工作树内容，不以Git HEAD冒充实际被测版本。',
                '', '| 类别 | 正确/总数 |', '|---|---:|']
        grouped=defaultdict(list)
        for row in rows:
            grouped[row['category']].append(row)
        for category,items in sorted(grouped.items()):
            lines.append(f"| {category} | {sum(r['score']['correct'] for r in items)}/{len(items)} |")
        lines+=['','### 失败案例（保留原始结果，不据此重新调参）','']
        failed=[r for r in rows if not r['score']['correct']]
        if not failed:
            lines.append('本轮留出集没有自动评分失败案例；不等于不存在业务、泛化或文字回答错误。')
        for row in failed:
            lines.append(f"- `{row['id']}`：category={row['category']}，error={row['error'] or '无运行异常，结果/动作未匹配'}，trace_id=`{row['trace_id']}`；score={row['score']}。")
    lines+=['','## 诊断指标与费用边界','']
    for name,(summary,rows,manifest) in runs.items():
        recalls=[r['score']['field_recall'] for r in rows if r['score'].get('field_recall') is not None]
        attempts=[]
        for row in rows:
            result=row['result']
            if result.get('attempts'):
                attempts.append(result['attempts'])
            attempts.extend(t['attempts'] for t in result.get('task_results',[]) if t.get('attempts'))
        first=sum(a[0]['error'] is None for a in attempts)
        final=sum(a[-1]['error'] is None for a in attempts)
        recovered=sum(a[0]['error'] is not None and a[-1]['error'] is None for a in attempts)
        initial_failed=sum(a[0]['error'] is not None for a in attempts)
        lines.append(f"- **{name}**：字段Recall宏平均={sum(recalls)/len(recalls):.1%}（{len(recalls)}题）；有尝试记录的查询单元={len(attempts)}，首次执行成功={first}，最终执行成功={final}，首次失败后恢复={recovered}/{initial_failed}；逻辑模型调用={summary['calls']}，未知用量调用={summary['unknown_usage_calls']}，启动/预热={summary['warmup_seconds']:.2f}秒。")
    lines+=['','未核实当前供应商/账户的实际计费单价，因此不填写人民币费用。Token为已知用量，不能代替完整账单；Embedding、服务端未知用量和本地重排资源另计。',
            '修复前首题包含本地重排加载，修复后运行器显式预热；温度与SDK重试配置也已调整，不能将前后延迟差异归因于某一个修复。',
            '', '## 局限与结论', '',
            '- 只有三张表、固定合成数据和少量题目；存在字段/日期/地区相关性，执行结果相等不保证所有数据上语义等价。',
            '- 尚未完成独立盲标注、多数据实例语义验证、完整故障注入矩阵或全部组件消融；不能宣称多智能体必然更准确。',
            '- 请求级隔离、SQL语义和超时等离线治理结果见 offline-tests.json 与 offline-tests.log；它们不计入真实业务正确率。',
            '- 本轮冻结后不依据留出集修改代码。后续修改应建立v2并重新评测；长期使用后该留出集不再是未见数据。',
            '', '## 复现', '',
            '在backend目录运行：', '', '```powershell',
            'python -X utf8 -m evaluation.check',
            'python -X utf8 -m evaluation.run --split holdout --name holdout-reproduction --freeze askdata-eval-v1',
            'python -X utf8 -m evaluation.report', '```', '',
            '详细评分规则见README.md，逐题结果与trace位于runs/。源码恢复请从frozen/快照解压到独立目录，保留自己的.env，不要覆盖当前工作树。']
    (ROOT/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(ROOT/'report.md')


if __name__=='__main__':
    generate()
