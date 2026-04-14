-- Migration 006: query_feedbacks table for answer ratings
CREATE TABLE IF NOT EXISTS query_feedbacks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    trace_id VARCHAR(36) NOT NULL,
    repo_id INT NOT NULL,
    user_id INT NULL,
    rating VARCHAR(8) NOT NULL,
    comment TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_feedback_trace (trace_id),
    INDEX idx_feedback_repo (repo_id),
    INDEX idx_feedback_user (user_id),
    INDEX idx_feedback_rating (rating),
    FOREIGN KEY (repo_id) REFERENCES repos(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
