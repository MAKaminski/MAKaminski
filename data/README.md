# Cursor Usage Data

This directory stores usage telemetry pulled by `scripts/fetch_cursor_usage.py` and committed by `.github/workflows/cursor-usage.yml`.

## Purpose and architecture

The tracker captures daily Cursor team analytics and appends hourly snapshots to SQLite so the profile can expose:

- a badge-friendly summary (`cursor_usage_latest.json`)
- trend context (`last_7d`)
- project allocation (`by_project` from AI code commit repo names)

### Codepaths

- **Workflow:** `.github/workflows/cursor-usage.yml` (hourly schedule: `0 * * * *`)
- **Fetcher:** `scripts/fetch_cursor_usage.py`
- **Schema:** `data/schema.sql`
- **Outputs:** `data/cursor_usage.db`, `data/cursor_usage_latest.json`

## Data flow

For the current UTC day (`startDate=endDate=today`), the fetcher calls:

1. `/analytics/team/agent-edits`
2. `/analytics/team/tabs`
3. `/analytics/ai-code/commits?pageSize=100`

It then:

1. Appends rows into `usage_snapshots`
2. Aggregates last 7 days (`metric_type` counts + top projects)
3. Writes `cursor_usage_latest.json` for README badge consumption

## Setup

1. Add `CURSOR_API_KEY` as a repository secret (Cursor Admin API key).
2. Use Cursor **Enterprise** for Analytics + AI Code Tracking endpoints.
3. Ensure Actions can write repository contents (workflow commits under `github-actions[bot]`).

## Local execution

Run from repository root:

```bash
python scripts/fetch_cursor_usage.py
```

If `CURSOR_API_KEY` is unset, the script still exits successfully and writes:

```json
{
  "display_value": "CURSOR_API_KEY not set",
  "snapshots_today": 0
}
```

## Output contract (`cursor_usage_latest.json`)

Success shape:

```json
{
  "display_value": "<inserted-row-count>",
  "recorded_at": "YYYY-MM-DDTHH:00:00Z",
  "snapshots_today": 0,
  "last_7d": {},
  "by_project": {}
}
```

API-error shape (including non-Enterprise 401/403):

```json
{
  "display_value": "NEED TO UPGRADE TO ENTERPRISE PLAN TO RETURN VALUE",
  "snapshots_today": 0,
  "recorded_at": "YYYY-MM-DDTHH:00:00Z",
  "last_7d": {},
  "by_project": {}
}
```

## Troubleshooting runbook

### Badge shows `CURSOR_API_KEY not set`

- Confirm repository secret name is exactly `CURSOR_API_KEY`.
- Re-run workflow (`Cursor Usage Tracker`) manually via `workflow_dispatch`.

### Badge shows upgrade message

- Expected when API responds `401/403`.
- Verify the key belongs to a Cursor Enterprise team with analytics access.

### No DB growth but workflow succeeded

- The workflow uses `continue-on-error: true` for fetch step, so job can pass with no data insert.
- Check workflow logs for `API error:` lines from the script.
- Validate query window assumptions: script requests the current UTC day only.

### `by_project` is empty

- `by_project` is populated only from AI code commit rows where `repoName`/`repository` exists.
- If AI Code Tracking has no commits in the window, this is expected.

## Common pitfalls

- **UTC boundaries:** snapshots are bucketed by UTC hour (`YYYY-MM-DDTHH:00:00Z`), not local time.
- **No backfill logic:** default execution captures "today" only; historical backfill requires script changes or manual replay.
- **Top projects capped:** summary keeps only top 20 projects in last 7 days.

## Query examples

```sql
-- Rows inserted by metric in last 24h
SELECT metric_type, COUNT(*)
FROM usage_snapshots
WHERE recorded_at >= datetime('now', '-1 day')
GROUP BY metric_type;
```

```sql
-- Project allocation over last 7 days
SELECT project, COUNT(*) AS rows
FROM usage_snapshots
WHERE recorded_at >= datetime('now', '-7 days')
  AND project IS NOT NULL
GROUP BY project
ORDER BY rows DESC;
```
