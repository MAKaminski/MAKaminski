import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

import scripts.fetch_cursor_usage as fetch_cursor_usage


class _FixedDatetime:
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 3, 30, 10, 5, 0, tzinfo=timezone.utc)


class FetchCursorUsageTests(unittest.TestCase):
    def test_get_returns_enterprise_error_on_401(self):
        with patch("scripts.fetch_cursor_usage.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = HTTPError(
                url="https://api.cursor.com/example",
                code=401,
                msg="Unauthorized",
                hdrs=None,
                fp=None,
            )
            data, err = fetch_cursor_usage._get("https://api.cursor.com/example", "key")

        self.assertIsNone(data)
        self.assertEqual(err, fetch_cursor_usage.ENTERPRISE_REQUIRED)

    def test_main_writes_summary_when_api_key_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cursor_usage.db"
            summary_path = Path(tmpdir) / "cursor_usage_latest.json"

            with patch.object(fetch_cursor_usage, "get_db_path", return_value=db_path):
                with patch.dict(os.environ, {}, clear=True):
                    rc = fetch_cursor_usage.main()

            self.assertEqual(rc, 0)
            self.assertTrue(summary_path.exists())
            summary = json.loads(summary_path.read_text())
            self.assertEqual(summary["display_value"], "CURSOR_API_KEY not set")
            self.assertEqual(summary["snapshots_today"], 0)

    def test_main_writes_api_error_summary_and_stops_follow_up_calls(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cursor_usage.db"
            summary_path = Path(tmpdir) / "cursor_usage_latest.json"

            with patch.object(fetch_cursor_usage, "get_db_path", return_value=db_path):
                with patch.object(fetch_cursor_usage, "datetime", _FixedDatetime):
                    with patch.dict(os.environ, {"CURSOR_API_KEY": "test-key"}, clear=True):
                        with patch.object(
                            fetch_cursor_usage,
                            "fetch_agent_edits",
                            return_value=(None, fetch_cursor_usage.ENTERPRISE_REQUIRED),
                        ):
                            with patch.object(fetch_cursor_usage, "fetch_tabs") as mock_tabs:
                                with patch.object(fetch_cursor_usage, "fetch_ai_commits") as mock_ai:
                                    rc = fetch_cursor_usage.main()

            self.assertEqual(rc, 0)
            mock_tabs.assert_not_called()
            mock_ai.assert_not_called()
            summary = json.loads(summary_path.read_text())
            self.assertEqual(summary["display_value"], fetch_cursor_usage.ENTERPRISE_REQUIRED)
            self.assertEqual(summary["snapshots_today"], 0)
            self.assertEqual(summary["recorded_at"], "2026-03-30T10:00:00Z")

    def test_main_inserts_all_metrics_and_builds_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cursor_usage.db"
            summary_path = Path(tmpdir) / "cursor_usage_latest.json"

            agent_rows = {"data": [{"edits": 1}, {"edits": 2}]}
            tabs_rows = {"data": [{"completions": 3}]}
            ai_rows = {
                "commits": [
                    {"repoName": "repo-one", "userEmail": "one@example.com", "id": 1},
                    {"repository": "repo-two", "userId": "u2", "id": 2},
                    {"userId": "u3", "id": 3},
                ]
            }

            with patch.object(fetch_cursor_usage, "get_db_path", return_value=db_path):
                with patch.object(fetch_cursor_usage, "datetime", _FixedDatetime):
                    with patch.dict(os.environ, {"CURSOR_API_KEY": "test-key"}, clear=True):
                        with patch.object(fetch_cursor_usage, "fetch_agent_edits", return_value=(agent_rows, None)):
                            with patch.object(fetch_cursor_usage, "fetch_tabs", return_value=(tabs_rows, None)):
                                with patch.object(fetch_cursor_usage, "fetch_ai_commits", return_value=(ai_rows, None)):
                                    rc = fetch_cursor_usage.main()

            self.assertEqual(rc, 0)
            summary = json.loads(summary_path.read_text())
            self.assertEqual(summary["display_value"], "6")
            self.assertEqual(summary["snapshots_today"], 6)
            self.assertEqual(summary["last_7d"]["agent_edits"], 2)
            self.assertEqual(summary["last_7d"]["tabs"], 1)
            self.assertEqual(summary["last_7d"]["ai_commits"], 3)
            self.assertEqual(summary["by_project"], {"repo-one": 1, "repo-two": 1})

            conn = sqlite3.connect(db_path)
            count = conn.execute("SELECT COUNT(*) FROM usage_snapshots").fetchone()[0]
            self.assertEqual(count, 6)

            ai_commit_rows = conn.execute(
                """
                SELECT project, user_id
                FROM usage_snapshots
                WHERE metric_type = 'ai_commits'
                ORDER BY id
                """
            ).fetchall()
            conn.close()

            self.assertEqual(
                ai_commit_rows,
                [("repo-one", "one@example.com"), ("repo-two", "u2"), (None, "u3")],
            )


if __name__ == "__main__":
    unittest.main()
