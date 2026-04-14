-- Migration 005: add trace_id to query_logs
ALTER TABLE query_logs
    ADD COLUMN trace_id VARCHAR(36) NULL UNIQUE AFTER id;

CREATE INDEX idx_query_logs_trace_id ON query_logs (trace_id);
