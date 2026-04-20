#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

DEFAULT_BASE_URL = "http://172.36.164.85:5000"


def codex_home() -> Path:
    home = os.environ.get("CODEX_HOME")
    if home:
        return Path(home).expanduser()
    return Path.home() / ".codex"


def default_token_file() -> Path:
    return codex_home() / "openllm-kb-search" / "token.env"


def mask_token(token: str) -> str:
    if len(token) <= 12:
        return token[:4] + "***"
    return token[:12] + "***"


def store_token(token_file: Path, token: str, base_url: str) -> dict[str, str]:
    token = token.strip()
    base_url = base_url.rstrip("/")
    if not token.startswith("ollw_"):
        raise ValueError("token 必须以 ollw_ 开头")
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(
        f"OPEN_LLM_WIKI_BASE_URL={base_url}\nOPEN_LLM_WIKI_TOKEN={token}\n",
        encoding="utf-8",
    )
    os.chmod(token_file, stat.S_IRUSR | stat.S_IWUSR)
    return {
        "token_file": str(token_file),
        "base_url": base_url,
        "token_prefix": mask_token(token),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Save Open LLM Wiki token for the shared OpenClaw skill.")
    parser.add_argument("--token", help="Open LLM Wiki API token. If omitted, stdin is used.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Base URL of the Open LLM Wiki service.")
    parser.add_argument("--token-file", default=str(default_token_file()), help="Secret file path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = args.token if args.token is not None else sys.stdin.read().strip()
    if not token:
        print(json.dumps({"error": "token_missing", "message": "未提供 token。"}, ensure_ascii=False))
        return 2
    try:
        result = store_token(Path(args.token_file).expanduser(), token, args.base_url)
    except ValueError as exc:
        print(json.dumps({"error": "invalid_token", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"saved": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
