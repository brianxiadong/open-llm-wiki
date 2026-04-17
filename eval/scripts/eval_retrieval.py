"""离线检索评测脚本：量化 RAG 改动对 Recall@k / MRR 的影响。

运行示例：

    # 评测服务端默认 RAG 配置
    python eval/scripts/eval_retrieval.py \
        --username admin \
        --repo llm-kb \
        --top-k 10 \
        --out eval/results/baseline.json

    # 关闭 BM25 / 调整阈值
    RAG_ENABLE_BM25=false RAG_CHUNK_SCORE_THRESHOLD=0.3 \
        python eval/scripts/eval_retrieval.py ...

前提：
    * ``eval/corpus/*.md`` 已经被摄入到指定 (user, repo) 的 wiki/Qdrant
    * ``eval/ground_truth/questions.json`` 的 ``expected_pages`` 是期望被命中的 wiki 页面

输出结构：

    {
      "config": {...},
      "summary": {"recall_at_k": 0.83, "mrr": 0.72, "n": 15, ...},
      "per_question": [{"id": "q01", "hit_pages": [...], "recall": 1.0, "rr": 1.0}, ...]
    }

该脚本只读 Qdrant / 本地文件，不会修改任何 wiki 内容，可以安全地重复跑。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# 让脚本可以直接 `python eval/scripts/eval_retrieval.py` 运行
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from config import Config  # noqa: E402
from llmwiki_core import HybridRetriever, RetrievalConfig  # noqa: E402
from qdrant_service import QdrantService  # noqa: E402


def _load_questions(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("questions") or [])


def _unique(seq: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in seq:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _evaluate_one(
    retriever: HybridRetriever,
    repo_id: int,
    question: dict[str, Any],
    *,
    top_k: int,
) -> dict[str, Any]:
    q_text = str(question.get("question") or "")
    expected = set(str(p) for p in question.get("expected_pages") or [])
    hits = retriever.retrieve_chunks(repo_id=repo_id, query=q_text, top_k=top_k)
    ranked_pages = _unique([str(h.get("filename") or "") for h in hits])[:top_k]

    hit_set = set(ranked_pages) & expected
    recall = len(hit_set) / max(1, len(expected)) if expected else 0.0
    rr = 0.0
    for rank, fn in enumerate(ranked_pages, start=1):
        if fn in expected:
            rr = 1.0 / rank
            break

    return {
        "id": question.get("id"),
        "question": q_text,
        "difficulty": question.get("difficulty"),
        "preferred_path": question.get("preferred_path"),
        "expected_pages": sorted(expected),
        "ranked_pages": ranked_pages,
        "recall": round(recall, 4),
        "rr": round(rr, 4),
        "top_chunk_score": round(float(hits[0].get("score") or 0.0), 4) if hits else 0.0,
        "sources_top1": hits[0].get("sources") if hits else [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrieval evaluation (Recall@k / MRR)")
    parser.add_argument("--username", required=True, help="Wiki owner username")
    parser.add_argument("--repo", required=True, help="Wiki repo slug")
    parser.add_argument("--repo-id", type=int, required=True, help="Numeric repo id used by Qdrant collections")
    parser.add_argument("--questions", default=str(_REPO_ROOT / "eval/ground_truth/questions.json"))
    parser.add_argument("--out", default="")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--disable-bm25", action="store_true", help="Override RAG_ENABLE_BM25=false")
    args = parser.parse_args()

    questions_path = Path(args.questions)
    if not questions_path.is_file():
        print(f"[error] questions file not found: {questions_path}", file=sys.stderr)
        return 2

    if args.disable_bm25:
        os.environ["RAG_ENABLE_BM25"] = "false"

    qdrant = QdrantService(
        Config.QDRANT_URL,
        Config.EMBEDDING_API_BASE,
        Config.EMBEDDING_API_KEY,
        Config.EMBEDDING_MODEL,
        Config.EMBEDDING_DIMENSIONS,
        chunk_min=Config.RAG_CHUNK_MIN,
        chunk_max=Config.RAG_CHUNK_MAX,
        chunk_overlap=Config.RAG_CHUNK_OVERLAP,
    )
    retrieval_cfg = RetrievalConfig.from_config(Config)
    if args.disable_bm25:
        retrieval_cfg = RetrievalConfig(**{**retrieval_cfg.__dict__, "enable_bm25": False})
    retriever = HybridRetriever(qdrant=qdrant, config=retrieval_cfg)

    questions = _load_questions(questions_path)
    per_question: list[dict[str, Any]] = []
    for q in questions:
        per_question.append(
            _evaluate_one(retriever, args.repo_id, q, top_k=args.top_k)
        )

    n = len(per_question)
    recall_at_k = sum(item["recall"] for item in per_question) / n if n else 0.0
    mrr = sum(item["rr"] for item in per_question) / n if n else 0.0
    hits_with_at_least_one = sum(1 for item in per_question if item["rr"] > 0)

    report = {
        "config": {
            "username": args.username,
            "repo": args.repo,
            "repo_id": args.repo_id,
            "top_k": args.top_k,
            "embedding_model": Config.EMBEDDING_MODEL,
            "chunk_min": Config.RAG_CHUNK_MIN,
            "chunk_max": Config.RAG_CHUNK_MAX,
            "chunk_overlap": Config.RAG_CHUNK_OVERLAP,
            "chunk_score_threshold": retrieval_cfg.chunk_score_threshold,
            "max_chunks_per_file": retrieval_cfg.max_chunks_per_file,
            "rrf_k": retrieval_cfg.rrf_k,
            "enable_bm25": retrieval_cfg.enable_bm25,
            "enable_hyde": Config.RAG_ENABLE_HYDE,
        },
        "summary": {
            "n": n,
            "recall_at_k": round(recall_at_k, 4),
            "mrr": round(mrr, 4),
            "hit_rate_at_k": round(hits_with_at_least_one / n, 4) if n else 0.0,
        },
        "per_question": per_question,
    }

    out_text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(out_text + "\n", encoding="utf-8")
        print(f"Wrote {out_path}")
    print("=" * 60)
    print(f"n={n}  Recall@{args.top_k}={report['summary']['recall_at_k']}  "
          f"MRR={report['summary']['mrr']}  "
          f"HitRate={report['summary']['hit_rate_at_k']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
