"""Core wiki operations: ingest, query, lint."""

from __future__ import annotations

import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Generator, Iterator

from exceptions import LLMClientError, QdrantServiceError
from llm_client import LLMClient
from llmwiki_core import HybridRetriever, RetrievalConfig
from qdrant_service import QdrantService
from utils import (
    classify_query_mode,
    list_wiki_pages,
    local_now,
    local_today_date_str,
    read_jsonl,
    render_markdown,
)
from wiki_prompts import (
    apply_citation_penalty,
    build_comparison_user_prompt,
    build_generic_user_prompt,
    classify_intent,
    compose_system_prompt,
    validate_citations,
)

logger = logging.getLogger(__name__)

# ReAct（极致推理）：每轮一次规划 LLM + 可选检索，上限步数防止失控
REACT_MAX_STEPS = 6
# 深度 / 极致：首轮（及 ReAct 累积）检索后，评审是否覆盖问题；不足时最多追加的独立检索查询条数
RETRIEVAL_CRITIQUE_MAX_FOLLOWUPS = 3

_JSON_FENCE_CHARS = ("`", "```")

_UNCERTAINTY_PHRASES = (
    "基于现有资料只能推测到",
    "现有证据不足以支持更确定的结论",
    "当前知识库中缺少直接证据",
)


def _contains_uncertainty(text: str) -> bool:
    return any(p in text for p in _UNCERTAINTY_PHRASES)


def _build_history_block(history: list[dict]) -> str:
    """将最近 N 条对话历史转换成上下文块。"""
    if not history:
        return ""
    parts = []
    for msg in history[-6:]:
        role = "用户" if msg.get("role") == "user" else "助手"
        content = str(msg.get("content", ""))[:500]
        parts.append(f"{role}：{content}")
    return "--- 对话历史 ---\n" + "\n".join(parts) + "\n\n"

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
        *,
        retriever: HybridRetriever | None = None,
        retrieval_config: RetrievalConfig | None = None,
        enable_hyde: bool = False,
        context_chunk_chars: int = 700,
        context_expand_neighbors: int = 1,
        ingest_llm_concurrency: int = 1,
        ingest_index_concurrency: int = 1,
        enable_prompt_guard: bool = True,
        enable_comparison_template: bool = True,
        comparison_min_dimensions: int = 3,
        citation_postcheck: bool = True,
        citation_penalty: float = 0.25,
    ) -> None:
        self._llm = llm_client
        self._qdrant = qdrant_service
        self._data_dir = data_dir
        if retriever is None and qdrant_service is not None:
            retriever = HybridRetriever(
                qdrant=qdrant_service,
                config=retrieval_config or RetrievalConfig(),
            )
        self._retriever = retriever
        self._enable_hyde = bool(enable_hyde)
        self._context_chunk_chars = max(200, int(context_chunk_chars))
        self._context_expand_neighbors = max(0, int(context_expand_neighbors))
        self._ingest_llm_concurrency = max(1, int(ingest_llm_concurrency))
        self._ingest_index_concurrency = max(1, int(ingest_index_concurrency))
        self._enable_prompt_guard = bool(enable_prompt_guard)
        self._enable_comparison_template = bool(enable_comparison_template)
        self._comparison_min_dimensions = max(2, int(comparison_min_dimensions))
        self._citation_postcheck = bool(citation_postcheck)
        self._citation_penalty = max(0.0, float(citation_penalty))

    # -- path helpers -------------------------------------------------------

    def _repo_base(self, username: str, repo_slug: str) -> str:
        return os.path.join(self._data_dir, username, repo_slug)

    def _wiki_dir(self, username: str, repo_slug: str) -> str:
        return os.path.join(self._repo_base(username, repo_slug), "wiki")

    def _raw_dir(self, username: str, repo_slug: str) -> str:
        return os.path.join(self._repo_base(username, repo_slug), "raw")

    def _facts_records_dir(self, username: str, repo_slug: str) -> str:
        return os.path.join(self._repo_base(username, repo_slug), "facts", "records")

    def _fact_records_path(self, username: str, repo_slug: str, source_filename: str) -> str:
        stem = os.path.splitext(source_filename)[0]
        return os.path.join(self._facts_records_dir(username, repo_slug), f"{stem}.jsonl")

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

    def _chat_text(
        self,
        system: str,
        user: str,
        *,
        retries: int = 1,
        temperature: float = 0.5,
    ) -> str:
        """Call LLM expecting free text. Retry on transient failure."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_error: Exception | None = None
        for attempt in range(1 + max(0, retries)):
            try:
                return self._llm.chat(messages, temperature=temperature)
            except LLMClientError as exc:
                last_error = exc
                logger.warning(
                    "chat_text attempt=%s failed: %s", attempt + 1, exc,
                )
        logger.error("chat_text gave up after %s attempts: %s", 1 + retries, last_error)
        return ""

    # -- INGEST concurrency helpers ----------------------------------------

    def _generate_page_task(
        self,
        *,
        kind: str,
        spec: dict,
        system_base: str,
        analysis: dict,
        truncated_source: str,
        wiki_dir: str,
    ) -> dict:
        """在工作线程里生成/更新单个 wiki 页面；捕获所有异常返回结构化结果。"""
        filename = spec.get("filename", "")
        try:
            if kind == "create":
                return self._create_single_page(
                    spec=spec,
                    system_base=system_base,
                    analysis=analysis,
                    truncated_source=truncated_source,
                    wiki_dir=wiki_dir,
                )
            return self._update_single_page(
                spec=spec,
                system_base=system_base,
                analysis=analysis,
                wiki_dir=wiki_dir,
            )
        except Exception as exc:
            logger.exception("Page %s (%s) task failed: %s", filename, kind, exc)
            return {"ok": False, "kind": kind, "filename": filename, "error": str(exc)}

    def _create_single_page(
        self,
        *,
        spec: dict,
        system_base: str,
        analysis: dict,
        truncated_source: str,
        wiki_dir: str,
    ) -> dict:
        filename = spec.get("filename", "")
        title = spec.get("title", filename.replace(".md", ""))
        page_type = spec.get("type", "concept")
        page_md = self._chat_text(
            system=system_base,
            user=(
                f"请为以下主题生成完整的 Wiki 页面（Markdown 格式，包含 YAML frontmatter）。\n"
                f"文件名: {filename}\n标题: {title}\n类型: {page_type}\n"
                f"原因: {spec.get('reason', '')}\n\n"
                f"--- 原始资料摘要 ---\n{json.dumps(analysis, ensure_ascii=False)}\n\n"
                f"--- 原始资料节选 ---\n{truncated_source[:8000]}\n\n"
                "请确保：\n"
                "1. 以 YAML frontmatter 开头 (title, type, tags, source, updated)\n"
                "2. 内容准确、结构清晰\n"
                "3. 适当添加对其他页面的交叉引用链接 [Title](other-page.md)"
            ),
        )
        if not page_md:
            return {"ok": False, "kind": "create", "filename": filename, "error": "empty LLM output"}
        _write_file(os.path.join(wiki_dir, filename), page_md)
        logger.info("Created wiki page: %s", filename)
        return {"ok": True, "kind": "create", "filename": filename}

    def _update_single_page(
        self,
        *,
        spec: dict,
        system_base: str,
        analysis: dict,
        wiki_dir: str,
    ) -> dict:
        filename = spec.get("filename", "")
        existing_path = os.path.join(wiki_dir, filename)
        existing_content = _read_file(existing_path)
        if not existing_content:
            logger.warning("Page to update not found: %s", filename)
            return {"ok": False, "kind": "update", "filename": filename, "error": "file not found"}
        updated_md = self._chat_text(
            system=system_base,
            user=(
                f"请更新以下 Wiki 页面。\n"
                f"更新原因: {spec.get('reason', '')}\n"
                f"需要添加的内容: {spec.get('what_to_add', '')}\n\n"
                f"--- 当前页面内容 ({filename}) ---\n{existing_content}\n\n"
                f"--- 原始资料摘要 ---\n{json.dumps(analysis, ensure_ascii=False)}\n\n"
                "请返回更新后的完整页面（保留 frontmatter，更新 updated 日期）。"
            ),
        )
        if not updated_md:
            return {"ok": False, "kind": "update", "filename": filename, "error": "empty LLM output"}
        _write_file(existing_path, updated_md)
        logger.info("Updated wiki page: %s", filename)
        return {"ok": True, "kind": "update", "filename": filename}

    def _index_single_page(
        self,
        *,
        repo_id: int,
        wiki_dir: str,
        filename: str,
    ) -> dict:
        """并发 upsert_page + upsert_page_chunks 单页的工作函数。"""
        filepath = os.path.join(wiki_dir, filename)
        content = _read_file(filepath)
        if not content:
            return {"ok": False, "filename": filename, "error": "empty content"}
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
            return {"ok": False, "filename": filename, "error": f"page: {exc}"}
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
            return {"ok": False, "filename": filename, "error": f"chunk: {exc}"}
        return {"ok": True, "filename": filename}

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
        fact_hit_count: int = 0,
        top_fact_score: float = 0.0,
        fact_channel: bool = False,
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
        if fact_hit_count >= 1:
            score += 0.30
            reasons.append(f"命中 {fact_hit_count} 条结构化事实")
        if fact_hit_count >= 3:
            score += 0.10
        if both_channels:
            score += 0.15
            reasons.append("LLM Wiki 与向量检索均命中")
        if fact_channel:
            score += 0.10
            reasons.append("结构化事实层命中")
        if top_chunk_score >= 0.85:
            score += 0.10
        elif top_chunk_score >= 0.75:
            score += 0.05
        if top_fact_score >= 0.90:
            score += 0.10
        elif top_fact_score >= 0.80:
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

    def _collect_allowed_sources(
        self,
        *,
        wiki_evidence: list[dict],
        chunk_evidence: list[dict],
        fact_evidence: list[dict],
    ) -> list[dict]:
        """返回本次回答允许引用的文件名集合（去重，保持召回顺序）。

        用于 guard prompt 的『可引用的来源列表』与事后引用合法性校验。
        包含：wiki 证据命中的 filename、chunk 证据命中的 filename、
        以及 fact 证据的 source_file / source_markdown_filename。
        """
        seen: set[str] = set()
        out: list[str] = []
        for ev in wiki_evidence or []:
            fn = ev.get("filename") if isinstance(ev, dict) else None
            if fn and fn not in seen:
                seen.add(fn)
                out.append(fn)
        for ev in chunk_evidence or []:
            fn = ev.get("filename") if isinstance(ev, dict) else None
            if fn and fn not in seen:
                seen.add(fn)
                out.append(fn)
        for ev in fact_evidence or []:
            if not isinstance(ev, dict):
                continue
            for key in ("source_file", "source_markdown_filename"):
                fn = ev.get(key)
                if fn and fn not in seen:
                    seen.add(fn)
                    out.append(fn)
        return out

    def _build_fact_evidence(
        self,
        username: str,
        repo_slug: str,
        fact_hits: list[dict],
    ) -> list[dict]:
        source_base_url = f"/{username}/{repo_slug}/sources"
        fact_evidence: list[dict] = []
        for hit in fact_hits:
            source_markdown_filename = hit.get("source_markdown_filename") or ""
            sheet = hit.get("sheet") or ""
            row_index = hit.get("row_index", 0)
            fact_evidence.append(
                {
                    "record_id": hit.get("record_id", ""),
                    "source_file": hit.get("source_file", ""),
                    "source_markdown_filename": source_markdown_filename,
                    "sheet": sheet,
                    "row_index": row_index,
                    "fields": hit.get("fields", {}),
                    "snippet": hit.get("fact_text", "")[:200],
                    "score": hit.get("score", 0.0),
                    "title": f"{sheet} 第 {row_index} 行".strip(),
                    "url": (
                        f"{source_base_url}/{source_markdown_filename}"
                        if source_markdown_filename
                        else source_base_url
                    ),
                }
            )
        return fact_evidence

    # -----------------------------------------------------------------------
    # RETRIEVAL (shared by query_stream / query_with_evidence)
    # -----------------------------------------------------------------------

    def _retrieval_top_k(self, query_mode: str) -> tuple[int, int]:
        """根据 query_mode 动态分配 chunk/fact 的召回数量。

        规则（基于默认 RetrievalConfig）：
        - ``fact``: 结构化事实为主，加大 fact_top_k、减小 chunk_top_k
        - ``narrative``: 文本综述为主，反之
        - ``hybrid`` 或未知: 两边都足量
        """
        cfg = self._retriever.config if self._retriever else RetrievalConfig()
        base_chunk = cfg.chunk_top_k
        base_fact = cfg.fact_top_k
        if query_mode == "fact":
            return max(4, base_chunk // 2), max(base_fact, int(base_fact * 1.5))
        if query_mode == "narrative":
            return max(base_chunk, int(base_chunk * 1.2)), max(4, base_fact // 2)
        return base_chunk, base_fact

    def _maybe_hyde(self, question: str) -> str | None:
        """可选 HyDE：用低温度让 LLM 先写一段假想答案，再作为 dense 检索的查询文本。

        只影响向量通道；BM25 仍用原始问题，避免假想答案污染关键字排序。
        返回 None 表示不启用或失败，调用方应退回原始 question。
        """
        if not self._enable_hyde:
            return None
        try:
            text = self._llm.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "你在帮助检索。请用 3~5 句话写一段简短、具体的答案草稿，"
                            "用词尽量与可能的资料表述接近。不要声明自己不确定，不要列大纲。"
                        ),
                    },
                    {"role": "user", "content": question},
                ],
                temperature=0.2,
            )
            text = (text or "").strip()
            if len(text) < 10:
                return None
            return f"{question}\n{text}"
        except Exception as exc:
            logger.warning("HyDE failed, fallback to raw question: %s", exc)
            return None

    def _retrieve_chunks(self, repo_id: int, question: str, query_mode: str) -> list[dict]:
        if self._retriever is None:
            return []
        chunk_k, _ = self._retrieval_top_k(query_mode)
        dense_q = self._maybe_hyde(question)
        try:
            hits = self._retriever.retrieve_chunks(
                repo_id=repo_id,
                query=question,
                top_k=chunk_k,
                dense_query=dense_q,
                expand_neighbors=self._context_expand_neighbors,
            )
        except QdrantServiceError as exc:
            logger.warning("retrieve_chunks failed: %s", exc)
            return []
        except Exception as exc:
            logger.warning("retrieve_chunks failed: %s", exc)
            return []
        if not isinstance(hits, list):
            return []
        return [h for h in hits if isinstance(h, dict)]

    def _retrieve_facts(self, repo_id: int, question: str, query_mode: str) -> list[dict]:
        if self._retriever is None:
            return []
        _, fact_k = self._retrieval_top_k(query_mode)
        try:
            hits = self._retriever.retrieve_facts(
                repo_id=repo_id,
                query=question,
                top_k=fact_k,
            )
        except QdrantServiceError as exc:
            logger.warning("retrieve_facts failed: %s", exc)
            return []
        except Exception as exc:
            logger.warning("retrieve_facts failed: %s", exc)
            return []
        if not isinstance(hits, list):
            return []
        return [h for h in hits if isinstance(h, dict)]

    @staticmethod
    def _normalize_reasoning_mode(raw: str | None) -> str:
        m = (raw or "standard").strip().lower()
        return m if m in ("standard", "deep", "react") else "standard"

    def _react_observation_line(
        self,
        wiki_filenames: list[str],
        chunk_hits: list[dict],
        fact_hits: list[dict],
    ) -> str:
        sample = ", ".join(wiki_filenames[:6])
        if len(wiki_filenames) > 6:
            sample += "…"
        return (
            f"页面 {len(wiki_filenames)}，片段 {len(chunk_hits)}，事实 {len(fact_hits)}。"
            f"页面示例：{sample or '无'}"
        )

    def _react_plan_json(
        self, main_question: str, history_text: str, obs_summary: str
    ) -> dict[str, Any]:
        return self._chat_json(
            system=(
                "你是知识库检索编排助手（ReAct）。根据主问题与「已执行步骤」，决定下一步。\n"
                "仅允许两种 action：\n"
                "- retrieve：用 action_input 给出一条简短、具体的检索查询（供向量与索引使用）；\n"
                "- finish：action_input 置空，表示不再检索（证据已够或无法继续）。\n"
                "只输出一个 JSON 对象，不要代码块或其它文字：\n"
                '{"thought":"用一句话写推理","action":"retrieve或finish","action_input":"…"}\n'
            ),
            user=(
                f"【主问题】\n{main_question}\n\n"
                f"【当前累积】\n{obs_summary}\n\n"
                f"【已执行步骤】\n{history_text}\n\n"
                "请输出下一步 JSON。"
            ),
            default={"thought": "结束检索", "action": "finish", "action_input": ""},
        )

    def _react_retrieval_iter(
        self,
        system_base: str,
        index_content: str,
        repo_id: int,
        question: str,
        query_mode: str,
    ) -> Iterator[Any]:
        """Yields (\"progress\", message, percent) then (\"result\", wiki, chunks, facts, trace)."""
        wiki_acc: list[str] = []
        chunk_acc: list[dict] = []
        fact_acc: list[dict] = []
        transcript: list[str] = []
        trace: list[dict] = []
        for step in range(REACT_MAX_STEPS):
            w_m = self._dedupe_wiki_filenames(wiki_acc)
            c_m = self._merge_chunk_hits(chunk_acc)
            f_m = self._merge_fact_hits(fact_acc)
            obs_summary = self._react_observation_line(w_m, c_m, f_m)
            history_text = "\n".join(transcript) if transcript else "（尚无）"
            plan = self._react_plan_json(question, history_text, obs_summary)
            thought = str(plan.get("thought") or "").strip()
            action = str(plan.get("action") or "finish").strip().lower()
            action_input = str(plan.get("action_input") or "").strip()
            entry: dict[str, Any] = {
                "step": step + 1,
                "thought": thought,
                "action": action,
                "action_input": action_input,
            }
            hint = thought if len(thought) <= 100 else thought[:99] + "…"
            yield (
                "progress",
                f"极致推理(ReAct)：第 {step + 1} 步 — {hint or '…'}",
                6 + min(34, step * 6),
            )
            if action != "retrieve" or not action_input:
                if action == "retrieve" and not action_input:
                    entry["observation"] = "未提供检索语句，结束。"
                trace.append(entry)
                break
            wiki_acc.extend(
                self._pick_wiki_filenames(system_base, index_content, action_input)
            )
            chunk_acc.extend(self._retrieve_chunks(repo_id, action_input, query_mode))
            fact_acc.extend(self._retrieve_facts(repo_id, action_input, query_mode))
            w2 = self._dedupe_wiki_filenames(wiki_acc)
            c2 = self._merge_chunk_hits(chunk_acc)
            f2 = self._merge_fact_hits(fact_acc)
            obs_line = self._react_observation_line(w2, c2, f2)
            entry["observation"] = obs_line
            trace.append(entry)
            transcript.append(
                f"--- 第 {step + 1} 步 ---\n思考：{thought}\n"
                f"动作：retrieve — {action_input}\n观察：{obs_line}"
            )
        yield (
            "result",
            self._dedupe_wiki_filenames(wiki_acc),
            self._merge_chunk_hits(chunk_acc),
            self._merge_fact_hits(fact_acc),
            trace,
        )

    @staticmethod
    def _log_query_mode_for_reasoning(reasoning_mode: str, classify_mode: str) -> str:
        if reasoning_mode == "deep":
            return "deep"
        if reasoning_mode == "react":
            return "react"
        return classify_mode

    def _pick_wiki_filenames(
        self, system_base: str, index_content: str, question: str
    ) -> list[str]:
        if not index_content:
            return []
        pick_result = self._chat_json(
            system=system_base,
            user=(
                "根据用户问题，从索引中选出最相关的页面（最多 8 个）。\n"
                '返回 JSON: {"filenames": ["a.md", "b.md"]}\n\n'
                f"--- 问题 ---\n{question}\n\n--- index.md ---\n{index_content}"
            ),
            default={"filenames": []},
        )
        names = pick_result.get("filenames", [])
        if isinstance(names, str):
            names = [names]
        return [n for n in names if isinstance(n, str) and n.strip()]

    def _deep_sub_questions(self, system_base: str, question: str) -> list[str]:
        res = self._chat_json(
            system=system_base,
            user=(
                "将用户问题拆成 2～4 个具体、可独立检索知识库的子问题；"
                "每个子问题用简短中文，覆盖主问题的不同方面。\n"
                '严格返回 JSON：`{"sub_questions":["..."]}`\n\n'
                f"主问题：\n{question}"
            ),
            default={"sub_questions": []},
        )
        raw = res.get("sub_questions") or []
        out: list[str] = []
        for s in raw:
            t = (s or "").strip()
            if t and t not in out:
                out.append(t)
        return out[:4]

    @staticmethod
    def _dedupe_wiki_filenames(names: list[str], max_n: int = 12) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for fn in names:
            fn = (fn or "").strip()
            if not fn or fn in seen:
                continue
            seen.add(fn)
            out.append(fn)
        return out[:max_n]

    @staticmethod
    def _merge_chunk_hits(hits: list[dict], max_n: int = 24) -> list[dict]:
        def _rank_score(hit: dict) -> float:
            for key in ("fused_score", "score", "dense_score", "bm25_score"):
                try:
                    value = float(hit.get(key) or 0.0)
                except (TypeError, ValueError):
                    value = 0.0
                if value:
                    return value
            return 0.0

        by_id: dict[str, dict] = {}
        for h in hits:
            if not isinstance(h, dict):
                continue
            cid = h.get("chunk_id")
            if not cid:
                continue
            sc = _rank_score(h)
            prev = by_id.get(str(cid))
            if prev is None or sc > _rank_score(prev):
                by_id[str(cid)] = h
        merged = sorted(by_id.values(), key=lambda x: -_rank_score(x))
        return merged[:max_n]

    @staticmethod
    def _merge_fact_hits(hits: list[dict], max_n: int = 20) -> list[dict]:
        by_key: dict[str, dict] = {}
        for h in hits:
            if not isinstance(h, dict):
                continue
            key = str(h.get("record_id") or "").strip()
            if not key:
                key = (
                    f"{h.get('source_file', '')}|{h.get('sheet', '')}|"
                    f"{h.get('row_index', '')}"
                )
            sc = float(h.get("score") or 0.0)
            prev = by_key.get(key)
            if prev is None or sc > float(prev.get("score") or 0.0):
                by_key[key] = h
        merged = sorted(by_key.values(), key=lambda x: -float(x.get("score") or 0.0))
        return merged[:max_n]

    def _retrieval_evidence_summary(
        self,
        question: str,
        wiki_filenames: list[str],
        chunk_hits: list[dict],
        fact_hits: list[dict],
        *,
        max_chunks_in_summary: int = 8,
        snippet_chars: int = 160,
    ) -> str:
        """供检索评审 LLM 阅读的紧凑摘要（非全文）。"""
        wiki_preview = ", ".join(wiki_filenames[:12])
        if len(wiki_filenames) > 12:
            wiki_preview += "…"
        lines = [
            f"【用户问题】\n{question}\n",
            f"【结构化 Wiki 路径命中】共 {len(wiki_filenames)} 个：{wiki_preview or '无'}",
        ]
        if chunk_hits:
            lines.append("【向量片段摘录】（按融合排序）")
            for i, h in enumerate(chunk_hits[:max_chunks_in_summary]):
                if not isinstance(h, dict):
                    continue
                fn = h.get("filename") or "?"
                try:
                    sc = float(h.get("fused_score") or h.get("score") or 0.0)
                except (TypeError, ValueError):
                    sc = 0.0
                tx = (h.get("chunk_text") or "")[:snippet_chars].replace("\n", " ")
                lines.append(f"  - [{i + 1}] {fn} (score={sc:.3f}) {tx}")
        else:
            lines.append("【向量片段】无命中")
        lines.append(f"【结构化事实行】约 {len(fact_hits)} 条")
        return "\n".join(lines)

    def _retrieval_critique_json(
        self,
        system_base: str,
        reasoning_mode: str,
        evidence_summary: str,
    ) -> dict[str, Any]:
        mode_label = "深度推理" if reasoning_mode == "deep" else "极致推理(ReAct)"
        schema_hint = (system_base or "").strip()[:2500]
        return self._chat_json(
            system=(
                f"你是检索质量评审员（仅用于 {mode_label}）。根据用户问题与「已召回证据摘要」，"
                "判断当前证据是否**很可能**足以支撑准确、完整的回答。\n"
                "若明显偏题、关键实体或维度未覆盖、证据过少、或仅有泛泛页面而无实质片段，"
                "应判定为不足，并给出 1～3 条**简短中文检索查询**（供向量与索引选页），"
                "查询须具体、可独立检索知识库。\n"
                "若证据已覆盖问题要点，sufficient 为 true，follow_up_queries 为空数组。\n"
                "只输出 JSON，不要代码块或其它文字：\n"
                '{"sufficient": true/false, "follow_up_queries": ["..."], "brief_rationale": "一句话"}\n'
            ),
            user=f"{schema_hint}\n\n---\n{evidence_summary}\n\n请输出 JSON。",
            default={
                "sufficient": True,
                "follow_up_queries": [],
                "brief_rationale": "",
            },
        )

    def _refine_retrieval_after_critique(
        self,
        system_base: str,
        index_content: str,
        repo_id: int,
        question: str,
        query_mode: str,
        reasoning_mode: str,
        wiki_filenames: list[str],
        chunk_hits: list[dict],
        fact_hits: list[dict],
    ) -> tuple[list[str], list[dict], list[dict], list[dict[str, Any]]]:
        """深度 / 极致：评审检索质量，不足时追加至多一轮补充检索。标准模式勿调用。"""
        trace: list[dict[str, Any]] = []
        if reasoning_mode not in ("deep", "react"):
            return wiki_filenames, chunk_hits, fact_hits, trace

        evidence_summary = self._retrieval_evidence_summary(
            question, wiki_filenames, chunk_hits, fact_hits
        )
        crit = self._retrieval_critique_json(
            system_base, reasoning_mode, evidence_summary
        )
        if not isinstance(crit, dict):
            crit = {
                "sufficient": True,
                "follow_up_queries": [],
                "brief_rationale": "",
            }

        sufficient = bool(crit.get("sufficient", True))
        raw_extra = crit.get("follow_up_queries") or []
        extras: list[str] = []
        if isinstance(raw_extra, str):
            raw_extra = [raw_extra]
        if isinstance(raw_extra, list):
            for q in raw_extra:
                if isinstance(q, str):
                    t = q.strip()
                    if t and t not in extras:
                        extras.append(t)
                if len(extras) >= RETRIEVAL_CRITIQUE_MAX_FOLLOWUPS:
                    break

        trace.append(crit)
        if sufficient or not extras:
            return wiki_filenames, chunk_hits, fact_hits, trace

        crit["refined"] = True
        wiki_acc = list(wiki_filenames)
        chunk_acc: list[dict] = list(chunk_hits)
        fact_acc: list[dict] = list(fact_hits)
        for qtext in extras:
            wiki_acc.extend(
                self._pick_wiki_filenames(system_base, index_content, qtext)
            )
            chunk_acc.extend(self._retrieve_chunks(repo_id, qtext, query_mode))
            fact_acc.extend(self._retrieve_facts(repo_id, qtext, query_mode))

        return (
            self._dedupe_wiki_filenames(wiki_acc),
            self._merge_chunk_hits(chunk_acc),
            self._merge_fact_hits(fact_acc),
            trace,
        )

    def _build_chunk_context(
        self,
        chunk_hits: list[dict],
        page_contents: dict[str, str],
    ) -> str:
        """以 chunk 为单位拼装上下文；不再把整页灌给 LLM。

        - 每条 chunk 输出 ``page_title / heading`` 做小标题，``filename`` 作为来源引用
        - 同页多条 chunk 按 ``position`` 排序，便于 LLM 理解上下文连续性
        - 截断长度由 ``RAG_CONTEXT_CHUNK_CHARS`` 控制
        """
        if not chunk_hits:
            return ""
        by_file: dict[str, list[dict]] = {}
        for hit in chunk_hits:
            fn = hit.get("filename") or ""
            by_file.setdefault(fn, []).append(hit)
        parts: list[str] = []
        limit = self._context_chunk_chars
        for fn, hits in by_file.items():
            hits_sorted = sorted(hits, key=lambda h: int(h.get("position") or 0))
            title = hits_sorted[0].get("page_title") or fn.replace(".md", "")
            parts.append(f"=== {title} ({fn}) ===")
            for h in hits_sorted:
                heading = h.get("heading") or ""
                snippet = str(h.get("chunk_text") or "").strip()
                if len(snippet) > limit:
                    snippet = snippet[:limit].rstrip() + "…"
                if heading:
                    parts.append(f"[{heading}]\n{snippet}")
                else:
                    parts.append(snippet)
        return "\n\n".join(parts)

    def _build_fact_context(self, fact_hits: list[dict]) -> str:
        parts: list[str] = []
        for hit in fact_hits:
            fields_json = json.dumps(hit.get("fields", {}), ensure_ascii=False)
            parts.append(
                "=== FACT ===\n"
                f"source_file: {hit.get('source_file', '')}\n"
                f"source_markdown_filename: {hit.get('source_markdown_filename', '')}\n"
                f"sheet: {hit.get('sheet', '')}\n"
                f"row_index: {hit.get('row_index', 0)}\n"
                f"fields: {fields_json}\n"
                f"fact_text: {hit.get('fact_text', '')}"
            )
        return "\n\n".join(parts)

    # -----------------------------------------------------------------------
    # INGEST
    # -----------------------------------------------------------------------

    def ingest(
        self,
        repo: Any,
        username: str,
        source_filename: str,
        progress_callback=None,
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

        # -- 4 & 5. Execute: create + update pages （并发生成） ------------
        created_files: list[str] = []
        updated_files: list[str] = []

        generation_tasks: list[tuple[str, dict]] = []
        for idx, spec in enumerate(pages_to_create):
            spec_with_default = dict(spec)
            if not spec_with_default.get("filename"):
                spec_with_default["filename"] = f"page-{idx}.md"
            generation_tasks.append(("create", spec_with_default))
        for spec in pages_to_update:
            if not spec.get("filename"):
                continue
            generation_tasks.append(("update", spec))

        total_gen = len(generation_tasks)
        if total_gen:
            yield _progress(
                "execute", 40,
                f"Generating {total_gen} pages with concurrency={self._ingest_llm_concurrency} …",
            )

            max_workers = min(self._ingest_llm_concurrency, total_gen)
            files_lock = threading.Lock()
            done = 0
            with ThreadPoolExecutor(
                max_workers=max_workers, thread_name_prefix="ingest-gen",
            ) as pool:
                future_to_task = {
                    pool.submit(
                        self._generate_page_task,
                        kind=kind,
                        spec=spec,
                        system_base=system_base,
                        analysis=analysis,
                        truncated_source=truncated_source,
                        wiki_dir=wiki_dir,
                    ): (kind, spec)
                    for kind, spec in generation_tasks
                }
                for fut in as_completed(future_to_task):
                    kind, spec = future_to_task[fut]
                    filename = spec.get("filename", "")
                    try:
                        result = fut.result()
                    except Exception as exc:
                        logger.exception("Page %s task crashed: %s", filename, exc)
                        result = {"ok": False, "kind": kind, "filename": filename, "error": str(exc)}

                    done += 1
                    pct = 40 + int(30 * done / total_gen)

                    if result.get("ok"):
                        with files_lock:
                            if kind == "create":
                                created_files.append(filename)
                            else:
                                updated_files.append(filename)
                        msg = f"{kind.capitalize()}d {filename} [{done}/{total_gen}]"
                    else:
                        err = result.get("error") or "empty output"
                        msg = f"Failed to {kind} {filename}: {err} [{done}/{total_gen}]"
                    yield _progress("execute", pct, msg)

        yield _progress(
            "execute", 70,
            f"Done writing pages (created={len(created_files)}, updated={len(updated_files)})",
        )

        # -- 6. Vector index （多页并发） -----------------------------------
        all_changed = created_files + updated_files
        if all_changed:
            total_idx = len(all_changed)
            yield _progress(
                "index", 70,
                f"Indexing {total_idx} pages with concurrency={self._ingest_index_concurrency} …",
            )
            max_idx_workers = min(self._ingest_index_concurrency, total_idx)
            done_idx = 0
            with ThreadPoolExecutor(
                max_workers=max_idx_workers, thread_name_prefix="ingest-index",
            ) as pool:
                future_to_fn = {
                    pool.submit(
                        self._index_single_page,
                        repo_id=repo_id,
                        wiki_dir=wiki_dir,
                        filename=fn,
                    ): fn
                    for fn in all_changed
                }
                for fut in as_completed(future_to_fn):
                    fn = future_to_fn[fut]
                    try:
                        result = fut.result()
                    except Exception as exc:
                        logger.exception("Index %s crashed: %s", fn, exc)
                        result = {"ok": False, "filename": fn, "error": str(exc)}
                    done_idx += 1
                    pct = 70 + int(14 * done_idx / total_idx)
                    if result.get("ok"):
                        msg = f"Indexed {fn} [{done_idx}/{total_idx}]"
                    else:
                        msg = f"Index failed for {fn}: {result.get('error','')} [{done_idx}/{total_idx}]"
                    yield _progress("index", pct, msg)

        fact_records = read_jsonl(self._fact_records_path(username, repo_slug, source_filename))
        if fact_records and self._qdrant:
            yield _progress("index", 84, f"Indexing {len(fact_records)} fact records …")
            try:
                def _on_fact_progress(done: int, total: int) -> None:
                    if not progress_callback or total <= 0:
                        return
                    pct = min(87, 84 + int(3 * done / total))
                    progress_callback(
                        _progress("index", pct, f"Indexing {done}/{total} fact records …")
                    )

                self._qdrant.upsert_fact_records(
                    repo_id=repo_id,
                    source_filename=source_filename,
                    records=fact_records,
                    progress_callback=_on_fact_progress,
                )
            except QdrantServiceError as exc:
                logger.error("Fact upsert failed for %s: %s", source_filename, exc)
                yield _progress("index", 84, f"Fact index failed for {source_filename}: {exc}")

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
        now_str = local_now().strftime("%Y-%m-%d %H:%M")
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
            now_str_ov = local_today_date_str()

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
                        self._qdrant.upsert_page_chunks(
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
        reasoning_mode: str = "standard",
    ) -> Any:
        """Stream query: yields dicts {"event": str, "data": dict}."""
        repo_slug = repo.slug
        repo_id = repo.id
        wiki_base_url = f"/{username}/{repo_slug}/wiki"
        wiki_dir = self._wiki_dir(username, repo_slug)
        schema_content = _read_file(self._schema_path(username, repo_slug))
        index_content = _read_file(self._index_path(username, repo_slug))
        reasoning_mode = self._normalize_reasoning_mode(reasoning_mode)
        query_mode = classify_query_mode(question)
        system_base = (
            "你是一个 Wiki 知识助手。根据 Wiki 内容准确回答问题，并引用来源页面。\n\n"
            + (schema_content or "")
        )

        yield {"event": "progress", "data": {"message": "正在检索相关页面…", "percent": 10}}

        sub_questions_out: list[str] = []
        react_trace_out: list[dict] = []
        if reasoning_mode == "deep":
            yield {
                "event": "progress",
                "data": {"message": "深度推理：拆解检索要点…", "percent": 8},
            }
            subs = self._deep_sub_questions(system_base, question)
            if len(subs) >= 2:
                sub_questions_out = subs
                queries = [question] + [
                    s
                    for s in subs
                    if s.strip().lower() != question.strip().lower()
                ]
            else:
                queries = [question]
            wiki_acc: list[str] = []
            chunk_acc: list[dict] = []
            fact_acc: list[dict] = []
            nq = len(queries)
            for i, qtext in enumerate(queries):
                yield {
                    "event": "progress",
                    "data": {
                        "message": f"深度推理：检索步骤 {i + 1}/{nq}…",
                        "percent": 10 + int(24 * (i + 1) / max(nq, 1)),
                    },
                }
                wiki_acc.extend(
                    self._pick_wiki_filenames(system_base, index_content, qtext)
                )
                chunk_acc.extend(self._retrieve_chunks(repo_id, qtext, query_mode))
                fact_acc.extend(self._retrieve_facts(repo_id, qtext, query_mode))
            wiki_filenames = self._dedupe_wiki_filenames(wiki_acc)
            chunk_hits = self._merge_chunk_hits(chunk_acc)
            fact_hits = self._merge_fact_hits(fact_acc)
        elif reasoning_mode == "react":
            yield {
                "event": "progress",
                "data": {"message": "极致推理：ReAct 规划与检索…", "percent": 5},
            }
            wiki_filenames = []
            chunk_hits = []
            fact_hits = []
            for item in self._react_retrieval_iter(
                system_base, index_content, repo_id, question, query_mode
            ):
                if item[0] == "progress":
                    _, msg, pct = item
                    yield {"event": "progress", "data": {"message": msg, "percent": pct}}
                else:
                    _, wiki_filenames, chunk_hits, fact_hits, react_trace_out = item
        else:
            wiki_filenames = self._pick_wiki_filenames(
                system_base, index_content, question
            )
            chunk_hits = self._retrieve_chunks(repo_id, question, query_mode)
            fact_hits = self._retrieve_facts(repo_id, question, query_mode)

        critique_trace_out: list[dict[str, Any]] = []
        if reasoning_mode in ("deep", "react"):
            yield {
                "event": "progress",
                "data": {"message": "正在评审检索结果是否覆盖问题…", "percent": 32},
            }
            wiki_filenames, chunk_hits, fact_hits, critique_trace_out = (
                self._refine_retrieval_after_critique(
                    system_base,
                    index_content,
                    repo_id,
                    question,
                    query_mode,
                    reasoning_mode,
                    wiki_filenames,
                    chunk_hits,
                    fact_hits,
                )
            )
            if any(
                isinstance(c, dict) and c.get("refined") for c in critique_trace_out
            ):
                yield {
                    "event": "progress",
                    "data": {
                        "message": "检索评审：已按补充查询扩展证据…",
                        "percent": 36,
                    },
                }

        yield {"event": "progress", "data": {"message": "正在读取页面内容…", "percent": 40}}

        # 只有 Wiki 结构化通道挑中的页面才读整页（用于 evidence URL / 兜底）；
        # chunk 通道命中的片段直接走 _build_chunk_context，不再灌整页。
        page_contents: dict[str, str] = {}
        for fn in wiki_filenames:
            content = _read_file(os.path.join(wiki_dir, fn))
            if content:
                page_contents[fn] = content

        if not page_contents and not chunk_hits and not fact_hits:
            yield {"event": "done", "data": {
                "answer": "暂无相关 Wiki 内容可以回答该问题。请先导入相关资料。",
                "wiki_sources": [], "qdrant_sources": [], "referenced_pages": [],
                "fact_evidence": [],
                "reasoning_mode": reasoning_mode,
                "sub_questions": sub_questions_out,
                "react_trace": react_trace_out,
                "retrieval_critique": critique_trace_out,
            }}
            return

        context_parts: list[str] = []
        chunk_context = self._build_chunk_context(chunk_hits, page_contents)
        if chunk_context:
            context_parts.append(chunk_context)
        # Wiki 通道命中但 chunk 通道没命中的页面，仍给 LLM 一段前言作为兜底
        chunk_files = {h.get("filename") for h in chunk_hits if h.get("filename")}
        for fn, content in page_contents.items():
            if fn in chunk_files:
                continue
            context_parts.append(f"=== {fn} ===\n{content[:2000]}")
        fact_context = self._build_fact_context(fact_hits)
        if fact_context:
            context_parts.append(fact_context)
        context_block = "\n\n".join(context_parts)

        yield {"event": "progress", "data": {"message": "正在生成回答…", "percent": 60}}

        stream_allowed_sources = list(
            dict.fromkeys(
                list(wiki_filenames)
                + [h.get("filename") for h in chunk_hits if h.get("filename")]
                + [h.get("source_file") for h in fact_hits if h.get("source_file")]
                + [
                    h.get("source_markdown_filename")
                    for h in fact_hits
                    if h.get("source_markdown_filename")
                ]
            )
        )
        stream_allowed_sources = [s for s in stream_allowed_sources if s]
        stream_sys = compose_system_prompt(
            system_base,
            enable_guard=self._enable_prompt_guard,
            has_context=bool(context_block),
        )
        stream_intent = (
            classify_intent(question) if self._enable_comparison_template else "generic"
        )
        if stream_intent == "comparison" and context_block:
            stream_user = build_comparison_user_prompt(
                question=question,
                context_block=context_block,
                allowed_sources=stream_allowed_sources,
                query_mode=query_mode,
                min_dimensions=self._comparison_min_dimensions,
            )
        else:
            stream_user = build_generic_user_prompt(
                question=question,
                context_block=context_block,
                allowed_sources=stream_allowed_sources,
                query_mode=query_mode,
            )
        messages = [
            {"role": "system", "content": stream_sys},
            {"role": "user", "content": stream_user},
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
        fact_ev = self._build_fact_evidence(username, repo_slug, fact_hits)
        confidence = self._score_confidence(
            wiki_hit_count=len(wiki_filenames),
            chunk_hit_count=len(chunk_hits),
            top_chunk_score=chunk_hits[0]["score"] if chunk_hits else 0.0,
            hit_overview="overview.md" in wiki_filenames,
            both_channels=bool(wiki_filenames) and bool(chunk_hits),
            answer_text=answer_text,
            fact_hit_count=len(fact_hits),
            top_fact_score=fact_hits[0]["score"] if fact_hits else 0.0,
            fact_channel=bool(fact_hits),
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
                "url": f"{wiki_base_url}/{page_slug}",
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
                "url": f"{wiki_base_url}/{page_slug}",
                "snippet": hit.get("chunk_text", "")[:200],
                "score": hit.get("score", 0.0),
            })
        evidence_summary = (
            f"本回答基于 {len(wiki_ev)} 个 Wiki 页面、{len(chunk_ev)} 个原文片段"
            f"和 {len(fact_ev)} 条结构化事实生成。"
        )
        stream_validation = {"cited": [], "unknown": [], "ok": True}
        if self._citation_postcheck:
            stream_validation = validate_citations(answer_text, set(stream_allowed_sources))
            apply_citation_penalty(confidence, stream_validation, self._citation_penalty)
        yield {"event": "done", "data": {
            "answer": answer_text,
            "markdown": answer_text,
            "confidence": confidence,
            "wiki_evidence": wiki_ev,
            "chunk_evidence": chunk_ev,
            "fact_evidence": fact_ev,
            "evidence_summary": evidence_summary,
            "intent": stream_intent,
            "citation_validation": stream_validation,
            "wiki_sources": [f for f in wiki_filenames if f in loaded],
            "qdrant_sources": list({h["filename"] for h in chunk_hits}),
            "referenced_pages": list(loaded),
            "reasoning_mode": reasoning_mode,
            "sub_questions": sub_questions_out,
            "react_trace": react_trace_out,
            "retrieval_critique": critique_trace_out,
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
        history: list[dict] | None = None,
        reasoning_mode: str = "standard",
    ) -> dict[str, Any]:
        """Query using dual-channel evidence retrieval with rule-based confidence scoring."""
        repo_slug = repo.slug
        repo_id = repo.id
        wiki_dir = self._wiki_dir(username, repo_slug)
        schema_content = _read_file(self._schema_path(username, repo_slug))
        index_content = _read_file(self._index_path(username, repo_slug))
        reasoning_mode = self._normalize_reasoning_mode(reasoning_mode)
        query_mode = classify_query_mode(question)

        system_base = (
            "你是一个 Wiki 知识助手。根据 Wiki 内容准确回答问题。\n"
            "当关键结论证据不足时，必须使用以下提示语之一：\n"
            "「基于现有资料只能推测到」、「现有证据不足以支持更确定的结论」、"
            "「当前知识库中缺少直接证据」。\n\n"
            + (schema_content or "")
        )

        sub_questions_out: list[str] = []
        react_trace_out: list[dict] = []
        critique_trace_out: list[dict[str, Any]] = []
        if reasoning_mode == "deep":
            subs = self._deep_sub_questions(system_base, question)
            if len(subs) >= 2:
                sub_questions_out = subs
                queries = [question] + [
                    s
                    for s in subs
                    if s.strip().lower() != question.strip().lower()
                ]
            else:
                queries = [question]
            wiki_acc: list[str] = []
            chunk_acc: list[dict] = []
            fact_acc: list[dict] = []
            for qtext in queries:
                wiki_acc.extend(
                    self._pick_wiki_filenames(system_base, index_content, qtext)
                )
                chunk_acc.extend(self._retrieve_chunks(repo_id, qtext, query_mode))
                fact_acc.extend(self._retrieve_facts(repo_id, qtext, query_mode))
            wiki_filenames = self._dedupe_wiki_filenames(wiki_acc)
            chunk_hits = self._merge_chunk_hits(chunk_acc)
            fact_hits = self._merge_fact_hits(fact_acc)
        elif reasoning_mode == "react":
            wiki_filenames = []
            chunk_hits = []
            fact_hits = []
            for item in self._react_retrieval_iter(
                system_base, index_content, repo_id, question, query_mode
            ):
                if item[0] == "result":
                    _, wiki_filenames, chunk_hits, fact_hits, react_trace_out = item
        else:
            wiki_filenames = self._pick_wiki_filenames(
                system_base, index_content, question
            )
            chunk_hits = self._retrieve_chunks(repo_id, question, query_mode)
            fact_hits = self._retrieve_facts(repo_id, question, query_mode)

        if reasoning_mode in ("deep", "react"):
            wiki_filenames, chunk_hits, fact_hits, critique_trace_out = (
                self._refine_retrieval_after_critique(
                    system_base,
                    index_content,
                    repo_id,
                    question,
                    query_mode,
                    reasoning_mode,
                    wiki_filenames,
                    chunk_hits,
                    fact_hits,
                )
            )

        # -- Build wiki_evidence ---------------------------------------
        wiki_evidence: list[dict] = []
        page_contents: dict[str, str] = {}
        chunk_fns = {h["filename"] for h in chunk_hits if h.get("filename")}
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
            if fn and fn not in page_contents:
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
                "sources": hit.get("sources") or ["dense"],
            })

        fact_evidence = self._build_fact_evidence(username, repo_slug, fact_hits)

        if not page_contents and not chunk_hits and not fact_hits:
            empty_conf = self._score_confidence(0, 0, 0.0, False, False, "", 0, 0.0, False)
            return {
                "markdown": "暂无相关 Wiki 内容可以回答该问题。请先导入相关资料。",
                "confidence": empty_conf,
                "wiki_evidence": [],
                "chunk_evidence": [],
                "fact_evidence": [],
                "evidence_summary": "暂无证据。",
                "referenced_pages": [],
                "wiki_sources": [],
                "qdrant_sources": [],
                "query_mode": self._log_query_mode_for_reasoning(
                    reasoning_mode, query_mode
                ),
                "reasoning_mode": reasoning_mode,
                "sub_questions": sub_questions_out,
                "react_trace": react_trace_out,
                "retrieval_critique": critique_trace_out,
            }

        # -- Build context & generate answer ---------------------------
        # Chunk 粒度优先，Wiki 整页只作兜底（命中了 chunk 的页面不再整页灌入）
        context_parts: list[str] = []
        chunk_context = self._build_chunk_context(chunk_hits, page_contents)
        if chunk_context:
            context_parts.append(chunk_context)
        for fn, content in page_contents.items():
            if fn in chunk_fns:
                continue
            context_parts.append(f"=== {fn} ===\n{content[:2000]}")
        fact_context = self._build_fact_context(fact_hits)
        if fact_context:
            context_parts.append(fact_context)
        context_block = "\n\n".join(context_parts)

        allowed_sources = self._collect_allowed_sources(
            wiki_evidence=wiki_evidence,
            chunk_evidence=chunk_evidence,
            fact_evidence=fact_evidence,
        )
        history_block = _build_history_block(history) if history else ""
        has_context = bool(context_block)
        sys_prompt = compose_system_prompt(
            system_base,
            enable_guard=self._enable_prompt_guard,
            has_context=has_context,
        )
        intent = (
            classify_intent(question) if self._enable_comparison_template else "generic"
        )
        if intent == "comparison" and has_context:
            user_prompt = build_comparison_user_prompt(
                question=question,
                context_block=context_block,
                history_block=history_block,
                allowed_sources=allowed_sources,
                query_mode=query_mode,
                min_dimensions=self._comparison_min_dimensions,
            )
        else:
            user_prompt = build_generic_user_prompt(
                question=question,
                context_block=context_block,
                history_block=history_block,
                allowed_sources=allowed_sources,
                query_mode=query_mode,
            )
        answer = self._chat_text(system=sys_prompt, user=user_prompt)

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
            fact_hit_count=len(fact_hits),
            top_fact_score=fact_hits[0]["score"] if fact_hits else 0.0,
            fact_channel=bool(fact_hits),
        )

        citation_validation = {"cited": [], "unknown": [], "ok": True}
        if self._citation_postcheck:
            citation_validation = validate_citations(answer, set(allowed_sources))
            apply_citation_penalty(confidence, citation_validation, self._citation_penalty)
        loaded = set(page_contents.keys())
        evidence_summary = (
            f"本回答基于 {len(wiki_evidence)} 个 Wiki 页面、{len(chunk_evidence)} 个原文片段"
            f"和 {len(fact_evidence)} 条结构化事实生成。"
        )

        return {
            "markdown": answer,
            "confidence": confidence,
            "wiki_evidence": wiki_evidence,
            "chunk_evidence": chunk_evidence,
            "fact_evidence": fact_evidence,
            "evidence_summary": evidence_summary,
            "query_mode": self._log_query_mode_for_reasoning(reasoning_mode, query_mode),
            "reasoning_mode": reasoning_mode,
            "sub_questions": sub_questions_out,
            "react_trace": react_trace_out,
            "retrieval_critique": critique_trace_out,
            "intent": intent,
            "citation_validation": citation_validation,
            "referenced_pages": list(loaded),
            "wiki_sources": [e["filename"] for e in wiki_evidence],
            "qdrant_sources": list(chunk_fns),
        }

    # -----------------------------------------------------------------------
    # LINT
    # -----------------------------------------------------------------------

    def find_gaps(
        self,
        repo: Any,
        username: str,
        query_logs: list[dict],
    ) -> dict[str, Any]:
        """分析查询日志和现有 Wiki，找出知识缺口并提出补充建议。"""
        wiki_dir = self._wiki_dir(username, repo.slug)
        pages = list_wiki_pages(wiki_dir)
        schema_content = _read_file(self._schema_path(username, repo.slug))

        pages_summary = "\n".join(
            f"- {p['filename']}: {p['title']} (type: {p['type']})"
            for p in pages
            if p["filename"] not in ("log.md", "schema.md")
        )
        low_conf_q = "\n".join(f"- {q['question']}" for q in query_logs[:30])

        return self._chat_json(
            system=(
                "你是一个知识库分析师。根据用户的问题历史和当前 Wiki 内容，找出知识缺口。\n\n"
                + (schema_content or "")
            ),
            user=(
                "分析以下低置信度问题（Wiki 无法很好回答的问题）和现有 Wiki 页面，"
                "识别知识缺口并给出具体补充建议。\n\n"
                '返回 JSON：\n'
                '{"gaps": [{"topic": "主题名称", "description": "缺口说明", '
                '"suggested_sources": ["建议来源"], "priority": "high|medium|low"}], '
                '"summary": "总体分析"}\n\n'
                f"--- 低置信度问题 ---\n{low_conf_q or '(无)'}\n\n"
                f"--- 现有 Wiki 页面 ---\n{pages_summary or '(空)'}"
            ),
            default={"gaps": [], "summary": "暂无分析数据"},
        )

    def find_entity_duplicates(
        self,
        repo: Any,
        username: str,
    ) -> dict[str, Any]:
        """识别 Wiki 中可能重复或指代同一概念的页面。"""
        wiki_dir = self._wiki_dir(username, repo.slug)
        pages = list_wiki_pages(wiki_dir)
        schema_content = _read_file(self._schema_path(username, repo.slug))

        pages_detail = []
        for p in pages:
            if p["filename"] in ("log.md", "schema.md", "index.md"):
                continue
            content = _read_file(os.path.join(wiki_dir, p["filename"]))
            fm, _ = render_markdown(content) if content else ({}, "")
            tags = fm.get("tags", [])
            pages_detail.append(
                f"- {p['filename']}: {p['title']} (type: {p['type']}, tags: {tags})"
            )

        return self._chat_json(
            system=(
                "你是一个 Wiki 质量审查员。识别可能重复的页面并建议合并。\n\n"
                + (schema_content or "")
            ),
            user=(
                "分析以下 Wiki 页面列表，找出可能指代同一概念或高度重叠的页面组。\n\n"
                '返回 JSON：\n'
                '{"duplicate_groups": [{"pages": ["a.md", "b.md"], '
                '"reason": "重复原因", "suggestion": "建议操作"}], '
                '"total_issues": 0}\n\n'
                "--- Wiki 页面列表 ---\n" + "\n".join(pages_detail)
            ),
            default={"duplicate_groups": [], "total_issues": 0},
        )

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
