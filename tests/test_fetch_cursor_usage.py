import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "fetch_cursor_usage.py"
    spec = importlib.util.spec_from_file_location("fetch_cursor_usage", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


fetch_cursor_usage = _load_module()


class _FixedDatetime:
    @classmethod
    def now(cls, _tz):
        return datetime(2026, 3, 25, 10, 0, 0, tzinfo=timezone.utc)


class FetchCursorUsageTests(unittest.TestCase):
    def test_get_returns_enterprise_message_on_403(self):
        http_error = urllib.error.HTTPError(
            url="https://api.cursor.com/analytics/team/agent-edits",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=None,
        )

        with mock.patch.object(fetch_cursor_usage.urllib.request, "urlopen", side_effect=http_error):
            data, err = fetch_cursor_usage._get("https://api.cursor.com/analytics/team/agent-edits", "api-key")

        self.assertIsNone(data)
        self.assertEqual(err, fetch_cursor_usage.ENTERPRISE_REQUIRED)

    def test_main_without_api_key_writes_summary_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cursor_usage.db"
            summary_path = db_path.parent / "cursor_usage_latest.json"

            with mock.patch.object(fetch_cursor_usage, "get_db_path", return_value=db_path):
                with mock.patch.dict(os.environ, {}, clear=True):
                    exit_code = fetch_cursor_usage.main()

            self.assertEqual(exit_code, 0)
            self.assertTrue(summary_path.exists())
            summary = json.loads(summary_path.read_text())
            self.assertEqual(summary["display_value"], "CURSOR_API_KEY not set")
            self.assertEqual(summary["snapshots_today"], 0)

    def test_main_stops_after_first_api_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cursor_usage.db"
            summary_path = db_path.parent / "cursor_usage_latest.json"

            with mock.patch.object(fetch_cursor_usage, "get_db_path", return_value=db_path):
                with mock.patch.object(fetch_cursor_usage, "datetime", _FixedDatetime):
                    with mock.patch.object(
                        fetch_cursor_usage,
                        "fetch_agent_edits",
                        return_value=(None, fetch_cursor_usage.ENTERPRISE_REQUIRED),
                    ):
                        with mock.patch.object(fetch_cursor_usage, "fetch_tabs") as fetch_tabs_mock:
                            with mock.patch.object(fetch_cursor_usage, "fetch_ai_commits") as fetch_ai_commits_mock:
                                with mock.patch.dict(os.environ, {"CURSOR_API_KEY": "token"}, clear=True):
                                    exit_code = fetch_cursor_usage.main()

            self.assertEqual(exit_code, 0)
            fetch_tabs_mock.assert_not_called()
            fetch_ai_commits_mock.assert_not_called()
            summary = json.loads(summary_path.read_text())
            self.assertEqual(summary["display_value"], fetch_cursor_usage.ENTERPRISE_REQUIRED)
            self.assertEqual(summary["snapshots_today"], 0)
            self.assertEqual(summary["recorded_at"], "2026-03-25T10:00:00Z")

    def test_main_success_writes_summary_and_snapshots(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "cursor_usage.db"
            summary_path = db_path.parent / "cursor_usage_latest.json"

            with mock.patch.object(fetch_cursor_usage, "get_db_path", return_value=db_path):
                with mock.patch.object(fetch_cursor_usage, "datetime", _FixedDatetime):
                    with mock.patch.object(
                        fetch_cursor_usage,
                        "fetch_agent_edits",
                        return_value=({"data": [{"tokens": 10}]}, None),
                    ):
                        with mock.patch.object(
                            fetch_cursor_usage,
                            "fetch_tabs",
                            return_value=({"data": [{"accepted": 3}]}, None),
                        ):
                            with mock.patch.object(
                                fetch_cursor_usage,
                                "fetch_ai_commits",
                                return_value=(
                                    {
                                        "commits": [
                                            {"repoName": "repo-a", "userEmail": "dev@example.com"},
                                            {"repository": "repo-b", "userId": "u-2"},
                                        ]
                                    },
                                    None,
                                ),
                            ):
                                with mock.patch.dict(os.environ, {"CURSOR_API_KEY": "token"}, clear=True):
                                    exit_code = fetch_cursor_usage.main()

            self.assertEqual(exit_code, 0)
            summary = json.loads(summary_path.read_text())
            self.assertEqual(summary["display_value"], "4")
            self.assertEqual(summary["snapshots_today"], 4)
            self.assertEqual(summary["by_project"], {"repo-a": 1, "repo-b": 1})

            conn = sqlite3.connect(db_path)
            try:
                snapshot_count = conn.execute("SELECT COUNT(*) FROM usage_snapshots").fetchone()[0]
                self.assertEqual(snapshot_count, 4)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
