"""Prompt guard 与对比模板 —— 跨域通用的生成层保护。

本模块纯函数，不依赖任何业务对象，方便单测与跨仓库复用。包含：

- ``GUARD_SYSTEM_PROMPT``：字段级严格 + 无正文来源 + 不跨实体传染的系统指令
- ``classify_intent``：基于关键词的轻量意图识别（comparison vs generic）
- ``build_comparison_user_prompt`` / ``build_generic_user_prompt``：两种 user prompt
- ``validate_citations`` + ``apply_citation_penalty``：answer 生成后的引用合法性后校验

设计原则：证据可追溯性由 UI 侧的 ``wiki_evidence`` / ``chunk_evidence`` 面板统一承担，
答案正文**不再**嵌入「(来源: xxx.md)」这类追溯标注，保证阅读体验自然。
"""

from __future__ import annotations

import re


GUARD_SYSTEM_PROMPT = (
    "【回答规则（严格遵守）】\n"
    "1. 字段级严格：具体数值、型号、参数、日期、人名、功能清单、协议/接口、"
    "规格项等事实性字段，必须且只能使用下方『检索上下文』中出现的内容；"
    "上下文未涉及的字段一律写「资料中未提及」，绝不跨实体/版本/条目推断"
    "（不同产品、不同条款、不同版本之间的字段不得互相套用）。\n"
    "2. 推理级允许：对概念解释、差异总结、共性提炼、建议性结论，"
    "允许基于上下文做合理推断。\n"
    "3. 无正文来源标注：答案正文中【禁止】嵌入文件名、手册名、表名、章节号、"
    "行号、sheet 名等来源信息（例如 `xxx.md`、`xxx.docx`、「第 X 章」、"
    "「Sheet1 第 N 行」、「(来源: …)」、「(依据: …)」）。"
    "需要指代时使用「该资料」「上述文档」「相关章节」这类代词；"
    "证据追溯由前端的证据面板统一展示。\n"
    "4. 未知即未知：若某字段或维度在上下文中不存在，直接写「资料中未提及」，"
    "不要为了篇幅编造、不要用通用常识补全。\n"
    "5. 不跨实体传染：问题涉及多个实体（产品对比、方案对比、条款对比等）时，"
    "每条字段只能归属其真实来源实体，绝不把 A 的属性当作 B 的属性。\n"
    "6. 上下文为空或严重不足时：坦率写「当前知识库中缺少直接证据」，不要编造。\n"
)


_COMPARISON_CUES = (
    "对比", "比较", "区别", "差异", "异同", "优缺点",
    "哪个更", "哪款更", "哪个好", "哪款好",
    "选哪", "怎么选", "选型",
    " vs ", " vs.", "vs ", " vs",
)


def classify_intent(question: str) -> str:
    """轻量意图识别：命中关键词则归为 ``comparison``，否则 ``generic``。

    不走 LLM，保证 latency / 成本稳定；误分类由对比模板内部的退化规则兜底。
    """
    if not question:
        return "generic"
    q = question.strip().lower()
    for cue in _COMPARISON_CUES:
        if cue.strip().lower() in q:
            return "comparison"
    return "generic"


_CITATION_PATTERN = re.compile(
    r"([A-Za-z0-9_\-\u4e00-\u9fff]+\.(?:md|docx|doc|pdf|xlsx|xls|pptx|ppt|txt|csv))"
)


def extract_cited_filenames(answer: str) -> list[str]:
    """从 answer 里抽取形如 ``xxx.md`` / ``xxx.docx`` 的文件名引用。

    即使我们已经指示 LLM 不要在正文中写文件名，此函数仍作为兜底：万一 LLM
    违反指令写了文件名，走 ``validate_citations`` 可以识别是否编造。
    """
    if not answer:
        return []
    return sorted(set(_CITATION_PATTERN.findall(answer)))


def validate_citations(answer: str, allowed: set[str]) -> dict:
    """校验 answer 里引用的文件名是否都在命中来源集合 ``allowed`` 中。

    返回 ``{"cited": [...], "unknown": [...], "ok": bool}``。
    ``allowed`` 通常应包含 ``wiki_evidence.filename``、``chunk_evidence.filename``、
    ``fact_evidence.source_file``、``fact_evidence.source_markdown_filename``。

    注：guard 要求 LLM 不在正文里写文件名；``cited`` 通常应为空列表。
    若不为空且其中存在非法项，说明 LLM 既违规又编造，属于需要降级的信号。
    """
    cited = extract_cited_filenames(answer)
    unknown = [c for c in cited if c not in allowed]
    return {"cited": cited, "unknown": unknown, "ok": not unknown}


def apply_citation_penalty(confidence: dict, validation: dict, penalty: float) -> dict:
    """发现非法来源时对 confidence 扣分并重算 level；返回同一 dict（就地修改）。"""
    if not confidence or validation.get("ok", True):
        return confidence
    unknown = validation.get("unknown") or []
    if not unknown:
        return confidence
    new_score = max(0.0, round(float(confidence.get("score", 0.0)) - float(penalty), 2))
    confidence["score"] = new_score
    reasons = list(confidence.get("reasons", []))
    preview = ", ".join(unknown[:3])
    reasons.append(f"发现 {len(unknown)} 处疑似编造来源：{preview}")
    confidence["reasons"] = reasons
    if new_score >= 0.75:
        confidence["level"] = "high"
    elif new_score >= 0.45:
        confidence["level"] = "medium"
    else:
        confidence["level"] = "low"
    return confidence


def _history_block(history_block: str) -> str:
    if not history_block:
        return ""
    text = history_block.rstrip()
    if not text:
        return ""
    return text + "\n\n"


_SOURCES_HINT_LINE = (
    "（以下清单仅供你判断信息覆盖范围；"
    "【请勿】在答案正文中写出任何文件名或手册名——证据由前端独立展示。）"
)


def build_comparison_user_prompt(
    *,
    question: str,
    context_block: str,
    history_block: str = "",
    allowed_sources: list[str] | None = None,
    query_mode: str = "hybrid",
    min_dimensions: int = 3,
) -> str:
    """对比 / 选型类问题的动态模板；维度不足时 LLM 内部退化为要点回答。"""
    sources = allowed_sources or []
    sources_line = ", ".join(sources) if sources else "（无命中来源）"
    return (
        "用户正在比较多个实体（产品 / 方案 / 条款 / 版本等）。请按以下结构输出 Markdown：\n\n"
        "### 1. 核心差异一览\n"
        "- 用 1~2 行概括每个实体最关键的差异点（不超过 4 条）。\n\n"
        "### 2. 维度对比表\n"
        f"- 从『检索上下文』中【动态归纳】至少 {min_dimensions} 个对该问题有价值的对比维度（最多 8 个）。\n"
        "- 用 Markdown 表格；每个单元格仅填来自上下文的事实；"
        "上下文未提及的单元格必须写「资料中未提及」。\n"
        "- 表格中【不要】出现文件名、手册名、章节号、行号等来源信息；"
        "证据由前端证据面板独立展示。\n"
        "- 严禁把 A 实体的字段填到 B 实体的单元格。\n\n"
        "### 3. 选型建议（可选）\n"
        "- 仅当上下文里出现明确的应用场景、产品定位、适用规模等描述时才写。\n"
        "- 句式示例：「面向 <场景/规模> 的需求，建议选 <实体>，"
        "因其 <上下文中的对应定位/能力描述>」。\n"
        "- 不要在句中嵌入文件名、手册名；上下文里没有选型描述时直接省略这一节，不要编造。\n\n"
        "### 4. 资料缺口\n"
        "- 列出用户问题可能关心、但上下文未涉及的维度；若无缺口，写「无明显缺口」。\n\n"
        "### 退化规则\n"
        f"- 若你能从上下文真实归纳出的维度数 < {min_dimensions}，请只输出第 1、4 两节，"
        "并在开头加一句「（资料维度不足，已退化为要点回答）」。\n\n"
        + _history_block(history_block)
        + f"--- 问题 ---\n{question}\n\n"
        + f"--- 检索上下文 ---\n{context_block}\n\n"
        + f"--- 命中资料清单 ---\n{sources_line}\n"
        + _SOURCES_HINT_LINE + "\n\n"
        + f"（问题类型：{query_mode}；必须遵守系统规则中的"
        "『字段级严格』『无正文来源标注』『不跨实体传染』。）"
    )


def build_generic_user_prompt(
    *,
    question: str,
    context_block: str,
    history_block: str = "",
    allowed_sources: list[str] | None = None,
    query_mode: str = "hybrid",
) -> str:
    """通用（非对比）问题的 user prompt。"""
    sources = allowed_sources or []
    sources_line = ", ".join(sources) if sources else "（无命中来源）"
    return (
        "请根据『检索上下文』回答用户问题，使用 Markdown 格式。\n"
        f"问题类型：{query_mode}。\n"
        "- 命中结构化事实时，优先给出精确字段值；但【不要】在正文里写出表名、行号、"
        "文件名、手册名等来源信息；\n"
        "- 无直接证据时，使用「基于现有资料只能推测到」"
        "「现有证据不足以支持更确定的结论」"
        "或「当前知识库中缺少直接证据」等提示，不要编造；\n"
        "- 证据追溯由前端证据面板统一展示。\n\n"
        + _history_block(history_block)
        + f"--- 问题 ---\n{question}\n\n"
        + f"--- 检索上下文 ---\n{context_block}\n\n"
        + f"--- 命中资料清单 ---\n{sources_line}\n"
        + _SOURCES_HINT_LINE + "\n"
    )


def compose_system_prompt(base_system: str, *, enable_guard: bool, has_context: bool) -> str:
    """把基础 system prompt 与 guard 指令合并。

    只有在启用 guard 且有 context 时才注入；其他情况保持原 system，避免压抑生成。
    """
    if enable_guard and has_context:
        return f"{base_system.rstrip()}\n\n{GUARD_SYSTEM_PROMPT}"
    return base_system
