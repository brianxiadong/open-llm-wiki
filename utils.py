"""Utility functions for Open-LLM-Wiki."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import markdown as md_lib
import yaml
from markdown.extensions.codehilite import CodeHiliteExtension
from markdown.extensions.fenced_code import FencedCodeExtension
from markdown.extensions.tables import TableExtension
from markdown.extensions.toc import TocExtension


def get_app_tz() -> ZoneInfo:
    """应用展示用 IANA 时区（来自 ``Config.APP_TIMEZONE``，默认东八区）。"""
    from config import Config

    name = (getattr(Config, "APP_TIMEZONE", None) or "Asia/Shanghai").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Asia/Shanghai")


def utc_to_local(dt: datetime | None) -> datetime | None:
    """将 UTC（含无 tz 的 naive，按 UTC 理解）转为应用本地时区。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(get_app_tz())


def local_now() -> datetime:
    """当前时刻在应用本地时区下的带时区 datetime。"""
    return datetime.now(timezone.utc).astimezone(get_app_tz())


def local_today_date_str() -> str:
    """应用本地时区的 ``YYYY-MM-DD``（日志分卷、Wiki frontmatter 等）。"""
    return local_now().strftime("%Y-%m-%d")


def slugify(text: str) -> str:
    """Convert text to URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text.strip("-")


def safe_upload_basename(filename: str | None) -> str:
    """Sanitize an uploaded file name for local storage while preserving Unicode (e.g. CJK).

    Werkzeug's ``secure_filename`` strips all non-ASCII characters; this replaces that
    behavior for user-visible originals. Removes path components, NULs, and characters
    unsafe on common filesystems (``/\\:*?"<>|`` and ASCII control chars). Trailing
    spaces/dots (invalid on Windows) are stripped.
    """
    if not filename:
        return ""
    name = str(filename).replace("\x00", "")
    # Normalize separators so basename strips any uploaded path tricks
    name = os.path.basename(name.replace("\\", "/"))
    if name in (".", ".."):
        return ""
    chars: list[str] = []
    for ch in name:
        o = ord(ch)
        if ch in '/\\:*?"<>|' or o < 32:
            chars.append("_")
        else:
            chars.append(ch)
    name = "".join(chars).strip()
    name = name.rstrip(" .")
    return name


def render_markdown(text: str, wiki_base_url: str = "") -> tuple[dict, str]:
    """Render markdown to HTML.

    Returns (frontmatter_dict, html_string).
    Parses YAML frontmatter delimited by ``---`` and rewrites ``.md`` links
    to point at *wiki_base_url*.
    """
    frontmatter: dict = {}
    content = text
    normalized = text.lstrip()
    if normalized.startswith("```"):
        first_nl = normalized.find("\n")
        if first_nl != -1:
            normalized = normalized[first_nl + 1:].lstrip()
    if normalized.startswith("---"):
        parts = normalized.split("---", 2)
        if len(parts) >= 3:
            try:
                frontmatter = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                pass
            content = parts[2]
            if content.rstrip().endswith("```"):
                content = content.rstrip()[:-3]

    if wiki_base_url:
        content = re.sub(
            r"\[([^\]]+)\]\(([^)]+)\.md\)",
            lambda m: f"[{m.group(1)}]({wiki_base_url}/{m.group(2)})",
            content,
        )

    extensions = [
        CodeHiliteExtension(css_class="highlight"),
        FencedCodeExtension(),
        TocExtension(permalink=True),
        TableExtension(),
        "md_in_html",
    ]
    html = md_lib.markdown(content, extensions=extensions)
    return frontmatter, html


def extract_links(markdown_text: str) -> list[str]:
    """Extract all ``.md`` link targets from markdown text (without extension)."""
    return re.findall(r"\[[^\]]+\]\(([^)]+)\.md\)", markdown_text)


def get_backlinks(wiki_dir: str, target_page: str) -> list[dict]:
    """Find all pages that link to *target_page*.

    Returns ``[{"filename": …, "title": …}, …]``.
    """
    backlinks: list[dict] = []
    target = target_page.replace(".md", "")
    for filename in os.listdir(wiki_dir):
        if not filename.endswith(".md"):
            continue
        filepath = os.path.join(wiki_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        if f"]({target}.md)" in content or f"]({target})" in content:
            fm, _ = render_markdown(content)
            title = fm.get("title", filename.replace(".md", ""))
            backlinks.append({"filename": filename, "title": title})
    return backlinks


def ensure_repo_dirs(data_dir: str, username: str, repo_slug: str) -> str:
    """Create the standard repo directory layout and return the base path.

    Layout::

        <data_dir>/<username>/<repo_slug>/
            raw/
                assets/
            wiki/
    """
    base = os.path.join(data_dir, username, repo_slug)
    os.makedirs(os.path.join(base, "raw", "assets"), exist_ok=True)
    os.makedirs(os.path.join(base, "wiki"), exist_ok=True)
    os.makedirs(os.path.join(base, "facts", "records"), exist_ok=True)
    return base


def get_repo_path(data_dir: str, username: str, repo_slug: str) -> str:
    return os.path.join(data_dir, username, repo_slug)


def list_wiki_pages(wiki_dir: str) -> list[dict]:
    """List all wiki pages with frontmatter metadata.

    Returns ``[{"filename", "title", "type", "updated"}, …]``.
    """
    pages: list[dict] = []
    if not os.path.isdir(wiki_dir):
        return pages
    for filename in sorted(os.listdir(wiki_dir)):
        if not filename.endswith(".md"):
            continue
        filepath = os.path.join(wiki_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        fm, _ = render_markdown(content)
        pages.append(
            {
                "filename": filename,
                "title": fm.get("title", filename.replace(".md", "")),
                "type": fm.get("type", "unknown"),
                "updated": fm.get("updated", ""),
            }
        )
    return pages


def list_raw_sources(raw_dir: str) -> list[dict]:
    """List raw source files.

    Returns ``[{"filename", "size_kb", "is_markdown"}, …]``.
    """
    sources: list[dict] = []
    if not os.path.isdir(raw_dir):
        return sources
    for filename in sorted(os.listdir(raw_dir)):
        if filename == "assets" or filename.startswith("."):
            continue
        filepath = os.path.join(raw_dir, filename)
        if os.path.isfile(filepath):
            size_kb = os.path.getsize(filepath) / 1024
            sources.append(
                {
                    "filename": filename,
                    "size_kb": round(size_kb, 1),
                    "is_markdown": filename.endswith(".md"),
                }
            )
    return sources


def _normalize_cell_value(value):
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _normalize_header_row(header_row: list) -> list[str]:
    headers: list[str] = []
    used: dict[str, int] = {}
    for idx, cell in enumerate(header_row, start=1):
        base = str(cell).strip() if cell is not None else ""
        if not base:
            base = f"col_{idx}"
        if base in used:
            used[base] += 1
            base = f"{base}_{used[base]}"
        else:
            used[base] = 1
        headers.append(base)
    return headers


def _find_header_row_index(rows: list[list], max_scan: int = 5) -> int:
    """Pick the row most likely to be the real column header.

    Excel sheets often have one or more title / merged-cell rows before the
    actual headers.  The heuristic: among the first *max_scan* rows, the row
    with the highest count of non-empty cells is the header.  Ties go to the
    earlier row.
    """
    if not rows:
        return 0
    scan = min(len(rows), max_scan)
    best_idx, best_count = 0, 0
    for i in range(scan):
        count = sum(1 for c in rows[i] if c not in (None, ""))
        if count > best_count:
            best_count = count
            best_idx = i
    return best_idx


def _row_to_fact_text(source_filename: str, sheet_name: str, row_index: int, fields: dict) -> str:
    pairs = [f"{key}={value}" for key, value in fields.items()]
    joined = "; ".join(pairs)
    return (
        f"来源={source_filename}; 表={sheet_name}; 行={row_index}; {joined}"
        if joined
        else f"来源={source_filename}; 表={sheet_name}; 行={row_index}"
    )


def build_tabular_markdown_and_records(
    source_filename: str,
    tables: list[dict],
    source_markdown_filename: str | None = None,
) -> tuple[str, list[dict]]:
    """Convert tables into Markdown plus row-level fact records."""
    stem = os.path.splitext(os.path.basename(source_filename))[0]
    markdown_parts = [f"# {stem}\n", f"> 来源文件: {source_filename}\n"]
    records: list[dict] = []

    for table_idx, table in enumerate(tables):
        table_name = str(table.get("name") or f"Sheet{table_idx + 1}").strip()
        rows = table.get("rows") or []
        non_empty_rows = [
            list(row)
            for row in rows
            if row is not None and any(cell not in (None, "") for cell in row)
        ]
        if not non_empty_rows:
            continue

        header_idx = _find_header_row_index(non_empty_rows)
        headers = _normalize_header_row(non_empty_rows[header_idx])
        data_rows = non_empty_rows[header_idx + 1:]

        markdown_parts.append(f"\n## Sheet: {table_name}\n")
        markdown_parts.append("| " + " | ".join(headers) + " |")
        markdown_parts.append("| " + " | ".join(["---"] * len(headers)) + " |")

        for row_offset, raw_row in enumerate(data_rows, start=header_idx + 2):
            padded = list(raw_row) + [None] * max(0, len(headers) - len(raw_row))
            values = [_normalize_cell_value(cell) for cell in padded[: len(headers)]]
            markdown_parts.append(
                "| " + " | ".join("" if value is None else str(value) for value in values) + " |"
            )
            fields = {
                header: value
                for header, value in zip(headers, values, strict=False)
                if value not in (None, "")
            }
            if not fields:
                continue
            table_slug = slugify(table_name) or f"sheet-{table_idx + 1}"
            records.append(
                {
                    "record_id": f"{table_slug}:{row_offset}",
                    "source_file": source_filename,
                    "source_markdown_filename": source_markdown_filename
                    or f"{stem}.md",
                    "sheet": table_name,
                    "row_index": row_offset,
                    "fields": fields,
                    "fact_text": _row_to_fact_text(source_filename, table_name, row_offset, fields),
                }
            )

    return "\n".join(markdown_parts), records


def write_jsonl(path: str, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def classify_query_mode(question: str) -> str:
    text = (question or "").strip().lower()
    if not text:
        return "narrative"

    fact_signals = [
        "多少", "几", "名单", "列表", "占比", "百分比", "同比", "环比", "增长",
        "下降", "收入", "销量", "金额", "日期", "时间", "排名", "top", "分别", "各",
    ]
    narrative_signals = [
        "总结", "概述", "趋势", "原因", "分析", "解读", "如何", "怎么", "介绍", "建议",
    ]

    has_fact_keyword = any(token in text for token in fact_signals)
    has_metric_pattern = bool(
        re.search(r"\d", text)
        and re.search(r"(多少|几|占比|百分比|同比|环比|增长|下降|收入|销量|金额|排名)", text)
    )
    has_fact_signal = has_fact_keyword or has_metric_pattern
    has_narrative_signal = any(token in text for token in narrative_signals)

    if has_fact_signal and has_narrative_signal:
        return "hybrid"
    if has_fact_signal:
        return "fact"
    return "narrative"


DEFAULT_SCHEMA_MD = """\
---
title: Wiki Schema
---

# Wiki Schema

This file defines the structure and conventions for the wiki.

## Page Types

- **concept**: Explains a single concept, term, or technology.
- **guide**: Step-by-step instructions or how-to content.
- **reference**: API docs, tables, specs, or lookup content.
- **overview**: High-level summaries that link to detail pages.
- **comparison**: Side-by-side analysis of alternatives.
- **log**: Changelog or ingestion history.
- **index**: The main entry point listing all pages.

## Frontmatter Fields

Every page should start with YAML frontmatter:

```yaml
---
title: Page Title
type: concept | guide | reference | overview | comparison
tags: [tag1, tag2]
source: original-source-filename.pdf
updated: YYYY-MM-DD
---
```

## Conventions

- Use Chinese for page content (unless the source is English-only).
- Filenames use lowercase ascii with hyphens: `my-page.md`.
- Cross-reference other pages with `[Title](other-page.md)`.
- Keep each page focused on a single topic.
"""

SCHEMA_ACADEMIC_MD = """\
---
title: Wiki Schema — 学术研究
---

# Wiki Schema — 学术研究

## Page Types

- **paper**: 论文摘要和关键发现。
- **concept**: 核心理论概念和术语定义。
- **method**: 研究方法、实验设计、技术实现。
- **result**: 实验结果、数据集、基准对比。
- **comparison**: 不同方法/模型的横向对比。
- **overview**: 某一研究方向的综合综述。
- **index**: 主目录，按主题分类所有页面。
- **log**: 摄入日志。

## Frontmatter Fields

```yaml
---
title: Paper/Concept Title
type: paper | concept | method | result | comparison | overview
tags: [nlp, transformer, etc.]
source: paper.pdf.md
evidence_level: strong | moderate | weak
updated: YYYY-MM-DD
---
```

## Conventions

- 摘要页（paper）包含：研究问题、方法、结论、局限性。
- 在 result 页标注证据等级 (evidence_level)。
- 对比页使用 Markdown 表格。
- 交叉引用格式：[作者年份](page.md)。
"""

SCHEMA_PRODUCT_MD = """\
---
title: Wiki Schema — 产品文档
---

# Wiki Schema — 产品文档

## Page Types

- **feature**: 功能介绍和使用说明。
- **guide**: 操作教程、快速入门。
- **reference**: API 文档、配置项、参数表。
- **faq**: 常见问题与解答。
- **changelog**: 版本更新记录。
- **overview**: 产品整体介绍。
- **index**: 主目录。
- **log**: 摄入日志。

## Frontmatter Fields

```yaml
---
title: Feature Name
type: feature | guide | reference | faq | changelog | overview
version: "1.0"
updated: YYYY-MM-DD
---
```

## Conventions

- guide 页使用有序步骤。
- reference 页使用表格格式。
- 每个功能页链接到对应的 guide 和 reference。
"""

SCHEMA_TECH_NOTES_MD = """\
---
title: Wiki Schema — 技术笔记
---

# Wiki Schema — 技术笔记

## Page Types

- **concept**: 技术概念、算法原理。
- **howto**: 如何解决某个具体问题。
- **snippet**: 代码片段、命令备忘。
- **troubleshoot**: 故障排查记录。
- **overview**: 技术栈整体概述。
- **index**: 主目录。
- **log**: 摄入日志。

## Frontmatter Fields

```yaml
---
title: Topic Name
type: concept | howto | snippet | troubleshoot | overview
tags: [python, docker, etc.]
updated: YYYY-MM-DD
---
```

## Conventions

- snippet 页包含可直接复制的代码块。
- troubleshoot 页包含：症状、根因、解决方案。
- 使用 howto 而非 guide（更口语化）。
"""

SCHEMA_TEMPLATES = {
    "default": ("通用", DEFAULT_SCHEMA_MD),
    "academic": ("学术研究", SCHEMA_ACADEMIC_MD),
    "product": ("产品文档", SCHEMA_PRODUCT_MD),
    "tech_notes": ("技术笔记", SCHEMA_TECH_NOTES_MD),
}


def file_md5(path: str) -> str:
    """计算文件的 MD5 哈希，用于重复检测。"""
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class QueryTraceLogger:
    """每日滚动 JSONL 文件日志，每次查询写一行，方便追溯。

    文件路径：<log_dir>/query_trace_YYYY-MM-DD.jsonl
    每行格式：
    {
      "ts": "2026-04-13T22:31:21+08:00",
      "repo": "xiadong/test-xiadong",
      "user": "xiadong",
      "question": "...",
      "mode": "fact",
      "latency_ms": 1234,
      "confidence": {"level": "high", "score": 0.82},
      "wiki_hits": [{"filename": "...", "title": "...", "reason": "..."}],
      "chunk_hits": [{"filename": "...", "score": 0.91, "snippet": "..."}],
      "fact_hits": [{"source_file": "...", "score": 0.87, "fields": {...}}],
      "answer": "完整回答 markdown"
    }
    """

    _lock = threading.Lock()

    def __init__(self, log_dir: str) -> None:
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)

    def _log_path(self) -> str:
        today = local_today_date_str()
        return os.path.join(self.log_dir, f"query_trace_{today}.jsonl")

    def write(
        self,
        *,
        repo: str,
        user: str | None,
        question: str,
        mode: str,
        latency_ms: int | None,
        confidence: dict,
        wiki_evidence: list,
        chunk_evidence: list,
        fact_evidence: list,
        answer: str,
    ) -> None:
        record = {
            "ts": local_now().isoformat(),
            "repo": repo,
            "user": user or "anonymous",
            "question": question,
            "mode": mode,
            "latency_ms": latency_ms,
            "confidence": confidence,
            "wiki_hits": [
                {"filename": e.get("filename", ""), "title": e.get("title", ""), "reason": e.get("reason", "")}
                for e in (wiki_evidence or [])
            ],
            "chunk_hits": [
                {"filename": e.get("filename", ""), "score": e.get("score"), "snippet": (e.get("snippet") or "")[:300]}
                for e in (chunk_evidence or [])
            ],
            "fact_hits": [
                {"source_file": e.get("source_file", ""), "score": e.get("score"), "fields": e.get("fields", {})}
                for e in (fact_evidence or [])
            ],
            "answer": answer,
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with open(self._log_path(), "a", encoding="utf-8") as f:
                f.write(line + "\n")


def _format_deploy_revision_file(raw: str) -> str | None:
    """Parse ``deploy/revision.txt``: line 1 = git short SHA, line 2 = deploy time (optional)."""
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return None
    if len(lines) == 1:
        return lines[0]
    return f"{lines[0]} · {lines[1]}"


def get_app_revision() -> str:
    """Human-visible build/deployment revision (git short SHA when available).

    Resolution order:
    1. ``APP_REVISION`` environment variable (manual override)
    2. ``deploy/revision.txt`` (written by ``scripts/deploy.sh``: SHA + deploy timestamp, Asia/Shanghai)
    3. ``git rev-parse --short HEAD`` when ``.git`` exists (local dev, no timestamp)
    4. ``unknown``
    """
    override = os.environ.get("APP_REVISION", "").strip()
    if override:
        return override
    base = os.path.dirname(os.path.abspath(__file__))
    rev_file = os.path.join(base, "deploy", "revision.txt")
    try:
        if os.path.isfile(rev_file):
            with open(rev_file, encoding="utf-8") as f:
                v = _format_deploy_revision_file(f.read())
            if v:
                return v
    except OSError:
        pass
    if os.path.isdir(os.path.join(base, ".git")):
        try:
            proc = subprocess.run(
                ["git", "-C", base, "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return "unknown"
