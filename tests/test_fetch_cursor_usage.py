import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _load_module():
    module_path = Path(__file__).resolve().parent.parent / "scripts" / "fetch_cursor_usage.py"
    spec = importlib.util.spec_from_file_location("fetch_cursor_usage", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load fetch_cursor_usage module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetch_cursor_usage = _load_module()


class FetchCursorUsageTests(unittest.TestCase):
    def test_main_writes_summary_when_api_key_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "cursor_usage.db"
            summary_path = db_path.parent / "cursor_usage_latest.json"

            with (
                mock.patch.dict("os.environ", {}, clear=True),
                mock.patch.object(fetch_cursor_usage, "get_db_path", return_value=db_path),
            ):
                rc = fetch_cursor_usage.main()

            self.assertEqual(rc, 0)
            self.assertTrue(summary_path.exists())
            summary = json.loads(summary_path.read_text())
            self.assertEqual(summary["display_value"], "CURSOR_API_KEY not set")
            self.assertEqual(summary["snapshots_today"], 0)

    def test_main_stops_after_first_api_error_and_writes_error_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "cursor_usage.db"
            summary_path = db_path.parent / "cursor_usage_latest.json"

            tabs_mock = mock.Mock()
            ai_mock = mock.Mock()

            with (
                mock.patch.dict("os.environ", {"CURSOR_API_KEY": "token"}, clear=True),
                mock.patch.object(fetch_cursor_usage, "get_db_path", return_value=db_path),
                mock.patch.object(
                    fetch_cursor_usage,
                    "fetch_agent_edits",
                    return_value=(None, fetch_cursor_usage.ENTERPRISE_REQUIRED),
                ),
                mock.patch.object(fetch_cursor_usage, "fetch_tabs", tabs_mock),
                mock.patch.object(fetch_cursor_usage, "fetch_ai_commits", ai_mock),
            ):
                rc = fetch_cursor_usage.main()

            self.assertEqual(rc, 0)
            tabs_mock.assert_not_called()
            ai_mock.assert_not_called()
            summary = json.loads(summary_path.read_text())
            self.assertEqual(summary["display_value"], fetch_cursor_usage.ENTERPRISE_REQUIRED)
            self.assertEqual(summary["snapshots_today"], 0)
            self.assertEqual(summary["last_7d"], {})
            self.assertEqual(summary["by_project"], {})

    def test_main_aggregates_successful_metrics_and_project_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "cursor_usage.db"
            summary_path = db_path.parent / "cursor_usage_latest.json"

            agent_data = {"data": [{"dailyLinesAdded": 10}, {"dailyLinesAdded": 5}]}
            tabs_data = {"data": [{"acceptCount": 3}]}
            ai_commits_data = {
                "commits": [
                    {"repoName": "core-repo", "userEmail": "a@example.com", "id": "1"},
                    {"repository": "core-repo", "userId": "u2", "id": "2"},
                    {"repoName": "side-repo", "userEmail": "b@example.com", "id": "3"},
                ]
            }

            with (
                mock.patch.dict("os.environ", {"CURSOR_API_KEY": "token"}, clear=True),
                mock.patch.object(fetch_cursor_usage, "get_db_path", return_value=db_path),
                mock.patch.object(fetch_cursor_usage, "fetch_agent_edits", return_value=(agent_data, None)),
                mock.patch.object(fetch_cursor_usage, "fetch_tabs", return_value=(tabs_data, None)),
                mock.patch.object(fetch_cursor_usage, "fetch_ai_commits", return_value=(ai_commits_data, None)),
            ):
                rc = fetch_cursor_usage.main()

            self.assertEqual(rc, 0)
            summary = json.loads(summary_path.read_text())
            self.assertEqual(summary["snapshots_today"], 6)
            self.assertEqual(summary["display_value"], "6")
            self.assertEqual(summary["last_7d"]["agent_edits"], 2)
            self.assertEqual(summary["last_7d"]["tabs"], 1)
            self.assertEqual(summary["last_7d"]["ai_commits"], 3)
            self.assertEqual(summary["by_project"]["core-repo"], 2)
            self.assertEqual(summary["by_project"]["side-repo"], 1)

            conn = sqlite3.connect(db_path)
            try:
                count = conn.execute("SELECT COUNT(*) FROM usage_snapshots").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(count, 6)


if __name__ == "__main__":
    unittest.main()
