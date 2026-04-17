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
| 检索增强 | Qdrant 页面/Chunk/Fact 检索 + LLM Wiki 结构化导航，叙事层与事实层互补 |

## 3. 三层架构（per repo）

每个仓库严格遵循 Karpathy 定义的三层结构：

```
{username}/{repo-slug}/
├── schema.md          ← 第三层：Schema，控制 LLM 行为的配置文档
├── raw/               ← 第一层：原始文档（不可变，LLM 只读不写）
│   ├── assets/        ← 图片等附件（MinerU 提取的图片）
│   ├── originals/     ← 表格/二进制原始文件备份（仅上传时保留）
│   ├── article-1.md
│   ├── paper.pdf.md   ← PDF 上传时由 MinerU 转为 markdown
│   └── ...
├── facts/
│   └── records/       ← 结构化事实层（JSONL，按行/记录保真存储）
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
- CSV (.csv)——转换为 Markdown 展示稿 + `facts/records/*.jsonl` 行级 records
- Excel (.xlsx/.xls)——用 openpyxl 解析，转换为 Markdown 表格（支持多 Sheet）+ `facts/records/*.jsonl`

其中 Markdown 用于 Wiki 叙事层摄入，JSONL records 用于 Fact Layer 保真检索；原始表格文件会保存在 `raw/originals/` 便于回溯。

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
  若本次来源存在 `facts/records/<source>.jsonl`：
    将每条 record 向量化
    → 写入 `repo_{id}_facts`

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

采用**叙事层 + 事实层三路证据检索**——LLM Wiki 结构化导航 + Qdrant chunk + Qdrant fact 互补：

```
                      用户提问
                         │
            ┌────────────┬────────────┐
            ▼            ▼            ▼
    Wiki 结构化路径   Qdrant Chunk   Qdrant Fact
    LLM 读 index.md  原文片段检索     行级 record 检索
    选出相关页面      返回 Top-N 段落   返回 Top-N 结构化事实
            └────────────┬────────────┘
                         ▼
                  合并证据，计算规则化置信度
                         │
                         ▼
              LLM 阅读页面内容 + 片段 + facts 综合回答
                         │
                         ▼
        返回 wiki_evidence + chunk_evidence + fact_evidence + confidence
        写入 query_logs 供后续分析
```

**Phase 2 升级（Fact Layer 落地）：**
- Qdrant 现采用**三层 collection**：
  - `repo_{id}`：页面级向量（全文，用于 Wiki 路径辅助检索）
  - `repo_{id}_chunks`：段落级向量（400-800 字，用于 chunk 证据检索）
  - `repo_{id}_facts`：结构化 record 向量（行/记录级，用于精确事实检索）
- 查询方法升级为 `query_with_evidence()`，返回 `wiki_evidence`、`chunk_evidence`、`fact_evidence`、`confidence`
- 置信度基于规则打分（0.0-1.0），分级为 `high/medium/low`，写入 `query_logs`
- SSE `done` 事件同步返回置信度和三路证据
- 新增 `manage.py rebuild-chunk-index` 命令用于存量数据回填
- 新增 `manage.py rebuild-fact-index` 命令用于存量 records 回填

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
    event: done  (answer, markdown, confidence, wiki_evidence, chunk_evidence, fact_evidence, evidence_summary, wiki_sources, qdrant_sources, referenced_pages)
        │
        ▼ 前端用 done 中的 markdown 调用 POST /query（_rendered_answer 模式）渲染 HTML + 证据面板
```

- **后端**：`WikiEngine.query_stream()` 为生成器，`LLMClient.chat_stream()` 使用 `stream=True`；Flask 路由使用 `stream_with_context` + `mimetype=text/event-stream`。`done` 事件中的 `wiki_evidence` / `chunk_evidence` 链接必须输出为仓库级 Wiki 完整路径（`/{username}/{repo}/wiki/{page_slug}`）；`fact_evidence` 链接输出为来源页完整路径（`/{username}/{repo}/sources/{source_markdown_filename}`）。
- **前端**：`chat.js` 优先使用 `EventSource`；`answer_chunk` 事件实时更新加载气泡；`done` 后调用 `POST /query`（`_rendered_answer` 模式）获取完整渲染 HTML，并展示置信度 badge + 三路证据面板。
- **降级**：`queryStreamUrl` 缺失时自动回退到原 POST 轮询模式。
- **渲染复用**：`POST /query` 若请求体含 `_rendered_answer`，则跳过 LLM 调用，直接渲染 Markdown 返回 HTML，同时返回 `confidence`、`wiki_evidence`、`chunk_evidence`、`fact_evidence` 字段。该分支仍需像普通查询一样写入 `conversation_sessions`，以保证流式查询不会丢失历史消息，也不会让会话标题在重新加载后退回“新对话”。

**Phase 1 新增 query API 返回字段：**
```json
{
  "html": "...",
  "markdown": "...",
  "answer": "...",
  "confidence": {"level": "high|medium|low", "score": 0.85, "reasons": ["..."]},
  "wiki_evidence": [{"filename": "...", "title": "...", "type": "...", "url": "...", "reason": "..."}],
  "chunk_evidence": [{"chunk_id": "...", "filename": "...", "title": "...", "heading": "...", "url": "...", "snippet": "...", "score": 0.92}],
  "fact_evidence": [{"record_id": "...", "source_file": "...", "source_markdown_filename": "...", "sheet": "...", "row_index": 12, "fields": {"地区": "华东"}, "snippet": "...", "score": 0.96, "url": "..."}],
  "evidence_summary": "本回答基于 N 个 Wiki 页面、M 个原文片段和 K 条结构化事实生成。",
  "wiki_sources": [...],
  "qdrant_sources": [...]
}
```

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
    email         VARCHAR(255) UNIQUE NULL,
    email_verified TINYINT(1) NOT NULL DEFAULT 0,
    email_verified_at DATETIME NULL,
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

CREATE TABLE repo_share_codes (
    id                 INT AUTO_INCREMENT PRIMARY KEY,
    repo_id            INT NOT NULL,
    code               VARCHAR(32) NOT NULL UNIQUE,
    role               VARCHAR(16) NOT NULL DEFAULT 'viewer',
    created_by_user_id INT NULL,
    use_count          INT NOT NULL DEFAULT 0,
    is_active          TINYINT(1) NOT NULL DEFAULT 1,
    created_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_rsc_repo (repo_id),
    INDEX idx_rsc_creator (created_by_user_id),
    FOREIGN KEY (repo_id) REFERENCES repos(id),
    FOREIGN KEY (created_by_user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE repo_members (
    id                 INT AUTO_INCREMENT PRIMARY KEY,
    repo_id            INT NOT NULL,
    user_id            INT NOT NULL,
    role               VARCHAR(16) NOT NULL DEFAULT 'viewer',
    granted_by_user_id INT NULL,
    share_code_id      INT NULL,
    created_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_repo_member_repo_user (repo_id, user_id),
    INDEX idx_rm_repo (repo_id),
    INDEX idx_rm_user (user_id),
    INDEX idx_rm_granted_by (granted_by_user_id),
    INDEX idx_rm_share_code (share_code_id),
    FOREIGN KEY (repo_id) REFERENCES repos(id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (granted_by_user_id) REFERENCES users(id),
    FOREIGN KEY (share_code_id) REFERENCES repo_share_codes(id)
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
    cancel_requested TINYINT(1) NOT NULL DEFAULT 0,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    started_at    DATETIME,
    finished_at   DATETIME,
    FOREIGN KEY (repo_id) REFERENCES repos(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Phase 1 新增：查询日志表
CREATE TABLE query_logs (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    repo_id          INT NOT NULL,
    user_id          INT NULL,
    question         TEXT NOT NULL,
    answer_preview   TEXT,
    confidence       VARCHAR(16) NOT NULL DEFAULT 'low',
    wiki_hit_count   INT NOT NULL DEFAULT 0,
    chunk_hit_count  INT NOT NULL DEFAULT 0,
    used_wiki_pages  LONGTEXT,
    used_chunk_ids   LONGTEXT,
    evidence_summary TEXT,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_ql_repo (repo_id),
    INDEX idx_ql_user (user_id),
    CONSTRAINT fk_ql_repo FOREIGN KEY (repo_id) REFERENCES repos(id),
    CONSTRAINT fk_ql_user FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Wave 3 新增：多轮对话会话表
CREATE TABLE conversation_sessions (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    repo_id        INT NOT NULL,
    user_id        INT NOT NULL,
    session_key    VARCHAR(64) NOT NULL,
    title          VARCHAR(255) NOT NULL DEFAULT '新对话',
    messages_json  LONGTEXT NOT NULL,
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_cs_repo (repo_id),
    INDEX idx_cs_user (user_id),
    INDEX idx_cs_key (session_key),
    CONSTRAINT fk_cs_repo FOREIGN KEY (repo_id) REFERENCES repos(id),
    CONSTRAINT fk_cs_user FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Wave 3 新增：审计日志表
CREATE TABLE audit_logs (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    user_id       INT,
    username      VARCHAR(64),
    action        VARCHAR(64) NOT NULL,
    resource_type VARCHAR(32),
    resource_id   VARCHAR(128),
    detail        TEXT,
    ip            VARCHAR(64),
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_al_user (user_id),
    INDEX idx_al_action (action),
    INDEX idx_al_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Wave 3 新增：API Token 表
CREATE TABLE api_tokens (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    user_id      INT NOT NULL,
    name         VARCHAR(128) NOT NULL,
    token_hash   VARCHAR(256) NOT NULL UNIQUE,
    last_used_at DATETIME,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active    TINYINT(1) NOT NULL DEFAULT 1,
    INDEX idx_at_user (user_id),
    CONSTRAINT fk_at_user FOREIGN KEY (user_id) REFERENCES users(id)
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
GET  /verify-email/{token}               → 邮箱验证
GET  /forgot-password                    → 找回密码页
POST /forgot-password                    → 发送重置邮件
GET  /reset-password/{token}             → 重置密码页
POST /reset-password/{token}             → 提交新密码
GET  /logout                             → 登出

用户：
GET  /user/settings                      → 个人设置页（显示名称、邮箱、密码）
POST /user/settings                     → 更新个人资料 / 修改密码 / 删除当前账号
GET  /user/settings/tokens               → API Token 管理页
POST /user/settings/tokens/create        → 创建 API Token
POST /user/settings/tokens/{id}/revoke   → 吊销 API Token

仓库管理：
GET  /                                   → 首页（已登录则跳转仓库列表）
GET  /guide                              → 使用教程（`templates/guide.html`，顶栏「使用教程」文字按钮）
GET  /{username}                         → 用户的仓库列表（需登录，未登录重定向到登录页）
POST /repos/join                         → 通过访问码挂载共享知识库到当前用户列表
POST /shared-repos/{repo_id}/leave       → 退出共享知识库
POST /{username}/repos                   → 创建新仓库
GET  /{username}/{repo}                  → 仓库面板（Wiki 概览 + README）
POST /{username}/{repo}/delete           → 删除仓库
GET  /{username}/{repo}/settings         → 仓库设置
POST /{username}/{repo}/import.zip       → 从 ZIP 导入/恢复整个知识库
POST /{username}/{repo}/members/{id}/delete → owner 移除共享成员
POST /{username}/{repo}/share-codes/{id}/disable → owner 停用访问码

Wiki 浏览：
GET  /{username}/{repo}/wiki/{page}      → 查看 Wiki 页面（公开库可访客访问）
GET  /{username}/{repo}/wiki/search      → Wiki 全文关键词搜索
GET  /{username}/{repo}/wiki/export.zip  → 导出 Wiki 为 ZIP
GET  /{username}/{repo}/graph            → 链接关系图
GET  /{username}/{repo}/search/semantic  → 语义检索（向量相似度）

原始文档：
GET  /{username}/{repo}/sources          → 原始文档列表（公开库可访客访问）
GET  /{username}/{repo}/sources/{file}   → 查看原始文档（公开库可访客访问）
POST /{username}/{repo}/sources/upload   → 上传文档（owner/editor，可重复检测）
GET  /{username}/{repo}/sources/{id}/download → 下载原始文件
POST /{username}/{repo}/sources/batch-delete → 批量删除文件（owner/editor）
POST /{username}/{repo}/sources/batch-ingest → 批量摄入未处理文件（owner/editor）
POST /{username}/{repo}/sources/import-url   → 从 URL 导入网页（后端保留接口，前端不再暴露）

核心操作：
POST /{username}/{repo}/ingest/{file}    → 触发摄入（owner/editor）
GET  /{username}/{repo}/ingest/{task_id} → 摄入进度（SSE）
POST /api/tasks/{task_id}/retry          → 重试失败任务
POST /api/tasks/{task_id}/cancel         → 取消排队任务 / 请求终止运行中任务
GET  /{username}/{repo}/query            → 查询界面（公开库可访客访问）
POST /{username}/{repo}/query            → 提交查询（含多轮会话上下文）
GET  /{username}/{repo}/query/stream     → SSE 流式查询（EventSource，公开库可访客访问）
POST /{username}/{repo}/query/save       → 保存回答为 Wiki 页面（owner/editor）
GET  /{username}/{repo}/session?key=     → 获取会话历史
POST /{username}/{repo}/session/clear    → 清空会话
GET  /{username}/{repo}/sessions         → 列出该用户所有历史会话（JSON）
POST /{username}/{repo}/sessions/new     → 创建新会话（返回 session_key）
POST /{username}/{repo}/sessions/{key}/delete → 删除会话
POST /{username}/{repo}/sessions/{key}/rename → 重命名会话
POST /{username}/{repo}/lint             → 触发维护检查
POST /{username}/{repo}/lint/apply       → 应用修复建议
GET  /{username}/{repo}/tasks              → 任务队列看板
GET  /api/tasks/{task_id}/status           → 任务状态 JSON API
GET  /{username}/{repo}/insights           → 知识缺口分析（基于 query_logs）
GET  /{username}/{repo}/entity-check       → 实体去重检查

机密客户端本地 API：
GET  /api/bootstrap                               → 首屏仓库 / 活跃任务 bootstrap
POST /api/repositories                            → 创建本地机密知识库
POST /api/repositories/{repo_uuid}/documents      → 载入文档列表 + 活跃任务
POST /api/repositories/{repo_uuid}/documents/upload → 上传并加入本地处理队列
POST /api/repositories/{repo_uuid}/documents/delete → 删除文档，并同步清理本地文件与 Qdrant 索引
POST /api/repositories/{repo_uuid}/query          → 本地问答
GET  /api/tasks/{task_id}                         → 查询本地上传任务状态

说明：
- `POST /api/repositories` 支持 `storage_mode=encrypted|plain`
- `encrypted` 需要访问口令，`plain` 不加密本地 vault，也不要求口令

操作历史：
GET  /{username}/{repo}/log              → 查看操作日志（log.md）

Wiki 编辑：
GET/POST /{username}/{repo}/wiki/{page}/edit   → 编辑 Wiki 页面（owner/editor）
POST     /{username}/{repo}/wiki/{page}/delete → 删除 Wiki 页面（owner/editor）

全局搜索：
GET  /{username}/search                      → 全局跨仓库搜索（仅 owner，repo.global_search）

管理后台：
GET  /admin/                                 → 管理统计后台（仅 ADMIN_USERNAME，admin.dashboard）
GET  /admin/feedbacks                        → 用户反馈列表（关联 query_logs 时 MySQL 上对 trace_id 统一 COLLATE，避免混用排序规则报错）
```

### 6.2 页面设计

**前端 UI/UX**：全站基于 Pico CSS + 自定义样式（`static/css/style.css`、`static/css/chat.css`）。视觉方向为文档/知识库工具型 SaaS，采用 Inter 字体与蓝灰分层（B2 + S2）：页面外层使用蓝灰壳层背景，导航/侧栏使用略深一层的蓝灰面板，主内容区保持更亮的白色阅读面。组件分隔不只依赖极淡的 1px 线，而是通过更清晰的蓝灰边框、轻阴影、面板底色差共同建立层级。主操作与链接使用蓝色强调色（`#2563EB`），顶栏为半透明毛玻璃 sticky，页脚简述产品能力。交互上为可点击元素提供 `cursor-pointer`、`:focus-visible` 轮廓与 150–300ms 过渡；尊重 `prefers-reduced-motion`。图标统一为 Lucide（SVG），不用 emoji 作界面图标。

**顶栏**：右侧导航首项为「使用教程」文字按钮（无图标），链至 `/guide`。

**首页 / 仓库列表**：卡片式展示用户的知识库。当前登录用户访问自己的列表页时，顶部提供同一行的两个操作入口：「添加共享知识库」和「新建知识库」；前者通过弹窗输入访问码完成加入，后者直接进入新建页。列表分为「我的知识库」与「共享给我的知识库」两组，后者在卡片上显示 owner 和角色标签（只读 / 可编辑），并支持主动退出共享。

**仓库面板**：左侧是来源文件与 Wiki 页面的导航区，右侧是聊天式查询区域。标题下方提供紧凑型的「知识库操作」条，不再把高频能力隐藏在三点菜单中；功能按「文档与导入 / 检索与探索 / 维护与治理 / 导出与设置」分组展示。共享成员采用三层权限：`owner` 拥有完整设置/删除/共享管理能力，`editor` 可上传文档、编辑 Wiki、保存问答结果与管理任务，`viewer` 仅浏览、搜索、问答。若历史数据导致 `page_count` / `source_count` 与磁盘实际内容不一致，面板渲染时会自动回填并纠正计数。公开仓库的未登录访客不显示会话栏，查询退化为无状态对话；已登录的共享成员则拥有自己的会话历史。

**Wiki 页面**：渲染后的 markdown，顶部显示 frontmatter 元数据（类型、创建日期、来源）。页面内的 `[链接](page.md)` 自动转为站内链接。侧边栏显示「被引用此页面」的反向链接列表。owner/editor 可通过页面顶部的「编辑」和「删除」按钮管理页面。系统级页面（如 `overview`）若因历史数据缺失被访问，会自动补回默认占位内容并正常渲染，避免空知识库直接落到 404。

**Wiki 编辑页**（`templates/wiki/edit.html`）：EasyMDE Markdown 编辑器，支持实时预览与自动保存草稿。

**查询界面**：上方输入框，下方显示回答（markdown 渲染）。回答中的 Wiki 引用可点击跳转。owner/editor 可见「保存为 Wiki 页面」按钮。输入框右侧的会话相关操作使用带文字的可见按钮（如「历史记录」「清空对话」），避免只靠图标导致功能不可发现；这些工具按钮与发送按钮不仅共享统一控件高度，还会沿输入条纵向拉伸，尽量贴合整块输入容器。独立查询页的「查询」按钮也与输入框统一高度；共享成员与公开库中的已登录用户都拥有各自的会话历史，而公开仓库访客不显示会话栏。

**来源证据页**（`templates/source/list.html`、`templates/source/view.html`）：公开仓库允许访客查看来源列表和来源 Markdown 预览，用于承接 evidence 链接；上传、删除、重命名和摄入按钮对 owner/editor 可见，下载原始文件对已登录且有仓库访问权的成员可见。文档管理页的上传流程包含明确的“已选择文件”确认态；批量删除和批量摄入按钮默认禁用，只有勾选文件后才进入可点击状态。内部系统前端不再展示“从 URL 导入网页”入口。

**认证页**（`templates/auth/login.html`、`templates/auth/register.html`、`templates/auth/forgot_password.html`、`templates/auth/reset_password.html`）：注册时收集邮箱并创建未验证账号，系统发送一次性邮箱验证链接；用户完成验证后才允许登录。登录支持用户名或邮箱，若账号未验证则拒绝登录并重发验证邮件。忘记密码通过 SMTP 发送一次性重置链接；若邮件服务未配置，注册和找回密码都会直接提示不可用，不继续尝试发信。登录成功后的 `next` 参数仅允许站内地址，防止开放重定向。

**表单安全**：服务端对所有同源非只读请求统一启用 CSRF 校验，模板通过 `csrf_token()` 注入隐藏字段，前端 `fetch/XMLHttpRequest` 会自动附带 `X-CSRFToken` 头；携带 `Authorization: Bearer` 的 API Token 请求不参与 CSRF 校验。

**关系图**：用 D3.js 力导向图展示页面间的链接关系。类似 Obsidian 的 graph view。

**全局搜索页**（`templates/user/search.html`）：按知识库分组展示跨仓库关键词搜索结果，含摘要和匹配次数。

**个人设置页**（`templates/user/settings.html`、`templates/user/tokens.html`）：包含「基本信息」「修改密码」「危险操作」三个区域。删除账号时要求再次输入当前用户名和密码确认，提交后会同步清理名下知识库目录、向量索引、任务、会话、API Token 以及关联查询记录中的用户引用，并立即注销当前会话；E2E 测试脚本会复用这条删除流程自动回收 `e2e_*` 账号。

**知识库设置页**（`templates/repo/settings.html`）：由「基本信息」「共享访问码」「共享成员」「Wiki Schema」「导入 Wiki（ZIP）」「README」「删除知识库」七个独立表单区块组成；owner 可在这里生成 viewer/editor 访问码、停用访问码以及移除共享成员。每个表单都显式携带 `action` 隐藏字段，避免未启用 CSRF 模板变量时提交丢失操作类型。

**基础壳层**（`templates/base.html` + `static/css/style.css`）：站点统一头部、页脚与全局设计令牌都在这里定义。为了适配内网和离线环境，正文不再依赖 Google Fonts，而是使用系统自带中文优先字体栈；Pico CSS、Lucide、EasyMDE、D3 也全部 vendoring 到 `static/vendor/` 由应用自身提供，避免 CDN 或外网不可达时页面样式、图标、编辑器和图谱功能失效。知识库聊天页注入给 `chat.js` 的配置对象必须使用 `tojson` 输出，避免 Jinja 自动转义把 URL 变成 `&#34;...&#34;` 从而导致会话栏和“新对话”初始化失效。

**管理后台**（`templates/admin/dashboard.html`）：展示用户总数、知识库总数、任务统计、磁盘占用及最近注册用户列表。仅 ADMIN_USERNAME 可访问。

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
# 生产/公测环境必须设置随机 SECRET_KEY；默认值仅允许测试/开发环境使用
SECRET_KEY=change-me-to-a-random-string
DATA_DIR=./data
APP_BASE_URL=https://wiki.example.com
WTF_CSRF_ENABLED=true

# SMTP / 注册验证 / 找回密码
MAIL_HOST=smtp.example.com
MAIL_PORT=465
MAIL_USERNAME=noreply@example.com
MAIL_PASSWORD=****
MAIL_USE_SSL=true
MAIL_FROM=noreply@example.com
PASSWORD_RESET_EXPIRES=3600

# Admin
ADMIN_USERNAME=admin
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
├── mailer.py              ← SMTP 发信封装（邮箱验证 / 找回密码）
├── llm_client.py          ← OpenAI 兼容 LLM 客户端封装
├── wiki_engine.py         ← 核心 Wiki 操作（ingest / query / lint）
├── qdrant_service.py      ← Qdrant 向量检索服务（embedding + 读写）
├── mineru_client.py       ← MinerU 文档解析客户端（HTTP 调用）
├── llmwiki_core/          ← 服务端 / 机密客户端共享 contract
│   └── contracts.py       ← RepoRef / LocalRepoPaths / QueryRunResult
├── confidential_client/   ← 本地机密知识库运行层
│   ├── cli.py             ← 纯客户端 CLI 入口
│   ├── controller.py      ← 客户端 workflow controller
│   ├── crypto.py          ← 本地 vault 加解密（scrypt + AES-GCM）
│   ├── desktop.py         ← 桌面客户端入口
│   ├── gui.py             ← Tkinter 桌面界面
│   ├── health.py          ← 外部服务健康检查
│   ├── manager.py         ← 本地 repo 目录与导入导出管理
│   ├── repository.py      ← 机密 repo manifest + 加密仓库封装
│   ├── qdrant.py          ← 机密模式 Qdrant adapter（payload 最小化 + 本地映射）
│   └── runtime.py         ← 客户端 ingest / query 运行时
├── utils.py               ← 工具函数（markdown 渲染、文件处理、slug 生成）
├── task_worker.py         ← 后台任务队列 Worker（threading daemon）
├── requirements.txt       ← Python 依赖
├── .env.example           ← 环境变量模板
├── templates/             ← Jinja2 模板
│   ├── base.html          ← 基础布局（导航栏、侧边栏）
│   ├── index.html         ← 首页
│   ├── auth/
│   │   ├── login.html
│   │   ├── register.html
│   │   ├── forgot_password.html
│   │   └── reset_password.html
│   ├── repo/
│   │   ├── list.html      ← 仓库列表
│   │   ├── new.html       ← 新建仓库
│   │   ├── dashboard.html ← 仓库面板
│   │   └── settings.html  ← 仓库设置（基本信息 / Schema / ZIP 导入 / README / 删除）
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
cryptography>=42.0
pyyaml>=6.0
trafilatura>=2.0
lxml_html_clean>=0.4
xai-sdk>=1.11
openpyxl>=3.1
```

**为什么选择这些**：
- `flask` + `flask-login`：Web 框架 + 会话认证，最简组合
- `flask-sqlalchemy` + `pymysql`：ORM + MySQL 驱动，比原生 SQL 更安全易维护
- `openai`：OpenAI 兼容接口的 Python SDK，支持 base_url 自定义；同时用于调用 Embedding 模型
- `qdrant-client`：Qdrant 向量数据库的官方 Python SDK
- `markdown` + `pygments`：Markdown 渲染 + 语法高亮
- `httpx`：HTTP 客户端，用于调用 MinerU API
- `cryptography`：机密客户端本地 vault 加密（AES-GCM）
- `pyyaml`：解析 Wiki 页面的 YAML frontmatter
- `openpyxl`：Excel 表格转 Markdown + Fact records
- `pywebview`：客户端本地 HTML/WebView 容器，复用服务端页面视觉风格

**机密客户端 GUI**：当前实现改为 `本地 Flask 页面 + pywebview 容器`。客户端页面不再做“近似风格”复制，而是直接复用服务端 `base / source/list / ops/query` 的页面结构、class 命名和静态资源，只把交互切到本地机密知识库链路；桌面端默认中文界面，主区采用两个标签页：`文档管理` 与 `智能问答`，首页直接进入工作区，不再额外渲染独立 hero 横幅。文档上传入口位于文档管理页按钮，点击后弹出与服务端相同风格的上传弹窗；知识库创建入口也改为按钮 + 弹窗，而不是在侧栏常驻大表单。文档列表展示面向用户的原始文件名（自动隐藏本地存储前缀），时间列使用紧凑的“最近更新”格式；问答结果中的“证据摘要”改为分组渲染置信度、Wiki 证据、片段证据与事实证据，不再直接暴露原始 frontmatter / JSON 拼接文本。客户端附加样式会把整体字号略微下调，尽量贴近服务端页面的阅读密度，外部服务参数不直接暴露给终端用户。

**客户端默认服务配置**：机密客户端启动时优先从以下路径读取未提交到 Git 的默认服务配置，并自动注入到新建知识库中：

- 开发 / 打包机：`packaging/client/default-services.local.json`
- 桌面包旁路分发：应用可执行文件同目录下的 `default-services.json`
- 用户本地覆盖：`<client_home>/private/default-services.json`
- 若以上文件均不存在：回退到当前项目 `config.Config` 的外部服务配置

仓库内仅保留示例文件 `packaging/client/default-services.example.json`；真实配置文件必须加入 `.gitignore`。轻量打包脚本与 PyInstaller 二进制打包会优先携带 `packaging/client/default-services.local.json`，若缺失则自动根据当前构建环境的 `config.Config` 生成并内嵌 `default-services.json`，保证客户端开箱即用且与服务端外部设施配置保持一致。

**客户端启动与打包**：

- 开发态启动：`python -m confidential_client.desktop` 或 `make client-desktop`
- 轻量打包：`make client-package`
- 独立二进制打包：`make client-binary`
- macOS `.app` 包骨架：`make client-macos-app`
- Windows 安装包：`make client-windows-installer`
- GitHub Actions Windows 打包：推送到 `main`（命中客户端/打包相关路径）或手动触发 `.github/workflows/windows-client.yml`
- `scripts/build-confidential-client.sh` 生成的是跨平台 launcher 包
- `scripts/build-confidential-client-binary.sh` 读取 `packaging/confidential-client.spec`，在具备 `pyinstaller` 的构建机上生成独立桌面包；spec 会基于 PyInstaller 注入的 `SPECPATH` 定位仓库根目录并注入 `sys.path/pathex`，避免 Windows CI 因执行上下文差异找不到 `confidential_client`，同时使用 `COLLECT` 产出 `onedir` 目录布局，把 `static/` 与默认服务配置一并打入，供 Inno Setup 安装脚本直接收集
- `scripts/build-macos-app.sh` 生成 `.app` 包结构
- `scripts/build-windows-installer.ps1` 调用 Inno Setup 生成 Windows 安装包
- `.github/workflows/windows-client.yml` 在 `windows-latest` 上执行 `PyInstaller + Inno Setup`，自动上传 `open-llm-wiki-client-<version>-setup.exe` 与二进制 zip 工件；workflow 会先通过 `Get-Command`、常见安装目录和 Chocolatey 目录定位 `ISCC.exe`，减少 GitHub Runner 上的路径差异问题；如需内嵌生产外部服务配置，可在仓库 Secrets 中提供 `CLIENT_DEFAULT_SERVICES_JSON`
- `scripts/sign-macos-client.sh` / `scripts/sign-windows-client.ps1` 分别承载 macOS / Windows 签名步骤
- `packaging/appcast.sample.json` 是自动更新清单样例

### 8.3 异步处理

摄入操作可能耗时较长（多步 LLM 调用）。采用**后台任务队列 + SSE 进度轮询**：

**架构**：
- 上传文件后自动创建 `status=queued` 的摄入任务
- `TaskWorker`（Python daemon thread）轮询 DB 取任务执行
- 多 gunicorn worker 下用乐观锁（`UPDATE ... WHERE status='queued'`）防重复
- SSE 端点轮询 DB 读进度，不在 HTTP 线程内执行 LLM

**任务状态流转**：`queued → running → done / failed / cancelled`

**进度追踪**：Task 表的 `progress`（0-100）、`progress_msg` 和 `cancel_requested` 字段由 Worker / API 实时更新，前端通过 SSE 或 JSON API 轮询展示。

**取消语义**：
- 排队中的任务可直接标记为 `cancelled`
- 运行中的任务通过 `cancel_requested=1` 协作式终止，Worker 在阶段边界检查后收尾并写回 `cancelled`

**任务队列看板**（`/{user}/{repo}/tasks`）：显示所有任务的状态、进度条、耗时，支持取消/终止与失败后重试，JS 自动刷新。

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

Qdrant 既承担叙事层的页面/片段检索，也承担 Fact Layer 的 record 检索。

**Collection 设计**：每个仓库对应三类 collection：

- `repo_{repo_id}`：页面级向量（Wiki 页面全文）
- `repo_{repo_id}_chunks`：段落级向量（chunk evidence）
- `repo_{repo_id}_facts`：结构化 record 向量（fact evidence）

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

**大批量 record 写入保护**：Fact Layer 以及机密客户端模式下的 chunk / fact 向量写入，统一按固定小批次分段 `upsert`，避免 Excel/CSV 大表格在单次请求中携带过多向量与 payload，触发 Qdrant 默认 32MB JSON 请求体限制。Fact records 的 embedding 进一步改为“批量请求 + 受控并发”的模式，减少 1 行 1 请求带来的长尾耗时；客户端运行时会在 fact 阶段透传更细粒度的进度事件，避免长时间停留在固定 `84%`。

**机密客户端补充**：`confidential_client/qdrant.py` 在客户端模式下复用同一套检索接口，但 Qdrant payload 仅保存 `repo_id + point_ref + kind` 这类 opaque 字段；`filename/title/chunk_text/fact_text` 等可读元数据落到客户端本地 `qdrant-map.sqlite`，由本地 runtime 恢复，平台服务端不参与。

**客户端完整链路**：`confidential_client/manager.py + controller.py + runtime.py + gui.py` 组合出完整客户端流程：

- 本地创建 / 列出 / 删除机密知识库
- 从导出 bundle 恢复本地知识库
- 导入 / 恢复 bundle 时使用客户端内置安全解包逻辑，兼容 Python 3.11/3.12，且阻止 tar 路径穿越 / link entry
- 新建知识库时自动读取本地私有默认服务配置，不在 GUI 中暴露 LLM / Embedding / Qdrant / MinerU 参数
- 新建知识库时支持两种本地存储模式：`加密模式` 与 `明文模式`；两者都走同一套客户端 runtime / Qdrant opaque payload，只是本地仓库存储方式不同
- 本地执行 ingest / query / history / export
- 摄入过程中把文档状态、最近进度、最近完成时间持久化到客户端 vault 内，桌面端可直接展示文档管理视图
- 桌面端通过本地 Flask API + WebView 页面承载交互；客户端页面沿用服务端文档管理 / 问答的视觉样式，并通过后台线程执行 ingest，文档页展示实时进度
- 对于明文模式，GUI 会自动禁用“访问口令”输入框；文档载入、上传、删除、问答等本地 API 不再要求传入口令，便于单机便捷使用
- 文档列表默认隐藏本地存储生成的十六进制前缀，统一展示用户可读文件名，并将“上传时间”收敛为紧凑的“最近更新”日期时间
- 问答页证据区使用分组卡片展示置信度、摘要、Wiki / 片段 / 事实证据，片段摘要会清洗 YAML frontmatter 后再展示
- “已存在知识库”下拉框由服务端首屏 HTML 直接渲染当前仓库选项，前端脚本再接管刷新与切换，避免 WebView 初始化异常时列表空白
- `载入内容` / 切换当前知识库时只更新仓库摘要，不重新整体重绘知识库下拉区域，降低 WebView 下交互失效风险
- 上传弹窗支持多文件选择；提交后立即关闭弹窗并把文件插入文档列表，后台按本地任务队列依次处理
- 客户端上传队列的暂存文件保留原始文件名，使用独立临时目录避免冲突，确保中文文件名的 `.csv/.xlsx` 等扩展名不会在暂存阶段丢失
- `/api/bootstrap` 与文档列表接口会返回未完成上传任务，页面刷新后可恢复“处理中 / 排队中”文档显示与轮询
- 任务进度事件中的 `ready` 仅用于表达“进度已到 100%”，任务注册器仍保持 `running`，直到后台线程完成文档列表回填并显式写成 `done`；前端轮询只在最终 `done/failed` 后停止，避免列表状态停留在“处理中”
- 文档管理页提供删除动作；删除时会清理客户端 vault 中的文档状态、`raw/` 原始/转换文件、`facts/records/*.jsonl`、受该文档摄入影响的本地 Wiki 页面，以及 Qdrant 中对应的 page / chunk / fact 向量，并在删除后重建 `index.md` 与 `overview.md` 以避免继续引用已删除内容
- 客户端测试除路由/状态流外，还需校验“渲染后的最终内联脚本”可被浏览器解析，避免 Python 模板字符串转义导致前端整段脚本失效
- 机密客户端遇到老式 `.doc` 文件时，会优先尝试本地自动转换为 `.docx`（macOS `textutil` / LibreOffice），再交给 MinerU；若当前环境无可用转换器，则提示用户先转为 `.docx` 或 PDF

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
- 邮箱（唯一，用于注册验证与找回密码）
- 显示名称
- 密码（二次确认）

登录支持“用户名或邮箱 + 密码”。新注册账号默认 `email_verified=false`，必须先通过邮件中的签名链接完成验证后才允许登录；线上存量用户在迁移时回填为已验证。忘记密码通过带时效签名的重置链接完成，不在数据库额外持久化 reset token。

### 11.2 用户功能

| 功能 | 说明 |
|------|------|
| 登录 | 用户名或邮箱 + 密码；未验证邮箱的账号禁止登录 |
| 注册 | 开放，填写用户名 / 邮箱 / 显示名称 / 密码，并发送邮箱验证邮件 |
| 登出 | 清除 session |
| 修改显示名称 / 邮箱 | 个人设置页 |
| 修改密码 | 需验证旧密码 |
| 邮箱验证 | 点击验证链接后激活登录权限；未验证账号登录时自动重发验证邮件 |
| 找回密码 | 输入邮箱后发送重置链接；若 SMTP 未配置，则页面提示暂时无法发送找回密码邮件 |

### 11.3 权限模型

| 操作 | 谁可以 |
|------|--------|
| 浏览公开仓库的 Wiki | 所有访客 |
| 查看公开仓库的原始文档预览 | 所有访客 |
| 向公开仓库提问（Query） | 所有访客 |
| 创建仓库 | 自己 |
| 浏览私有仓库 Wiki / 原始文档 / Query | 仅 owner / admin |
| 上传文档 / 下载原始文件 / 触发摄入 / Lint / 终止任务 | 仅仓库创建者 |
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

`manage.py migrate` 读取已执行过的版本号（存在 `schema_version` 表中），按序执行新的迁移文件。迁移执行器对 `schema_version` 写入使用 `INSERT IGNORE`，兼容历史 SQL 文件中已包含版本登记语句的情况，避免重复插入导致部署中断。

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
├── mailer.py              ← SMTP 发信、邮箱验证与找回密码邮件
├── manage.py              ← CLI 管理命令（init-db / migrate / check / rebuild-*-index）
├── models.py              ← SQLAlchemy 模型
├── llm_client.py          ← LLM 客户端
├── wiki_engine.py         ← 核心 Wiki 操作（含 Fact Layer 查询融合）
├── qdrant_service.py      ← Qdrant 向量检索（page/chunk/fact）
├── mineru_client.py       ← MinerU 文档解析
├── llmwiki_core/
│   ├── __init__.py
│   └── contracts.py       ← 服务端 / 客户端共享 contract
├── confidential_client/
│   ├── __init__.py
│   ├── cli.py             ← 机密知识库 CLI
│   ├── controller.py      ← 桌面端与 CLI 复用的控制层
│   ├── crypto.py          ← 本地加密实现（scrypt + AES-GCM）
│   ├── desktop.py         ← WebView 桌面客户端入口
│   ├── gui.py             ← 本地 Flask + WebView GUI（复用服务端页面设计）
│   ├── health.py          ← 外部服务健康检查
│   ├── manager.py         ← 本地 repo 管理 / 导入导出 / 默认服务配置加载
│   ├── repository.py      ← 加密仓库 manifest + vault 封装 / 文档状态持久化
│   ├── qdrant.py          ← 机密模式 Qdrant payload 最小化适配器
│   ├── runtime.py         ← 本地 ingest / query 运行时（含文档进度回调）
│   ├── update.py          ← 自动更新检查
│   └── version.py         ← 客户端版本元数据
├── utils.py               ← 工具函数（Markdown/JSONL/表格 records）
├── task_worker.py         ← 后台任务 Worker
├── exceptions.py          ← 自定义异常
├── Makefile               ← 常用命令
├── pyproject.toml         ← Ruff / 项目元数据
├── requirements.txt       ← 生产依赖
├── requirements-dev.txt   ← 开发依赖
├── .env                   ← 环境变量（不提交）
├── .env.example           ← 环境变量模板
├── .github/
│   └── workflows/
│       └── windows-client.yml ← Windows 客户端自动打包流水线
├── .gitignore
├── deploy/
│   └── llmwiki.service    ← 受版本控制的 systemd 服务文件模板
├── migrations/            ← SQL 迁移文件
│   └── 001_init.sql
├── scripts/
│   ├── build-confidential-client.sh ← 客户端 launcher 打包脚本
│   ├── build-confidential-client-binary.sh ← 客户端二进制打包脚本
│   ├── build-macos-app.sh ← macOS app bundle 构建脚本
│   ├── build-windows-installer.ps1 ← Windows 安装包构建脚本
│   ├── sign-macos-client.sh ← macOS 签名脚本
│   ├── sign-windows-client.ps1 ← Windows 签名脚本
│   └── deploy.sh          ← 部署脚本
├── packaging/
│   ├── appcast.sample.json ← 自动更新清单样例
│   ├── client/
│   │   └── default-services.example.json ← 客户端默认服务配置示例
│   ├── confidential-client.spec ← PyInstaller 打包配置
│   ├── macos/
│   │   └── Info.plist.template
│   └── windows/
│       └── open-llm-wiki-client.iss
├── templates/
│   ├── base.html
│   ├── errors/
│   │   ├── 404.html
│   │   └── 500.html
│   ├── auth/
│   │   ├── forgot_password.html
│   │   ├── login.html
│   │   ├── register.html
│   │   └── reset_password.html
│   ├── user/
│   │   ├── settings.html
│   │   └── tokens.html
│   ├── repo/
│   ├── wiki/
│   ├── source/
│   └── ops/
│       ├── query.html
│       ├── lint.html
│       └── tasks.html    ← 任务队列看板
├── static/
│   ├── css/
│   ├── js/
│   └── vendor/
│       ├── pico/
│       ├── lucide/
│       ├── easymde/
│       └── d3/
├── tests/                 ← 测试
│   ├── test_confidential_client.py
│   ├── test_confidential_desktop.py
│   ├── test_confidential_packaging.py
│   ├── test_confidential_update.py
│   └── ...
├── logs/                  ← 日志文件（不提交）
├── data/                  ← 用户数据（不提交）
└── docs/
    ├── design.md
    └── private-kb-user-manual.md
```

### 12.10 部署

**部署目标**：`172.36.164.85`（Anolis OS 8.9, x86_64）

**部署脚本**（`scripts/deploy.sh`）：在项目根本地 `.env` 中配置 `DEPLOY_HOST`、`DEPLOY_PORT`、`DEPLOY_USER`、`DEPLOY_PASSWORD`（可选 `DEPLOY_PATH`，默认 `/opt/open-llm-wiki`），执行 `./scripts/deploy.sh` 时会自动 `source` 该 `.env`。这些凭据仅保存在本地工作区，**不提交 Git**。脚本用 `sshpass` 上传 tarball，并将仓库内的 `deploy/llmwiki.service` 安装到 `/etc/systemd/system/llmwiki.service`，随后执行迁移、`daemon-reload`、重启 `llmwiki` 服务并校验 unit 已加载 `/opt/open-llm-wiki/.env`。服务器上的业务 `.env` 与 `data/` 不会被部署覆盖。

```bash
#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVER_HOST="${DEPLOY_HOST:-172.36.164.85}"
SERVER_PORT="${DEPLOY_PORT:-2234}"
SERVER_USER="${DEPLOY_USER:-root}"
SERVER_PATH="${DEPLOY_PATH:-/opt/open-llm-wiki}"
TARBALL="/tmp/llmwiki-deploy.tar.gz"

echo "打包项目..."
tar czf "$TARBALL" \
    --exclude=".venv" --exclude="__pycache__" --exclude="*.pyc" \
    --exclude=".env" --exclude="data" --exclude=".git" --exclude="**/._*" \
    --exclude="*.tar.gz" -C "$PROJECT_ROOT" .

echo "上传到服务器..."
sshpass -p "$DEPLOY_PASSWORD" scp -o StrictHostKeyChecking=no -P "$SERVER_PORT" \
    "$TARBALL" "${SERVER_USER}@${SERVER_HOST}:/tmp/"

echo "解压并重启..."
sshpass -p "$DEPLOY_PASSWORD" ssh -o StrictHostKeyChecking=no -p "$SERVER_PORT" \
    "${SERVER_USER}@${SERVER_HOST}" "
    cd $SERVER_PATH
    tar xzf /tmp/llmwiki-deploy.tar.gz --exclude='**/._*' 2>/dev/null
    install -m 644 deploy/llmwiki.service /etc/systemd/system/llmwiki.service
    .venv/bin/python manage.py migrate
    systemctl daemon-reload
    systemctl restart llmwiki
    systemctl show llmwiki -p EnvironmentFiles | grep '/opt/open-llm-wiki/.env'
    curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://localhost:5000/health
  "

echo "✓ 部署完成"
```

**Systemd 服务文件**（`deploy/llmwiki.service` → `/etc/systemd/system/llmwiki.service`）：
```ini
[Unit]
Description=Open LLM Wiki
After=network.target mysql.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/open-llm-wiki
EnvironmentFile=/opt/open-llm-wiki/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/open-llm-wiki/.venv/bin/gunicorn -w 2 -b 0.0.0.0:5000 --timeout 300 'app:create_app()'
Restart=on-failure
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

- **机密知识库客户端**：新增独立桌面客户端承载高敏知识库运行链路，复用共享 Python core，但不与平台服务端通信，也不在平台服务端数据库中留存机密库数据；客户端仅与用户配置的 MinerU / LLM / Embedding / Qdrant 交互
- **Obsidian 兼容**：用户可以用 Obsidian 直接打开 data/{user}/{repo}/ 目录浏览 Wiki
- **批量摄入**：一次上传多个文档，排队自动处理
- **仓库协作**：多人共同维护一个仓库
- **导出功能**：生成 Marp 幻灯片、PDF 报告
- **Webhook**：摄入完成后通知
- **Schema 模板市场**：为不同场景（研究、读书、竞品分析）提供预设 Schema
- **BM25 混合检索**：在 Qdrant 语义检索基础上叠加关键词检索，进一步提升召回率
