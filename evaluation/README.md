# AskData 业务评测 v1

本目录是可复现的本地业务评测，不是 Spider 等公开基准的成绩。
首版42题：开发27题，留出15题，冒烟6题是开发集子集。
标签由开发助手编写和复核，参考SQL在固定夹具上执行生成预期结果，尚无独立人工标注审计。
禁止将此报告描述成通用场景准确率或有统计显著性的提升证明。

## 数据与规则

- fixture.duckdb：固定12个客户、6个商品、180笔订单，覆盖2026年8~10月、支付/退款/取消。
- datasets/dev.jsonl：开发与排错使用。
- datasets/holdout.jsonl：在版本冻结后运行；不依据其结果继续修改被测实现。
- datasets/smoke.jsonl：6题，覆盖计数、指标、日期、JOIN、排名和多任务。
- 数据仅为合成演示数据，不含真实用户记录。
- 只向模型提供问题/对话；参考SQL、预期结果、必要字段仅由评分器读取。
- SQL结果的数值归一化到小数点后6位，忽略列别名；无排序要求时保留重复行但忽略行顺序。
- 有排序要求时必须遵守行顺序；可通过列排列匹配允许同一结果中包含多个事实。
- 查询正确要求业务success且所有参考事实在未截断、审核通过的结果中匹配。
- 澄清题要求协作状态为clarification；此项只测动作，澄清文字质量仍需人工判断。
- 多轮题评估最终一轮结果，延迟和成本包含整段对话。
- 不以文字答案是否包含某个数字作为主要正确性依据；本版不自动评分答案文字的忠实性。
- 检索Recall使用标注必要字段集合，是诊断项；多任务按最终任务记录取并集，不等于逐步骤召回率。
- 不在本版主准确率中混入网络故障注入；离线故障治理测试另报。
- 相似业务结构存在于两个集合中；没有外部盲标注，不能声称严格无泄漏的公开测试。

## 命令

在 backend 目录、askdata环境中：

```powershell
python -X utf8 -m evaluation.prepare
python -X utf8 -m evaluation.run --split smoke --name before-fixes
python -X utf8 -m evaluation.run --split dev --name development-v1
python -X utf8 -m evaluation.run --freeze-version NEW_VERSION_NAME --api-timeout 30
python -X utf8 -m evaluation.run --split holdout --name holdout-reproduction --freeze askdata-eval-v1 --api-timeout 30
```

运行器真实调用现有API，按题落盘，支持同一配置中断续跑。不要用同一输出目录运行不同配置。
每个案例重建独立graph、会话与偏好存储；Embedding内容缓存用于避免重复付费。
冷启动/索引准备单列；本地重排模型首次加载会进入首题耗时，报告需明确说明。
`--mode single|multi|auto` 可覆盖案例模式，`--retrieval bm25|vector|hybrid|rerank` 用于消融。
开发集用于实验，最终留出集只跑冻结的默认配置，不挑选最好的一次。

## 交付

- runs/*/manifest.json：源码hash、夹具hash、数据集hash、模式与模型。
- runs/*/predictions.jsonl：每题输出、SQL/任务结果、trace_id、耗时、调用用量和评分。
- runs/*/summary.json：正确率、P50/P95墙钟延迟和Token。
- runs/*/traces/：可定位故障的逐步骤记录。
- frozen/*/manifest.json：冻结源码、数据、配置与安装包版本；不包含API密钥。
- frozen/*/snapshot.zip：被测Python源码、标注集、夹具快照，可恢复未提交的工作树版本。
- 最终report.md：实测结果、边界与后续工作，不编造因缺少供应商价格而无法确定的费用。

提供商返回用量只能统计已知Token；失败请求未返回用量时单独报未知数量。
未验证当前账户的计费单价，因此报告不填人民币金额。可取得单价后按输入/输出Token分别计算，
另计Embedding、可能的服务端重试费用和本地重排资源，不能称为完整账单。

修复后的运行器把本地重排预热单列。修复前首题包含首次加载成本，前后延迟不能直接作因果比较。
冻结后不要重新执行prepare或覆盖frozen目录。直接使用保存的标注集和数据快照。
提供商服务端模型可能变化，温度为0也不能保证将来逐字复现。

## 仓库发布范围

仓库保留源码、测试、标注、合成评测夹具、正式报告、冻结清单和小型源码快照，以及逐题预测和汇总证据。
会话副本、trace、控制台日志和Embedding缓存仅保留在本地，不上传。报告再生成不依赖这些日志。
应用演示数据库未入库，首次启动前请按项目README运行`python data/seed_demo_data.py`。

冻结检查使用原始字节哈希；Git检出时的换行转换或排除的本地文件可能导致不匹配。需要精确复现v1时，请将frozen内的源码快照解压到独立目录，复制对应冻结清单并配置自己的.env。不要把Git检出自动当作与原始工作树逐字节相同。
