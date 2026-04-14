-- Migration: 扩展 query_logs 表，记录完整检索轨迹
-- 兼容 MySQL 5.7+（不使用 IF NOT EXISTS）

ALTER TABLE query_logs
  ADD COLUMN full_answer    LONGTEXT     NULL        COMMENT '完整 LLM 回答（markdown）',
  ADD COLUMN retrieval_json LONGTEXT     NULL        COMMENT '完整证据 JSON：wiki/chunk/fact 命中详情及分数',
  ADD COLUMN query_mode     VARCHAR(16)  NOT NULL DEFAULT '' COMMENT '查询模式: narrative/fact/hybrid',
  ADD COLUMN latency_ms     INT          NULL        COMMENT '从收到请求到返回答案的耗时（毫秒）';
