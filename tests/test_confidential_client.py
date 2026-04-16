from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from confidential_client.repository import ConfidentialRepository
from confidential_client.runtime import ConfidentialRuntime
from llmwiki_core.contracts import ConfidentialServices


def _services() -> ConfidentialServices:
    return ConfidentialServices(
        llm_api_base="http://fake-llm",
        llm_api_key="key",
        llm_model="test-model",
        llm_max_tokens=1024,
        embedding_api_base="http://fake-embed",
        embedding_api_key="",
        embedding_model="test-embed",
        embedding_dimensions=128,
        qdrant_url="http://fake-qdrant:6333",
        mineru_api_url="http://fake-mineru:8000",
    )


def test_confidential_repository_round_trip(tmp_path):
    repo_dir = tmp_path / "repo"
    repo = ConfidentialRepository.create(
        repo_dir,
        name="Finance",
        slug="finance",
        passphrase="secret-pass",
        services=_services(),
    )

    assert repo.manifest.mode == "confidential"
    assert repo.vault_path.exists()

    with repo.unlocked("secret-pass") as workspace:
        schema_path = workspace.repo_paths.wiki_dir / "schema.md"
        assert schema_path.exists()
        assert workspace.load_services().qdrant_url == "http://fake-qdrant:6333"

    with pytest.raises(Exception):
        with repo.unlocked("wrong-pass"):
            pass


@pytest.fixture
def confidential_qdrant(tmp_path):
    mock_client = MagicMock()
    mock_client.collection_exists.return_value = True
    with patch("qdrant_service.QdrantClient", return_value=mock_client), \
         patch("qdrant_service.OpenAI") as mock_openai:
        mock_embed_resp = MagicMock()
        mock_embed_resp.data = [MagicMock(embedding=[0.1] * 128)]
        mock_embed_resp.usage = MagicMock(total_tokens=10)
        mock_openai.return_value.embeddings.create.return_value = mock_embed_resp
        from confidential_client.qdrant import ConfidentialQdrantService

        service = ConfidentialQdrantService(
            qdrant_map_path=tmp_path / "qdrant-map.sqlite",
            qdrant_url="http://fake-qdrant:6333",
            embedding_api_base="http://fake-embed",
            embedding_api_key="",
            embedding_model="test-embed",
            embedding_dimensions=128,
        )
        yield service, mock_client


def test_confidential_qdrant_payload_excludes_plaintext(confidential_qdrant):
    service, mock_client = confidential_qdrant

    service.upsert_page(
        repo_id=7,
        filename="budget.md",
        title="预算",
        page_type="analysis",
        content="# Budget\n\nSensitive details here.",
    )
    service.upsert_page_chunks(
        repo_id=7,
        filename="budget.md",
        title="预算",
        page_type="analysis",
        content="# Budget\n\n" + "word " * 120,
    )
    service.upsert_fact_records(
        repo_id=7,
        source_filename="sales.md",
        records=[
            {
                "record_id": "csv:2",
                "source_file": "sales.csv",
                "source_markdown_filename": "sales.md",
                "sheet": "CSV",
                "row_index": 2,
                "fields": {"地区": "华东", "收入": 1200},
                "fact_text": "来源=sales.csv; 表=CSV; 行=2; 地区=华东; 收入=1200",
            }
        ],
    )

    payloads: list[dict] = []
    for call in mock_client.upsert.call_args_list:
        for point in call.kwargs["points"]:
            payloads.append(point.payload)

    assert payloads
    for payload in payloads:
        assert "content" not in payload
        assert "title" not in payload
        assert "chunk_text" not in payload
        assert "fact_text" not in payload
        assert "point_ref" in payload


def test_confidential_qdrant_search_resolves_local_metadata(confidential_qdrant):
    service, mock_client = confidential_qdrant

    service.upsert_page(
        repo_id=9,
        filename="plan.md",
        title="计划",
        page_type="guide",
        content="# Plan\n\nTop secret.",
    )
    point_id = service._safe_point_id(service._stable_point_id(9, "plan.md"))
    mock_hit = MagicMock(id=point_id, score=0.91, payload={"point_ref": f"page:{point_id}"})
    mock_client.query_points.return_value = MagicMock(points=[mock_hit])

    results = service.search(repo_id=9, query="计划是什么")

    assert results == [{"filename": "plan.md", "title": "计划", "score": 0.91}]


def test_confidential_runtime_ingest_and_query(tmp_path):
    repo_dir = tmp_path / "repo"
    source_path = tmp_path / "finance.md"
    source_path.write_text("# 财务数据\n\n这是敏感材料。\n", encoding="utf-8")

    ConfidentialRepository.create(
        repo_dir,
        name="Finance",
        slug="finance",
        passphrase="secret-pass",
        services=_services(),
    )

    mock_llm = MagicMock()
    mock_llm.chat_json.side_effect = [
        {
            "summary": "finance",
            "key_entities": ["预算"],
            "key_concepts": ["现金流"],
            "main_findings": ["利润改善"],
        },
        {
            "pages_to_create": [
                {
                    "filename": "budget.md",
                    "title": "预算",
                    "type": "analysis",
                    "reason": "new topic",
                }
            ],
            "pages_to_update": [],
        },
        {"filenames": ["budget.md"]},
    ]
    mock_llm.chat.side_effect = [
        "---\ntitle: 预算\ntype: analysis\nupdated: 2026-04-16\n---\n\n# 预算\n\n预算内容。\n",
        "---\ntitle: 首页\ntype: index\nupdated: 2026-04-16\n---\n\n# Index\n\n- [预算](budget.md)\n",
        "---\ntitle: 概览\ntype: overview\nupdated: 2026-04-16\n---\n\n# 概览\n\n预算概览。\n",
        "这是客户端本地生成的回答。",
    ]

    mock_qdrant = MagicMock()
    mock_qdrant.search_chunks.return_value = []
    mock_qdrant.search_facts.return_value = []

    with patch("confidential_client.runtime.LLMClient", return_value=mock_llm), \
         patch("confidential_client.runtime.ConfidentialQdrantService", return_value=mock_qdrant):
        runtime = ConfidentialRuntime(ConfidentialRepository(repo_dir), "secret-pass")
        events = runtime.ingest_file(source_path)
        result = runtime.query("预算情况如何？")
        history = runtime.load_history()

    assert any(event.get("phase") == "done" for event in events)
    assert result.answer == "这是客户端本地生成的回答。"
    assert history[-1]["question"] == "预算情况如何？"
    assert mock_qdrant.upsert_page.called


def test_confidential_cli_create_and_export(tmp_path):
    services_path = tmp_path / "services.json"
    services_path.write_text(
        """{
  "llm_api_base": "http://fake-llm",
  "llm_api_key": "key",
  "llm_model": "test-model",
  "llm_max_tokens": 1024,
  "embedding_api_base": "http://fake-embed",
  "embedding_api_key": "",
  "embedding_model": "test-embed",
  "embedding_dimensions": 128,
  "qdrant_url": "http://fake-qdrant:6333",
  "mineru_api_url": ""
}""",
        encoding="utf-8",
    )
    repo_dir = tmp_path / "cli-repo"
    export_path = tmp_path / "cli-repo.tgz"

    from confidential_client.cli import cli

    runner = CliRunner()
    create_result = runner.invoke(
        cli,
        [
            "create",
            str(repo_dir),
            "--name",
            "CLI Repo",
            "--slug",
            "cli-repo",
            "--passphrase",
            "secret-pass",
            "--services-file",
            str(services_path),
        ],
    )
    export_result = runner.invoke(
        cli,
        ["export", str(repo_dir), str(export_path)],
    )

    assert create_result.exit_code == 0
    assert export_result.exit_code == 0
    assert export_path.exists()
