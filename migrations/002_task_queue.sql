-- Migration 002: add progress tracking fields to tasks

ALTER TABLE `tasks`
  ADD COLUMN `progress` INT NOT NULL DEFAULT 0 AFTER `output_data`,
  ADD COLUMN `progress_msg` TEXT NULL AFTER `progress`,
  ADD COLUMN `started_at` DATETIME NULL AFTER `created_at`;

-- Reset any stuck tasks from previous runs
UPDATE `tasks` SET `status` = 'failed', `progress_msg` = 'Reset by migration'
  WHERE `status` IN ('running', 'pending');

INSERT IGNORE INTO `schema_version` (`version`) VALUES (2);
