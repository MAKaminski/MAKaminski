-- Cursor usage tracking schema
-- Supports hourly snapshots, project allocation, and graphing

CREATE TABLE IF NOT EXISTS usage_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  recorded_at TEXT NOT NULL,           -- ISO 8601, truncated to hour
  metric_type TEXT NOT NULL,            -- agent_edits, tabs, ai_commits, tokens
  project TEXT,                        -- repo/project name for allocation
  user_id TEXT,                        -- optional user/email
  value_json TEXT NOT NULL,            -- JSON blob of metric values
  source TEXT NOT NULL,                 -- cursor_analytics, cursor_ai_code, etc.
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_usage_recorded ON usage_snapshots(recorded_at);
CREATE INDEX IF NOT EXISTS idx_usage_project ON usage_snapshots(project);
CREATE INDEX IF NOT EXISTS idx_usage_metric ON usage_snapshots(metric_type);
