#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "http://172.36.164.85:5000"


def codex_home() -> Path:
    home = os.environ.get("CODEX_HOME")
    if home:
        return Path(home).expanduser()
    return Path.home() / ".codex"


def default_token_file() -> Path:
    return codex_home() / "openllm-kb-search" / "token.env"


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def load_credentials(token_file: Path) -> tuple[str | None, str]:
    file_values = load_env_file(token_file)
    token = os.environ.get("OPEN_LLM_WIKI_TOKEN") or file_values.get("OPEN_LLM_WIKI_TOKEN")
    base_url = (
        os.environ.get("OPEN_LLM_WIKI_BASE_URL")
        or file_values.get("OPEN_LLM_WIKI_BASE_URL")
        or DEFAULT_BASE_URL
    )
    return token, base_url.rstrip("/")


@dataclass
class ApiError(Exception):
    status: int
    body: Any


def api_request(
    *,
    base_url: str,
    token: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 45,
) -> Any:
    url = urllib.parse.urljoin(base_url + "/", path.lstrip("/"))
    data = None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url=url, method=method.upper(), headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {"message": raw}
        raise ApiError(exc.code, body) from exc


def normalize(text: str) -> str:
    return text.strip().lower()


def score_repo_hint(repo: dict[str, Any], hint: str) -> int:
    hint_norm = normalize(hint)
    full_name = normalize(str(repo.get("full_name", "")))
    slug = normalize(str(repo.get("slug", "")))
    name = normalize(str(repo.get("name", "")))
    if not hint_norm:
        return 0
    if hint_norm == full_name:
        return 500
    if hint_norm == slug:
        return 450
    if hint_norm == name:
        return 420
    if hint_norm in full_name:
        return 300
    if hint_norm in slug:
        return 260
    if hint_norm in name:
        return 240
    return 0


def resolve_repo_hint(repos: list[dict[str, Any]], hint: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    scored = []
    for repo in repos:
        score = score_repo_hint(repo, hint)
        if score > 0:
            scored.append((score, str(repo.get("updated_at", "")), repo))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    candidates = [repo for _, _, repo in scored]
    if not candidates:
        return None, []
    if len(scored) == 1 or scored[0][0] > scored[1][0]:
        return scored[0][2], candidates[:5]
    top_score = scored[0][0]
    ambiguous = [repo for score, _, repo in scored if score == top_score]
    return None, ambiguous[:5]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Query Open LLM Wiki via token auth.")
    parser.add_argument("--question", help="Natural language question.")
    parser.add_argument("--repo", help="Explicit repo full name, e.g. owner/slug.")
    parser.add_argument("--repo-name", help="Repo hint: display name, slug, or owner/slug.")
    parser.add_argument("--base-url", help="Override base URL.")
    parser.add_argument("--token-file", default=str(default_token_file()), help="Secret file path.")
    parser.add_argument("--check-token", action="store_true", help="Validate token by calling /api/v1/me.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    return parser.parse_args()


def dump(data: Any, pretty: bool) -> None:
    if pretty:
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(data, ensure_ascii=False))


def main() -> int:
    args = parse_args()
    token, stored_base_url = load_credentials(Path(args.token_file).expanduser())
    if not token:
        dump(
            {
                "error": "token_missing",
                "message": "未找到已保存 token，请先运行 save_token.py。",
            },
            args.pretty,
        )
        return 2
    base_url = (args.base_url or stored_base_url or DEFAULT_BASE_URL).rstrip("/")
    try:
        if args.check_token:
            result = api_request(base_url=base_url, token=token, method="GET", path="/api/v1/me")
            dump(result, args.pretty)
            return 0

        if not args.question:
            dump({"error": "question_missing", "message": "请提供 --question。"}, args.pretty)
            return 2

        payload: dict[str, Any] = {"query": args.question}
        if args.repo:
            payload["repo"] = args.repo
        elif args.repo_name:
            repo_result = api_request(base_url=base_url, token=token, method="GET", path="/api/v1/repos")
            repos = list(repo_result.get("repos", []))
            selected, candidates = resolve_repo_hint(repos, args.repo_name)
            if selected is None:
                dump(
                    {
                        "error": "repo_ambiguous" if candidates else "repo_not_found",
                        "message": "知识库名称未命中唯一仓库，请检查候选列表。",
                        "repo_hint": args.repo_name,
                        "candidates": candidates,
                    },
                    args.pretty,
                )
                return 4
            payload["repo"] = selected["full_name"]

        result = api_request(
            base_url=base_url,
            token=token,
            method="POST",
            path="/api/v1/search",
            payload=payload,
        )
    except ApiError as exc:
        data = exc.body if isinstance(exc.body, dict) else {"message": str(exc.body)}
        data.setdefault("status", exc.status)
        dump(data, args.pretty)
        return 1

    dump(result, args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
