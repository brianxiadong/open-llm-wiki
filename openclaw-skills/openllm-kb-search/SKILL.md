---
name: openllm-kb-search
description: 内部知识库检索技能。用于处理“内部知识库 / 公司知识库 / 文档库 / 资料库 / 知识问答”等请求，通过 Open LLM Wiki 的 `/api/v1/search` 对外接口回答用户问题；若用户尚未配置 `ollw_...` token，需要先向用户索取并保存；若用户指定了知识库名则显式命中该知识库，未指定时走服务端自动知识库路由。
---

# 内部知识库检索

用于通过 Open LLM Wiki 的 `/api/v1/**` 接口回答内部知识库问题。

## 触发条件

- 用户要求查询内部知识库、公司知识库、Wiki、产品资料库、项目知识库、文档库、资料库。
- 用户提出“查知识库”“查内部资料”“从知识库里找”“根据内部文档回答”“知识问答”等明确知识库检索诉求。
- 用户提供了或需要提供 `ollw_...` API token。
- 用户明确指定了知识库名，例如 `owner/slug`、repo slug 或知识库展示名。
- 用户没有指定知识库，但希望系统自动匹配最合适的知识库。

## 工作流

1. 先定位“当前用户自己的工作区根目录”。
   常见形式是：
   - `/data/openclaw/workspace/<user-folder>/`
   - 例如 `/data/openclaw/workspace/wecom-dm-jiajia/`
   当前如果拿到的是共享根目录 `/data/openclaw/workspace/`，不要保存 token；必须继续定位到具体用户子目录后再保存。
2. token 只允许保存在当前用户私有工作区下：
   - `<user-workspace>/.openclaw/openllm-kb-search/token.env`
   - 例如 `/data/openclaw/workspace/wecom-dm-jiajia/.openclaw/openllm-kb-search/token.env`
3. 若上述 token 文件不存在，或读取后没有 `OPEN_LLM_WIKI_TOKEN`，向用户索取新的 `ollw_...` token。
4. 拿到 token 后，直接由 OpenClaw 把下面内容写入该文件，不要写进 repo 文件，不要写进共享目录，也不要写进对话正文：
   ```dotenv
   OPEN_LLM_WIKI_BASE_URL=http://172.36.164.85:5000
   OPEN_LLM_WIKI_TOKEN=ollw_...
   ```
5. 发起查询前先用 `GET /api/v1/me` 验证 token。
   - 若返回 `401`、`403` 或 `invalid_token`，说明 token 已失效，重新向用户索取并覆盖保存。
6. 如果用户指定了知识库：
   - 先调用 `GET /api/v1/repos`
   - 用户给的是 `owner/slug` 时，按 `full_name` 精确匹配
   - 用户给的是知识库名或 slug 时，按 `name` / `slug` / `full_name` 匹配
   - 若匹配出多个候选，暂停并让用户选择，不要擅自决定
   - 匹配成功后，调用 `POST /api/v1/search`，body 里带上 `repo`
7. 如果用户没有指定知识库，不要手动选库，直接调用 `POST /api/v1/search` 且不传 `repo`，让服务端自动路由。
8. 回复用户时要说明：
   - 本次是显式命中的哪个知识库，还是走了自动路由
   - 若接口返回了 `confidence`、`trace_id`、`routing.reason`，一并带上
   - 若接口已返回 `answer`，优先直接使用接口返回的 `answer` 作为正文，不要再让 OpenClaw 二次改写
   - 一旦已经拿到接口 `answer`，就结束在“知识库结果直返”模式：不要再补一大段总结、不要改写标题、不要改写人名、不要把多行内容压成一段自然语言
   - 尤其当 `answer` 中包含表格、名单、日程、参数表时，必须原样保留字段与行对应关系，不能把主讲、内容、时间等字段重新总结或跨行拼接
   - 如果当前请求来自快捷命令 `/kb`，最终回复应尽量短：可先用 1 行说明“命中知识库/自动路由到某知识库”，随后直接粘贴接口 `answer`；除此之外不要再扩写
   - 只有在接口 `answer` 明显缺失时，才允许基于接口返回的 `evidence` 做补充说明；补充时也必须保持 `facts[].fields` 的原字段值和原行对应关系

## 接口约定

### 1. 验证 token

- 方法：`GET`
- 路径：`/api/v1/me`
- 头：`Authorization: Bearer <token>`

### 2. 获取当前 token 可见知识库

- 方法：`GET`
- 路径：`/api/v1/repos`
- 头：`Authorization: Bearer <token>`

### 3. 显式指定知识库检索

- 方法：`POST`
- 路径：`/api/v1/search`
- 头：
  - `Authorization: Bearer <token>`
  - `Content-Type: application/json`
- body：
  ```json
  {
    "query": "AE350的核心参数有哪些",
    "repo": "alice/ae350-kb"
  }
  ```

### 4. 自动路由检索

- 方法：`POST`
- 路径：`/api/v1/search`
- 头：
  - `Authorization: Bearer <token>`
  - `Content-Type: application/json`
- body：
  ```json
  {
    "query": "AE350的核心参数有哪些"
  }
  ```

## 规则

- 这是一个轻量 skill：优先依赖 OpenClaw 自己的本地写文件能力和 HTTP 调用能力，不要额外生成脚本。
- 不要把 token 写进仓库文件、可提交配置或共享目录。
- 不要把 token 写到 `/data/openclaw/workspace/.openclaw/...` 这种共享根目录下。
- token 必须保存在当前用户私有工作区，例如 `/data/openclaw/workspace/<user-folder>/.openclaw/openllm-kb-search/token.env`。
- 如果当前会话只能拿到共享根工作区 `/data/openclaw/workspace/`，不要落盘 token；先定位到具体用户子目录。
- 不要在正常回复里回显完整 token；最多只展示脱敏前缀。
- 遇到 `401` 或 `invalid_token` 时，不要死试，直接让用户提供新 token 并重新保存。
- 遇到 `422` 且是知识库路由失败或匹配歧义时，把候选知识库列给用户选择。
- 如果无法确定当前会话对应哪个用户私有工作区，先确认路径，再保存 token。
- 一旦接口已返回 `answer`，禁止再把它改写成新的长篇总结；默认直接返回 `answer` 原文，仅允许附带极短的路由说明。
