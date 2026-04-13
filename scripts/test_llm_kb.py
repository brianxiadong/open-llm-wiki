#!/usr/bin/env python3
"""
对 LLM 测试知识库执行一批问题，打印回答、置信度和证据来源。

用法:
    python scripts/test_llm_kb.py [--user testuser] [--repo test-kb] [--verbose]
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

# ---------------------------------------------------------------------------
# 测试问题集（覆盖知识库各主题）
# ---------------------------------------------------------------------------
TEST_QUESTIONS = [
    # Transformer / 注意力机制
    "Transformer 中 Self-Attention 的计算复杂度是多少？为什么长序列会有问题？",
    "Multi-Head Attention 相比单头注意力有什么优势？",
    # 预训练 / Scaling Laws
    "什么是 Scaling Laws？它对 LLM 训练有什么指导意义？",
    # RLHF / 对齐
    "RLHF 的完整流程是什么？包含哪几个阶段？",
    "SFT 和 RLHF 有什么区别？各自适用什么场景？",
    # Prompt Engineering
    "Chain-of-Thought 提示和普通提示有什么区别？什么场景下效果更好？",
    "Zero-shot 和 Few-shot 提示各自的优缺点是什么？",
    # 量化
    "GPTQ 和 AWQ 量化方法有什么区别？",
    "QLoRA 是什么？它如何在量化模型上做微调？",
    # 推理优化
    "KV Cache 的作用是什么？它如何减少推理延迟？",
    "Flash Attention 相比标准 Attention 有哪些改进？",
    # Agent
    "LLM Agent 的三大核心组件是什么？",
    "ReAct 框架中 Reasoning 和 Acting 是如何交替进行的？",
    # 评测
    "MMLU 基准测试评估的是什么能力？包含哪些领域？",
    # Fine-tuning / LoRA
    "LoRA 的核心思想是什么？相比全参数微调有哪些优势？",
    "PEFT 包含哪几种主流方法？",
    # 跨领域综合
    "RAG 和微调分别适合什么场景？如何选择？",
]

CONF_EMOJI = {"high": "🟢", "medium": "🟡", "low": "🔴"}
CONF_LABEL = {"high": "高", "medium": "中", "low": "低"}


def run_tests(username: str, repo_slug: str, verbose: bool) -> None:
    import logging
    logging.disable(logging.WARNING)

    from app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        from models import Repo, User

        user = User.query.filter_by(username=username).first()
        if not user:
            print(f"用户 {username!r} 不存在")
            return
        repo = Repo.query.filter_by(user_id=user.id, slug=repo_slug).first()
        if not repo:
            print(f"知识库 {repo_slug!r} 不存在")
            return

        engine = flask_app.wiki_engine

        results = []
        total = len(TEST_QUESTIONS)

        print(f"知识库: {username}/{repo_slug}")
        print(f"共 {total} 道测试题\n")
        print("=" * 70)

        for idx, question in enumerate(TEST_QUESTIONS, 1):
            print(f"\n[{idx:02d}/{total}] {question}")
            print("-" * 60)

            result = engine.query_with_evidence(repo, username, question)

            conf = result.get("confidence", {})
            level = conf.get("level", "low")
            score = conf.get("score", 0.0)
            reasons = conf.get("reasons", [])
            answer = result.get("markdown", "（无回答）")
            wiki_ev = result.get("wiki_evidence", [])
            chunk_ev = result.get("chunk_evidence", [])

            emoji = CONF_EMOJI.get(level, "⚪")
            label = CONF_LABEL.get(level, level)

            # 截断回答显示
            answer_preview = answer.replace("\n", " ").strip()
            if len(answer_preview) > 300:
                answer_preview = answer_preview[:300] + "…"

            print(f"置信度: {emoji} {label}（{score:.2f}）  |  "
                  f"Wiki命中: {len(wiki_ev)}  原文片段: {len(chunk_ev)}")
            if reasons:
                print(f"原因: {' · '.join(reasons)}")
            print(f"\n回答摘要:\n{textwrap.fill(answer_preview, width=68, initial_indent='  ', subsequent_indent='  ')}")

            if verbose and wiki_ev:
                print("\n引用页面:")
                for ev in wiki_ev[:3]:
                    print(f"  · [{ev['title']}]({ev['filename']}) — {ev['reason']}")

            if verbose and chunk_ev:
                print("\n原文片段 (Top 3):")
                for hit in chunk_ev[:3]:
                    snippet = hit.get("snippet", "").replace("\n", " ")[:120]
                    print(f"  · {hit['filename']} (score={hit['score']:.3f}): {snippet}…")

            results.append({
                "q": question,
                "level": level,
                "score": score,
                "wiki_hits": len(wiki_ev),
                "chunk_hits": len(chunk_ev),
            })

        # 统计
        print("\n" + "=" * 70)
        print("测试结果统计")
        print("=" * 70)
        high = sum(1 for r in results if r["level"] == "high")
        medium = sum(1 for r in results if r["level"] == "medium")
        low = sum(1 for r in results if r["level"] == "low")
        avg_score = sum(r["score"] for r in results) / len(results)
        avg_wiki = sum(r["wiki_hits"] for r in results) / len(results)
        avg_chunk = sum(r["chunk_hits"] for r in results) / len(results)

        print(f"  总题数    : {total}")
        print(f"  高置信度  : {high:2d} 题 ({high/total*100:.0f}%)  🟢")
        print(f"  中置信度  : {medium:2d} 题 ({medium/total*100:.0f}%)  🟡")
        print(f"  低置信度  : {low:2d} 题 ({low/total*100:.0f}%)  🔴")
        print(f"  平均分数  : {avg_score:.3f}")
        print(f"  平均Wiki命中 : {avg_wiki:.1f} 页/题")
        print(f"  平均片段命中 : {avg_chunk:.1f} 片/题")

        print("\n各题明细:")
        for r in results:
            emoji = CONF_EMOJI.get(r["level"], "⚪")
            q_short = r["q"][:50] + ("…" if len(r["q"]) > 50 else "")
            print(f"  {emoji} [{r['score']:.2f}] {q_short}")


def main() -> None:
    parser = argparse.ArgumentParser(description="测试 LLM 知识库问答效果")
    parser.add_argument("--user", default="testuser")
    parser.add_argument("--repo", default="test-kb")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示证据来源详情")
    args = parser.parse_args()
    run_tests(args.user, args.repo, args.verbose)


if __name__ == "__main__":
    main()
