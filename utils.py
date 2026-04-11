"""Utility functions for Open-LLM-Wiki."""

from __future__ import annotations

import os
import re

import markdown as md_lib
import yaml
from markdown.extensions.codehilite import CodeHiliteExtension
from markdown.extensions.fenced_code import FencedCodeExtension
from markdown.extensions.tables import TableExtension
from markdown.extensions.toc import TocExtension


def slugify(text: str) -> str:
    """Convert text to URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text.strip("-")


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
