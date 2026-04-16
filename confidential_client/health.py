"""Health checks for confidential client external services."""

from __future__ import annotations

from typing import Any

import httpx
from openai import OpenAI

from llmwiki_core.contracts import ConfidentialServices


def check_services(services: ConfidentialServices) -> dict[str, dict[str, Any]]:
    return {
        "qdrant": _check_http(f"{services.qdrant_url.rstrip('/')}/healthz"),
        "mineru": _check_optional_http(services.mineru_api_url),
        "embedding": _check_embedding(services),
        "llm": _check_llm(services),
    }


def _ok(message: str) -> dict[str, Any]:
    return {"ok": True, "message": message}


def _fail(message: str) -> dict[str, Any]:
    return {"ok": False, "message": message}


def _check_http(url: str) -> dict[str, Any]:
    try:
        response = httpx.get(url, timeout=5)
        if response.status_code == 200:
            return _ok("HTTP 200")
        return _fail(f"HTTP {response.status_code}")
    except Exception as exc:
        return _fail(str(exc))


def _check_optional_http(base_url: str) -> dict[str, Any]:
    if not base_url:
        return _ok("未配置，按需启用")
    return _check_http(f"{base_url.rstrip('/')}/health")


def _check_embedding(services: ConfidentialServices) -> dict[str, Any]:
    try:
        client = OpenAI(
            base_url=services.embedding_api_base,
            api_key=services.embedding_api_key or "dummy",
            timeout=10,
        )
        response = client.embeddings.create(model=services.embedding_model, input="health check")
        if response.data:
            return _ok("embedding ok")
        return _fail("embedding empty")
    except Exception as exc:
        return _fail(str(exc))


def _check_llm(services: ConfidentialServices) -> dict[str, Any]:
    try:
        client = OpenAI(
            base_url=services.llm_api_base,
            api_key=services.llm_api_key or "dummy",
            timeout=15,
        )
        response = client.chat.completions.create(
            model=services.llm_model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=8,
            temperature=0,
        )
        content = response.choices[0].message.content if response.choices else ""
        if content is not None:
            return _ok("llm ok")
        return _fail("llm empty")
    except Exception as exc:
        return _fail(str(exc))
