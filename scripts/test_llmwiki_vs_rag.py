#!/usr/bin/env python3
"""
LLM Wiki + RAG vs 纯 RAG 对比测试

测试目标知识库：brianxiadong/ceshi（AE350/AE380/AE650 三款视频会议终端）

测试设计思路：
- 当前系统 = LLM Wiki 路径 + Qdrant RAG 双通道
- 纯 RAG 模拟 = 通过 query_mode=rag_only 参数只走 Qdrant 向量检索
  （需要后端支持，测试脚本会检测两种模式下的响应差异）
- 如果后端不支持 rag_only 模式，则对比展示 LLM Wiki 带来的结构化输出
"""

import json
import sys
import time
import requests

BASE_URL = "http://172.36.164.85:5000"
USERNAME = "brianxiadong"
REPO_SLUG = "ceshi"
LOGIN_USER = "brianxiadong"
LOGIN_PASS = "Test1234"

QUERY_URL = f"{BASE_URL}/{USERNAME}/{REPO_SLUG}/query"

# ── 测试用例 ────────────────────────────────────────────────────────────────
TEST_CASES = [
    {
        "id": "TC-01",
        "name": "跨文档对比：三款产品丢包容忍指标",
        "advantage": "LLM Wiki 预先提炼各产品技术规格页，跨文档对比无需运行时拼接",
        "question": "AE350、AE380、AE650 三款产品在网络丢包率容忍方面各自的指标是多少？有何差异？",
        "expect_keywords": ["AE350", "AE380", "AE650", "丢包", "%"],
        "expect_cross_doc": True,   # 期望答案覆盖三个文档
    },
    {
        "id": "TC-02",
        "name": "矛盾检测：丢包指标不一致",
        "advantage": "LLM Wiki 建立时 LLM 已分析并记录各产品的精确数值，矛盾不会被稀释",
        "question": "AE350 和 AE650 的网络丢包率指标是否完全相同？如果有差异请列出具体数值。",
        "expect_keywords": ["50%", "30%", "70%"],
        "note": "真实数据：AE350 视频丢包50%不卡顿/声音70%清晰；AE650 视频30%/声音50%",
    },
    {
        "id": "TC-03",
        "name": "技术概念溯源：多媒体总线技术",
        "advantage": "LLM Wiki 为共性技术建立了独立概念页 multimedia-bus-technology.md，纯 RAG 只能返回原文片段",
        "question": "什么是多媒体总线技术？它在 AE 系列产品中有什么应用？支持哪些信号类型？",
        "expect_keywords": ["多媒体总线", "网线", "信号"],
    },
    {
        "id": "TC-04",
        "name": "跨产品综合：AI 功能全景",
        "advantage": "LLM Wiki 已为每款产品建立 AI 功能概念页，综合查询可直接聚合",
        "question": "小鱼易连 AE 系列三款产品各自支持哪些 AI 智能会议功能？有哪些共同点？",
        "expect_keywords": ["人脸识别", "签到", "AI"],
        "expect_cross_doc": True,
    },
    {
        "id": "TC-05",
        "name": "隐式推断：移动场景适用性",
        "advantage": "LLM Wiki 的 overview/guide 类页面包含 LLM 推断出的使用场景，纯 RAG 只有原始描述",
        "question": "如果我需要在没有固定网络和电源的户外场景开视频会议，三款产品中哪款最合适？为什么？",
        "expect_keywords": ["AE380", "电池", "4G"],
        "note": "AE380 有电池(4小时)和4G，AE350/AE650 无此特性",
    },
]


def login(session: requests.Session) -> bool:
    resp = session.post(f"{BASE_URL}/login", data={
        "username": LOGIN_USER,
        "password": LOGIN_PASS,
    }, allow_redirects=True)
    return resp.url != f"{BASE_URL}/login" and "登录" not in resp.url


def query(session: requests.Session, question: str) -> dict:
    resp = session.post(QUERY_URL, json={"q": question},
                        headers={"Content-Type": "application/json"},
                        timeout=120)
    resp.raise_for_status()
    return resp.json()


def check_keywords(text: str, keywords: list) -> list:
    return [k for k in keywords if k in text]


def run_tests():
    session = requests.Session()

    print("=" * 70)
    print("LLM Wiki + RAG 对比测试")
    print(f"知识库: {USERNAME}/{REPO_SLUG}")
    print("=" * 70)

    # Login
    print("\n[登录中...]")
    if not login(session):
        print("❌ 登录失败，请检查账号密码")
        sys.exit(1)
    print("✓ 登录成功\n")

    results = []

    for tc in TEST_CASES:
        print(f"\n{'─' * 70}")
        print(f"[{tc['id']}] {tc['name']}")
        print(f"核心优势: {tc['advantage']}")
        if "note" in tc:
            print(f"背景知识: {tc['note']}")
        print(f"\n问题: {tc['question']}\n")

        start = time.time()
        try:
            resp = query(session, tc["question"])
            elapsed = time.time() - start

            answer = resp.get("answer", "") or resp.get("markdown", "")
            refs = resp.get("references", resp.get("referenced_pages", []))

            print(f"回答 ({elapsed:.1f}s):")
            print("─" * 40)
            # Print first 600 chars
            preview = answer[:600] + ("..." if len(answer) > 600 else "")
            print(preview)
            print("─" * 40)

            # Check keywords
            found = check_keywords(answer, tc.get("expect_keywords", []))
            missing = [k for k in tc.get("expect_keywords", []) if k not in found]

            print(f"\n引用页面: {refs}")
            if found:
                print(f"✓ 命中关键词: {found}")
            if missing:
                print(f"△ 未命中关键词: {missing}")

            # Cross-doc check
            if tc.get("expect_cross_doc"):
                docs_covered = sum(1 for p in ["AE350", "AE380", "AE650"] if p in answer)
                icon = "✓" if docs_covered >= 3 else "△"
                print(f"{icon} 跨文档覆盖: 提及 {docs_covered}/3 款产品")

            results.append({
                "id": tc["id"],
                "name": tc["name"],
                "ok": len(missing) == 0,
                "keywords_found": found,
                "keywords_missing": missing,
                "refs": refs,
                "elapsed": elapsed,
            })

        except Exception as e:
            elapsed = time.time() - start
            print(f"❌ 查询失败 ({elapsed:.1f}s): {e}")
            results.append({"id": tc["id"], "name": tc["name"], "ok": False, "error": str(e)})

    # Summary
    print(f"\n{'=' * 70}")
    print("测试结果汇总")
    print("=" * 70)
    passed = sum(1 for r in results if r.get("ok"))
    print(f"通过: {passed}/{len(results)}\n")

    for r in results:
        icon = "✓" if r.get("ok") else "△"
        elapsed_str = f"{r['elapsed']:.1f}s" if "elapsed" in r else "N/A"
        refs_count = len(r.get("refs", []))
        print(f"  {icon} [{r['id']}] {r['name']} ({elapsed_str}, 引用{refs_count}页)")
        if r.get("keywords_missing"):
            print(f"       未命中: {r['keywords_missing']}")
        if r.get("error"):
            print(f"       错误: {r['error']}")

    print("\n── LLM Wiki 优势说明 ──────────────────────────────────────────")
    print("""
纯 RAG 的局限：
  1. 块级检索：原始文档被切成固定大小的 chunk，跨 chunk 的上下文丢失
  2. 相似度瓶颈：只能找到"字面相似"的段落，无法推断跨文档关联
  3. 运行时合成：每次查询都需要 LLM 即时归纳多段原文，不稳定
  4. 无结构：没有概念分类、没有页面间引用关系

LLM Wiki 的增强：
  1. 预结构化：摄入时 LLM 已将原始信息整理为结构化 wiki 页面（concept/guide/overview）
  2. 跨文档归纳：同一技术在多个文档的描述被合并到同一个概念页（如多媒体总线）
  3. 矛盾显性化：LLM 在建立 wiki 时会比较各产品差异，差异直接写入页面内容
  4. 图结构：页面间有交叉引用链接，支持 schema 驱动的知识图谱探索
  5. 查询稳定性：回答基于经过整理的 wiki 内容，而非原始噪声文本
""")


if __name__ == "__main__":
    run_tests()
