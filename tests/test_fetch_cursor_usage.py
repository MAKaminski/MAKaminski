import json
import sqlite3
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from scripts import fetch_cursor_usage as cursor_usage


class FetchCursorUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db_path = Path(self.tempdir.name) / "cursor_usage.db"

    def _read_summary(self) -> dict:
        summary_path = self.db_path.parent / "cursor_usage_latest.json"
        self.assertTrue(summary_path.exists(), "expected summary JSON to be written")
        return json.loads(summary_path.read_text())

    def test_get_returns_enterprise_required_for_forbidden(self) -> None:
        http_error = urllib.error.HTTPError(
            url="https://api.cursor.com/analytics/team/tabs",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=None,
        )
        with patch("scripts.fetch_cursor_usage.urllib.request.urlopen", side_effect=http_error):
            payload, error = cursor_usage._get("https://api.cursor.com/analytics/team/tabs", "test-key")

        self.assertIsNone(payload)
        self.assertEqual(error, cursor_usage.ENTERPRISE_REQUIRED)

    def test_main_writes_summary_when_api_key_missing(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with patch("scripts.fetch_cursor_usage.get_db_path", return_value=self.db_path):
                exit_code = cursor_usage.main()

        self.assertEqual(exit_code, 0)
        summary = self._read_summary()
        self.assertEqual(summary["display_value"], "CURSOR_API_KEY not set")
        self.assertEqual(summary["snapshots_today"], 0)

    def test_main_writes_api_error_and_short_circuits_follow_up_calls(self) -> None:
        with patch.dict("os.environ", {"CURSOR_API_KEY": "test-key"}, clear=True):
            with patch("scripts.fetch_cursor_usage.get_db_path", return_value=self.db_path):
                with patch(
                    "scripts.fetch_cursor_usage.fetch_agent_edits",
                    return_value=(None, cursor_usage.ENTERPRISE_REQUIRED),
                ):
                    with patch("scripts.fetch_cursor_usage.fetch_tabs") as fetch_tabs_mock:
                        with patch("scripts.fetch_cursor_usage.fetch_ai_commits") as fetch_ai_commits_mock:
                            exit_code = cursor_usage.main()

        self.assertEqual(exit_code, 0)
        fetch_tabs_mock.assert_not_called()
        fetch_ai_commits_mock.assert_not_called()

        summary = self._read_summary()
        self.assertEqual(summary["display_value"], cursor_usage.ENTERPRISE_REQUIRED)
        self.assertEqual(summary["snapshots_today"], 0)
        self.assertEqual(summary["last_7d"], {})
        self.assertEqual(summary["by_project"], {})

    def test_main_inserts_snapshots_and_summarizes_projects(self) -> None:
        agent_data = {"data": [{"accepted_lines": 100}]}
        tabs_data = {"data": [{"accepted_tabs": 3}]}
        ai_commits = {
            "commits": [
                {"repoName": "repo-a", "userEmail": "dev@example.com", "commitHash": "abc"},
                {"repository": "repo-b", "userId": "user-1", "commitHash": "def"},
            ]
        }

        with patch.dict("os.environ", {"CURSOR_API_KEY": "test-key"}, clear=True):
            with patch("scripts.fetch_cursor_usage.get_db_path", return_value=self.db_path):
                with patch("scripts.fetch_cursor_usage.fetch_agent_edits", return_value=(agent_data, None)):
                    with patch("scripts.fetch_cursor_usage.fetch_tabs", return_value=(tabs_data, None)):
                        with patch("scripts.fetch_cursor_usage.fetch_ai_commits", return_value=(ai_commits, None)):
                            exit_code = cursor_usage.main()

        self.assertEqual(exit_code, 0)

        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT metric_type, project, user_id FROM usage_snapshots ORDER BY id ASC"
        ).fetchall()
        counts = dict(
            conn.execute(
                "SELECT metric_type, COUNT(*) FROM usage_snapshots GROUP BY metric_type"
            ).fetchall()
        )
        conn.close()

        self.assertEqual(len(rows), 4)
        self.assertEqual(counts["agent_edits"], 1)
        self.assertEqual(counts["tabs"], 1)
        self.assertEqual(counts["ai_commits"], 2)
        self.assertEqual(rows[2], ("ai_commits", "repo-a", "dev@example.com"))
        self.assertEqual(rows[3], ("ai_commits", "repo-b", "user-1"))

        summary = self._read_summary()
        self.assertEqual(summary["display_value"], "4")
        self.assertEqual(summary["snapshots_today"], 4)
        self.assertEqual(summary["last_7d"]["agent_edits"], 1)
        self.assertEqual(summary["last_7d"]["tabs"], 1)
        self.assertEqual(summary["last_7d"]["ai_commits"], 2)
        self.assertEqual(summary["by_project"]["repo-a"], 1)
        self.assertEqual(summary["by_project"]["repo-b"], 1)


if __name__ == "__main__":
    unittest.main()
