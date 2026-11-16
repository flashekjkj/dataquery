# AskData 代码与输出文件导览

本导览按当前工作区整理，供面试复习和按文件定位实现使用。路径从项目根目录起算。每个文件条目后说明职责，源码链接可直接打开。

同构目录选取一个代表展开，其余目录逐一说明差异。会话、日志、Trace 和逐题模型调用记录采用实际存在的代表文件说明。依赖安装目录、编译缓存和编辑器内部文件按生成目录说明。

历史图示、README 和旧评测反映生成时的实现。当前执行逻辑以源码为准，最新检索评测以 evaluation/retrieval-v2 为准。

## 目录

- [根目录配置与文档](#root)
- [启动与接口](#entry)
- [Agent 编排与协作](#agent)
- [模型与应用配置](#app)
- [数据源与治理](#source)
- [检索与上下文](#retrieval)
- [SQL 生成与执行](#sql)
- [记忆与技能](#memory)
- [MCP 工具](#mcp)
- [可观测性](#obs)
- [演示数据与运行时输出](#data)
- [前端](#front)
- [测试文件](#tests)
- [评测程序](#evalcode)
- [首版业务评测产物](#evalv1)
- [标准化评测包](#packages)
- [单多智能体对比产物](#comparison)
- [最新检索评测产物](#retrievalv2)
- [生成目录与复习路线](#generated)

<a id="root"></a>
## 根目录配置与文档

- [README.md](<E:/desktop/LLM_Project/askdata/README.md>)：项目总览、启动方法和架构介绍，部分模式选择、节点数及本地重排描述仍属于旧版本。
- [MULTI_AGENT.md](<E:/desktop/LLM_Project/askdata/MULTI_AGENT.md>)：多智能体协作的设计、使用和验证说明，结合 supervisor.py 核对当前自动路由行为。
- [DOCKER.md](<E:/desktop/LLM_Project/askdata/DOCKER.md>)：Docker 部署、环境变量、数据卷和验收操作说明。
- [COMMAND.md](<E:/desktop/LLM_Project/askdata/COMMAND.md>)：本地常用命令笔记，包含依赖锁定、前后端运行与 Docker 操作。
- [DEV_LOG.md](<E:/desktop/LLM_Project/askdata/DEV_LOG.md>)：按阶段记录开发过程和优化事项，适合了解演进背景。
- [EVALUATION_PLAN.md](<E:/desktop/LLM_Project/askdata/EVALUATION_PLAN.md>)：早期代码审查和评测准备计划，具体结果需继续查阅各版本报告。
- [compose.yaml](<E:/desktop/LLM_Project/askdata/compose.yaml>)：定义前后端容器、端口、环境和持久化卷的组合部署配置。
- [.env.docker.example](<E:/desktop/LLM_Project/askdata/.env.docker.example>)：容器环境变量模板，用于配置模型服务及部署参数。
- [.gitignore](<E:/desktop/LLM_Project/askdata/.gitignore>)：规定凭据、缓存、日志、会话、依赖和构建目录的 Git 忽略规则。
- [.dockerignore](<E:/desktop/LLM_Project/askdata/.dockerignore>)：控制 Docker 构建上下文，减少无关内容和本地状态进入镜像。
- [.env.docker](<E:/desktop/LLM_Project/askdata/.env.docker>)：本机容器运行配置，可能包含凭据，复习时理解变量用途即可。

<a id="entry"></a>
## 启动与接口

- [backend/main.py](<E:/desktop/LLM_Project/askdata/backend/main.py>)：CLI 入口，构建 Agent 并循环接收问题，支持新会话、历史和技能命令。
- [backend/server/main.py](<E:/desktop/LLM_Project/askdata/backend/server/main.py>)：FastAPI 应用入口，通过 lifespan 初始化与清理 Agent，注册路由、中间件及指标接口。
- [backend/server/routes.py](<E:/desktop/LLM_Project/askdata/backend/server/routes.py>)：实现健康检查、普通问答、SSE 进度、会话和技能相关 HTTP 接口，处理结果序列化和错误响应。
- [backend/server/schemas.py](<E:/desktop/LLM_Project/askdata/backend/server/schemas.py>)：定义 HTTP 请求及响应的 Pydantic 模型，约束接口输入和返回格式。
- [backend/Dockerfile](<E:/desktop/LLM_Project/askdata/backend/Dockerfile>)：构建后端容器并配置服务启动方式。
- [backend/requirements.in](<E:/desktop/LLM_Project/askdata/backend/requirements.in>)：维护直接依赖清单，是更新依赖时的上游输入。
- [backend/requirements.txt](<E:/desktop/LLM_Project/askdata/backend/requirements.txt>)：锁定完整依赖版本，供本地环境安装和复现。
- [backend/requirements-docker.txt](<E:/desktop/LLM_Project/askdata/backend/requirements-docker.txt>)：补充容器运行所需依赖。
- [backend/.env.example](<E:/desktop/LLM_Project/askdata/backend/.env.example>)：模型、失败治理、重排、数据源和可观测性配置模板。
- [backend/.env](<E:/desktop/LLM_Project/askdata/backend/.env>)：本机后端实际配置，包含模型密钥等敏感信息，由 app/config.py 加载。

<a id="agent"></a>
## Agent 编排与协作

- [backend/agent/runtime.py](<E:/desktop/LLM_Project/askdata/backend/agent/runtime.py>)：组装连接器、检索、记忆、技能和状态图；初始化每轮输入，执行 Agent 并整理问答结果。
- [backend/agent/state.py](<E:/desktop/LLM_Project/askdata/backend/agent/state.py>)：定义 AskDataState，共享问题、字段上下文、SQL、结果、记忆、任务计划和审核状态。
- [backend/agent/graph.py](<E:/desktop/LLM_Project/askdata/backend/agent/graph.py>)：注册节点和条件边，编排单路、工具及多角色流程，限制修复和升级，并包装查询会话。
- [backend/agent/nodes.py](<E:/desktop/LLM_Project/askdata/backend/agent/nodes.py>)：实现记忆加载、改写、技能匹配、路由、检索、SQL 生成执行修复、工具调用和回答等节点。
- [backend/agent/query_context.py](<E:/desktop/LLM_Project/askdata/backend/agent/query_context.py>)：准备 SQL 生成所需字段、关联和样例值，同时提供路由、回答及记忆触发等公共逻辑。
- [backend/agent/supervisor.py](<E:/desktop/LLM_Project/askdata/backend/agent/supervisor.py>)：判断协作模式，规划并串行执行有依赖的子任务，组织审核、修订和汇总。
- [backend/agent/contracts.py](<E:/desktop/LLM_Project/askdata/backend/agent/contracts.py>)：定义 TaskPlan、QueryTask、ReviewDecision 等结构化契约，检查任务依赖及模型返回数据。
- [backend/agent/policies.py](<E:/desktop/LLM_Project/askdata/backend/agent/policies.py>)：集中判断 SQL 是否仍可修复，依据成功状态、错误类型和尝试预算控制循环。
- [backend/agent/outcomes.py](<E:/desktop/LLM_Project/askdata/backend/agent/outcomes.py>)：把图状态归类为成功、失败、澄清或降级等业务结果，供接口和指标共用。
- [backend/agent/prompts.py](<E:/desktop/LLM_Project/askdata/backend/agent/prompts.py>)：保存任务规划和多任务结果汇总所用 Prompt。
- [backend/agent/specialists/query_agent.py](<E:/desktop/LLM_Project/askdata/backend/agent/specialists/query_agent.py>)：构建查询子图，复用检索、生成、执行和修复节点完成单个子任务。
- [backend/agent/specialists/review_agent.py](<E:/desktop/LLM_Project/askdata/backend/agent/specialists/review_agent.py>)：调用审核模型检查子任务证据与结果，返回结构化审核决定。
- [backend/agent_graph.mmd](<E:/desktop/LLM_Project/askdata/backend/agent_graph.mmd>)：导出的 Mermaid 图源文件，可用于渲染状态图。
- [backend/agent_graph.md](<E:/desktop/LLM_Project/askdata/backend/agent_graph.md>)：包含导出状态图和预览说明的 Markdown 文件。
- [backend/agent_graph.jpg](<E:/desktop/LLM_Project/askdata/backend/agent_graph.jpg>)：状态图图片产物，用于快速浏览流程，可能滞后于后续源码修改。

<a id="app"></a>
## 模型与应用配置

- [backend/app/config.py](<E:/desktop/LLM_Project/askdata/backend/app/config.py>)：通过 Pydantic Settings 加载环境变量，定义模型、重试、重排、检索策略和数据源配置。
- [backend/app/model_client.py](<E:/desktop/LLM_Project/askdata/backend/app/model_client.py>)：统一聊天、Embedding 和重排调用，支持 mock、流式输出、错误分类、重试、结构化输出兼容及调用计量。
- [backend/app/schemas.py](<E:/desktop/LLM_Project/askdata/backend/app/schemas.py>)：定义 LLMResponse、EmbeddingResponse、RerankItem 和 RerankResponse 等模型接口数据结构。
- [backend/app/clock.py](<E:/desktop/LLM_Project/askdata/backend/app/clock.py>)：提供业务时间和可覆盖的评测时间，使相对日期查询可以复现。

<a id="source"></a>
## 数据源与治理

- [backend/datasources/models.py](<E:/desktop/LLM_Project/askdata/backend/datasources/models.py>)：定义 DataSourceConfig、ColumnSchema、TableSchema、SchemaSnapshot 和 QueryContext，并计算元数据内容版本。
- [backend/datasources/base.py](<E:/desktop/LLM_Project/askdata/backend/datasources/base.py>)：声明连接器抽象接口，提供限定表名、标识符引用、字段角色和确定性采样 SQL 等公共行为。
- [backend/datasources/config.py](<E:/desktop/LLM_Project/askdata/backend/datasources/config.py>)：加载服务端数据源配置，解析相对路径，并提供默认演示源配置。
- [backend/datasources/registry.py](<E:/desktop/LLM_Project/askdata/backend/datasources/registry.py>)：按方言创建并注册连接器，以 SecuredConnector 包装，提供上下文、刷新和统一关闭。
- [backend/datasources/duckdb.py](<E:/desktop/LLM_Project/askdata/backend/datasources/duckdb.py>)：实现 DuckDB 元数据探查、演示目录融合、采样、只读查询以及一致快照会话。
- [backend/datasources/postgres.py](<E:/desktop/LLM_Project/askdata/backend/datasources/postgres.py>)：用 SQLAlchemy 反射 PostgreSQL 多 Schema 元数据，实现只读连接池、超时、事务回滚和一致快照。
- [backend/datasources/policy.py](<E:/desktop/LLM_Project/askdata/backend/datasources/policy.py>)：解析并校验 SQL 的表列、函数及 JOIN 访问，执行标识符限定、星号展开和保守授权检查。
- [backend/datasources/semantics.py](<E:/desktop/LLM_Project/askdata/backend/datasources/semantics.py>)：定义指标、合法 JOIN、关联基数和时间字段，加载覆盖配置并检查引用是否合法。
- [backend/datasources/secured.py](<E:/desktop/LLM_Project/askdata/backend/datasources/secured.py>)：把权限、语义、元数据和执行包装为统一数据源视图，管理版本刷新与活动查询会话。
- [backend/datasources/demo.py](<E:/desktop/LLM_Project/askdata/backend/datasources/demo.py>)：生成电商演示字段目录和兼容 QueryContext，提供中文名称、别名及字段角色。
- [backend/datasources/README.md](<E:/desktop/LLM_Project/askdata/backend/datasources/README.md>)：数据源模块入口说明和治理文档导航。
- [backend/datasources/GOVERNANCE.md](<E:/desktop/LLM_Project/askdata/backend/datasources/GOVERNANCE.md>)：说明权限、语义配置、快照、刷新、数据源切换与测试方法及支持边界。
- [backend/datasource.example.json](<E:/desktop/LLM_Project/askdata/backend/datasource.example.json>)：数据源连接与运行方式配置样例。
- [backend/policy.example.json](<E:/desktop/LLM_Project/askdata/backend/policy.example.json>)：表列访问控制配置样例。
- [backend/semantics.example.json](<E:/desktop/LLM_Project/askdata/backend/semantics.example.json>)：指标、合法 JOIN 与时间字段配置样例。

<a id="retrieval"></a>
## 检索与上下文

- [backend/retrieval/store.py](<E:/desktop/LLM_Project/askdata/backend/retrieval/store.py>)：将每个可用数据库字段转换成 FieldDocument，分别生成语义检索文本与关键词文本。
- [backend/retrieval/service.py](<E:/desktop/LLM_Project/askdata/backend/retrieval/service.py>)：构建字段及表向量索引，筛选候选表，在范围内召回并融合字段，记录阶段诊断并裁剪规划元数据。
- [backend/retrieval/ranker.py](<E:/desktop/LLM_Project/askdata/backend/retrieval/ranker.py>)：实现中文和标识符分词、自定义 BM25、加权 RRF、候选重排及基础 HybridRetriever。
- [backend/retrieval/config.py](<E:/desktop/LLM_Project/askdata/backend/retrieval/config.py>)：统一定义策略、召回数、RRF 参数、重排数量、表数量和字段总预算。
- [backend/retrieval/context.py](<E:/desktop/LLM_Project/askdata/backend/retrieval/context.py>)：在语义召回基础上补齐时间字段和合法 JOIN 的完整键组，控制总预算并记录未补齐的关联。
- [backend/retrieval/cache.py](<E:/desktop/LLM_Project/askdata/backend/retrieval/cache.py>)：使用 SQLite 保存字段与表向量，通过命名空间和内容 hash 复用、更新与清理缓存。

<a id="sql"></a>
## SQL 生成与执行

- [backend/sql/generator.py](<E:/desktop/LLM_Project/askdata/backend/sql/generator.py>)：格式化字段、语义、关联和样例值，生成与修复 SQL，解析 JSON 或文本返回，提供带执行修复的辅助流程。
- [backend/sql/sql_prompts.py](<E:/desktop/LLM_Project/askdata/backend/sql/sql_prompts.py>)：维护 SQL 生成、修复、路由、回答及相关模型任务的 Prompt 模板。
- [backend/sql/executor.py](<E:/desktop/LLM_Project/askdata/backend/sql/executor.py>)：执行受限只读查询，检查语句安全、禁止外部读取、限制行数与耗时，并定义 QueryResult 和执行异常。

<a id="memory"></a>
## 记忆与技能

- [backend/memory/conversation.py](<E:/desktop/LLM_Project/askdata/backend/memory/conversation.py>)：以会话文件保存问答记录，生成近期上下文、滚动摘要，提供会话列表与删除操作。
- [backend/memory/long_term.py](<E:/desktop/LLM_Project/askdata/backend/memory/long_term.py>)：加载、合并和保存用户长期偏好，供后续查询注入。
- [backend/skill/registry.py](<E:/desktop/LLM_Project/askdata/backend/skill/registry.py>)：加载技能配置，根据关键词匹配技能，结合当前指标可用性派生表范围并过滤不可用技能。
- [backend/data/skills/sales_analysis/skill.json](<E:/desktop/LLM_Project/askdata/backend/data/skills/sales_analysis/skill.json>)：销售分析技能的声明，包含业务指标、匹配规则、专属指令和工具需求等配置。
- [backend/data/skills/customer_profile/skill.json](<E:/desktop/LLM_Project/askdata/backend/data/skills/customer_profile/skill.json>)：客户画像技能声明，与销售技能采用相同配置形式，切换业务指标和分析要求。

<a id="mcp"></a>
## MCP 工具

- [backend/mcp_client/manager.py](<E:/desktop/LLM_Project/askdata/backend/mcp_client/manager.py>)：管理外部 MCP 服务进程和连接，发现、调用工具，并转换为模型工具调用格式。
- [backend/mcp_client/demo_servers/calculator_server.py](<E:/desktop/LLM_Project/askdata/backend/mcp_client/demo_servers/calculator_server.py>)：示例计算器 MCP 服务，供 Agent 验证外部工具调用链路。
- [backend/mcp_server/server.py](<E:/desktop/LLM_Project/askdata/backend/mcp_server/server.py>)：将本项目数据源探查和只读查询能力封装为 MCP 工具，对外提供目录及查库接口。
- [backend/mcp_server/run_server.py](<E:/desktop/LLM_Project/askdata/backend/mcp_server/run_server.py>)：独立 MCP Server 启动入口。

<a id="obs"></a>
## 可观测性

- [backend/observability/__init__.py](<E:/desktop/LLM_Project/askdata/backend/observability/__init__.py>)：导出日志初始化、logger 以及请求标识和 Token 上下文访问接口。
- [backend/observability/context.py](<E:/desktop/LLM_Project/askdata/backend/observability/context.py>)：用 ContextVar 保存请求 ID、会话 ID 和 Token 累计数据，隔离并发请求。
- [backend/observability/logging_setup.py](<E:/desktop/LLM_Project/askdata/backend/observability/logging_setup.py>)：配置 loguru 控制台与 JSONL 日志，并附加请求及会话标识。
- [backend/observability/metrics.py](<E:/desktop/LLM_Project/askdata/backend/observability/metrics.py>)：定义 Prometheus 请求、结果、节点耗时、模型调用、Token、重试和重排降级等指标。
- [backend/observability/middleware.py](<E:/desktop/LLM_Project/askdata/backend/observability/middleware.py>)：建立 HTTP 请求上下文，关联请求 ID，统计状态和包含流式响应的请求耗时。
- [backend/observability/tracer.py](<E:/desktop/LLM_Project/askdata/backend/observability/tracer.py>)：管理请求 Trace、span、JSONL 落盘和 LangGraph 回调，记录父子图节点及模型工具执行。

<a id="data"></a>
## 演示数据与运行时输出

- [backend/data/database.py](<E:/desktop/LLM_Project/askdata/backend/data/database.py>)：兼容导出演示目录加载函数，实际元数据定义在 datasources/demo.py。
- [backend/data/seed_demo_data.py](<E:/desktop/LLM_Project/askdata/backend/data/seed_demo_data.py>)：创建和填充本地电商演示数据库的初始化脚本。
- [backend/data/view_db.py](<E:/desktop/LLM_Project/askdata/backend/data/view_db.py>)：直接读取本地演示表并打印样例行，用于人工检查数据。
- [backend/data/demo_ecom.duckdb](<E:/desktop/LLM_Project/askdata/backend/data/demo_ecom.duckdb>)：应用使用的本地演示数据库，与 evaluation 下固定评测夹具分别管理。
- [backend/data/cache/schema_vectors.sqlite](<E:/desktop/LLM_Project/askdata/backend/data/cache/schema_vectors.sqlite>)：字段和表 Embedding 的本地持久缓存，由 retrieval/cache.py 管理。
- [backend/data/logs/app.jsonl](<E:/desktop/LLM_Project/askdata/backend/data/logs/app.jsonl>)：结构化运行日志，记录请求标识、错误与执行过程，属于本机生成状态。
- [backend/data/conversations/9fa807385ba34c03b77f731105b2c0e1.json](<E:/desktop/LLM_Project/askdata/backend/data/conversations/9fa807385ba34c03b77f731105b2c0e1.json>)：旧布局的会话记录样例，保存该会话的历史轮次和摘要。 同目录其他会话或日期文件形式一致。
- [backend/data/traces/traces-20261103.jsonl](<E:/desktop/LLM_Project/askdata/backend/data/traces/traces-20261103.jsonl>)：请求 Trace 日志样例，记录各 span 的名称、耗时和元数据。 同目录其他会话或日期文件形式一致。
- [backend/data/sources/demo/ce0a367a42a8aa9b52aa30d0689c2e5dae380641aec2bf0de93ac57aaecce285/conversations/19de5bd50abb4c698c1bee22759dfcd1.json](<E:/desktop/LLM_Project/askdata/backend/data/sources/demo/ce0a367a42a8aa9b52aa30d0689c2e5dae380641aec2bf0de93ac57aaecce285/conversations/19de5bd50abb4c698c1bee22759dfcd1.json>)：按数据源及版本隔离后的会话输出样例，对应当前运行时的记忆隔离目录。 同目录其他会话或日期文件形式一致。

`backend/data/sources/` 的实际路径包含数据源和隔离版本 hash。会话文件由 ConversationMemory 管理，长期偏好文件由 LongTermMemoryStore 按需创建。

<a id="front"></a>
## 前端

- [frontend/src/main.js](<E:/desktop/LLM_Project/askdata/frontend/src/main.js>)：创建 Vue 应用并挂载根组件，加载全局样式。
- [frontend/src/App.vue](<E:/desktop/LLM_Project/askdata/frontend/src/App.vue>)：组织会话列表与消息状态，发送问题、打开和删除会话，控制窄屏侧栏。
- [frontend/src/api/index.js](<E:/desktop/LLM_Project/askdata/frontend/src/api/index.js>)：封装 HTTP 请求、问答、会话、技能及 SSE 事件读取，统一处理接口错误。
- [frontend/src/components/ChatWindow.vue](<E:/desktop/LLM_Project/askdata/frontend/src/components/ChatWindow.vue>)：渲染当前对话、欢迎示例和输入框，处理发送、换行、输入法与滚动交互。
- [frontend/src/components/MessageBubble.vue](<E:/desktop/LLM_Project/askdata/frontend/src/components/MessageBubble.vue>)：展示用户和助手消息、结果表格、加载状态及折叠执行详情，支持 SQL 复制。
- [frontend/src/components/ResultTable.vue](<E:/desktop/LLM_Project/askdata/frontend/src/components/ResultTable.vue>)：把查询的列名和数据行渲染成表格，并展示结果数量及截断信息。
- [frontend/src/components/SessionList.vue](<E:/desktop/LLM_Project/askdata/frontend/src/components/SessionList.vue>)：展示会话列表、当前项、新建和删除操作，向父组件发出交互事件。
- [frontend/src/style.css](<E:/desktop/LLM_Project/askdata/frontend/src/style.css>)：定义全局字体、主题变量、基础布局和通用交互样式。
- [frontend/src/assets/hero.png](<E:/desktop/LLM_Project/askdata/frontend/src/assets/hero.png>)：保留的图片素材，是否展示取决于组件引用。
- [frontend/src/assets/vue.svg](<E:/desktop/LLM_Project/askdata/frontend/src/assets/vue.svg>)：Vue 模板保留图标素材。
- [frontend/src/assets/vite.svg](<E:/desktop/LLM_Project/askdata/frontend/src/assets/vite.svg>)：Vite 模板保留图标素材。
- [frontend/public/favicon.svg](<E:/desktop/LLM_Project/askdata/frontend/public/favicon.svg>)：网站浏览器标签页图标静态资源。
- [frontend/public/icons.svg](<E:/desktop/LLM_Project/askdata/frontend/public/icons.svg>)：站点可直接访问的 SVG 图标资源。
- [frontend/index.html](<E:/desktop/LLM_Project/askdata/frontend/index.html>)：Vite 页面模板，提供应用挂载点和脚本入口。
- [frontend/vite.config.js](<E:/desktop/LLM_Project/askdata/frontend/vite.config.js>)：配置 Vue 构建插件及开发代理等 Vite 行为。
- [frontend/package.json](<E:/desktop/LLM_Project/askdata/frontend/package.json>)：声明前端依赖和开发、构建、预览命令。
- [frontend/package-lock.json](<E:/desktop/LLM_Project/askdata/frontend/package-lock.json>)：锁定 npm 依赖树以支持一致安装。
- [frontend/nginx.conf](<E:/desktop/LLM_Project/askdata/frontend/nginx.conf>)：配置前端容器的静态资源服务、页面路由及后端代理。
- [frontend/Dockerfile](<E:/desktop/LLM_Project/askdata/frontend/Dockerfile>)：构建前端资源并生成 Nginx 运行镜像。
- [frontend/.gitignore](<E:/desktop/LLM_Project/askdata/frontend/.gitignore>)：前端依赖、构建和本地工具文件的忽略规则。
- [frontend/README.md](<E:/desktop/LLM_Project/askdata/frontend/README.md>)：保留的 Vue 与 Vite 模板使用说明。

<a id="tests"></a>
## 测试文件

测试文件有不同执行形式，部分是 unittest，部分是脚本或普通测试函数。带真实模型或已部署 HTTP 服务的演示测试可能调用付费 API，先检查文件入口及 mock 设置。

- [backend/test/manual_docker.py](<E:/desktop/LLM_Project/askdata/backend/test/manual_docker.py>)：对已部署 HTTP 服务进行人工冒烟验收，覆盖接口、会话及重启持久化检查，会调用配置的模型服务。
- [backend/test/manual_e2e.py](<E:/desktop/LLM_Project/askdata/backend/test/manual_e2e.py>)：使用真实 Agent 连续追问，人工观察多轮改写和会话连贯性。
- [backend/test/test_collaboration.py](<E:/desktop/LLM_Project/askdata/backend/test/test_collaboration.py>)：验证任务隔离、依赖、审核修订、自动路由、单路升级及有界失败行为。
- [backend/test/test_comparison.py](<E:/desktop/LLM_Project/askdata/backend/test/test_comparison.py>)：验证单多模式评分规则，包括部分事实、澄清、截断和不同结果表示。
- [backend/test/test_datasources.py](<E:/desktop/LLM_Project/askdata/backend/test/test_datasources.py>)：验证连接器、反射、采样、跨 Schema、只读执行和电商回放，包含可选真实 PG 集成测试。
- [backend/test/test_evaluation.py](<E:/desktop/LLM_Project/askdata/backend/test/test_evaluation.py>)：验证 SQL 安全与语义边界、时间字段补全、指标计量及结果等价评分。
- [backend/test/test_executor.py](<E:/desktop/LLM_Project/askdata/backend/test/test_executor.py>)：直接演示执行器的查询、行数限制和危险 SQL 拦截行为。
- [backend/test/test_generator.py](<E:/desktop/LLM_Project/askdata/backend/test/test_generator.py>)：演示 SQL 生成及带执行错误的修复辅助流程，需检查所用客户端是否为真实模式。
- [backend/test/test_graph.py](<E:/desktop/LLM_Project/askdata/backend/test/test_graph.py>)：提供 FakeClient，并验证图结构、成功、修复、耗尽、工具及会话隔离。
- [backend/test/test_mcp_server.py](<E:/desktop/LLM_Project/askdata/backend/test/test_mcp_server.py>)：验证本项目 MCP Server 的工具发现、查询及写入拦截。
- [backend/test/test_memory.py](<E:/desktop/LLM_Project/askdata/backend/test/test_memory.py>)：验证会话存取、摘要、长期偏好、多轮改写和每轮尝试日志重置。
- [backend/test/test_model_client.py](<E:/desktop/LLM_Project/askdata/backend/test/test_model_client.py>)：验证模型错误分类、重试、JSON 模式降级、流式调用和用量记录。
- [backend/test/test_observability.py](<E:/desktop/LLM_Project/askdata/backend/test/test_observability.py>)：验证 Trace 生命周期、落盘、回调、请求 ID 和异常响应。
- [backend/test/test_retrieval.py](<E:/desktop/LLM_Project/askdata/backend/test/test_retrieval.py>)：人工打印混合、BM25 和向量结果的检索演示脚本。
- [backend/test/test_retrieval_regressions.py](<E:/desktop/LLM_Project/askdata/backend/test/test_retrieval_regressions.py>)：定向检查零分过滤、标识符分词、统一候选、JOIN 补全、重排降级与 API 索引映射。
- [backend/test/test_runtime_lifecycle.py](<E:/desktop/LLM_Project/askdata/backend/test/test_runtime_lifecycle.py>)：验证异常时 Trace 释放、调用方 Trace 保留及统一修复策略。
- [backend/test/test_schema_index.py](<E:/desktop/LLM_Project/askdata/backend/test/test_schema_index.py>)：验证字段向量索引的构建和查询结果。
- [backend/test/test_schema_store.py](<E:/desktop/LLM_Project/askdata/backend/test/test_schema_store.py>)：验证字段文档生成及元数据到检索文本的转换。
- [backend/test/test_skill_registry.py](<E:/desktop/LLM_Project/askdata/backend/test/test_skill_registry.py>)：验证技能加载、匹配与按名称查找。
- [backend/test/test_source_governance.py](<E:/desktop/LLM_Project/askdata/backend/test/test_source_governance.py>)：验证权限、语义覆盖、缓存局部失效、Schema 漂移、候选表预算、快照及离线评测包。
- [backend/test/test_sql_json.py](<E:/desktop/LLM_Project/askdata/backend/test/test_sql_json.py>)：验证 JSON、Markdown SQL、纯文本、分号和无效内容的 SQL 解析。
- [backend/test/test_tool_manager.py](<E:/desktop/LLM_Project/askdata/backend/test/test_tool_manager.py>)：验证 MCP 工具列表、调用及模型接口格式转换。
- [backend/test/test.py](<E:/desktop/LLM_Project/askdata/backend/test/test.py>)：本地保留的 Transformer 模型学习代码，包含注意力、编码器和解码器等类，已被 Git 忽略。

### Python 包初始化文件

- [backend/agent/__init__.py](<E:/desktop/LLM_Project/askdata/backend/agent/__init__.py>)：定义该包对外导出的类型或接口，阅读时核对其 import 与 __all__。
- [backend/agent/specialists/__init__.py](<E:/desktop/LLM_Project/askdata/backend/agent/specialists/__init__.py>)：定义该包对外导出的类型或接口，阅读时核对其 import 与 __all__。
- [backend/app/__init__.py](<E:/desktop/LLM_Project/askdata/backend/app/__init__.py>)：定义该包对外导出的类型或接口，阅读时核对其 import 与 __all__。
- [backend/data/__init__.py](<E:/desktop/LLM_Project/askdata/backend/data/__init__.py>)：定义该包对外导出的类型或接口，阅读时核对其 import 与 __all__。
- [backend/datasources/__init__.py](<E:/desktop/LLM_Project/askdata/backend/datasources/__init__.py>)：定义该包对外导出的类型或接口，阅读时核对其 import 与 __all__。
- [backend/evaluation/__init__.py](<E:/desktop/LLM_Project/askdata/backend/evaluation/__init__.py>)：定义该包对外导出的类型或接口，阅读时核对其 import 与 __all__。
- [backend/mcp_client/__init__.py](<E:/desktop/LLM_Project/askdata/backend/mcp_client/__init__.py>)：定义该包对外导出的类型或接口，阅读时核对其 import 与 __all__。
- [backend/mcp_server/__init__.py](<E:/desktop/LLM_Project/askdata/backend/mcp_server/__init__.py>)：包初始化标记，当前没有业务执行逻辑。
- [backend/memory/__init__.py](<E:/desktop/LLM_Project/askdata/backend/memory/__init__.py>)：定义该包对外导出的类型或接口，阅读时核对其 import 与 __all__。
- [backend/retrieval/__init__.py](<E:/desktop/LLM_Project/askdata/backend/retrieval/__init__.py>)：定义该包对外导出的类型或接口，阅读时核对其 import 与 __all__。
- [backend/server/__init__.py](<E:/desktop/LLM_Project/askdata/backend/server/__init__.py>)：定义该包对外导出的类型或接口，阅读时核对其 import 与 __all__。
- [backend/skill/__init__.py](<E:/desktop/LLM_Project/askdata/backend/skill/__init__.py>)：包初始化标记，当前没有业务执行逻辑。
- [backend/sql/__init__.py](<E:/desktop/LLM_Project/askdata/backend/sql/__init__.py>)：定义该包对外导出的类型或接口，阅读时核对其 import 与 __all__。
- [backend/test/__init__.py](<E:/desktop/LLM_Project/askdata/backend/test/__init__.py>)：定义该包对外导出的类型或接口，阅读时核对其 import 与 __all__。

<a id="evalcode"></a>
## 评测程序

- [backend/evaluation/prepare.py](<E:/desktop/LLM_Project/askdata/backend/evaluation/prepare.py>)：生成首版确定性电商夹具、开发和留出标注，并执行参考 SQL 得到预期结果。
- [backend/evaluation/run.py](<E:/desktop/LLM_Project/askdata/backend/evaluation/run.py>)：运行真实模型业务评测，保存逐题预测、调用和汇总，支持冻结及结果等价评分。
- [backend/evaluation/check.py](<E:/desktop/LLM_Project/askdata/backend/evaluation/check.py>)：对固定评测案例进行离线 SQL 语义检查，生成修复前后的诊断记录。
- [backend/evaluation/report.py](<E:/desktop/LLM_Project/askdata/backend/evaluation/report.py>)：读取业务评测运行结果，汇总正确率、时延、Token 与失败信息并生成报告。
- [backend/evaluation/comparison_data.py](<E:/desktop/LLM_Project/askdata/backend/evaluation/comparison_data.py>)：生成单多智能体对比使用的两套夹具及成组题目。
- [backend/evaluation/comparison.py](<E:/desktop/LLM_Project/askdata/backend/evaluation/comparison.py>)：执行单多模式配对比较，设置运行预算，收集事实证据并计算对比指标。
- [backend/evaluation/package_fixtures.py](<E:/desktop/LLM_Project/askdata/backend/evaluation/package_fixtures.py>)：构造电商、改名拆表、新领域、Schema 漂移和权限变体的标准包输入。
- [backend/evaluation/packages.py](<E:/desktop/LLM_Project/askdata/backend/evaluation/packages.py>)：加载本地标准包及预测 SQL，校验权限行为、执行结果和表关联路径，支持明确标记的参考回放。
- [backend/evaluation/retrieval_data.py](<E:/desktop/LLM_Project/askdata/backend/evaluation/retrieval_data.py>)：生成检索 v2 的两个目录、固定夹具及开发和留出题，预先指定 Agent 配对验收子集。
- [backend/evaluation/retrieval_benchmark.py](<E:/desktop/LLM_Project/askdata/backend/evaluation/retrieval_benchmark.py>)：执行检索消融、开发选型、冻结校验、留出比较和真实 Agent 验收，支持断点续跑及夹具修订审计。
- [evaluation/finish_report.py](<E:/desktop/LLM_Project/askdata/evaluation/finish_report.py>)：汇总首版已完成运行、冻结与审计记录，生成历史正式报告。
- [evaluation/retrieval_diagnostic.py](<E:/desktop/LLM_Project/askdata/evaluation/retrieval_diagnostic.py>)：旧开发集检索诊断入口，只允许本地重排，当前输出另存 retrieval-diagnostic-current.json。
- [evaluation/single-multi/report_comparison.py](<E:/desktop/LLM_Project/askdata/evaluation/single-multi/report_comparison.py>)：读取单多智能体配对结果，生成对比统计和报告。
- [evaluation/single-multi/audit_reviews.py](<E:/desktop/LLM_Project/askdata/evaluation/single-multi/audit_reviews.py>)：只读重放历史 SQL，检查审核前后事实证据的变化。
- [evaluation/single-multi/test_reporting.py](<E:/desktop/LLM_Project/askdata/evaluation/single-multi/test_reporting.py>)：验证单多评测报告的统计、过滤和呈现逻辑。

<a id="evalv1"></a>
## 首版业务评测产物

- [evaluation/README.md](<E:/desktop/LLM_Project/askdata/evaluation/README.md>)：首版评测协议、命令和交付说明，并导航到检索 v2。
- [evaluation/CHANGELOG.md](<E:/desktop/LLM_Project/askdata/evaluation/CHANGELOG.md>)：记录首版评测前后的改造和验证变化。
- [evaluation/labels.md](<E:/desktop/LLM_Project/askdata/evaluation/labels.md>)：解释题目标签、预期动作和评分口径。
- [evaluation/fixture.duckdb](<E:/desktop/LLM_Project/askdata/evaluation/fixture.duckdb>)：首版业务评测的固定电商数据库，隔离于应用演示库。
- [evaluation/datasets/dev.jsonl](<E:/desktop/LLM_Project/askdata/evaluation/datasets/dev.jsonl>)：用于排错和优化的开发题，每行包含问题与参考标注。
- [evaluation/datasets/holdout.jsonl](<E:/desktop/LLM_Project/askdata/evaluation/datasets/holdout.jsonl>)：首版冻结后的留出题，现已运行，后续可作为回归数据。
- [evaluation/datasets/smoke.jsonl](<E:/desktop/LLM_Project/askdata/evaluation/datasets/smoke.jsonl>)：快速冒烟题集，取自开发集。
- [evaluation/report.md](<E:/desktop/LLM_Project/askdata/evaluation/report.md>)：首版业务正式评测报告，属于历史版本成绩。
- [evaluation/retrieval-diagnostic.json](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-diagnostic.json>)：旧24题检索组件对比结果，保留当时召回配置下的指标和字段记录。
- [evaluation/semantic_before.json](<E:/desktop/LLM_Project/askdata/evaluation/semantic_before.json>)：定向 SQL 语义检查的修复前结果。
- [evaluation/semantic_after.json](<E:/desktop/LLM_Project/askdata/evaluation/semantic_after.json>)：同类定向语义检查的修复后结果。
- [evaluation/offline-tests.json](<E:/desktop/LLM_Project/askdata/evaluation/offline-tests.json>)：首版离线测试验收摘要。
- [evaluation/artifact-hashes.json](<E:/desktop/LLM_Project/askdata/evaluation/artifact-hashes.json>)：首版评测相关产物的完整性 hash 记录。
- [evaluation/development-confirmation-note.json](<E:/desktop/LLM_Project/askdata/evaluation/development-confirmation-note.json>)：开发集修复确认运行的范围、未完成请求及解释。
- [evaluation/frozen/askdata-eval-v1/manifest.json](<E:/desktop/LLM_Project/askdata/evaluation/frozen/askdata-eval-v1/manifest.json>)：首版冻结的源码、配置、数据和依赖版本清单。
- [evaluation/frozen/askdata-eval-v1/snapshot.zip](<E:/desktop/LLM_Project/askdata/evaluation/frozen/askdata-eval-v1/snapshot.zip>)：首版被测源码、题目和夹具快照，用于恢复历史实现。

### 重复运行目录的代表

- [evaluation/runs/holdout-v1/manifest.json](<E:/desktop/LLM_Project/askdata/evaluation/runs/holdout-v1/manifest.json>)：本次首版留出运行的源码、模型、数据集与运行配置。
- [evaluation/runs/holdout-v1/predictions.jsonl](<E:/desktop/LLM_Project/askdata/evaluation/runs/holdout-v1/predictions.jsonl>)：逐题完整输出、SQL、调用、耗时和评分记录。
- [evaluation/runs/holdout-v1/summary.json](<E:/desktop/LLM_Project/askdata/evaluation/runs/holdout-v1/summary.json>)：汇总本次运行的正确率、时延、调用与用量等指标。

同构运行目录对应关系如下。

- `evaluation/runs/before-fixes/`：改造前基线，同样包含 manifest.json、predictions.jsonl、summary.json。
- `evaluation/runs/development-v1/`：开发集首轮运行，采用上述三个同名文件。
- `evaluation/runs/development-confirmation/`：修复确认运行，实际保留 manifest.json 和 predictions.jsonl，范围限制见 development-confirmation-note.json。

<a id="packages"></a>
## 标准化评测包

- [evaluation/packages/README.md](<E:/desktop/LLM_Project/askdata/evaluation/packages/README.md>)：标准评测包格式、运行命令和参考回放的适用边界说明。
- [evaluation/packages/reference-replay.json](<E:/desktop/LLM_Project/askdata/evaluation/packages/reference-replay.json>)：全部标准包的参考 SQL 回放结果，用于验证夹具与评分框架。

以招聘新领域包逐个展开文件，其余包复用这一组织形式。

- [evaluation/packages/new_domain/manifest.json](<E:/desktop/LLM_Project/askdata/evaluation/packages/new_domain/manifest.json>)：声明包名称、类别及夹具、Policy、语义和案例文件的位置。
- [evaluation/packages/new_domain/init.sql](<E:/desktop/LLM_Project/askdata/evaluation/packages/new_domain/init.sql>)：创建招聘岗位和申请表，并填充确定性样例数据。
- [evaluation/packages/new_domain/cases.jsonl](<E:/desktop/LLM_Project/askdata/evaluation/packages/new_domain/cases.jsonl>)：保存新领域问题、参考 SQL、预期表及关联路径等标注。
- [evaluation/packages/new_domain/policy.json](<E:/desktop/LLM_Project/askdata/evaluation/packages/new_domain/policy.json>)：定义该包允许访问的表列。
- [evaluation/packages/new_domain/semantics.json](<E:/desktop/LLM_Project/askdata/evaluation/packages/new_domain/semantics.json>)：定义 offer 指标的过滤口径及申请到岗位的合法 JOIN。
- [evaluation/packages/new_domain/predictions.example.json](<E:/desktop/LLM_Project/askdata/evaluation/packages/new_domain/predictions.example.json>)：手工预测 SQL 输入示例，展示评测器接受的提交格式。

其余包逐一对应如下。

- `evaluation/packages/ecommerce/`：电商基准，包含 manifest.json、cases.jsonl、policy.json、semantics.json；夹具文件为 fixture.duckdb。
- `evaluation/packages/renamed_split/`：字段改名与拆表测试，包含 manifest.json、init.sql、cases.jsonl、policy.json、semantics.json。
- `evaluation/packages/schema_drift/`：Schema 漂移及旧上下文失效测试，包含 manifest.json、init.sql、cases.jsonl、policy.json、semantics.json。
- `evaluation/packages/permissions/`：权限允许与拒绝变体测试，包含 manifest.json、init.sql、cases.jsonl、policy.json、semantics.json。

<a id="comparison"></a>
## 单多智能体对比产物

- [evaluation/single-multi/PROTOCOL.md](<E:/desktop/LLM_Project/askdata/evaluation/single-multi/PROTOCOL.md>)：单路与协作配对评测的运行预算、任务分组和判分规则。
- [evaluation/single-multi/STATUS.md](<E:/desktop/LLM_Project/askdata/evaluation/single-multi/STATUS.md>)：记录历史运行、中断及恢复状态。
- [evaluation/single-multi/DATASOURCE_PLAN.md](<E:/desktop/LLM_Project/askdata/evaluation/single-multi/DATASOURCE_PLAN.md>)：当时提出的数据源改造计划，当前实现与操作以 datasources/GOVERNANCE.md 为准。
- [evaluation/single-multi/cases.jsonl](<E:/desktop/LLM_Project/askdata/evaluation/single-multi/cases.jsonl>)：两套夹具上的单多模式对比题目及预期结果。
- [evaluation/single-multi/fixture-A.duckdb](<E:/desktop/LLM_Project/askdata/evaluation/single-multi/fixture-A.duckdb>)：对比实验的 A 组固定数据库。
- [evaluation/single-multi/fixture-B.duckdb](<E:/desktop/LLM_Project/askdata/evaluation/single-multi/fixture-B.duckdb>)：对比实验的 B 组固定数据库。
- [evaluation/single-multi/report.md](<E:/desktop/LLM_Project/askdata/evaluation/single-multi/report.md>)：单多智能体比较的正式结果和限制说明。
- [evaluation/single-multi/clarification-review.json](<E:/desktop/LLM_Project/askdata/evaluation/single-multi/clarification-review.json>)：澄清问题回答的审阅记录。
- [evaluation/single-multi/service-audit.json](<E:/desktop/LLM_Project/askdata/evaluation/single-multi/service-audit.json>)：模型服务调用与异常相关的审计记录。
- [evaluation/single-multi/runs/paired-recovered-v1/manifest.json](<E:/desktop/LLM_Project/askdata/evaluation/single-multi/runs/paired-recovered-v1/manifest.json>)：恢复后配对运行的版本、模型、数据和预算清单。
- [evaluation/single-multi/runs/paired-recovered-v1/manifest-before-resume.json](<E:/desktop/LLM_Project/askdata/evaluation/single-multi/runs/paired-recovered-v1/manifest-before-resume.json>)：恢复前的清单副本，支持核对运行配置变化。
- [evaluation/single-multi/runs/paired-recovered-v1/predictions.jsonl](<E:/desktop/LLM_Project/askdata/evaluation/single-multi/runs/paired-recovered-v1/predictions.jsonl>)：逐题、逐模式的输出、SQL、任务结果和评分原始记录。
- [evaluation/single-multi/runs/paired-recovered-v1/resume-provenance.json](<E:/desktop/LLM_Project/askdata/evaluation/single-multi/runs/paired-recovered-v1/resume-provenance.json>)：说明中断恢复时保留和重跑记录的原因及来源。
- [evaluation/single-multi/runs/paired-recovered-v1/sandbox-failed-resume.jsonl](<E:/desktop/LLM_Project/askdata/evaluation/single-multi/runs/paired-recovered-v1/sandbox-failed-resume.jsonl>)：保留沙箱中恢复失败的记录，供审计和故障定位。
- [evaluation/single-multi/runs/paired-recovered-v1/complete.json](<E:/desktop/LLM_Project/askdata/evaluation/single-multi/runs/paired-recovered-v1/complete.json>)：记录完成数量、源码完整性检查和运行完成状态。
- [evaluation/single-multi/runs/paired-recovered-v1/blind-clarification.json](<E:/desktop/LLM_Project/askdata/evaluation/single-multi/runs/paired-recovered-v1/blind-clarification.json>)：隐藏执行模式标识后的澄清回答审阅材料。
- [evaluation/single-multi/runs/paired-recovered-v1/review-audit.json](<E:/desktop/LLM_Project/askdata/evaluation/single-multi/runs/paired-recovered-v1/review-audit.json>)：回放检查审核阶段前后证据的审计产物。
- [evaluation/single-multi/runs/paired-recovered-v1/calls/A-A1-single.jsonl](<E:/desktop/LLM_Project/askdata/evaluation/single-multi/runs/paired-recovered-v1/calls/A-A1-single.jsonl>)：A-A1 单路案例的模型调用明细；calls 中其他案例及模式文件形式一致。
- [evaluation/single-multi/runs/paired-recovered-v1/analysis.json](<E:/desktop/LLM_Project/askdata/evaluation/single-multi/runs/paired-recovered-v1/analysis.json>)：本地重新评分与配对汇总分析，包含有效样本、配对项和评分器版本，属于被忽略的派生输出。

<a id="retrievalv2"></a>
## 最新检索评测产物

- [evaluation/retrieval-v2/README.md](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/README.md>)：当前检索实验的命令、指标、选型规则、复现方法与失败修订说明。
- [evaluation/retrieval-v2/report.md](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/report.md>)：开发选型、检索留出结果、真实 Agent 配对结果、故障及验证范围的汇总报告。
- [evaluation/retrieval-v2/datasets.json](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/datasets.json>)：开发40题与留出16题的预置问题、参考 SQL、必要字段及 Agent 验收标记。
- [evaluation/retrieval-v2/contexts.json](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/contexts.json>)：电商与招聘财务目录的 SchemaSnapshot 和 QueryContext 固定元数据。
- [evaluation/retrieval-v2/company.sql](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/company.sql>)：创建招聘财务跨 Schema 夹具及相似干扰表的 SQL。
- [evaluation/retrieval-v2/company.duckdb](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/company.duckdb>)：由 company.sql 创建的本地评测数据库。
- [evaluation/retrieval-v2/dev.jsonl](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/dev.jsonl>)：开发集各检索策略的阶段排名、字段覆盖与结构补全记录。
- [evaluation/retrieval-v2/dev-summary.json](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/dev-summary.json>)：开发集各策略的检索指标和降级数量汇总。
- [evaluation/retrieval-v2/dev-manifest.json](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/dev-manifest.json>)：开发运行时的源码、夹具、模型及参数 hash 清单，用于断点续跑核对。
- [evaluation/retrieval-v2/dev-startup.json](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/dev-startup.json>)：开发运行索引准备和本地模型加载的启动耗时。
- [evaluation/retrieval-v2/dev-calls.json](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/dev-calls.json>)：开发阶段捕获的真实模型调用耗时、状态和用量记录。
- [evaluation/retrieval-v2/selection.json](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/selection.json>)：开发集选中的策略、参数、选型规则和开发指标。
- [evaluation/retrieval-v2/freeze.json](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/freeze.json>)：留出评测前锁定的源码、配置、题目、夹具、策略和供应商 hash。
- [evaluation/retrieval-v2/snapshot.zip](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/snapshot.zip>)：原冻结时的源码与夹具压缩快照。
- [evaluation/retrieval-v2/holdout.jsonl](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/holdout.jsonl>)：留出集每题每策略的召回、融合、重排和结构补全明细。
- [evaluation/retrieval-v2/holdout-summary.json](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/holdout-summary.json>)：留出集各策略的覆盖、排序质量、耗时及降级汇总。
- [evaluation/retrieval-v2/holdout-manifest.json](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/holdout-manifest.json>)：留出运行的配置与完整性清单。
- [evaluation/retrieval-v2/holdout-startup.json](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/holdout-startup.json>)：留出运行的索引准备和加载耗时。
- [evaluation/retrieval-v2/holdout-calls.json](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/holdout-calls.json>)：留出阶段捕获的模型调用状态、耗时及 Token。
- [evaluation/retrieval-v2/agent.jsonl](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/agent.jsonl>)：预选8题、两种检索方案共16次真实 Agent 验收的输出、SQL、调用和判分。
- [evaluation/retrieval-v2/analysis.json](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/analysis.json>)：补充统计 Agent 正确率、SQL 表路径、服务异常和已知 Token，并记录冻结核对结果。
- [evaluation/retrieval-v2/validation.json](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/validation.json>)：记录离线回归数量、PG 跳过原因、夹具逐字段采样检查及采样修复说明。
- [evaluation/retrieval-v2/agent-amendment.json](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/agent-amendment.json>)：绑定原冻结清单，记录 Agent 评测适配器修订的源码 hash 和原因。
- [evaluation/retrieval-v2/agent-harness-amendment.patch](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/agent-harness-amendment.patch>)：展示电商 demo_catalog 适配、部分报告处理和修订审计的源码差异。
- [evaluation/retrieval-v2/agent-harness.zip](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/agent-harness.zip>)：保存修订后的评测适配器，复原时叠加到原 snapshot.zip。
- [evaluation/retrieval-v2/failed-local-run/dev.jsonl](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/failed-local-run/dev.jsonl>)：保留缺少本地重排权重时的开发逐题记录。
- [evaluation/retrieval-v2/failed-local-run/dev-summary.json](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/failed-local-run/dev-summary.json>)：汇总上述全部重排降级的开发结果。
- [evaluation/retrieval-v2/failed-local-run/selection.json](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/failed-local-run/selection.json>)：上述故障运行产生的旧选型记录，由正式 API 运行取代。
- [evaluation/retrieval-v2/failed-local-run/reason.json](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/failed-local-run/reason.json>)：说明权重缺失、降级原因及该轮尚未进入冻结留出评测。
- [evaluation/retrieval-v2/failed-agent-fixture-run/agent.jsonl](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/failed-agent-fixture-run/agent.jsonl>)：保留评测连接器表名适配错误导致的首轮 Agent 记录。
- [evaluation/retrieval-v2/traces/traces-20261107.jsonl](<E:/desktop/LLM_Project/askdata/evaluation/retrieval-v2/traces/traces-20261107.jsonl>)：真实 Agent 验收的节点和模型调用 Trace；同目录其他日期文件采用相同记录形式。

<a id="generated"></a>
## 生成目录与复习路线

- `frontend/node_modules/`：npm 安装的第三方依赖文件，由 package-lock.json 约束版本。
- `frontend/dist/`：Vite 构建输出，典型文件为 index.html 和带内容 hash 的 assets/*.js、assets/*.css，具体名称随构建变化。
- `backend/**/__pycache__/`：Python 字节码缓存，由解释器生成。
- `backend/*.log`：测试和评测命令的控制台输出重定向文件，常见检索验收日志包括 retrieval-acceptance-api.log，正式结论见 validation.json 和 report.md。
- `.claude/`、`.codex/`、`.agents/`、`.vscode/`：本机开发工具和编辑器状态或配置，按本地环境存在情况生成。

建议复习顺序为 runtime.py → state.py → graph.py → nodes.py → SQL 模块 → 数据源模型和治理 → 检索模块 → supervisor.py 与 specialists → 模型失败治理 → 记忆与 Skill → 可观测性 → 评测 → 前端与部署。

每读一个模块，结合对应测试记录输入、输出、依赖和失败处理。阅读历史快照、失败归档和早期报告时，同时核对其 manifest 与当前源码的版本差异。
