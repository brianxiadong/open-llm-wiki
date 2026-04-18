"""api_router.preselect_candidates / route_to_repo 纯单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from api_router import _tokenize, preselect_candidates, route_to_repo


# ---------------------------------------------------------------------------
# _tokenize
# ---------------------------------------------------------------------------


def test_tokenize_ascii_lowercases_and_drops_short():
    toks = _tokenize("AE350 RJ45 PoE a")
    assert "ae350" in toks
    assert "rj45" in toks
    assert "poe" in toks
    assert "a" not in toks  # 单字符被过滤


def test_tokenize_filters_stopwords():
    toks = _tokenize("帮我 查询 AE350 的参数")
    assert "帮我" not in toks
    assert "查询" not in toks
    assert "ae350" in toks


# ---------------------------------------------------------------------------
# preselect_candidates
# ---------------------------------------------------------------------------


def test_preselect_returns_all_when_under_limit():
    repos = [
        {"full_name": "u/a", "name": "A", "description": ""},
        {"full_name": "u/b", "name": "B", "description": ""},
    ]
    out = preselect_candidates("hello", repos, limit=10)
    assert out == repos


def test_preselect_ranks_by_name_and_description_overlap():
    repos = [
        {"full_name": "u/notes", "name": "个人笔记", "description": ""},
        {"full_name": "u/ae350", "name": "AE350 产品资料", "description": "小鱼 AE350 规格手册"},
        {"full_name": "u/meeting", "name": "会议纪要", "description": "周会记录"},
        {"full_name": "u/other", "name": "其他", "description": ""},
    ]
    out = preselect_candidates("帮我查询 AE350 产品参数", repos, limit=2)
    assert out[0]["full_name"] == "u/ae350"
    assert len(out) == 2


def test_preselect_falls_back_to_head_when_no_overlap():
    """query 词跟所有 KB 都不重合时，退化为按原顺序取前 N 个。"""
    repos = [
        {"full_name": f"u/{i}", "name": f"KB {i}", "description": ""} for i in range(15)
    ]
    out = preselect_candidates("xyz123nomatch", repos, limit=3)
    assert len(out) == 3
    assert [r["full_name"] for r in out] == ["u/0", "u/1", "u/2"]


def test_preselect_name_weight_beats_description():
    """name 命中权重 2.0 > description 命中 1.0：名字含关键词的应排前。"""
    repos = [
        {"full_name": "u/desc-only", "name": "XYZ", "description": "AE350 相关内容"},
        {"full_name": "u/name-hit", "name": "AE350 手册", "description": ""},
    ]
    out = preselect_candidates("AE350", repos, limit=1)
    assert out[0]["full_name"] == "u/name-hit"


# ---------------------------------------------------------------------------
# route_to_repo
# ---------------------------------------------------------------------------


def _llm(return_value=None, raise_exc=None):
    m = MagicMock()
    if raise_exc is not None:
        m.chat_json.side_effect = raise_exc
    else:
        m.chat_json.return_value = return_value
    return m


def test_route_empty_candidates_short_circuits():
    llm = _llm()
    r = route_to_repo(llm, "anything", [], min_confidence=0.5)
    assert r["ok"] is False
    assert r["error"] == "empty_candidates"
    assert r["selected_full_name"] is None
    assert llm.chat_json.call_count == 0  # 不该调 LLM


def test_route_high_confidence_ok():
    candidates = [
        {"full_name": "u/a", "name": "A", "description": ""},
        {"full_name": "u/b", "name": "B", "description": ""},
    ]
    llm = _llm(return_value={"selected": "u/a", "confidence": 0.9, "reason": "命中 A"})
    r = route_to_repo(llm, "q", candidates, min_confidence=0.5)
    assert r["ok"] is True
    assert r["selected_full_name"] == "u/a"
    assert r["confidence"] == pytest.approx(0.9)
    assert r["reason"] == "命中 A"


def test_route_low_confidence_rejected():
    candidates = [{"full_name": "u/a", "name": "A", "description": ""}]
    llm = _llm(return_value={"selected": "u/a", "confidence": 0.2, "reason": "不太确定"})
    r = route_to_repo(llm, "q", candidates, min_confidence=0.5)
    assert r["ok"] is False
    assert r["error"] == "low_confidence"
    assert r["selected_full_name"] is None


def test_route_selected_not_in_candidates_rejected():
    candidates = [{"full_name": "u/a", "name": "A", "description": ""}]
    llm = _llm(return_value={"selected": "u/ghost", "confidence": 0.99, "reason": "?"})
    r = route_to_repo(llm, "q", candidates, min_confidence=0.5)
    assert r["ok"] is False
    assert r["error"] == "selected_not_in_candidates"
    assert r["selected_full_name"] is None


def test_route_null_selected_rejected():
    candidates = [{"full_name": "u/a", "name": "A", "description": ""}]
    llm = _llm(return_value={"selected": None, "confidence": 0.0, "reason": "无匹配"})
    r = route_to_repo(llm, "q", candidates, min_confidence=0.5)
    assert r["ok"] is False
    assert r["error"] == "low_confidence"


def test_route_bad_confidence_type_coerces_to_zero():
    candidates = [{"full_name": "u/a", "name": "A", "description": ""}]
    llm = _llm(return_value={"selected": "u/a", "confidence": "not-a-number", "reason": ""})
    r = route_to_repo(llm, "q", candidates, min_confidence=0.5)
    assert r["ok"] is False
    assert r["confidence"] == 0.0


def test_route_llm_exception_surfaces_as_llm_error():
    candidates = [{"full_name": "u/a", "name": "A", "description": ""}]
    llm = _llm(raise_exc=RuntimeError("boom"))
    r = route_to_repo(llm, "q", candidates, min_confidence=0.5)
    assert r["ok"] is False
    assert r["error"] == "llm_error"
    assert "boom" in r["reason"]


def test_route_empty_llm_response_handled():
    candidates = [{"full_name": "u/a", "name": "A", "description": ""}]
    llm = _llm(return_value=None)
    r = route_to_repo(llm, "q", candidates, min_confidence=0.5)
    assert r["ok"] is False
    assert r["error"] == "low_confidence"


def test_route_confidence_clamped_to_01():
    candidates = [{"full_name": "u/a", "name": "A", "description": ""}]
    llm = _llm(return_value={"selected": "u/a", "confidence": 1.5, "reason": ""})
    r = route_to_repo(llm, "q", candidates, min_confidence=0.5)
    assert r["confidence"] == pytest.approx(1.0)
    assert r["ok"] is True
