# Fact Layer Dual-Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为结构化资料（优先 Excel / CSV）增加保真事实层，使查询同时利用 Wiki 叙事层和 Fact Layer，避免行级事实在摄入后丢失。

**Architecture:** 保留现有 `raw -> wiki -> qdrant(page/chunk)` 主流程，同时新增 `facts/records` 文件存储和 `repo_{id}_facts` 向量检索层。摄入时对表格型资料抽取结构化 records 并索引，查询时先做问题路由，再融合 `wiki_evidence`、`chunk_evidence`、`fact_evidence` 回答。

**Tech Stack:** Flask, Jinja2 SSR, Python filesystem storage, Qdrant, OpenAI-compatible embeddings, pytest

---

## File Map

- Modify: `utils.py`
  - 新增表格记录抽取、JSONL 写入/读取、问题类型判定等通用函数
- Modify: `app.py`
  - 上传 Excel / CSV 时生成 Markdown + facts JSONL
  - 查询 API 返回 `fact_evidence`
- Modify: `qdrant_service.py`
  - 新增 fact collection / upsert / search / delete 能力
- Modify: `wiki_engine.py`
  - ingest 时为 facts 建索引
  - query / query_stream / query_with_evidence 增加 fact path
  - fact-aware confidence
- Modify: `static/js/chat.js`
  - 展示 `fact_evidence`
- Modify: `static/css/chat.css`
  - facts 证据面板样式
- Modify: `manage.py`
  - 新增 `rebuild-fact-index`
- Modify: `docs/design.md`
  - 同步 Fact Layer 架构、目录结构、查询流程
- Test: `tests/test_utils.py`
  - records 抽取与问题分类
- Test: `tests/test_qdrant_service.py`
  - fact collection upsert/search
- Test: `tests/test_wiki_engine.py`
  - fact-aware query / stream
- Test: `tests/test_routes.py`
  - API 返回 `fact_evidence`

## Task 1: 表格记录抽取底座

**Files:**
- Modify: `utils.py`
- Test: `tests/test_utils.py`

- [ ] **Step 1: 写失败测试，覆盖 Excel/CSV records 抽取和事实型问题判定**
- [ ] **Step 2: 运行测试，确认失败**
- [ ] **Step 3: 在 `utils.py` 增加表格记录抽取与 JSONL 工具函数**
- [ ] **Step 4: 再跑测试，确认通过**

## Task 2: 上传阶段生成 Markdown + facts records

**Files:**
- Modify: `app.py`
- Modify: `utils.py`
- Test: `tests/test_routes.py`

- [ ] **Step 1: 写失败测试，验证上传 CSV/Excel 后会生成 facts records 文件**
- [ ] **Step 2: 运行测试，确认失败**
- [ ] **Step 3: 修改上传流程，同时产出 Markdown 展示版和 JSONL records**
- [ ] **Step 4: 再跑测试，确认通过**

## Task 3: Qdrant Fact Layer

**Files:**
- Modify: `qdrant_service.py`
- Test: `tests/test_qdrant_service.py`

- [ ] **Step 1: 写失败测试，验证 facts collection upsert/search/delete**
- [ ] **Step 2: 运行测试，确认失败**
- [ ] **Step 3: 实现 `repo_{id}_facts` collection 与 record 向量化**
- [ ] **Step 4: 再跑测试，确认通过**

## Task 4: ingest 时建立事实索引

**Files:**
- Modify: `wiki_engine.py`
- Modify: `manage.py`
- Test: `tests/test_wiki_engine.py`

- [ ] **Step 1: 写失败测试，验证 ingest 会为 facts 建索引**
- [ ] **Step 2: 运行测试，确认失败**
- [ ] **Step 3: 实现 ingest fact index 与 `rebuild-fact-index` 命令**
- [ ] **Step 4: 再跑测试，确认通过**

## Task 5: 查询双通道升级为三通道

**Files:**
- Modify: `wiki_engine.py`
- Modify: `app.py`
- Test: `tests/test_wiki_engine.py`
- Test: `tests/test_routes.py`

- [ ] **Step 1: 写失败测试，验证事实型问题优先返回 `fact_evidence`**
- [ ] **Step 2: 运行测试，确认失败**
- [ ] **Step 3: 实现问题分类、fact path 检索、fact-aware confidence**
- [ ] **Step 4: 让 query / query_stream / query_with_evidence 都返回 `fact_evidence`**
- [ ] **Step 5: 再跑测试，确认通过**

## Task 6: 前端展示事实证据

**Files:**
- Modify: `static/js/chat.js`
- Modify: `static/css/chat.css`
- Test: `tests/test_routes.py`

- [ ] **Step 1: 写失败测试，验证 API schema 包含 `fact_evidence`**
- [ ] **Step 2: 运行测试，确认失败**
- [ ] **Step 3: 在前端加入“结构化事实证据”面板**
- [ ] **Step 4: 再跑测试，确认通过**

## Task 7: 文档同步与回归验证

**Files:**
- Modify: `docs/design.md`
- Test: `tests/test_utils.py`
- Test: `tests/test_qdrant_service.py`
- Test: `tests/test_wiki_engine.py`
- Test: `tests/test_routes.py`

- [ ] **Step 1: 更新设计文档，写明 Fact Layer 架构与流程**
- [ ] **Step 2: 运行相关测试全集**
- [ ] **Step 3: 检查无额外回归**
