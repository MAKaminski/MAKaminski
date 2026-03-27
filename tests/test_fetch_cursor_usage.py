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
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "fetch_cursor_usage.py"
    spec = importlib.util.spec_from_file_location("fetch_cursor_usage", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FetchCursorUsageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_get_maps_401_to_enterprise_required(self):
        http_error = urllib.error.HTTPError(
            url="https://api.cursor.com/analytics/team/agent-edits",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=None,
        )
        with mock.patch.object(self.mod.urllib.request, "urlopen", side_effect=http_error):
            data, err = self.mod._get("https://example.com", "fake-key")

        self.assertIsNone(data)
        self.assertEqual(err, self.mod.ENTERPRISE_REQUIRED)

    def test_main_without_api_key_writes_skip_summary(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "cursor_usage.db"
            with mock.patch.object(self.mod, "get_db_path", return_value=db_path), mock.patch.dict(
                os.environ, {"CURSOR_API_KEY": ""}, clear=False
            ):
                exit_code = self.mod.main()

            self.assertEqual(exit_code, 0)
            summary_path = db_path.parent / "cursor_usage_latest.json"
            self.assertTrue(summary_path.exists())
            summary = json.loads(summary_path.read_text())
            self.assertEqual(summary["display_value"], "CURSOR_API_KEY not set")
            self.assertEqual(summary["snapshots_today"], 0)

    def test_main_stops_after_first_api_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "cursor_usage.db"
            tabs_mock = mock.Mock(return_value=({"data": [{"count": 1}]}, None))
            commits_mock = mock.Mock(return_value=({"commits": [{"repoName": "repo"}]}, None))

            with mock.patch.object(self.mod, "get_db_path", return_value=db_path), mock.patch.dict(
                os.environ, {"CURSOR_API_KEY": "valid-key"}, clear=False
            ), mock.patch.object(
                self.mod, "fetch_agent_edits", return_value=(None, "API unavailable")
            ), mock.patch.object(
                self.mod, "fetch_tabs", tabs_mock
            ), mock.patch.object(
                self.mod, "fetch_ai_commits", commits_mock
            ):
                exit_code = self.mod.main()

            self.assertEqual(exit_code, 0)
            tabs_mock.assert_not_called()
            commits_mock.assert_not_called()

            summary = json.loads((db_path.parent / "cursor_usage_latest.json").read_text())
            self.assertEqual(summary["display_value"], "API unavailable")
            self.assertEqual(summary["snapshots_today"], 0)

            conn = sqlite3.connect(db_path)
            self.addCleanup(conn.close)
            row_count = conn.execute("SELECT COUNT(*) FROM usage_snapshots").fetchone()[0]
            self.assertEqual(row_count, 0)

    def test_main_writes_ai_commit_rows_and_project_summary(self):
        fixed_now = datetime(2026, 3, 27, 10, 30, 0, tzinfo=timezone.utc)

        class _FixedDateTime:
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return fixed_now.replace(tzinfo=None)
                return fixed_now.astimezone(tz)

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "cursor_usage.db"
            with mock.patch.object(self.mod, "datetime", _FixedDateTime), mock.patch.object(
                self.mod, "get_db_path", return_value=db_path
            ), mock.patch.dict(
                os.environ, {"CURSOR_API_KEY": "valid-key"}, clear=False
            ), mock.patch.object(
                self.mod, "fetch_agent_edits", return_value=({"data": [{"edits": 7}]}, None)
            ), mock.patch.object(
                self.mod, "fetch_tabs", return_value=({"data": [{"tabsAccepted": 3}]}, None)
            ), mock.patch.object(
                self.mod,
                "fetch_ai_commits",
                return_value=(
                    {
                        "commits": [
                            {"repoName": "repo-a", "userEmail": "a@example.com", "id": "c1"},
                            {"repository": "repo-b", "userId": "u2", "id": "c2"},
                            {"repository": None, "userId": "u3", "id": "c3"},
                        ]
                    },
                    None,
                ),
            ):
                exit_code = self.mod.main()

            self.assertEqual(exit_code, 0)

            conn = sqlite3.connect(db_path)
            self.addCleanup(conn.close)
            total_count = conn.execute("SELECT COUNT(*) FROM usage_snapshots").fetchone()[0]
            self.assertEqual(total_count, 5)

            ai_rows = conn.execute(
                "SELECT project, user_id FROM usage_snapshots WHERE metric_type = 'ai_commits' ORDER BY id"
            ).fetchall()
            self.assertEqual(
                ai_rows,
                [
                    ("repo-a", "a@example.com"),
                    ("repo-b", "u2"),
                    (None, "u3"),
                ],
            )

            summary = json.loads((db_path.parent / "cursor_usage_latest.json").read_text())
            self.assertEqual(summary["display_value"], "5")
            self.assertEqual(summary["snapshots_today"], 5)
            self.assertEqual(summary["recorded_at"], "2026-03-27T10:00:00Z")
            self.assertEqual(summary["by_project"], {"repo-a": 1, "repo-b": 1})
            self.assertEqual(summary["last_7d"]["agent_edits"], 1)
            self.assertEqual(summary["last_7d"]["tabs"], 1)
            self.assertEqual(summary["last_7d"]["ai_commits"], 3)


if __name__ == "__main__":
    unittest.main()
