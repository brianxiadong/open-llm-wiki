"""AI 生成知识库描述：基于 wiki 内容给 Repo.description 出一条建议文本。

设计：
- 输入只看 KB 的实际内容（wiki pages 首选，raw 文件名降级），不看旧 description。
- 输出 80~200 字中文，客观第三人称，含主题 + 适用场景，不许罗列文件名、
  不许营销口吻、不许内嵌来源标注。
- 纯函数 + 依赖注入：sample_content() 返回结构化采样，generate_description()
  接 llm_client + 采样结果，方便单测。
"""

from __future__ import annotations

import logging
import os
from typing import Any

from utils import list_raw_sources, list_wiki_pages, render_markdown


logger = logging.getLogger(__name__)


# 单次生成的总字符预算：控制 prompt token，8k 字符 ≈ 4k~5k tokens
_MAX_CHARS_PER_PAGE = 400
_MAX_TOTAL_CHARS = 8000
_MAX_PAGES = 25
_MAX_RAW_FILES = 60


# ---------------------------------------------------------------------------
# 采样：wiki 页优先，原始文件降级
# ---------------------------------------------------------------------------


def _strip_frontmatter_and_head(md_text: str, limit: int) -> str:
    """去掉 frontmatter 与顶级标题，取正文前 ``limit`` 字符；清掉多余空行。"""
    fm, _ = render_markdown(md_text)
    body = md_text
    # 把顶部 --- frontmatter --- 切掉
    stripped = md_text.lstrip()
    if stripped.startswith("---"):
        parts = stripped.split("---", 2)
        if len(parts) >= 3:
            body = parts[2]
    # 去掉正文最前面的一级标题（与 title 重复）
    lines = [ln for ln in body.splitlines() if not ln.strip().startswith("```")]
    out: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not out and (not s or s.startswith("#")):
            continue
        if s.startswith("#"):
            out.append(s.lstrip("# ").strip())
            continue
        out.append(ln.rstrip())
    text = "\n".join(out).strip()
    text = " ".join(text.split())  # 折叠所有空白
    return text[:limit]


def sample_content(
    wiki_dir: str,
    raw_dir: str,
) -> dict:
    """采样 KB 内容用于描述生成。

    返回：
      ``{"pages": [{"title", "excerpt"}, ...],
         "raw_files": [{"filename"}, ...],
         "total_pages": int, "total_raw": int, "truncated": bool,
         "source": "wiki" | "raw" | "empty"}``
    """
    pages: list[dict] = []
    total_chars = 0
    truncated = False

    wiki_pages_meta = list_wiki_pages(wiki_dir) if os.path.isdir(wiki_dir) else []
    # 排除列表/索引页（如 schema.md / index.md），由 wiki frontmatter.type 判断
    content_pages = [p for p in wiki_pages_meta if p.get("type") not in {"index", "schema", "log"}]
    total_pages = len(content_pages)

    for meta in content_pages[:_MAX_PAGES]:
        fpath = os.path.join(wiki_dir, meta["filename"])
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                raw = f.read()
        except OSError:
            continue
        excerpt = _strip_frontmatter_and_head(raw, _MAX_CHARS_PER_PAGE)
        if not excerpt:
            continue
        if total_chars + len(excerpt) > _MAX_TOTAL_CHARS:
            truncated = True
            break
        pages.append({"title": meta.get("title") or meta["filename"], "excerpt": excerpt})
        total_chars += len(excerpt)

    if total_pages > _MAX_PAGES:
        truncated = True

    raw_files: list[dict] = []
    total_raw = 0
    if not pages:
        raw_list = list_raw_sources(raw_dir) if os.path.isdir(raw_dir) else []
        total_raw = len(raw_list)
        for r in raw_list[:_MAX_RAW_FILES]:
            raw_files.append({"filename": r.get("filename", "")})
        if total_raw > _MAX_RAW_FILES:
            truncated = True

    if pages:
        source = "wiki"
    elif raw_files:
        source = "raw"
    else:
        source = "empty"

    return {
        "pages": pages,
        "raw_files": raw_files,
        "total_pages": total_pages,
        "total_raw": total_raw,
        "truncated": truncated,
        "source": source,
    }


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = (
    "你是『知识库简介撰写助手』。根据给定的知识库内容片段，产出一段中文 description，"
    "用于在仓库列表、路由候选等场景帮助用户与系统快速理解该知识库的主题与适用范围。"
    "\n\n【严格要求】"
    "\n1. 长度 80~200 字中文；一个自然段，不使用标题、列表、Markdown。"
    "\n2. 客观第三人称；不得出现「我们」「本人」等主观称谓，也不要营销口吻（「首选」「业界领先」）。"
    "\n3. 内容须覆盖：主题领域 + 主要涉及的对象/产品/模块 + 典型使用场景或读者。"
    "\n4. 不得在正文中嵌入文件名、章节号、来源标注；也不要罗列文件清单。"
    "\n5. 信息不足时坦率简短（例如『该知识库目前资料较少』），不得凭空编造。"
    "\n6. 输出 JSON：{\"description\": \"……\"}；不要附加任何其他文本。"
)


def _build_user_prompt(repo_name: str, sample: dict) -> str:
    lines: list[str] = [f"知识库名称：{repo_name}"]
    source = sample["source"]
    if source == "wiki":
        lines.append(f"以下是该知识库的 {len(sample['pages'])} 份章节摘要（共 {sample['total_pages']} 页）：")
        for i, p in enumerate(sample["pages"], 1):
            lines.append(f"{i}. 《{p['title']}》\n   {p['excerpt']}")
    elif source == "raw":
        lines.append(
            f"该知识库尚未生成 wiki，现有原始文档 {sample['total_raw']} 份，文件名如下（仅供推断主题，不得罗列到 description）："
        )
        names = [r["filename"] for r in sample["raw_files"]]
        lines.append("  " + ", ".join(names))
    else:
        lines.append("（该知识库目前没有任何内容。）")
    lines.append("\n请根据以上材料撰写 description（严格按系统消息的 JSON 格式）。")
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def generate_description(
    llm_client: Any,
    *,
    repo_name: str,
    wiki_dir: str,
    raw_dir: str,
) -> dict:
    """生成描述建议。

    返回：
      ``{"ok": bool, "suggestion": str, "source": str,
         "source_pages_count": int, "source_raw_count": int,
         "truncated": bool, "error": str | None}``
    """
    sample = sample_content(wiki_dir, raw_dir)
    result = {
        "ok": False,
        "suggestion": "",
        "source": sample["source"],
        "source_pages_count": len(sample["pages"]),
        "source_raw_count": len(sample["raw_files"]),
        "total_pages": sample["total_pages"],
        "total_raw": sample["total_raw"],
        "truncated": sample["truncated"],
        "error": None,
    }
    if sample["source"] == "empty":
        result["error"] = "empty_knowledge_base"
        return result

    user_prompt = _build_user_prompt(repo_name, sample)
    try:
        raw = llm_client.chat_json(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
        ) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("description generator chat_json failed: %s", exc)
        result["error"] = "llm_error"
        return result

    suggestion = (raw.get("description") or "").strip()
    # 基本清洗：去掉首尾引号、折叠多余空白
    if suggestion.startswith(("“", "\"")):
        suggestion = suggestion.lstrip("“\"")
    if suggestion.endswith(("”", "\"")):
        suggestion = suggestion.rstrip("”\"")
    suggestion = " ".join(suggestion.split())

    if not suggestion:
        result["error"] = "empty_response"
        return result

    # 长度兜底：超过 260 字时尾部截断（避免生成过长）
    if len(suggestion) > 260:
        suggestion = suggestion[:256].rstrip("，,。.；;") + "…"

    result["ok"] = True
    result["suggestion"] = suggestion
    return result


__all__ = [
    "sample_content",
    "generate_description",
]
