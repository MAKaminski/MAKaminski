# Cursor Usage Data

- **cursor_usage.db** — SQLite database with `usage_snapshots` table (hourly snapshots, project allocation)
- **cursor_usage_latest.json** — Summary for badges; includes `by_project` for graphing
- **schema.sql** — Table definition

## Setup

1. Add `CURSOR_API_KEY` to repo secrets (Admin API key from [cursor.com/dashboard](https://cursor.com/dashboard) → Settings → Advanced → Admin API Keys)
2. Requires Cursor **Enterprise** (Analytics API + AI Code Tracking API)
3. Workflow runs hourly; data accumulates for graphing and project allocation

## Query examples

```sql
-- Usage by project (last 7 days)
SELECT project, metric_type, COUNT(*) 
FROM usage_snapshots 
WHERE recorded_at >= datetime('now', '-7 days') AND project IS NOT NULL
GROUP BY project, metric_type;
```
