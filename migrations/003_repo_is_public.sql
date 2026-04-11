-- Add is_public visibility flag to repos table
ALTER TABLE repos ADD COLUMN is_public TINYINT(1) NOT NULL DEFAULT 0;
