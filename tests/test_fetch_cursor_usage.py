import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _load_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "fetch_cursor_usage.py"
    spec = importlib.util.spec_from_file_location("fetch_cursor_usage", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


fetch_cursor_usage = _load_module()


class AuthHeaderTests(unittest.TestCase):
    def test_auth_header_encodes_api_key_as_basic_auth(self):
        header = fetch_cursor_usage._auth_header("test-key")
        self.assertIn("Authorization", header)
        self.assertEqual(header["Authorization"], "Basic dGVzdC1rZXk6")


class GetRequestTests(unittest.TestCase):
    def test_get_returns_enterprise_error_for_401(self):
        err = fetch_cursor_usage.urllib.error.HTTPError(
            url="https://api.cursor.com/example",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=None,
        )
        with mock.patch.object(fetch_cursor_usage.urllib.request, "urlopen", side_effect=err):
            data, message = fetch_cursor_usage._get("https://api.cursor.com/example", "key")

        self.assertIsNone(data)
        self.assertEqual(message, fetch_cursor_usage.ENTERPRISE_REQUIRED)

    def test_get_returns_http_error_message_for_non_auth_failures(self):
        err = fetch_cursor_usage.urllib.error.HTTPError(
            url="https://api.cursor.com/example",
            code=500,
            msg="Server Error",
            hdrs=None,
            fp=None,
        )
        with mock.patch.object(fetch_cursor_usage.urllib.request, "urlopen", side_effect=err):
            data, message = fetch_cursor_usage._get("https://api.cursor.com/example", "key")

        self.assertIsNone(data)
        self.assertIn("HTTP Error 500", message)


class MainFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "data" / "cursor_usage.db"
        self.summary_path = self.db_path.parent / "cursor_usage_latest.json"

    def test_main_writes_skip_summary_when_api_key_missing(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(fetch_cursor_usage, "get_db_path", return_value=self.db_path):
                exit_code = fetch_cursor_usage.main()

        self.assertEqual(exit_code, 0)
        self.assertTrue(self.summary_path.exists())
        summary = json.loads(self.summary_path.read_text())
        self.assertEqual(summary["display_value"], "CURSOR_API_KEY not set")
        self.assertEqual(summary["snapshots_today"], 0)

    def test_main_short_circuits_follow_up_calls_when_initial_api_error(self):
        with mock.patch.dict(os.environ, {"CURSOR_API_KEY": "secret"}, clear=True):
            with mock.patch.object(fetch_cursor_usage, "get_db_path", return_value=self.db_path):
                with mock.patch.object(
                    fetch_cursor_usage,
                    "fetch_agent_edits",
                    return_value=(None, "upstream failure"),
                ):
                    with mock.patch.object(fetch_cursor_usage, "fetch_tabs") as tabs_mock:
                        with mock.patch.object(fetch_cursor_usage, "fetch_ai_commits") as ai_mock:
                            exit_code = fetch_cursor_usage.main()

        self.assertEqual(exit_code, 0)
        tabs_mock.assert_not_called()
        ai_mock.assert_not_called()
        summary = json.loads(self.summary_path.read_text())
        self.assertEqual(summary["display_value"], "upstream failure")
        self.assertEqual(summary["snapshots_today"], 0)
        self.assertEqual(summary["by_project"], {})

    def test_main_persists_rows_and_summary_for_successful_fetches(self):
        agent_rows = {"data": [{"editsAccepted": 7}]}
        tab_rows = {"data": [{"tabAccepted": 5}]}
        commit_rows = {
            "commits": [
                {"repoName": "repo-a", "userEmail": "a@example.com", "id": "1"},
                {"repository": "repo-b", "userId": "u-2", "id": "2"},
            ]
        }

        with mock.patch.dict(os.environ, {"CURSOR_API_KEY": "secret"}, clear=True):
            with mock.patch.object(fetch_cursor_usage, "get_db_path", return_value=self.db_path):
                with mock.patch.object(fetch_cursor_usage, "fetch_agent_edits", return_value=(agent_rows, None)):
                    with mock.patch.object(fetch_cursor_usage, "fetch_tabs", return_value=(tab_rows, None)):
                        with mock.patch.object(fetch_cursor_usage, "fetch_ai_commits", return_value=(commit_rows, None)):
                            exit_code = fetch_cursor_usage.main()

        self.assertEqual(exit_code, 0)

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT metric_type, project, user_id, source FROM usage_snapshots ORDER BY id"
            ).fetchall()
        finally:
            conn.close()

        self.assertEqual(len(rows), 4)
        self.assertEqual([row[0] for row in rows], ["agent_edits", "tabs", "ai_commits", "ai_commits"])
        self.assertEqual(rows[2][1], "repo-a")
        self.assertEqual(rows[3][1], "repo-b")
        self.assertEqual(rows[2][2], "a@example.com")
        self.assertEqual(rows[3][2], "u-2")
        self.assertEqual(rows[2][3], "cursor_ai_code")

        summary = json.loads(self.summary_path.read_text())
        self.assertEqual(summary["display_value"], "4")
        self.assertEqual(summary["snapshots_today"], 4)
        self.assertEqual(summary["by_project"].get("repo-a"), 1)
        self.assertEqual(summary["by_project"].get("repo-b"), 1)


if __name__ == "__main__":
    unittest.main()
