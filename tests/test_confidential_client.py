from __future__ import annotations

import tarfile
from pathlib import Path
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


def test_confidential_repository_plain_mode_round_trip(tmp_path):
    repo_dir = tmp_path / "repo-plain"
    repo = ConfidentialRepository.create(
        repo_dir,
        name="Finance Plain",
        slug="finance-plain",
        passphrase="",
        services=_services(),
        storage_mode="plain",
    )

    assert repo.manifest.mode == "confidential"
    assert repo.manifest.storage_mode == "plain"
    assert repo.requires_passphrase is False
    assert repo.vault_path.exists()

    with repo.unlocked("") as workspace:
        schema_path = workspace.repo_paths.wiki_dir / "schema.md"
        assert schema_path.exists()
        assert workspace.load_services().qdrant_url == "http://fake-qdrant:6333"

    with repo.unlocked("") as workspace:
        workspace.append_history("Q", "A")

    with repo.unlocked("") as workspace:
        assert workspace.load_history()[-1]["answer"] == "A"


def test_confidential_repository_restore_rejects_path_traversal(tmp_path):
    bundle_path = tmp_path / "malicious.tgz"
    escaped_path = tmp_path / "escaped.txt"

    with tarfile.open(bundle_path, mode="w:gz") as tar:
        manifest = tmp_path / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        tar.add(manifest, arcname="../escaped.txt")

    with pytest.raises(ValueError, match="unsafe path"):
        ConfidentialRepository.restore(tmp_path / "restored", bundle_path)

    assert not escaped_path.exists()


@pytest.fixture
def confidential_qdrant(tmp_path):
    mock_client = MagicMock()
    mock_client.collection_exists.return_value = True
    with patch("qdrant_service.QdrantClient", return_value=mock_client), \
         patch("qdrant_service.OpenAI") as mock_openai:
        def fake_embed(*, model, input):
            texts = input if isinstance(input, list) else [input]
            response = MagicMock()
            response.data = [
                MagicMock(index=idx, embedding=[float(idx + 1)] * 128)
                for idx, _ in enumerate(texts)
            ]
            response.usage = MagicMock(total_tokens=len(texts))
            return response

        mock_openai.return_value.embeddings.create.side_effect = fake_embed
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


def test_confidential_qdrant_fact_upsert_batches_large_payloads(confidential_qdrant):
    service, mock_client = confidential_qdrant

    records = [
        {
            "record_id": f"csv:{idx}",
            "source_file": "250411.xlsx",
            "source_markdown_filename": "250411.md",
            "sheet": "Sheet1",
            "row_index": idx,
            "fields": {"id": idx},
            "fact_text": f"row={idx}",
        }
        for idx in range(service.UPSERT_BATCH_SIZE * 2 + 9)
    ]

    service.upsert_fact_records(
        repo_id=11,
        source_filename="250411.md",
        records=records,
    )

    fact_calls = [
        call
        for call in mock_client.upsert.call_args_list
        if call.kwargs["collection_name"] == "repo_11_facts"
    ]
    expected_upserts = (
        len(records) + service.EMBEDDING_BATCH_SIZE - 1
    ) // service.EMBEDDING_BATCH_SIZE
    assert len(fact_calls) == expected_upserts
    assert sum(len(call.kwargs["points"]) for call in fact_calls) == len(records)
    assert all(len(call.kwargs["points"]) <= service.UPSERT_BATCH_SIZE for call in fact_calls)


def test_confidential_runtime_records_fact_progress_events(tmp_path):
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
    runtime = ConfidentialRuntime(ConfidentialRepository(repo_dir), "secret-pass")
    progress_events = []

    class FakeEngine:
        def ingest(self, repo, username, source_filename, progress_callback=None):
            if progress_callback:
                progress_callback({"phase": "index", "progress": 86, "message": "Indexing 64/8000 fact records …"})
                progress_callback({"phase": "index", "progress": 87, "message": "Indexing 128/8000 fact records …"})
            yield {"phase": "done", "progress": 100, "message": "Ingest complete: 1 created, 0 updated"}

    with patch.object(runtime, "_build_engine", return_value=FakeEngine()), \
         patch.object(runtime, "_stage_source", return_value="finance.md"):
        runtime.ingest_file(source_path, on_event=progress_events.append)

    assert any(event["message"] == "Indexing 64/8000 fact records …" for event in progress_events)
    assert any(event["message"] == "Indexing 128/8000 fact records …" for event in progress_events)


def test_confidential_runtime_delete_document_cleans_local_and_qdrant(tmp_path):
    repo_dir = tmp_path / "repo"
    ConfidentialRepository.create(
        repo_dir,
        name="Finance",
        slug="finance",
        passphrase="secret-pass",
        services=_services(),
    )
    repo = ConfidentialRepository(repo_dir)
    runtime = ConfidentialRuntime(repo, "secret-pass")

    with repo.unlocked("secret-pass") as workspace:
        raw_dir = workspace.repo_paths.raw_dir
        wiki_dir = workspace.repo_paths.wiki_dir
        facts_dir = workspace.repo_paths.facts_records_dir
        raw_dir.mkdir(parents=True, exist_ok=True)
        facts_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "finance.pdf").write_bytes(b"pdf")
        (raw_dir / "finance.pdf.md").write_text("# finance", encoding="utf-8")
        (facts_dir / "finance.pdf.jsonl").write_text('{"record_id":"1"}\n', encoding="utf-8")
        (wiki_dir / "budget.md").write_text(
            "---\ntitle: 预算\ntype: analysis\nupdated: 2026-04-16\n---\n\n# 预算\n",
            encoding="utf-8",
        )
        (wiki_dir / "plan.md").write_text(
            "---\ntitle: 计划\ntype: guide\nupdated: 2026-04-16\n---\n\n# 计划\n",
            encoding="utf-8",
        )
        (wiki_dir / "log.md").write_text(
            (
                "---\ntitle: Log\ntype: log\nupdated: 2026-04-16\n---\n\n# Log\n\n"
                "## 2026-04-16 10:00 UTC — Ingested `finance.pdf.md`\n\n"
                "- Created: budget.md\n"
                "- Updated: plan.md\n"
            ),
            encoding="utf-8",
        )
        workspace.save_documents(
            [
                {
                    "filename": "finance.pdf",
                    "processed_filename": "finance.pdf.md",
                    "file_ext": ".pdf",
                    "size_bytes": 3,
                    "size_display": "0.0 KB",
                    "status": "ready",
                    "progress": 100,
                    "progress_message": "文档处理完成",
                    "created_at": "2026-04-16T00:00:00Z",
                    "updated_at": "2026-04-16T00:00:01Z",
                    "last_ingested_at": "2026-04-16T00:00:01Z",
                }
            ]
        )

    mock_qdrant = MagicMock()
    mock_qdrant._qdrant.collection_exists.return_value = True

    with patch("confidential_client.runtime.ConfidentialQdrantService", return_value=mock_qdrant):
        remaining = runtime.delete_document("finance.pdf")

    assert remaining == []
    mock_qdrant.delete_page.assert_any_call(repo.manifest.repo_id, "budget.md")
    mock_qdrant.delete_page.assert_any_call(repo.manifest.repo_id, "plan.md")
    mock_qdrant.delete_page_chunks.assert_any_call(repo.manifest.repo_id, "budget.md")
    mock_qdrant.delete_page_chunks.assert_any_call(repo.manifest.repo_id, "plan.md")
    mock_qdrant.delete_fact_records.assert_called_once_with(repo.manifest.repo_id, "finance.pdf.md")
    mock_qdrant.upsert_page.assert_called_once()
    mock_qdrant.upsert_page_chunks.assert_called_once()

    with repo.unlocked("secret-pass") as workspace:
        assert workspace.load_documents() == []
        assert not (workspace.repo_paths.raw_dir / "finance.pdf").exists()
        assert not (workspace.repo_paths.raw_dir / "finance.pdf.md").exists()
        assert not (workspace.repo_paths.facts_records_dir / "finance.pdf.jsonl").exists()
        assert not (workspace.repo_paths.wiki_dir / "budget.md").exists()
        assert not (workspace.repo_paths.wiki_dir / "plan.md").exists()
        assert "finance.pdf.md" not in (workspace.repo_paths.wiki_dir / "log.md").read_text(encoding="utf-8")
        assert "budget.md" not in (workspace.repo_paths.wiki_dir / "index.md").read_text(encoding="utf-8")
        overview_content = (workspace.repo_paths.wiki_dir / "overview.md").read_text(encoding="utf-8")
        assert "当前共 0 个知识页面" in overview_content


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
    progress_events = []

    with patch("confidential_client.runtime.LLMClient", return_value=mock_llm), \
         patch("confidential_client.runtime.ConfidentialQdrantService", return_value=mock_qdrant):
        runtime = ConfidentialRuntime(ConfidentialRepository(repo_dir), "secret-pass")
        events = runtime.ingest_file(source_path, on_event=progress_events.append)
        result = runtime.query("预算情况如何？")
        history = runtime.load_history()
        documents = runtime.list_documents()

    assert any(event.get("phase") == "done" for event in events)
    assert any(event.get("status") == "ready" for event in progress_events)
    assert result.answer == "这是客户端本地生成的回答。"
    assert history[-1]["question"] == "预算情况如何？"
    assert documents[0]["filename"] == "finance.md"
    assert documents[0]["status"] == "ready"
    assert documents[0]["progress"] == 100
    assert documents[0]["size_display"].endswith("KB")
    assert mock_qdrant.upsert_page.called


def test_confidential_runtime_converts_doc_before_mineru(tmp_path):
    repo_dir = tmp_path / "repo"
    source_path = tmp_path / "AE650.doc"
    source_path.write_bytes(b"legacy-doc")

    ConfidentialRepository.create(
        repo_dir,
        name="Finance",
        slug="finance",
        passphrase="secret-pass",
        services=_services(),
    )
    runtime = ConfidentialRuntime(ConfidentialRepository(repo_dir), "secret-pass")

    parse_calls = []

    def fake_run(cmd, check, capture_output, text):
        output_path = Path(cmd[4])
        output_path.write_bytes(b"converted-docx")
        return MagicMock()

    def fake_parse(path):
        parse_calls.append(path)
        return {"md_content": "# Converted"}

    def textutil_which(name):
        return "/usr/bin/textutil" if name == "textutil" else None

    mock_mineru = MagicMock()
    mock_mineru.parse_file.side_effect = fake_parse

    with patch("confidential_client.runtime.shutil.which", side_effect=textutil_which), \
         patch("confidential_client.runtime.subprocess.run", side_effect=fake_run), \
         patch("confidential_client.runtime.MineruClient", return_value=mock_mineru):
        with runtime._repository.unlocked("secret-pass") as workspace:
            md_name = runtime._stage_source(workspace, source_path)

    assert md_name == "AE650.doc.md"
    assert parse_calls and parse_calls[0].endswith("AE650.docx")
    assert (repo_dir / "manifest.json").exists()


def test_confidential_runtime_doc_without_converter_raises_helpful_error(tmp_path):
    repo_dir = tmp_path / "repo"
    source_path = tmp_path / "AE650.doc"
    source_path.write_bytes(b"legacy-doc")

    ConfidentialRepository.create(
        repo_dir,
        name="Finance",
        slug="finance",
        passphrase="secret-pass",
        services=_services(),
    )
    runtime = ConfidentialRuntime(ConfidentialRepository(repo_dir), "secret-pass")

    with patch("confidential_client.runtime.shutil.which", return_value=None):
        with runtime._repository.unlocked("secret-pass") as workspace:
            with pytest.raises(Exception, match="无法直接解析 \\.doc 文件"):
                runtime._stage_source(workspace, source_path)


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
