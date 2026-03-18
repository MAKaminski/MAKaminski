#!/usr/bin/env python3
"""
Fetch Cursor usage from Analytics API (Enterprise) and append to SQLite.
Runs hourly via GitHub Actions. Requires CURSOR_API_KEY secret.

Cursor Analytics API: https://cursor.com/docs/account/teams/analytics-api
"""

import base64
import json
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_BASE = "https://api.cursor.com"


def _auth_header(api_key: str) -> dict:
    creds = base64.b64encode(f"{api_key}:".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


def get_db_path() -> Path:
    root = Path(__file__).resolve().parent.parent
    return root / "data" / "cursor_usage.db"


def init_db(conn: sqlite3.Connection) -> None:
    schema_path = Path(__file__).resolve().parent.parent / "data" / "schema.sql"
    if schema_path.exists():
        conn.executescript(schema_path.read_text())
    conn.commit()


def _get(url: str, api_key: str) -> dict | None:
    req = urllib.request.Request(url, headers=_auth_header(api_key))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def fetch_agent_edits(api_key: str, start_date: str, end_date: str) -> dict | None:
    url = f"{API_BASE}/analytics/team/agent-edits?startDate={start_date}&endDate={end_date}"
    return _get(url, api_key)


def fetch_tabs(api_key: str, start_date: str, end_date: str) -> dict | None:
    url = f"{API_BASE}/analytics/team/tabs?startDate={start_date}&endDate={end_date}"
    return _get(url, api_key)


def fetch_ai_commits(api_key: str, start_date: str, end_date: str) -> dict | None:
    """AI Code Tracking API: per-commit, per-repo (project allocation)."""
    url = f"{API_BASE}/analytics/ai-code/commits?startDate={start_date}&endDate={end_date}&pageSize=100"
    return _get(url, api_key)


def main() -> int:
    api_key = os.environ.get("CURSOR_API_KEY")
    if not api_key or not api_key.strip():
        print("CURSOR_API_KEY not set; skipping Cursor usage fetch")
        return 0

    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    init_db(conn)

    now = datetime.now(timezone.utc)
    hour_bucket = now.strftime("%Y-%m-%dT%H:00:00Z")
    today = now.strftime("%Y-%m-%d")

    inserted = 0

    # Agent edits (daily aggregates from Cursor Analytics API)
    agent_data = fetch_agent_edits(api_key, today, today)
    if agent_data and agent_data.get("data"):
        for row in agent_data["data"]:
            conn.execute(
                """
                INSERT INTO usage_snapshots (recorded_at, metric_type, project, value_json, source)
                VALUES (?, 'agent_edits', NULL, ?, 'cursor_analytics')
                """,
                (hour_bucket, json.dumps(row)),
            )
            inserted += 1

    # Tabs (Tab autocomplete usage)
    tabs_data = fetch_tabs(api_key, today, today)
    if tabs_data and tabs_data.get("data"):
        for row in tabs_data["data"]:
            conn.execute(
                """
                INSERT INTO usage_snapshots (recorded_at, metric_type, project, value_json, source)
                VALUES (?, 'tabs', NULL, ?, 'cursor_analytics')
                """,
                (hour_bucket, json.dumps(row)),
            )
            inserted += 1

    # AI Code commits (per-repo for project allocation)
    ai_commits = fetch_ai_commits(api_key, today, today)
    if ai_commits and ai_commits.get("commits"):
        for commit in ai_commits["commits"]:
            project = commit.get("repoName") or commit.get("repository")
            conn.execute(
                """
                INSERT INTO usage_snapshots (recorded_at, metric_type, project, user_id, value_json, source)
                VALUES (?, 'ai_commits', ?, ?, ?, 'cursor_ai_code')
                """,
                (
                    hour_bucket,
                    project,
                    commit.get("userEmail") or commit.get("userId"),
                    json.dumps(commit),
                ),
            )
            inserted += 1

    conn.commit()

    # Aggregate for summary (last 7 days for graphing context)
    cursor = conn.execute(
        """
        SELECT metric_type, COUNT(*)
        FROM usage_snapshots
        WHERE recorded_at >= datetime('now', '-7 days')
        GROUP BY metric_type
        """
    )
    metric_counts = dict(cursor.fetchall())

    # Project allocation (last 7 days)
    cursor = conn.execute(
        """
        SELECT project, COUNT(*)
        FROM usage_snapshots
        WHERE recorded_at >= datetime('now', '-7 days') AND project IS NOT NULL
        GROUP BY project
        ORDER BY 2 DESC
        LIMIT 20
        """
    )
    by_project = {row[0]: row[1] for row in cursor if row[0]}

    # Write summary JSON for badge display
    summary = {
        "recorded_at": hour_bucket,
        "snapshots_today": inserted,
        "last_7d": metric_counts,
        "by_project": by_project,
    }
    summary_path = db_path.parent / "cursor_usage_latest.json"
    summary_path.write_text(json.dumps(summary, indent=2))

    conn.close()
    print(f"Inserted {inserted} snapshots; summary written to {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
