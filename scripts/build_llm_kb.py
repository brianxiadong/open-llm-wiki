#!/usr/bin/env python3
"""
构建 LLM 测试知识库：
  1. 用 trafilatura 抓取权威网页（Hugging Face Blog / Lilian Weng's Blog 等）
  2. 将抓取内容写入 raw/ 目录
  3. 调用内网 WikiEngine（qwen35-27b）摄入，生成结构化 Wiki 页面

用法:
    python scripts/build_llm_kb.py [--user testuser] [--repo test-kb] [--no-ingest]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import trafilatura

# ---------------------------------------------------------------------------
# LLM 知识话题 → 权威网页 URL
# ---------------------------------------------------------------------------
TOPICS = [
    {
        "slug": "transformer-attention-mechanism",
        "title": "Transformer 与注意力机制",
        "urls": [
            "https://lilianweng.github.io/posts/2018-06-24-attention/",
            "https://huggingface.co/blog/transformers-history",
        ],
    },
    {
        "slug": "llm-pretraining-scaling",
        "title": "LLM 预训练与 Scaling Laws",
        "urls": [
            "https://lilianweng.github.io/posts/2023-01-27-the-transformer-family-v2/",
            "https://huggingface.co/blog/large-language-models",
        ],
    },
    {
        "slug": "rlhf-alignment",
        "title": "RLHF 与 LLM 对齐",
        "urls": [
            "https://huggingface.co/blog/rlhf",
            "https://lilianweng.github.io/posts/2023-03-15-prompt-engineering/",
        ],
    },
    {
        "slug": "rag-retrieval-augmented-generation",
        "title": "RAG 检索增强生成",
        "urls": [
            "https://huggingface.co/blog/rag",
            "https://lilianweng.github.io/posts/2023-10-25-adv-attack-llm/",
        ],
    },
    {
        "slug": "llm-inference-optimization",
        "title": "LLM 推理优化",
        "urls": [
            "https://huggingface.co/blog/optimize-llm",
            "https://huggingface.co/blog/tgi-llama31",
        ],
    },
    {
        "slug": "prompt-engineering",
        "title": "Prompt Engineering 技术",
        "urls": [
            "https://www.promptingguide.ai/introduction/basics",
            "https://huggingface.co/blog/gemma-peft",
        ],
    },
    {
        "slug": "llm-quantization",
        "title": "LLM 量化与压缩",
        "urls": [
            "https://huggingface.co/blog/merve/quantization",
            "https://huggingface.co/blog/4bit-transformers-bitsandbytes",
        ],
    },
    {
        "slug": "llm-agent-frameworks",
        "title": "LLM Agent 框架",
        "urls": [
            "https://lilianweng.github.io/posts/2023-06-23-agent/",
            "https://huggingface.co/blog/smolagents",
        ],
    },
    {
        "slug": "llm-evaluation-benchmarks",
        "title": "LLM 评测基准",
        "urls": [
            "https://huggingface.co/blog/open-llm-leaderboard-v2",
            "https://huggingface.co/blog/evaluating-mmlu-leaderboard",
        ],
    },
    {
        "slug": "fine-tuning-peft-lora",
        "title": "Fine-tuning、PEFT 与 LoRA",
        "urls": [
            "https://huggingface.co/blog/peft",
            "https://huggingface.co/blog/lora",
        ],
    },
]


def fetch_url(url: str, timeout: int = 15) -> str:
    """用 trafilatura 抓取并提取网页正文。"""
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return ""
    text = trafilatura.extract(
        downloaded,
        include_comments=False,
        include_tables=True,
        no_fallback=False,
    )
    return text or ""


def build_raw_content(topic: dict) -> str:
    """抓取话题所有 URL，合并为一份 Markdown 原始文档。"""
    parts = [f"# {topic['title']}\n"]
    for url in topic["urls"]:
        print(f"    抓取: {url}")
        content = fetch_url(url)
        if content:
            parts.append(f"\n---\n来源: {url}\n\n{content}\n")
            print(f"    ✓ {len(content)} 字符")
        else:
            print(f"    ✗ 抓取失败或内容为空")
        time.sleep(1)
    return "\n".join(parts)


def save_raw(raw_dir: Path, slug: str, content: str) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    filepath = raw_dir / f"{slug}.md"
    filepath.write_text(content, encoding="utf-8")
    print(f"  已保存: {filepath.name} ({len(content)} chars)")
    return filepath


def ingest_file(username: str, repo_slug: str, filename: str) -> None:
    from app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        from models import Repo, User

        user = User.query.filter_by(username=username).first()
        if not user:
            print(f"  [SKIP] 用户 {username!r} 不存在，跳过摄入。")
            return

        repo = Repo.query.filter_by(user_id=user.id, slug=repo_slug).first()
        if not repo:
            print(f"  [SKIP] 知识库 {repo_slug!r} 不存在，跳过摄入。")
            return

        engine = flask_app.wiki_engine
        print(f"  正在摄入 {filename} ...")
        for progress in engine.ingest(repo, username, filename):
            phase = progress.get("phase", "")
            msg = progress.get("message", "")
            pct = progress.get("progress", 0)
            print(f"    [{pct:3d}%] {phase}: {msg}")


def main() -> None:
    parser = argparse.ArgumentParser(description="抓取网页 + qwen35-27b 构建 LLM 测试知识库")
    parser.add_argument("--user", default="testuser")
    parser.add_argument("--repo", default="test-kb")
    parser.add_argument("--no-ingest", action="store_true", help="只抓取，不摄入")
    parser.add_argument(
        "--topics",
        nargs="*",
        help=f"指定话题 slug。可选: {[t['slug'] for t in TOPICS]}",
    )
    args = parser.parse_args()

    raw_dir = ROOT / "data" / args.user / args.repo / "raw"
    selected = TOPICS
    if args.topics:
        selected = [t for t in TOPICS if t["slug"] in args.topics]

    print(f"目标知识库: {args.user}/{args.repo}")
    print(f"原始文件目录: {raw_dir}")
    print(f"共 {len(selected)} 个话题\n")

    for i, topic in enumerate(selected, 1):
        slug = topic["slug"]
        print(f"[{i}/{len(selected)}] {topic['title']} ({slug})")

        raw_file = raw_dir / f"{slug}.md"
        if raw_file.exists() and raw_file.stat().st_size > 500:
            print(f"  已存在 ({raw_file.stat().st_size} bytes)，跳过抓取")
        else:
            content = build_raw_content(topic)
            if len(content) < 200:
                print(f"  [WARN] 内容太少，跳过: {slug}")
                continue
            save_raw(raw_dir, slug, content)

        if not args.no_ingest:
            try:
                ingest_file(args.user, args.repo, f"{slug}.md")
            except Exception as exc:
                print(f"  [ERROR] 摄入失败: {exc}")

        print()

    print("完成！")


if __name__ == "__main__":
    main()
