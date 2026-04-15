# AGENTS.md — AI Agent 协作规范

本文件定义 AI Agent（Cursor / Claude Code / 其他）在本项目中的工作规范。
所有 Agent 在执行任何代码变更前，必须先阅读本文件。

## 1. 文档同步规则（强制）

**任何代码变更必须同步更新对应文档。不允许只改代码不改文档。**

| 变更类型 | 必须更新的文档 |
|---------|-------------|
| 新增/删除/重命名 Python 模块 | `docs/design.md` §8.1 项目结构、§12.9 完整结构 |
| 数据库表结构变更 | `docs/design.md` §5.1 表结构 + 新建 `migrations/xxx.sql` |
| 新增/修改路由 | `docs/design.md` §6.1 路由设计 |
| 新增/修改页面模板 | `docs/design.md` §6.2 页面设计、§12.9 模板列表 |
| 环境变量变更 | `docs/design.md` §7.1 + `.env.example` |
| 外部服务集成变更 | `docs/design.md` 对应章节（MinerU §8.5 / Qdrant §8.6 等） |
| 架构性变更（如队列/异步） | `docs/design.md` §2 系统约束、§8.3 异步处理 |
| 新增依赖 | `requirements.txt` + `docs/design.md` §8.2 |

### 检查清单

Agent 在回复"已完成"之前，必须自查：

```
□ docs/design.md 是否反映了本次变更？
□ .env.example 是否与 .env 同步（新增/删除变量）？
□ migrations/ 是否有对应的 SQL 迁移文件？
□ 新增测试是否覆盖本次变更的核心逻辑？
□ AGENTS.md 的规则本身是否需要更新？
```

## 2. 测试规则（强制）

### 2.1 测试分层

| 层级 | 位置 | 运行命令 | 覆盖范围 |
|------|------|---------|---------|
| 单元测试 | `tests/test_*.py` | `make test` | 业务逻辑，mock 外部服务 |
| 契约测试 | `tests/test_contracts.py` | `make test` | 外部 API 字段名/响应结构 |
| 前端 HTML | `tests/test_frontend.py` | `make test` | 页面结构/form/元素完整性 |
| E2E 测试 | `scripts/ab-e2e-test.sh` | `make test-e2e` | agent-browser 真实浏览器 |
| 巡检截图 | `scripts/ab-inspect.sh` | `make inspect` | agent-browser 页面截图 |

### 2.2 什么必须有测试

- **外部 API 调用**：必须有契约测试验证请求字段名、Content-Type、响应结构解析
- **LLM 输出处理**：必须测试 markdown code fence 包裹、JSON 嵌套等常见格式偏差
- **HTML 模板**：涉及 form 提交的页面必须有路由测试验证 form action 正确性
- **新增路由**：必须有对应的路由测试（至少测试状态码）
- **前端真实交互异常**：优先使用已安装的 `agent-browser` 复现，尤其是“按钮点击无效”“一直加载中”“JS 初始化异常”“样式/资源加载失败”等浏览器侧问题；排查时优先记录 console / errors / network 证据

### 2.3 测试覆盖本次变更的已知 bug 类型

以下场景曾导致线上问题，新代码必须回归：

| Bug | 根因 | 测试 |
|-----|------|------|
| MinerU 422 | 字段名 `file` 应为 `files` | `test_mineru_field_name` |
| 摄入按钮无效 | HTML 嵌套 form | `test_no_nested_forms` |
| Wiki 页面不显示 | LLM 输出 ` ```yaml ` 包裹 | `test_clean_llm_markdown` |
| 摄入 0 created | 服务器无法连外网 LLM | 启动时 `manage.py check` |
| 进度 SSE 阻塞 worker | 摄入在 HTTP 线程内执行 | `test_task_queued_on_upload` |
| 未验证邮箱仍可登录 | 缺少 `email_verified` 登录门禁 | `test_login_blocks_unverified_user_and_resends_verification_email` |

## 3. 架构约束

- **单体应用**：Flask + Jinja2 SSR，不引入前后端分离
- **后台队列**：`task_worker.py` 用 Python threading，不引入 Celery/Redis
- **数据库**：MySQL，手动 SQL 迁移（不用 Alembic）
- **文件存储**：本地文件系统，不用 Git/S3
- **LLM 接口**：仅 OpenAI 兼容接口

## 4. 部署信息

| 项 | 值 |
|----|---|
| 服务器 | 172.36.164.85:2234 (SSH) |
| 应用路径 | /opt/open-llm-wiki |
| 服务名 | llmwiki (systemd) |
| 端口 | 5000 |
| LLM | http://172.36.237.245:30000/v1 (qwen35-27b) |
| Embedding | http://172.36.237.245:11434/v1 (bge-m3) |
| MinerU | http://172.36.237.175:8000 |
| Qdrant | http://172.36.164.85:6333 |
| MySQL | 172.36.164.85:3306 / llmwiki |

### 4.1 部署约定（强制）

- 生产 `systemd` 服务名固定为 `llmwiki`，服务文件来源于仓库内 `deploy/llmwiki.service`
- `deploy/llmwiki.service` 必须包含 `EnvironmentFile=/opt/open-llm-wiki/.env`，否则应用将读不到 `MAIL_*` 等运行时配置
- 服务器连接信息仅保存在项目根本地 `.env` 的 `DEPLOY_HOST / DEPLOY_PORT / DEPLOY_USER / DEPLOY_PASSWORD`，用于 `scripts/deploy.sh`
- 本地 `.env` 绝不提交到 Git；仓库只提交 `.env.example`

## 5. 代码风格

- Python 代码遵循 Ruff 规则（`pyproject.toml`）
- 不加叙述性注释（如 "# 导入模块"），只注释非显而易见的逻辑
- 提交信息用中文或英文均可，简洁描述 why 而非 what
