from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QUERY_SCRIPT = ROOT / "openclaw-skills" / "openllm-kb-search" / "scripts" / "query_openllm.py"
SAVE_SCRIPT = ROOT / "openclaw-skills" / "openllm-kb-search" / "scripts" / "save_token.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_resolve_repo_hint_prefers_exact_full_name():
    mod = _load_module(QUERY_SCRIPT, "query_openllm_exact")
    repos = [
        {"full_name": "alice/demo", "slug": "demo", "name": "演示库", "updated_at": "2026-04-18T10:00:00"},
        {"full_name": "bob/demo", "slug": "demo2", "name": "另一个库", "updated_at": "2026-04-18T09:00:00"},
    ]
    selected, candidates = mod.resolve_repo_hint(repos, "alice/demo")
    assert selected["full_name"] == "alice/demo"
    assert candidates[0]["full_name"] == "alice/demo"


def test_resolve_repo_hint_returns_ambiguous_when_scores_tie():
    mod = _load_module(QUERY_SCRIPT, "query_openllm_ambiguous")
    repos = [
        {"full_name": "alice/perf", "slug": "perf-a", "name": "性能库", "updated_at": "2026-04-18T10:00:00"},
        {"full_name": "bob/perf", "slug": "perf-b", "name": "性能库", "updated_at": "2026-04-18T09:00:00"},
    ]
    selected, candidates = mod.resolve_repo_hint(repos, "性能库")
    assert selected is None
    assert len(candidates) == 2


def test_store_token_writes_secret_file(tmp_path: Path):
    mod = _load_module(SAVE_SCRIPT, "save_token_mod")
    target = tmp_path / "token.env"
    result = mod.store_token(target, "ollw_abc123456", "http://172.36.164.85:5000/")
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert "OPEN_LLM_WIKI_TOKEN=ollw_abc123456" in content
    assert "OPEN_LLM_WIKI_BASE_URL=http://172.36.164.85:5000" in content
    assert result["token_prefix"].startswith("ollw_abc123")
