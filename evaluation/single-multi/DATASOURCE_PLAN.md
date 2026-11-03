# 数据源接入与跨Schema改造方案（方案，不是已实现功能）

## 范围

先支持DuckDB与一种外部数据库PostgreSQL，完成两套不同业务结构的接入与评测。
“任意连接”指在已支持的驱动、授权范围内接入新库，不承诺无业务说明即可正确理解任何指标。

区分三件事：

1. 新Schema泛化：换一套表、字段、业务领域，不修改Agent节点代码。
2. 同一个PostgreSQL库跨schema查询：例如sales.orders与crm.customers，处理同名表和限定名。
3. 跨数据库联合分析：两个独立连接的结果汇总或联邦查询，第一版不做自动跨库JOIN。

## 当前耦合点

- agent/runtime.py固定DB_PATH与DATA_DIR。
- retrieval/service.py从全局SCHEMA构建索引。
- agent/query_context.py与supervisor.py读取全局SCHEMA/RELATIONS。
- sql提示词固定DuckDB方言，执行器用DuckDB解析和连接。
- Skill直接声明orders/customers/products表名；新库不能自动沿用这些物理表名。
- 评测程序仍围绕固定夹具，需要新数据包清单，不能只换.db就保留旧标准答案。

## 阶段一：接入层与元数据目录

建议新增datasources包，职责如下：

```text
datasources/
  models.py          # DataSourceConfig / SchemaSnapshot / QueryContext
  registry.py        # source_id -> 已授权配置
  base.py            # Connector接口
  duckdb.py
  postgres.py
  catalog.py         # 元数据采集、版本与缓存
```

接口至少包含test_connection、inspect_schema、sample_values、execute_readonly与close。
QueryContext携带source_id、dialect、schema_version、policy_version、可见表集合和指标定义。
以构造参数把context注入检索、规划、审核、生成与执行；取消它们对全局SCHEMA的依赖。
现有静态电商Schema保留为DemoCatalog，不再作为所有数据源的唯一事实来源。

PostgreSQL可使用SQLAlchemy Inspector采集schema/table/column/PK/FK/注释；
缺少外键时，业务管理员确认关联，不让LLM自行建立可执行的授权关系。
SQLAlchemy官方参考：https://docs.sqlalchemy.org/en/20/core/reflection.html

第一版验收：配置一套PostgreSQL库，选可见表并采集元数据，不改Agent代码即可查询；
DuckDB演示库仍通过回归。第二个连接不复用第一个连接的Schema或记忆。

## 阶段二：方言、权限与语义

- 生成提示词接受dialect参数；SQL校验和执行由对应Connector负责，不能用DuckDB parser验证所有PostgreSQL SQL。
- PostgreSQL使用权限受限的只读账号、只读事务、每请求statement_timeout，保证超时后连接回滚再归还池。
- 服务端凭source_id查找连接凭据，凭据不进入模型、日志或评测归档；连接配置由授权管理入口维护。
- 检索、采样、规划可见Schema与执行都使用同一授权范围；Skill范围是业务能力范围，不等于安全授权。
- 表标识使用source/schema/table/column完整身份，处理同名表、引号和大小写；执行前核对引用。
- 添加语义配置：指标表达式、时间字段、过滤规则、枚举、单位、合法JOIN、粒度、示例SQL及版本。
- Skill改为引用业务能力/指标，再解析到数据源的物理表，而非全局硬编码orders。

PostgreSQL官方参数说明：https://www.postgresql.org/docs/current/runtime-config-client.html

第一版验收：两套同名orders表不串库；禁止表不会进入模型上下文或执行；金额指标的定义可查看，
不明确时请求澄清，不把缺失语义填成猜测。

多智能体还需明确多条查询的数据一致性：子任务不能无提示地混用不同观察时刻的数据。
优先使用可固定的数据快照/仓库分区或业务截止时间，并把snapshot_id/as_of写入任务结果。
若采用PostgreSQL事务快照，应限制事务持有时间，不在等待长时间LLM推理时无限占用事务。
审核返工使用新快照时，需要重查相关依赖结果或明确标注观察时刻，不能直接拼接成严格一致的总数。

## 阶段三：Schema变化与大目录检索

对标准化元数据、业务注释和权限版本计算hash。缓存键至少含：
source_id + schema_version + policy_version + embedding_model + 文档内容hash。
新增字段只重建相关文档；删除/重命名后旧文档和相关结果缓存失效。
会话绑定数据源，切换时显式创建新上下文；偏好按user/source/指标版本隔离。

表较多时先召回候选表，再在候选中取必要字段；用受认可的关联图补足连接键和桥接表。
规划者和审核者也应按需读取候选目录，不继续把所有数据库的全量Schema拼入每次Prompt。
对于日期、分组、过滤和粒度约束，建立结构化需求检查，避免仅依赖Top-K导致条件被丢弃。
保留检索模式开关，通过新开发集决定是否启用重排，而不是默认所有库都使用相同组合。

## 跨Schema验证包

建议按输入包组织：manifest.json、db/连接引用、schema语义配置、dev/test案例、参考SQL与评分规则。
连接引用只指向本地凭据配置，不保存密码。运行结果记录数据源、Schema和语义版本。

测试至少包括：

- 原电商库：防止改造造成退化。
- 改名/拆表库：字段从paid_amount改名为net_receipt，订单与支付拆开，验证真实schema linking与JOIN。
- 新领域库：采购/库存或招聘，不沿用电商Skill假设。
- 干扰与漂移库：增加无关表、同名字段、移除字段或改变类型，验证索引失效与澄清行为。
- 权限变体：相同问题在不同可见范围内，不泄露被禁止元数据或记录。

每个数据包分别比较single/multi，不能把在同一Schema上换记录的实验称为跨Schema。
复用本轮事实覆盖、严格完成、延迟与成本指标，增加表/字段/关联路径覆盖率与索引刷新耗时。
新测试包保持独立，不能针对每道测试题手写别名后仍宣称零样本泛化。

## 推荐实施顺序

1. 接口化现有DuckDB与SchemaCatalog，保证老测试全部通过。
2. PostgreSQL元数据发现、只读执行与最小数据源选择API。
3. 命名空间、权限、缓存失效和会话隔离。
4. 两个新Schema数据包与业务语义配置。
5. 在跨Schema任务上重新比较single/multi，再考虑并行子任务和跨库聚合。

面试演示目标：现场接入第二套库，采集Schema并补充可审阅的业务口径，不修改Agent代码完成问答；
再修改一列或切换用户范围，展示索引版本与执行边界如何生效。
