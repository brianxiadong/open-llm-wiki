"""通过 `/api/v1/search` 对真实部署做 E2E 检索/问答评测。

示例：

    export EVAL_API_TOKEN=ollw_...
    python eval/scripts/eval_e2e.py \\
        --base-url https://wiki.example.com \\
        --repo owner/slug \\
        --questions eval/ground_truth/e2e_questions.sample.json \\
        --modes standard,deep,react \\
        --out eval/results/e2e-report.json

依赖：仅标准库（urllib）；金标格式见样例 JSON。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_PostResult = tuple[int, dict[str, Any] | None, str | None]

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from eval.e2e.scoring import load_questions_file, score_one, summarize_runs  # noqa: E402


def _post_json(
    url: str,
    body: dict[str, Any],
    token: str,
    timeout: float,
) -> _PostResult:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = getattr(resp, "status", 200) or 200
            try:
                return status, json.loads(raw), None
            except json.JSONDecodeError:
                return status, None, f"invalid_json: {raw[:500]}"
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw)
        except Exception:
            parsed = None
        return e.code, parsed, None if parsed is not None else (raw[:500] if raw else str(e))
    except Exception as e:  # noqa: BLE001
        return 0, None, str(e)


def main() -> None:
    ap = argparse.ArgumentParser(description="E2E KB search eval via /api/v1/search")
    ap.add_argument("--base-url", default=os.environ.get("EVAL_BASE_URL", "http://127.0.0.1:5000"))
    ap.add_argument("--repo", required=True, help='owner/slug，写入每条请求的 body["repo"]')
    ap.add_argument("--questions", type=Path, required=True)
    ap.add_argument(
        "--modes",
        default="standard",
        help="逗号分隔：standard,deep,react",
    )
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument(
        "--token",
        default=os.environ.get("EVAL_API_TOKEN", ""),
        help="Bearer token；未传则读环境变量 EVAL_API_TOKEN",
    )
    args = ap.parse_args()
    token = (args.token or "").strip()
    if not token:
        print("缺少 token：请传 --token 或设置 EVAL_API_TOKEN", file=sys.stderr)
        sys.exit(2)

    base = args.base_url.rstrip("/")
    search_url = f"{base}/api/v1/search"
    modes = [m.strip().lower() for m in args.modes.split(",") if m.strip()]
    questions = load_questions_file(args.questions)

    rows: list[dict[str, Any]] = []
    for gold in questions:
        qtext = str(gold.get("question") or "").strip()
        if not qtext:
            continue
        qid = str(gold.get("id") or "")
        repo = str(gold.get("repo") or "").strip() or args.repo
        for mode in modes:
            body: dict[str, Any] = {"query": qtext, "repo": repo, "reasoning_mode": mode}
            status, payload, err = _post_json(search_url, body, token, args.timeout)
            http_ok = status == 200 and payload is not None and err is None
            raw = payload if isinstance(payload, dict) else {}
            scores = score_one(
                gold,
                raw,
                http_ok=http_ok,
                http_status=status if status else None,
                error=err,
            )
            rows.append(
                {
                    "id": qid,
                    "mode": mode,
                    "question": qtext,
                    "repo": repo,
                    "http_status": status,
                    "error": err,
                    "latency_ms": raw.get("latency_ms"),
                    "aggregate": scores.get("aggregate"),
                    "scores": scores,
                    "raw_response": raw if http_ok else raw,
                }
            )

    report = {
        "config": {
            "base_url": base,
            "search_url": search_url,
            "repo_default": args.repo,
            "modes": modes,
            "questions_file": str(args.questions),
        },
        "summary": summarize_runs(rows),
        "runs": rows,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
