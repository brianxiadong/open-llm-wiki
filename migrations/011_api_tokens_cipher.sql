-- 扩展 api_tokens：保存 Fernet 对称加密的明文，使 UI 列表支持"一键复制完整 token"。
-- 注意：此迁移会清空历史 token（用户已确认：目前没人在使用，且老记录只有 hash 无法恢复明文）。

DELETE FROM api_tokens;
ALTER TABLE api_tokens ADD COLUMN token_cipher TEXT NULL AFTER token_hash;
