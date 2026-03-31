import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest import mock

import scripts.fetch_cursor_usage as fetch_cursor_usage


class FetchCursorUsageTests(unittest.TestCase):
    def test_get_returns_enterprise_message_on_401_and_403(self) -> None:
        for status_code in (401, 403):
            with self.subTest(status_code=status_code):
                http_error = HTTPError(
                    url="https://api.cursor.com/analytics/team/tabs",
                    code=status_code,
                    msg="Unauthorized",
                    hdrs=None,
                    fp=None,
                )
                with mock.patch.object(
                    fetch_cursor_usage.urllib.request,
                    "urlopen",
                    side_effect=http_error,
                ):
                    data, error = fetch_cursor_usage._get(
                        "https://api.cursor.com/analytics/team/tabs",
                        "fake-api-key",
                    )

                self.assertIsNone(data)
                self.assertEqual(error, fetch_cursor_usage.ENTERPRISE_REQUIRED)

    def test_main_without_api_key_writes_fallback_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "cursor_usage.db"
            with mock.patch.object(fetch_cursor_usage, "get_db_path", return_value=db_path):
                with mock.patch.dict(os.environ, {}, clear=True):
                    result = fetch_cursor_usage.main()

            summary_path = Path(tmp_dir) / "cursor_usage_latest.json"
            summary = json.loads(summary_path.read_text())

        self.assertEqual(result, 0)
        self.assertEqual(summary["display_value"], "CURSOR_API_KEY not set")
        self.assertEqual(summary["snapshots_today"], 0)

    def test_main_stops_after_first_api_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "cursor_usage.db"
            tabs_mock = mock.Mock()
            ai_commits_mock = mock.Mock()

            with mock.patch.object(fetch_cursor_usage, "get_db_path", return_value=db_path):
                with mock.patch.dict(os.environ, {"CURSOR_API_KEY": "token"}):
                    with mock.patch.object(
                        fetch_cursor_usage,
                        "fetch_agent_edits",
                        return_value=(None, fetch_cursor_usage.ENTERPRISE_REQUIRED),
                    ):
                        with mock.patch.object(fetch_cursor_usage, "fetch_tabs", tabs_mock):
                            with mock.patch.object(
                                fetch_cursor_usage,
                                "fetch_ai_commits",
                                ai_commits_mock,
                            ):
                                result = fetch_cursor_usage.main()

            summary_path = Path(tmp_dir) / "cursor_usage_latest.json"
            summary = json.loads(summary_path.read_text())

        self.assertEqual(result, 0)
        self.assertEqual(summary["display_value"], fetch_cursor_usage.ENTERPRISE_REQUIRED)
        self.assertEqual(summary["snapshots_today"], 0)
        self.assertEqual(summary["last_7d"], {})
        self.assertEqual(summary["by_project"], {})
        tabs_mock.assert_not_called()
        ai_commits_mock.assert_not_called()

    def test_main_writes_aggregated_summary_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "cursor_usage.db"

            with mock.patch.object(fetch_cursor_usage, "get_db_path", return_value=db_path):
                with mock.patch.dict(os.environ, {"CURSOR_API_KEY": "token"}):
                    with mock.patch.object(
                        fetch_cursor_usage,
                        "fetch_agent_edits",
                        return_value=({"data": [{"accepted": 10}]}, None),
                    ):
                        with mock.patch.object(
                            fetch_cursor_usage,
                            "fetch_tabs",
                            return_value=({"data": [{"acceptedTabs": 4}]}, None),
                        ):
                            with mock.patch.object(
                                fetch_cursor_usage,
                                "fetch_ai_commits",
                                return_value=(
                                    {
                                        "commits": [
                                            {
                                                "repoName": "repo-a",
                                                "userEmail": "dev@example.com",
                                                "acceptedLinesAdded": 12,
                                            }
                                        ]
                                    },
                                    None,
                                ),
                            ):
                                result = fetch_cursor_usage.main()

            summary_path = Path(tmp_dir) / "cursor_usage_latest.json"
            summary = json.loads(summary_path.read_text())

        self.assertEqual(result, 0)
        self.assertEqual(summary["display_value"], "3")
        self.assertEqual(summary["snapshots_today"], 3)
        self.assertEqual(summary["last_7d"]["agent_edits"], 1)
        self.assertEqual(summary["last_7d"]["tabs"], 1)
        self.assertEqual(summary["last_7d"]["ai_commits"], 1)
        self.assertEqual(summary["by_project"], {"repo-a": 1})


if __name__ == "__main__":
    unittest.main()
