from __future__ import annotations

import io
import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from confidential_client.cli import cli
from confidential_client.controller import ConfidentialClientController
from confidential_client.gui import (
    _display_filename,
    _format_evidence_html,
    _format_evidence_text,
    _format_confidence,
    _format_document_detail,
    _format_timestamp,
    _IngestTaskRegistry,
    _translate_ingest_message,
    create_client_web_app,
)
from confidential_client.manager import ClientWorkspaceManager, default_services_from_server_config
from confidential_client.version import CLIENT_NAME
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


def _write_services_file(path: Path) -> None:
    path.write_text(json.dumps(_services().to_dict(), ensure_ascii=False), encoding="utf-8")


def _client_gui_script() -> str:
    text = Path("confidential_client/gui.py").read_text(encoding="utf-8")
    marker = "{% block scripts %}\n<script>\n(function () {"
    start = text.index(marker) + len("{% block scripts %}\n<script>\n")
    end = text.index("\n})();\n</script>", start) + len("\n})();")
    return text[start:end]


def _run_client_js_scenario(scenario_body: str) -> None:
    node_script = f"""
const clientScript = {json.dumps(_client_gui_script())};
const elements = new Map();
function defineTrackedText(el, key) {{
  let value = '';
  Object.defineProperty(el, key, {{
    get() {{ return value; }},
    set(next) {{
      value = String(next);
      if (key === 'innerHTML') {{
        el.children = [];
      }}
    }},
  }});
}}
function makeEl(id='') {{
  const el = {{
    id,
    hidden: false,
    value: '',
    style: {{}},
    dataset: {{}},
    files: [],
    children: [],
    open: false,
    className: '',
    listeners: {{}},
    appendChild(child) {{ this.children.push(child); return child; }},
    prepend(child) {{ this.children.unshift(child); return child; }},
    remove() {{}},
    addEventListener(type, cb) {{
      this.listeners[type] = this.listeners[type] || [];
      this.listeners[type].push(cb);
    }},
    dispatch(type, evt={{}}) {{
      const handlers = this.listeners[type] || [];
      handlers.forEach((handler) => handler(evt));
    }},
    setAttribute(name, value) {{ this[name] = value; }},
    removeAttribute(name) {{ delete this[name]; }},
    showModal() {{ this.open = true; }},
    close() {{ this.open = false; }},
    querySelectorAll() {{ return []; }},
    classList: {{ add() {{}}, remove() {{}}, toggle() {{}} }},
    click() {{ this.dispatch('click', {{ preventDefault() {{}}, target: this }}); }},
  }};
  defineTrackedText(el, 'innerHTML');
  defineTrackedText(el, 'textContent');
  return el;
}}
const document = {{
  getElementById(id) {{
    if (!elements.has(id)) elements.set(id, makeEl(id));
    return elements.get(id);
  }},
  querySelectorAll(selector) {{
    if (selector === '.client-tab-btn' || selector === '.client-tab-panel' || selector === '.doc-open') {{
      return [];
    }}
    return [];
  }},
  createElement(tag) {{
    return makeEl(tag);
  }},
}};
const fetchQueues = new Map();
function queueFetch(url, responses) {{
  fetchQueues.set(url, responses.slice());
}}
function fetch(url, options) {{
  if (!fetchQueues.has(url) || !fetchQueues.get(url).length) {{
    return Promise.reject(new Error('unexpected fetch ' + url));
  }}
  const payload = fetchQueues.get(url).shift();
  return Promise.resolve({{
    ok: payload.ok !== false,
    json: () => Promise.resolve(payload.body),
  }});
}}
async function flush(iterations = 4) {{
  for (let index = 0; index < iterations; index += 1) {{
    await new Promise((resolve) => setImmediate(resolve));
  }}
}}
function expect(condition, message) {{
  if (!condition) {{
    throw new Error(message);
  }}
}}
global.window = {{
  CLIENT_BOOTSTRAP: {{
    repos: [{{ repo_uuid: 'repo-1', name: '财务库', slug: 'finance' }}],
    active_repo_uuid: 'repo-1',
    tasks: [],
  }},
  lucide: {{ createIcons() {{}} }},
  setTimeout(fn) {{ return 1; }},
  clearInterval() {{}},
  setInterval() {{ return 1; }},
}};
global.document = document;
global.fetch = fetch;
global.Promise = Promise;
(async () => {{
  eval(clientScript);
  {scenario_body}
}})().catch((error) => {{
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
}});
"""
    subprocess.run(["node", "-e", node_script], cwd=Path.cwd(), check=True, text=True, capture_output=True)


def _assert_rendered_client_script_is_valid(app) -> None:
    html = app.test_client().get("/").data.decode("utf-8")
    start = html.rindex("<script>")
    end = html.rindex("</script>")
    script = html[start + len("<script>"):end]
    check_script = (
        "const fs = require('fs');"
        "const os = require('os');"
        "const path = require('path');"
        f"const script = {json.dumps(script)};"
        "const file = path.join(os.tmpdir(), 'open-llm-wiki-client-rendered.js');"
        "fs.writeFileSync(file, script, 'utf8');"
        "require('child_process').execFileSync('node', ['--check', file], { stdio: 'pipe' });"
    )
    subprocess.run(["node", "-e", check_script], cwd=Path.cwd(), check=True, text=True, capture_output=True)


def test_workspace_manager_create_update_export_import_delete(tmp_path):
    manager = ClientWorkspaceManager(tmp_path / "client-home")

    summary = manager.create_repository(
        name="Finance",
        slug="finance",
        passphrase="secret-pass",
        services=_services(),
    )
    repos = manager.list_repositories()
    assert len(repos) == 1
    assert repos[0].repo_uuid == summary.repo_uuid

    services = manager.load_services(summary.repo_uuid, "secret-pass")
    assert services.qdrant_url == "http://fake-qdrant:6333"

    updated = ConfidentialServices.from_dict(
        {**services.to_dict(), "mineru_api_url": "http://changed-mineru:8000"}
    )
    manager.update_services(summary.repo_uuid, passphrase="secret-pass", services=updated)
    assert manager.load_services(summary.repo_uuid, "secret-pass").mineru_api_url == "http://changed-mineru:8000"

    bundle_path = manager.export_repository(summary.repo_uuid)
    assert bundle_path.exists()

    manager.delete_repository(summary.repo_uuid)
    imported = manager.import_repository(bundle_path)
    assert imported.repo_uuid == summary.repo_uuid
    remaining = manager.list_repositories()
    assert len(remaining) == 1
    assert remaining[0].repo_uuid == imported.repo_uuid


def test_controller_query_builds_history(tmp_path):
    manager = ClientWorkspaceManager(tmp_path / "client-home")
    summary = manager.create_repository(
        name="Finance",
        slug="finance",
        passphrase="secret-pass",
        services=_services(),
    )
    controller = ConfidentialClientController(manager)

    runtime = MagicMock()
    runtime.load_history.return_value = [
        {"question": "Q1", "answer": "A1"},
        {"question": "Q2", "answer": "A2"},
    ]
    runtime.query.return_value = MagicMock(answer="A3", confidence={"level": "high"})

    with patch("confidential_client.controller.ConfidentialRuntime", return_value=runtime):
        controller.query(summary.repo_uuid, "secret-pass", "Q3")

    runtime.query.assert_called_once()
    history = runtime.query.call_args.kwargs["history"]
    assert history == [
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "Q2"},
        {"role": "assistant", "content": "A2"},
    ]


def test_health_checks_report_service_status():
    with patch("confidential_client.health.httpx.get") as mock_get, \
         patch("confidential_client.health.OpenAI") as mock_openai:
        mock_get.return_value = MagicMock(status_code=200)
        embed_resp = MagicMock()
        embed_resp.data = [MagicMock(embedding=[0.1, 0.2])]
        llm_resp = MagicMock()
        llm_resp.choices = [MagicMock(message=MagicMock(content="pong"))]
        client = mock_openai.return_value
        client.embeddings.create.return_value = embed_resp
        client.chat.completions.create.return_value = llm_resp

        controller = ConfidentialClientController(ClientWorkspaceManager(Path("/tmp") / "client-health"))
        result = controller.check_services(_services())

    assert result["qdrant"]["ok"] is True
    assert result["mineru"]["ok"] is True
    assert result["embedding"]["ok"] is True
    assert result["llm"]["ok"] is True


def test_controller_client_settings_round_trip(tmp_path):
    manager = ClientWorkspaceManager(tmp_path / "client-home")
    controller = ConfidentialClientController(manager)

    saved = controller.save_client_settings(
        {
            "update_manifest_url": "https://updates.example.com/appcast.json",
            "update_channel": "beta",
        }
    )
    loaded = controller.load_client_settings()

    assert saved["update_channel"] == "beta"
    assert loaded["update_manifest_url"] == "https://updates.example.com/appcast.json"


def test_manager_load_default_services_from_local_file(tmp_path):
    manager = ClientWorkspaceManager(tmp_path / "client-home")
    config_path = tmp_path / "packaging" / "client" / "default-services.local.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(_services().to_dict(), ensure_ascii=False), encoding="utf-8")

    with patch.object(manager, "_default_services_candidates", return_value=[config_path]):
        loaded = manager.load_default_services()

    assert loaded.qdrant_url == "http://fake-qdrant:6333"


def test_default_services_from_server_config_uses_runtime_config():
    fake_config = SimpleNamespace(
        LLM_API_BASE="http://runtime-llm/v1",
        LLM_API_KEY="runtime-key",
        LLM_MODEL="runtime-model",
        LLM_MAX_TOKENS=8192,
        EMBEDDING_API_BASE="http://runtime-embed/v1",
        EMBEDDING_API_KEY="embed-key",
        EMBEDDING_MODEL="runtime-embed-model",
        EMBEDDING_DIMENSIONS=2048,
        QDRANT_URL="http://runtime-qdrant:6333",
        MINERU_API_URL="http://runtime-mineru:8000",
    )

    with patch("confidential_client.manager.Config", fake_config):
        loaded = default_services_from_server_config()

    assert loaded.to_dict() == {
        "llm_api_base": "http://runtime-llm/v1",
        "llm_api_key": "runtime-key",
        "llm_model": "runtime-model",
        "llm_max_tokens": 8192,
        "embedding_api_base": "http://runtime-embed/v1",
        "embedding_api_key": "embed-key",
        "embedding_model": "runtime-embed-model",
        "embedding_dimensions": 2048,
        "qdrant_url": "http://runtime-qdrant:6333",
        "mineru_api_url": "http://runtime-mineru:8000",
    }


def test_gui_text_helpers_are_chinese_friendly():
    assert CLIENT_NAME == "Open LLM Wiki 机密客户端"
    assert "已读取原始文档" in _translate_ingest_message("Read source: finance.md (128 chars)")
    assert "级别：高" in _format_confidence({"level": "high", "score": 0.91, "reasons": ["证据充分"]})
    assert _display_filename("b62852bb1c57463aa16773b893e9633f_AE650.doc") == "AE650.doc"
    assert _display_filename("finance_report.md") == "finance_report.md"
    assert _format_timestamp("2026-04-16T00:00:00.123456+00:00").startswith("2026-04-16 ")
    detail = _format_document_detail(
        {
            "filename": "b62852bb1c57463aa16773b893e9633f_finance.md",
            "file_ext": ".md",
            "status": "ready",
            "progress": 100,
            "progress_message": "文档处理完成",
            "processed_filename": "finance.md",
            "updated_at": "2026-04-16T00:00:00Z",
            "last_ingested_at": "2026-04-16T00:00:00Z",
        }
    )
    assert "文档：finance.md" in detail
    assert "状态：已完成" in detail


def test_evidence_helpers_strip_frontmatter_and_render_sections():
    result = SimpleNamespace(
        confidence={"level": "high", "score": 0.95, "reasons": ["命中 8 个 Wiki 页面"]},
        wiki_evidence=[
            {
                "title": "AE650 产品概览",
                "type": "overview",
                "reason": "结构化路径命中",
                "url": "/ae650-overview",
            }
        ],
        chunk_evidence=[
            {
                "title": "AE350、AE380 与 AE650 对比",
                "heading": "comparison",
                "score": 0.6986355,
                "snippet": (
                    "---\n"
                    "title: AE350、AE380 与 AE650 对比\n"
                    "type: comparison\n"
                    "---\n"
                    "本文档旨在对比分析 AE350、AE380 与 AE650。"
                ),
            }
        ],
        fact_evidence=[
            {
                "source_markdown_filename": "ae650-specs-summary.pdf",
                "sheet": "Sheet1",
                "row_index": 3,
                "fields": {"型号": "AE650", "分辨率": "4K"},
            }
        ],
        evidence_summary="结构化路径与片段证据共同支持。",
    )

    evidence_text = _format_evidence_text(result)
    evidence_html = _format_evidence_html(result)

    assert "证据摘要：结构化路径与片段证据共同支持。" in evidence_text
    assert "title:" not in evidence_text
    assert "AE350、AE380 与 AE650 对比" in evidence_html
    assert "结构化路径与片段证据共同支持。" in evidence_html
    assert "title:" not in evidence_html


def test_client_web_app_bootstrap_and_create_repo(tmp_path):
    manager = ClientWorkspaceManager(tmp_path / "client-home")
    controller = ConfidentialClientController(manager)
    app = create_client_web_app(controller)
    client = app.test_client()
    _assert_rendered_client_script_is_valid(app)

    home_html = client.get("/").data.decode("utf-8")
    assert "机密知识库客户端" not in home_html
    assert "文档管理" in home_html

    resp = client.get("/api/bootstrap")
    assert resp.status_code == 200
    assert resp.get_json()["repos"] == []

    config_path = tmp_path / "services.local.json"
    config_path.write_text(json.dumps(_services().to_dict(), ensure_ascii=False), encoding="utf-8")

    with patch.object(manager, "_default_services_candidates", return_value=[config_path]):
        created = client.post(
            "/api/repositories",
            json={"name": "财务库", "slug": "finance", "passphrase": "secret-pass"},
        )

    assert created.status_code == 200
    data = created.get_json()
    assert data["repo"]["name"] == "财务库"
    assert len(data["repos"]) == 1

    home_html = client.get("/").data.decode("utf-8")
    assert 'id="tab-documents"' in home_html
    assert 'id="tab-query"' in home_html
    assert 'id="upload-dialog"' in home_html
    assert 'id="open-create-dialog-btn"' in home_html
    assert 'id="create-dialog"' in home_html
    assert 'id="status-filter"' in home_html
    assert 'id="query-loading"' in home_html
    assert 'id="query-result"' in home_html
    assert "财务库 [finance]" in home_html
    assert "新增文档" in home_html


def test_client_web_app_create_plain_repo_without_passphrase(tmp_path):
    manager = ClientWorkspaceManager(tmp_path / "client-home")
    controller = ConfidentialClientController(manager)
    app = create_client_web_app(controller)
    client = app.test_client()

    config_path = tmp_path / "services.local.json"
    config_path.write_text(json.dumps(_services().to_dict(), ensure_ascii=False), encoding="utf-8")

    with patch.object(manager, "_default_services_candidates", return_value=[config_path]):
        created = client.post(
            "/api/repositories",
            json={"name": "便捷库", "slug": "easy", "passphrase": "", "storage_mode": "plain"},
        )

    assert created.status_code == 200
    data = created.get_json()
    assert data["repo"]["name"] == "便捷库"
    assert data["repo"]["storage_mode"] == "plain"
    assert data["repo"]["requires_passphrase"] is False

    documents_resp = client.post(
        f"/api/repositories/{data['repo']['repo_uuid']}/documents",
        json={},
    )
    assert documents_resp.status_code == 200
    assert documents_resp.get_json()["documents"] == []


def test_client_js_load_repo_keeps_buttons_active():
    _run_client_js_scenario(
        """
queueFetch('/api/repositories/repo-1/documents', [
  {
    body: {
      documents: [
        {
          filename: 'finance.md',
          file_ext: '.md',
          status: 'ready',
          progress: 100,
          progress_message: '文档处理完成',
          processed_filename: 'finance.md',
          updated_at: '2026-04-16T00:00:00Z',
          last_ingested_at: '2026-04-16T00:00:00Z',
          detail: 'detail',
        },
      ],
      tasks: [],
    },
  },
]);
document.getElementById('repo-passphrase').value = 'secret-pass';
document.getElementById('open-repo-btn').click();
await flush();
expect(document.getElementById('documents-body').children.length === 1, '载入后文档列表应显示 1 行');
document.getElementById('open-create-dialog-btn').click();
expect(document.getElementById('create-dialog').open === true, '载入后创建知识库按钮应仍可打开弹窗');
document.getElementById('close-create-dialog').click();
document.getElementById('pick-file-btn').click();
expect(document.getElementById('upload-dialog').open === true, '载入后新增文档按钮应仍可打开弹窗');
document.getElementById('close-upload-dialog').click();
expect(document.getElementById('active-repo-label').textContent === '当前：财务库 · 加密', '当前仓库摘要应正常保留');
"""
    )


def test_client_js_plain_repo_does_not_require_passphrase():
    _run_client_js_scenario(
        """
queueFetch('/api/bootstrap', [
  {
    body: {
      repos: [{ repo_uuid: 'repo-1', name: '便捷库', slug: 'easy', storage_mode: 'plain', requires_passphrase: false }],
      active_repo_uuid: 'repo-1',
      tasks: [],
    },
  },
]);
queueFetch('/api/repositories/repo-1/documents', [
  {
    body: {
      documents: [
        {
          filename: 'finance.md',
          file_ext: '.md',
          status: 'ready',
          progress: 100,
          progress_message: '文档处理完成',
          processed_filename: 'finance.md',
          updated_at: '2026-04-16T00:00:00Z',
          last_ingested_at: '2026-04-16T00:00:00Z',
          detail: 'detail',
        },
      ],
      tasks: [],
    },
  },
]);
document.getElementById('refresh-bootstrap-btn').click();
await flush();
document.getElementById('open-repo-btn').click();
await flush();
expect(document.getElementById('repo-passphrase').disabled === true, '明文知识库应禁用口令输入框');
expect(document.getElementById('documents-body').children.length === 1, '明文知识库无需口令也应能正常载入');
"""
    )


def test_client_web_app_upload_and_query_routes(tmp_path):
    manager = ClientWorkspaceManager(tmp_path / "client-home")
    summary = manager.create_repository(
        name="Finance",
        slug="finance",
        passphrase="secret-pass",
        services=_services(),
    )
    controller = ConfidentialClientController(manager)
    app = create_client_web_app(controller)
    client = app.test_client()

    with patch.object(controller, "ingest_file") as ingest_file, patch.object(
        controller,
        "list_documents",
        return_value=[
            {
                "filename": "finance.md",
                "file_ext": ".md",
                "status": "ready",
                "progress": 100,
                "progress_message": "文档处理完成",
                "processed_filename": "finance.md",
                "updated_at": "2026-04-16T00:00:00Z",
                "last_ingested_at": "2026-04-16T00:00:00Z",
            }
        ],
    ), patch.object(controller, "query") as query:
        def fake_ingest(repo_uuid, passphrase, source_path, on_event=None):
            if on_event:
                on_event({"status": "processing", "progress": 35, "message": "Analyzing source content"})
                on_event({"status": "ready", "progress": 100, "message": "Ingest complete: 1 created, 0 updated"})
            return []

        ingest_file.side_effect = fake_ingest
        query.return_value = SimpleNamespace(
            answer="# 回答\n\n这是测试回答。",
            confidence={"level": "high", "score": 0.92, "reasons": ["证据充分"]},
            wiki_evidence=[],
            chunk_evidence=[],
            fact_evidence=[],
            evidence_summary="基于测试文档生成。",
        )

        upload_resp = client.post(
            f"/api/repositories/{summary.repo_uuid}/documents/upload",
            data={
                "passphrase": "secret-pass",
                "files": [
                    (io.BytesIO(b"# finance"), "finance.md"),
                    (io.BytesIO(b"# budget"), "budget.md"),
                ],
            },
            content_type="multipart/form-data",
        )

        assert upload_resp.status_code == 200
        task_payload = upload_resp.get_json()
        assert len(task_payload["tasks"]) == 2
        assert {item["filename"] for item in task_payload["tasks"]} == {"finance.md", "budget.md"}
        task_id = task_payload["tasks"][0]["task_id"]

        final_status = client.get(f"/api/tasks/{task_id}")
        assert final_status.status_code == 200
        final_payload = final_status.get_json()
        assert final_payload["status"] in {"queued", "running", "done"}

        documents_resp = client.post(
            f"/api/repositories/{summary.repo_uuid}/documents",
            json={"passphrase": "secret-pass"},
        )
        assert documents_resp.status_code == 200
        assert "tasks" in documents_resp.get_json()

        bootstrap_resp = client.get("/api/bootstrap")
        assert bootstrap_resp.status_code == 200
        assert "tasks" in bootstrap_resp.get_json()

        query_resp = client.post(
            f"/api/repositories/{summary.repo_uuid}/query",
            json={"passphrase": "secret-pass", "question": "财务情况如何？"},
        )

    assert query_resp.status_code == 200
    query_payload = query_resp.get_json()
    assert "测试回答" in query_payload["answer"]
    assert "级别：高" in query_payload["evidence_text"]
    assert "client-evidence-section" in query_payload["evidence_html"]


def test_client_web_app_upload_preserves_unicode_csv_extension(tmp_path):
    manager = ClientWorkspaceManager(tmp_path / "client-home")
    summary = manager.create_repository(
        name="Finance",
        slug="finance",
        passphrase="secret-pass",
        services=_services(),
    )
    controller = ConfidentialClientController(manager)
    app = create_client_web_app(controller)
    client = app.test_client()

    captured: dict[str, object] = {}

    def fake_ingest(repo_uuid, passphrase, source_path, on_event=None):
        captured["repo_uuid"] = repo_uuid
        captured["source_path"] = Path(source_path)
        if on_event:
            on_event({"status": "ready", "progress": 100, "message": "Ingest complete: 1 created, 0 updated"})
        return []

    with patch.object(controller, "ingest_file", side_effect=fake_ingest), patch.object(
        controller,
        "list_documents",
        return_value=[],
    ):
        upload_resp = client.post(
            f"/api/repositories/{summary.repo_uuid}/documents/upload",
            data={
                "passphrase": "secret-pass",
                "files": [(io.BytesIO("地区,收入\n华东,1200\n".encode("utf-8")), "财务数据.csv")],
            },
            content_type="multipart/form-data",
        )

        assert upload_resp.status_code == 200
        task_payload = upload_resp.get_json()
        assert task_payload["tasks"][0]["filename"] == "财务数据.csv"

        import time

        for _ in range(20):
            if "source_path" in captured:
                break
            time.sleep(0.05)

    assert captured["repo_uuid"] == summary.repo_uuid
    assert Path(captured["source_path"]).name == "财务数据.csv"
    assert Path(captured["source_path"]).suffix == ".csv"


def test_client_web_app_delete_document_route(tmp_path):
    manager = ClientWorkspaceManager(tmp_path / "client-home")
    summary = manager.create_repository(
        name="Finance",
        slug="finance",
        passphrase="secret-pass",
        services=_services(),
    )
    controller = ConfidentialClientController(manager)
    app = create_client_web_app(controller)
    client = app.test_client()

    with patch.object(
        controller,
        "delete_document",
        return_value=[
            {
                "filename": "budget.md",
                "processed_filename": "budget.md",
                "file_ext": ".md",
                "size_display": "1.0 KB",
                "status": "ready",
                "progress": 100,
                "progress_message": "文档处理完成",
                "updated_at": "2026-04-16T00:00:02Z",
                "last_ingested_at": "2026-04-16T00:00:02Z",
                "affected_pages": ["budget-page.md"],
            }
        ],
    ) as mock_delete:
        response = client.post(
            f"/api/repositories/{summary.repo_uuid}/documents/delete",
            json={"passphrase": "secret-pass", "filename": "finance.md"},
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["documents"][0]["filename"] == "budget.md"
    assert "关联页面" in payload["documents"][0]["detail"]
    mock_delete.assert_called_once_with(summary.repo_uuid, "secret-pass", "finance.md")


def test_client_js_upload_refresh_and_query_full_flow():
    _run_client_js_scenario(
        """
queueFetch('/api/repositories/repo-1/documents/upload', [
  {
    body: {
      tasks: [
        {
          task_id: 'task-1',
          repo_uuid: 'repo-1',
          filename: 'finance.md',
          file_ext: '.md',
          size_bytes: 1024,
          size_display: '1.0 KB',
          status: 'running',
          progress: 0,
          message: '已接收文档，准备处理',
          documents: [],
          created_at: '2026-04-16T00:00:00Z',
          updated_at: '2026-04-16T00:00:00Z',
        },
        {
          task_id: 'task-2',
          repo_uuid: 'repo-1',
          filename: 'budget.md',
          file_ext: '.md',
          size_bytes: 2048,
          size_display: '2.0 KB',
          status: 'queued',
          progress: 0,
          message: '等待排队处理',
          documents: [],
          created_at: '2026-04-16T00:00:01Z',
          updated_at: '2026-04-16T00:00:01Z',
        },
      ],
    },
  },
]);
queueFetch('/api/tasks/task-1', [
  {
    body: {
      task_id: 'task-1',
      repo_uuid: 'repo-1',
      filename: 'finance.md',
      file_ext: '.md',
      size_bytes: 1024,
      size_display: '1.0 KB',
      status: 'done',
      progress: 100,
      message: '文档处理完成',
      documents: [
        {
          filename: 'finance.md',
          file_ext: '.md',
          status: 'ready',
          progress: 100,
          progress_message: '文档处理完成',
          processed_filename: 'finance.md',
          updated_at: '2026-04-16T00:00:02Z',
          last_ingested_at: '2026-04-16T00:00:02Z',
          detail: 'finance detail',
        },
      ],
      created_at: '2026-04-16T00:00:00Z',
      updated_at: '2026-04-16T00:00:02Z',
    },
  },
]);
queueFetch('/api/tasks/task-2', [
  {
    body: {
      task_id: 'task-2',
      repo_uuid: 'repo-1',
      filename: 'budget.md',
      file_ext: '.md',
      size_bytes: 2048,
      size_display: '2.0 KB',
      status: 'queued',
      progress: 0,
      message: '等待排队处理',
      documents: [],
      created_at: '2026-04-16T00:00:01Z',
      updated_at: '2026-04-16T00:00:01Z',
    },
  },
  {
    body: {
      task_id: 'task-2',
      repo_uuid: 'repo-1',
      filename: 'budget.md',
      file_ext: '.md',
      size_bytes: 2048,
      size_display: '2.0 KB',
      status: 'queued',
      progress: 0,
      message: '等待排队处理',
      documents: [],
      created_at: '2026-04-16T00:00:01Z',
      updated_at: '2026-04-16T00:00:01Z',
    },
  },
]);
queueFetch('/api/bootstrap', [
  {
    body: {
      repos: [{ repo_uuid: 'repo-1', name: '财务库', slug: 'finance' }],
      active_repo_uuid: 'repo-1',
      tasks: [
        {
          task_id: 'task-2',
          repo_uuid: 'repo-1',
          filename: 'budget.md',
          file_ext: '.md',
          size_bytes: 2048,
          size_display: '2.0 KB',
          status: 'queued',
          progress: 0,
          message: '等待排队处理',
          documents: [],
          created_at: '2026-04-16T00:00:01Z',
          updated_at: '2026-04-16T00:00:01Z',
        },
      ],
    },
  },
]);
queueFetch('/api/repositories/repo-1/query', [
  {
    body: {
      answer: '# 回答\\n\\n这是测试回答。',
      html: '<p>这是测试回答。</p>',
      confidence: { level: 'high', score: 0.92, reasons: ['证据充分'] },
      evidence_html: '<div class="client-evidence"><section class="client-evidence-section"><h5 class="client-evidence-heading">证据摘要</h5><p class="client-evidence-summary-text">基于测试文档生成。</p></section></div>',
      evidence_text: '级别：高',
    },
  },
]);
document.getElementById('repo-passphrase').value = 'secret-pass';
document.getElementById('pick-file-btn').click();
document.getElementById('file-input').files = [
  { name: 'finance.md', size: 1024 },
  { name: 'budget.md', size: 2048 },
];
document.getElementById('file-input').dispatch('change', { target: document.getElementById('file-input') });
document.getElementById('upload-form').dispatch('submit', { preventDefault() {} });
await flush();
expect(document.getElementById('upload-dialog').open === false, '上传提交后弹窗应自动关闭');
expect(document.getElementById('documents-body').children.length >= 2, '上传后列表应立即展示排队文档');
document.getElementById('refresh-bootstrap-btn').click();
await flush();
expect(document.getElementById('documents-body').children.length >= 1, '刷新后未完成文档仍应保留');
document.getElementById('query-input').value = '预算情况如何？';
document.getElementById('query-form').dispatch('submit', { preventDefault() {} });
await flush();
expect(document.getElementById('query-result').hidden === false, '问答提交后结果区应可见');
expect(document.getElementById('result-content').innerHTML.indexOf('测试回答') >= 0, '问答结果应正确渲染');
expect(document.getElementById('evidence-text').innerHTML.indexOf('基于测试文档生成') >= 0, '证据摘要应渲染结构化 HTML');
document.getElementById('open-create-dialog-btn').click();
expect(document.getElementById('create-dialog').open === true, '完整流程后按钮仍应可交互');
"""
    )


def test_ingest_task_registry_keeps_ready_event_as_running(tmp_path):
    controller = SimpleNamespace(
        manager=SimpleNamespace(client_home=tmp_path / "client-home"),
        ingest_file=lambda repo_uuid, passphrase, source_path, on_event=None: [],
        list_documents=lambda repo_uuid, passphrase: [],
    )
    controller.manager.client_home.mkdir(parents=True, exist_ok=True)

    registry = _IngestTaskRegistry(controller)
    upload_path = controller.manager.client_home / "finance.md"
    upload_path.write_text("# finance", encoding="utf-8")

    task = registry.start(
        repo_uuid="repo-1",
        passphrase="secret-pass",
        upload_path=upload_path,
        filename="finance.md",
    )
    registry._update_from_event(
        task["task_id"],
        {
            "status": "ready",
            "progress": 100,
            "message": "Ingest complete: 1 created, 0 updated",
        },
    )
    snapshot = registry.snapshot(task["task_id"])

    assert snapshot["status"] == "running"
    assert snapshot["badge"] == "已完成"
    assert snapshot["progress"] == 100


def test_client_js_document_table_renders_delete_button():
    _run_client_js_scenario(
        """
queueFetch('/api/repositories/repo-1/documents', [
  {
    body: {
      documents: [
        {
          filename: 'finance.md',
          file_ext: '.md',
          size_display: '1.0 KB',
          status: 'ready',
          progress: 100,
          progress_message: '文档处理完成',
          processed_filename: 'finance.md',
          updated_at: '2026-04-16T00:00:02Z',
          last_ingested_at: '2026-04-16T00:00:02Z',
          detail: 'finance detail',
        },
      ],
      tasks: [],
    },
  },
]);
document.getElementById('repo-passphrase').value = 'secret-pass';
document.getElementById('open-repo-btn').click();
await flush();
const rows = document.getElementById('documents-body').children;
expect(rows.length === 1, '载入后应展示一条文档记录');
expect(rows[0].innerHTML.indexOf('删除') >= 0, '文档列表操作列应渲染删除按钮');
"""
    )


def test_client_js_document_table_shows_friendly_name_and_compact_time():
    _run_client_js_scenario(
        """
queueFetch('/api/repositories/repo-1/documents', [
  {
    body: {
      documents: [
        {
          filename: 'b62852bb1c57463aa16773b893e9633f_AE650.doc',
          file_ext: '.doc',
          size_display: '368.0 KB',
          status: 'ready',
          progress: 100,
          progress_message: '文档处理完成',
          processed_filename: 'AE650.doc',
          updated_at: '2026-04-16T11:36:12.988867+00:00',
          last_ingested_at: '2026-04-16T11:36:12.988867+00:00',
          detail: 'AE650 detail',
        },
      ],
      tasks: [],
    },
  },
]);
document.getElementById('repo-passphrase').value = 'secret-pass';
document.getElementById('open-repo-btn').click();
await flush();
const rows = document.getElementById('documents-body').children;
expect(rows.length === 1, '载入后应展示一条文档记录');
expect(rows[0].innerHTML.indexOf('AE650.doc') >= 0, '文件列表应展示去掉存储前缀后的文件名');
expect(rows[0].innerHTML.indexOf('b62852bb1c57463aa16773b893e9633f_AE650.doc') < 0, '文件列表不应直接展示存储前缀');
expect(rows[0].innerHTML.indexOf('T11:36:12.988867+00:00') < 0, '时间列不应展示原始 ISO 长时间戳');
expect(rows[0].innerHTML.indexOf('client-doc-time-date') >= 0, '时间列应渲染紧凑日期格式');
"""
    )


def test_ingest_task_registry_queues_tasks(tmp_path):
    started = []
    release_first = threading.Event()

    def fake_ingest(repo_uuid, passphrase, source_path, on_event=None):
        started.append(Path(source_path).name)
        if on_event:
            on_event({"status": "processing", "progress": 10, "message": "Analyzing source content"})
        if len(started) == 1:
            release_first.wait(timeout=2)
        if on_event:
            on_event({"status": "ready", "progress": 100, "message": "Ingest complete: 1 created, 0 updated"})
        return []

    controller = SimpleNamespace(
        manager=SimpleNamespace(client_home=tmp_path / "client-home"),
        ingest_file=fake_ingest,
        list_documents=lambda repo_uuid, passphrase: [],
    )
    controller.manager.client_home.mkdir(parents=True, exist_ok=True)

    registry = _IngestTaskRegistry(controller)
    upload_a = controller.manager.client_home / "a.md"
    upload_b = controller.manager.client_home / "b.md"
    upload_a.write_text("# a", encoding="utf-8")
    upload_b.write_text("# b", encoding="utf-8")

    task_a = registry.start(
        repo_uuid="repo-1",
        passphrase="secret-pass",
        upload_path=upload_a,
        filename="a.md",
    )
    task_b = registry.start(
        repo_uuid="repo-1",
        passphrase="secret-pass",
        upload_path=upload_b,
        filename="b.md",
    )

    assert task_a["status"] == "running"
    assert task_b["status"] == "queued"
    assert {item["filename"] for item in registry.list_tasks("repo-1", active_only=True)} == {"a.md", "b.md"}

    release_first.set()


def test_cli_list_import_and_health(tmp_path):
    services_path = tmp_path / "services.json"
    _write_services_file(services_path)
    repo_dir = tmp_path / "repo"
    bundle_path = tmp_path / "repo.tgz"

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
    assert create_result.exit_code == 0

    export_result = runner.invoke(cli, ["export", str(repo_dir), str(bundle_path)])
    assert export_result.exit_code == 0

    with patch("confidential_client.cli.ConfidentialClientController") as mock_controller:
        controller = mock_controller.return_value
        controller.list_repositories.return_value = [
            SimpleNamespace(repo_uuid="u1", name="R1", slug="s1", updated_at="2026-04-16T00:00:00Z")
        ]
        controller.import_repository.return_value = SimpleNamespace(repo_uuid="u2", name="R2", slug="s2")
        controller.check_services.return_value = {"qdrant": {"ok": True, "message": "HTTP 200"}}
        controller.check_for_updates.return_value = SimpleNamespace(
            to_dict=lambda: {"latest_version": "0.3.0", "update_available": True}
        )

        list_result = runner.invoke(cli, ["list"])
        import_result = runner.invoke(cli, ["import", str(bundle_path)])
        health_result = runner.invoke(cli, ["health", "--services-file", str(services_path)])
        version_result = runner.invoke(cli, ["version"])
        update_result = runner.invoke(cli, ["update-check", "--manifest-url", "https://updates.example.com/appcast.json"])

    assert list_result.exit_code == 0
    assert import_result.exit_code == 0
    assert health_result.exit_code == 0
    assert version_result.exit_code == 0
    assert update_result.exit_code == 0
    assert '"repo_uuid": "u1"' in list_result.output
    assert '"repo_uuid": "u2"' in import_result.output
    assert '"ok": true' in health_result.output.lower()
    assert "Open LLM Wiki 机密客户端" in version_result.output
    assert '"update_available": true' in update_result.output.lower()
