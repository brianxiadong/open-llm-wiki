"""金标驱动的 E2E 打分：页面召回、事实行、拒答、对比防串、关键词覆盖。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# 与 wiki_engine._UNCERTAINTY_PHRASES 对齐，避免评测包依赖全量引擎
_UNCERTAINTY_PHRASES = (
    "基于现有资料只能推测到",
    "现有证据不足以支持更确定的结论",
    "当前知识库中缺少直接证据",
)


def load_questions_file(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("questions") or [])


def _norm_page(name: str) -> str:
    s = (name or "").strip().replace("\\", "/")
    if not s:
        return ""
    base = s.rsplit("/", 1)[-1]
    if not base.endswith(".md"):
        base = f"{base}.md"
    return base


def _evidence_filenames(resp: dict[str, Any]) -> set[str]:
    ev = resp.get("evidence") or {}
    out: set[str] = set()
    for wp in ev.get("wiki_pages") or []:
        fn = _norm_page(str((wp or {}).get("filename") or ""))
        if fn:
            out.add(fn)
    for ch in ev.get("chunks") or []:
        fn = _norm_page(str((ch or {}).get("filename") or ""))
        if fn:
            out.add(fn)
    return out


def _facts_list(resp: dict[str, Any]) -> list[dict[str, Any]]:
    ev = resp.get("evidence") or {}
    raw = ev.get("facts") or []
    return [f for f in raw if isinstance(f, dict)]


def _fact_matches_expected(fact: dict[str, Any], exp: dict[str, Any]) -> bool:
    if exp.get("record_id") and str(fact.get("record_id") or "") != str(exp["record_id"]):
        return False
    if exp.get("sheet") and str(fact.get("sheet") or "") != str(exp["sheet"]):
        return False
    if exp.get("row_index") is not None and int(fact.get("row_index") or -1) != int(
        exp["row_index"]
    ):
        return False
    fields = fact.get("fields") or {}
    if not isinstance(fields, dict):
        fields = {}
    for sub in exp.get("field_substrings") or []:
        needle = str(sub)
        if not needle:
            continue
        joined = " ".join(str(v) for v in fields.values())
        if needle not in joined and needle not in json.dumps(fields, ensure_ascii=False):
            return False
    return True


def _keyword_coverage(answer: str, keywords: list[str]) -> tuple[float, list[str]]:
    if not keywords:
        return 1.0, []
    hits = [k for k in keywords if k and str(k) in answer]
    return len(hits) / len(keywords), hits


def _forbidden_hits(answer: str, forbidden: list[str]) -> list[str]:
    if not answer or not forbidden:
        return []
    lower = answer.lower()
    found: list[str] = []
    for item in forbidden:
        s = str(item).strip()
        if not s:
            continue
        if s.lower() in lower or s in answer:
            found.append(s)
    return found


def _mentions_uncertainty(answer: str, extra_phrases: list[str] | None) -> bool:
    if any(p in answer for p in _UNCERTAINTY_PHRASES):
        return True
    if "暂无相关 Wiki 内容" in answer:
        return True
    for p in extra_phrases or []:
        if p and str(p) in answer:
            return True
    return False


def score_one(
    gold: dict[str, Any],
    api_response: dict[str, Any],
    *,
    http_ok: bool,
    http_status: int | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """对单次 API 响应打分。api_response 为 JSON 体（失败时可传 {}）。"""
    qid = str(gold.get("id") or "")
    answer = str(api_response.get("answer") or "")
    conf = api_response.get("confidence") or {}
    conf_level = str((conf or {}).get("level") or "")
    conf_score = (conf or {}).get("score")

    expected_pages = [_norm_page(str(p)) for p in (gold.get("expected_pages") or [])]
    expected_pages = [p for p in expected_pages if p]
    hit_pages = _evidence_filenames(api_response)
    if expected_pages:
        page_hits = [p for p in expected_pages if p in hit_pages]
        page_recall = len(page_hits) / len(expected_pages)
    else:
        page_hits = []
        page_recall = 1.0

    exp_facts = [f for f in (gold.get("expected_facts") or []) if isinstance(f, dict)]
    facts_ret = _facts_list(api_response)
    fact_row_hits: list[dict[str, Any]] = []
    for exp in exp_facts:
        matched = any(_fact_matches_expected(f, exp) for f in facts_ret)
        fact_row_hits.append({"expected": exp, "matched": matched})
    fact_precision = (
        sum(1 for h in fact_row_hits if h["matched"]) / len(fact_row_hits)
        if fact_row_hits
        else 1.0
    )

    must_abstain = bool(gold.get("must_abstain"))
    extra_abstain = [str(x) for x in (gold.get("abstain_phrases") or []) if x]
    abstain_ok = True
    abstain_detail: dict[str, Any] = {}
    if must_abstain:
        verbal = _mentions_uncertainty(answer, extra_abstain)
        low_conf = conf_level == "low"
        abstain_ok = verbal or low_conf
        abstain_detail = {
            "verbal_uncertainty": verbal,
            "confidence_level": conf_level,
            "pass": abstain_ok,
        }

    comp = gold.get("comparison") if isinstance(gold.get("comparison"), dict) else {}
    forbidden = [str(x) for x in (comp.get("forbidden_in_answer") or []) if x]
    forbidden_found = _forbidden_hits(answer, forbidden)
    comparison_ok = not bool(forbidden_found)

    keywords = [str(k) for k in (gold.get("expected_keywords") or []) if k]
    kw_ratio, kw_hits = _keyword_coverage(answer, keywords)

    # 粗聚合：可按需调权重
    dims = {
        "page_recall": page_recall,
        "fact_row": fact_precision,
        "keywords": kw_ratio,
        "abstain": 1.0 if (not must_abstain or abstain_ok) else 0.0,
        "comparison": 1.0 if comparison_ok else 0.0,
    }
    aggregate = sum(dims.values()) / max(1, len(dims))

    return {
        "id": qid,
        "http_ok": http_ok,
        "http_status": http_status,
        "error": error,
        "latency_ms": api_response.get("latency_ms"),
        "reasoning_mode": api_response.get("reasoning_mode"),
        "confidence_level": conf_level,
        "confidence_score": conf_score,
        "evidence_counts": {
            "wiki_pages": len((api_response.get("evidence") or {}).get("wiki_pages") or []),
            "chunks": len((api_response.get("evidence") or {}).get("chunks") or []),
            "facts": len(_facts_list(api_response)),
        },
        "page_recall": page_recall,
        "expected_pages": expected_pages,
        "hit_pages": sorted(hit_pages & set(expected_pages)),
        "fact_expected_n": len(fact_row_hits),
        "fact_row_precision": fact_precision,
        "fact_row_hits": fact_row_hits,
        "keywords_coverage": kw_ratio,
        "keywords_hits": kw_hits,
        "must_abstain": must_abstain,
        "abstain": abstain_detail,
        "comparison_violations": forbidden_found,
        "dimensions": dims,
        "aggregate": aggregate,
    }


def summarize_runs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总多题 × 多模式结果（rows 每项应含 mode、scores 或顶层 aggregate）。"""
    by_mode: dict[str, list[float]] = {}
    lat_by_mode: dict[str, list[int]] = {}
    for row in rows:
        mode = str(row.get("mode") or "standard")
        agg = row.get("aggregate")
        if agg is None and isinstance(row.get("scores"), dict):
            agg = row["scores"].get("aggregate")
        if isinstance(agg, (int, float)):
            by_mode.setdefault(mode, []).append(float(agg))
        lat = row.get("latency_ms")
        if lat is None and isinstance(row.get("raw_response"), dict):
            lat = row["raw_response"].get("latency_ms")
        if isinstance(lat, int) and lat >= 0:
            lat_by_mode.setdefault(mode, []).append(lat)

    def _avg(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 4) if xs else None

    summary_modes = {
        m: {"mean_aggregate": _avg(v), "n": len(v)} for m, v in by_mode.items()
    }
    summary_latency = {m: _avg([float(x) for x in v]) for m, v in lat_by_mode.items()}
    overall_aggs = [float(x) for xs in by_mode.values() for x in xs]
    return {
        "mean_aggregate": _avg(overall_aggs),
        "per_mode": summary_modes,
        "mean_latency_ms": summary_latency,
        "total_runs": len(rows),
    }
