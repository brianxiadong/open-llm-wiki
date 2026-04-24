"""金标 + 合成响应：CI 内验证 E2E 打分与 /api/v1/search 装配（mock 引擎，无真实 LLM）。"""

from __future__ import annotations

from unittest.mock import patch

from eval.e2e.harness import (
    build_synthetic_api_response,
    build_synthetic_engine_result,
    index_questions_by_prompt,
    load_e2e_gold_questions,
    run_scoring_on_synthetic,
)
from eval.e2e.scoring import score_one
from tests.test_api_v1 import _create_repo, _create_user_with_token, _h


def test_run_scoring_on_synthetic_perfect_on_pack():
    report = run_scoring_on_synthetic()
    assert report["min_aggregate"] == 1.0
    assert len(report["rows"]) >= 1
    for row in report["rows"]:
        assert row["aggregate"] == 1.0, row.get("id")


def test_synthetic_engine_matches_gold_modes():
    gold = load_e2e_gold_questions()[0]
    for mode in ("standard", "deep", "react"):
        eng = build_synthetic_engine_result(gold, reasoning_mode=mode)
        assert eng["reasoning_mode"] == mode
        api = build_synthetic_api_response(gold, reasoning_mode=mode)
        assert score_one(gold, api, http_ok=True)["aggregate"] == 1.0


def test_api_search_harness_mock_all_questions_all_reasoning_modes(app):
    questions = load_e2e_gold_questions()
    by_prompt = index_questions_by_prompt(questions)
    plaintext, _, user_id = _create_user_with_token(app, username="e2e_harness_u")
    _create_repo(app, owner_id=user_id, name="Harness KB", slug="e2e-harness-kb")

    def side_effect(repo, owner, question, *args, **kwargs):
        g = by_prompt.get(str(question).strip())
        assert g is not None, f"missing gold for question: {question!r}"
        rm = kwargs.get("reasoning_mode", "standard")
        return build_synthetic_engine_result(g, reasoning_mode=rm)

    with patch.object(app.wiki_engine, "query_with_evidence", side_effect=side_effect):
        client = app.test_client()
        for mode in ("standard", "deep", "react"):
            for g in questions:
                resp = client.post(
                    "/api/v1/search",
                    json={
                        "query": g["question"],
                        "repo": "e2e_harness_u/e2e-harness-kb",
                        "reasoning_mode": mode,
                    },
                    headers=_h(plaintext),
                )
                assert resp.status_code == 200, (g.get("id"), resp.get_json())
                data = resp.get_json()
                assert data.get("reasoning_mode") == mode
                sc = score_one(g, data, http_ok=True)
                assert sc["aggregate"] == 1.0, (g.get("id"), mode, sc["dimensions"])
