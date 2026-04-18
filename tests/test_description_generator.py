"""description_generator.sample_content / generate_description 纯单测。"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from description_generator import generate_description, sample_content


# ---------------------------------------------------------------------------
# sample_content
# ---------------------------------------------------------------------------


def _write_wiki_page(wiki_dir: str, filename: str, title: str, body: str, type_: str = "page") -> None:
    os.makedirs(wiki_dir, exist_ok=True)
    path = os.path.join(wiki_dir, filename)
    content = f"---\ntitle: {title}\ntype: {type_}\n---\n\n# {title}\n\n{body}\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _write_raw_file(raw_dir: str, filename: str, body: str = "raw content") -> None:
    os.makedirs(raw_dir, exist_ok=True)
    with open(os.path.join(raw_dir, filename), "w", encoding="utf-8") as f:
        f.write(body)


def test_sample_content_empty_when_no_dirs(tmp_data_dir):
    s = sample_content(
        os.path.join(tmp_data_dir, "missing-wiki"),
        os.path.join(tmp_data_dir, "missing-raw"),
    )
    assert s["source"] == "empty"
    assert s["pages"] == []
    assert s["raw_files"] == []


def test_sample_content_prefers_wiki_over_raw(tmp_data_dir):
    wiki = os.path.join(tmp_data_dir, "wiki")
    raw = os.path.join(tmp_data_dir, "raw")
    _write_wiki_page(wiki, "ae350.md", "AE350 产品介绍", "AE350 是小鱼的新一代会议终端，支持 PoE 与 4K。")
    _write_raw_file(raw, "ae350-specifications.md", "specs")

    s = sample_content(wiki, raw)
    assert s["source"] == "wiki"
    assert s["total_pages"] == 1
    assert len(s["pages"]) == 1
    # excerpt 不应含 frontmatter 或标题行
    excerpt = s["pages"][0]["excerpt"]
    assert "title:" not in excerpt
    assert "---" not in excerpt
    assert "AE350" in excerpt
    assert s["raw_files"] == []  # wiki 够用就不降级


def test_sample_content_falls_back_to_raw(tmp_data_dir):
    wiki = os.path.join(tmp_data_dir, "wiki_empty")
    raw = os.path.join(tmp_data_dir, "raw_only")
    # wiki 里只有 index/schema 不算内容页
    _write_wiki_page(wiki, "index.md", "索引", "...", type_="index")
    _write_wiki_page(wiki, "schema.md", "Schema", "...", type_="schema")
    _write_raw_file(raw, "doc-a.pdf", "aaa")
    _write_raw_file(raw, "doc-b.docx", "bbb")

    s = sample_content(wiki, raw)
    assert s["source"] == "raw"
    assert s["pages"] == []
    assert {r["filename"] for r in s["raw_files"]} == {"doc-a.pdf", "doc-b.docx"}


def test_sample_content_skips_system_pages(tmp_data_dir):
    wiki = os.path.join(tmp_data_dir, "wiki_sys")
    raw = os.path.join(tmp_data_dir, "raw_sys")
    _write_wiki_page(wiki, "index.md", "索引", "索引内容", type_="index")
    _write_wiki_page(wiki, "schema.md", "Schema", "schema 内容", type_="schema")
    _write_wiki_page(wiki, "log.md", "Log", "log 内容", type_="log")
    _write_wiki_page(wiki, "ae350.md", "AE350", "AE350 终端介绍，适合会议室")
    os.makedirs(raw, exist_ok=True)

    s = sample_content(wiki, raw)
    titles = [p["title"] for p in s["pages"]]
    assert titles == ["AE350"]


def test_sample_content_truncates_when_pages_exceed_max(tmp_data_dir):
    wiki = os.path.join(tmp_data_dir, "wiki_big")
    raw = os.path.join(tmp_data_dir, "raw_big")
    for i in range(40):
        _write_wiki_page(wiki, f"p{i:02d}.md", f"Page {i}", f"Body content {i} " * 3)
    os.makedirs(raw, exist_ok=True)

    s = sample_content(wiki, raw)
    assert s["total_pages"] == 40
    assert len(s["pages"]) <= 25
    assert s["truncated"] is True


# ---------------------------------------------------------------------------
# generate_description
# ---------------------------------------------------------------------------


def _mock_llm(return_value=None, raise_exc=None):
    m = MagicMock()
    if raise_exc is not None:
        m.chat_json.side_effect = raise_exc
    else:
        m.chat_json.return_value = return_value
    return m


def test_generate_description_empty_kb_returns_error(tmp_data_dir):
    llm = _mock_llm()
    r = generate_description(
        llm,
        repo_name="Empty KB",
        wiki_dir=os.path.join(tmp_data_dir, "nowiki"),
        raw_dir=os.path.join(tmp_data_dir, "noraw"),
    )
    assert r["ok"] is False
    assert r["error"] == "empty_knowledge_base"
    assert llm.chat_json.call_count == 0  # 空 KB 不该调 LLM


def test_generate_description_success(tmp_data_dir):
    wiki = os.path.join(tmp_data_dir, "wiki")
    _write_wiki_page(wiki, "a.md", "AE350 概览", "AE350 是小鱼的会议终端。")

    llm = _mock_llm(
        return_value={"description": "本知识库聚焦小鱼 AE350 会议终端的产品资料，涵盖硬件参数与使用说明，面向售前与运维读者。"}
    )
    r = generate_description(
        llm,
        repo_name="AE350 KB",
        wiki_dir=wiki,
        raw_dir=os.path.join(tmp_data_dir, "raw"),
    )
    assert r["ok"] is True
    assert "AE350" in r["suggestion"]
    assert r["source"] == "wiki"
    assert r["source_pages_count"] == 1


def test_generate_description_strips_quotes_and_whitespace(tmp_data_dir):
    wiki = os.path.join(tmp_data_dir, "wiki_q")
    _write_wiki_page(wiki, "a.md", "A", "内容 A")
    llm = _mock_llm(
        return_value={"description": "  \"围绕 A 的知识汇总\"  "}
    )
    r = generate_description(
        llm,
        repo_name="K",
        wiki_dir=wiki,
        raw_dir=os.path.join(tmp_data_dir, "raw_q"),
    )
    assert r["ok"] is True
    assert r["suggestion"] == "围绕 A 的知识汇总"


def test_generate_description_truncates_overlong_output(tmp_data_dir):
    wiki = os.path.join(tmp_data_dir, "wiki_long")
    _write_wiki_page(wiki, "a.md", "A", "内容")
    llm = _mock_llm(return_value={"description": "甲" * 400})
    r = generate_description(
        llm,
        repo_name="K",
        wiki_dir=wiki,
        raw_dir=os.path.join(tmp_data_dir, "raw_long"),
    )
    assert r["ok"] is True
    assert len(r["suggestion"]) <= 260
    assert r["suggestion"].endswith("…")


def test_generate_description_empty_llm_response(tmp_data_dir):
    wiki = os.path.join(tmp_data_dir, "wiki_e")
    _write_wiki_page(wiki, "a.md", "A", "内容")
    llm = _mock_llm(return_value={"description": ""})
    r = generate_description(
        llm,
        repo_name="K",
        wiki_dir=wiki,
        raw_dir=os.path.join(tmp_data_dir, "raw_e"),
    )
    assert r["ok"] is False
    assert r["error"] == "empty_response"


def test_generate_description_llm_exception(tmp_data_dir):
    wiki = os.path.join(tmp_data_dir, "wiki_x")
    _write_wiki_page(wiki, "a.md", "A", "内容")
    llm = _mock_llm(raise_exc=RuntimeError("boom"))
    r = generate_description(
        llm,
        repo_name="K",
        wiki_dir=wiki,
        raw_dir=os.path.join(tmp_data_dir, "raw_x"),
    )
    assert r["ok"] is False
    assert r["error"] == "llm_error"
