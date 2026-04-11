CREATE TABLE IF NOT EXISTS query_logs (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    repo_id          INT NOT NULL,
    user_id          INT NOT NULL,
    question         TEXT NOT NULL,
    answer_preview   TEXT,
    confidence       VARCHAR(16) NOT NULL DEFAULT 'low',
    wiki_hit_count   INT NOT NULL DEFAULT 0,
    chunk_hit_count  INT NOT NULL DEFAULT 0,
    used_wiki_pages  LONGTEXT,
    used_chunk_ids   LONGTEXT,
    evidence_summary TEXT,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_ql_repo (repo_id),
    INDEX idx_ql_user (user_id),
    CONSTRAINT fk_ql_repo FOREIGN KEY (repo_id) REFERENCES repos(id),
    CONSTRAINT fk_ql_user FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
