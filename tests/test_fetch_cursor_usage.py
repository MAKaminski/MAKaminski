import importlib.util
import json
import urllib.error
from io import BytesIO
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "fetch_cursor_usage.py"
SPEC = importlib.util.spec_from_file_location("fetch_cursor_usage", MODULE_PATH)
assert SPEC and SPEC.loader
fetch_cursor_usage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch_cursor_usage)


@pytest.mark.parametrize("status_code", [401, 403])
def test_get_returns_enterprise_message_for_auth_errors(monkeypatch, status_code):
    def raise_http_error(request, timeout):
        raise urllib.error.HTTPError(
            url=request.full_url,
            code=status_code,
            msg="auth error",
            hdrs=None,
            fp=BytesIO(b""),
        )

    monkeypatch.setattr(fetch_cursor_usage.urllib.request, "urlopen", raise_http_error)

    payload, error = fetch_cursor_usage._get("https://example.test/api", "secret-key")

    assert payload is None
    assert error == fetch_cursor_usage.ENTERPRISE_REQUIRED


def test_main_writes_summary_when_api_key_missing(monkeypatch, tmp_path):
    db_path = tmp_path / "data" / "cursor_usage.db"
    monkeypatch.setattr(fetch_cursor_usage, "get_db_path", lambda: db_path)
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)

    exit_code = fetch_cursor_usage.main()

    summary = json.loads((db_path.parent / "cursor_usage_latest.json").read_text())
    assert exit_code == 0
    assert summary["display_value"] == "CURSOR_API_KEY not set"
    assert summary["snapshots_today"] == 0


def test_main_short_circuits_followup_calls_on_first_api_error(monkeypatch, tmp_path):
    db_path = tmp_path / "data" / "cursor_usage.db"
    calls = {"tabs": 0, "commits": 0}

    def fake_agent_edits(*_args, **_kwargs):
        return None, fetch_cursor_usage.ENTERPRISE_REQUIRED

    def fake_tabs(*_args, **_kwargs):
        calls["tabs"] += 1
        return {"data": [{"ignored": True}]}, None

    def fake_ai_commits(*_args, **_kwargs):
        calls["commits"] += 1
        return {"commits": [{"repoName": "ignored"}]}, None

    monkeypatch.setattr(fetch_cursor_usage, "get_db_path", lambda: db_path)
    monkeypatch.setattr(fetch_cursor_usage, "fetch_agent_edits", fake_agent_edits)
    monkeypatch.setattr(fetch_cursor_usage, "fetch_tabs", fake_tabs)
    monkeypatch.setattr(fetch_cursor_usage, "fetch_ai_commits", fake_ai_commits)
    monkeypatch.setenv("CURSOR_API_KEY", "token")

    exit_code = fetch_cursor_usage.main()

    summary = json.loads((db_path.parent / "cursor_usage_latest.json").read_text())
    assert exit_code == 0
    assert calls == {"tabs": 0, "commits": 0}
    assert summary["display_value"] == fetch_cursor_usage.ENTERPRISE_REQUIRED
    assert summary["snapshots_today"] == 0
    assert summary["last_7d"] == {}
    assert summary["by_project"] == {}
    assert summary["recorded_at"]


def test_main_success_writes_display_value_and_project_allocations(monkeypatch, tmp_path):
    db_path = tmp_path / "data" / "cursor_usage.db"

    monkeypatch.setattr(fetch_cursor_usage, "get_db_path", lambda: db_path)
    monkeypatch.setattr(
        fetch_cursor_usage,
        "fetch_agent_edits",
        lambda *_args, **_kwargs: ({"data": [{"metric": "agent"}]}, None),
    )
    monkeypatch.setattr(
        fetch_cursor_usage,
        "fetch_tabs",
        lambda *_args, **_kwargs: ({"data": [{"metric": "tab"}]}, None),
    )
    monkeypatch.setattr(
        fetch_cursor_usage,
        "fetch_ai_commits",
        lambda *_args, **_kwargs: (
            {
                "commits": [
                    {"repoName": "repo-a", "userEmail": "a@example.test", "lines": 10},
                    {"repository": "repo-b", "userId": "user-b", "lines": 20},
                ]
            },
            None,
        ),
    )
    monkeypatch.setenv("CURSOR_API_KEY", "token")

    exit_code = fetch_cursor_usage.main()

    summary = json.loads((db_path.parent / "cursor_usage_latest.json").read_text())
    assert exit_code == 0
    assert summary["display_value"] == "4"
    assert summary["snapshots_today"] == 4
    assert summary["last_7d"]["agent_edits"] == 1
    assert summary["last_7d"]["tabs"] == 1
    assert summary["last_7d"]["ai_commits"] == 2
    assert summary["by_project"] == {"repo-a": 1, "repo-b": 1}
