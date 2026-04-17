-- Migration 009: repo sharing via access code + member relation

CREATE TABLE IF NOT EXISTS repo_share_codes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    repo_id INT NOT NULL,
    code VARCHAR(32) NOT NULL,
    role VARCHAR(16) NOT NULL DEFAULT 'viewer',
    created_by_user_id INT NULL,
    use_count INT NOT NULL DEFAULT 0,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_repo_share_codes_code (code),
    INDEX idx_repo_share_codes_repo (repo_id),
    INDEX idx_repo_share_codes_creator (created_by_user_id),
    CONSTRAINT fk_repo_share_codes_repo FOREIGN KEY (repo_id) REFERENCES repos(id),
    CONSTRAINT fk_repo_share_codes_creator FOREIGN KEY (created_by_user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS repo_members (
    id INT AUTO_INCREMENT PRIMARY KEY,
    repo_id INT NOT NULL,
    user_id INT NOT NULL,
    role VARCHAR(16) NOT NULL DEFAULT 'viewer',
    granted_by_user_id INT NULL,
    share_code_id INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_repo_member_repo_user (repo_id, user_id),
    INDEX idx_repo_members_repo (repo_id),
    INDEX idx_repo_members_user (user_id),
    INDEX idx_repo_members_granted_by (granted_by_user_id),
    INDEX idx_repo_members_share_code (share_code_id),
    CONSTRAINT fk_repo_members_repo FOREIGN KEY (repo_id) REFERENCES repos(id),
    CONSTRAINT fk_repo_members_user FOREIGN KEY (user_id) REFERENCES users(id),
    CONSTRAINT fk_repo_members_granted_by FOREIGN KEY (granted_by_user_id) REFERENCES users(id),
    CONSTRAINT fk_repo_members_share_code FOREIGN KEY (share_code_id) REFERENCES repo_share_codes(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO schema_version (`version`) VALUES (9);
