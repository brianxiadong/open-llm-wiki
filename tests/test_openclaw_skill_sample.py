from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "openclaw-skills" / "openllm-kb-search"
SKILL_FILE = SKILL_DIR / "SKILL.md"


def test_openllm_skill_is_lightweight_text_only():
    assert SKILL_FILE.exists()
    assert not (SKILL_DIR / "scripts").exists()


def test_openllm_skill_mentions_user_private_workspace_storage():
    text = SKILL_FILE.read_text(encoding="utf-8")
    assert "/api/v1/search" in text
    assert "/api/v1/repos" in text
    assert "/data/openclaw/workspace/<user-folder>/.openclaw/openllm-kb-search/token.env" in text
    assert "不要把 token 写到 `/data/openclaw/workspace/.openclaw/...`" in text
    assert "当前如果拿到的是共享根目录 `/data/openclaw/workspace/`，不要保存 token" in text


def test_openllm_skill_requires_using_api_answer_verbatim_for_tables():
    text = SKILL_FILE.read_text(encoding="utf-8")
    assert "优先直接使用接口返回的 `answer` 作为正文" in text
    assert "不能把主讲、内容、时间等字段重新总结或跨行拼接" in text
    assert "不要再补一大段总结" in text
    assert "如果当前请求来自快捷命令 `/kb`，最终回复应尽量短" in text


def test_kb_shortcut_requires_direct_return_mode():
    text = (ROOT / "openclaw-skills" / "kb" / "SKILL.md").read_text(encoding="utf-8")
    agent_text = (
        ROOT / "openclaw-skills" / "kb" / "agents" / "openai.yaml"
    ).read_text(encoding="utf-8")
    assert "快捷直返" in text
    assert "最终回复最多保留 1 行简短路由说明" in text
    assert "只允许输出该 `answer` 原文" in agent_text
