"""自然语言 → 知识库路由（/api/v1/search 无 repo 分支使用）。

职责：
1. 粗筛：候选 KB 很多时，按 query 与 (name + description) 的分词重合度
   取 top N，避免把全站 KB 一次性喂给 LLM，控制成本与 latency。
2. LLM 路由：在候选清单里选 1 个最匹配的 KB，产出置信度与原因；
   置信度不足或候选为空时返回 None 让调用方降级。

设计约束：
- 纯函数，不依赖 Flask / DB 对象；输入输出全是 dict/list，方便单测与复用。
- 对 LLM 返回做严格校验：selected 必须出现在 candidates 里，防幻觉污染。
- 温度低（0.0 传参）但让调用方自由；本模块只管格式约束。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 粗筛：query ↔ (name + description) 的分词重合度
# ---------------------------------------------------------------------------

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_RE = re.compile(r"[A-Za-z0-9]+")
_STOPWORDS = {
    "的", "了", "和", "与", "及", "在", "是", "有", "这", "那", "哪",
    "帮我", "查询", "查一下", "查下", "看一下", "看下", "请", "我",
    "the", "a", "an", "of", "is", "are", "to", "and", "or", "for",
}


def _tokenize(text: str) -> list[str]:
    """轻量分词：CJK 走 jieba（按需 import），ASCII 走字母/数字块，全部 lowercase。"""
    if not text:
        return []
    tokens: list[str] = []
    for m in _ASCII_RE.findall(text):
        w = m.lower()
        if len(w) >= 2 and w not in _STOPWORDS:
            tokens.append(w)
    cjk_parts = _CJK_RE.findall(text)
    if cjk_parts:
        try:
            import jieba  # noqa: WPS433 (lazy import; 可选依赖)
            for part in cjk_parts:
                for w in jieba.cut_for_search(part):  # type: ignore[attr-defined]
                    w = w.strip()
                    if len(w) >= 2 and w not in _STOPWORDS:
                        tokens.append(w)
        except Exception:  # noqa: BLE001 — 兜底：退化为整块中文
            for part in cjk_parts:
                if len(part) >= 2 and part not in _STOPWORDS:
                    tokens.append(part)
    return tokens


def _score_candidate(query_tokens: set[str], repo: dict) -> float:
    """重合度评分：name 命中权重 2.0，description 命中权重 1.0。"""
    if not query_tokens:
        return 0.0
    name_tokens = set(_tokenize(repo.get("name") or ""))
    desc_tokens = set(_tokenize(repo.get("description") or ""))
    name_hit = len(query_tokens & name_tokens)
    desc_hit = len(query_tokens & desc_tokens)
    return 2.0 * name_hit + 1.0 * desc_hit


def preselect_candidates(
    query: str,
    repos: Iterable[dict],
    *,
    limit: int = 10,
) -> list[dict]:
    """按 query 与 (name+description) 的重合度粗筛 top N 候选。

    - 候选数 ≤ limit 时直接原样返回（保持 updated_at 顺序）。
    - 评分全 0（无任何重合）时，也直接原样返回前 limit 个，让 LLM 自己选。
    """
    repo_list = list(repos)
    if len(repo_list) <= limit:
        return repo_list
    q_tokens = set(_tokenize(query))
    scored = [(_score_candidate(q_tokens, r), idx, r) for idx, r in enumerate(repo_list)]
    # 任何评分都 > 0 的放前面；相同分数按原顺序稳定
    scored.sort(key=lambda t: (-t[0], t[1]))
    if scored[0][0] <= 0:
        return repo_list[:limit]
    return [r for _, _, r in scored[:limit]]


# ---------------------------------------------------------------------------
# LLM 路由
# ---------------------------------------------------------------------------

_ROUTER_SYSTEM_PROMPT = (
    "你是「知识库路由器」。给定用户的自然语言问题与候选知识库清单（name + description），"
    "判断用户最想检索的是哪一个知识库。"
    "\n\n【严格输出 JSON】"
    "\n{"
    "\n  \"selected\": \"owner/slug\" | null,   // 选中的知识库，全路径；选不出则 null"
    "\n  \"confidence\": 0.0~1.0,                  // 主观置信度"
    "\n  \"reason\": \"≤ 60 字中文说明依据\""
    "\n}"
    "\n\n【判断原则】"
    "\n1. 直接名称 / 产品型号 / 关键词匹配 → 高置信度（≥ 0.8）。"
    "\n2. 仅主题相近或描述侧面相关 → 中等（0.5 ~ 0.8）。"
    "\n3. 无明显匹配、或多个 KB 都像 → selected=null，confidence ≤ 0.3。"
    "\n4. selected 字段必须**精确等于**候选中的某个 `owner/slug`，禁止自造。"
    "\n5. 候选为空时直接返回 selected=null。"
)


def _build_router_user_prompt(query: str, candidates: list[dict]) -> str:
    lines: list[str] = [f"用户问题：{query}\n\n候选知识库："]
    if not candidates:
        lines.append("（当前用户无可见知识库）")
    else:
        for idx, c in enumerate(candidates, 1):
            full = c.get("full_name") or f"{c.get('owner','?')}/{c.get('slug','?')}"
            name = c.get("name") or "（未命名）"
            desc = (c.get("description") or "").strip() or "（暂无描述）"
            desc = desc.replace("\n", " ")
            if len(desc) > 200:
                desc = desc[:200] + "…"
            lines.append(f"{idx}. `{full}` — name={name} — description={desc}")
    return "\n".join(lines)


def route_to_repo(
    llm_client: Any,
    query: str,
    candidates: list[dict],
    *,
    min_confidence: float = 0.5,
) -> dict:
    """调用 LLM 在候选里选 1 个知识库。

    返回：
      ``{"selected_full_name": "owner/slug" | None,
         "confidence": float, "reason": str, "ok": bool,
         "error": "empty_candidates" | "low_confidence" | "llm_error" | "selected_not_in_candidates" | None,
         "raw": {...}}``
    """
    result: dict = {
        "selected_full_name": None,
        "confidence": 0.0,
        "reason": "",
        "ok": False,
        "error": None,
        "raw": None,
    }
    if not candidates:
        result["error"] = "empty_candidates"
        result["reason"] = "当前用户没有可见的知识库"
        return result

    system = _ROUTER_SYSTEM_PROMPT
    user = _build_router_user_prompt(query, candidates)
    raw: dict = {}
    try:
        raw = llm_client.chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        ) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("router chat_json failed: %s", exc)
        result["error"] = "llm_error"
        result["reason"] = f"LLM 调用失败：{exc}"
        return result

    result["raw"] = raw
    selected = raw.get("selected")
    try:
        confidence = float(raw.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    reason = (raw.get("reason") or "").strip()
    result["confidence"] = max(0.0, min(1.0, confidence))
    result["reason"] = reason

    if not selected or not isinstance(selected, str):
        result["error"] = "low_confidence"
        if not reason:
            result["reason"] = "LLM 未选出匹配的知识库"
        return result

    # 防幻觉：selected 必须在候选里
    candidate_full_names = {
        c.get("full_name") or f"{c.get('owner','?')}/{c.get('slug','?')}"
        for c in candidates
    }
    if selected not in candidate_full_names:
        logger.info("router selected %r not in candidates", selected)
        result["error"] = "selected_not_in_candidates"
        if not reason:
            result["reason"] = f"LLM 返回的 {selected!r} 不在候选清单中"
        return result

    if result["confidence"] < min_confidence:
        result["error"] = "low_confidence"
        if not reason:
            result["reason"] = (
                f"置信度 {result['confidence']:.2f} 低于阈值 {min_confidence:.2f}"
            )
        return result

    result["selected_full_name"] = selected
    result["ok"] = True
    return result


__all__ = [
    "preselect_candidates",
    "route_to_repo",
]
