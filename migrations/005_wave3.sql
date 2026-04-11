-- Migration 005: feature wave 3 (conversation sessions, audit logs, api tokens)

CREATE TABLE IF NOT EXISTS conversation_sessions (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    repo_id        INT NOT NULL,
    user_id        INT NOT NULL,
    session_key    VARCHAR(64) NOT NULL,
    messages_json  LONGTEXT NOT NULL,
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_cs_repo (repo_id),
    INDEX idx_cs_user (user_id),
    INDEX idx_cs_key (session_key),
    CONSTRAINT fk_cs_repo FOREIGN KEY (repo_id) REFERENCES repos(id),
    CONSTRAINT fk_cs_user FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS audit_logs (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    user_id       INT,
    username      VARCHAR(64),
    action        VARCHAR(64) NOT NULL,
    resource_type VARCHAR(32),
    resource_id   VARCHAR(128),
    detail        TEXT,
    ip            VARCHAR(64),
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_al_user (user_id),
    INDEX idx_al_action (action),
    INDEX idx_al_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS api_tokens (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    user_id      INT NOT NULL,
    name         VARCHAR(128) NOT NULL,
    token_hash   VARCHAR(256) NOT NULL UNIQUE,
    last_used_at DATETIME,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active    TINYINT(1) NOT NULL DEFAULT 1,
    INDEX idx_at_user (user_id),
    CONSTRAINT fk_at_user FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

ALTER TABLE query_logs MODIFY user_id INT NULL;
