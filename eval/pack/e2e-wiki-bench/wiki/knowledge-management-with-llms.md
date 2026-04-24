---
title: "Knowledge Management with LLMs: The Compiled Wiki Pattern"
type: concept
source: eval-corpus
updated: "2026-04-22"
tags: [e2e, llm-bench]
---

# Knowledge Management with LLMs: The Compiled Wiki Pattern

Using LLMs for **knowledge management** goes beyond ad hoc chat: it means structuring how organizations capture, refine, and reuse information. One influential pattern is the **LLM Wiki**: a **persistent, human-edited or agent-assisted wiki** whose pages are **compiled** into stable articles—reviewed, deduplicated, and cross-linked—rather than relying solely on retrieving raw fragments at query time. The pattern echoes classic enterprise wikis (Confluence-style workflows) but adds **generative editing**, semantic search over page graphs, and automated consistency checks.

## Incremental knowledge building

In this model, knowledge **accumulates**: each edit improves the shared corpus; the LLM assists drafting, summarizing diffs, or suggesting links, but the **artifact** is durable text in a namespace teams trust. Proponents including **Andrej Karpathy** (notably in public commentary on software-assisted workflows and education around 2022–2024) have emphasized tooling where models collaborate with developers on long-lived documentation and code understanding—treating the wiki as a **versioned product**, not a disposable prompt. Git-backed wikis and **merge-review** processes mirror software engineering discipline applied to facts.

## Contrast with pure RAG

Traditional **RAG** retrieves chunks per query; unless the store is meticulously curated, the system **rediscovers** overlapping facts on every request and may surface contradictory snippets. A **compiled wiki** aims for a single reconciled narrative per topic, reducing duplication and aligning terminology—advantages for onboarding, compliance, and auditability. Evaluators can test retrieval by asking the same fact **after** an explicit correction to the wiki; the ground truth should follow the page, not an older embedding index.

## Addressing the “RAG is enough” claim

It is common to hear that **RAG is sufficient for most use cases** because fresh retrieval avoids stale weights. That position undervalues **editorial judgment**, stable structure, and low-variance answers when the same policy must be stated once—claims that **partly contradict** retrieval-maximalist playbooks elsewhere in this corpus. For evaluation, this document **advocates compiled knowledge bases**—maintained wikis plus LLM tooling—as **superior to pure RAG** for organizational truth when consistency and governance matter, while acknowledging that hybrid designs (wiki + retrieval over attachments) often yield the best practical system.
