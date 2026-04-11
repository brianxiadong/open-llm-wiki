"""Core wiki operations: ingest, query, lint."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Generator

from exceptions import LLMClientError, QdrantServiceError, WikiEngineError
from llm_client import LLMClient
from qdrant_service import QdrantService
from utils import list_wiki_pages, render_markdown

logger = logging.getLogger(__name__)

_JSON_FENCE_CHARS = ("`", "```")

_UNCERTAINTY_PHRASES = (
    "基于现有资料只能推测到",
    "现有证据不足以支持更确定的结论",
    "当前知识库中缺少直接证据",
)


def _contains_uncertainty(text: str) -> bool:
    return any(p in text for p in _UNCERTAINTY_PHRASES)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_file(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _clean_llm_markdown(text: str) -> str:
    """Strip wrapping code fences that LLMs sometimes add around markdown."""
    stripped = text.strip()
    if stripped.startswith("```") and not stripped.startswith("---"):
        first_nl = stripped.find("\n")
        if first_nl != -1:
            stripped = stripped[first_nl + 1:]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3].rstrip()
    return stripped


def _write_file(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cleaned = _clean_llm_markdown(content)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(cleaned)


def _safe_json_loads(text: str) -> dict | list:
    """Parse JSON from LLM output, stripping optional markdown fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.index("\n") if "\n" in cleaned else len(cleaned)
        cleaned = cleaned[first_nl + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[: -3]
    cleaned = cleaned.strip()
    return json.loads(cleaned)


def _progress(phase: str, progress: int, message: str, **extra: Any) -> dict:
    return {"phase": phase, "progress": progress, "message": message, **extra}


# ---------------------------------------------------------------------------
# WikiEngine
# ---------------------------------------------------------------------------


class WikiEngine:
    """Orchestrates ingest / query / lint using an LLM and vector store."""

    def __init__(
        self,
        llm_client: LLMClient,
        qdrant_service: QdrantService,
        data_dir: str,
    ) -> None:
        self._llm = llm_client
        self._qdrant = qdrant_service
        self._data_dir = data_dir

    # -- path helpers -------------------------------------------------------

    def _repo_base(self, username: str, repo_slug: str) -> str:
        return os.path.join(self._data_dir, username, repo_slug)

    def _wiki_dir(self, username: str, repo_slug: str) -> str:
        return os.path.join(self._repo_base(username, repo_slug), "wiki")

    def _raw_dir(self, username: str, repo_slug: str) -> str:
        return os.path.join(self._repo_base(username, repo_slug), "raw")

    def _schema_path(self, username: str, repo_slug: str) -> str:
        return os.path.join(self._wiki_dir(username, repo_slug), "schema.md")

    def _index_path(self, username: str, repo_slug: str) -> str:
        return os.path.join(self._wiki_dir(username, repo_slug), "index.md")

    def _log_path(self, username: str, repo_slug: str) -> str:
        return os.path.join(self._wiki_dir(username, repo_slug), "log.md")

    # -- LLM wrappers -------------------------------------------------------

    def _chat_json(
        self,
        system: str,
        user: str,
        *,
        retries: int = 1,
        default: dict | list | None = None,
    ) -> Any:
        """Call LLM expecting JSON.  Retry once on parse failure."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        for attempt in range(1 + retries):
            try:
                result = self._llm.chat_json(messages)
                if result:
                    return result
            except (LLMClientError, json.JSONDecodeError) as exc:
                logger.warning(
                    "chat_json attempt=%s failed: %s", attempt + 1, exc
                )
        if default is not None:
            return default
        return {}

    def _chat_text(self, system: str, user: str) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            return self._llm.chat(messages, temperature=0.5)
        except LLMClientError as exc:
            logger.error("chat_text failed: %s", exc)
            return ""

    # -----------------------------------------------------------------------
    # CONFIDENCE SCORING
    # -----------------------------------------------------------------------

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

    # -----------------------------------------------------------------------
    # INGEST
    # -----------------------------------------------------------------------

    def ingest(
        self,
        repo: Any,
        username: str,
        source_filename: str,
    ) -> Generator[dict, None, None]:
        """Ingest a raw source file into the wiki.

        Yields progress dicts ``{"phase", "progress", "message", …}``.
        """
        repo_slug = repo.slug
        repo_id = repo.id
        wiki_dir = self._wiki_dir(username, repo_slug)
        raw_dir = self._raw_dir(username, repo_slug)

        # -- 1. Read inputs -------------------------------------------------
        source_path = os.path.join(raw_dir, source_filename)
        source_content = _read_file(source_path)
        if not source_content:
            yield _progress("error", 0, f"Source file not found or empty: {source_filename}")
            return

        schema_content = _read_file(self._schema_path(username, repo_slug))
        index_content = _read_file(self._index_path(username, repo_slug))

        system_base = (
            "你是一个专业的 Wiki 维护者。你负责将原始资料整理成结构化的 Wiki 页面。\n"
            "请严格遵守以下 schema 规范：\n\n" + (schema_content or "(暂无 schema)")
        )

        yield _progress("read", 5, f"Read source: {source_filename} ({len(source_content)} chars)")

        # -- 2. Analyze -----------------------------------------------------
        yield _progress("analyze", 10, "Analyzing source content …")

        truncated_source = source_content[:30000]
        analysis = self._chat_json(
            system=system_base,
            user=(
                "请分析以下原始资料，返回 JSON：\n"
                '{"summary": "...", "key_entities": ["..."], '
                '"key_concepts": ["..."], "main_findings": ["..."]}\n\n'
                f"--- 原始资料 ({source_filename}) ---\n{truncated_source}"
            ),
            default={
                "summary": "Unable to analyze",
                "key_entities": [],
                "key_concepts": [],
                "main_findings": [],
            },
        )

        yield _progress(
            "analyze", 25, "Analysis complete",
            analysis=analysis,
        )

        # -- 3. Plan --------------------------------------------------------
        yield _progress("plan", 30, "Planning wiki updates …")

        plan = self._chat_json(
            system=system_base,
            user=(
                "根据以下分析结果和当前 Wiki 索引，规划需要创建和更新的页面。\n"
                "返回 JSON：\n"
                '{"pages_to_create": [{"filename": "xxx.md", "title": "...", '
                '"type": "concept|guide|reference|overview|comparison", '
                '"reason": "..."}], '
                '"pages_to_update": [{"filename": "xxx.md", '
                '"reason": "...", "what_to_add": "..."}]}\n\n'
                f"--- 分析结果 ---\n{json.dumps(analysis, ensure_ascii=False)}\n\n"
                f"--- 当前 index.md ---\n{index_content or '(空)'}"
            ),
            default={"pages_to_create": [], "pages_to_update": []},
        )

        pages_to_create: list[dict] = plan.get("pages_to_create", [])
        pages_to_update: list[dict] = plan.get("pages_to_update", [])
        total_pages = len(pages_to_create) + len(pages_to_update)

        yield _progress(
            "plan", 40,
            f"Plan: create {len(pages_to_create)}, update {len(pages_to_update)} pages",
            plan=plan,
        )

        # -- 4. Execute: create pages ---------------------------------------
        created_files: list[str] = []
        updated_files: list[str] = []

        for idx, page_spec in enumerate(pages_to_create):
            filename = page_spec.get("filename", f"page-{idx}.md")
            title = page_spec.get("title", filename.replace(".md", ""))
            page_type = page_spec.get("type", "concept")
            pct = 40 + int(30 * (idx + 1) / max(total_pages, 1))

            yield _progress("execute", pct, f"Creating {filename} …")

            try:
                page_md = self._chat_text(
                    system=system_base,
                    user=(
                        f"请为以下主题生成完整的 Wiki 页面（Markdown 格式，包含 YAML frontmatter）。\n"
                        f"文件名: {filename}\n标题: {title}\n类型: {page_type}\n"
                        f"原因: {page_spec.get('reason', '')}\n\n"
                        f"--- 原始资料摘要 ---\n{json.dumps(analysis, ensure_ascii=False)}\n\n"
                        f"--- 原始资料节选 ---\n{truncated_source[:8000]}\n\n"
                        "请确保：\n"
                        "1. 以 YAML frontmatter 开头 (title, type, tags, source, updated)\n"
                        "2. 内容准确、结构清晰\n"
                        "3. 适当添加对其他页面的交叉引用链接 [Title](other-page.md)"
                    ),
                )
                if page_md:
                    _write_file(os.path.join(wiki_dir, filename), page_md)
                    created_files.append(filename)
                    logger.info("Created wiki page: %s", filename)
                else:
                    logger.warning("LLM returned empty content for %s", filename)
            except Exception as exc:
                logger.exception("Failed to create page %s: %s", filename, exc)
                yield _progress("execute", pct, f"Failed to create {filename}: {exc}")

        # -- 5. Execute: update pages ---------------------------------------
        for idx, page_spec in enumerate(pages_to_update):
            filename = page_spec.get("filename", "")
            if not filename:
                continue
            pct = 40 + int(30 * (len(pages_to_create) + idx + 1) / max(total_pages, 1))

            yield _progress("execute", pct, f"Updating {filename} …")

            existing_path = os.path.join(wiki_dir, filename)
            existing_content = _read_file(existing_path)
            if not existing_content:
                logger.warning("Page to update not found: %s", filename)
                yield _progress("execute", pct, f"Skipped {filename} (not found)")
                continue

            try:
                updated_md = self._chat_text(
                    system=system_base,
                    user=(
                        f"请更新以下 Wiki 页面。\n"
                        f"更新原因: {page_spec.get('reason', '')}\n"
                        f"需要添加的内容: {page_spec.get('what_to_add', '')}\n\n"
                        f"--- 当前页面内容 ({filename}) ---\n{existing_content}\n\n"
                        f"--- 原始资料摘要 ---\n{json.dumps(analysis, ensure_ascii=False)}\n\n"
                        "请返回更新后的完整页面（保留 frontmatter，更新 updated 日期）。"
                    ),
                )
                if updated_md:
                    _write_file(existing_path, updated_md)
                    updated_files.append(filename)
                    logger.info("Updated wiki page: %s", filename)
                else:
                    logger.warning("LLM returned empty update for %s", filename)
            except Exception as exc:
                logger.exception("Failed to update page %s: %s", filename, exc)
                yield _progress("execute", pct, f"Failed to update {filename}: {exc}")

        yield _progress("execute", 70, f"Done writing pages (created={len(created_files)}, updated={len(updated_files)})")

        # -- 6. Vector index ------------------------------------------------
        all_changed = created_files + updated_files
        for idx, filename in enumerate(all_changed):
            pct = 70 + int(15 * (idx + 1) / max(len(all_changed), 1))
            yield _progress("index", pct, f"Indexing {filename} …")

            filepath = os.path.join(wiki_dir, filename)
            content = _read_file(filepath)
            if not content:
                continue
            fm, _ = render_markdown(content)
            title = fm.get("title", filename.replace(".md", ""))
            page_type = fm.get("type", "unknown")

            try:
                self._qdrant.upsert_page(
                    repo_id=repo_id,
                    filename=filename,
                    title=title,
                    page_type=page_type,
                    content=content,
                )
            except QdrantServiceError as exc:
                logger.error("Vector upsert failed for %s: %s", filename, exc)
                yield _progress("index", pct, f"Vector index failed for {filename}: {exc}")

        yield _progress("index", 85, "Vector indexing complete")

        # -- 7. Finalize: rebuild index.md ----------------------------------
        yield _progress("finalize", 88, "Rebuilding index.md …")

        current_pages = list_wiki_pages(wiki_dir)
        pages_summary = "\n".join(
            f"- [{p['title']}]({p['filename']}) (type: {p['type']})"
            for p in current_pages
            if p["filename"] not in ("index.md", "log.md", "schema.md")
        )

        new_index = self._chat_text(
            system=system_base,
            user=(
                "请生成 Wiki 的 index.md 首页。包含 YAML frontmatter "
                "(title: 首页, type: index, updated: 今天日期)。\n"
                "按类型分组列出所有页面，使用 Markdown 链接格式。\n\n"
                f"--- 当前所有页面 ---\n{pages_summary or '(暂无页面)'}"
            ),
        )
        if new_index:
            _write_file(self._index_path(username, repo_slug), new_index)

        # -- 8. Append to log.md -------------------------------------------
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        log_entry = (
            f"\n## {now_str} — Ingested `{source_filename}`\n\n"
            f"- Created: {', '.join(created_files) or 'none'}\n"
            f"- Updated: {', '.join(updated_files) or 'none'}\n"
        )

        log_path = self._log_path(username, repo_slug)
        existing_log = _read_file(log_path)
        if not existing_log:
            existing_log = "---\ntitle: Ingestion Log\ntype: log\n---\n\n# Ingestion Log\n"
        _write_file(log_path, existing_log + log_entry)

        # -- 9. Update overview.md -----------------------------------------
        if total_pages > 0:
            yield _progress("finalize", 94, "Updating overview.md …")
            overview_path = os.path.join(wiki_dir, "overview.md")

            overview_pages = list_wiki_pages(wiki_dir)
            page_summaries_for_overview = "\n".join(
                f"- [{p['title']}]({p['filename']}) (type: {p['type']})"
                for p in overview_pages
                if p["filename"] not in ("log.md", "schema.md", "overview.md")
            )
            now_str_ov = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            new_overview = self._chat_text(
                system=system_base,
                user=(
                    "请生成或更新 Wiki 的 overview.md 全局概览页面。\n"
                    f"包含 YAML frontmatter (title: 概览, type: overview, updated: {now_str_ov})。\n"
                    "内容要求：对知识库的整体高层综述，涵盖主要主题、核心发现、重要实体，"
                    "适合作为新读者的入口。使用 Markdown 格式，包含必要的章节标题。\n\n"
                    f"--- 当前所有页面 ---\n{page_summaries_for_overview or '(暂无页面)'}\n\n"
                    f"--- 最新摄入来源 ---\n{source_filename}\n\n"
                    f"--- 摄入分析摘要 ---\n{json.dumps(analysis, ensure_ascii=False)}"
                ),
            )
            if new_overview:
                _write_file(overview_path, new_overview)
                if self._qdrant:
                    try:
                        fm_ov, _ = render_markdown(new_overview)
                        self._qdrant.upsert_page(
                            repo_id=repo_id,
                            filename="overview.md",
                            title=fm_ov.get("title", "概览"),
                            page_type="overview",
                            content=new_overview,
                        )
                    except QdrantServiceError as exc:
                        logger.error("Vector upsert failed for overview.md: %s", exc)

        yield _progress(
            "done", 100,
            f"Ingest complete: {len(created_files)} created, {len(updated_files)} updated",
            created=created_files,
            updated=updated_files,
        )

    # -----------------------------------------------------------------------
    # QUERY
    # -----------------------------------------------------------------------

    def query(
        self,
        repo: Any,
        username: str,
        question: str,
    ) -> dict[str, Any]:
        """Answer a question using both the wiki index and vector search."""
        repo_slug = repo.slug
        repo_id = repo.id
        wiki_dir = self._wiki_dir(username, repo_slug)

        schema_content = _read_file(self._schema_path(username, repo_slug))
        index_content = _read_file(self._index_path(username, repo_slug))

        system_base = (
            "你是一个 Wiki 知识助手。根据 Wiki 内容准确回答问题，并引用来源页面。\n\n"
            + (schema_content or "")
        )

        # -- Wiki path: ask LLM to pick relevant pages ---------------------
        wiki_filenames: list[str] = []
        if index_content:
            pick_result = self._chat_json(
                system=system_base,
                user=(
                    "根据用户问题，从索引中选出最相关的页面（最多 8 个）。\n"
                    '返回 JSON: {"filenames": ["a.md", "b.md"]}\n\n'
                    f"--- 问题 ---\n{question}\n\n"
                    f"--- index.md ---\n{index_content}"
                ),
                default={"filenames": []},
            )
            wiki_filenames = pick_result.get("filenames", [])
            if isinstance(wiki_filenames, str):
                wiki_filenames = [wiki_filenames]

        # -- Qdrant path ---------------------------------------------------
        qdrant_filenames: list[str] = []
        try:
            hits = self._qdrant.search(repo_id=repo_id, query=question, limit=8)
            qdrant_filenames = [h["filename"] for h in hits if h.get("filename")]
        except QdrantServiceError as exc:
            logger.warning("Qdrant search failed during query: %s", exc)

        # -- Merge & deduplicate -------------------------------------------
        seen: set[str] = set()
        merged: list[str] = []
        for fn in wiki_filenames + qdrant_filenames:
            if fn not in seen:
                seen.add(fn)
                merged.append(fn)

        # -- Read page contents --------------------------------------------
        page_contents: dict[str, str] = {}
        for fn in merged:
            content = _read_file(os.path.join(wiki_dir, fn))
            if content:
                page_contents[fn] = content

        if not page_contents:
            return {
                "answer": "暂无相关 Wiki 内容可以回答该问题。请先导入相关资料。",
                "referenced_pages": [],
                "wiki_sources": [],
                "qdrant_sources": [],
                "suggested_filename": None,
            }

        # -- Build context block -------------------------------------------
        context_parts: list[str] = []
        for fn, content in page_contents.items():
            context_parts.append(f"=== {fn} ===\n{content[:6000]}")
        context_block = "\n\n".join(context_parts)

        # -- Answer --------------------------------------------------------
        answer_result = self._chat_json(
            system=system_base,
            user=(
                "根据以下 Wiki 页面回答用户问题。\n"
                "返回 JSON:\n"
                '{"answer": "详细回答（使用 Markdown 格式）", '
                '"referenced_pages": ["a.md", "b.md"], '
                '"suggested_filename": "new-page.md 或 null（如果回答不够完整可建议新建页面）"}\n\n'
                f"--- 问题 ---\n{question}\n\n"
                f"--- Wiki 页面内容 ---\n{context_block}"
            ),
            default={
                "answer": "抱歉，无法生成回答。",
                "referenced_pages": list(page_contents.keys()),
                "suggested_filename": None,
            },
        )

        # wiki_sources / qdrant_sources: only include pages that were actually loaded
        loaded = set(page_contents.keys())
        return {
            "answer": answer_result.get("answer", ""),
            "referenced_pages": answer_result.get("referenced_pages", list(page_contents.keys())),
            "wiki_sources": [f for f in wiki_filenames if f in loaded],
            "qdrant_sources": [f for f in qdrant_filenames if f in loaded],
            "suggested_filename": answer_result.get("suggested_filename"),
        }

    # -----------------------------------------------------------------------
    # QUERY STREAM
    # -----------------------------------------------------------------------

    def query_stream(
        self,
        repo: Any,
        username: str,
        question: str,
    ) -> Any:
        """Stream query: yields dicts {"event": str, "data": dict}."""
        repo_slug = repo.slug
        repo_id = repo.id
        wiki_dir = self._wiki_dir(username, repo_slug)
        schema_content = _read_file(self._schema_path(username, repo_slug))
        index_content = _read_file(self._index_path(username, repo_slug))
        system_base = (
            "你是一个 Wiki 知识助手。根据 Wiki 内容准确回答问题，并引用来源页面。\n\n"
            + (schema_content or "")
        )

        yield {"event": "progress", "data": {"message": "正在检索相关页面…", "percent": 10}}

        wiki_filenames: list[str] = []
        if index_content:
            pick_result = self._chat_json(
                system=system_base,
                user=(
                    "根据用户问题，从索引中选出最相关的页面（最多 8 个）。\n"
                    '返回 JSON: {"filenames": ["a.md", "b.md"]}\n\n'
                    f"--- 问题 ---\n{question}\n\n--- index.md ---\n{index_content}"
                ),
                default={"filenames": []},
            )
            wiki_filenames = pick_result.get("filenames", [])
            if isinstance(wiki_filenames, str):
                wiki_filenames = [wiki_filenames]

        qdrant_filenames: list[str] = []
        chunk_hits: list[dict] = []
        if self._qdrant:
            try:
                chunk_hits = self._qdrant.search_chunks(repo_id=repo_id, query=question, limit=8)
                qdrant_filenames = list({h["filename"] for h in chunk_hits})
            except QdrantServiceError:
                pass

        yield {"event": "progress", "data": {"message": "正在读取页面内容…", "percent": 40}}

        seen: set[str] = set()
        merged: list[str] = []
        for fn in wiki_filenames + qdrant_filenames:
            if fn not in seen:
                seen.add(fn)
                merged.append(fn)

        page_contents: dict[str, str] = {}
        for fn in merged:
            content = _read_file(os.path.join(wiki_dir, fn))
            if content:
                page_contents[fn] = content

        if not page_contents:
            yield {"event": "done", "data": {
                "answer": "暂无相关 Wiki 内容可以回答该问题。请先导入相关资料。",
                "wiki_sources": [], "qdrant_sources": [], "referenced_pages": [],
            }}
            return

        context_parts = [f"=== {fn} ===\n{content[:6000]}"
                         for fn, content in page_contents.items()]
        context_block = "\n\n".join(context_parts)

        yield {"event": "progress", "data": {"message": "正在生成回答…", "percent": 60}}

        messages = [
            {"role": "system", "content": system_base},
            {"role": "user", "content": (
                "根据以下 Wiki 页面回答用户问题。使用 Markdown 格式。\n\n"
                f"--- 问题 ---\n{question}\n\n"
                f"--- Wiki 页面内容 ---\n{context_block}"
            )},
        ]

        answer_chunks: list[str] = []
        try:
            for chunk in self._llm.chat_stream(messages):
                answer_chunks.append(chunk)
                yield {"event": "answer_chunk", "data": {"chunk": chunk}}
        except Exception as exc:
            logger.error("query_stream LLM error: %s", exc)
            yield {"event": "error", "data": {"message": str(exc)}}
            return

        loaded = set(page_contents.keys())
        answer_text = "".join(answer_chunks)
        confidence = self._score_confidence(
            wiki_hit_count=len(wiki_filenames),
            chunk_hit_count=len(chunk_hits),
            top_chunk_score=chunk_hits[0]["score"] if chunk_hits else 0.0,
            hit_overview="overview.md" in wiki_filenames,
            both_channels=bool(wiki_filenames) and bool(chunk_hits),
            answer_text=answer_text,
        )
        wiki_ev = []
        for fn in wiki_filenames:
            if fn not in loaded:
                continue
            fm_ev, _ = render_markdown(page_contents[fn])
            reason = "高层概览页命中" if fn == "overview.md" else "结构化路径选中"
            page_slug = fn.replace(".md", "")
            wiki_ev.append({
                "filename": fn,
                "title": fm_ev.get("title", page_slug),
                "type": fm_ev.get("type", "unknown"),
                "url": f"/{page_slug}",
                "reason": reason,
            })
        chunk_ev = []
        for hit in chunk_hits:
            fn = hit["filename"]
            page_slug = fn.replace(".md", "")
            chunk_ev.append({
                "chunk_id": hit["chunk_id"],
                "filename": fn,
                "title": hit.get("page_title", page_slug),
                "heading": hit.get("heading", ""),
                "url": f"/{page_slug}",
                "snippet": hit.get("chunk_text", "")[:200],
                "score": hit.get("score", 0.0),
            })
        evidence_summary = (
            f"本回答基于 {len(wiki_ev)} 个 Wiki 页面和 {len(chunk_ev)} 个原文片段生成。"
        )
        yield {"event": "done", "data": {
            "answer": answer_text,
            "markdown": answer_text,
            "confidence": confidence,
            "wiki_evidence": wiki_ev,
            "chunk_evidence": chunk_ev,
            "evidence_summary": evidence_summary,
            "wiki_sources": [f for f in wiki_filenames if f in loaded],
            "qdrant_sources": list({h["filename"] for h in chunk_hits}),
            "referenced_pages": list(loaded),
        }}

    # -----------------------------------------------------------------------
    # QUERY WITH EVIDENCE
    # -----------------------------------------------------------------------

    def query_with_evidence(
        self,
        repo: Any,
        username: str,
        question: str,
        wiki_base_url: str = "",
    ) -> dict[str, Any]:
        """Query using dual-channel evidence retrieval with rule-based confidence scoring."""
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

        # -- Wiki path -------------------------------------------------
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

        # -- Chunk path ------------------------------------------------
        chunk_hits: list[dict] = []
        if self._qdrant:
            try:
                chunk_hits = self._qdrant.search_chunks(
                    repo_id=repo_id, query=question, limit=8
                )
            except QdrantServiceError as exc:
                logger.warning("search_chunks failed: %s", exc)

        # -- Build wiki_evidence ---------------------------------------
        wiki_evidence: list[dict] = []
        page_contents: dict[str, str] = {}
        chunk_fns = {h["filename"] for h in chunk_hits}
        for fn in wiki_filenames:
            content = _read_file(os.path.join(wiki_dir, fn))
            if not content:
                continue
            page_contents[fn] = content
            fm, _ = render_markdown(content)
            if fn == "overview.md":
                reason = "高层概览页命中"
            elif fn in chunk_fns:
                reason = "结构化路径与片段证据共同支持"
            else:
                reason = "结构化路径选中"
            page_slug = fn.replace(".md", "")
            wiki_evidence.append({
                "filename": fn,
                "title": fm.get("title", page_slug),
                "type": fm.get("type", "unknown"),
                "url": f"{wiki_base_url}/{page_slug}" if wiki_base_url else f"/{page_slug}",
                "reason": reason,
            })

        # -- Build chunk_evidence --------------------------------------
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

        # -- Build context & generate answer ---------------------------
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

        # -- Confidence ------------------------------------------------
        top_score = chunk_hits[0]["score"] if chunk_hits else 0.0
        hit_overview = "overview.md" in wiki_filenames
        both = bool(wiki_filenames) and bool(chunk_hits)
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
            "qdrant_sources": list(chunk_fns),
        }

    # -----------------------------------------------------------------------
    # LINT
    # -----------------------------------------------------------------------

    def lint(
        self,
        repo: Any,
        username: str,
    ) -> dict[str, Any]:
        """Check the wiki for structural issues and inconsistencies."""
        repo_slug = repo.slug
        wiki_dir = self._wiki_dir(username, repo_slug)

        schema_content = _read_file(self._schema_path(username, repo_slug))
        pages = list_wiki_pages(wiki_dir)

        if not pages:
            return {"issues": [], "suggestions": ["Wiki is empty — ingest some sources first."]}

        # Build a compact summary: frontmatter + first 200 chars of body
        page_summaries: list[str] = []
        for p in pages:
            filepath = os.path.join(wiki_dir, p["filename"])
            content = _read_file(filepath)
            fm, html = render_markdown(content)
            body_preview = content[:200].replace("\n", " ")
            page_summaries.append(
                f"### {p['filename']}\n"
                f"title={p['title']}  type={p['type']}  updated={p['updated']}\n"
                f"preview: {body_preview}…"
            )

        pages_block = "\n\n".join(page_summaries)

        system = (
            "你是一个 Wiki 质量审查员。检查 Wiki 的结构完整性和内容一致性。\n\n"
            + (schema_content or "")
        )

        result = self._chat_json(
            system=system,
            user=(
                "请审查以下 Wiki 页面，检查：\n"
                "1. 互相矛盾的内容\n"
                "2. 孤立页面（没有被其他页面引用）\n"
                "3. 缺失的交叉引用\n"
                "4. Frontmatter 缺失或不规范\n"
                "5. 页面类型标注是否正确\n"
                "6. 建议合并或拆分的页面\n\n"
                '返回 JSON:\n'
                '{"issues": [{"type": "contradiction|orphan|missing_link|bad_frontmatter|wrong_type", '
                '"page": "filename.md", "description": "..."}], '
                '"suggestions": ["建议1", "建议2"]}\n\n'
                f"--- 所有页面摘要 ---\n{pages_block}"
            ),
            default={"issues": [], "suggestions": []},
        )

        return {
            "issues": result.get("issues", []),
            "suggestions": result.get("suggestions", []),
        }

    def apply_fixes(
        self,
        repo: Any,
        username: str,
        issues: list[dict],
    ) -> dict[str, Any]:
        """Apply automatic fixes for lint issues where possible.

        Skips contradiction issues (require human review).
        Returns {"fixed": [...], "skipped": [...], "errors": [...]}.
        """
        repo_slug = repo.slug
        wiki_dir = self._wiki_dir(username, repo_slug)
        schema_content = _read_file(self._schema_path(username, repo_slug))

        system_base = (
            "你是一个 Wiki 维护者。修复以下 Wiki 页面的结构问题，保持原有内容不变。\n\n"
            + (schema_content or "")
        )

        fixed: list[str] = []
        skipped: list[str] = []
        errors: list[str] = []

        for issue in issues:
            issue_type = issue.get("type", "")
            page_file = issue.get("page", "")
            description = issue.get("description", "")

            if issue_type == "contradiction":
                skipped.append(page_file or "unknown")
                continue

            if not page_file:
                skipped.append("unknown")
                continue

            filepath = os.path.join(wiki_dir, page_file)
            existing_content = _read_file(filepath)
            if not existing_content:
                errors.append(f"{page_file}: file not found")
                continue

            try:
                if issue_type == "bad_frontmatter":
                    fixed_content = self._chat_text(
                        system=system_base,
                        user=(
                            f"请修复以下 Wiki 页面的 frontmatter 问题。\n"
                            f"问题描述: {description}\n"
                            "要求: 添加或修复 YAML frontmatter (title, type, updated 字段)，"
                            "保持正文内容完全不变。\n\n"
                            f"--- 当前内容 ({page_file}) ---\n{existing_content}"
                        ),
                    )
                elif issue_type in ("orphan", "missing_link"):
                    index_content = _read_file(self._index_path(username, repo_slug))
                    fixed_content = self._chat_text(
                        system=system_base,
                        user=(
                            f"请修复以下 Wiki 页面的链接问题。\n"
                            f"问题类型: {issue_type}\n"
                            f"问题描述: {description}\n"
                            "对于孤立页面：在 index.md 中添加对应链接。"
                            "对于缺失链接：在合适位置添加交叉引用。\n\n"
                            f"--- 待修复页面 ({page_file}) ---\n{existing_content}\n\n"
                            f"--- index.md ---\n{index_content or '(空)'}"
                        ),
                    )
                elif issue_type == "wrong_type":
                    fixed_content = self._chat_text(
                        system=system_base,
                        user=(
                            f"请修复以下 Wiki 页面的 type 字段。\n"
                            f"问题描述: {description}\n"
                            "只修改 frontmatter 中的 type 字段，其余内容保持不变。\n\n"
                            f"--- 当前内容 ({page_file}) ---\n{existing_content}"
                        ),
                    )
                else:
                    skipped.append(page_file)
                    continue

                if fixed_content:
                    _write_file(filepath, fixed_content)
                    if self._qdrant:
                        try:
                            fm, _ = render_markdown(fixed_content)
                            self._qdrant.upsert_page(
                                repo_id=repo.id,
                                filename=page_file,
                                title=fm.get("title", page_file),
                                page_type=fm.get("type", "unknown"),
                                content=fixed_content,
                            )
                        except QdrantServiceError:
                            pass
                    fixed.append(page_file)
                else:
                    errors.append(f"{page_file}: LLM returned empty content")
            except Exception as exc:
                logger.exception("apply_fixes failed for %s: %s", page_file, exc)
                errors.append(f"{page_file}: {exc}")

        return {"fixed": fixed, "skipped": skipped, "errors": errors}
