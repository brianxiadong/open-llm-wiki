"""Contract & regression tests for bugs that escaped unit tests.

Each test targets a specific production incident to prevent recurrence.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── MinerU API contract ──────────────────────────────────────────────────────


def _httpx_client_context(mock_http):
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=mock_http)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def test_mineru_field_name_is_files_not_file():
    """MinerU API requires field name 'files' (plural). Using 'file' causes 422."""
    from mineru_client import MineruClient

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"results": {"test": {"md_content": "# ok"}}}
    mock_resp.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.post.return_value = mock_resp

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(b"%PDF-1.4 fake")
        path = tmp.name

    try:
        with patch("mineru_client.httpx.Client", return_value=_httpx_client_context(mock_http)):
            client = MineruClient("http://mineru:8000")
            client.parse_file(path)
    finally:
        Path(path).unlink(missing_ok=True)

    call_args = mock_http.post.call_args
    files_arg = call_args[1]["files"]
    assert isinstance(files_arg, list), "files should be a list of tuples"
    field_name = files_arg[0][0]
    assert field_name == "files", f"Field name must be 'files', got '{field_name}'"


def test_mineru_return_md_as_form_data():
    """return_md must be sent as form data, not query params."""
    from mineru_client import MineruClient

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"results": {"test": {"md_content": "# ok"}}}
    mock_resp.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.post.return_value = mock_resp

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(b"fake")
        path = tmp.name

    try:
        with patch("mineru_client.httpx.Client", return_value=_httpx_client_context(mock_http)):
            client = MineruClient("http://mineru:8000")
            client.parse_file(path)
    finally:
        Path(path).unlink(missing_ok=True)

    call_kw = mock_http.post.call_args[1]
    assert "params" not in call_kw, "return_md should NOT be in params"
    assert call_kw["data"] == {"return_md": "true"}, "return_md should be in data"


def test_mineru_nested_results_extraction():
    """MinerU returns {results: {filename: {md_content: ...}}} — must extract."""
    from mineru_client import MineruClient

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "task_id": "abc",
        "results": {"my-doc": {"md_content": "# Extracted Content"}}
    }
    mock_resp.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.post.return_value = mock_resp

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(b"fake")
        path = tmp.name

    try:
        with patch("mineru_client.httpx.Client", return_value=_httpx_client_context(mock_http)):
            client = MineruClient("http://mineru:8000")
            result = client.parse_file(path)
    finally:
        Path(path).unlink(missing_ok=True)

    assert result["md_content"] == "# Extracted Content"


# ── LLM output format cleaning ───────────────────────────────────────────────


def test_clean_llm_markdown_strips_yaml_fence():
    """LLM sometimes wraps frontmatter in ```yaml ... ``` — must be stripped."""
    from wiki_engine import _clean_llm_markdown

    raw = "```yaml\n---\ntitle: Test\ntype: concept\n---\n\n# Test Page\n\nContent here.\n```"
    cleaned = _clean_llm_markdown(raw)
    assert cleaned.startswith("---"), f"Should start with ---, got: {cleaned[:20]}"
    assert "```yaml" not in cleaned
    assert not cleaned.rstrip().endswith("```")


def test_clean_llm_markdown_strips_markdown_fence():
    """LLM sometimes wraps in ```markdown ... ```."""
    from wiki_engine import _clean_llm_markdown

    raw = "```markdown\n---\ntitle: Test\n---\n\n# Hello\n```"
    cleaned = _clean_llm_markdown(raw)
    assert cleaned.startswith("---")
    assert "```markdown" not in cleaned


def test_clean_llm_markdown_preserves_clean_content():
    """Clean content without fences should pass through unchanged."""
    from wiki_engine import _clean_llm_markdown

    raw = "---\ntitle: Test\n---\n\n# Hello"
    cleaned = _clean_llm_markdown(raw)
    assert cleaned == raw


def test_render_markdown_parses_fenced_frontmatter():
    """render_markdown must handle frontmatter wrapped in ```yaml."""
    from utils import render_markdown

    text = "```yaml\n---\ntitle: My Page\ntype: concept\n---\n\n# My Page\n\nContent.\n```"
    fm, html = render_markdown(text)
    assert fm.get("title") == "My Page"
    assert fm.get("type") == "concept"
    assert "My Page" in html


# ── HTML structure: no nested forms ──────────────────────────────────────────


def test_source_list_no_nested_forms(app, auth_client, sample_repo):
    """Ingest/delete buttons must NOT be inside another <form> tag.
    HTML does not support nested forms — inner forms are silently ignored."""
    client, repo_info = sample_repo
    resp = client.get(f"/{repo_info['username']}/{repo_info['slug']}/sources")
    html = resp.data.decode("utf-8")

    forms = list(re.finditer(r"<form\b", html, re.IGNORECASE))
    form_ends = list(re.finditer(r"</form>", html, re.IGNORECASE))

    depth = 0
    events = []
    for m in sorted(forms + form_ends, key=lambda x: x.start()):
        if "<form" in m.group().lower():
            depth += 1
            events.append(("open", depth, m.start()))
        else:
            events.append(("close", depth, m.start()))
            depth -= 1

    max_depth = max((e[1] for e in events), default=0)
    assert max_depth <= 1, (
        f"Found nested forms (max depth={max_depth}). "
        "HTML does not support nested <form> tags."
    )


# ── Task queue: upload auto-creates task ─────────────────────────────────────


def test_task_queued_on_upload(app, auth_client, sample_repo):
    """Uploading a file must auto-create a queued ingest task."""
    import io
    client, repo_info = sample_repo

    data = {
        "file": (io.BytesIO(b"# Test doc\n\nContent."), "auto-ingest.md"),
    }
    resp = client.post(
        f"/{repo_info['username']}/{repo_info['slug']}/sources/upload",
        data=data,
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        from models import Task
        task = Task.query.filter_by(
            repo_id=repo_info["id"],
            type="ingest",
            input_data="auto-ingest.md",
        ).first()
        assert task is not None, "Upload should auto-create ingest task"
        assert task.status == "queued", f"Task should be queued, got {task.status}"


def test_task_queue_page_accessible(app, auth_client, sample_repo):
    """Task queue page should be accessible."""
    client, repo_info = sample_repo
    resp = client.get(f"/{repo_info['username']}/{repo_info['slug']}/tasks")
    assert resp.status_code == 200
    assert "任务队列" in resp.data.decode("utf-8")


# ── New evidence/confidence JSON schema ──────────────────────────────────


def test_query_api_response_has_confidence_fields(app, sample_repo):
    """query API must return confidence, wiki_evidence, chunk_evidence fields."""
    from unittest.mock import patch
    client, repo_info = sample_repo
    slug = repo_info["slug"]
    fake = {
        "markdown": "answer", "confidence": {"level": "low", "score": 0.1, "reasons": []},
        "wiki_evidence": [], "chunk_evidence": [], "evidence_summary": "",
        "referenced_pages": [], "wiki_sources": [], "qdrant_sources": [],
    }
    with patch.object(app.wiki_engine, "query_with_evidence", return_value=fake):
        resp = client.post(f"/alice/{slug}/query", json={"q": "test"})
    data = resp.get_json()
    for field in ("confidence", "wiki_evidence", "chunk_evidence", "evidence_summary",
                  "html", "markdown", "wiki_sources", "qdrant_sources"):
        assert field in data, f"Missing field: {field}"
    assert isinstance(data["confidence"], dict)
    assert "level" in data["confidence"]
    assert "score" in data["confidence"]
    assert "reasons" in data["confidence"]


def test_query_stream_done_has_evidence_fields(app, sample_repo):
    """SSE done event must include confidence, wiki_evidence, chunk_evidence fields."""
    from unittest.mock import patch
    client, repo_info = sample_repo
    slug = repo_info["slug"]

    def fake_stream(repo, username, question):
        yield {"event": "done", "data": {
            "answer": "Hi", "markdown": "Hi",
            "confidence": {"level": "low", "score": 0.1, "reasons": []},
            "wiki_evidence": [], "chunk_evidence": [],
            "evidence_summary": "暂无证据。",
            "wiki_sources": [], "qdrant_sources": [],
            "referenced_pages": [],
        }}

    with patch.object(app.wiki_engine, "query_stream", side_effect=fake_stream):
        resp = client.get(f"/alice/{slug}/query/stream?q=test")
    body = resp.data.decode()
    assert "confidence" in body
    assert "wiki_evidence" in body
    assert "chunk_evidence" in body
