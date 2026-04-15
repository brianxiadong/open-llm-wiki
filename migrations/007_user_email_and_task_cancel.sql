-- Migration 007: user email + task cancel support

ALTER TABLE users
  ADD COLUMN email VARCHAR(255) NULL AFTER username;

CREATE UNIQUE INDEX uq_users_email ON users (email);

ALTER TABLE tasks
  ADD COLUMN cancel_requested TINYINT(1) NOT NULL DEFAULT 0 AFTER progress_msg;

INSERT IGNORE INTO schema_version (`version`) VALUES (7);
