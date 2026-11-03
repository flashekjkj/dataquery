# 数据源接入与治理

当前使用说明统一见 [GOVERNANCE.md](GOVERNANCE.md)，包含数据源切换、权限、语义覆盖、快照、缓存刷新和离线评测。

- 默认使用电商DuckDB；外部DuckDB/PG使用服务端DATA_SOURCE_FILE配置。
- 新数据源必须显式提供Policy；无Policy默认拒绝，电商演示源有内置只读授权。
- BaseConnector提供连通、元数据、采样、只读执行和query_session。
- Registry返回SecuredConnector，统一实施表/列与合法JOIN校验；RawConnector只供底层基础设施与测试使用。
- QueryContext包含授权Schema快照、Policy和语义版本，经检索、规划、审核、SQL生成和修复注入。
- Skill通过业务指标绑定当前Schema；命中指标缺失时不加载该Skill。
- DuckDB和PostgreSQL均支持独立查询、一致快照两种模式；PG支持同库跨schema。
- 配置示例位于backend/datasource.example.json、policy.example.json、semantics.example.json。
- Schema/Policy/语义变更后需要刷新并重建Agent；HTTP/CLI当前通过重启完成。

生产仍需专用只读账号与身份认证；当前不实现行级策略或跨库联邦JOIN。
