"""Unit tests for wiki_prompts (guard + comparison template + citation validation)."""

from __future__ import annotations

from wiki_prompts import (
    GUARD_SYSTEM_PROMPT,
    apply_citation_penalty,
    build_comparison_user_prompt,
    build_generic_user_prompt,
    classify_intent,
    compose_system_prompt,
    extract_cited_filenames,
    validate_citations,
)


# ---------------------------------------------------------------------------
# classify_intent
# ---------------------------------------------------------------------------


def test_classify_intent_comparison_keywords():
    assert classify_intent("AE350 和 AE650 的对比是什么") == "comparison"
    assert classify_intent("两款设备有什么区别") == "comparison"
    assert classify_intent("哪款更适合大会议室") == "comparison"
    assert classify_intent("AE350 vs AE650") == "comparison"
    assert classify_intent("这三款怎么选") == "comparison"


def test_classify_intent_generic_default():
    assert classify_intent("什么是 Wi-Fi 6") == "generic"
    assert classify_intent("AE350 的拾音距离是多少") == "generic"
    assert classify_intent("") == "generic"
    assert classify_intent(None) == "generic"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# compose_system_prompt
# ---------------------------------------------------------------------------


def test_compose_system_prompt_injects_guard_only_when_context():
    base = "你是一个 Wiki 助手。"

    injected = compose_system_prompt(base, enable_guard=True, has_context=True)
    assert base in injected
    assert "字段级严格" in injected
    assert "不跨实体传染" in injected

    # 无 context 时不注入（避免压抑纯生成任务）
    no_guard = compose_system_prompt(base, enable_guard=True, has_context=False)
    assert no_guard == base

    # 开关关闭时也不注入
    disabled = compose_system_prompt(base, enable_guard=False, has_context=True)
    assert disabled == base


def test_guard_system_prompt_contains_core_rules():
    """guard 必须覆盖 6 条核心规则（跨域通用性保证）。"""
    for key in (
        "字段级严格",
        "推理级允许",
        "无正文来源标注",
        "未知即未知",
        "不跨实体传染",
    ):
        assert key in GUARD_SYSTEM_PROMPT, f"guard 缺失规则：{key}"


def test_guard_forbids_inline_source_markers():
    """guard 文案必须显式禁止在正文中嵌入 `(来源: xxx.md)` / `(依据: xxx.md)` 等追溯标注。"""
    for forbidden in ("文件名", "手册名", "(来源:", "(依据:", "章节号"):
        assert forbidden in GUARD_SYSTEM_PROMPT, f"guard 未覆盖：{forbidden}"


# ---------------------------------------------------------------------------
# build_comparison_user_prompt / build_generic_user_prompt
# ---------------------------------------------------------------------------


def test_build_comparison_user_prompt_contains_structure_and_sources():
    prompt = build_comparison_user_prompt(
        question="AE350 和 AE650 的对比",
        context_block="=== 内容片段 ===",
        history_block="",
        allowed_sources=["ae350-overview.md", "ae650-overview.md"],
        query_mode="hybrid",
        min_dimensions=3,
    )
    assert "核心差异一览" in prompt
    assert "维度对比表" in prompt
    assert "选型建议" in prompt
    assert "资料缺口" in prompt
    assert "ae350-overview.md" in prompt
    assert "ae650-overview.md" in prompt
    assert "至少 3 个" in prompt
    assert "退化规则" in prompt


def test_build_comparison_user_prompt_forbids_inline_source_in_instructions():
    """对比模板指令必须禁止表格/正文中嵌入文件名。"""
    prompt = build_comparison_user_prompt(
        question="AE350 和 AE650 的对比",
        context_block="ctx",
        allowed_sources=["ae350-overview.md"],
        query_mode="hybrid",
    )
    # 不再要求表格最后一列为「来源」
    assert "表格最后一列为" not in prompt
    # 必须显式禁止在表格 / 正文中出现文件名
    assert "【不要】出现文件名" in prompt or "不要】出现文件名" in prompt
    # 清单区提示 LLM 不要在答案正文写文件名
    assert "请勿" in prompt and ("文件名" in prompt)


def test_build_comparison_user_prompt_handles_no_sources():
    prompt = build_comparison_user_prompt(
        question="A vs B",
        context_block="",
        allowed_sources=[],
        query_mode="hybrid",
    )
    assert "无命中来源" in prompt


def test_build_generic_user_prompt_basic():
    prompt = build_generic_user_prompt(
        question="什么是 X",
        context_block="some context",
        allowed_sources=["a.md"],
        query_mode="narrative",
    )
    assert "命中资料清单" in prompt
    assert "a.md" in prompt
    assert "narrative" in prompt
    # guard 指令要求 LLM 不在正文写文件名
    assert "请勿" in prompt and "文件名" in prompt


# ---------------------------------------------------------------------------
# extract_cited_filenames / validate_citations
# ---------------------------------------------------------------------------


def test_extract_cited_filenames_picks_multiple_extensions():
    text = (
        "依据 ae350-overview.md 中的描述，详见 AE350产品描述.docx 与 specs.xlsx。"
        "另见 handbook.pdf。"
    )
    cited = extract_cited_filenames(text)
    assert "ae350-overview.md" in cited
    assert "AE350产品描述.docx" in cited
    assert "specs.xlsx" in cited
    assert "handbook.pdf" in cited


def test_validate_citations_flags_unknown():
    answer = "详见 ae350-overview.md 和 fabricated-manual.md"
    allowed = {"ae350-overview.md", "ae650-overview.md"}
    v = validate_citations(answer, allowed)
    assert v["ok"] is False
    assert "fabricated-manual.md" in v["unknown"]
    assert "ae350-overview.md" in v["cited"]


def test_validate_citations_ok_when_all_allowed():
    answer = "依据 ae350-overview.md，拾音距离为 5 米。"
    allowed = {"ae350-overview.md"}
    v = validate_citations(answer, allowed)
    assert v["ok"] is True
    assert v["unknown"] == []


def test_validate_citations_empty_answer():
    v = validate_citations("", {"a.md"})
    assert v["ok"] is True
    assert v["cited"] == []


# ---------------------------------------------------------------------------
# apply_citation_penalty
# ---------------------------------------------------------------------------


def test_apply_citation_penalty_downgrades_score_and_level():
    conf = {"score": 0.80, "level": "high", "reasons": ["命中多页"]}
    v = {"cited": ["a.md", "b.md"], "unknown": ["b.md"], "ok": False}
    apply_citation_penalty(conf, v, penalty=0.25)
    assert conf["score"] == 0.55
    assert conf["level"] == "medium"
    assert any("疑似编造" in r for r in conf["reasons"])


def test_apply_citation_penalty_drops_to_low_when_severe():
    conf = {"score": 0.50, "level": "medium", "reasons": []}
    v = {"cited": ["x.md"], "unknown": ["x.md"], "ok": False}
    apply_citation_penalty(conf, v, penalty=0.30)
    assert conf["score"] == 0.20
    assert conf["level"] == "low"


def test_apply_citation_penalty_no_op_when_ok():
    conf = {"score": 0.80, "level": "high", "reasons": ["r"]}
    v = {"cited": ["a.md"], "unknown": [], "ok": True}
    result = apply_citation_penalty(conf, v, penalty=0.25)
    assert result["score"] == 0.80
    assert result["level"] == "high"
    assert result["reasons"] == ["r"]


def test_apply_citation_penalty_does_not_go_negative():
    conf = {"score": 0.10, "level": "low", "reasons": []}
    v = {"cited": ["a.md"], "unknown": ["a.md"], "ok": False}
    apply_citation_penalty(conf, v, penalty=0.50)
    assert conf["score"] == 0.0
    assert conf["level"] == "low"
