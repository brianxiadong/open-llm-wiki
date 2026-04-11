# Phase 1：可信度底座 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有查询链路从"页级来源展示"升级为"双通道 chunk 级证据 + 规则化置信度 + 查询日志"。

**Architecture:**
- `qdrant_service.py`：新增 chunk collection 能力，保留现有 page collection 不变
- `wiki_engine.py`：新增 `query_with_evidence()` + `_score_confidence()`，现有 `query()` / `query_stream()` 升级复用
- `models.py` + `migrations/004_query_logs.sql`：新增 `QueryLog` 表
- `app.py`：query 路由升级返回 schema，写入 query_logs，render-only 兼容
- `static/js/chat.js` + `templates/repo/dashboard.html` + `static/css/chat.css`：双证据面板 + 置信度展示

**Tech Stack:** Flask, SQLAlchemy, Qdrant, Python threading（不引入新依赖）

**Spec:** `docs/superpowers/specs/2026-04-11-phase1-evidence-confidence-design.md`

---

## 关键常量定义（全局通用）

不确定性提示语集合（用于置信度惩罚判断）：
```python
_UNCERTAINTY_PHRASES = (
    "基于现有资料只能推测到",
    "现有证据不足以支持更确定的结论",
    "当前知识库中缺少直接证据",
)
```

置信度分级阈值：
- `score >= 0.75` → `high`
- `0.45 <= score < 0.75` → `medium`
- `score < 0.45` → `low`

Chunk collection 命名：`repo_{repo_id}_chunks`
Chunk point id：`int(md5(f"{repo_id}:{filename}:{chunk_id}")[:16], 16)`

---

## Task 1：Qdrant Chunk 索引与检索

**Files:**
- Modify: `qdrant_service.py`
- Create: `tests/test_qdrant_service.py`

**规范：**

### 新增 `_chunk_collection_name()`
```python
def _chunk_collection_name(self, repo_id: int) -> str:
    return f"repo_{repo_id}_chunks"
```

### 新增 `_stable_chunk_point_id()`
```python
@staticmethod
def _stable_chunk_point_id(repo_id: int, filename: str, chunk_id: str) -> int:
    digest = hashlib.md5(f"{repo_id}:{filename}:{chunk_id}".encode()).hexdigest()
    return int(digest[:16], 16)
```

### 新增 `split_page_into_chunks()`
```python
def split_page_into_chunks(self, content: str) -> list[dict]:
    """Split Markdown page into section chunks.

    Returns list of {"chunk_id": str, "heading": str, "chunk_text": str, "position": int}.
    Target chunk size: 400-800 chars. Short sections are merged forward.
    """
    import re as _re
    lines = content.split("\n")
    sections: list[tuple[str, list[str]]] = []
    current_heading = ""
    current_lines: list[str] = []
    for line in lines:
        m = _re.match(r"^#{1,4}\s+(.+)", line)
        if m:
            if current_lines:
                sections.append((current_heading, current_lines))
            current_heading = m.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_heading, current_lines))

    chunks: list[dict] = []
    position = 0
    pending_heading = ""
    pending_text = ""
    for heading, body_lines in sections:
        text = "\n".join(body_lines).strip()
        if not text:
            continue
        combined = pending_text + ("\n" + text if pending_text else text)
        combined_heading = pending_heading or heading
        if len(combined) < 400 and heading != sections[-1][0]:
            pending_heading = combined_heading
            pending_text = combined
            continue
        if len(combined) <= 800:
            chunks.append({
                "chunk_id": f"{position}",
                "heading": combined_heading,
                "chunk_text": combined[:800],
                "position": position,
            })
            position += 1
            pending_heading = ""
            pending_text = ""
        else:
            # flush pending first if any
            if pending_text:
                chunks.append({
                    "chunk_id": f"{position}",
                    "heading": pending_heading,
                    "chunk_text": pending_text[:800],
                    "position": position,
                })
                position += 1
                pending_heading = ""
                pending_text = ""
            # split long text into 800-char pieces
            for i in range(0, len(text), 800):
                piece = text[i:i + 800]
                chunks.append({
                    "chunk_id": f"{position}",
                    "heading": heading,
                    "chunk_text": piece,
                    "position": position,
                })
                position += 1
    if pending_text:
        chunks.append({
            "chunk_id": f"{position}",
            "heading": pending_heading,
            "chunk_text": pending_text[:800],
            "position": position,
        })
    return chunks
```

### 新增 `ensure_chunk_collection()`
```python
def ensure_chunk_collection(self, repo_id: int) -> None:
    name = self._chunk_collection_name(repo_id)
    try:
        if self._qdrant.collection_exists(collection_name=name):
            return
        self._qdrant.create_collection(
            collection_name=name,
            vectors_config=VectorParams(
                size=self._embedding_dimensions,
                distance=Distance.COSINE,
            ),
        )
    except Exception as e:
        raise QdrantServiceError(str(e)) from e
```

### 新增 `upsert_page_chunks()`
```python
def upsert_page_chunks(
    self,
    repo_id: int,
    filename: str,
    title: str,
    page_type: str,
    content: str,
) -> None:
    chunks = self.split_page_into_chunks(content)
    if not chunks:
        return
    self.ensure_chunk_collection(repo_id)
    collection = self._chunk_collection_name(repo_id)
    points = []
    for chunk in chunks:
        try:
            vector = self._embed(chunk["chunk_text"])
        except QdrantServiceError:
            logger.warning("Chunk embed failed filename=%s chunk_id=%s", filename, chunk["chunk_id"])
            continue
        point_id = self._stable_chunk_point_id(repo_id, filename, chunk["chunk_id"])
        points.append(PointStruct(
            id=point_id,
            vector=vector,
            payload={
                "repo_id": repo_id,
                "filename": filename,
                "page_title": title,
                "page_type": page_type,
                "chunk_id": f"{filename}#{chunk['chunk_id']}",
                "heading": chunk["heading"],
                "chunk_text": chunk["chunk_text"][:800],
                "position": chunk["position"],
            },
        ))
    if points:
        try:
            self._qdrant.upsert(collection_name=collection, points=points)
        except Exception as e:
            raise QdrantServiceError(str(e)) from e
```

### 新增 `search_chunks()`
```python
def search_chunks(
    self, repo_id: int, query: str, limit: int = 8
) -> list[dict[str, Any]]:
    collection = self._chunk_collection_name(repo_id)
    try:
        if not self._qdrant.collection_exists(collection_name=collection):
            return []
    except Exception:
        return []
    try:
        vector = self._embed(query)
        results = self._qdrant.search(
            collection_name=collection,
            query_vector=vector,
            limit=limit,
        )
    except Exception as e:
        raise QdrantServiceError(str(e)) from e
    out = []
    for r in results:
        pl = r.payload or {}
        out.append({
            "chunk_id": pl.get("chunk_id", ""),
            "filename": pl.get("filename", ""),
            "page_title": pl.get("page_title", ""),
            "page_type": pl.get("page_type", ""),
            "heading": pl.get("heading", ""),
            "chunk_text": pl.get("chunk_text", ""),
            "position": pl.get("position", 0),
            "score": r.score,
        })
    return out
```

### 新增 `delete_page_chunks()`
```python
def delete_page_chunks(self, repo_id: int, filename: str) -> None:
    collection = self._chunk_collection_name(repo_id)
    try:
        if not self._qdrant.collection_exists(collection_name=collection):
            return
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        self._qdrant.delete(
            collection_name=collection,
            points_selector=Filter(
                must=[FieldCondition(key="filename", match=MatchValue(value=filename))]
            ),
        )
    except Exception as e:
        logger.warning("delete_page_chunks failed filename=%s: %s", filename, e)
```

### 新增 `delete_chunk_collection()`
```python
def delete_chunk_collection(self, repo_id: int) -> None:
    name = self._chunk_collection_name(repo_id)
    try:
        self._qdrant.delete_collection(collection_name=name)
    except Exception as e:
        logger.warning("delete_chunk_collection failed repo_id=%s: %s", repo_id, e)
```

- [ ] **Step 1: 创建 tests/test_qdrant_service.py**

```python
"""Tests for QdrantService chunk indexing."""
from unittest.mock import MagicMock, patch
import pytest


@pytest.fixture
def qdrant_service():
    mock_client = MagicMock()
    mock_client.collection_exists.return_value = False
    with patch("qdrant_service.QdrantClient", return_value=mock_client), \
         patch("qdrant_service.OpenAI") as mock_openai:
        mock_embed_resp = MagicMock()
        mock_embed_resp.data = [MagicMock(embedding=[0.1] * 128)]
        mock_embed_resp.usage = MagicMock(total_tokens=10)
        mock_openai.return_value.embeddings.create.return_value = mock_embed_resp
        from qdrant_service import QdrantService
        svc = QdrantService(
            "http://fake:6333", "http://fake-embed", "", "test-model", 128
        )
        svc._qdrant = mock_client
        yield svc, mock_client


def test_chunk_collection_name(qdrant_service):
    svc, _ = qdrant_service
    assert svc._chunk_collection_name(1) == "repo_1_chunks"
    assert svc._chunk_collection_name(42) == "repo_42_chunks"


def test_split_page_into_chunks_basic(qdrant_service):
    svc, _ = qdrant_service
    content = """---
title: Test
type: concept
---

# Section A

This is section A content with enough text to form a chunk. """ + "word " * 50 + """

## Section B

This is section B content. """ + "word " * 50

    chunks = svc.split_page_into_chunks(content)
    assert len(chunks) >= 1
    for c in chunks:
        assert "chunk_id" in c
        assert "heading" in c
        assert "chunk_text" in c
        assert "position" in c
        assert len(c["chunk_text"]) <= 800


def test_split_empty_content(qdrant_service):
    svc, _ = qdrant_service
    chunks = svc.split_page_into_chunks("")
    assert chunks == []


def test_split_no_headings(qdrant_service):
    svc, _ = qdrant_service
    content = "Some plain text.\n\nMore paragraphs here. " + "word " * 60
    chunks = svc.split_page_into_chunks(content)
    assert len(chunks) >= 1


def test_stable_chunk_point_id_deterministic(qdrant_service):
    svc, _ = qdrant_service
    id1 = svc._stable_chunk_point_id(1, "page.md", "0")
    id2 = svc._stable_chunk_point_id(1, "page.md", "0")
    id3 = svc._stable_chunk_point_id(1, "page.md", "1")
    assert id1 == id2
    assert id1 != id3
    assert isinstance(id1, int)


def test_upsert_page_chunks_calls_qdrant(qdrant_service):
    svc, mock_client = qdrant_service
    mock_client.collection_exists.return_value = True
    svc.upsert_page_chunks(
        repo_id=1,
        filename="test.md",
        title="Test",
        page_type="concept",
        content="## Section\n\n" + "word " * 80,
    )
    assert mock_client.upsert.called


def test_search_chunks_returns_empty_when_no_collection(qdrant_service):
    svc, mock_client = qdrant_service
    mock_client.collection_exists.return_value = False
    result = svc.search_chunks(repo_id=1, query="test")
    assert result == []


def test_search_chunks_returns_structured_results(qdrant_service):
    svc, mock_client = qdrant_service
    mock_client.collection_exists.return_value = True
    mock_hit = MagicMock()
    mock_hit.score = 0.92
    mock_hit.payload = {
        "chunk_id": "test.md#0",
        "filename": "test.md",
        "page_title": "Test",
        "page_type": "concept",
        "heading": "Section A",
        "chunk_text": "Some text here",
        "position": 0,
    }
    mock_client.search.return_value = [mock_hit]
    results = svc.search_chunks(repo_id=1, query="test query")
    assert len(results) == 1
    assert results[0]["chunk_id"] == "test.md#0"
    assert results[0]["score"] == 0.92
    assert results[0]["heading"] == "Section A"
```

- [ ] **Step 2: 运行测试确认失败**
```bash
cd /Volumes/mydata/codes/github/brianxiadong/open-llm-wiki
python -m pytest tests/test_qdrant_service.py -v 2>&1 | tail -20
```

- [ ] **Step 3: 在 qdrant_service.py 实现所有新方法**（见上方规范）

- [ ] **Step 4: 运行测试确认通过**
```bash
python -m pytest tests/test_qdrant_service.py -v 2>&1 | tail -20
```

- [ ] **Step 5: 运行全量测试确认无回归**
```bash
python -m pytest tests/ --ignore=tests/test_e2e.py -v 2>&1 | tail -20
```

- [ ] **Step 6: git commit**
```bash
git add qdrant_service.py tests/test_qdrant_service.py
git commit -m "feat: add chunk index and search to QdrantService"
```

---

## Task 2：WikiEngine 证据化查询 + 置信度打分

**Files:**
- Modify: `wiki_engine.py`
- Modify: `tests/test_wiki_engine.py`

**规范：**

### 全局常量（放在文件顶部 `_JSON_FENCE_CHARS` 附近）
```python
_UNCERTAINTY_PHRASES = (
    "基于现有资料只能推测到",
    "现有证据不足以支持更确定的结论",
    "当前知识库中缺少直接证据",
)
```

### 新增 `_contains_uncertainty(text: str) -> bool`
```python
def _contains_uncertainty(text: str) -> bool:
    return any(p in text for p in _UNCERTAINTY_PHRASES)
```

### 新增 `WikiEngine._score_confidence()`
```python
def _score_confidence(
    self,
    wiki_hit_count: int,
    chunk_hit_count: int,
    top_chunk_score: float,
    hit_overview: bool,
    both_channels: bool,
    answer_text: str,
) -> dict:
    score = 0.0
    reasons: list[str] = []

    if wiki_hit_count >= 1:
        score += 0.30
        reasons.append(f"命中 {wiki_hit_count} 个 Wiki 页面")
    if wiki_hit_count >= 2:
        score += 0.15
    if chunk_hit_count >= 2:
        score += 0.25
        reasons.append(f"命中 {chunk_hit_count} 个段落证据")
    if chunk_hit_count >= 4:
        score += 0.10
    if both_channels:
        score += 0.15
        reasons.append("LLM Wiki 与向量检索均命中")
    if top_chunk_score >= 0.85:
        score += 0.10
    elif top_chunk_score >= 0.75:
        score += 0.05
    if hit_overview:
        score += 0.05
        reasons.append("命中概览页")
    if _contains_uncertainty(answer_text):
        score -= 0.20
        reasons.append("回答存在证据不足提示")

    score = max(0.0, min(1.0, score))
    if score >= 0.75:
        level = "high"
    elif score >= 0.45:
        level = "medium"
    else:
        level = "low"
        if not reasons:
            reasons.append("证据不足")

    return {"level": level, "score": round(score, 2), "reasons": reasons}
```

### 新增 `WikiEngine.query_with_evidence()`

```python
def query_with_evidence(
    self,
    repo: Any,
    username: str,
    question: str,
    wiki_base_url: str = "",
) -> dict[str, Any]:
    """Query with dual-channel evidence and confidence scoring."""
    from utils import render_markdown as _render_md

    repo_slug = repo.slug
    repo_id = repo.id
    wiki_dir = self._wiki_dir(username, repo_slug)
    schema_content = _read_file(self._schema_path(username, repo_slug))
    index_content = _read_file(self._index_path(username, repo_slug))

    system_base = (
        "你是一个 Wiki 知识助手。根据 Wiki 内容准确回答问题。\n"
        "当关键结论证据不足时，必须使用以下提示语之一：\n"
        "「基于现有资料只能推测到」、「现有证据不足以支持更确定的结论」、"
        "「当前知识库中缺少直接证据」。\n\n"
        + (schema_content or "")
    )

    # -- Wiki path ---------------------------------------------------
    wiki_filenames: list[str] = []
    if index_content:
        pick = self._chat_json(
            system=system_base,
            user=(
                "根据用户问题，从索引中选出最相关的页面（最多 8 个）。\n"
                '返回 JSON: {"filenames": ["a.md", "b.md"]}\n\n'
                f"--- 问题 ---\n{question}\n\n--- index.md ---\n{index_content}"
            ),
            default={"filenames": []},
        )
        wiki_filenames = pick.get("filenames", [])
        if isinstance(wiki_filenames, str):
            wiki_filenames = [wiki_filenames]

    # -- Chunk path --------------------------------------------------
    chunk_hits: list[dict] = []
    if self._qdrant:
        try:
            chunk_hits = self._qdrant.search_chunks(
                repo_id=repo_id, query=question, limit=8
            )
        except QdrantServiceError as exc:
            logger.warning("search_chunks failed: %s", exc)

    # -- Build wiki_evidence -----------------------------------------
    wiki_evidence: list[dict] = []
    page_contents: dict[str, str] = {}
    for fn in wiki_filenames:
        content = _read_file(os.path.join(wiki_dir, fn))
        if not content:
            continue
        page_contents[fn] = content
        fm, _ = _render_md(content)
        reason = "结构化路径选中"
        if fn == "overview.md":
            reason = "高层概览页命中"
        elif fn in [c["filename"] for c in chunk_hits]:
            reason = "结构化路径与片段证据共同支持"
        page_slug = fn.replace(".md", "")
        wiki_evidence.append({
            "filename": fn,
            "title": fm.get("title", page_slug),
            "type": fm.get("type", "unknown"),
            "url": f"{wiki_base_url}/{page_slug}" if wiki_base_url else f"/{page_slug}",
            "reason": reason,
        })

    # -- Build chunk_evidence ----------------------------------------
    chunk_evidence: list[dict] = []
    for hit in chunk_hits:
        fn = hit["filename"]
        if fn not in page_contents:
            content = _read_file(os.path.join(wiki_dir, fn))
            if content:
                page_contents[fn] = content
        page_slug = fn.replace(".md", "")
        chunk_evidence.append({
            "chunk_id": hit["chunk_id"],
            "filename": fn,
            "title": hit.get("page_title", page_slug),
            "heading": hit.get("heading", ""),
            "url": f"{wiki_base_url}/{page_slug}" if wiki_base_url else f"/{page_slug}",
            "snippet": hit.get("chunk_text", "")[:200],
            "score": hit.get("score", 0.0),
        })

    if not page_contents:
        empty_conf = self._score_confidence(0, 0, 0.0, False, False, "")
        return {
            "markdown": "暂无相关 Wiki 内容可以回答该问题。请先导入相关资料。",
            "confidence": empty_conf,
            "wiki_evidence": [],
            "chunk_evidence": [],
            "evidence_summary": "暂无证据。",
            "referenced_pages": [],
            "wiki_sources": [],
            "qdrant_sources": [],
        }

    # -- Build context & answer ---------------------------------------
    context_parts = [f"=== {fn} ===\n{content[:5000]}"
                     for fn, content in page_contents.items()]
    context_block = "\n\n".join(context_parts)

    answer = self._chat_text(
        system=system_base,
        user=(
            "根据以下 Wiki 页面和原文片段回答用户问题，使用 Markdown 格式。\n"
            "若证据不足，请使用指定提示语。\n\n"
            f"--- 问题 ---\n{question}\n\n"
            f"--- Wiki 内容 ---\n{context_block}"
        ),
    )

    # -- Confidence --------------------------------------------------
    top_score = chunk_hits[0]["score"] if chunk_hits else 0.0
    hit_overview = "overview.md" in wiki_filenames
    both = bool(wiki_filenames) and bool(chunk_hits)
    chunk_fns = list({h["filename"] for h in chunk_hits})
    confidence = self._score_confidence(
        wiki_hit_count=len(wiki_filenames),
        chunk_hit_count=len(chunk_hits),
        top_chunk_score=top_score,
        hit_overview=hit_overview,
        both_channels=both,
        answer_text=answer,
    )

    loaded = set(page_contents.keys())
    evidence_summary = (
        f"本回答基于 {len(wiki_evidence)} 个 Wiki 页面和 {len(chunk_evidence)} 个原文片段生成。"
    )

    return {
        "markdown": answer,
        "confidence": confidence,
        "wiki_evidence": wiki_evidence,
        "chunk_evidence": chunk_evidence,
        "evidence_summary": evidence_summary,
        "referenced_pages": list(loaded),
        "wiki_sources": [e["filename"] for e in wiki_evidence],
        "qdrant_sources": chunk_fns,
    }
```

### 升级 `query_stream()` 复用 `query_with_evidence()`

找到现有 `query_stream()` 方法，将其主体替换为调用 `query_with_evidence()` 并在流式阶段只进行 LLM 回答生成：

`query_stream()` 修改要点：
1. 检索阶段（Wiki + chunk）仍由 `query_with_evidence()` 的内部逻辑处理
2. 流式输出阶段继续用 `chat_stream()`
3. 流式结束后用完整 answer 文本重算 confidence（应用不确定性惩罚）
4. `done` 事件返回 `answer`（兼容）、`markdown`、`confidence`、`wiki_evidence`、`chunk_evidence`、`evidence_summary`、`wiki_sources`（`string[]`）、`qdrant_sources`（`string[]`）

具体实现：在 `query_stream()` 中：
- 先调用内部共享逻辑（不含最终 LLM 回答生成）得到 `wiki_evidence`、`chunk_evidence`、`page_contents` 等
- 用 `chat_stream()` 流式生成回答
- 流式结束后组装 `done` payload

**为保持 Task 2 规模可控，`query_stream()` 的具体改写在 Step 3 详细实现，可参考 `query_with_evidence()` 共享逻辑进行重构。**

- [ ] **Step 1: 写测试（置信度规则 + 证据化查询）**

在 `tests/test_wiki_engine.py` 末尾添加：

```python
# ── confidence scoring ────────────────────────────────────────

def test_confidence_high():
    from wiki_engine import WikiEngine
    mock_llm = MagicMock()
    engine = WikiEngine(mock_llm, MagicMock(), "/tmp")
    result = engine._score_confidence(
        wiki_hit_count=3, chunk_hit_count=4,
        top_chunk_score=0.90, hit_overview=True,
        both_channels=True, answer_text="正常回答"
    )
    assert result["level"] == "high"
    assert result["score"] >= 0.75


def test_confidence_medium():
    from wiki_engine import WikiEngine
    mock_llm = MagicMock()
    engine = WikiEngine(mock_llm, MagicMock(), "/tmp")
    result = engine._score_confidence(
        wiki_hit_count=1, chunk_hit_count=2,
        top_chunk_score=0.78, hit_overview=False,
        both_channels=True, answer_text="正常回答"
    )
    assert result["level"] == "medium"
    assert 0.45 <= result["score"] < 0.75


def test_confidence_low_no_evidence():
    from wiki_engine import WikiEngine
    mock_llm = MagicMock()
    engine = WikiEngine(mock_llm, MagicMock(), "/tmp")
    result = engine._score_confidence(
        wiki_hit_count=0, chunk_hit_count=0,
        top_chunk_score=0.0, hit_overview=False,
        both_channels=False, answer_text=""
    )
    assert result["level"] == "low"
    assert result["score"] < 0.45


def test_confidence_uncertainty_penalty():
    from wiki_engine import WikiEngine
    mock_llm = MagicMock()
    engine = WikiEngine(mock_llm, MagicMock(), "/tmp")
    result = engine._score_confidence(
        wiki_hit_count=1, chunk_hit_count=2,
        top_chunk_score=0.78, hit_overview=False,
        both_channels=True,
        answer_text="基于现有资料只能推测到一些内容"
    )
    result_no_penalty = engine._score_confidence(
        wiki_hit_count=1, chunk_hit_count=2,
        top_chunk_score=0.78, hit_overview=False,
        both_channels=True, answer_text="正常回答"
    )
    assert result["score"] < result_no_penalty["score"]


def test_query_with_evidence_no_content(tmp_data_dir):
    from wiki_engine import WikiEngine
    mock_qdrant = MagicMock()
    mock_qdrant.search_chunks.return_value = []
    engine = WikiEngine(MagicMock(), mock_qdrant, tmp_data_dir)
    repo = MagicMock(); repo.slug = "empty"; repo.id = 1
    result = engine.query_with_evidence(repo, "alice", "test?")
    assert "markdown" in result
    assert "confidence" in result
    assert result["confidence"]["level"] == "low"
    assert result["wiki_evidence"] == []
    assert result["chunk_evidence"] == []


def test_query_with_evidence_with_pages(tmp_data_dir):
    wiki_dir = os.path.join(tmp_data_dir, "alice", "ev1", "wiki")
    os.makedirs(wiki_dir, exist_ok=True)
    with open(os.path.join(wiki_dir, "index.md"), "w") as f:
        f.write("---\ntitle: 首页\ntype: index\n---\n\n- [C](concept.md)\n")
    with open(os.path.join(wiki_dir, "concept.md"), "w") as f:
        f.write("---\ntitle: Concept\ntype: concept\n---\n\n# Concept\n\nDetails.\n")

    mock_llm = MagicMock()
    mock_llm.chat_json.return_value = {"filenames": ["concept.md"]}
    mock_llm.chat.return_value = "Here is the answer."

    mock_qdrant = MagicMock()
    mock_qdrant.search_chunks.return_value = [{
        "chunk_id": "concept.md#0", "filename": "concept.md",
        "page_title": "Concept", "page_type": "concept",
        "heading": "Details", "chunk_text": "Details here.", "position": 0,
        "score": 0.88,
    }]

    engine = WikiEngine(mock_llm, mock_qdrant, tmp_data_dir)
    repo = MagicMock(); repo.slug = "ev1"; repo.id = 1
    result = engine.query_with_evidence(repo, "alice", "What is it?")

    assert result["markdown"]
    assert len(result["wiki_evidence"]) >= 1
    assert len(result["chunk_evidence"]) >= 1
    assert result["confidence"]["level"] in ("high", "medium", "low")
    assert "evidence_summary" in result
```

- [ ] **Step 2: 运行测试确认失败**
```bash
python -m pytest tests/test_wiki_engine.py -k "confidence or query_with_evidence" -v 2>&1 | tail -20
```

- [ ] **Step 3: 在 wiki_engine.py 实现**
  1. 在文件顶部（`_JSON_FENCE_CHARS` 下方）加 `_UNCERTAINTY_PHRASES` 和 `_contains_uncertainty()`
  2. 在 `WikiEngine` 类中加 `_score_confidence()`
  3. 在 `WikiEngine` 类中加 `query_with_evidence()`
  4. 修改 `query_stream()`：将检索逻辑抽取为共享内部方法 `_retrieve_evidence()`，`query_with_evidence()` 和 `query_stream()` 都调用它；`query_stream()` 在流式结束后用完整 answer 文本重算 confidence；`done` 事件新增字段

- [ ] **Step 4: 运行测试确认通过**
```bash
python -m pytest tests/test_wiki_engine.py -v 2>&1 | tail -20
```

- [ ] **Step 5: 全量测试**
```bash
python -m pytest tests/ --ignore=tests/test_e2e.py -v 2>&1 | tail -20
```

- [ ] **Step 6: git commit**
```bash
git add wiki_engine.py tests/test_wiki_engine.py
git commit -m "feat: add query_with_evidence and confidence scoring to WikiEngine"
```

---

## Task 3：QueryLog 模型 + Migration + 路由落地

**Files:**
- Modify: `models.py`
- Create: `migrations/004_query_logs.sql`
- Modify: `app.py`（query_api + render-only + query_stream 路由）
- Modify: `tests/test_routes.py`
- Modify: `tests/test_models.py`

### models.py 新增 QueryLog

```python
class QueryLog(db.Model):
    __tablename__ = "query_logs"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    repo_id = db.Column(db.Integer, db.ForeignKey("repos.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    question = db.Column(db.Text, nullable=False)
    answer_preview = db.Column(db.Text, nullable=True)
    confidence = db.Column(db.String(16), nullable=False, default="low")
    wiki_hit_count = db.Column(db.Integer, nullable=False, default=0)
    chunk_hit_count = db.Column(db.Integer, nullable=False, default=0)
    used_wiki_pages = db.Column(db.Text().with_variant(LONGTEXT(), "mysql"), nullable=True)
    used_chunk_ids = db.Column(db.Text().with_variant(LONGTEXT(), "mysql"), nullable=True)
    evidence_summary = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utc_now)
```

### migrations/004_query_logs.sql

```sql
CREATE TABLE IF NOT EXISTS query_logs (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    repo_id          INT NOT NULL,
    user_id          INT NOT NULL,
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
```

### app.py 路由改动

**`query_api` 路由：**
1. 不再调用旧 `wiki_engine.query()`，改为调用 `wiki_engine.query_with_evidence()`
2. 写入 `QueryLog`
3. 在 render-only 分支（`_rendered_answer` 不为 None 时），保留现有功能，并额外回传 `confidence`、`wiki_evidence`、`chunk_evidence`、`evidence_summary`（从请求体读取，可为空）
4. 返回新 schema（含兼容旧字段）

**新 `query_api` 主路径返回值：**
```python
return jsonify(
    html=answer_html,
    markdown=result["markdown"],
    answer=result["markdown"],  # 兼容键
    confidence=result["confidence"],
    wiki_evidence=result["wiki_evidence"],
    chunk_evidence=result["chunk_evidence"],
    evidence_summary=result["evidence_summary"],
    referenced_pages=result["referenced_pages"],
    # 兼容旧键
    references=[_fn_to_ref(fn) for fn in result["wiki_sources"]],
    wiki_sources=[_fn_to_ref(fn) for fn in result["wiki_sources"]],
    qdrant_sources=[_fn_to_ref(fn) for fn in result["qdrant_sources"]],
)
```

**`query_stream` 路由：**
- 在 `done` 事件中额外返回 `confidence`、`wiki_evidence`、`chunk_evidence`、`evidence_summary`
- 保持 `answer`、`wiki_sources`（`string[]`）、`qdrant_sources`（`string[]`）不变（兼容现有 chat.js）

- [ ] **Step 1: 写测试**

```python
# tests/test_models.py 末尾添加
def test_query_log_creation(app):
    with app.app_context():
        from models import QueryLog, db
        log = QueryLog(
            repo_id=1, user_id=1,
            question="test question",
            answer_preview="test answer",
            confidence="medium",
            wiki_hit_count=2, chunk_hit_count=3,
            evidence_summary="2 pages, 3 chunks",
        )
        db.session.add(log)
        db.session.commit()
        fetched = QueryLog.query.filter_by(question="test question").first()
        assert fetched is not None
        assert fetched.confidence == "medium"

# tests/test_routes.py 末尾添加
def test_query_api_returns_confidence(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    fake = {
        "markdown": "Test answer",
        "confidence": {"level": "medium", "score": 0.6, "reasons": ["命中 1 个页面"]},
        "wiki_evidence": [{"filename": "overview.md", "title": "概览",
                           "type": "overview", "url": "/test", "reason": "高层概览页命中"}],
        "chunk_evidence": [],
        "evidence_summary": "基于 1 个页面生成。",
        "referenced_pages": ["overview.md"],
        "wiki_sources": ["overview.md"],
        "qdrant_sources": [],
    }
    with patch.object(app.wiki_engine, "query_with_evidence", return_value=fake):
        resp = client.post(f"/alice/{slug}/query", json={"q": "test"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "confidence" in data
    assert data["confidence"]["level"] == "medium"
    assert "wiki_evidence" in data
    assert "chunk_evidence" in data
    assert "html" in data
    # 兼容旧键
    assert "wiki_sources" in data
    assert "qdrant_sources" in data


def test_query_api_render_only_returns_confidence(sample_repo):
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    resp = client.post(
        f"/alice/{slug}/query",
        json={"q": "test", "_rendered_answer": "# Hello",
              "_confidence": {"level": "low", "score": 0.2, "reasons": []},
              "_wiki_evidence": [], "_chunk_evidence": [],
              "_evidence_summary": ""},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "html" in data
    assert "confidence" in data


def test_query_stream_done_has_evidence(sample_repo, app):
    client, repo_info = sample_repo
    slug = repo_info["slug"]

    def fake_stream(repo, username, question):
        yield {"event": "progress", "data": {"message": "检索中", "percent": 10}}
        yield {"event": "answer_chunk", "data": {"chunk": "Hi"}}
        yield {"event": "done", "data": {
            "answer": "Hi", "markdown": "Hi",
            "confidence": {"level": "low", "score": 0.1, "reasons": []},
            "wiki_evidence": [], "chunk_evidence": [],
            "evidence_summary": "暂无证据。",
            "wiki_sources": [], "qdrant_sources": [],
            "referenced_pages": [],
        }}

    with patch.object(app.wiki_engine, "query_stream", side_effect=fake_stream):
        resp = client.get(f"/alice/{slug}/query/stream?q=test")
    assert resp.status_code == 200
    assert b"confidence" in resp.data
```

- [ ] **Step 2: 运行测试确认失败**
```bash
python -m pytest tests/test_models.py -k "query_log" -v
python -m pytest tests/test_routes.py -k "confidence or render_only_returns" -v 2>&1 | tail -20
```

- [ ] **Step 3: 实现**
  1. `models.py`：加 `QueryLog`
  2. `migrations/004_query_logs.sql`：建表
  3. `app.py`：
     - `query_api`：调用 `query_with_evidence()`，写 QueryLog，更新返回 schema，更新 render-only 分支
     - `query_stream`：在 `done` 事件 payload 中加 `confidence / wiki_evidence / chunk_evidence / evidence_summary`

- [ ] **Step 4: 运行测试**
```bash
python -m pytest tests/test_models.py tests/test_routes.py -v 2>&1 | tail -30
```

- [ ] **Step 5: 全量测试**
```bash
python -m pytest tests/ --ignore=tests/test_e2e.py -v 2>&1 | tail -20
```

- [ ] **Step 6: git commit**
```bash
git add models.py migrations/004_query_logs.sql app.py tests/test_models.py tests/test_routes.py
git commit -m "feat: add QueryLog model and upgrade query routes to evidence schema"
```

---

## Task 4：聊天 UI 双证据展示

**Files:**
- Modify: `static/js/chat.js`
- Modify: `static/css/chat.css`
- Modify: `templates/repo/dashboard.html`（无需改动，chatConfig 已有所需 URL）

**规范：**

### chat.js 改动要点

1. `addAIMessage()` 函数签名扩展，支持新参数：`confidence`、`wikiEvidence`、`chunkEvidence`、`evidenceSummary`
2. 渲染顺序：`置信度标识` → `回答正文` → `LLM Wiki 证据区` → `原文片段证据区`
3. 新增辅助函数：
   - `renderConfidenceBadge(confidence)`
   - `renderWikiEvidence(items)`
   - `renderChunkEvidence(items)`
4. 低置信度时在回答顶部加警告横幅
5. SSE `done` 事件处理：读取新字段，传入 render-only POST，再由 `addAIMessage()` 渲染
6. fallback（非 SSE）路径：`addAIMessage()` 同样消费新字段

**置信度 badge HTML 模板：**
```html
<div class="confidence-badge confidence-{level}" title="{reasons_text}">
  <i data-lucide="shield-check"></i>
  <span>{label}</span>
  <button class="confidence-toggle">▾</button>
  <div class="confidence-reasons hidden">{reasons_list}</div>
</div>
```
- `high`：`高置信度`
- `medium`：`中置信度`
- `low`：`低置信度`（加警告提示）

**Wiki 证据区 HTML 模板（单条）：**
```html
<div class="evidence-item evidence-wiki">
  <span class="badge badge-{type}">{type}</span>
  <a href="{url}">{title}</a>
  <span class="evidence-reason">{reason}</span>
</div>
```

**Chunk 证据区 HTML 模板（单条）：**
```html
<div class="evidence-item evidence-chunk">
  <a href="{url}" class="evidence-chunk-title">{title}</a>
  {heading && <span class="evidence-heading">§ {heading}</span>}
  <div class="evidence-snippet">{snippet}</div>
  <span class="evidence-score">{score_pct}%</span>
</div>
```

### chat.css 新增样式块

```css
/* Evidence panels */
.evidence-panel { margin-top: 0.75rem; border-top: 1px solid var(--pico-muted-border-color); padding-top: 0.75rem; }
.evidence-panel-header { font-size: 0.82rem; font-weight: 600; color: var(--pico-muted-color); display: flex; align-items: center; gap: 0.35rem; margin-bottom: 0.5rem; }
.evidence-items { display: flex; flex-direction: column; gap: 0.4rem; }
.evidence-item { font-size: 0.83rem; padding: 0.35rem 0.5rem; border-radius: 4px; background: var(--pico-card-background-color); border: 1px solid var(--pico-muted-border-color); }
.evidence-item a { font-weight: 500; }
.evidence-reason { color: var(--pico-muted-color); font-size: 0.78rem; margin-left: 0.4rem; }
.evidence-heading { font-size: 0.78rem; color: var(--pico-primary); margin-left: 0.4rem; }
.evidence-snippet { color: var(--pico-muted-color); font-size: 0.8rem; margin-top: 0.2rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
.evidence-score { float: right; font-size: 0.75rem; color: var(--pico-muted-color); }

/* Confidence badge */
.confidence-badge { display: inline-flex; align-items: center; gap: 0.3rem; padding: 0.2rem 0.6rem; border-radius: 99px; font-size: 0.78rem; font-weight: 600; margin-bottom: 0.5rem; cursor: pointer; }
.confidence-badge.confidence-high { background: #d1fae5; color: #065f46; }
.confidence-badge.confidence-medium { background: #fef3c7; color: #92400e; }
.confidence-badge.confidence-low { background: #fee2e2; color: #991b1b; }
.confidence-reasons { font-size: 0.78rem; padding: 0.3rem 0; }
.confidence-reasons.hidden { display: none; }
.low-confidence-warning { background: #fff7ed; border: 1px solid #fed7aa; border-radius: 6px; padding: 0.5rem 0.75rem; font-size: 0.82rem; color: #9a3412; margin-bottom: 0.5rem; }
```

- [ ] **Step 1: 修改 chat.js**
  1. `addAIMessage(html, refs, markdown, wikiSources, qdrantSources, confidence, wikiEvidence, chunkEvidence, evidenceSummary)` — 新增 4 个尾部参数（可选，缺省时行为不变）
  2. 加 `renderConfidenceBadge()`、`renderWikiEvidence()`、`renderChunkEvidence()` 辅助函数
  3. 更新 SSE `done` 事件处理，将新字段传入 render-only POST，同时传入 `addAIMessage()`
  4. 更新 fallback 非 SSE 路径，从响应 JSON 读取新字段

- [ ] **Step 2: 在 chat.css 末尾追加新样式**

- [ ] **Step 3: 人工验证思路（无需真实浏览器）**
  - 创建一个最小 HTML 文件，模拟 `addAIMessage()` 的渲染输出，确保样式正确加载
  - 测试 `renderConfidenceBadge()` 对 high/medium/low 三种 level 的输出

- [ ] **Step 4: 全量测试（前端 HTML 结构）**
```bash
python -m pytest tests/test_frontend.py -v 2>&1 | tail -20
```

- [ ] **Step 5: git commit**
```bash
git add static/js/chat.js static/css/chat.css
git commit -m "feat: dual-evidence panels and confidence badge in chat UI"
```

---

## Task 5：Ingest 时同步 Chunk Index + 回填命令 + 测试收尾 + 文档

**Files:**
- Modify: `wiki_engine.py`（ingest 流程加 chunk upsert）
- Modify: `app.py`（edit_page、save_query_page 中加 chunk upsert）
- Modify: `manage.py`（新增 rebuild-chunk-index 命令）
- Modify: `tests/test_contracts.py`（新增契约测试）
- Modify: `docs/design.md`

### wiki_engine.py ingest 改动

在 `ingest()` 的向量索引阶段（Step 4），在 `self._qdrant.upsert_page()` 调用之后，加：
```python
try:
    self._qdrant.upsert_page_chunks(
        repo_id=repo_id,
        filename=filename,
        title=title,
        page_type=page_type,
        content=content,
    )
except QdrantServiceError as exc:
    logger.error("Chunk upsert failed for %s: %s", filename, exc)
```

在删除 wiki 页面时（`_purge_source_wiki`），在 `app.qdrant.delete_page()` 之后加：
```python
try:
    app.qdrant.delete_page_chunks(repo.id, fname)
except Exception:
    pass
```

### app.py edit_page 改动

在保存 wiki 页面后的 `qdrant.upsert_page()` 之后加 chunk upsert：
```python
try:
    current_app.qdrant.upsert_page_chunks(
        repo_id=repo.id, filename=f"{page_slug}.md",
        title=fm.get("title", page_slug),
        page_type=fm.get("type", "unknown"), content=content,
    )
except Exception:
    pass
```

### manage.py 新增 rebuild-chunk-index 命令

```python
@cli.command("rebuild-chunk-index")
@click.option("--repo-id", default=None, type=int, help="只重建指定 repo 的 chunk 索引")
def rebuild_chunk_index(repo_id):
    """重建 Qdrant chunk 索引（用于存量数据回填）"""
    from app import create_app
    from models import Repo, db
    from utils import list_wiki_pages, get_repo_path

    app = create_app()
    with app.app_context():
        if not app.qdrant:
            click.echo("Qdrant 不可用，跳过。")
            return
        query = Repo.query
        if repo_id:
            query = query.filter_by(id=repo_id)
        repos = query.all()
        for repo in repos:
            user = repo.user
            base = get_repo_path(app.config["DATA_DIR"], user.username, repo.slug)
            wiki_dir = os.path.join(base, "wiki")
            if not os.path.isdir(wiki_dir):
                continue
            pages = list_wiki_pages(wiki_dir)
            click.echo(f"Rebuilding chunks for repo={repo.id} ({repo.slug}): {len(pages)} pages")
            for page in pages:
                fpath = os.path.join(wiki_dir, page["filename"])
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        content = f.read()
                    app.qdrant.upsert_page_chunks(
                        repo_id=repo.id,
                        filename=page["filename"],
                        title=page["title"],
                        page_type=page["type"],
                        content=content,
                    )
                    click.echo(f"  OK: {page['filename']}")
                except Exception as exc:
                    click.echo(f"  FAIL: {page['filename']}: {exc}")
    click.echo("Done.")
```

### 契约测试新增（test_contracts.py 末尾）

```python
def test_query_api_response_has_confidence_fields(sample_repo, app):
    """query API 必须返回 confidence、wiki_evidence、chunk_evidence 字段。"""
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    fake = {
        "markdown": "answer", "confidence": {"level": "low", "score": 0.1, "reasons": []},
        "wiki_evidence": [], "chunk_evidence": [], "evidence_summary": "",
        "referenced_pages": [], "wiki_sources": [], "qdrant_sources": [],
    }
    with patch.object(app.wiki_engine, "query_with_evidence", return_value=fake):
        resp = client.post(f"/alice/{slug}/query", json={"q": "test"})
    data = resp.get_json()
    for field in ("confidence", "wiki_evidence", "chunk_evidence", "evidence_summary",
                  "html", "markdown", "wiki_sources", "qdrant_sources"):
        assert field in data, f"Missing field: {field}"
    assert isinstance(data["confidence"], dict)
    assert "level" in data["confidence"]
    assert "score" in data["confidence"]
    assert "reasons" in data["confidence"]


def test_query_stream_done_has_evidence_fields(sample_repo, app):
    """SSE done 事件必须包含 confidence、wiki_evidence、chunk_evidence 字段。"""
    client, repo_info = sample_repo
    slug = repo_info["slug"]

    def fake_stream(repo, username, question):
        yield {"event": "done", "data": {
            "answer": "Hi", "markdown": "Hi",
            "confidence": {"level": "low", "score": 0.1, "reasons": []},
            "wiki_evidence": [], "chunk_evidence": [],
            "evidence_summary": "暂无证据。",
            "wiki_sources": [], "qdrant_sources": [],
            "referenced_pages": [],
        }}

    with patch.object(app.wiki_engine, "query_stream", side_effect=fake_stream):
        resp = client.get(f"/alice/{slug}/query/stream?q=test")
    body = resp.data.decode()
    assert "confidence" in body
    assert "wiki_evidence" in body
    assert "chunk_evidence" in body
```

- [ ] **Step 1: 写测试**（见上方契约测试）

- [ ] **Step 2: 运行测试确认失败**
```bash
python -m pytest tests/test_contracts.py -k "confidence_fields or evidence_fields" -v
```

- [ ] **Step 3: 在 wiki_engine.py ingest() 加 chunk upsert**

- [ ] **Step 4: 在 app.py edit_page / save_query_page 加 chunk upsert**

- [ ] **Step 5: 在 _purge_source_wiki 加 delete_page_chunks**

- [ ] **Step 6: 在 manage.py 加 rebuild-chunk-index 命令**

- [ ] **Step 7: 运行所有测试**
```bash
python -m pytest tests/ --ignore=tests/test_e2e.py -v 2>&1 | tail -30
```
Expected: 全部通过

- [ ] **Step 8: 更新 docs/design.md**

在以下章节同步更新：
1. **§4.2 Query**：升级为"双通道证据模型 + 规则化置信度"描述，加返回 schema 说明
2. **§5.1 数据模型**：新增 `query_logs` 表结构
3. **§8.6 Qdrant**：说明 page collection + chunk collection 双层结构
4. **§6.1 路由**：`query_api` 返回 schema 升级说明

- [ ] **Step 9: git commit**
```bash
git add wiki_engine.py app.py manage.py tests/test_contracts.py docs/design.md
git commit -m "feat: sync chunk index on ingest/edit, add rebuild command, contract tests"
```

- [ ] **Step 10: 推送**
```bash
git push origin main
```
