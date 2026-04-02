import base64
import json
import os
import sqlite3
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import scripts.fetch_cursor_usage as fetch_cursor_usage


class FetchCursorUsageTests(unittest.TestCase):
    def test_auth_header_uses_basic_auth_with_trailing_colon(self) -> None:
        header = fetch_cursor_usage._auth_header("test-api-key")
        expected_creds = base64.b64encode(b"test-api-key:").decode()

        self.assertEqual(header, {"Authorization": f"Basic {expected_creds}"})

    def test_get_returns_enterprise_required_for_401(self) -> None:
        error = urllib.error.HTTPError(
            url="https://example.com",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=None,
        )
        with patch("scripts.fetch_cursor_usage.urllib.request.urlopen", side_effect=error):
            data, err = fetch_cursor_usage._get("https://example.com", "irrelevant")

        self.assertIsNone(data)
        self.assertEqual(err, fetch_cursor_usage.ENTERPRISE_REQUIRED)

    def test_get_returns_enterprise_required_for_403(self) -> None:
        error = urllib.error.HTTPError(
            url="https://example.com",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=None,
        )
        with patch("scripts.fetch_cursor_usage.urllib.request.urlopen", side_effect=error):
            data, err = fetch_cursor_usage._get("https://example.com", "irrelevant")

        self.assertIsNone(data)
        self.assertEqual(err, fetch_cursor_usage.ENTERPRISE_REQUIRED)

    def test_get_returns_http_error_message_for_non_auth_failure(self) -> None:
        error = urllib.error.HTTPError(
            url="https://example.com",
            code=500,
            msg="Server Error",
            hdrs=None,
            fp=None,
        )
        with patch("scripts.fetch_cursor_usage.urllib.request.urlopen", side_effect=error):
            data, err = fetch_cursor_usage._get("https://example.com", "irrelevant")

        self.assertIsNone(data)
        self.assertIn("HTTP Error 500", err)

    def test_main_writes_summary_when_api_key_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "cursor_usage.db"
            summary_path = Path(temp_dir) / "cursor_usage_latest.json"

            with (
                patch("scripts.fetch_cursor_usage.get_db_path", return_value=db_path),
                patch.dict(os.environ, {}, clear=True),
            ):
                rc = fetch_cursor_usage.main()

            self.assertEqual(rc, 0)
            self.assertTrue(summary_path.exists())

            summary = json.loads(summary_path.read_text())
            self.assertEqual(summary["display_value"], "CURSOR_API_KEY not set")
            self.assertEqual(summary["snapshots_today"], 0)

    def test_main_stops_after_first_api_error_and_writes_error_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "cursor_usage.db"
            summary_path = Path(temp_dir) / "cursor_usage_latest.json"

            with (
                patch("scripts.fetch_cursor_usage.get_db_path", return_value=db_path),
                patch.dict(os.environ, {"CURSOR_API_KEY": "key"}, clear=True),
                patch("scripts.fetch_cursor_usage.fetch_agent_edits", return_value=(None, "api boom")),
                patch("scripts.fetch_cursor_usage.fetch_tabs") as fetch_tabs_mock,
                patch("scripts.fetch_cursor_usage.fetch_ai_commits") as fetch_ai_commits_mock,
            ):
                rc = fetch_cursor_usage.main()

            self.assertEqual(rc, 0)
            fetch_tabs_mock.assert_not_called()
            fetch_ai_commits_mock.assert_not_called()

            summary = json.loads(summary_path.read_text())
            self.assertEqual(summary["display_value"], "api boom")
            self.assertEqual(summary["snapshots_today"], 0)
            self.assertEqual(summary["last_7d"], {})
            self.assertEqual(summary["by_project"], {})

    def test_main_aggregates_successful_responses_and_persists_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "cursor_usage.db"
            summary_path = Path(temp_dir) / "cursor_usage_latest.json"

            agent_edits = {"data": [{"acceptedLinesAdded": 10}, {"acceptedLinesAdded": 2}]}
            tabs = {"data": [{"totalTabsAccepted": 3}]}
            ai_commits = {
                "commits": [
                    {"repoName": "repo-a", "userEmail": "dev1@example.com"},
                    {"repository": "repo-b", "userId": "dev2"},
                    {"repoName": "repo-a", "userId": "dev3"},
                ]
            }

            with (
                patch("scripts.fetch_cursor_usage.get_db_path", return_value=db_path),
                patch.dict(os.environ, {"CURSOR_API_KEY": "key"}, clear=True),
                patch("scripts.fetch_cursor_usage.fetch_agent_edits", return_value=(agent_edits, None)),
                patch("scripts.fetch_cursor_usage.fetch_tabs", return_value=(tabs, None)),
                patch("scripts.fetch_cursor_usage.fetch_ai_commits", return_value=(ai_commits, None)),
            ):
                rc = fetch_cursor_usage.main()

            self.assertEqual(rc, 0)

            summary = json.loads(summary_path.read_text())
            self.assertEqual(summary["display_value"], "6")
            self.assertEqual(summary["snapshots_today"], 6)
            self.assertEqual(summary["by_project"], {"repo-a": 2, "repo-b": 1})
            self.assertEqual(summary["last_7d"].get("agent_edits"), 2)
            self.assertEqual(summary["last_7d"].get("tabs"), 1)
            self.assertEqual(summary["last_7d"].get("ai_commits"), 3)

            conn = sqlite3.connect(db_path)
            try:
                total_rows = conn.execute("SELECT COUNT(*) FROM usage_snapshots").fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(total_rows, 6)

    def test_main_stops_before_ai_commits_when_tabs_call_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "cursor_usage.db"
            summary_path = Path(temp_dir) / "cursor_usage_latest.json"

            agent_edits = {"data": [{"acceptedLinesAdded": 10}]}

            with (
                patch("scripts.fetch_cursor_usage.get_db_path", return_value=db_path),
                patch.dict(os.environ, {"CURSOR_API_KEY": "key"}, clear=True),
                patch("scripts.fetch_cursor_usage.fetch_agent_edits", return_value=(agent_edits, None)),
                patch("scripts.fetch_cursor_usage.fetch_tabs", return_value=(None, "tabs api failure")),
                patch("scripts.fetch_cursor_usage.fetch_ai_commits") as fetch_ai_commits_mock,
            ):
                rc = fetch_cursor_usage.main()

            self.assertEqual(rc, 0)
            fetch_ai_commits_mock.assert_not_called()

            summary = json.loads(summary_path.read_text())
            self.assertEqual(summary["display_value"], "tabs api failure")
            self.assertEqual(summary["snapshots_today"], 0)

            conn = sqlite3.connect(db_path)
            try:
                total_rows = conn.execute("SELECT COUNT(*) FROM usage_snapshots").fetchone()[0]
                metric_types = [row[0] for row in conn.execute("SELECT metric_type FROM usage_snapshots")]
            finally:
                conn.close()

            self.assertEqual(total_rows, 1)
            self.assertEqual(metric_types, ["agent_edits"])


if __name__ == "__main__":
    unittest.main()
