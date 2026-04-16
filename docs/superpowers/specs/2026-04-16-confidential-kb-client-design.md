# 加密知识库客户端方案设计

**目标**：在保留现有常规知识库 Web 模式的前提下，新增“加密知识库”模式。加密知识库在客户端完成完整运行链路，不与平台服务端通信，也不在平台服务端数据库中留存任何加密知识库数据。核心知识库引擎尽量共享，避免未来维护和调优分叉。

**结论摘要**：

- 保留两种知识库模式：
  - `normal`：现有服务端模式
  - `confidential`：客户端本地完整运行模式
- 共享一套 Python `core engine`，服务端和客户端仅在 runner / adapter 层分化
- 加密知识库的明文只在客户端出现，平台服务端既不保存明文，也不保存该知识库的元数据、会话和索引状态
- `MinerU / LLM / Embedding / Qdrant` 继续正常使用，不作为本期主要安全边界的一部分
- 加密知识库应设计为 `local-first`，即使平台服务端完全不可用也不受影响

## 1. 背景

当前项目已经具备完整的服务端知识库链路：

- 用户上传原始文档
- 服务端完成解析、摄入、Wiki 生成、向量索引
- 服务端执行查询、证据融合、回答生成
- 原始文档 / Wiki / 查询日志 / 会话历史主要存于服务端

该模式适合常规知识库，但不适合“平台管理员不应直接看到业务文本”的场景。为支持更高敏感度的数据，需要引入第二条链路：

- 用户在客户端本地持有加密知识库明文
- 客户端本地运行完整摄入 / 检索 / 回答流程
- 客户端只与用户配置的 `MinerU / LLM / Embedding / Qdrant` 等外部设施交互
- 平台服务端对该类知识库零参与、零存储、零运行时依赖

## 2. 设计目标

本期设计应满足：

1. 保留现有 `normal` 知识库行为不变
2. 新增 `confidential` 知识库类型
3. `confidential` 知识库在客户端本地完成：
   - 文档导入
   - 加密 / 解密
   - 解析 / chunk / facts / Wiki 生成
   - embedding / 检索 / 证据融合 / 回答
4. 平台服务端不参与 `confidential` 模式的创建、运行、存储与同步
5. 核心能力共享，避免服务端和客户端出现两套不同的知识库引擎
6. Windows / macOS 都可作为正式支持平台
7. 后续调优时，`normal` 和 `confidential` 的能力语义尽量一致

## 3. 非目标

本期明确不做：

- 端到端零知识证明
- 防御拥有客户端本机权限的攻击者
- 将 `MinerU / LLM / Embedding / Qdrant` 纳入本期主要威胁模型
- 离线本地大模型的第一阶段支持
- 将整个产品完全迁移为纯客户端
- 平台级同步、多设备漫游与服务端托管备份

## 4. 模式定义

### 4.1 `normal` 知识库

沿用现有模式：

- 通过 Web 创建、上传、查询
- 服务端持有明文运行态
- 服务端负责摄入、检索、回答和日志
- 适用于公开或低敏数据

### 4.2 `confidential` 知识库

新增模式，关键约束：

- 创建后只能由客户端打开和运行
- 客户端本地保存：
  - 原始文档明文
  - Wiki 明文
  - 本地索引映射
  - 查询历史 / 会话
- 平台服务端不保存：
  - repo 元数据
  - 文档内容或密文副本
  - 会话、查询日志、索引状态
  - 与该知识库有关的任何数据库记录
- 平台服务端不得作为运行时、控制面或数据面的任何组成部分

## 5. 核心设计原则

### 5.1 共享核心，不共享宿主

必须把知识库能力抽成共享 Python `core engine`，而不是分别维护两套逻辑。

共享的能力包括：

- source ingest pipeline
- chunk/fact/page 抽取规则
- schema 解析与应用
- query orchestration
- evidence merge
- confidence scoring
- prompt builder
- result schema

分离的能力包括：

- 存储读写
- 任务调度
- UI 交互
- 加密密钥管理
- 本地服务配置与凭据保存

### 5.2 `local-first`

加密知识库的本地副本是运行时真源：

- 平台服务端完全不可达时仍应可继续打开知识库、浏览和查询
- 迁移、备份、恢复都应围绕本地 repo 进行
- 平台服务端不承担任何兜底角色

### 5.3 统一能力语义

`normal` 与 `confidential` 应共享同一套能力语义：

- 同一套 chunking
- 同一套 fact/page 概念
- 同一套 evidence 格式
- 同一套 confidence 规则
- 同一套 query response 结构

不同之处应尽量只体现在运行位置和存储方式，而非算法本身。

## 6. 威胁模型与安全边界

### 6.1 本期保护目标

对 `confidential` 知识库，本期主要保护：

- 平台服务端磁盘、数据库中不存在该知识库的数据留存
- 平台管理员通过平台侧任何界面都无法查看该知识库正文
- 平台服务端挂掉不影响客户端本地使用

### 6.2 本期不覆盖

本期不覆盖：

- 拥有客户端本地机器权限的攻击者
- 用户自有 `MinerU / LLM / Embedding / Qdrant` 侧的文本泄露
- 内存抓取、调试 hook、恶意本机软件

### 6.3 设计结论

在上述边界下，`confidential` 模式是成立的：

- 明文仅在客户端存在
- 外部工具正常使用
- Qdrant 不应保存可读文本 metadata

## 7. 总体架构

```text
                    +-------------------------------+
                    |        Shared Python Core     |
                    | ingest / query / evidence     |
                    | chunk / fact / prompt / conf  |
                    +-------------------------------+
                           ^                  ^
                           |                  |
                 +---------+----+      +------+---------+
                 | Server Runner |      | Desktop Runner|
                 | normal repos  |      | confidential  |
                 +---------+----+      +------+---------+
                           |                  |
               +-----------+----+      +------+-------------------+
               | Server Adapters |      | Local Adapters          |
               | MySQL / FS /    |      | local FS / SQLite /     |
               | Qdrant / tasks  |      | crypto / Qdrant         |
               +-----------------+      +--------------------------+
```

说明：

- `normal` 与 `confidential` 共享核心算法与 contract
- `confidential` runner 不调用平台服务端接口
- 平台服务端数据库中不登记 `confidential` repo

## 8. 模块划分

### 8.1 共享 `core`

建议抽出一个共享 Python package，例如：

```text
llmwiki_core/
  models/
  schema/
  ingest/
  query/
  evidence/
  confidence/
  prompts/
  contracts/
```

职责：

- 知识库领域模型
- 统一的 ingest / query 流程
- runner 无关的输入输出协议

### 8.2 `server runner`

保留现有 Flask 侧逻辑，但逐步改为调用 `core`：

- repo 权限与 Web 路由
- 服务端任务队列
- 现有 `normal` 模式存储与日志

### 8.3 `desktop runner`

客户端本地运行器，负责：

- 本地 repo 打开 / 创建 / 删除
- 本地任务与进度
- 本地查询与会话
- 本地加密
- 外部服务配置

### 8.4 adapter 层

按运行环境切分：

- `storage adapters`
- `vector adapters`
- `llm adapters`
- `parse adapters`
- `crypto adapters`

## 9. 客户端架构

### 9.1 技术栈

建议第一阶段采用：

- Python 3.11+
- `tkinter` 桌面 UI（当前实现）
- SQLite 本地元数据
- `cryptography` 实现加密
- PyInstaller 或 Nuitka 打包

说明：

- 设计上允许后续迁移到独立 GUI 框架
- 当前优先选择标准库 `tkinter`，避免客户端首版引入额外 GUI 依赖

### 9.2 平台范围

第一阶段正式支持：

- macOS
- Windows

后续可增补 Linux。

### 9.3 本地目录布局

建议每个客户端有单独的数据根目录：

```text
client-data/
  repos/
    <repo-id>/
      config.json
      raw/
      wiki/
      facts/
      cache/
      sessions/
      qdrant-map.sqlite
  state.sqlite
  logs/
```

其中：

- `raw/` 和 `wiki/` 保存本地明文运行态
- `facts/` 保存本地结构化事实层
- `qdrant-map.sqlite` 保存 `chunk_id -> 本地文档映射`

## 10. 加密知识库数据模型

### 10.1 repo 类型

平台服务端无任何新增 repo 字段。

`confidential` repo 仅存在于客户端本地 manifest 中，例如：

- `repo_mode = confidential`
- `repo_id = <local uuid>`
- `created_at`
- `local_data_path`
- `qdrant_collection_prefix`

客户端创建加密知识库时需设置：

- 本地 repo 口令或密钥
- 外部服务连接配置

### 10.2 本地密钥模型

第一阶段建议：

- 用户创建 `confidential` 知识库时设置 repo 口令
- 使用 `scrypt` 派生 repo 主密钥
- 本地文件使用 `AES-256-GCM` 或 `XChaCha20-Poly1305`

平台服务端不保存 repo 明文密钥，也不参与密钥协商。

### 10.3 本地备份单元

如需备份，建议以本地导出包为单位生成密文：

- source document blob
- wiki page blob
- repo manifest
- 本地会话与索引映射

## 11. Qdrant 方案

### 11.1 设计原则

加密知识库模式下，Qdrant 仅承担 ANN 检索加速，不应保存可直接阅读的业务文本。

### 11.2 推荐 payload

推荐 payload 最小化为：

```json
{
  "repo_id": "conf_repo_xxx",
  "doc_id": "d_019a",
  "chunk_id": "c_0188",
  "kind": "chunk"
}
```

如果确需额外字段，也应保存为加密 blob，例如：

```json
{
  "repo_id": "conf_repo_xxx",
  "chunk_id": "c_0188",
  "kind": "chunk",
  "enc_meta": "<ciphertext>"
}
```

### 11.3 客户端恢复

Qdrant 返回结果后，客户端通过本地 SQLite / 文件索引恢复：

- chunk 文本
- page 标题
- source 文件信息
- fact fields

因此：

- 不应把 snippet 明文写入 Qdrant
- 不应把标题 / 页名 / 原文片段明文写入 Qdrant

## 12. 运行时链路

### 12.1 摄入

加密知识库摄入链路：

1. 客户端选择文件
2. 客户端本地解析或调用用户配置的 `MinerU`
3. 客户端调用共享 `core ingest`
4. 客户端生成本地 `raw/wiki/facts/chunks`
5. 客户端调用 embedding
6. 客户端写入用户配置的 Qdrant
7. 客户端本地保存索引映射与运行态

### 12.2 查询

加密知识库查询链路：

1. 客户端读取本地知识库
2. 客户端构造 query 与历史上下文
3. 客户端调用 embedding 检索 Qdrant
4. 客户端根据 `chunk_id` 恢复本地 evidence
5. 客户端构造 prompt
6. 客户端调用用户配置的 LLM
7. 客户端本地保存会话与回答

平台服务端不参与该链路。

## 13. 平台隔离边界

### 13.1 平台服务端角色

平台服务端在 `confidential` 模式中不提供任何能力：

- 不存储 repo
- 不存储会话
- 不存储索引
- 不提供 query / ingest / 预览
- 不感知客户端本地密钥

### 13.2 不应提供

对 `confidential` repo，平台服务端不应提供：

- 明文预览
- 服务端 query
- 服务端 session 存储
- query_logs 明文分析
- trace 明细
- repo 创建入口
- repo 管理入口
- 任何自动回传数据库记录的埋点

### 13.3 对外通信边界

客户端仅与以下外部设施通信：

- `MinerU`
- `Embedding`
- `LLM`
- `Qdrant`

这些设施按现有模式正常使用；本方案不要求它们为零知识。

## 14. 与现有 Web 服务端的关系

### 14.1 保留现有 `normal` 模式

现有服务端功能继续保留：

- Web 创建知识库
- 服务端上传 / 摄入
- Web Query / SSE
- 管理后台 / 审计 / 统计

### 14.2 新增 `confidential` 模式

服务端不新增任何 `confidential` 支持：

- 无 repo mode 字段
- 无 confidential repo 接口
- 无同步接口
- 无客户端绑定接口

`confidential` 能力由独立客户端承载，平台 Web 继续只处理 `normal` 模式。

## 15. API 与协议原则

建议所有 runner 都共享同构 contract：

- `ingest(request) -> ingest_result`
- `query(request) -> query_result`
- `save_page(request) -> save_result`

这样客户端和服务端调优时共用相同 contract。

## 16. Windows / macOS 要求

### 16.1 第一阶段硬约束

- 不依赖用户自己安装 Python
- 不依赖系统级命令行工具链作为必需项
- 使用系统规范的数据目录
- 支持应用内配置外部服务地址

### 16.2 建议支持的文件类型

第一阶段优先保证跨平台稳定：

- `.md`
- `.txt`
- `.csv`
- `.xlsx`
- `.pdf`

后续再扩展：

- `.docx`
- `.pptx`
- 图片 OCR

## 17. 实施阶段

### Phase 0：架构整理

- 抽共享 `core contract`
- 标记服务端中与 core 耦合的逻辑
- 定义本地 repo manifest / adapter contract

### Phase 1：本地只读原型

- 客户端创建 / 打开 `confidential` repo
- 本地导入 / 保存 / 查询
- 平台服务端完全不参与运行时

### Phase 2：本地持久化与恢复

- 本地加密存储完善
- 导出 / 导入备份包
- 重建索引工具

### Phase 3：能力对齐

- 把 `normal` 模式逐步迁到共享 `core`
- 建立统一评测集
- 对齐 `normal` / `confidential` 的检索和回答质量

## 18. 主要风险

### 18.1 双 runner 长期分叉

若只共享数据模型而不共享 query / ingest 逻辑，后续会出现两套系统。

**应对**：优先抽 core contract 与 pipeline。

### 18.2 客户端本地依赖复杂

文档解析、打包、平台差异会带来分发复杂度。

**应对**：第一阶段收缩文件类型和依赖面。

### 18.3 客户端状态漂移

本地 repo 与 Qdrant 之间可能出现索引漂移，或因机器迁移导致恢复不完整。

**应对**：本地真源 + 导出/导入校验 + 重建索引工具。

## 19. 最终建议

推荐正式采用以下方向：

1. 保留现有 `normal` 服务端知识库
2. 新增 `confidential` 客户端知识库
3. 客户端作为加密知识库唯一运行时入口
4. 平台服务端对加密知识库零通信、零存储、零运行时参与
5. 知识库核心能力抽为共享 Python `core engine`

这条路线的最大价值不是“更安全”本身，而是：

- 让加密知识库具备可持续维护能力
- 保证未来调优时不出现服务端和客户端两套能力分叉
- 为后续 Windows/macOS 正式客户端留出清晰边界
