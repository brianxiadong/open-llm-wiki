"""eval.e2e.scoring 单元测试（不发起 HTTP）。"""

from __future__ import annotations

from eval.e2e.scoring import score_one, summarize_runs


def test_score_page_recall_and_facts():
    gold = {
        "id": "t1",
        "expected_pages": ["ae350-overview.md", "other.md"],
        "expected_facts": [{"sheet": "参数", "row_index": 2, "record_id": "fact-rec-1"}],
        "expected_keywords": ["RJ45"],
        "must_abstain": False,
        "comparison": {"forbidden_in_answer": ["错误实体X"]},
    }
    resp = {
        "answer": "## 答案\n设备带 RJ45。",
        "confidence": {"level": "high", "score": 0.8},
        "reasoning_mode": "standard",
        "latency_ms": 100,
        "evidence": {
            "wiki_pages": [{"filename": "ae350-overview.md"}],
            "chunks": [{"filename": "ae350-specifications.md"}],
            "facts": [
                {
                    "record_id": "fact-rec-1",
                    "sheet": "参数",
                    "row_index": 2,
                    "fields": {"接口": "RJ45"},
                }
            ],
        },
    }
    s = score_one(gold, resp, http_ok=True)
    assert s["page_recall"] == 0.5
    assert s["fact_row_precision"] == 1.0
    assert s["keywords_coverage"] == 1.0
    assert s["dimensions"]["comparison"] == 1.0


def test_must_abstain_passes_on_uncertainty_phrase():
    gold = {"id": "t2", "must_abstain": True}
    resp = {
        "answer": "当前知识库中缺少直接证据说明该问题。",
        "confidence": {"level": "high", "score": 0.9},
        "evidence": {"wiki_pages": [], "chunks": [], "facts": []},
    }
    s = score_one(gold, resp, http_ok=True)
    assert s["dimensions"]["abstain"] == 1.0
    assert s["abstain"]["pass"] is True


def test_must_abstain_passes_on_low_confidence():
    gold = {"id": "t3", "must_abstain": True}
    resp = {
        "answer": "可能是这样。",
        "confidence": {"level": "low", "score": 0.2},
        "evidence": {"wiki_pages": [], "chunks": [], "facts": []},
    }
    s = score_one(gold, resp, http_ok=True)
    assert s["dimensions"]["abstain"] == 1.0


def test_comparison_violation():
    gold = {
        "id": "t4",
        "comparison": {"forbidden_in_answer": ["竞品ZZ型号"]},
    }
    resp = {
        "answer": "对比：竞品ZZ型号更好。",
        "confidence": {"level": "high", "score": 0.9},
        "evidence": {"wiki_pages": [], "chunks": [], "facts": []},
    }
    s = score_one(gold, resp, http_ok=True)
    assert s["dimensions"]["comparison"] == 0.0
    assert "竞品ZZ型号" in s["comparison_violations"]


def test_summarize_runs_per_mode():
    rows = [
        {"mode": "standard", "aggregate": 0.8},
        {"mode": "standard", "aggregate": 0.6},
        {"mode": "deep", "aggregate": 0.9},
    ]
    s = summarize_runs(rows)
    assert s["per_mode"]["standard"]["n"] == 2
    assert s["per_mode"]["deep"]["n"] == 1
    assert s["mean_aggregate"] is not None
