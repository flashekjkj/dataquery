# AskData Docker 部署

当前方案使用两个 Linux 容器：Nginx 提供前端并代理 `/api`，FastAPI 运行后端。MCP 计算器仍作为后端容器内的本地子进程运行。

## 本机验证记录（2026-10-22）

前后端已启动，访问地址为 <http://localhost:8080>。已通过网页入口、健康检查、技能列表、真实模型闲聊、数据库查询、MCP 计算器、Nginx SSE 转发及后端重启后的会话保留检查。后端日志确认本地精排模型正常加载；绑定挂载均位于 `F:\AskData`。

构建最终使用 PyPI 官方源和本机代理；本机访问清华镜像时出现 TLS 连接中断，未使用该镜像完成构建。

## 存储位置

本机 Docker 程序位于 `F:\DockerDesktop`，Docker 虚拟磁盘位于 `F:\DockerData`。项目挂载的数据通过 `.env.docker` 中的 `ASKDATA_STORAGE_ROOT` 设置，当前为 `F:\AskData`。

```text
F:\AskData\
├── data\
│   ├── demo_ecom.duckdb   # 原数据库的独立副本，容器只读挂载
│   ├── conversations\    # 部署实例的会话文件
│   └── user_memory.json  # 部署实例的长期偏好
└── models\huggingface\hub\models--BAAI--bge-reranker-base\
```

部署实例的数据和本地开发数据相互独立。首次准备时复制现有数据库和会话；后续不要重复覆盖部署目录。Skill 配置与代码一起进入镜像。

## 启动前准备

1. 启动 Docker Desktop，确认使用 Linux 容器。
2. 配置 `backend/.env` 中的模型 API 参数。该文件通过 Compose 注入，不复制进镜像。
3. 参考 `.env.docker.example` 准备 `.env.docker`。
4. 准备上述数据目录，复制现有数据库；`user_memory.json` 不存在时初始化为 `{"users": {}}`。
5. 将已有 Hugging Face 模型目录复制到对应缓存路径。Windows 缓存若使用符号链接，需复制实际模型文件，避免指向 C 盘的链接。

后端每次启动会调用 Embedding API 构建字段索引，问答也会调用已配置的模型 API，使用量按模型服务商规则计费。精排采用 CPU 推理；模型缓存缺失时会告警并按现有代码降级，不能将这种情况视为精排已验证。

## PowerShell 命令

在项目根目录运行。刚安装 Docker 时，旧终端可能需要临时补充工具目录；新终端通常无需这一步。

```powershell
$env:PATH = 'F:\DockerDesktop\resources\bin;' + $env:PATH
docker --context desktop-linux compose --env-file .env.docker config --quiet
docker --context desktop-linux compose --env-file .env.docker build
docker --context desktop-linux compose --env-file .env.docker up -d --wait --wait-timeout 600
```

本机若在构建时连接 Docker Hub 超时，可在当前 PowerShell 会话使用已有代理重试。下面的 `7897` 必须与本机代理实际监听端口一致，代理需要保持运行；这些设置只影响当前终端。`127.0.0.1` 供 Windows 端工具使用，`host.docker.internal` 供构建容器访问宿主机代理。

```powershell
$env:HTTP_PROXY = 'http://127.0.0.1:7897'
$env:HTTPS_PROXY = 'http://127.0.0.1:7897'
$env:NO_PROXY = 'localhost,127.0.0.1'
docker --context desktop-linux compose --env-file .env.docker --progress plain build --build-arg HTTP_PROXY=http://host.docker.internal:7897 --build-arg HTTPS_PROXY=http://host.docker.internal:7897
```

普通 Python 依赖下载慢时，可增加 `--build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`，并用 `--build-arg NO_PROXY=pypi.tuna.tsinghua.edu.cn` 让该镜像直连。默认仍使用 PyPI 官方源；CPU 版 PyTorch 始终使用其官方源。参见[清华镜像使用说明](https://mirrors.tuna.tsinghua.edu.cn/help/pypi/)。

打开 <http://localhost:8080>。健康检查为 <http://localhost:8080/api/health>。首次启动需等待字段索引构建完成。

```powershell
# 状态和日志
docker --context desktop-linux compose --env-file .env.docker ps
docker --context desktop-linux compose --env-file .env.docker logs -f --tail 100

# 重启服务（后端会重新构建索引）
docker --context desktop-linux compose --env-file .env.docker restart

# 修改代码或 Skill 后重建并启动
docker --context desktop-linux compose --env-file .env.docker up -d --build --wait --wait-timeout 600

# 停止并移除容器和网络；F:\AskData 下的绑定挂载文件保留
docker --context desktop-linux compose --env-file .env.docker down
```

Nginx 已关闭代理缓冲，支持 `/api/chat/stream` 的节点进度 SSE。模型本身的逐 token 输出仍由原项目能力决定。

## 验证

- 网页与 `/api/health` 正常。
- 提问“你好”，验证模型连接。
- 提问“华东地区上月销售额是多少”，验证查询链路。
- 提问“帮我计算 3*5+2”，验证 MCP 计算器。
- 查看 SSE 节点事件与容器日志，确认精排没有降级。
- 重启服务后检查历史会话和长期偏好仍在。

现有 Python 环境中可运行部署冒烟检查（会调用模型 API，并创建带 `docker-` 前缀的测试会话）：

```powershell
python backend/test/manual_docker.py
# 将上一步打印的 PERSISTENCE_SESSION 值填入下面命令，重启容器后验证：
python backend/test/manual_docker.py --check-session docker-query-实际会话ID
```

当前 LangGraph Checkpoint 仍在内存中；持久化的是已有 JSON 会话和偏好，不能等同于完整图状态持久化。

## 迁移到其他电脑或服务器

安装 Docker/Compose，复制代码和部署数据，在目标机器配置 `backend/.env`、`.env.docker`。Linux 上将 `ASKDATA_STORAGE_ROOT` 换成实际绝对路径，例如 `/srv/askdata`。当前端口仅绑定本机，远程访问需按实际部署方案调整。
