"""CI / pytest 用金标评测：从 `e2e_questions.json` 合成「理想引擎输出」，不启真实 LLM。

用于验证：金标字段自洽、`score_one` 维度、以及 `/api/v1/search` 在 mock 引擎下的装配逻辑。
线上真实效果仍用 `eval/scripts/eval_e2e.py`。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eval.e2e.scoring import _norm_page, score_one, summarize_runs

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_GOLD_PATH = _REPO_ROOT / "eval" / "ground_truth" / "e2e_questions.json"


def load_e2e_gold_questions(path: Path | None = None) -> list[dict[str, Any]]:
    data = json.loads((path or _DEFAULT_GOLD_PATH).read_text(encoding="utf-8"))
    return [q for q in (data.get("questions") or []) if isinstance(q, dict)]


def index_questions_by_prompt(questions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(q.get("question") or "").strip(): q for q in questions if str(q.get("question") or "").strip()}


def build_synthetic_engine_result(
    gold: dict[str, Any],
    *,
    reasoning_mode: str = "standard",
) -> dict[str, Any]:
    """构造与 `WikiEngine.query_with_evidence` 返回结构兼容的 dict，供 patch 或手工检查。"""
    pages_raw = gold.get("expected_pages") or []
    pages = [_norm_page(str(p)) for p in pages_raw if p]
    must_abstain = bool(gold.get("must_abstain"))
    keywords = [str(k) for k in (gold.get("expected_keywords") or []) if k]

    if must_abstain:
        markdown = "当前知识库中缺少直接证据说明该问题。"
        confidence = {"level": "low", "score": 0.28, "reasons": ["synthetic_abstain"]}
        wiki_evidence: list[dict[str, Any]] = []
        chunk_evidence: list[dict[str, Any]] = []
        fact_evidence: list[dict[str, Any]] = []
    else:
        if keywords:
            markdown = "## 答案\n" + "；".join(keywords)
        else:
            markdown = "## 答案\n（参见引用的证据页面。）"
        confidence = {"level": "high", "score": 0.91, "reasons": ["synthetic_hit"]}
        wiki_evidence = [
            {
                "filename": fn,
                "title": fn.replace(".md", ""),
                "reason": "synthetic",
                "type": "concept",
                "url": f"/wiki/{fn.replace('.md', '')}",
            }
            for fn in pages
        ]
        chunk_evidence = [
            {
                "chunk_id": f"syn-{i}",
                "filename": fn,
                "page_title": fn.replace(".md", ""),
                "heading": "",
                "snippet": f"synthetic chunk for {fn}",
                "score": 0.86,
                "sources": ["dense"],
            }
            for i, fn in enumerate(pages[:8] or ["placeholder.md"])
        ]
        if not pages:
            chunk_evidence = [
                {
                    "chunk_id": "syn-0",
                    "filename": "placeholder.md",
                    "page_title": "placeholder",
                    "heading": "",
                    "snippet": "synthetic",
                    "score": 0.5,
                    "sources": ["dense"],
                }
            ]
        fact_evidence = []
        for ef in gold.get("expected_facts") or []:
            if not isinstance(ef, dict):
                continue
            fields: dict[str, Any] = {}
            subs = ef.get("field_substrings") or []
            for j, sub in enumerate(subs):
                key = ("EstCost_USD" if str(sub).isdigit() and len(str(sub)) >= 5 else f"field_{j}")
                fields[key] = sub if not str(sub).isdigit() else int(sub)
            if not fields and not subs:
                fields["_synthetic"] = "1"
            rid = str(ef.get("record_id") or "")
            fact_evidence.append(
                {
                    "record_id": rid,
                    "source_file": "e2e-training-costs.xlsx",
                    "source_markdown_filename": "e2e-training-costs.md",
                    "sheet": str(ef.get("sheet") or ""),
                    "row_index": ef.get("row_index"),
                    "fields": fields,
                    "snippet": json.dumps(fields, ensure_ascii=False)[:200],
                    "score": 0.93,
                    "title": f"{ef.get('sheet', '')} 第 {ef.get('row_index', '')} 行",
                    "url": "/sources/e2e-training-costs.md",
                }
            )

    return {
        "markdown": markdown,
        "confidence": confidence,
        "wiki_evidence": wiki_evidence,
        "chunk_evidence": chunk_evidence,
        "fact_evidence": fact_evidence,
        "evidence_summary": "synthetic harness",
        "query_mode": "hybrid",
        "intent": "generic",
        "citation_validation": {"cited": [], "unknown": [], "ok": True},
        "referenced_pages": list(pages),
        "wiki_sources": list(pages),
        "qdrant_sources": list(pages[:3]),
        "reasoning_mode": reasoning_mode,
        "sub_questions": [],
        "react_trace": [],
        "retrieval_critique": [],
    }


def build_synthetic_api_response(
    gold: dict[str, Any],
    *,
    reasoning_mode: str = "standard",
    latency_ms: int = 7,
) -> dict[str, Any]:
    """构造与 `POST /api/v1/search` 响应体（打分相关字段）一致的 dict。"""
    eng = build_synthetic_engine_result(gold, reasoning_mode=reasoning_mode)
    wiki_ev = eng.get("wiki_evidence") or []
    chunk_ev = eng.get("chunk_evidence") or []
    fact_ev = eng.get("fact_evidence") or []
    return {
        "answer": eng.get("markdown", "") or "",
        "confidence": eng.get("confidence", {}) or {},
        "query_mode": eng.get("query_mode", "") or "",
        "intent": eng.get("intent"),
        "citation_validation": eng.get("citation_validation"),
        "reasoning_mode": eng.get("reasoning_mode", reasoning_mode),
        "sub_questions": eng.get("sub_questions") or [],
        "react_trace": eng.get("react_trace") or [],
        "retrieval_critique": eng.get("retrieval_critique") or [],
        "latency_ms": latency_ms,
        "evidence": {
            "wiki_pages": [
                {
                    "filename": e.get("filename", ""),
                    "title": e.get("title", ""),
                    "reason": e.get("reason", ""),
                }
                for e in wiki_ev
            ],
            "chunks": [
                {
                    "filename": e.get("filename", ""),
                    "score": e.get("score"),
                    "snippet": (e.get("snippet") or "")[:300],
                }
                for e in chunk_ev
            ],
            "facts": [
                {
                    "record_id": e.get("record_id", ""),
                    "source_file": e.get("source_file", ""),
                    "source_markdown_filename": e.get("source_markdown_filename", ""),
                    "sheet": e.get("sheet", ""),
                    "row_index": e.get("row_index"),
                    "score": e.get("score"),
                    "fields": e.get("fields", {}),
                    "snippet": (e.get("snippet") or "")[:300],
                    "title": e.get("title", ""),
                    "url": e.get("url", ""),
                }
                for e in fact_ev
            ],
        },
    }


def run_scoring_on_synthetic(questions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """对金标列表逐题合成响应并打分，返回报告结构（供 pytest 或 CLI）。"""
    qs = questions if questions is not None else load_e2e_gold_questions()
    rows: list[dict[str, Any]] = []
    for gold in qs:
        api = build_synthetic_api_response(gold)
        scores = score_one(gold, api, http_ok=True)
        rows.append(
            {
                "id": gold.get("id"),
                "aggregate": scores["aggregate"],
                "dimensions": scores["dimensions"],
                "scores": scores,
            }
        )
    summary = summarize_runs(rows)
    aggs = (
        r["aggregate"]
        for r in rows
        if isinstance(r.get("aggregate"), (int, float))
    )
    min_agg = min(aggs, default=1.0)
    return {"summary": summary, "rows": rows, "min_aggregate": min_agg}


def main() -> None:
    report = run_scoring_on_synthetic()
    out = {
        "min_aggregate": report["min_aggregate"],
        "summary": report["summary"],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
