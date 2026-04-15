-- Migration 008: enforce email verification for new registrations

ALTER TABLE users
  ADD COLUMN email_verified TINYINT(1) NOT NULL DEFAULT 0 AFTER email,
  ADD COLUMN email_verified_at DATETIME NULL AFTER email_verified;

UPDATE users
SET email_verified = 1,
    email_verified_at = COALESCE(email_verified_at, created_at)
WHERE email IS NOT NULL;

INSERT IGNORE INTO schema_version (`version`) VALUES (8);
