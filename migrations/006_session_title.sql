-- Migration 006: add title to conversation_sessions
ALTER TABLE conversation_sessions ADD COLUMN title VARCHAR(255) NOT NULL DEFAULT '新对话' AFTER session_key;
