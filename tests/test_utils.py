"""Tests for utils.py."""

import os

import pytest

from utils import (
    ensure_repo_dirs,
    extract_links,
    get_backlinks,
    get_repo_path,
    list_raw_sources,
    list_wiki_pages,
    render_markdown,
    slugify,
)


def test_slugify():
    assert slugify("Hello World") == "hello-world"
    assert slugify("Test 123") == "test-123"
    assert slugify("  spaces  ") == "spaces"
    assert slugify("special!@#chars") == "specialchars"
    assert slugify("") == ""


def test_render_markdown_basic():
    fm, html = render_markdown("# Hello\n\nParagraph.")
    assert fm == {}
    assert "<h1" in html and "Hello" in html
    assert "<p" in html and "Paragraph" in html


def test_render_markdown_frontmatter():
    text = "---\ntitle: Test\ntype: concept\n---\n\n# Content"
    fm, html = render_markdown(text)
    assert fm == {"title": "Test", "type": "concept"}
    assert "<h1" in html and "Content" in html


def test_render_markdown_wiki_links():
    text = "[Link](page.md)"
    fm, html = render_markdown(text, wiki_base_url="/u/repo/wiki")
    assert fm == {}
    assert '/u/repo/wiki/page"' in html or 'href="/u/repo/wiki/page"' in html


def test_extract_links():
    assert extract_links("See [A](page-a.md) and [B](page-b.md)") == ["page-a", "page-b"]
    assert extract_links("no links here") == []


def test_get_backlinks(tmp_data_dir):
    wiki_dir = os.path.join(tmp_data_dir, "wiki_bt")
    os.makedirs(wiki_dir)
    a_path = os.path.join(wiki_dir, "page-a.md")
    b_path = os.path.join(wiki_dir, "page-b.md")
    with open(a_path, "w", encoding="utf-8") as f:
        f.write("---\ntitle: Page A\n---\n\nLink to [B](page-b.md)\n")
    with open(b_path, "w", encoding="utf-8") as f:
        f.write("---\ntitle: Page B\n---\n\nStandalone.\n")

    backlinks = get_backlinks(wiki_dir, "page-b.md")
    assert len(backlinks) == 1
    assert backlinks[0]["filename"] == "page-a.md"
    assert backlinks[0]["title"] == "Page A"


def test_ensure_repo_dirs(tmp_data_dir):
    base = ensure_repo_dirs(tmp_data_dir, "user1", "my-repo")
    assert base == os.path.join(tmp_data_dir, "user1", "my-repo")
    assert os.path.isdir(os.path.join(base, "raw", "assets"))
    assert os.path.isdir(os.path.join(base, "raw"))
    assert os.path.isdir(os.path.join(base, "wiki"))


def test_get_repo_path(tmp_data_dir):
    p = get_repo_path(tmp_data_dir, "alice", "kb")
    assert p == os.path.join(tmp_data_dir, "alice", "kb")


def test_list_wiki_pages(tmp_data_dir):
    wiki_dir = os.path.join(tmp_data_dir, "wiki_list")
    os.makedirs(wiki_dir)
    with open(os.path.join(wiki_dir, "one.md"), "w", encoding="utf-8") as f:
        f.write("---\ntitle: First\ntype: guide\nupdated: 2025-01-01\n---\n\n# One\n")
    with open(os.path.join(wiki_dir, "two.md"), "w", encoding="utf-8") as f:
        f.write("---\ntitle: Second\ntype: concept\n---\n\n# Two\n")

    pages = list_wiki_pages(wiki_dir)
    assert len(pages) == 2
    by_name = {p["filename"]: p for p in pages}
    assert by_name["one.md"]["title"] == "First"
    assert by_name["one.md"]["type"] == "guide"
    # YAML parses ISO dates as datetime.date objects
    assert str(by_name["one.md"]["updated"]) == "2025-01-01"
    assert by_name["two.md"]["title"] == "Second"
    assert by_name["two.md"]["type"] == "concept"


def test_list_wiki_pages_empty(tmp_data_dir):
    missing = os.path.join(tmp_data_dir, "no_such_wiki")
    assert list_wiki_pages(missing) == []


def test_list_raw_sources(tmp_data_dir):
    raw_dir = os.path.join(tmp_data_dir, "raw_src")
    os.makedirs(raw_dir)
    with open(os.path.join(raw_dir, "notes.md"), "w", encoding="utf-8") as f:
        f.write("# hi\n")
    with open(os.path.join(raw_dir, "data.bin"), "wb") as f:
        f.write(b"x" * 1024)

    sources = list_raw_sources(raw_dir)
    by_name = {s["filename"]: s for s in sources}
    assert set(by_name) == {"notes.md", "data.bin"}
    assert by_name["notes.md"]["is_markdown"] is True
    assert by_name["data.bin"]["is_markdown"] is False
    assert by_name["data.bin"]["size_kb"] == pytest.approx(1.0, rel=1e-3)


def test_list_raw_sources_skips_hidden_and_assets(tmp_data_dir):
    raw_dir = os.path.join(tmp_data_dir, "raw_skip")
    os.makedirs(raw_dir)
    os.makedirs(os.path.join(raw_dir, "assets"))
    with open(os.path.join(raw_dir, ".hidden"), "w", encoding="utf-8") as f:
        f.write("secret\n")
    with open(os.path.join(raw_dir, "visible.txt"), "w", encoding="utf-8") as f:
        f.write("ok\n")

    sources = list_raw_sources(raw_dir)
    names = [s["filename"] for s in sources]
    assert names == ["visible.txt"]


def test_schema_templates_in_utils():
    from utils import SCHEMA_TEMPLATES
    assert "default" in SCHEMA_TEMPLATES
    assert "academic" in SCHEMA_TEMPLATES
    assert len(SCHEMA_TEMPLATES) >= 3


def test_create_repo_with_academic_schema(auth_client, app):
    resp = auth_client.post(
        "/repos/new",
        data={
            "name": "Academic KB",
            "slug": "academic-kb",
            "description": "academic",
            "schema_template": "academic",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    with app.app_context():
        import os
        from config import Config
        schema_path = os.path.join(Config.DATA_DIR, "alice", "academic-kb", "wiki", "schema.md")
        assert os.path.exists(schema_path)
        content = open(schema_path).read()
        assert "学术研究" in content or "paper" in content
