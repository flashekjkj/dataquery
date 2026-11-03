# 执行状态：已完成48次执行（2026-11-03）

最终结果见 report.md，运行目录为 runs/paired-recovered-v1。前40条保留，后8条断点补齐。20组有答案题中，single完成11组，multi完成12组；配对multi独赢3组、single独赢2组，其余15组平局。API超时/连接异常及硬超时保留在主指标内，不代表纯推理准确率。

跨日续跑期间backend/test/test.py变化，业务源码hash一致。完整源码一致性断言失败，complete.json明确保存source_unchanged=false及production_source_unchanged=true；原manifest、变更hash与沙箱联网失败尝试均归档，不隐瞒例外。12项针对性测试通过，审核SQL只读重放完成。

推荐继续优化多智能体的预算分配、规划耗时和独立子任务执行；保留单智能体作为简单题的低成本路径。当前结果不能证明理论上限或跨Schema泛化。数据源与跨Schema后续方案见 DATASOURCE_PLAN.md，尚未实施。

以下保留早期账户故障历史，已被本次完成状态取代。

> 更新：用户更新凭据后服务已恢复，paired-recovered-v1正在运行。本页下面的欠费情况是历史记录，不代表新运行仍然欠费。

## 已完成

- 准备12类题目、两份独立数据实例，共24组配对/48次执行的协议与运行器。
- 验证单表、多表、JSON、标签联合表和子表合并的中性评分，支持部分事实覆盖。
- 6项针对性单元测试通过，另验证子表拼接接受正确分区、拒绝夹带额外错误行。
- 保留预算校准轮与正式轮的全部原始记录。
- 已完成数据源接入与跨Schema的分阶段方案：DATASOURCE_PLAN.md。

## 外部阻塞

正式轮开始后，上游模型API返回400，类型/代码为Arrearage，消息指向overdue-payment。
这是账户/计费可用性问题，不能据此评价单、多智能体谁更准确。
正式轮有48次调用记录，但46次受到服务问题影响，仅1组完整有效配对；样本不足以比较架构。
后续已给评测器增加账户故障熔断，且补齐Embedding裸SDK异常的失败计量，避免未来继续扫描失败请求。
这些是评测器改动，没有修改生产Agent逻辑，也没有回写已保存的运行结果。

## 目前能说的结论

唯一有效正式配对中，两种模式都答对：single约16.6秒，multi约50.1秒。
这只说明该简单题的执行开销，不能代表复合任务表现或理论上限。
校准轮另有3组简单题两种模式均通过，复杂题主要碰到25秒请求预算，未完成有效上限比较；
校准轮不与正式轮混合计分。

不能声称“多智能体更强”或“单智能体更强”。当前缺的是可用的上游服务与有效复杂任务样本。

## 恢复后运行

先恢复账户状态，或在本地.env配置可用模型。不要在聊天里发送密钥。
使用新的运行名称，避免把服务故障轮与恢复后的结果拼接或挑最好成绩：

```powershell
# backend目录，askdata环境
python -X utf8 -m evaluation.comparison --name paired-recovered --deadline 240 --api-timeout 90 --api-attempts 1
# 项目根目录
python -X utf8 evaluation/single-multi/report_comparison.py --name paired-recovered
```

若再次出现账户/认证等不可恢复错误，运行器写blocked.json并停止；不会把它当成低准确率继续批量消耗。
完成后读取新的report.md，分别比较单查询、复合分析、多轮和澄清；不预设最终推荐模式。
