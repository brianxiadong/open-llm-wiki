# Open LLM Wiki

基于 Karpathy 提出的 LLM Wiki 思路构建的多用户知识库平台。

它不是“每次查询都临时检索一遍原文”的传统 RAG，而是让 LLM 在文档摄入阶段持续维护一个可演进的 Wiki：新资料进入后被分析、整合、交叉引用、写回知识库，查询时直接基于已经“编译好”的知识回答。

更完整的设计说明见：`docs/design.md`

## 项目特点

- 多用户、多知识库，每个仓库独立维护自己的 `raw/ + wiki/ + schema.md`
- 支持 Markdown、TXT、PDF、DOCX、PPTX、图片、CSV、Excel 等多种资料导入
- 摄入流程会自动生成或更新 Wiki 页面、目录页、概览页和操作日志
- 查询采用双通道检索：Wiki 结构化导航 + Qdrant 向量检索
- 支持流式回答（SSE）、证据展示、置信度分级、将优质回答保存为 Wiki 页面
- 前端为 Flask + Jinja2 SSR，界面面向知识库/文档工作台场景优化
- Wiki 文件最终落盘为 Markdown，可直接被 Obsidian 等工具打开

## 适用场景

- 研究资料整理
- 技术文档 / 产品文档沉淀
- 团队内部知识库
- 竞品分析、读书笔记、项目复盘
- 需要“持续累积知识”而不是“一次性问答”的 AI 文档系统

## 核心理念

传统 RAG 的典型路径是：

1. 用户提问
2. 临时检索原文片段
3. 拼接上下文
4. 让模型现场回答

本项目的路径不同：

1. 用户上传原始文档
2. LLM 在摄入阶段分析和整合知识
3. 将结果写入可持续演进的 Wiki
4. 查询时优先读取结构化知识，再辅以向量检索补全细节

也就是说，这个系统的核心产物不是聊天记录，而是一个持续生长的 Wiki。

## 三层结构

每个知识库仓库都遵循以下结构：

```text
{username}/{repo-slug}/
├── schema.md
├── raw/
│   ├── assets/
│   └── ...
└── wiki/
    ├── index.md
    ├── log.md
    ├── overview.md
    └── ...
```

- `raw/`：原始资料层，作为事实来源，LLM 只读不写
- `wiki/`：LLM 维护的知识层，存放概览、实体、概念、来源摘要等页面
- `schema.md`：仓库级知识组织规则，控制 LLM 如何摄入、查询和维护该仓库

## 技术栈

- 后端：Flask 3、Flask-Login、Flask-SQLAlchemy
- 前端：Jinja2 SSR + Pico CSS + 自定义主题
- 数据库：MySQL
- 向量检索：Qdrant
- LLM 接口：OpenAI Compatible API
- 文档解析：MinerU
- 异步任务：Python `threading`
- 部署：Gunicorn + systemd

## 主要能力

### 1. 文档摄入

支持以下输入：

- Markdown / TXT：直接写入
- PDF / DOCX / PPTX / 图片：通过 MinerU 解析
- CSV：按文本导入
- Excel（`.xlsx` / `.xls`）：解析为 Markdown 表格

摄入完成后，系统会：

- 分析文档摘要、实体、概念、发现
- 决定需要创建或更新哪些 Wiki 页面
- 写回 `wiki/`
- 更新 `index.md`、`overview.md`、`log.md`
- 同步更新 Qdrant 页面级和 chunk 级索引

### 2. 智能查询

查询采用双通道：

- Wiki 路径：从 `index.md` 和已存在的结构化页面中导航
- Qdrant 路径：从向量索引中检索语义相关页面 / chunk

最终返回：

- 回答内容
- Wiki 证据
- Chunk 证据
- 置信度（`high / medium / low`）

### 3. Wiki 维护

系统支持：

- 维护检查（lint）
- 实体去重检查
- 知识缺口分析
- 关系图谱
- Wiki 编辑 / 删除
- 将优质回答保存为新的 Wiki 页面

## 快速开始

### 1. 环境要求

- Python `>= 3.11`
- MySQL
- Qdrant
- 一个 OpenAI 兼容的 LLM 服务
- 一个 Embedding 服务
- MinerU 文档解析服务

### 2. 初始化项目

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

然后按你的环境修改 `.env`。

### 3. 初始化数据库

```bash
.venv/bin/python manage.py init-db
```

如果你使用 SQL 迁移文件：

```bash
.venv/bin/python manage.py migrate
```

### 4. 启动前检查

```bash
.venv/bin/python manage.py check
```

这会检查：

- MySQL
- MinerU
- Qdrant
- Embedding 服务

### 5. 启动开发环境

```bash
make dev
```

默认监听：

```text
http://0.0.0.0:5000
```

### 6. 生产启动

```bash
make prod
```

等价命令：

```bash
.venv/bin/gunicorn -w 2 -b 0.0.0.0:5000 'app:create_app()'
```

## 环境变量

完整示例见：`.env.example`

最重要的变量如下：

| 变量 | 说明 |
|------|------|
| `LLM_API_BASE` | OpenAI 兼容 LLM 服务地址 |
| `LLM_API_KEY` | LLM API Key |
| `LLM_MODEL` | 对话 / 生成模型名 |
| `EMBEDDING_API_BASE` | Embedding 服务地址 |
| `EMBEDDING_MODEL` | 向量模型名 |
| `EMBEDDING_DIMENSIONS` | 向量维度 |
| `QDRANT_URL` | Qdrant 地址 |
| `MINERU_API_URL` | MinerU 服务地址 |
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | MySQL 配置 |
| `SECRET_KEY` | Flask Secret |
| `DATA_DIR` | 数据目录 |
| `ADMIN_USERNAME` | 管理员用户名 |

## 常用命令

### Makefile

```bash
make init
make dev
make prod
make migrate
make check
make test
make test-e2e
make inspect
make lint
make format
```

### 管理命令

```bash
.venv/bin/python manage.py init-db
.venv/bin/python manage.py migrate
.venv/bin/python manage.py check
.venv/bin/python manage.py create-user <username> <password> --display-name "Display Name"
.venv/bin/python manage.py rebuild-chunk-index --repo-id <id>
```

## 目录结构

```text
.
├── app.py                 # Flask 应用与所有 Blueprint 路由
├── config.py              # 配置加载
├── models.py              # SQLAlchemy 模型
├── wiki_engine.py         # Wiki 摄入 / 查询 / 维护核心逻辑
├── qdrant_service.py      # Qdrant 页面级与 chunk 级索引封装
├── llm_client.py          # OpenAI Compatible LLM 客户端
├── mineru_client.py       # MinerU 文档解析客户端
├── task_worker.py         # 后台任务队列（threading）
├── utils.py               # Markdown、仓库路径、页面扫描等工具函数
├── templates/             # Jinja2 模板
├── static/                # CSS / JS / 静态资源
├── tests/                 # 单元、契约、前端、路由、E2E 测试
├── scripts/               # 对比测试、E2E 脚本等
├── migrations/            # SQL 迁移文件
└── docs/design.md         # 完整设计文档
```

## 主要页面 / 功能入口

- 登录 / 注册
- 我的知识库列表
- 仓库仪表盘
- 文档上传与文档管理
- Wiki 阅读 / 编辑
- 智能查询
- 任务队列
- 维护检查
- 知识缺口分析
- 实体去重检查
- 关系图谱
- 全局搜索
- 管理后台

## 测试

### 基础测试

```bash
make test
```

### E2E 测试

```bash
make test-e2e
```

### 页面巡检截图

```bash
make inspect
```

### 前端结构测试示例

```bash
pytest tests/test_frontend.py -q
```

## 开发约定

- 前后端不分离，保持 Flask + Jinja2 SSR
- 不引入 Celery / Redis，后台任务使用 Python `threading`
- Wiki 内容最终落盘为 Markdown
- 向量检索只作为增强，核心知识资产始终是 Wiki 本身
- 新增路由、页面、依赖、环境变量、表结构时，需要同步更新 `docs/design.md`

## 何时选择这个项目

如果你的目标是：

- 让知识随着文档导入持续积累
- 沉淀结构化的可读知识资产
- 不希望每次问答都临时拼上下文
- 希望把高质量回答反哺为新的知识页面

那么这个项目比“纯聊天 + 临时检索”的方案更适合。

## 参考

- `docs/design.md`：完整设计与架构说明
- Karpathy LLM Wiki 原始思路：<https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f>
