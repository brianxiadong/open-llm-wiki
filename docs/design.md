# Open LLM Wiki — 设计文档

> 基于 [Karpathy 的 LLM Wiki 模式](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)，构建多用户共享知识库平台。

## 1. 核心理念

区别于 RAG 的「每次查询重新检索」，LLM Wiki 的核心是：**LLM 增量构建并维护一个持久化的 Wiki**。

- 新文档加入时，LLM 不是简单索引，而是**阅读、提取、整合**到已有 Wiki 中
- 知识被**编译一次，持续更新**，而非每次查询重新推导
- 交叉引用、矛盾标记、综合总结——这些在摄入时就已完成
- Wiki 是一个**持久的、复利增长的知识制品**

本系统将这一模式扩展为多用户平台：每个用户可拥有多个知识库仓库，每个仓库独立运行完整的 LLM Wiki 机制。

## 2. 系统约束

| 约束 | 决策 |
|------|------|
| 内部系统，无需高可用 | 单进程部署，MySQL |
| 前后端不分离 | Flask + Jinja2 服务端渲染 |
| 架构尽量简单 | Python threading 后台任务队列，无 Celery/Redis/webpack |
| 保持原汁原味 | Wiki 就是磁盘上的 markdown 文件，可用 Obsidian 直接打开 |
| LLM 接口 | 仅 OpenAI 兼容接口（覆盖 OpenAI/DeepSeek/Ollama 等） |
| 文档解析 | 调用已部署的 MinerU 服务（http://172.36.237.175:8000） |
| 检索增强 | Qdrant 向量检索 + LLM Wiki 结构化导航，双通道互补 |

## 3. 三层架构（per repo）

每个仓库严格遵循 Karpathy 定义的三层结构：

```
{username}/{repo-slug}/
├── schema.md          ← 第三层：Schema，控制 LLM 行为的配置文档
├── raw/               ← 第一层：原始文档（不可变，LLM 只读不写）
│   ├── assets/        ← 图片等附件（MinerU 提取的图片）
│   ├── article-1.md
│   ├── paper.pdf.md   ← PDF 上传时由 MinerU 转为 markdown
│   └── ...
└── wiki/              ← 第二层：LLM 生成的 Wiki（LLM 完全拥有）
    ├── index.md       ← 内容目录——所有页面的分类索引
    ├── log.md         ← 时间线——操作的追加记录
    ├── overview.md    ← 全局综述——所有来源的高层综合
    └── ...            ← 实体页、概念页、来源摘要页等
```

### 3.1 原始文档层（raw/）

用户上传的原始资料。不可变——LLM 只从中读取，永不修改。这是知识的事实来源。

支持的格式（通过 MinerU 服务解析）：
- Markdown (.md)——直接存储，不经过 MinerU
- 纯文本 (.txt)——直接存储，不经过 MinerU
- PDF (.pdf)——调用 MinerU 转为 .pdf.md
- DOCX (.docx)——调用 MinerU 转为 .docx.md
- PPT (.pptx)——调用 MinerU 转为 .pptx.md
- 图片 (.png/.jpg)——调用 MinerU OCR 转为 .md

原始上传文件（二进制）不保留，只保留转换后的 markdown 和 MinerU 提取的图片。

### 3.2 Wiki 层（wiki/）

LLM 生成并维护的 markdown 文件集合。用户阅读，LLM 书写。

**两个特殊文件**（来自原文档）：

**index.md**——面向内容的目录。每个页面有链接、一行摘要、元数据（日期、来源数）。按类别组织（实体、概念、来源等）。LLM 在每次摄入时更新它。查询时 LLM 先读 index.md 定位相关页面，再深入阅读。在中等规模（~100 个来源、数百页面）下效果出奇的好，避免了嵌入式 RAG 基础设施。

**log.md**——时间线记录。追加式的操作日志——摄入、查询、维护检查。每条以统一前缀开头（如 `## [2026-04-10] ingest | 文章标题`），可用简单工具解析。

**Wiki 页面格式**：
```markdown
---
title: 页面标题
type: entity|concept|source|topic|analysis
created: 2026-04-10
updated: 2026-04-10
sources:
  - article-1.md
  - paper.pdf.md
---

# 页面标题

正文内容，包含到 [其他页面](other-page.md) 的交叉引用。
```

### 3.3 Schema 层（schema.md）

控制 LLM 如何维护该仓库 Wiki 的配置文档。新建仓库时可从预设模板选择，用户可以随时修改。用户与 LLM 共同演进这个文件。

预设模板（`utils.SCHEMA_TEMPLATES`）：
- **通用**（default）：通用页面类型（concept/guide/reference/overview 等）
- **学术研究**（academic）：paper/concept/method/result/comparison，含证据等级字段
- **产品文档**（product）：feature/guide/reference/faq/changelog
- **技术笔记**（tech_notes）：concept/howto/snippet/troubleshoot

Schema 定义：
- Wiki 的组织结构（有哪些类别的页面）
- 命名约定和格式要求
- 摄入工作流（拿到新文档时做什么）
- 查询工作流（被问问题时做什么）
- 维护工作流（Lint 时检查什么）
- 领域特定规则（如：在研究场景中标注证据等级）

## 4. 核心操作

### 4.1 Ingest（摄入）

用户上传文档到 raw/，触发 LLM 处理。这是 Wiki 知识增长的主要途径。

**多步流程**：

```
Step 1: 分析
  LLM 读取原始文档
  → 返回：关键实体、核心概念、主要发现、与已有知识的关联

Step 2: 规划
  LLM 读取 index.md + 相关已有页面
  → 返回：需要创建/更新哪些 Wiki 页面，每个页面的变更摘要

Step 3: 执行
  对每个需要变更的页面：
    LLM 生成完整页面内容（新建）或修改内容（更新）
    → 系统写入 wiki/ 目录

Step 4: 向量索引
  对本次新建/更新的每个 wiki 页面：
    调用 Embedding 模型生成向量
    → 写入 Qdrant（upsert，按页面文件名去重）

Step 5: 收尾
  更新 index.md（新增/修改条目）
  追加 log.md（记录本次摄入）

Step 6: 更新 overview.md
  LLM 综合所有 Wiki 页面生成全局概览
  → 写入 overview.md + Qdrant upsert（仅当本次有实际变更时执行）
```

**为什么分多步而非一次调用？**
- 单个来源可能需要触碰 10-15 个 Wiki 页面
- 更新已有页面需要先读取它们（上下文窗口有限）
- 分步执行让每一步的输出更精确、可控

### 4.2 Query（查询）

用户对 Wiki 提问。LLM 基于已编译的知识回答，而非从原始文档重新检索。

采用**双通道检索**——LLM Wiki 结构化导航 + Qdrant 向量语义检索互补：

```
                      用户提问
                         │
            ┌────────────┴────────────┐
            ▼                         ▼
    Wiki 结构化路径              Qdrant 语义路径
    LLM 读 index.md             向量相似度检索
    沿目录和交叉引用导航         返回 Top-K 页面
    选出结构上相关的页面         选出语义上相关的页面
            │                         │
            └────────────┬────────────┘
                         ▼
                  合并去重，得到最终页面集
                         │
                         ▼
                  LLM 阅读这些页面，综合回答
                         │
                         ▼
              用户可选择将回答保存为新 Wiki 页面
```

**为什么需要两条路径？** 它们各自擅长不同类型的查询：

| 查询类型 | Wiki 路径更优 | Qdrant 路径更优 |
|---------|:---:|:---:|
| "Transformer 的完整概述" | ✅ 直接读编译好的页面 | |
| "这个领域的整体结论" | ✅ overview.md 就是答案 | |
| "A 和 B 有什么矛盾" | ✅ 摄入时已标记矛盾 | |
| "哪篇文档提到了 FlashAttention" | | ✅ 精确关键词命中 |
| "我记得有个优化训练速度的方法..." | | ✅ 模糊语义匹配 |
| 跨多个主题的复杂综合问题 | ✅ 交叉引用已建好 | ✅ 覆盖面更广 |

两条路径找到的页面合并后，既覆盖了结构上相关的（Wiki 路径），也覆盖了语义上相关的（Qdrant 路径），检索质量远高于单一通道。

关键洞察（来自原文档）：**好的回答可以被归档为新的 Wiki 页面。** 一次对比分析、一个发现的关联——这些不应该消失在聊天历史中。保存的回答同样会被向量化写入 Qdrant，后续查询可以检索到。

#### SSE 流式路径

为改善用户体验，查询新增了流式响应（Server-Sent Events）路径：

```
GET /{username}/{repo}/query/stream?q=<question>
        │
        ▼
    event: progress  (message="正在检索相关页面…", percent=10)
        │
        ▼ (双通道检索 + 读取页面内容)
    event: progress  (message="正在生成回答…", percent=60)
        │
        ▼ (LLM chat_stream，逐 token 推送)
    event: answer_chunk  (chunk="...")  × N
        │
        ▼
    event: done  (answer, wiki_sources, qdrant_sources, referenced_pages)
        │
        ▼ 前端用 done 中的 markdown 调用 POST /query（_rendered_answer 模式）渲染 HTML
```

- **后端**：`WikiEngine.query_stream()` 为生成器，`LLMClient.chat_stream()` 使用 `stream=True`；Flask 路由使用 `stream_with_context` + `mimetype=text/event-stream`。
- **前端**：`chat.js` 优先使用 `EventSource`；`answer_chunk` 事件实时更新加载气泡；`done` 后调用 `POST /query`（`_rendered_answer` 模式）获取完整渲染 HTML。
- **降级**：`queryStreamUrl` 缺失时自动回退到原 POST 轮询模式。
- **渲染复用**：`POST /query` 若请求体含 `_rendered_answer`，则跳过 LLM 调用，直接渲染 Markdown 返回 HTML，避免重复计费。

### 4.3 Lint（维护检查）

定期让 LLM 对 Wiki 做健康检查。

**检查项**：
- 页面间的矛盾
- 被新来源取代的过时声明
- 孤立页面（没有入站链接）
- 被提及但缺少独立页面的重要概念
- 缺失的交叉引用
- 可以通过网络搜索填补的数据缺口

**输出**：结构化报告，列出问题和建议修复。用户审阅后可一键应用修复。

**自动修复（`apply_fixes`）**：

`WikiEngine.apply_fixes()` 已实现，支持以下四种问题类型的自动修复：

| 问题类型 | 修复方式 |
|---------|---------|
| `bad_frontmatter` | LLM 补全或修正 YAML frontmatter（title、type、updated 字段） |
| `orphan` | LLM 在 index.md 中添加对应链接 |
| `missing_link` | LLM 在合适位置添加交叉引用 |
| `wrong_type` | LLM 修正 frontmatter 中的 type 字段 |

`contradiction`（矛盾）类问题跳过，需人工审查。修复后自动同步至 Qdrant 向量索引。

## 5. 数据模型

### 5.1 MySQL 表结构

数据库地址：`172.36.164.85:3306`，数据库名：`llmwiki`。

MySQL 只存储用户和仓库的元数据。实际内容全部在文件系统上。

```sql
CREATE TABLE users (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(64) UNIQUE NOT NULL,
    password_hash VARCHAR(256) NOT NULL,
    display_name  VARCHAR(128),
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE repos (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    user_id       INT NOT NULL,
    name          VARCHAR(128) NOT NULL,
    slug          VARCHAR(128) NOT NULL,
    description   TEXT DEFAULT (''),
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    source_count  INT DEFAULT 0,
    page_count    INT DEFAULT 0,
    is_public     TINYINT(1) NOT NULL DEFAULT 0,
    UNIQUE KEY uniq_user_slug (user_id, slug),
    FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE tasks (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    repo_id       INT NOT NULL,
    type          VARCHAR(20) NOT NULL,
    status        VARCHAR(20) NOT NULL DEFAULT 'queued',
    input_data    TEXT,
    output_data   LONGTEXT,
    progress      INT NOT NULL DEFAULT 0,
    progress_msg  TEXT,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    started_at    DATETIME,
    finished_at   DATETIME,
    FOREIGN KEY (repo_id) REFERENCES repos(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 5.2 文件系统布局

```
data/
├── alice/                      ← 用户 alice
│   ├── ml-research/            ← 仓库：机器学习研究
│   │   ├── schema.md
│   │   ├── raw/
│   │   │   ├── assets/
│   │   │   ├── attention-paper.pdf.md
│   │   │   └── transformer-survey.md
│   │   └── wiki/
│   │       ├── index.md
│   │       ├── log.md
│   │       ├── overview.md
│   │       ├── attention-mechanism.md
│   │       ├── transformer.md
│   │       └── self-attention-vs-cross-attention.md
│   └── book-notes/             ← 仓库：读书笔记
│       ├── schema.md
│       ├── raw/
│       └── wiki/
└── bob/                        ← 用户 bob
    └── competitive-analysis/
        ├── schema.md
        ├── raw/
        └── wiki/
```

## 6. Web 界面

### 6.1 路由设计

```
认证：
GET  /login                              → 登录页
POST /login                              → 登录
GET  /register                           → 注册页（开放注册）
POST /register                           → 注册
GET  /logout                             → 登出

用户：
GET  /{username}/settings                → 个人设置页（显示名称、密码）
POST /{username}/settings/profile        → 修改显示名称
POST /{username}/settings/password       → 修改密码

仓库管理：
GET  /                                   → 首页（已登录则跳转仓库列表）
GET  /{username}                         → 用户的仓库列表
POST /{username}/repos                   → 创建新仓库
GET  /{username}/{repo}                  → 仓库面板（Wiki 概览）
POST /{username}/{repo}/delete           → 删除仓库
GET  /{username}/{repo}/settings         → 仓库设置

Wiki 浏览：
GET  /{username}/{repo}/wiki             → Wiki 页面列表
GET  /{username}/{repo}/wiki/{page}      → 查看 Wiki 页面
GET  /{username}/{repo}/wiki/search      → Wiki 全文关键词搜索
GET  /{username}/{repo}/wiki/export.zip  → 导出 Wiki 为 ZIP
GET  /{username}/{repo}/graph            → 链接关系图

原始文档：
GET  /{username}/{repo}/sources          → 原始文档列表
GET  /{username}/{repo}/sources/{file}   → 查看原始文档
POST /{username}/{repo}/sources/upload   → 上传文档
POST /{username}/{repo}/sources/batch-delete → 批量删除文件
POST /{username}/{repo}/sources/batch-ingest → 批量摄入未处理文件
POST /{username}/{repo}/sources/import-url   → 从 URL 导入网页

核心操作：
POST /{username}/{repo}/ingest/{file}    → 触发摄入
GET  /{username}/{repo}/ingest/{task_id} → 摄入进度（SSE）
POST /api/tasks/{task_id}/retry          → 重试失败任务
GET  /{username}/{repo}/query            → 查询界面
POST /{username}/{repo}/query            → 提交查询（完整响应 / _rendered_answer 仅渲染模式）
GET  /{username}/{repo}/query/stream     → SSE 流式查询（EventSource）
POST /{username}/{repo}/query/save       → 保存回答为 Wiki 页面
POST /{username}/{repo}/lint             → 触发维护检查
POST /{username}/{repo}/lint/apply       → 应用修复建议
GET  /{username}/{repo}/tasks              → 任务队列看板（实时进度）
GET  /api/tasks/{task_id}/status           → 任务状态 JSON API

Schema：
GET  /{username}/{repo}/schema           → 查看 Schema
POST /{username}/{repo}/schema           → 更新 Schema

操作历史：
GET  /{username}/{repo}/log              → 查看操作日志（log.md）

Wiki 编辑：
GET/POST /{username}/{repo}/wiki/{page}/edit   → 编辑 Wiki 页面（仅 owner）
POST     /{username}/{repo}/wiki/{page}/delete → 删除 Wiki 页面（仅 owner）
```

### 6.2 页面设计

**首页 / 仓库列表**：卡片式展示用户的所有仓库，每个卡片显示名称、描述、来源数、页面数、最后更新时间。右上角「新建仓库」按钮。

**仓库面板**：左侧是 Wiki 页面的树状导航（按类别），右侧默认显示 overview.md 的渲染内容。顶部工具栏有：上传文档、查询、维护检查、Schema、操作日志。

**Wiki 页面**：渲染后的 markdown，顶部显示 frontmatter 元数据（类型、创建日期、来源）。页面内的 `[链接](page.md)` 自动转为站内链接。侧边栏显示「被引用此页面」的反向链接列表。owner 可通过页面顶部的「编辑」和「删除」按钮管理页面。

**Wiki 编辑页**（`templates/wiki/edit.html`）：EasyMDE Markdown 编辑器，支持实时预览与自动保存草稿。

**查询界面**：上方输入框，下方显示回答（markdown 渲染）。回答中的 Wiki 引用可点击跳转。有「保存为 Wiki 页面」按钮。

**关系图**：用 D3.js 力导向图展示页面间的链接关系。类似 Obsidian 的 graph view。

## 7. LLM 集成

### 7.1 客户端配置

使用 OpenAI 兼容接口，通过环境变量配置：

```
# LLM 配置（对话/生成）
LLM_API_BASE=http://172.36.237.245:30000/v1
LLM_API_KEY=none
LLM_MODEL=qwen35-27b
LLM_MAX_TOKENS=16384

# Embedding 模型（向量化，可与 LLM 使用不同的服务）
EMBEDDING_API_BASE=http://172.36.237.245:11434/v1
EMBEDDING_API_KEY=
EMBEDDING_MODEL=bge-m3
EMBEDDING_DIMENSIONS=1024

# Qdrant 向量数据库
QDRANT_URL=http://localhost:6333

# MinerU 文档解析服务
MINERU_API_URL=http://172.36.237.175:8000
MINERU_TIMEOUT=300

# MySQL 数据库
DB_HOST=172.36.164.85
DB_PORT=3306
DB_NAME=llmwiki
DB_USER=private_cloud
DB_PASSWORD=****

# Flask
SECRET_KEY=change-me-to-a-random-string
DATA_DIR=./data
```

### 7.2 Prompt 构造

每次 LLM 调用都遵循统一结构：

```
System Prompt:
  你是一个 Wiki 维护者。你负责阅读原始文档、构建和维护结构化的 Wiki。
  以下是本 Wiki 的 Schema：
  ---
  {schema.md 内容}
  ---

User Prompt:（根据操作类型不同）
  [摄入] 处理以下文档并整合到 Wiki 中...
  [查询] 基于 Wiki 回答以下问题...
  [维护] 检查 Wiki 的健康状态...
```

### 7.3 摄入的 Prompt 细节

**Step 1 — 分析**：

```
请分析以下原始文档，提取关键信息。

文档名：{filename}
---
{文档内容}
---

请以 JSON 格式返回：
{
  "summary": "文档的一句话摘要",
  "key_entities": ["实体1", "实体2", ...],
  "key_concepts": ["概念1", "概念2", ...],
  "main_findings": ["发现1", "发现2", ...],
  "potential_connections": "与 Wiki 中已有知识可能存在的关联"
}
```

**Step 2 — 规划**：

```
基于上面的分析结果，以及当前 Wiki 的目录：

当前 Wiki 目录（index.md）：
---
{index.md 内容}
---

请规划需要创建或更新哪些 Wiki 页面。以 JSON 格式返回：
{
  "pages_to_create": [
    {"filename": "xxx.md", "title": "标题", "type": "source|entity|concept|topic", "reason": "创建原因"}
  ],
  "pages_to_update": [
    {"filename": "xxx.md", "reason": "更新原因", "what_to_add": "需要增加的内容概述"}
  ]
}
```

**Step 3 — 执行**（对每个页面调用）：

```
创建页面场景：
请为以下主题创建一个 Wiki 页面。
页面文件名：{filename}
页面类型：{type}
相关来源：{source filename}
上下文：{来自 Step 1 的分析}
要求：遵循 Schema 中定义的页面格式，包含交叉引用。
请直接返回完整的 markdown 内容（包含 frontmatter）。

更新页面场景：
请更新以下 Wiki 页面，整合新来源的信息。
当前页面内容：
---
{现有页面内容}
---
需要整合的新信息：{来自 Step 1 和 Step 2}
要求：保持页面结构，自然地整合新内容，标注矛盾之处，更新交叉引用。
请返回更新后的完整 markdown 内容。
```

### 7.4 查询的 Prompt 细节（双通道）

**Step 1 — 双通道页面定位**（并行执行）：

通道 A — Wiki 结构化导航：
```
用户问题：{question}
当前 Wiki 目录：
---
{index.md 内容}
---
请选出回答此问题所需的 Wiki 页面（最多 10 个），以 JSON 数组返回文件名列表。
```

通道 B — Qdrant 语义检索（无需 LLM 调用）：
```python
# 系统自动执行
query_vector = embedding_model.encode(question)
qdrant_results = qdrant.search(
    collection_name=f"repo_{repo_id}",
    query_vector=query_vector,
    limit=10
)
qdrant_pages = [hit.payload["filename"] for hit in qdrant_results]
```

合并：将两个通道的结果去重合并，得到最终页面集合。

**Step 2 — 回答**：
```
用户问题：{question}
相关 Wiki 页面：
---
{page1.md 内容}
---
{page2.md 内容}
---
请基于以上 Wiki 内容回答问题。要求：
1. 综合多个页面的信息
2. 引用具体页面：使用 [页面标题](page.md) 格式
3. 如果 Wiki 中信息不足，明确指出
4. 如果此回答值得保存，建议一个文件名
```

### 7.5 维护检查的 Prompt

```
请对以下 Wiki 进行健康检查。

Wiki 目录：
---
{index.md 内容}
---

所有页面列表及摘要：
{每个页面的 frontmatter + 前几行}

请检查以下问题并返回 JSON 报告：
{
  "contradictions": [{"page1": "a.md", "page2": "b.md", "description": "矛盾描述"}],
  "stale_claims": [{"page": "x.md", "claim": "过时声明", "newer_source": "y.md"}],
  "orphan_pages": ["无入站链接的页面"],
  "missing_pages": ["被提及但不存在的概念"],
  "missing_crossrefs": [{"from": "a.md", "to": "b.md", "reason": "应该引用的原因"}],
  "suggestions": ["其他改进建议"]
}
```

## 8. 技术实现

### 8.1 项目结构

```
open-llm-wiki/
├── app.py                 ← Flask 应用入口 + 路由注册
├── config.py              ← 配置（环境变量加载）
├── models.py              ← MySQL 数据库模型（用 Flask-SQLAlchemy + PyMySQL）
├── llm_client.py          ← OpenAI 兼容 LLM 客户端封装
├── wiki_engine.py         ← 核心 Wiki 操作（ingest / query / lint）
├── qdrant_service.py      ← Qdrant 向量检索服务（embedding + 读写）
├── mineru_client.py       ← MinerU 文档解析客户端（HTTP 调用）
├── utils.py               ← 工具函数（markdown 渲染、文件处理、slug 生成）
├── task_worker.py         ← 后台任务队列 Worker（threading daemon）
├── requirements.txt       ← Python 依赖
├── .env.example           ← 环境变量模板
├── templates/             ← Jinja2 模板
│   ├── base.html          ← 基础布局（导航栏、侧边栏）
│   ├── index.html         ← 首页
│   ├── auth/
│   │   ├── login.html
│   │   └── register.html
│   ├── repo/
│   │   ├── list.html      ← 仓库列表
│   │   ├── new.html       ← 新建仓库
│   │   ├── dashboard.html ← 仓库面板
│   │   └── settings.html  ← 仓库设置
│   ├── wiki/
│   │   ├── page.html      ← Wiki 页面渲染
│   │   └── graph.html     ← 关系图
│   ├── source/
│   │   ├── list.html      ← 来源列表
│   │   └── view.html      ← 查看来源
│   └── ops/
│       ├── query.html     ← 查询界面
│       ├── lint.html      ← 维护报告
│       └── tasks.html     ← 任务队列看板
├── static/
│   ├── css/
│   │   └── style.css      ← 样式（使用 Pico CSS 或类似极简框架）
│   └── js/
│       ├── app.js         ← 通用交互（SSE 监听、markdown 预览）
│       └── graph.js       ← D3.js 力导向图
└── data/                  ← 用户数据根目录（.gitignore）
```

### 8.2 依赖清单

```
flask>=3.0
flask-login>=0.6
flask-sqlalchemy>=3.1
pymysql>=1.1
openai>=1.0
qdrant-client>=1.9
python-dotenv>=1.0
markdown>=3.5
pygments>=2.17
httpx>=0.27
pyyaml>=6.0
trafilatura>=1.12
```

**为什么选择这些**：
- `flask` + `flask-login`：Web 框架 + 会话认证，最简组合
- `flask-sqlalchemy` + `pymysql`：ORM + MySQL 驱动，比原生 SQL 更安全易维护
- `openai`：OpenAI 兼容接口的 Python SDK，支持 base_url 自定义；同时用于调用 Embedding 模型
- `qdrant-client`：Qdrant 向量数据库的官方 Python SDK
- `markdown` + `pygments`：Markdown 渲染 + 语法高亮
- `httpx`：HTTP 客户端，用于调用 MinerU API
- `pyyaml`：解析 Wiki 页面的 YAML frontmatter

### 8.3 异步处理

摄入操作可能耗时较长（多步 LLM 调用）。采用**后台任务队列 + SSE 进度轮询**：

**架构**：
- 上传文件后自动创建 `status=queued` 的摄入任务
- `TaskWorker`（Python daemon thread）轮询 DB 取任务执行
- 多 gunicorn worker 下用乐观锁（`UPDATE ... WHERE status='queued'`）防重复
- SSE 端点轮询 DB 读进度，不在 HTTP 线程内执行 LLM

**任务状态流转**：`queued → running → done / failed`

**进度追踪**：Task 表的 `progress`（0-100）和 `progress_msg` 字段由 Worker 实时更新，前端通过 SSE 或 JSON API 轮询展示。

**任务队列看板**（`/{user}/{repo}/tasks`）：显示所有任务的状态、进度条、耗时，JS 自动刷新。

### 8.4 Markdown 渲染增强

Wiki 页面需要特殊处理：

1. **Wiki 链接转换**：`[标题](page.md)` → 转为站内路由链接
2. **YAML Frontmatter 提取**：解析并在页面顶部显示元数据
3. **反向链接收集**：解析所有页面的链接，为当前页面构建「被引用」列表
4. **语法高亮**：代码块使用 Pygments
5. **目录生成**：长页面自动生成 TOC

### 8.5 MinerU 文档解析集成

通过 HTTP 调用已部署的 MinerU 服务（http://172.36.237.175:8000）完成文档解析。

**API 端点**：
- `POST /file_parse` — 同步解析（上传文件，等待返回 markdown）
- `POST /tasks` — 异步解析（返回 task_id，适合大文件）
- `GET /tasks/{task_id}` — 查询异步任务状态
- `GET /tasks/{task_id}/result` — 获取异步任务结果
- `GET /health` — 健康检查

**解析流程**：

```python
# mineru_client.py 核心逻辑
import os

class MineruClient:
    def __init__(self, base_url):
        self.base_url = base_url

    def parse_file(self, file_path: str) -> dict:
        """同步解析文件，返回包含 md_content 的字典"""
        with open(file_path, 'rb') as f:
            response = httpx.post(
                f"{self.base_url}/file_parse",
                files=[("files", (os.path.basename(file_path), f))],
                data={"return_md": "true"},
                timeout=300
            )
        return self._extract_md(response.json())

    def health_check(self) -> bool:
        resp = httpx.get(f"{self.base_url}/health")
        return resp.status_code == 200
```

**文件类型路由**：
- `.md` / `.txt` → 直接读取内容，存入 raw/
- `.pdf` / `.docx` / `.pptx` / `.png` / `.jpg` → 上传到 MinerU → 拿回 markdown → 存入 raw/（后缀为 `.pdf.md` 等）
- MinerU 提取的图片 → 存入 raw/assets/

**配置**（环境变量）：
```
MINERU_API_URL=http://172.36.237.175:8000
MINERU_TIMEOUT=300
```

### 8.6 Qdrant 向量检索集成

Qdrant 作为双通道查询中的语义检索通道，同时在摄入时建立向量索引。

**Collection 设计**：每个仓库对应一个 Qdrant collection，命名为 `repo_{repo_id}`。

```python
# qdrant_service.py 核心逻辑

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from openai import OpenAI

class QdrantService:
    def __init__(self, qdrant_url, embedding_client):
        self.client = QdrantClient(url=qdrant_url)
        self.embedding = embedding_client

    def ensure_collection(self, repo_id: int, vector_size: int = 1024):
        """确保 collection 存在，不存在则创建"""
        name = f"repo_{repo_id}"
        if not self.client.collection_exists(name):
            self.client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(
                    size=vector_size,
                    distance=Distance.COSINE
                )
            )

    def upsert_page(self, repo_id: int, filename: str, title: str,
                     page_type: str, content: str):
        """将 wiki 页面向量化后写入 Qdrant（按 filename 去重）"""
        vector = self._embed(content)
        point = PointStruct(
            id=self._stable_id(repo_id, filename),
            vector=vector,
            payload={
                "repo_id": repo_id,
                "filename": filename,
                "title": title,
                "type": page_type,
                "content": content[:5000],  # 截断，用于回显
            }
        )
        self.client.upsert(
            collection_name=f"repo_{repo_id}",
            points=[point]
        )

    def search(self, repo_id: int, query: str, limit: int = 10):
        """语义检索，返回最相关的 wiki 页面"""
        vector = self._embed(query)
        results = self.client.search(
            collection_name=f"repo_{repo_id}",
            query_vector=vector,
            limit=limit
        )
        return [
            {"filename": r.payload["filename"],
             "title": r.payload["title"],
             "score": r.score}
            for r in results
        ]

    def delete_collection(self, repo_id: int):
        """删除仓库时清理 collection"""
        self.client.delete_collection(f"repo_{repo_id}")

    def _embed(self, text: str) -> list[float]:
        """调用 Embedding 模型"""
        resp = self.embedding.embeddings.create(
            model=self.embedding_model,
            input=text
        )
        return resp.data[0].embedding

    def _stable_id(self, repo_id: int, filename: str) -> str:
        """生成稳定的 point ID，同一页面 upsert 时覆盖旧向量"""
        import hashlib
        raw = f"{repo_id}:{filename}"
        return hashlib.md5(raw.encode()).hexdigest()
```

**Embedding 模型**：通过 OpenAI 兼容接口调用，可独立配置（与 LLM 用不同模型/地址）：
```
EMBEDDING_API_BASE=http://172.36.237.245:11434/v1
EMBEDDING_API_KEY=
EMBEDDING_MODEL=bge-m3          # 或其他 embedding 模型
EMBEDDING_DIMENSIONS=1024
```

**配置**：
```
QDRANT_URL=http://localhost:6333    # Qdrant 服务地址
```

**与 Wiki 生命周期的对应关系**：

| Wiki 操作 | Qdrant 操作 |
|-----------|-------------|
| 创建仓库 | 创建 collection |
| 摄入 — 新建 wiki 页面 | upsert point |
| 摄入 — 更新 wiki 页面 | upsert point（覆盖旧向量） |
| 查询 — 页面定位 | search（与 Wiki 路径并行） |
| Lint — 修复页面 | upsert point（同步更新） |
| 保存回答为 wiki 页面 | upsert point |
| 删除仓库 | 删除 collection |

## 9. 默认 Schema 模板

新建仓库时自动生成的 `schema.md`：

```markdown
# Wiki Schema

## 结构
本 Wiki 遵循 LLM Wiki 模式。页面按以下类型组织：

- **index.md** — 所有页面的分类目录，每个条目包含链接和一句话摘要
- **log.md** — 按时间线记录的操作日志（追加式）
- **overview.md** — 全局综述，综合所有来源的高层观点
- **来源摘要页** — 每个摄入的来源对应一个摘要页面
- **实体页** — 关键实体（人物、组织、产品等）各一个页面
- **概念页** — 核心概念和主题各一个页面
- **分析页** — 对比分析、综合讨论等衍生内容

## 命名约定
- 文件名使用小写英文 + 连字符：`attention-mechanism.md`
- 如果主题是中文，文件名用拼音或英文翻译

## 交叉引用
- 使用标准 markdown 链接：`[页面标题](page-name.md)`
- 首次提及一个有独立页面的实体/概念时，必须链接

## 摄入工作流
处理新文档时：
1. 阅读完整文档，理解核心内容
2. 创建来源摘要页（source-xxx.md）
3. 识别关键实体和概念
4. 为新实体创建页面，为已有实体更新页面
5. 更新 overview.md，整合新信息
6. 标注任何与已有 Wiki 内容的矛盾
7. 更新 index.md
8. 追加 log.md

## 查询工作流
回答问题时：
1. 阅读 index.md 定位相关页面
2. 阅读相关页面内容
3. 综合回答，引用具体页面
4. 如果信息不足，明确告知

## 页面格式
每个 Wiki 页面遵循此格式：

  ---
  title: 页面标题
  type: entity | concept | source | topic | analysis
  created: YYYY-MM-DD
  updated: YYYY-MM-DD
  sources:
    - 来源文件名.md
  ---

  # 页面标题

  正文内容。
```

## 10. 默认初始文件

新建仓库时自动创建的 Wiki 文件：

**wiki/index.md**：
```markdown
# Wiki Index

> 本文件是 Wiki 的内容目录，由 LLM 自动维护。

## 概览
- [Overview](overview.md) — 全局综述（尚无来源）

## 来源摘要
（暂无）

## 实体
（暂无）

## 概念
（暂无）

## 分析
（暂无）
```

**wiki/log.md**：
```markdown
# Wiki Log

> 本文件是操作的时间线记录，由 LLM 自动维护。每条记录按时间倒序排列。

## [YYYY-MM-DD] init | 仓库创建
仓库初始化完成。
```

**wiki/overview.md**：
```markdown
---
title: Overview
type: topic
created: YYYY-MM-DD
updated: YYYY-MM-DD
sources: []
---

# Overview

本 Wiki 尚无摄入的来源。添加第一个文档开始构建知识库。
```

## 11. 用户系统与权限

### 11.1 注册与认证

- **开放注册**：任何人可访问注册页创建账号
- **Flask-Login** 管理会话（基于 cookie 的 session）
- **werkzeug.security** 的 `generate_password_hash` / `check_password_hash` 处理密码

注册时需要填写：
- 用户名（唯一，用于 URL 路径如 `/{username}/{repo}`）
- 显示名称
- 密码（二次确认）

### 11.2 用户功能

| 功能 | 说明 |
|------|------|
| 登录 | 用户名 + 密码 |
| 注册 | 开放，填写用户名 / 显示名称 / 密码 |
| 登出 | 清除 session |
| 修改显示名称 | 个人设置页 |
| 修改密码 | 需验证旧密码 |

### 11.3 权限模型

| 操作 | 谁可以 |
|------|--------|
| 浏览任意仓库的 Wiki | 所有登录用户 |
| 查看原始文档 | 所有登录用户 |
| 向仓库提问（Query） | 所有登录用户 |
| 创建仓库 | 自己 |
| 上传文档 / 触发摄入 / Lint | 仅仓库创建者 |
| 修改 Schema | 仅仓库创建者 |
| 删除仓库 | 仅仓库创建者 |
| 修改个人信息 / 密码 | 仅本人 |

### 11.4 暂不实现

- OAuth / SSO
- 仓库协作（多人编辑同一仓库）
- API Token
- 管理员角色 / 后台管理

这些在需要时可以增量添加。

## 12. 工程化

### 12.1 项目初始化

首次运行需要完成环境初始化。通过 `Makefile` 统一管理常用命令：

```makefile
.PHONY: init dev prod migrate test lint clean deploy

# 首次初始化：创建虚拟环境、安装依赖、建表、创建数据目录
init:
	python -m venv .venv
	.venv/bin/pip install -r requirements.txt
	.venv/bin/python manage.py init-db
	mkdir -p data

# 开发模式启动（热重载）
dev:
	.venv/bin/flask run --debug --host 0.0.0.0 --port 5000

# 生产模式启动
prod:
	.venv/bin/gunicorn -w 2 -b 0.0.0.0:5000 app:app

# 数据库迁移
migrate:
	.venv/bin/python manage.py migrate

# 运行测试
test:
	.venv/bin/pytest tests/ -v

# 代码检查
lint:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .

# 代码格式化
format:
	.venv/bin/ruff format .

# 清理
clean:
	rm -rf __pycache__ .pytest_cache .venv

# 部署到服务器
deploy:
	./scripts/deploy.sh
```

### 12.2 manage.py 管理脚本

统一的项目管理入口，处理初始化、迁移等运维命令：

```python
# manage.py
import click
from app import create_app, db

@click.group()
def cli():
    pass

@cli.command()
def init_db():
    """初始化数据库（建表）"""
    app = create_app()
    with app.app_context():
        db.create_all()
    click.echo("数据库初始化完成。")

@cli.command()
def migrate():
    """数据库迁移（增量变更）"""
    # 简单方案：读取 migrations/ 目录下的 SQL 文件按序执行
    pass

@cli.command()
@click.argument('username')
@click.argument('password')
def create_user(username, password):
    """通过命令行创建用户"""
    pass

@cli.command()
def check():
    """检查外部依赖连通性"""
    # 检查 MySQL、MinerU、Qdrant、Embedding API 是否可达
    pass
```

### 12.3 数据库迁移

不引入 Alembic（太重），采用手动 SQL 迁移文件：

```
migrations/
├── 001_init.sql          ← 初始建表
├── 002_add_xxx.sql       ← 后续变更
└── ...
```

`manage.py migrate` 读取已执行过的版本号（存在 `schema_version` 表中），按序执行新的迁移文件。简单可控。

### 12.4 日志

使用 Python 标准 `logging` 模块，按模块分 logger：

```python
# config.py 中配置
import logging

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

logging.basicConfig(format=LOG_FORMAT, level=LOG_LEVEL)
```

| Logger 名称 | 记录内容 |
|-------------|---------|
| `app` | 请求、路由、认证 |
| `wiki_engine` | 摄入/查询/维护的每个步骤和耗时 |
| `llm_client` | LLM API 调用（请求摘要、token 用量、耗时） |
| `mineru_client` | MinerU 调用（文件名、耗时、成功/失败） |
| `qdrant_service` | 向量操作（upsert/search、耗时） |

生产环境日志输出到文件：
```
LOG_FILE=./logs/app.log
LOG_LEVEL=INFO
```

### 12.5 错误处理

**分层错误处理策略**：

```python
# 自定义异常
class LLMWikiError(Exception):
    """基类"""
    pass

class LLMClientError(LLMWikiError):
    """LLM API 调用失败"""
    pass

class MineruClientError(LLMWikiError):
    """MinerU 解析失败"""
    pass

class QdrantServiceError(LLMWikiError):
    """Qdrant 操作失败"""
    pass
```

**Flask 错误页面**：
```python
@app.errorhandler(404)
def not_found(e):
    return render_template("errors/404.html"), 404

@app.errorhandler(500)
def server_error(e):
    app.logger.error(f"Internal error: {e}", exc_info=True)
    return render_template("errors/500.html"), 500
```

**外部服务降级**：
- MinerU 不可用 → 提示用户"文档解析服务暂不可用"，仍可上传 .md/.txt
- Qdrant 不可用 → 查询退化为纯 Wiki 路径（只走 index.md），摄入跳过向量写入，记录待补
- LLM 不可用 → 核心功能不可用，明确提示，Wiki 浏览不受影响
- Embedding 不可用 → 同 Qdrant 不可用的降级策略

### 12.6 配置校验

应用启动时校验必要配置，快速失败：

```python
def validate_config():
    """启动时校验，缺少必要配置立即退出"""
    required = ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD",
                "LLM_API_BASE", "LLM_API_KEY", "LLM_MODEL"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"缺少必要环境变量: {', '.join(missing)}")
```

可选服务（MinerU、Qdrant、Embedding）启动时检测连通性，不可达时打印警告但不阻止启动。

### 12.7 代码质量

```
# requirements-dev.txt（开发依赖）
ruff>=0.4            # Linter + Formatter（替代 flake8 + black + isort）
pytest>=8.0          # 测试框架
pytest-cov>=5.0      # 覆盖率
```

**Ruff 配置**（`pyproject.toml`）：
```toml
[tool.ruff]
target-version = "py311"
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "W", "I"]

[tool.ruff.format]
quote-style = "double"
```

### 12.8 测试

```
tests/
├── conftest.py              ← 共享 fixtures（Flask test client、mock DB）
├── test_auth.py             ← 注册、登录、修改密码
├── test_repo.py             ← 仓库 CRUD
├── test_wiki_engine.py      ← 摄入、查询、维护（mock LLM 响应）
├── test_mineru_client.py    ← MinerU 调用（mock HTTP）
├── test_qdrant_service.py   ← Qdrant 操作（mock client）
└── test_utils.py            ← 工具函数
```

**测试策略**：
- 业务逻辑：单元测试，mock 外部服务（LLM、MinerU、Qdrant）
- 路由/视图：Flask test client 集成测试
- 外部服务客户端：mock HTTP 响应
- 不做端到端测试（内部系统，手动验证即可）

### 12.9 项目结构（完整版）

```
open-llm-wiki/
├── app.py                 ← Flask 应用工厂 + 路由注册
├── config.py              ← 配置加载 + 校验
├── manage.py              ← CLI 管理命令（init-db / migrate / check）
├── models.py              ← SQLAlchemy 模型
├── llm_client.py          ← LLM 客户端
├── wiki_engine.py         ← 核心 Wiki 操作
├── qdrant_service.py      ← Qdrant 向量检索
├── mineru_client.py       ← MinerU 文档解析
├── utils.py               ← 工具函数
├── task_worker.py         ← 后台任务 Worker
├── exceptions.py          ← 自定义异常
├── Makefile               ← 常用命令
├── pyproject.toml         ← Ruff / 项目元数据
├── requirements.txt       ← 生产依赖
├── requirements-dev.txt   ← 开发依赖
├── .env                   ← 环境变量（不提交）
├── .env.example           ← 环境变量模板
├── .gitignore
├── migrations/            ← SQL 迁移文件
│   └── 001_init.sql
├── scripts/
│   └── deploy.sh          ← 部署脚本
├── templates/
│   ├── base.html
│   ├── errors/
│   │   ├── 404.html
│   │   └── 500.html
│   ├── auth/
│   ├── repo/
│   ├── wiki/
│   ├── source/
│   └── ops/
│       ├── query.html
│       ├── lint.html
│       └── tasks.html    ← 任务队列看板
├── static/
│   ├── css/
│   └── js/
├── tests/                 ← 测试
├── logs/                  ← 日志文件（不提交）
├── data/                  ← 用户数据（不提交）
└── docs/
    └── design.md
```

### 12.10 部署

**部署目标**：`172.36.164.85`（Anolis OS 8.9, x86_64）

**部署脚本**（`scripts/deploy.sh`）：
```bash
#!/bin/bash
set -e

REMOTE_USER=$DEPLOY_USER
REMOTE_HOST=$DEPLOY_HOST
REMOTE_PORT=$DEPLOY_PORT
REMOTE_DIR=/opt/open-llm-wiki

echo "打包项目..."
tar czf /tmp/open-llm-wiki.tar.gz \
    --exclude='.git' --exclude='data' --exclude='.venv' \
    --exclude='__pycache__' --exclude='logs' --exclude='.env' \
    -C "$(dirname "$0")/.." .

echo "上传到服务器..."
scp -P $REMOTE_PORT /tmp/open-llm-wiki.tar.gz $REMOTE_USER@$REMOTE_HOST:/tmp/

echo "部署..."
ssh -p $REMOTE_PORT $REMOTE_USER@$REMOTE_HOST << 'EOF'
    mkdir -p /opt/open-llm-wiki
    cd /opt/open-llm-wiki
    tar xzf /tmp/open-llm-wiki.tar.gz
    python3 -m venv .venv 2>/dev/null || true
    .venv/bin/pip install -r requirements.txt -q
    .venv/bin/python manage.py migrate
    # 重启服务（systemd）
    systemctl restart open-llm-wiki
    rm /tmp/open-llm-wiki.tar.gz
EOF

echo "部署完成。"
```

**Systemd 服务文件**（`/etc/systemd/system/open-llm-wiki.service`）：
```ini
[Unit]
Description=Open LLM Wiki
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/open-llm-wiki
EnvironmentFile=/opt/open-llm-wiki/.env
ExecStart=/opt/open-llm-wiki/.venv/bin/gunicorn -w 2 -b 0.0.0.0:5000 app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**外部依赖**：
- MinerU 服务（http://172.36.237.175:8000）——文档解析
- MySQL（172.36.164.85:3306）——元数据存储
- Qdrant（http://172.36.164.85:6333）——向量检索
- Embedding 模型（http://172.36.237.245:11434/v1）——文本向量化

### 12.11 健康检查

提供 `/health` 端点，检查所有依赖状态：

```json
GET /health

{
  "status": "ok",
  "services": {
    "mysql": "ok",
    "qdrant": "ok",
    "mineru": "ok",
    "embedding": "ok"
  },
  "version": "0.1.0"
}
```

任何一个服务不可达时 status 为 `degraded`（而非 `error`，因为有降级策略）。

## 13. 验证与评估框架

系统的核心价值在于：摄入质量、检索准确率、回答质量。需要一套可量化的评估方案来验证系统是否正常工作，以及调优 Prompt 模板、模型选择、检索参数时提供依据。

### 13.1 评估维度

| 维度 | 评什么 | 为什么重要 |
|------|--------|-----------|
| **摄入质量** | LLM 生成的 Wiki 页面是否准确、完整 | 知识编译是整个系统的基础 |
| **检索准确率** | 给定问题，能否找到正确的 Wiki 页面 | 找不到页面，回答就无从谈起 |
| **回答质量** | 最终回答是否准确、全面、有引用 | 用户直接感知的价值 |
| **双通道对比** | Wiki 路径 vs Qdrant 路径各自的贡献 | 验证双通道架构的必要性 |

### 13.2 测试语料设计

选取一个聚焦领域（如 LLM 技术），准备以下材料：

**测试文档集**（6-10 篇，存放在 `eval/corpus/`）：
- 覆盖多个相关主题，形成实体和概念的交叉网络
- 刻意设计文档间的**重叠**（如两篇都谈注意力机制，但侧重不同）
- 刻意设计文档间的**矛盾**（如一篇认为 RAG 足够，另一篇认为需要 LLM Wiki）
- 包含可提取的具体事实（人名、日期、数字），方便验证准确性

**Ground Truth 问答对**（15-20 个，存放在 `eval/ground_truth/questions.json`）：

每个问答对包含：
```json
{
  "id": "q01",
  "question": "问题文本",
  "difficulty": "easy|medium|hard",
  "preferred_path": "wiki|qdrant|both",
  "expected_pages": ["应该命中的 wiki 页面列表"],
  "answer_key_points": ["答案应包含的要点"],
  "source_docs": ["对应的原始文档"]
}
```

问题设计要覆盖不同类型：

| 类型 | 适合路径 | 示例 |
|------|---------|------|
| 概述型 | Wiki | "Transformer 的核心创新是什么？" |
| 精确查找 | Qdrant | "哪篇文档提到了 FlashAttention？" |
| 模糊回忆 | Qdrant | "我记得有个降低注意力显存的技术..." |
| 多文档综合 | Both | "RAG 和 LLM Wiki 的根本区别？" |
| 矛盾检测 | Wiki | "不同文档间存在哪些矛盾观点？" |
| 全局综述 | Wiki | "给出这个领域的整体概述" |

**预期实体与交叉引用**（存放在 `eval/ground_truth/entities.json`）：
- 摄入后应该出现的实体页和概念页列表
- 预期的交叉引用关系
- 预期被标记的矛盾

### 13.3 评估指标

**摄入质量指标**：

| 指标 | 计算方式 | 目标 |
|------|---------|------|
| 实体召回率 | 实际生成的实体页 ÷ 预期实体页 | ≥ 80% |
| 交叉引用完整度 | 实际交叉引用 ÷ 预期交叉引用 | ≥ 70% |
| 矛盾检出率 | 实际标记的矛盾 ÷ 预期矛盾 | ≥ 50% |
| 页面准确性 | LLM-as-Judge 打分（1-5） | ≥ 4.0 |

**检索准确率指标**：

| 指标 | 计算方式 | 说明 |
|------|---------|------|
| Recall@K | 命中的预期页面数 ÷ 预期页面总数 | K=10，衡量是否找全 |
| Precision@K | 命中的预期页面数 ÷ 返回的页面总数 | 衡量是否精准 |
| Wiki 路径独占贡献 | 仅 Wiki 路径找到、Qdrant 未找到的页面数 | 验证 Wiki 路径价值 |
| Qdrant 路径独占贡献 | 仅 Qdrant 找到、Wiki 路径未找到的页面数 | 验证 Qdrant 路径价值 |

**回答质量指标**（LLM-as-Judge）：

| 维度 | 评分标准 | 权重 |
|------|---------|------|
| 准确性 | 答案要点是否被覆盖（对照 answer_key_points） | 40% |
| 完整性 | 是否遗漏重要信息 | 25% |
| 引用正确性 | 引用的 Wiki 页面是否确实包含相关内容 | 20% |
| 表达质量 | 是否清晰、有条理 | 15% |

### 13.4 评估流程

```
Step 1: 初始化
  创建测试仓库
  按顺序摄入 eval/corpus/ 中的文档

Step 2: 摄入质量评估
  检查 wiki/ 目录下的页面
  对照 entities.json 统计实体召回率、交叉引用完整度、矛盾检出率

Step 3: 检索准确率评估
  对每个问题分别执行：
    a) 仅 Wiki 路径 → 记录命中页面
    b) 仅 Qdrant 路径 → 记录命中页面
    c) 双通道合并 → 记录命中页面
  对照 expected_pages 计算 Recall/Precision

Step 4: 回答质量评估
  对每个问题执行完整查询流程
  用另一个 LLM（或同模型不同温度）作为 Judge 打分
  对照 answer_key_points 评估覆盖率

Step 5: 生成报告
  输出到 eval/results/，包含各项指标和对比分析
```

### 13.5 对比实验

通过评估框架可以做以下对比：

| 实验 | 对比项 | 目的 |
|------|--------|------|
| 单通道 vs 双通道 | 仅 Wiki / 仅 Qdrant / 双通道 | 验证双通道架构的价值 |
| 不同 LLM 模型 | DeepSeek-V3 vs 其他模型 | 选择最佳模型 |
| 不同 Prompt 模板 | 摄入 Prompt A vs B | 优化 Prompt |
| 不同 Embedding 模型 | bge-m3 vs 其他 | 选择最佳 Embedding |
| 不同 Top-K | K=5 / K=10 / K=20 | 找到最佳检索数量 |

### 13.6 eval/ 目录结构

```
eval/
├── corpus/                    ← 测试文档（后续构建）
│   ├── doc-01-xxx.md
│   └── ...
├── ground_truth/              ← 标准答案
│   ├── questions.json         ← 问答对 + 预期命中页面
│   └── entities.json          ← 预期实体 + 交叉引用 + 矛盾
├── scripts/
│   ├── run_eval.py            ← 评估主入口
│   ├── eval_ingest.py         ← 摄入质量评估
│   ├── eval_retrieval.py      ← 检索准确率评估
│   └── eval_answer.py         ← 回答质量评估（LLM-as-Judge）
└── results/                   ← 评估结果输出
    └── report-YYYY-MM-DD.json
```

## 14. 未来可扩展方向（不在初始范围内）

- **Obsidian 兼容**：用户可以用 Obsidian 直接打开 data/{user}/{repo}/ 目录浏览 Wiki
- **批量摄入**：一次上传多个文档，排队自动处理
- **仓库协作**：多人共同维护一个仓库
- **导出功能**：生成 Marp 幻灯片、PDF 报告
- **Webhook**：摄入完成后通知
- **Schema 模板市场**：为不同场景（研究、读书、竞品分析）提供预设 Schema
- **BM25 混合检索**：在 Qdrant 语义检索基础上叠加关键词检索，进一步提升召回率
