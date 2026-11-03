# 当前项目单智能体与多智能体配对评测

状态：48次执行记录完整；源码一致性详情见续跑说明及complete.json。
续跑说明：前40条于前一日保存，后8条跨日续跑；业务时钟保持固定。仅backend/test/test.py发生变化，业务源码不变。原manifest与变更hash见manifest-before-resume.json、resume-provenance.json。沙箱联网失败的8条单独归档sandbox-failed-resume.jsonl，不与有效运行混合。完整性检查基于记录变更后的manifest。


API失败/预算受影响执行数：19/48。全体有答案配对20组，无已观察调用故障的配对子集9组。

本轮报告固定预算内实际交付，超时纳入主指标；纯事实正确性与超时原因需结合逐题记录解释，不能推导理论上限。

协议见PROTOCOL.md；两份数据库结构相同，这不是跨Schema成绩。主指标排除澄清题。

## 全部执行的预算内交付表现（包含请求超时和服务失败）

| 模式 | 有答案题数 | 严格完成 | 全部事实具备 | 事实覆盖宏平均 | P50秒 | P95秒 | 已知LLM输入/输出Token |
|---|---:|---:|---:|---:|---:|---:|---:|
| single | 20 | 11 | 11 | 55.0% | 92.0 | 98.9 | 34,632/20,928 |
| multi | 20 | 12 | 12 | 60.0% | 122.8 | 240.2 | 150,871/88,798 |

这张表描述预算内实际交付，不将失败样本丢掉；若存在全局账户故障，不可解释为架构推理能力。

## 无已观察服务故障的完整配对子集（条件诊断，不替代全体结果）

| 模式 | 有答案题数 | 严格完成 | 全部事实具备 | 事实覆盖 | P50秒 | P95秒 | 已知LLM输入/输出Token |
|---|---:|---:|---:|---:|---:|---:|---:|
| single | 9 | 9 | 9 | 100.0% | 24.3 | 88.4 | 20,172/9,810 |
| multi | 9 | 9 | 9 | 100.0% | 81.0 | 208.2 | 74,912/32,030 |

## 分组结果（全体执行，含超时）

| 组别 | 模式 | 有答案题数 | 严格完成 | 事实覆盖 |
|---|---|---:|---:|---:|
| simple | single | 6 | 6 | 100.0% |
| simple | multi | 6 | 6 | 100.0% |
| compound | single | 10 | 1 | 10.0% |
| compound | multi | 10 | 3 | 30.0% |
| dialogue | single | 4 | 4 | 100.0% |
| dialogue | multi | 4 | 3 | 75.0% |

## 配对差异

完整可配对有答案案例20组：multi独赢3组，single独赢2组，平局15组。
事实覆盖差值（multi-single）的意图族聚类bootstrap描述区间：[-0.15, 0.25]。小样本、人工设计题，不能据此宣称一般统计规律。

## 成本与治理

- single：LLM逻辑调用79，Embedding调用28，已记录重试0，review调用0，硬超时0，案例异常0，失败调用10，最终unverified任务0，发生语义返工的任务0。
- multi：LLM逻辑调用129，Embedding调用30，已记录重试0，review调用26，硬超时3，案例异常3，失败调用10，最终unverified任务0，发生语义返工的任务1。

延迟分位数按有答案案例统计，多轮按整段耗时；Token和调用总数包含澄清题。Token只表示已知用量，未核实货币单价；失败尝试与强制终止请求可能有未知用量。预热不进入查询延迟。时间上限相同但自然Token预算不同，不能宣称等Token上限比较。

## 澄清（启发式，需单独审阅）

- A-A1-single：自动动作判断=False；原文见blind-clarification.json。
- A-A1-multi：自动动作判断=True；原文见blind-clarification.json。
- A-A2-multi：自动动作判断=True；原文见blind-clarification.json。
- A-A2-single：自动动作判断=False；原文见blind-clarification.json。
- B-A1-multi：自动动作判断=True；原文见blind-clarification.json。
- B-A1-single：自动动作判断=False；原文见blind-clarification.json。
- B-A2-single：自动动作判断=False；原文见blind-clarification.json。
- B-A2-multi：自动动作判断=False；原文见blind-clarification.json。

响应盲审补充：由本助手隐藏模式标签后按固定口径复核，不是独立专家审阅；不参与主事实分数。

- single：已审4/4，避免武断判断且指出缺失口径2，明确追问0。
- multi：已审4/4，避免武断判断且指出缺失口径3，明确追问3。

## 逐题结果

| 案例 | 模式 | 严格完成 | 事实覆盖 | 业务状态 | 时间秒 |
|---|---|---|---|---|---:|
| A-S1 | single | True | 1.0 | success | 19.7 |
| A-S1 | multi | True | 1.0 | success | 42.9 |
| A-S2 | multi | True | 1.0 | success | 63.9 |
| A-S2 | single | True | 1.0 | success | 27.2 |
| A-S3 | single | True | 1.0 | success | 18.3 |
| A-S3 | multi | True | 1.0 | success | 63.5 |
| A-C1 | multi | False | 0.0 | API调用异常/超时 | 95.7 |
| A-C1 | single | False | 0.0 | API调用异常/超时 | 98.0 |
| A-C2 | single | False | 0.0 | API调用异常/超时 | 96.5 |
| A-C2 | multi | True | 1.0 | success | 212.7 |
| A-C3 | multi | False | 0.0 | 预算超时，未交付 | 240.2 |
| A-C3 | single | True | 1.0 | success | 127.4 |
| A-C4 | single | False | 0.0 | API调用异常/超时 | 95.5 |
| A-C4 | multi | False | 0.0 | API调用异常/超时 | 100.1 |
| A-C5 | multi | True | 1.0 | success | 206.3 |
| A-C5 | single | False | 0.0 | API调用异常/超时 | 96.4 |
| A-D1 | single | True | 1.0 | success | 68.0 |
| A-D1 | multi | True | 1.0 | success | 140.7 |
| A-D2 | multi | True | 1.0 | success | 124.7 |
| A-D2 | single | True | 1.0 | success | 62.7 |
| A-A1 | single | False | None | success | 22.8 |
| A-A1 | multi | True | None | clarification | 25.5 |
| A-A2 | multi | True | None | clarification | 21.4 |
| A-A2 | single | False | None | success | 53.8 |
| B-S1 | multi | True | 1.0 | success | 48.9 |
| B-S1 | single | True | 1.0 | success | 15.0 |
| B-S2 | single | True | 1.0 | success | 14.0 |
| B-S2 | multi | True | 1.0 | success | 120.8 |
| B-S3 | multi | True | 1.0 | success | 81.0 |
| B-S3 | single | True | 1.0 | success | 24.3 |
| B-C1 | single | False | 0.0 | API调用异常/超时 | 97.2 |
| B-C1 | multi | True | 1.0 | success | 218.5 |
| B-C2 | multi | False | 0.0 | 预算超时，未交付 | 240.2 |
| B-C2 | single | False | 0.0 | API调用异常/超时 | 98.9 |
| B-C3 | single | False | 0.0 | API调用异常/超时 | 98.4 |
| B-C3 | multi | False | 0.0 | API调用异常/超时 | 154.8 |
| B-C4 | multi | False | 0.0 | 预算超时，未交付 | 240.2 |
| B-C4 | single | False | 0.0 | API调用异常/超时 | 97.5 |
| B-C5 | single | False | 0.0 | API调用异常/超时 | 98.3 |
| B-C5 | multi | False | 0.0 | API调用异常/超时 | 95.0 |
| B-D1 | multi | True | 1.0 | success | 208.2 |
| B-D1 | single | True | 1.0 | success | 88.4 |
| B-D2 | single | True | 1.0 | success | 67.5 |
| B-D2 | multi | False | 0.0 | API调用异常/超时 | 44.9 |
| B-A1 | multi | True | None | clarification | 24.1 |
| B-A1 | single | False | None | success | 41.1 |
| B-A2 | single | False | None | API调用异常/超时 | 104.8 |
| B-A2 | multi | False | None | API调用异常/超时 | 95.1 |

## 解释边界

- 对比当前实现，不是证明所有多智能体或其理论上限。两个数据实例不是两个全新领域。
- 当前single每轮主要生成一条SQL，multi可规划多次查询；这不是仅改变Agent数量、其余完全相同的因果实验。单个ReAct Agent也可被设计成多次查库，本轮未实现该额外基线。
- multi规划/审核可见全量静态Schema，SQL生成主要使用检索字段；信息组织与自然调用预算也随模式改变，不能把全部差异单独归因于协作。
- 参考结果由独立SQL生成，未提供给Agent；尚无独立人工标签审计或完整文字忠实性评分。
- 评分使用同一中性归一化：允许JSON、显式标签联合表，以及正确子表合并、指标长表透视成参考表；金额规范到2位、比例到4位，计数严格为整数。此归一化修订来自输出格式审计并对两种模式全量应用。保留运行器原始分数，不挑选有利案例。
- 澄清不以multi特有状态作为唯一正确标准，但自动规则可能漏掉提示信息SQL；该项不参与主事实分数。
- 单次配置的配对观察不等于重复随机推理实验；网络/服务状态及小数据仍影响结论。
- 超时按未交付计入，可能已有中间计算但未形成最终响应；不是把未知中间内容认定为错误。
