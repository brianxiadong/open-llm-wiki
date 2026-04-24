"""Tests for utils.py."""

import os

import pytest

from utils import (
    _find_header_row_index,
    _format_deploy_revision_file,
    build_tabular_markdown_and_records,
    classify_query_mode,
    ensure_repo_dirs,
    extract_links,
    get_app_revision,
    get_backlinks,
    utc_to_local,
    get_repo_path,
    list_raw_sources,
    list_wiki_pages,
    normalize_inline_bullet_markdown,
    render_markdown,
    safe_upload_basename,
    slugify,
)


def test_get_app_revision_prefers_env(monkeypatch):
    monkeypatch.setenv("APP_REVISION", "env-override-9")
    assert get_app_revision() == "env-override-9"


def test_format_deploy_revision_file_one_line():
    assert _format_deploy_revision_file("abc1234\n\n") == "abc1234"


def test_format_deploy_revision_file_sha_and_timestamp():
    raw = "abc1234\n2026-04-22 16:00:00 +0800\n"
    assert _format_deploy_revision_file(raw) == "abc1234 · 2026-04-22 16:00:00 +0800"


def test_utc_to_local_interprets_naive_as_utc():
    from datetime import datetime

    loc = utc_to_local(datetime(2026, 1, 1, 0, 0, 0))
    assert loc.hour == 8
    assert loc.utcoffset().total_seconds() == 8 * 3600


def test_slugify():
    assert slugify("Hello World") == "hello-world"
    assert slugify("Test 123") == "test-123"
    assert slugify("  spaces  ") == "spaces"
    assert slugify("special!@#chars") == "specialchars"
    assert slugify("") == ""


def test_safe_upload_basename_preserves_cjk():
    assert safe_upload_basename("0--中文--250930.docx") == "0--中文--250930.docx"
    assert safe_upload_basename("报告 v2（终稿）.md") == "报告 v2（终稿）.md"


def test_safe_upload_basename_strips_path_and_forbidden():
    assert safe_upload_basename("../../etc/passwd") == "passwd"
    # basename is last segment; forbidden chars become underscores
    assert safe_upload_basename("a/b:c*d?.txt") == "b_c_d_.txt"
    assert safe_upload_basename(None) == ""
    assert safe_upload_basename("..") == ""


def test_normalize_inline_bullet_markdown_leaves_code_fences():
    raw = "```\n* line in code\n```\n\n说明： * 日期: 1 * 区域: 东北"
    norm = normalize_inline_bullet_markdown(raw)
    assert "```\n* line in code\n```" in norm
    assert "说明：\n\n* 日期:" in norm
    assert "\n* 区域:" in norm


def test_render_markdown_inline_star_bullets_become_list():
    text = (
        "该条记录的具体运营指标如下： * 日期: 2025/7/27 * 区域: 东北 "
        "* 满意度: 91.3 * 是否故障: 否"
    )
    fm, html = render_markdown(text)
    assert fm == {}
    assert "<ul" in html
    assert "<li" in html
    assert "日期" in html and "东北" in html and "91.3" in html


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
    assert os.path.isdir(os.path.join(base, "facts", "records"))


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


def test_build_tabular_markdown_and_records():
    markdown, records = build_tabular_markdown_and_records(
        source_filename="sales.xlsx",
        source_markdown_filename="sales.md",
        tables=[
            {
                "name": "Q4 Revenue",
                "rows": [
                    ["地区", "收入", "增长率"],
                    ["华东", 1200, "12%"],
                    ["华南", 980, "8%"],
                ],
            }
        ],
    )

    assert "# sales" in markdown
    assert "## Sheet: Q4 Revenue" in markdown
    assert "| 地区 | 收入 | 增长率 |" in markdown
    assert len(records) == 2
    assert records[0]["source_file"] == "sales.xlsx"
    assert records[0]["source_markdown_filename"] == "sales.md"
    assert records[0]["sheet"] == "Q4 Revenue"
    assert records[0]["row_index"] == 2
    assert records[0]["fields"] == {"地区": "华东", "收入": 1200, "增长率": "12%"}
    assert "地区=华东" in records[0]["fact_text"]
    assert "收入=1200" in records[0]["fact_text"]


def test_find_header_row_index_skips_title_rows():
    rows = [
        ["主流 LLM 发布时间线（2022-2025）", None, None, None, None, None, None],
        ["发布日期", "模型名称", "公司", "参数量", "开源", "里程碑意义", "训练数据量(T tokens)"],
        ["2023-07-18", "LLaMA 2", "Meta", "70B", "是", "商业可用", 2],
    ]
    assert _find_header_row_index(rows) == 1


def test_find_header_row_index_double_title():
    rows = [
        ["2025 主流大语言模型综合评测得分表", None, None, None],
        ["基本信息", None, "推理与知识", None],
        ["模型名称", "公司", "MMLU", "HumanEval"],
        ["GPT-4o", "OpenAI", 88.7, 90.2],
    ]
    assert _find_header_row_index(rows) == 2


def test_find_header_row_index_no_title():
    rows = [
        ["地区", "收入", "增长率"],
        ["华东", 1200, "12%"],
    ]
    assert _find_header_row_index(rows) == 0


def test_build_tabular_with_title_row():
    """Excel with a merged title row → title row skipped, correct headers used."""
    markdown, records = build_tabular_markdown_and_records(
        source_filename="llm.xlsx",
        tables=[
            {
                "name": "发布时间线",
                "rows": [
                    ["主流 LLM 发布时间线", None, None, None],
                    ["发布日期", "模型名称", "公司", "训练数据量(T tokens)"],
                    ["2023-07-18", "LLaMA 2", "Meta", 2],
                    ["2024-04-18", "Llama 3", "Meta", 15],
                ],
            }
        ],
    )
    assert "| 发布日期 | 模型名称 | 公司 | 训练数据量(T tokens) |" in markdown
    assert "主流 LLM 发布时间线" not in markdown.split("## Sheet")[1].split("|---")[0].replace("Sheet: 发布时间线", "")
    assert len(records) == 2
    assert records[0]["fields"]["模型名称"] == "LLaMA 2"
    assert records[0]["fields"]["训练数据量(T tokens)"] == 2
    assert "训练数据量(T tokens)=2" in records[0]["fact_text"]
    assert "模型名称=LLaMA 2" in records[0]["fact_text"]


def test_classify_query_mode_prefers_fact_for_exact_lookup():
    assert classify_query_mode("华东地区 2024Q4 收入是多少？") == "fact"
    assert classify_query_mode("总结一下 2024Q4 市场趋势") == "narrative"
    assert classify_query_mode("对比一下华东和华南的收入，并总结差异") == "hybrid"


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
