import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime as real_datetime
from datetime import timezone
from pathlib import Path
from unittest import mock


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "fetch_cursor_usage.py"
SPEC = importlib.util.spec_from_file_location("fetch_cursor_usage", SCRIPT_PATH)
fetch_cursor_usage = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(fetch_cursor_usage)


class FixedDateTime:
    @classmethod
    def now(cls, tz=None):
        return real_datetime(2099, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class FetchCursorUsageTests(unittest.TestCase):
    def test_main_without_api_key_writes_skip_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cursor_usage.db"
            summary_path = db_path.parent / "cursor_usage_latest.json"

            with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
                fetch_cursor_usage, "get_db_path", return_value=db_path
            ):
                exit_code = fetch_cursor_usage.main()

            self.assertEqual(exit_code, 0)
            self.assertTrue(summary_path.exists())
            summary = json.loads(summary_path.read_text())
            self.assertEqual(summary["display_value"], "CURSOR_API_KEY not set")
            self.assertEqual(summary["snapshots_today"], 0)
            self.assertFalse(db_path.exists())

    def test_main_api_error_short_circuits_later_fetches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cursor_usage.db"
            summary_path = db_path.parent / "cursor_usage_latest.json"

            with (
                mock.patch.dict(os.environ, {"CURSOR_API_KEY": "token"}, clear=True),
                mock.patch.object(fetch_cursor_usage, "get_db_path", return_value=db_path),
                mock.patch.object(fetch_cursor_usage, "datetime", FixedDateTime),
                mock.patch.object(
                    fetch_cursor_usage,
                    "fetch_agent_edits",
                    return_value=(None, fetch_cursor_usage.ENTERPRISE_REQUIRED),
                ),
                mock.patch.object(fetch_cursor_usage, "fetch_tabs") as fetch_tabs,
                mock.patch.object(fetch_cursor_usage, "fetch_ai_commits") as fetch_ai_commits,
            ):
                exit_code = fetch_cursor_usage.main()

            self.assertEqual(exit_code, 0)
            fetch_tabs.assert_not_called()
            fetch_ai_commits.assert_not_called()

            summary = json.loads(summary_path.read_text())
            self.assertEqual(summary["display_value"], fetch_cursor_usage.ENTERPRISE_REQUIRED)
            self.assertEqual(summary["snapshots_today"], 0)
            self.assertEqual(summary["recorded_at"], "2099-01-02T03:00:00Z")
            self.assertEqual(summary["last_7d"], {})
            self.assertEqual(summary["by_project"], {})

            with sqlite3.connect(db_path) as conn:
                count = conn.execute("SELECT COUNT(*) FROM usage_snapshots").fetchone()[0]
            self.assertEqual(count, 0)

    def test_main_successful_fetch_writes_expected_aggregates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cursor_usage.db"
            summary_path = db_path.parent / "cursor_usage_latest.json"

            with (
                mock.patch.dict(os.environ, {"CURSOR_API_KEY": "token"}, clear=True),
                mock.patch.object(fetch_cursor_usage, "get_db_path", return_value=db_path),
                mock.patch.object(fetch_cursor_usage, "datetime", FixedDateTime),
                mock.patch.object(
                    fetch_cursor_usage,
                    "fetch_agent_edits",
                    return_value=({"data": [{"count": 1}, {"count": 2}]}, None),
                ),
                mock.patch.object(
                    fetch_cursor_usage,
                    "fetch_tabs",
                    return_value=({"data": [{"accepted": 3}]}, None),
                ),
                mock.patch.object(
                    fetch_cursor_usage,
                    "fetch_ai_commits",
                    return_value=(
                        {
                            "commits": [
                                {"repoName": "repo-a", "userEmail": "one@example.com"},
                                {"repository": "repo-b", "userId": "u-2"},
                                {"repoName": "repo-a", "userEmail": "three@example.com"},
                            ]
                        },
                        None,
                    ),
                ),
            ):
                exit_code = fetch_cursor_usage.main()

            self.assertEqual(exit_code, 0)
            summary = json.loads(summary_path.read_text())
            self.assertEqual(summary["display_value"], "6")
            self.assertEqual(summary["recorded_at"], "2099-01-02T03:00:00Z")
            self.assertEqual(summary["snapshots_today"], 6)
            self.assertEqual(
                summary["last_7d"],
                {"agent_edits": 2, "tabs": 1, "ai_commits": 3},
            )
            self.assertEqual(summary["by_project"], {"repo-a": 2, "repo-b": 1})

            with sqlite3.connect(db_path) as conn:
                total = conn.execute("SELECT COUNT(*) FROM usage_snapshots").fetchone()[0]
                by_metric = dict(
                    conn.execute(
                        "SELECT metric_type, COUNT(*) FROM usage_snapshots GROUP BY metric_type"
                    ).fetchall()
                )
                ai_user_ids = [
                    row[0]
                    for row in conn.execute(
                        """
                        SELECT user_id
                        FROM usage_snapshots
                        WHERE metric_type = 'ai_commits'
                        ORDER BY user_id
                        """
                    ).fetchall()
                ]

            self.assertEqual(total, 6)
            self.assertEqual(by_metric, {"agent_edits": 2, "tabs": 1, "ai_commits": 3})
            self.assertEqual(ai_user_ids, ["one@example.com", "three@example.com", "u-2"])


if __name__ == "__main__":
    unittest.main()
