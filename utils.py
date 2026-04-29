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


def normalize_inline_bullet_markdown(text: str) -> str:
    """把段落内连写的「* 列表项」拆成标准 Markdown 列表行。

    模型常输出「如下： * 日期: … * 区域: …」单行；CommonMark 要求 ``*`` 位于行首，
    否则不会解析为 ``<ul>``，页面上会显示为一串带星号的正文。
    跳过 fenced code 块，避免误改示例代码。
    """
    if not text or "*" not in text:
        return text
    parts = re.split(r"(```[\s\S]*?```)", text)
    out: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            out.append(part)
            continue
        part = re.sub(r"([：:])\s+\*", r"\1\n\n*", part)
        part = re.sub(
            r"(?<![\n*])[ \t]+\*\s+(?=[\u4e00-\u9fffA-Za-z0-9（(])",
            "\n* ",
            part,
        )
        out.append(part)
    return "".join(out)


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

    content = normalize_inline_bullet_markdown(content)

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
        base = _clean_header_label(cell)
        if not base:
            base = f"col_{idx}"
        if base in used:
            used[base] += 1
            base = f"{base}_{used[base]}"
        else:
            used[base] = 1
        headers.append(base)
    return headers


def _clean_header_label(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    text = text.replace("\r", "").replace("\n", "")
    return re.sub(r"[ \t]+", " ", text)


def _row_non_empty_count(row: list) -> int:
    return sum(1 for c in row if c not in (None, ""))


def _row_numeric_count(row: list) -> int:
    return sum(1 for c in row if isinstance(c, int | float) and not isinstance(c, bool))


def _find_header_row_index(rows: list[list], max_scan: int = 5) -> int:
    """Pick the row most likely to be the real column header.

    Excel sheets often have one or more title / merged-cell rows before the
    actual headers.  The heuristic: among the first *max_scan* rows, the row
    with the highest count of non-empty cells is the header. When that row
    already looks data-heavy, use the previous row as the bottom header row.
    Ties go to the earlier row.
    """
    if not rows:
        return 0
    scan = min(len(rows), max_scan)
    best_idx, best_count = 0, 0
    for i in range(scan):
        count = _row_non_empty_count(rows[i])
        if count > best_count:
            best_count = count
            best_idx = i
    if (
        best_idx > 0
        and _row_numeric_count(rows[best_idx]) >= 2
        and _row_non_empty_count(rows[best_idx - 1]) >= 2
    ):
        return best_idx - 1
    return best_idx


def _header_start_index(rows: list[list], header_idx: int) -> int:
    if header_idx <= 0:
        return header_idx
    max_cols = max((len(r) for r in rows[:header_idx + 1]), default=0)
    if max_cols <= 0:
        return header_idx
    if _row_non_empty_count(rows[header_idx]) >= max_cols * 0.6:
        return header_idx
    start = header_idx
    while start > 0 and _row_non_empty_count(rows[start - 1]) > 1:
        start -= 1
    return start


def _normalize_header_rows(header_rows: list[list]) -> list[str]:
    if not header_rows:
        return []
    max_cols = max(len(r) for r in header_rows)
    clean_rows: list[list[str]] = []
    for row in header_rows:
        clean_rows.append([
            _clean_header_label(row[idx] if idx < len(row) else None)
            for idx in range(max_cols)
        ])
    filled_rows: list[list[str]] = []
    for row_idx, row in enumerate(clean_rows):
        filled: list[str] = []
        current = ""
        for idx in range(max_cols):
            value = row[idx]
            if value:
                current = value
                filled.append(value)
            elif any(prev[idx] for prev in clean_rows[:row_idx]):
                filled.append("")
            else:
                filled.append(current)
        filled_rows.append(filled)

    combined: list[str] = []
    for col in range(max_cols):
        parts: list[str] = []
        for row in filled_rows:
            value = row[col]
            if value and value not in parts:
                parts.append(value)
        combined.append(" ".join(parts))
    return _normalize_header_row(combined)


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
        header_start = _header_start_index(non_empty_rows, header_idx)
        header_rows = non_empty_rows[header_start:header_idx + 1]
        headers = (
            _normalize_header_rows(header_rows)
            if len(header_rows) > 1
            else _normalize_header_row(non_empty_rows[header_idx])
        )
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


_GLOSSARY_LINE_RE = re.compile(r"^(.{1,120}?)(?:\s*(?:=>|=|:|：)\s*)(.{1,800})$")


def parse_glossary_entries(text: str, *, max_entries: int = 200) -> list[dict]:
    """Parse repo-level glossary lines such as ``双模 = SVC + AVC``.

    The glossary is intentionally plain text so owners can maintain it from the
    settings page without learning YAML. Multiple aliases can be written on the
    left side with ``|`` / ``，`` / ``、`` separators.
    """
    if not text:
        return []
    entries: list[dict] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(">"):
            continue
        line = re.sub(r"^\s*[-*]\s+", "", line)
        match = _GLOSSARY_LINE_RE.match(line)
        if not match:
            continue
        lhs = match.group(1).strip()
        definition = re.sub(r"\s+", " ", match.group(2).strip())
        aliases = [
            part.strip()
            for part in re.split(r"[|,，、；;]", lhs)
            if part and part.strip()
        ]
        if not aliases or not definition:
            continue
        entries.append({
            "term": aliases[0],
            "aliases": aliases,
            "definition": definition,
        })
        if len(entries) >= max_entries:
            break
    return entries


def match_glossary_entries(question: str, entries: list[dict], *, max_matches: int = 8) -> list[dict]:
    """Return glossary entries whose term or alias appears in the query."""
    if not question or not entries:
        return []
    q = question.lower()
    matched: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        aliases = [str(a).strip() for a in entry.get("aliases") or [] if str(a).strip()]
        if not aliases:
            continue
        for alias in aliases:
            needle = alias.lower()
            if needle and needle in q:
                key = str(entry.get("term") or alias).lower()
                if key not in seen:
                    matched.append(entry)
                    seen.add(key)
                break
        if len(matched) >= max_matches:
            break
    return matched


def expand_query_with_glossary(question: str, matches: list[dict], *, max_chars: int = 800) -> str:
    """Append matched glossary definitions to a retrieval query.

    This keeps the user-visible question unchanged while giving dense/BM25/fact
    retrieval the vocabulary that may appear in source documents.
    """
    base = question or ""
    if not matches:
        return base
    parts: list[str] = []
    for item in matches:
        term = str(item.get("term") or "").strip()
        definition = str(item.get("definition") or "").strip()
        if term and definition:
            parts.append(f"{term} = {definition}")
    if not parts:
        return base
    suffix = "; ".join(parts)
    if len(suffix) > max_chars:
        suffix = suffix[:max_chars].rstrip() + "…"
    return f"{base}\n\n检索术语解释：{suffix}"


def build_glossary_context(matches: list[dict]) -> str:
    """Build the prompt context block for glossary matches."""
    if not matches:
        return ""
    lines = [
        "=== GLOSSARY ===",
        "以下为当前知识库设置的术语解释；用户问题包含这些术语时，优先按这里的含义理解。",
    ]
    for item in matches:
        term = str(item.get("term") or "").strip()
        definition = str(item.get("definition") or "").strip()
        if term and definition:
            lines.append(f"- {term} = {definition}")
    return "\n".join(lines) if len(lines) > 2 else ""


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
title: 知识库提示词
---

# 知识库提示词

这段内容会作为当前知识库的自定义提示词，参与文档摄入、Wiki 生成和问答回答。
你可以按自己的业务修改，但不要要求模型忽略系统安全规则或编造资料中不存在的信息。

## 回答规则

- 优先依据检索上下文和原始文档回答，不要凭空补全。
- 如果资料中没有直接证据，请明确说明“资料中未提及”或“现有证据不足”。
- 对型号、价格、参数、日期、折扣、适用范围等字段保持严格，不跨产品或跨版本套用。
- 如果问题涉及表格或清单，按原始记录逐行整理，避免把不同行字段拼成一条新记录。

## 输出风格

- 默认使用中文，结论先行，必要时给出简短依据。
- 能直接回答时不要绕弯；需要计算时写出关键公式。
- 不要在正文中写文件名或来源标注，证据由页面下方证据面板展示。
"""

SCHEMA_ACADEMIC_MD = """\
---
title: 知识库提示词 — 学术研究
---

# 知识库提示词 — 学术研究

## 回答规则

- 优先区分“论文原文结论”“实验结果”“作者观点”和“你的归纳”。
- 涉及指标、数据集、模型版本、实验设置时，必须使用资料中的明确字段。
- 对不确定、样本不足或只在单篇论文中出现的结论，标注证据强弱。
- 对比方法或模型时，按同一维度横向比较，不把 A 论文的实验条件套到 B 论文。

## 输出风格

- 先给摘要，再列依据和局限。
- 对比类问题优先使用表格。
- 结论不要过度外推，避免把相关性说成因果。
"""

SCHEMA_PRODUCT_MD = """\
---
title: 知识库提示词 — 产品文档
---

# 知识库提示词 — 产品文档

## 回答规则

- 产品型号、规格参数、价格、折扣、授权范围、停售/新增状态必须严格依据资料。
- 用户问“是否支持/是不是/能不能”时，先给明确结论，再列出命中的产品特性。
- 若术语解释中定义了业务含义，优先按该知识库内定义理解。
- 不要把其他型号、其他系列或旧版本的能力套用到当前型号。

## 输出风格

- 简洁、面向销售/售前/实施人员。
- 对价格或折扣计算，写出市场价、折扣和计算结果。
- 对配置清单或参数较多的问题，使用表格。
"""

SCHEMA_TECH_NOTES_MD = """\
---
title: 知识库提示词 — 技术笔记
---

# 知识库提示词 — 技术笔记

## 回答规则

- 优先给可执行步骤和判断依据。
- 涉及命令、配置、版本号、接口字段时，必须按资料原文。
- 排障问题按“现象、可能原因、验证方法、处理建议”组织。
- 如果资料缺少环境或版本信息，明确指出缺口。

## 输出风格

- 命令和配置使用代码块。
- 先给最小可行方案，再补充注意事项。
- 不确定时给出下一步验证命令，而不是武断结论。
"""

SCHEMA_TEMPLATES = {
    "default": ("通用问答", DEFAULT_SCHEMA_MD),
    "academic": ("学术研究", SCHEMA_ACADEMIC_MD),
    "product": ("产品/报价", SCHEMA_PRODUCT_MD),
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
