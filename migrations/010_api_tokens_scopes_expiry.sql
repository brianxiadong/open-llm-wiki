-- 扩展 api_tokens 以支持过期时间、scope、prefix（OpenClaw 接入所需）
ALTER TABLE api_tokens ADD COLUMN token_prefix VARCHAR(16) NOT NULL DEFAULT '';
ALTER TABLE api_tokens ADD COLUMN scopes VARCHAR(255) NOT NULL DEFAULT 'kb:search,kb:read';
ALTER TABLE api_tokens ADD COLUMN expires_at DATETIME NULL;
CREATE INDEX idx_api_tokens_prefix ON api_tokens(token_prefix);
