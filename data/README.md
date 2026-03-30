# Cursor Usage Data

This directory stores the data that powers the **Cursor Usage** badge in the root `README.md`.

## What this subsystem does

- Fetches Cursor team usage from Cursor APIs via `scripts/fetch_cursor_usage.py`
- Appends snapshot rows to SQLite (`data/cursor_usage.db`)
- Publishes badge-friendly summary JSON (`data/cursor_usage_latest.json`)
- Commits updated `data/` files from the hourly workflow (`.github/workflows/cursor-usage.yml`)

## Files

- `cursor_usage.db` - SQLite database with the `usage_snapshots` table
- `cursor_usage_latest.json` - Summary JSON read by the profile badge (`$.display_value`)
- `schema.sql` - SQLite schema and indexes
- `README.md` (this file) - architecture, operations, and troubleshooting notes

## Workflow and architecture

1. GitHub Actions runs **hourly** (`cron: "0 * * * *"`).
2. Workflow sets `CURSOR_API_KEY` from repo secrets and runs:
   - `python scripts/fetch_cursor_usage.py`
3. Script initializes schema from `data/schema.sql` (if needed).
4. Script fetches usage for the current UTC date (`startDate=today`, `endDate=today`) from:
   - `/analytics/team/agent-edits` -> `metric_type='agent_edits'`
   - `/analytics/team/tabs` -> `metric_type='tabs'`
   - `/analytics/ai-code/commits` -> `metric_type='ai_commits'`, with `project` from `repoName`/`repository`
5. Script writes summary JSON and exits 0 (including handled error states).
6. Workflow commits any `data/` changes with:
   - `chore: update Cursor usage snapshot [automated]`

## Public data contract

### `cursor_usage_latest.json`

Current keys written by the script:

- `display_value` (string) - value shown by badge
- `recorded_at` (ISO hour bucket, when available)
- `snapshots_today` (integer)
- `last_7d` (object: metric_type -> count)
- `by_project` (object: project -> count)

Error/fallback values:

- Missing secret: `display_value = "CURSOR_API_KEY not set"`
- 401/403 API access failure: `display_value = "NEED TO UPGRADE TO ENTERPRISE PLAN TO RETURN VALUE"`

### `usage_snapshots` table (`schema.sql`)

Columns:

- `recorded_at` (TEXT, ISO hour bucket)
- `metric_type` (TEXT)
- `project` (TEXT, nullable)
- `user_id` (TEXT, nullable)
- `value_json` (TEXT; raw JSON payload)
- `source` (TEXT; e.g. `cursor_analytics`, `cursor_ai_code`)

Indexes:

- `idx_usage_recorded(recorded_at)`
- `idx_usage_project(project)`
- `idx_usage_metric(metric_type)`

## Local runbook

### One-off run

```bash
CURSOR_API_KEY="<admin-api-key>" python scripts/fetch_cursor_usage.py
```

### Verify outputs

```bash
python scripts/fetch_cursor_usage.py
sqlite3 data/cursor_usage.db "SELECT metric_type, COUNT(*) FROM usage_snapshots GROUP BY metric_type;"
sqlite3 data/cursor_usage.db "SELECT project, COUNT(*) FROM usage_snapshots WHERE project IS NOT NULL GROUP BY project ORDER BY 2 DESC LIMIT 10;"
```

## Constraints and interpretation notes

- Snapshot granularity is **hourly**, but source date range is **current day**.
- Counts in `last_7d` and SQL examples are counts of **stored snapshot rows**, not deduplicated business events.
- Badge reads only `$.display_value`; detailed context remains in DB/JSON.
- Workflow uses `continue-on-error: true` for the fetch step to avoid hard workflow failures.

## Troubleshooting

### Badge shows `CURSOR_API_KEY not set`

- Confirm repo secret `CURSOR_API_KEY` exists and is non-empty.
- Re-run workflow manually (`workflow_dispatch`) after updating secret.

### Badge shows enterprise upgrade message

- The script maps API 401/403 to:
  - `NEED TO UPGRADE TO ENTERPRISE PLAN TO RETURN VALUE`
- Verify account/API entitlement for Cursor Analytics + AI Code Tracking endpoints.

### `by_project` is empty

- `by_project` is populated from AI code commits endpoint only.
- If no commits are returned for the date range, project allocation remains empty.

### DB grows over time

- This workflow appends snapshots hourly and commits updated `data/`.
- If repository size becomes a concern, consider retention/compaction strategy in a future change.

## Query examples

```sql
-- Snapshot volume by metric type (last 7 days)
SELECT metric_type, COUNT(*)
FROM usage_snapshots
WHERE recorded_at >= datetime('now', '-7 days')
GROUP BY metric_type;

-- Project allocation snapshots (last 7 days)
SELECT project, COUNT(*)
FROM usage_snapshots
WHERE recorded_at >= datetime('now', '-7 days')
  AND project IS NOT NULL
GROUP BY project
ORDER BY 2 DESC;
```
