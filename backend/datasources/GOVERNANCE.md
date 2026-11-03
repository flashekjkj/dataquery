# 数据源治理与验收

## 范围与边界

本阶段提供服务端绑定的表/列Policy、语义配置、指标Skill、两种快照模式、版本与向量缓存、候选表召回及离线包评测。
不实现跨库联邦JOIN、行级过滤、多租户登录认证或自动发现业务口径。
principal来自服务端可信Policy文件，不接受客户端自报身份。生产仍需认证网关与专用只读数据库账号；
一个服务实例绑定一个Policy身份，不能把这层当作完整的用户身份系统。底层RawConnector是基础设施接口，不应直接暴露给用户。
HTTP、CLI、独立MCP入口使用Registry返回的SecuredConnector。

## 切换数据源

1. 安装backend/requirements.txt；在askdata虚拟环境运行后端。
2. 将datasource.example.json复制为datasource.local.json，填写DuckDB path或PG url。
3. 复制policy.example.json、semantics.example.json为各自的.local.json文件，按真实表结构修改。
4. 将datasource.local.json中的policy_file、semantic_files改为这些本地文件路径。
5. backend/.env配置`DATA_SOURCE_FILE=datasource.local.json`并重启服务。

相对DATA_SOURCE_FILE基于backend，配置中的相对路径基于数据源配置文件目录。
切换数据库必须使用新的source_id。会话和偏好按数据源、连接指纹、Schema/Policy/语义版本隔离，不复用旧权限下的历史。
未配置DATA_SOURCE_FILE时保持电商演示源；演示源明确内置只读目录授权与演示语义，新外部源没有Policy则默认不暴露任何表。
本地`*.local.json`、环境配置、日志、缓存、会话均被Git忽略；不要在版本库填写真实凭据。

DuckDB示例（表标识同样限定schema）：

```json
{
  "source_id": "local_orders",
  "dialect": "duckdb",
  "path": "data/other.duckdb",
  "policy_file": "policy.local.json",
  "semantic_files": ["semantics.default.json", "semantics.source.json", "semantics.local.json"],
  "query_mode": "consistent",
  "snapshot_seconds": 120
}
```

## Policy与语义

Policy的tables键使用反射返回的精确id，如`"sales"."orders"`，不要自行按点拆分标识符。
值为null允许该表全部列，列表只允许列出的字段，空列表不暴露表。空tables拒绝所有表。
未知表/列配置直接报错。Policy作用于反射可见视图、采样、向量构建、规划/审核及SQL执行。
执行前SQLGlot解析物理来源、限定列并展开星号；CTE、子查询、UNION不能绕过表/列限制。
星号只返回授权列；未知函数、跨catalog、表函数、写入、锁等保守拒绝。

semantic_files按数组顺序由低到高覆盖，内置演示语义优先级最低；同名指标/JOIN/时间字段整项替换，其他项保留。
格式见semantics.example.json。指标包含依赖表、表达式、过滤条件、时间字段、描述、单位和grain字段列表。
不支持指标引用另一个指标名；表达式必须直接绑定当前快照中的物理字段，避免隐式依赖循环。
JOIN必须声明两端、完整等值键和cardinality；一端必须包含已反射主键，不自动批准many-to-many。
当前支持直接物理表等值JOIN与能追溯的合法相关EXISTS；复杂派生JOIN、IN子查询等要求改写，不能猜测放行。
无合法JOIN配置时不能联表，即使数据库执行器本身允许。跨库引用始终拒绝。
受Policy隐藏的指标、JOIN和时间字段会被移出提示词。语义表达式参与生成与审核，不是确定性指标SQL编译器；
模型是否完整遵守过滤、单位、粒度仍通过参考SQL结果验证，不能因执行成功宣称业务正确。

Skill文件仅引用metrics名字，运行时解析到当前数据源的指标依赖和合法维度关联。
缺少指标定义的Skill不加载，不能靠强制skill参数绕过权限。
现有sales_analysis和customer_profile可在有相同业务指标绑定的新Schema上复用，文件不再声明物理table_scope。

## 快照

- independent：每次采样/SQL使用自己的只读事务，成本较低，不保证多条查询读取同一时刻。
- consistent：一次问答（含子任务和返工）复用同一只读连接。PG使用REPEATABLE READ，执行保存点支持失败后修复；DuckDB使用同一MVCC事务。
- snapshot_seconds限制整个快照生命周期（1至300秒），超时终止连接并明确失败，不静默换新快照。
- PG同时受statement_timeout约束；结束、取消、异常会回滚并释放连接。长LLM等待会占用一致快照连接，应按并发量配置池。
- REST返回snapshot，SSE发送snapshot事件，包含模式、source_id、snapshot_id和观察时间；独立模式snapshot_id为空。
- DuckDB执行异常可能使事务失效，此时一致模式整体失败，不重开事务假装仍一致。

服务入口自动管理会话；底层直接graph.invoke/astream使用者须包裹`with graph.query_session():`。
独立MCP的一次query_database调用视为一次请求，不跨多次工具调用保留事务。

## 版本、缓存与刷新

SchemaSnapshot内容hash、Policy hash、语义hash都进入QueryContext。
每次问答前探查原始元数据；发生变化则拒绝旧上下文。Registry.refresh(source_id)重建授权视图和语义，随后必须重建索引与图；
旧图在版本变化后拒绝运行。HTTP/CLI当前通过重启完成这一过程，没有开放不受保护的管理刷新API。
Policy/语义文件修改也需要显式refresh或重启，不承诺配置文件实时自动生效。

SQLite缓存位于backend/data/cache/schema_vectors.sqlite，仅存表/字段向量及内容hash，不存问题、结果或凭据。
缓存namespace含source_id、Policy/语义版本、模型ID、provider指纹；字段内容独立hash。
单字段变更只重算该字段与所属表向量，删除文档局部清除；撤权或语义变化切换namespace，避免旧向量串用。
旧namespace不会作为新上下文命中；如需物理清除历史缓存，可停服务后删除该SQLite文件重建。

检索先用表向量选择3张候选表，再补合法桥接表，总量最多6张；随后在候选内召回字段。
规划/审核每张候选表最多12个相关字段并补主键、关联键，指标最多4个，不灌入全目录。
必要表超出预算时可能召回不足，这是要通过路径覆盖率衡量的限制，不保证任意大库都能零配置回答。

## 离线测试

backend目录执行：

```powershell
python -m evaluation.packages prepare
python -m evaluation.packages run --package ../evaluation/packages/renamed_split --reference-replay --output replay.json
python -m evaluation.packages run --package ../evaluation/packages/new_domain --predictions predictions.json --output model-report.json
python -m unittest test.test_source_governance -v
```

predictions.json是`{"offers":"SELECT ...","job-path":"SELECT ..."}`，键与cases.jsonl一致；缺失预测计失败，不自动借用参考答案。
reference-replay显式使用标准SQL验证框架，报告会标明，不是模型成绩。实际模型实验先生成预测SQL再提供给runner，评测执行只访问复制出的临时本地数据库。
包由manifest.json、init.sql或fixture.duckdb、policy.json、semantics.json、cases.jsonl组成。
包路径不得越界；每个案例重新建立夹具。包和预测应来自可信评测维护者，不运行不可信初始化SQL。

已有ecommerce、renamed_split、new_domain、schema_drift、permissions五类包。
电商原42题按SQL子结果拆为查询案例，澄清题不混入SQL指标。漂移包验证旧上下文拒绝，刷新与局部缓存重建另有单元测试。
SQL正确率是独立参考结果等价（含重复行与显式顺序配置），执行成功率单列；表路径覆盖率是必需物理表和关联边的召回比例，
不是精确SQL文本匹配，也不单独证明关联粒度正确。权限/漂移拒绝率单列，普通连接故障不会冒充权限正确。
报告包含夹具、标注、Policy、语义和评分器hash，便于版本比较；跨新数据集只能在相同数据包上并列比较版本。

真实PG测试使用专用临时库，不使用业务库：

```powershell
$env:ASKDATA_TEST_PG_URL='postgresql+psycopg://user:TEST_PASSWORD@127.0.0.1:55439/postgres'
python -m unittest test.test_datasources test.test_source_governance test.test_collaboration test.test_evaluation test.test_comparison test.test_runtime_lifecycle test.test_observability test.test_sql_json -q
```

未配置ASKDATA_TEST_PG_URL时PG测试跳过；不能把跳过算已通过。本次验收使用真实PG 16，80项测试全部通过、无跳过。
五类离线包共56个SQL/拒绝案例的参考重放均通过，结果保存在evaluation/packages/reference-replay.json。
这是工程验收，不是线上模型准确率或大规模泛化成绩；未调用付费模型API。

刷新前必须排空当前问答，Registry会拒绝请求运行中的刷新，避免中途切换Policy/语义。
一致快照看门狗会终止数据库连接；如果LLM仍在等待，响应结束还受模型调用超时约束，不保证到期即取消远端模型请求。
