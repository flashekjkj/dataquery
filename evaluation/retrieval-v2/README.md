# 检索改进实验 v2

在项目 `backend` 目录中使用 askdata 环境运行。

```powershell
python -X utf8 -m evaluation.retrieval_benchmark prepare
python -X utf8 -m evaluation.retrieval_benchmark dev
# 根据 selection.json 在 backend/.env 设置 RETRIEVAL_STRATEGY 和 RETRIEVAL_LEXICAL_WEIGHT
python -X utf8 -m evaluation.retrieval_benchmark freeze
python -X utf8 -m evaluation.retrieval_benchmark holdout
python -X utf8 -m evaluation.retrieval_benchmark agent
python -X utf8 -m evaluation.retrieval_benchmark report
```

本目录的标注与结果已经存在，prepare 会拒绝覆盖。dev、holdout、agent 在源码、数据和配置相同时支持断点续跑。需要重新开发或新一轮测试时，将 `retrieval_data.ROOT` 改为新实验目录并重新生成、选型和冻结。已有留出集一经查看就作为回归集使用，新一轮结论使用新的留出问题。

`--offline` 可用于本地重排后端的 dev/holdout，要求全部向量已经缓存。该模式仍会运行真实的本地 CrossEncoder。当前已配置 DashScope 的 `qwen3-rerank` API，运行时需要联网。正常模式连接配置的模型服务计算缺失向量。agent 始终调用真实模型。Embedding 缓存包含模型与供应商标识的哈希键，未缓存密钥。

40道开发题包括旧开发集24道首轮单查询、新电商8题、新招聘与财务8题。16道留出题在开发集运行前生成，电商与招聘财务各8题。其中按协议预先指定4道电商、4道招聘题进行真实Agent配对验收。标注由助手编写，参考SQL执行检查，尚未进行独立人工审核。两个集合共享表结构和部分任务模板。

纯关键词、纯向量、等权RRF、加权RRF的三个权重、精确词命中后增强关键词的自适应方案、等权RRF加真实重排，共8种方案。所有方案使用相同表筛选、每路24候选、最多48重排候选、前8语义字段与最多12个上下文字段。字段上下文共用在线链路的JOIN及时间字段补全。开发选型规则依次最大化上下文字段召回、全字段完整率、nDCG，相同分数时优先纯向量。

选型前要求候选方案在开发集无重排降级。当前选定 `hybrid`，已写入本机 `backend/.env`。API重排在开发集中排序分数较高，发生1次连接失败，按既定规则排除出默认候选。API适配仍可通过 `RETRIEVAL_STRATEGY=rerank` 使用。参数改动后应建立新实验目录再评测。

候选表Recall、候选并集Recall、Recall@3/8、nDCG@8、最终上下文字段Recall、完整率、JOIN路径覆盖、上下文字符数及检索延迟分别计量。COUNT(*)新增题不强行指定主键。旧开发题保留原标注，仍包含主键代理标签。JOIN路径覆盖要求两端所有配置键实际存在于最终字段集合中。该指标衡量可用路径，真实SQL是否使用正确路径由Agent结果与SQL另外核对。

检索时延使用已缓存问题向量和已构建索引，API方案计入重排网络耗时。首次加载本地重排模型属于启动开销，需要单列。真实Agent使用同一模型、固定single模式、独立会话、最多2次SQL尝试，单次模型调用1次尝试、30秒配置超时，方案顺序交替。SQL以本地夹具结果等价评分，回答文本未评分。Agent与权限变体离线回归分开报告。

`freeze.json` 锁定源码、数据、夹具、供应商哈希和参数。留出集执行前后与真实Agent执行前后都检查。详细记录逐题保存在 JSONL，可检查BM25、向量、融合、重排与结构补全各阶段。

此前本地权重缺失导致的降级记录存放在 `failed-local-run`。该轮未冻结，也未运行留出集。API适配依据[阿里云官方重排文档](https://help.aliyun.com/zh/model-studio/text-rerank-api)。

真实Agent初次验收发现评测夹具的表标识适配错误。电商目录使用短表名，初始评测连接器使用限定表名，导致采样失败。已将适配器的 `demo_catalog` 设置为与电商目录一致，修订后的全部字段采样验证通过。初次Agent记录保留在 `failed-agent-fixture-run`，完整源码差异及修订哈希见 `agent-harness-amendment.patch`、`agent-amendment.json`。检索留出结果保留原冻结版本，Agent验收使用修订清单校验后重跑所有预选配对。复原时先展开 `snapshot.zip`，再叠加 `agent-harness.zip`。
