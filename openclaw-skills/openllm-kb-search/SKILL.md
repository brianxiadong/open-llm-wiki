---
name: openllm-kb-search
description: Use when users ask OpenClaw to query internal Open LLM Wiki knowledge bases, especially when a token must be collected and saved first, when a knowledge base name is specified and should be routed explicitly, or when the question should rely on automatic repo routing.
---

# Open LLM Wiki Query

Use this skill for internal knowledge base Q&A backed by this project's `/api/v1/**` endpoints.

## Trigger conditions

- The user asks OpenClaw to query an internal knowledge base, wiki, product KB, or repository knowledge.
- The user provides or needs to provide an `ollw_...` API token.
- The user names a knowledge base explicitly, such as `owner/slug`, repo slug, or a human-readable repo name.
- The user asks a question without naming a repo and expects the system to auto-select the best knowledge base.

## Workflow

1. Check whether a token has already been saved.
   Run:
   ```bash
   python openclaw-skills/openllm-kb-search/scripts/query_openllm.py --check-token
   ```
2. If the token is missing or invalid, ask the user for a fresh `ollw_...` token and save it.
   Run:
   ```bash
   python openclaw-skills/openllm-kb-search/scripts/save_token.py --token 'ollw_...'
   ```
   The token is stored outside the repo in `$CODEX_HOME/openllm-kb-search/token.env` or `~/.codex/openllm-kb-search/token.env`.
3. If the user specifies a repo:
   - If they provide `owner/slug`, pass it with `--repo`.
   - If they provide only a repo name or slug, pass it with `--repo-name`; the script will fetch visible repos from `/api/v1/repos` and resolve the best explicit target.
   - If multiple repos tie, stop and ask the user to choose.
4. If the user does not specify a repo, call `/api/v1/search` without `repo` so the server can auto-route.
5. In the final answer, include:
   - the selected repo or that routing stayed automatic
   - confidence / trace id when present
   - the answer itself in concise prose

## Commands

Check saved token:

```bash
python openclaw-skills/openllm-kb-search/scripts/query_openllm.py --check-token
```

Save or overwrite token:

```bash
python openclaw-skills/openllm-kb-search/scripts/save_token.py --token 'ollw_...'
```

Query with automatic routing:

```bash
python openclaw-skills/openllm-kb-search/scripts/query_openllm.py \
  --question 'AE350的核心参数有哪些' \
  --pretty
```

Query with explicit repo full name:

```bash
python openclaw-skills/openllm-kb-search/scripts/query_openllm.py \
  --question 'AE350的核心参数有哪些' \
  --repo 'xiadong/performance' \
  --pretty
```

Query with repo name hint:

```bash
python openclaw-skills/openllm-kb-search/scripts/query_openllm.py \
  --question 'AE350的核心参数有哪些' \
  --repo-name '性能测试知识库' \
  --pretty
```

## Guardrails

- Never write the token into repo files, committed config, or user-facing summaries.
- Never echo the full token back in normal responses; only mention a short masked prefix if needed.
- On `401` or `invalid_token`, ask the user for a new token and re-save it.
- On `422` with routing failure or repo ambiguity, surface the candidates and ask the user which repo to use.
- Prefer the scripts in this skill instead of hand-writing curl commands each time.
