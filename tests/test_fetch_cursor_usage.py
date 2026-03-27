import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

import scripts.fetch_cursor_usage as fetch_cursor_usage


class FetchCursorUsageTests(unittest.TestCase):
    def test_get_returns_enterprise_error_on_401(self) -> None:
        http_error = urllib.error.HTTPError(
            url="https://api.cursor.com/analytics/team/agent-edits",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=None,
        )
        with patch(
            "scripts.fetch_cursor_usage.urllib.request.urlopen",
            side_effect=http_error,
        ):
            data, err = fetch_cursor_usage._get("https://api.cursor.com/example", "api-key")

        self.assertIsNone(data)
        self.assertEqual(err, fetch_cursor_usage.ENTERPRISE_REQUIRED)

    def test_main_without_api_key_writes_summary_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "cursor_usage.db"
            with patch.dict(os.environ, {}, clear=True):
                with patch("scripts.fetch_cursor_usage.get_db_path", return_value=db_path):
                    rc = fetch_cursor_usage.main()

            self.assertEqual(rc, 0)
            summary_path = db_path.parent / "cursor_usage_latest.json"
            self.assertTrue(summary_path.exists())
            summary = json.loads(summary_path.read_text())
            self.assertEqual(summary["display_value"], "CURSOR_API_KEY not set")
            self.assertEqual(summary["snapshots_today"], 0)

    def test_main_stops_after_api_error_and_sets_error_display(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "cursor_usage.db"
            ai_commits_mock = Mock(return_value=({"commits": [{"repoName": "should-not-run"}]}, None))

            with patch.dict(os.environ, {"CURSOR_API_KEY": "token"}, clear=True):
                with patch("scripts.fetch_cursor_usage.get_db_path", return_value=db_path):
                    with patch(
                        "scripts.fetch_cursor_usage.fetch_agent_edits",
                        return_value=({"data": [{"editsAccepted": 12}]}, None),
                    ):
                        with patch(
                            "scripts.fetch_cursor_usage.fetch_tabs",
                            return_value=(None, "HTTP Error 500: Internal Server Error"),
                        ):
                            with patch("scripts.fetch_cursor_usage.fetch_ai_commits", ai_commits_mock):
                                rc = fetch_cursor_usage.main()

            self.assertEqual(rc, 0)
            ai_commits_mock.assert_not_called()

            summary_path = db_path.parent / "cursor_usage_latest.json"
            summary = json.loads(summary_path.read_text())
            self.assertEqual(
                summary["display_value"],
                "HTTP Error 500: Internal Server Error",
            )
            self.assertEqual(summary["snapshots_today"], 0)
            self.assertEqual(summary["last_7d"], {})
            self.assertEqual(summary["by_project"], {})


if __name__ == "__main__":
    unittest.main()
