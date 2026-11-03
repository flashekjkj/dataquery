# 标准化离线评测包

这是五类微型工程验收集，采用本地复制夹具，不访问线上数据源：

| 包 | 查询案例 | 拒绝案例 | 验证目的 |
|---|---:|---:|---|
| ecommerce | 42 | 0 | 原电商SQL子结果回归 |
| renamed_split | 2 | 2 | 金额改名、订单支付拆表、合法路径与错误路径 |
| new_domain | 2 | 0 | 招聘领域、岗位申请关联 |
| schema_drift | 0 | 2 | 新增/改名后拒绝陈旧上下文 |
| permissions | 2 | 4 | 表列可见范围、星号、CTE与子查询绕过 |

这些数量不足以宣称跨Schema泛化能力，需要在框架上继续增加独立业务与问题族。
漂移后的刷新、局部缓存失效和权限变更旧图拒绝，另由test_source_governance验证。

backend目录运行：

```powershell
python -m evaluation.packages run --package ../evaluation/packages/new_domain --predictions predictions.json --output report.json
```

预测格式：`{"offers":"SELECT COUNT(*) FROM applications WHERE stage='offer'", "job-path":"SELECT ..."}`。
缺少某题的预测计失败；不提供预测时拒绝运行，只有显式`--reference-replay`才使用参考SQL。
reference-replay.json保留参考重放结果，它是框架验收证据，不能作为模型准确率。
new_domain/predictions.example.json提供可直接执行的手工示例预测，也不代表模型输出。
报告区分执行成功、SQL结果正确、表路径覆盖和正确拒绝，保留包输入与评分器hash。

SQL正确率按独立参考结果等价衡量，保留重复行，ordered=true时保留顺序。
路径覆盖是预期物理表和表间等值关联边的召回率，不替代结果正确率或关联粒度审查。
存在多结果原题时拆成SQL子案例；不把澄清题混入SQL指标，也不声称这些子案例就是42个独立问题族。

扩展数据包应保存独立dev/test划分和问题族，添加新库的初始化SQL、权限、语义及人工可审核的参考SQL。
同一数据包比较不同代码版本；不同数据包之间不直接用总分涨跌判断优化收益。
包内初始化SQL来自可信维护者；runner不将不可信SQL包视为安全沙箱。

完整使用与PG集成验证见backend/datasources/GOVERNANCE.md。
