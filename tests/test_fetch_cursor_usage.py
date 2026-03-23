import json
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_cursor_usage as usage  # noqa: E402


class GetRequestTests(unittest.TestCase):
    def test_get_maps_403_to_enterprise_required_error(self) -> None:
        with mock.patch.object(
            usage.urllib.request,
            "urlopen",
            side_effect=urllib.error.HTTPError(
                url="https://api.cursor.com",
                code=403,
                msg="Forbidden",
                hdrs=None,
                fp=None,
            ),
        ):
            data, err = usage._get("https://api.cursor.com/example", "token")

        self.assertIsNone(data)
        self.assertEqual(err, usage.ENTERPRISE_REQUIRED)

    def test_get_returns_http_error_text_for_non_auth_errors(self) -> None:
        with mock.patch.object(
            usage.urllib.request,
            "urlopen",
            side_effect=urllib.error.HTTPError(
                url="https://api.cursor.com",
                code=500,
                msg="Internal Server Error",
                hdrs=None,
                fp=None,
            ),
        ):
            data, err = usage._get("https://api.cursor.com/example", "token")

        self.assertIsNone(data)
        self.assertIn("HTTP Error 500", err or "")


class MainFlowTests(unittest.TestCase):
    def test_main_writes_summary_when_api_key_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "data" / "cursor_usage.db"
            with mock.patch.dict("os.environ", {}, clear=True):
                with mock.patch.object(usage, "get_db_path", return_value=db_path):
                    rc = usage.main()

            self.assertEqual(rc, 0)
            summary = json.loads((db_path.parent / "cursor_usage_latest.json").read_text())
            self.assertEqual(summary["display_value"], "CURSOR_API_KEY not set")
            self.assertEqual(summary["snapshots_today"], 0)

    def test_main_stops_after_api_error_and_writes_error_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "data" / "cursor_usage.db"
            with mock.patch.dict("os.environ", {"CURSOR_API_KEY": "token"}, clear=True):
                with mock.patch.object(usage, "get_db_path", return_value=db_path):
                    with mock.patch.object(
                        usage, "fetch_agent_edits", return_value=(None, usage.ENTERPRISE_REQUIRED)
                    ):
                        with mock.patch.object(usage, "fetch_tabs") as tabs_mock:
                            with mock.patch.object(usage, "fetch_ai_commits") as ai_mock:
                                rc = usage.main()

            self.assertEqual(rc, 0)
            tabs_mock.assert_not_called()
            ai_mock.assert_not_called()

            summary = json.loads((db_path.parent / "cursor_usage_latest.json").read_text())
            self.assertEqual(summary["display_value"], usage.ENTERPRISE_REQUIRED)
            self.assertEqual(summary["snapshots_today"], 0)
            self.assertEqual(summary["last_7d"], {})
            self.assertEqual(summary["by_project"], {})

    def test_main_inserts_rows_and_generates_success_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "data" / "cursor_usage.db"
            with mock.patch.dict("os.environ", {"CURSOR_API_KEY": "token"}, clear=True):
                with mock.patch.object(usage, "get_db_path", return_value=db_path):
                    with mock.patch.object(
                        usage,
                        "fetch_agent_edits",
                        return_value=({"data": [{"agent": "editor"}]}, None),
                    ):
                        with mock.patch.object(
                            usage,
                            "fetch_tabs",
                            return_value=({"data": [{"tab": "accepted"}]}, None),
                        ):
                            with mock.patch.object(
                                usage,
                                "fetch_ai_commits",
                                return_value=(
                                    {
                                        "commits": [
                                            {
                                                "repoName": "repo-one",
                                                "userEmail": "dev@example.com",
                                            },
                                            {"repository": "repo-two", "userId": "123"},
                                        ]
                                    },
                                    None,
                                ),
                            ):
                                rc = usage.main()

            self.assertEqual(rc, 0)
            summary = json.loads((db_path.parent / "cursor_usage_latest.json").read_text())
            self.assertEqual(summary["display_value"], "4")
            self.assertEqual(summary["snapshots_today"], 4)
            self.assertEqual(summary["by_project"]["repo-one"], 1)
            self.assertEqual(summary["by_project"]["repo-two"], 1)

            with sqlite3.connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT metric_type, COUNT(*) FROM usage_snapshots GROUP BY metric_type"
                ).fetchall()
            counts = dict(rows)
            self.assertEqual(counts["agent_edits"], 1)
            self.assertEqual(counts["tabs"], 1)
            self.assertEqual(counts["ai_commits"], 2)


if __name__ == "__main__":
    unittest.main()
