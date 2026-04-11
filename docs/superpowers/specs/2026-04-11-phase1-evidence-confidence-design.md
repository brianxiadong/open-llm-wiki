# Phase 1：可信度底座设计

**目标**：在现有 Open LLM Wiki 查询链路上，增加可验证的双通道证据、规则化置信度和查询日志，为后续知识缺口分析与自动维护打基础。

**范围**：本期只覆盖查询可信度增强，不扩展到知识缺口发现、实体对齐、自动巡检等 Phase 2/3/4 能力。

## 1. 背景与现状

当前项目已经具备以下能力：

- `WikiEngine.query()`：LLM Wiki 结构化路径 + Qdrant 页级语义路径双通道查询
- `WikiEngine.query_stream()`：SSE 流式输出回答
- `QdrantService.upsert_page()`：按页面写入单个向量点
- 前端聊天区：展示 `wiki_sources` 与 `qdrant_sources`，但两者都仍是页级来源

当前主要缺口：

- Qdrant 仍是页级索引，无法定位到具体段落块
- 回答没有真正的“证据片段”结构
- 没有统一的置信度模型
- 服务端没有查询日志，后续无法做低置信度统计与知识缺口分析

## 2. 设计目标

本期查询链路升级后应满足：

1. 回答同时展示两类证据：
   - `LLM Wiki 证据`：结构化页面级证据
   - `原文片段证据`：Qdrant chunk 级证据
2. 回答默认采用“平衡模式”：
   - 尽量回答
   - 证据不足时明确提示低置信度
   - 不把缺乏支撑的推断表述成事实
3. 置信度由后端规则计算，不依赖 LLM 自评
4. 普通查询与 SSE 查询返回统一结构
5. 服务端持久化查询日志，为后续知识缺口分析提供数据

## 3. 非目标

本期明确不做：

- 句子级证据定位
- 页面内精确锚点跳转
- reranker / 学习排序模型
- 多轮对话上下文下的证据继承
- 来源可信度训练模型
- 缺口分析页面与自动维护调度

## 4. 关键决策

### 4.1 证据粒度

采用 `section/chunk` 级证据，而不是页级或句子级：

- 比页级证据更可验证
- 比句子级证据复杂度低，适合当前项目阶段
- 可以自然展示 Markdown 小节标题

### 4.2 双通道证据模型

查询保留现有双通道，但展示时分开：

- `LLM Wiki`：负责结构化知识证据
- `Qdrant chunk`：负责原文支撑片段

前端不合并成单一证据列表，避免用户混淆“结构化知识依据”和“原文召回依据”。

### 4.3 置信度策略

采用规则化打分：

- 输入信号为命中数量、双通道是否同时命中、chunk top score、是否命中关键页面、是否显式证据不足
- 输出为 `high / medium / low` + 分数 + 原因列表
- LLM 只负责回答与使用证据标注，不负责置信度最终定级

### 4.4 增量演进策略

本期不删除现有页级索引能力，而是增量增加 chunk 索引能力：

- `upsert_page()` 保留
- 新增 `upsert_page_chunks()` / `search_chunks()` / `delete_page_chunks()`
- 现有功能保持兼容，可信度查询优先使用 chunk 结果

### 4.5 Page / Chunk 共存策略

为避免页级检索与 chunk 检索互相污染，本期采用**双 collection** 策略：

- 页面 collection：`repo_{repo_id}`，保持现有行为不变
- chunk collection：`repo_{repo_id}_chunks`，仅用于证据片段召回

这样可以保证：

- 现有 `search()` 继续返回页级结果
- 新增 `search_chunks()` 只返回 chunk 结果
- 不需要在现有页级查询上额外引入 payload filter 兼容逻辑

## 5. 目标架构

### 5.1 QdrantService

保留现有 page index，同时新增 chunk index 支撑：

- `split_page_into_chunks(content: str) -> list[dict]`
- `upsert_page_chunks(repo_id, filename, title, page_type, content)`
- `search_chunks(repo_id, query, limit=8) -> list[dict]`
- `delete_page_chunks(repo_id, filename)`

新增私有命名方法：

- `_collection_name(repo_id)`：沿用现有页级 collection 命名方法
- `_chunk_collection_name(repo_id)`：chunk collection

### 5.2 WikiEngine

新增可信度查询编排能力：

- `query_with_evidence(repo, username, question)`
- `_build_wiki_evidence(...)`
- `_build_chunk_evidence(...)`
- `_score_confidence(...)`
- `_log_query(...)` 由路由或 service 完成，二选一，优先放路由层

`query_stream()` 复用 `query_with_evidence()` 的证据与置信度结构，避免普通查询和流式查询分叉。

### 5.3 Flask 路由层

- `POST /<username>/<repo_slug>/query`
- `GET /<username>/<repo_slug>/query/stream?q=<question>`

两者最终都返回**同构的 Markdown / evidence / confidence 数据**；其中 `html` 仅由普通查询响应和 render-only 响应返回，SSE `done` 不直接返回 `html`。

- `markdown`
- `html`
- `confidence`
- `wiki_evidence`
- `chunk_evidence`
- `evidence_summary`
- `referenced_pages`

### 5.4 前端聊天区

保留现有聊天体验，新增三块展示：

- `置信度标识`
- `LLM Wiki 证据面板`
- `原文片段证据面板`

## 6. 数据模型

### 6.1 新增 MySQL 表：`query_logs`

```sql
CREATE TABLE query_logs (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    repo_id           INT NOT NULL,
    user_id           INT NOT NULL,
    question          TEXT NOT NULL,
    answer_preview    TEXT,
    confidence        VARCHAR(16) NOT NULL DEFAULT 'low',
    wiki_hit_count    INT NOT NULL DEFAULT 0,
    chunk_hit_count   INT NOT NULL DEFAULT 0,
    used_wiki_pages   LONGTEXT,
    used_chunk_ids    LONGTEXT,
    evidence_summary  TEXT,
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (repo_id) REFERENCES repos(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 6.2 ORM 模型

在 `models.py` 增加 `QueryLog`：

- `repo_id`
- `user_id`
- `question`
- `answer_preview`
- `confidence`
- `wiki_hit_count`
- `chunk_hit_count`
- `used_wiki_pages`
- `used_chunk_ids`
- `evidence_summary`
- `created_at`

其中：

- `used_wiki_pages` 以 JSON 字符串存储文件名数组
- `used_chunk_ids` 以 JSON 字符串存储 chunk id 数组

### 6.3 Migration

新增 SQL 文件：

- `migrations/004_query_logs.sql`

本仓库的 `manage.py migrate` 会按 migration 文件名前缀自动写入 `schema_version`，因此 `004_query_logs.sql` **只包含建表/改表语句**，不在 SQL 文件内手工插入版本号。

## 7. Chunk 索引设计

### 7.1 切分策略

使用稳定的工程规则，不引入复杂语义切分：

1. 先按 Markdown 标题切成 section
2. section 内按段落聚合
3. 单个 chunk 目标长度约 `400-800` 字符
4. 太短的段落向相邻 chunk 合并
5. 为每个 chunk 保留 `heading` 和 `position`

### 7.2 Chunk Payload

```json
{
  "repo_id": 1,
  "filename": "concept-ai.md",
  "page_title": "AI",
  "page_type": "concept",
  "chunk_id": "concept-ai.md#2",
  "heading": "应用场景",
  "chunk_text": "AI 在客服、搜索、推荐和自动化场景中广泛应用……",
  "position": 2
}
```

### 7.3 Collection 与 ID 规则

- 页面继续写入 `repo_{repo_id}`
- chunk 写入 `repo_{repo_id}_chunks`

chunk point id 必须稳定，建议使用：

- 与现有 page point 一致，采用稳定 `int` id：
  - 对 `f"{repo_id}:{filename}:{chunk_id}"` 做 `md5`
  - 取前 16 位十六进制
  - 转为整数作为 Qdrant point id

确保页面重建时同一 chunk 位置可被覆盖更新。

### 7.4 写入时机

以下场景都要同步 chunk 索引：

- ingest 新建页面
- ingest 更新页面
- 手动编辑 Wiki 页面
- 保存 query answer 为页面
- 自动修复页面

删除页面时同时删除：

- page point
- 所有关联 chunk points

### 7.5 存量数据回填

上线本期能力后，历史仓库默认只有页级向量，没有 chunk 证据。为避免只有新写入页面才有片段证据，本期增加一次性回填能力：

- 新增管理命令：`python manage.py rebuild-chunk-index [--repo-id <id>]`
- 默认支持按单 repo 或全量 repo 重建 chunk 索引
- 回填失败不影响现有页级查询，只影响 chunk 证据展示

## 8. 查询流程

### 8.1 普通查询

1. 使用现有 Wiki 路径从 `index.md` 选择相关页面
2. 读取命中的 Wiki 页面，构造 `wiki_evidence`
3. 用问题走 `search_chunks()`，召回 top-k 原文片段
4. 构造 `chunk_evidence`
5. 将 `wiki_evidence` 与 `chunk_evidence` 作为上下文喂给 LLM，仅要求返回 Markdown 回答
6. 后端按规则计算 `confidence`
7. `referenced_pages`、日志中的证据列表均基于**送入 prompt 的证据集合**确定，而不是依赖 LLM 反向标注
8. 记录 `query_logs`
9. 返回统一结果结构

### 8.2 SSE 查询

SSE 查询沿用现有体验，但与普通查询共享同一套“检索、证据构造、置信度打分”逻辑，仅在回答生成阶段使用流式输出。

具体约定：

- 检索阶段先完整得到 `wiki_evidence`、`chunk_evidence` 与 `base_confidence`
- 回答阶段继续用 `chat_stream()` 逐段输出 Markdown
- 回答完成后，再根据最终回答文本是否包含固定不确定性提示语，对 `base_confidence` 应用 `-0.20` 惩罚，得到 `final_confidence`
- `done` 事件返回与普通查询同构的 Markdown / evidence / confidence 字段，但不返回 `html`
- SSE 路径**不额外追加第二次结构化 LLM 调用**
- render-only 模式负责将 `markdown + evidence + confidence` 渲染为前端最终 HTML

- `markdown`
- `answer`（兼容键，值与 `markdown` 相同，保留一个兼容周期）
- `confidence`
- `wiki_evidence`
- `chunk_evidence`
- `evidence_summary`
- `referenced_pages`
- `wiki_sources`（兼容键，`string[]` 文件名）
- `qdrant_sources`（兼容键，`string[]` 文件名）

前端继续用 render-only 模式渲染 HTML，但 evidence 字段直接复用。

## 9. 回答约束

### 9.1 平衡模式

Prompt 约束：

- 优先依据 `LLM Wiki 证据`
- 用 `chunk` 证据补充细节
- 当关键结论证据不足时，必须使用以下固定提示语之一：
  - `基于现有资料只能推测到`
  - `现有证据不足以支持更确定的结论`
  - `当前知识库中缺少直接证据`
- 不得将未被证据支持的内容写成确定性结论

### 9.2 LLM 输出形式

本期为兼容普通查询与 SSE 查询，LLM 只输出 **Markdown answer**，不要求其返回结构化 JSON。

由后端负责：

- 组织 `wiki_evidence`
- 组织 `chunk_evidence`
- 计算 `confidence`
- 生成 `evidence_summary`
- 写入 `query_logs`

若回答中包含固定的不确定性提示语（如“基于现有资料只能推测到”），后端可将其作为低置信度的附加信号。

## 10. 置信度规则

### 10.1 输入信号

- `wiki_hit_count`
- `chunk_hit_count`
- `top_chunk_score`
- 是否命中 `overview.md`
- 是否双通道同时命中
- 回答是否包含固定不确定性提示语

其中 `top_chunk_score` 的定义为：

- `search_chunks()` 返回结果中第一条命中的原始 `score`
- 直接使用 Qdrant `search()` 返回的 `r.score`
- 不做额外归一化或映射
- 当 `search_chunks()` 返回空列表时，`top_chunk_score = 0.0`
- Phase 1 的测试按 mock/raw score 编写，例如 `0.90`、`0.78`、`0.40`

### 10.2 分数公式

`score` 采用确定性累加规则，范围 `0.00 - 1.00`：

- `+0.30`：`wiki_hit_count >= 1`
- `+0.15`：`wiki_hit_count >= 2`
- `+0.25`：`chunk_hit_count >= 2`
- `+0.10`：`chunk_hit_count >= 4`
- `+0.15`：双通道同时命中
- `+0.10`：`top_chunk_score >= 0.85`
- `+0.05`：`0.75 <= top_chunk_score < 0.85`
- `+0.05`：命中 `overview.md`
- `-0.20`：回答包含固定不确定性提示语（在答案文本生成完成后应用）

最终：

- 分数下限为 `0.00`
- 分数上限为 `1.00`
- 检索完成但回答尚未生成时，只计算 `base_score`
- 普通查询在返回前根据最终回答文本应用不确定性惩罚，生成最终分数
- SSE 查询在 `done` 阶段根据最终回答文本应用不确定性惩罚，生成最终分数

### 10.3 分级规则

#### High

当 `score >= 0.75`

#### Medium

当 `0.45 <= score < 0.75`

#### Low

当 `score < 0.45`

### 10.4 输出结构

```json
{
  "level": "low",
  "score": 0.34,
  "reasons": [
    "仅命中 1 个段落证据",
    "未命中结构化 Wiki 页面",
    "回答存在证据不足提示"
  ]
}
```

## 11. 返回 Schema

```json
{
  "markdown": "...",
  "html": "...",
  "confidence": {
    "level": "medium",
    "score": 0.68,
    "reasons": [
      "LLM Wiki 与向量检索均命中",
      "命中 2 个 Wiki 页面",
      "命中 4 个段落证据"
    ]
  },
  "wiki_evidence": [
    {
      "filename": "overview.md",
      "title": "概览",
      "type": "overview",
      "url": "/alice/test/wiki/overview",
      "reason": "结构化路径命中，且为高层概览页"
    }
  ],
  "chunk_evidence": [
    {
      "chunk_id": "concept-ai.md#2",
      "filename": "concept-ai.md",
      "title": "AI",
      "heading": "应用场景",
      "url": "/alice/test/wiki/concept-ai",
      "snippet": "AI 在客服、搜索、推荐和自动化场景中广泛应用……",
      "score": 0.91
    }
  ],
  "evidence_summary": "本回答基于 2 个 Wiki 页面和 4 个原文片段生成。",
  "referenced_pages": [
    "overview.md",
    "concept-ai.md"
  ]
}
```

### 11.1 兼容策略

为避免一次性 breaking change，本期保留现有字段一个兼容周期：

- `references`
- `wiki_sources`
- `qdrant_sources`
- `answer`（仅 SSE done 事件保留，值与 `markdown` 相同）

映射方式：

- `POST /<username>/<repo_slug>/query` 与 render-only 响应中：
  - `wiki_sources`：保持当前对象数组格式 `[{url,title,filename}]`
  - `qdrant_sources`：保持当前对象数组格式 `[{url,title,filename}]`
  - `references`：与 `wiki_sources` 保持一致
- `GET /<username>/<repo_slug>/query/stream` 的 `done` 事件中：
  - `wiki_sources`：保持当前 `string[]` 文件名格式
  - `qdrant_sources`：保持当前 `string[]` 文件名格式
  - 由前端继续透传到 render-only 分支，再由后端转换成对象数组
- `answer`：与 `markdown` 保持相同内容，供现有 `chat.js` 流式 done 处理兼容

待前端全面切换后，再考虑删除旧键。

## 12. 前端展示

### 12.1 置信度区域

回答顶部展示：

- `高置信度`
- `中置信度`
- `低置信度`

点击后展开 reasons。

### 12.2 LLM Wiki 证据区

每条证据展示：

- 页面标题
- 页面类型
- 页面链接
- 命中原因

`reason` 采用规则生成，而不是交给 LLM 生成：

- 命中 `overview.md`：`高层概览页命中`
- 由 index 路径选中：`结构化路径选中`
- 同页存在 chunk 命中：`结构化路径与片段证据共同支持`

### 12.3 原文片段证据区

每条证据展示：

- 页面标题
- section 标题
- 片段摘要
- 相似度分数
- 页面链接

### 12.4 低置信度提示

当 `confidence.level == low` 时，在回答区域增加明显提示：

- 当前回答基于有限证据生成，请谨慎采用

## 13. 错误处理与回退

- Qdrant chunk 检索失败时：
  - 回退到仅 Wiki 证据回答
  - 置信度不高于 `medium`
- chunk 切分失败时：
  - 不影响页级索引
  - 记录日志并继续现有查询能力
- 日志写入失败时：
  - 不阻断回答返回
- render-only 分支必须同时接收并回传：
  - 请求体中的 `_rendered_answer` 作为正文 Markdown 载体
  - 响应中的 `markdown`
  - 响应中的 `answer`（兼容键）
  - `confidence`
  - `wiki_evidence`
  - `chunk_evidence`
  - `evidence_summary`
  - 兼容旧键投影结果

## 14. 测试策略

### 14.1 单元测试

新增或补强：

- `tests/test_qdrant_service.py`
  - Markdown chunk 切分
  - chunk payload 结构
  - `search_chunks()` 返回格式
- `tests/test_wiki_engine.py`
  - 双通道证据合成
  - high / medium / low 置信度判定
  - 证据不足时的平衡回答
- `tests/test_models.py`
  - `QueryLog` 写入

### 14.2 路由与契约测试

- `tests/test_routes.py`
  - `/query` 返回新 schema
  - `/query/stream` done 事件含 confidence/evidence
  - render-only 模式兼容新 schema
- `tests/test_contracts.py`
  - query response schema 的关键字段存在性
  - render-only 分支返回结构与普通查询分支一致

### 14.3 前端回归点

- 现有聊天页面不应因证据结构升级而破坏
- SSE done 事件渲染不重复
- 低置信度提示在普通查询和流式查询都能显示

## 15. 文档更新

实现时需同步更新：

- `docs/design.md`
  - Query 章节升级为双通道证据模型
  - 数据模型新增 `query_logs`
  - Qdrant 章节补充 page collection + chunk collection 双层结构
- 新增 migration 文档说明

## 16. 建议实施顺序

1. `Qdrant chunk 索引与检索`
2. `WikiEngine 证据化查询编排与置信度打分`
3. `QueryLog 模型、migration 与路由落地`
4. `聊天 UI 双证据展示`
5. `测试与文档收尾`

## 17. 验收标准

满足以下条件视为 Phase 1 完成：

- 查询结果能同时展示 Wiki 证据与 chunk 证据
- 回答包含 `confidence` 结构，且有可解释 reasons
- 低证据问题能被标记为低置信度
- 普通查询与 SSE 查询输出结构一致
- `query_logs` 成功记录查询行为
- 全量测试通过
