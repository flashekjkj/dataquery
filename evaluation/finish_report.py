"""汇总已完成的冻结运行与本轮审计记录，不修改冻结源码。"""
import hashlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'backend'))
from evaluation.report import generate
from evaluation.run import source_hashes


def main():
    frozen=json.loads((ROOT/'frozen'/'askdata-eval-v1'/'manifest.json').read_text(encoding='utf-8'))
    summary=json.loads((ROOT/'runs'/'holdout-v1'/'summary.json').read_text())
    assert source_hashes()==frozen['sources'], 'Frozen source changed'
    assert summary['n']==15 and summary['source_unchanged'], 'Holdout incomplete or source drift'
    generate()
    report=ROOT/'report.md'
    text=report.read_text(encoding='utf-8').replace('--freeze askdata-eval-v1\n','--freeze askdata-eval-v1 --api-timeout 30\n')
    text+='\n## 本轮审计补充\n\n'
    cfg=frozen['settings']
    text+=f"冻结模型配置：LLM={cfg['LLM_MODEL_ID']}，Embedding={cfg['LLM_EMBEDDING_MODEL']}，Reranker={cfg['RERANK_MODEL_ID']}，温度={cfg['LLM_TEMPERATURE']}，单次网络超时={cfg['LLM_TIMEOUT']}秒，外层最大尝试={cfg['LLM_MAX_RETRIES']}次。SDK内层自动重试已关闭。\n\n"
    offline=json.loads((ROOT/'offline-tests.json').read_text())
    text+=f"明确清单中的离线回归：{offline['run']}项，失败{offline['failures']}，错误{offline['errors']}。前端生产构建通过。\n\n"
    for name in ('before','after'):
        records=json.loads((ROOT/f'semantic_{name}.json').read_text(encoding='utf-8'))
        text+=f"定向SQL语义检查 {name}：{sum(r['passed'] for r in records)}/{len(records)}。"
    text+='\n\n这些定向检查验证已知语义问题修复，不代表通用业务准确率提升。\n\n'
    note=json.loads((ROOT/'development-confirmation-note.json').read_text())
    text+=f"开发集首轮结果完整保留为development-v1。其后复测请求{note['requested']}题，完成{note['completed']}题且已完成题中{note['correct']}题通过；dev-027因上游等待过长中断，未完成请求不计通过、用量未知。复测不替换首轮成绩。\n\n"
    text+='开发集定位到商品表被Skill范围排除、日期字段未召回两项语义错误；冻结前修复并确认对应题通过。另有网络故障影响开发成绩。\n\n'
    diagnostic=json.loads((ROOT/'retrieval-diagnostic.json').read_text(encoding='utf-8'))
    text+='## 开发集检索组件诊断（缓存Embedding，无新增远程调用）\n\n'
    text+='| 组件 | 题数 | Recall@8 | Recall@3 | MRR | 全字段覆盖@8 |\n|---|---:|---:|---:|---:|---:|\n'
    for variant,row in diagnostic['summary'].items():
        text+=f"| {variant} | {row['n']} | {row['recall_at_8']:.1%} | {row['recall_at_3']:.1%} | {row['mrr']:.3f} | {row['all_fields_rate']:.1%} |\n"
    text+='\n这是24道首轮单查询的组件级诊断，不应用Skill过滤、时间字段补足或多轮改写。各组件沿用当前召回配置，非等计算预算对照。当前混合/重排没有优于纯向量，不能宣称叠加组件必然提高效果。重排候选集合为8，因而只改变其顺序，不改变该集合的Recall@8。\n\n'
    text+='必要字段标签含COUNT查询的标识字段代理，属于诊断标注；不能把该指标当成唯一最小字段集合的严格召回率。\n\n'
    text+='执行模式按标注协议固定：SQL题主要使用single，多任务和澄清题使用multi。未单独评估auto复杂度路由，本轮总分不能视作默认auto模式的总体表现。\n\n'
    text+='冻结后未根据留出集调参。留出集只完整运行一次；没有对两套原始项目做性能对比，也没有以本轮结果宣称优于参考项目。\n\n'
    text+='报告完整再生成命令：`python evaluation/finish_report.py`（项目根目录）。价格未经核实，费用仅报告已知Token；中断请求费用未知。\n'
    text+='调用数统计的是逻辑调用，重试次数单独保存在calls中；成功重试之前的失败HTTP尝试也可能没有返回用量，未知用量字段不能当成全部未知计费请求的精确数量。\n'
    report.write_text(text,encoding='utf-8')
    snapshot=ROOT/'frozen'/'askdata-eval-v1'/'snapshot.zip'
    (ROOT/'artifact-hashes.json').write_text(json.dumps({'report_sha256':hashlib.sha256(report.read_bytes()).hexdigest(),
          'snapshot_sha256':hashlib.sha256(snapshot.read_bytes()).hexdigest(),
          'freeze_manifest_sha256':hashlib.sha256((snapshot.parent/'manifest.json').read_bytes()).hexdigest()},indent=2),encoding='utf-8')
    print(report)


if __name__=='__main__':
    main()
