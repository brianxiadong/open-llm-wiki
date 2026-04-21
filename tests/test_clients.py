"""Unit tests for LLMClient, MineruClient, and QdrantService with mocked I/O."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from openai import OpenAIError

from exceptions import LLMClientError, MineruClientError, QdrantServiceError  # noqa: F401
from llm_client import LLMClient
from mineru_client import MineruClient
from qdrant_service import QdrantService

# --- LLMClient ---


def test_llm_chat_success():
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="hello from model"))]
    mock_response.usage = MagicMock(prompt_tokens=1, completion_tokens=2, total_tokens=3)

    with patch("llm_client.OpenAI") as mock_openai_cls:
        mock_openai_cls.return_value.chat.completions.create.return_value = mock_response
        client = LLMClient(
            api_base="http://localhost/v1",
            api_key="sk-test",
            model="gpt-test",
            max_tokens=256,
        )
        out = client.chat([{"role": "user", "content": "hi"}])

    assert out == "hello from model"
    mock_openai_cls.assert_called_once_with(base_url="http://localhost/v1", api_key="sk-test", timeout=180.0)
    mock_openai_cls.return_value.chat.completions.create.assert_called_once()
    call_kw = mock_openai_cls.return_value.chat.completions.create.call_args.kwargs
    assert call_kw["model"] == "gpt-test"
    assert call_kw["messages"] == [{"role": "user", "content": "hi"}]
    assert call_kw["temperature"] == 0.7
    assert call_kw["max_tokens"] == 256
    assert "response_format" not in call_kw


def test_llm_chat_with_response_format():
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="{}"))]
    mock_response.usage = None

    fmt = {"type": "json_object"}
    with patch("llm_client.OpenAI") as mock_openai_cls:
        mock_openai_cls.return_value.chat.completions.create.return_value = mock_response
        client = LLMClient("http://x", "k", "m", 100)
        client.chat([], response_format=fmt)

    kwargs = mock_openai_cls.return_value.chat.completions.create.call_args.kwargs
    assert kwargs["response_format"] == fmt


def test_llm_chat_api_error():
    with patch("llm_client.OpenAI") as mock_openai_cls:
        mock_openai_cls.return_value.chat.completions.create.side_effect = OpenAIError("boom")
        client = LLMClient("http://x", "k", "m", 100)
        with pytest.raises(LLMClientError) as exc_info:
            client.chat([{"role": "user", "content": "x"}])
        assert "boom" in str(exc_info.value)


def test_llm_chat_json_success():
    with patch("llm_client.OpenAI"), patch.object(
        LLMClient,
        "chat",
        return_value='{"a": 1, "b": "two"}',
    ) as mock_chat:
        client = LLMClient("http://x", "k", "m", 100)
        data = client.chat_json([{"role": "user", "content": "q"}])

    assert data == {"a": 1, "b": "two"}
    mock_chat.assert_called_once()
    ckw = mock_chat.call_args.kwargs
    assert ckw["temperature"] == 0.3
    assert ckw["response_format"] == {"type": "json_object"}


def test_llm_chat_json_parse_error():
    with patch("llm_client.OpenAI"), patch.object(LLMClient, "chat", return_value="not valid json {{{"):
        client = LLMClient("http://x", "k", "m", 100)
        assert client.chat_json([]) == {}


def test_llm_health_check_success():
    with patch("llm_client.OpenAI") as mock_openai_cls:
        client = LLMClient("http://x", "k", "m", 100)
        ok, message = client.health_check()

    assert ok is True
    assert message == "ok"
    mock_openai_cls.return_value.models.list.assert_called_once_with()


def test_llm_health_check_failure():
    with patch("llm_client.OpenAI") as mock_openai_cls:
        mock_openai_cls.return_value.models.list.side_effect = OpenAIError("llm down")
        client = LLMClient("http://x", "k", "m", 100)
        ok, message = client.health_check()

    assert ok is False
    assert "llm down" in message


# --- MineruClient helpers ---


def _httpx_client_context(mock_instance: MagicMock) -> MagicMock:
    """Build a MagicMock that works as `with httpx.Client(...) as client:`."""
    ctx = MagicMock()
    ctx.__enter__.return_value = mock_instance
    ctx.__exit__.return_value = None
    return ctx


# --- MineruClient ---


def test_mineru_parse_file_success():
    body = {"results": {"test": {"md_content": "# parsed"}}}
    mock_resp = MagicMock()
    mock_resp.json.return_value = body
    mock_resp.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.post.return_value = mock_resp
    httpx_ctx = _httpx_client_context(mock_http)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(b"%PDF-1.4 fake")
        path = tmp.name

    try:
        with patch("mineru_client.httpx.Client", return_value=httpx_ctx):
            client = MineruClient("http://mineru:8000")
            out = client.parse_file(path)
    finally:
        Path(path).unlink(missing_ok=True)

    assert out["md_content"] == "# parsed"
    mock_http.post.assert_called_once()
    call_kw = mock_http.post.call_args
    assert "/file_parse" in call_kw[0][0]
    assert call_kw[1]["data"] == {"return_md": "true"}


def test_mineru_parse_file_not_found():
    client = MineruClient("http://mineru:8000")
    with pytest.raises(MineruClientError) as e:
        client.parse_file("/nonexistent/path/does-not-exist-12345.bin")
    assert "File not found" in str(e.value)


def test_mineru_parse_file_timeout():
    mock_http = MagicMock()
    mock_http.post.side_effect = httpx.TimeoutException("timeout", request=MagicMock())
    httpx_ctx = _httpx_client_context(mock_http)

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(b"x")
        path = tmp.name

    try:
        with patch("mineru_client.httpx.Client", return_value=httpx_ctx):
            client = MineruClient("http://mineru:8000")
            with pytest.raises(MineruClientError) as exc:
                client.parse_file(path)
            assert "timed out" in str(exc.value).lower() or "Request timed out" in str(exc.value)
    finally:
        Path(path).unlink(missing_ok=True)


def test_mineru_parse_async_success():
    task_body = {"task_id": "task-abc-123"}
    mock_resp = MagicMock()
    mock_resp.json.return_value = task_body
    mock_resp.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.post.return_value = mock_resp
    httpx_ctx = _httpx_client_context(mock_http)

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(b"data")
        path = tmp.name

    try:
        with patch("mineru_client.httpx.Client", return_value=httpx_ctx):
            client = MineruClient("http://mineru:8000")
            tid = client.parse_file_async(path)
    finally:
        Path(path).unlink(missing_ok=True)

    assert tid == "task-abc-123"
    assert "/tasks" in mock_http.post.call_args[0][0]


def test_mineru_get_task_status():
    status = {"state": "running", "progress": 0.5}
    mock_resp = MagicMock()
    mock_resp.json.return_value = status
    mock_resp.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.get.return_value = mock_resp
    httpx_ctx = _httpx_client_context(mock_http)

    with patch("mineru_client.httpx.Client", return_value=httpx_ctx):
        client = MineruClient("http://mineru:8000")
        out = client.get_task_status("tid-1")

    assert out == status
    assert "/tasks/tid-1" in mock_http.get.call_args[0][0]


def test_mineru_get_task_result():
    result = {"result_md": "hello"}
    mock_resp = MagicMock()
    mock_resp.json.return_value = result
    mock_resp.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.get.return_value = mock_resp
    httpx_ctx = _httpx_client_context(mock_http)

    with patch("mineru_client.httpx.Client", return_value=httpx_ctx):
        client = MineruClient("http://mineru:8000")
        out = client.get_task_result("tid-2")

    assert out == result
    assert "/tasks/tid-2/result" in mock_http.get.call_args[0][0]


def test_mineru_health_check_ok():
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    mock_http = MagicMock()
    mock_http.get.return_value = mock_resp
    httpx_ctx = _httpx_client_context(mock_http)

    with patch("mineru_client.httpx.Client", return_value=httpx_ctx):
        client = MineruClient("http://mineru:8000")
        assert client.health_check() is True


def test_mineru_health_check_fail():
    mock_http = MagicMock()
    mock_http.get.side_effect = httpx.ConnectError("refused", request=MagicMock())
    httpx_ctx = _httpx_client_context(mock_http)

    with patch("mineru_client.httpx.Client", return_value=httpx_ctx):
        client = MineruClient("http://mineru:8000")
        assert client.health_check() is False


# --- QdrantService ---


def test_stable_point_id():
    a = QdrantService._stable_point_id(1, "a.md")
    b = QdrantService._stable_point_id(1, "a.md")
    c = QdrantService._stable_point_id(1, "b.md")
    assert a == b
    assert a != c


def test_embed():
    emb = [0.1, 0.2, 0.3]
    mock_resp = MagicMock()
    mock_resp.data = [MagicMock(embedding=emb)]

    with patch("qdrant_service.QdrantClient"), patch("qdrant_service.OpenAI") as mock_oa:
        mock_oa.return_value.embeddings.create.return_value = mock_resp
        svc = QdrantService(
            qdrant_url="http://localhost:6333",
            embedding_api_base="http://embed",
            embedding_api_key="k",
            embedding_model="m",
            embedding_dimensions=1024,
        )
        out = svc._embed("hello")

    assert out == emb
    mock_oa.return_value.embeddings.create.assert_called_once_with(model="m", input="hello")


def test_ensure_collection_creates():
    with patch("qdrant_service.QdrantClient") as mock_qc, patch("qdrant_service.OpenAI"):
        mock_q = mock_qc.return_value
        mock_q.collection_exists.return_value = False
        svc = QdrantService("http://q", "http://e", "k", "em", 1024)
        svc.ensure_collection(7)

    mock_q.collection_exists.assert_called_once_with(collection_name="repo_7")
    mock_q.create_collection.assert_called_once()
    cc_kw = mock_q.create_collection.call_args.kwargs
    assert cc_kw["collection_name"] == "repo_7"


def test_ensure_collection_exists():
    with patch("qdrant_service.QdrantClient") as mock_qc, patch("qdrant_service.OpenAI"):
        mock_q = mock_qc.return_value
        mock_q.collection_exists.return_value = True
        svc = QdrantService("http://q", "http://e", "k", "em", 1024)
        svc.ensure_collection(7)

    mock_q.create_collection.assert_not_called()


def test_upsert_page():
    vector = [0.5] * 8
    mock_emb_resp = MagicMock()
    mock_emb_resp.data = [MagicMock(embedding=vector)]

    with patch("qdrant_service.QdrantClient") as mock_qc, patch("qdrant_service.OpenAI") as mock_oa:
        mock_q = mock_qc.return_value
        mock_q.collection_exists.return_value = True
        mock_oa.return_value.embeddings.create.return_value = mock_emb_resp

        svc = QdrantService("http://q", "http://e", "k", "em", embedding_dimensions=8)
        svc.upsert_page(
            repo_id=3,
            filename="notes.md",
            title="Notes",
            page_type="doc",
            content="body text",
        )

    mock_q.upsert.assert_called_once()
    u_kw = mock_q.upsert.call_args.kwargs
    assert u_kw["collection_name"] == "repo_3"
    points = u_kw["points"]
    assert len(points) == 1
    pt = points[0]
    assert pt.id == QdrantService._stable_point_id(3, "notes.md")
    assert list(pt.vector) == vector
    assert pt.payload == {
        "repo_id": 3,
        "filename": "notes.md",
        "title": "Notes",
        "type": "doc",
        "content": "body text",
    }


def test_upsert_fact_records_batches_large_payloads():
    with patch("qdrant_service.QdrantClient") as mock_qc, patch("qdrant_service.OpenAI") as mock_oa:
        mock_q = mock_qc.return_value
        mock_q.collection_exists.return_value = True

        def fake_embed(*, model, input):
            texts = input if isinstance(input, list) else [input]
            response = MagicMock()
            response.data = [
                MagicMock(index=idx, embedding=[float(idx + 1)] * 8)
                for idx, _ in enumerate(texts)
            ]
            response.usage = MagicMock(total_tokens=len(texts))
            return response

        mock_oa.return_value.embeddings.create.side_effect = fake_embed

        svc = QdrantService("http://q", "http://e", "k", "em", embedding_dimensions=8)
        records = [
            {
                "record_id": f"row-{idx}",
                "source_file": "250411.xlsx",
                "source_markdown_filename": "250411.md",
                "sheet": "Sheet1",
                "row_index": idx,
                "fields": {"id": idx},
                "fact_text": f"row {idx}",
            }
            for idx in range(QdrantService.UPSERT_BATCH_SIZE * 2 + 17)
        ]

        svc.upsert_fact_records(repo_id=3, source_filename="250411.md", records=records)

    expected_upserts = (
        len(records) + QdrantService.EMBEDDING_BATCH_SIZE - 1
    ) // QdrantService.EMBEDDING_BATCH_SIZE
    assert mock_q.upsert.call_count == expected_upserts
    total_points = sum(len(call.kwargs["points"]) for call in mock_q.upsert.call_args_list)
    assert total_points == len(records)
    assert all(
        len(call.kwargs["points"]) <= QdrantService.UPSERT_BATCH_SIZE
        for call in mock_q.upsert.call_args_list
    )
    assert mock_oa.return_value.embeddings.create.call_count < len(records)


def test_search_with_results():
    mock_hit = MagicMock()
    mock_hit.payload = {"filename": "f.md", "title": "T"}
    mock_hit.score = 0.91

    mock_emb_resp = MagicMock()
    mock_emb_resp.data = [MagicMock(embedding=[0.1, 0.2])]

    with patch("qdrant_service.QdrantClient") as mock_qc, patch("qdrant_service.OpenAI") as mock_oa:
        mock_q = mock_qc.return_value
        mock_q.collection_exists.return_value = True
        mock_q.query_points.return_value = MagicMock(points=[mock_hit])
        mock_oa.return_value.embeddings.create.return_value = mock_emb_resp

        svc = QdrantService("http://q", "http://e", "k", "em", embedding_dimensions=2)
        rows = svc.search(9, "query text", limit=5)

    mock_q.query_points.assert_called_once()
    sk = mock_q.query_points.call_args.kwargs
    assert sk["collection_name"] == "repo_9"
    assert sk["limit"] == 5
    assert sk["query"] == [0.1, 0.2]
    assert rows == [{"filename": "f.md", "title": "T", "score": 0.91}]


def test_search_no_collection():
    with patch("qdrant_service.QdrantClient") as mock_qc, patch("qdrant_service.OpenAI"):
        mock_q = mock_qc.return_value
        mock_q.collection_exists.return_value = False
        svc = QdrantService("http://q", "http://e", "k", "em", 1024)
        assert svc.search(1, "q") == []
    mock_q.query_points.assert_not_called()


def test_delete_collection():
    with patch("qdrant_service.QdrantClient") as mock_qc, patch("qdrant_service.OpenAI"):
        mock_q = mock_qc.return_value
        svc = QdrantService("http://q", "http://e", "k", "em", 1024)
        svc.delete_collection(42)

    mock_q.delete_collection.assert_called_once_with(collection_name="repo_42")
