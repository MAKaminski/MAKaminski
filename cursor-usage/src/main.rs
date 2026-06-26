//! Fetch Cursor usage from the Analytics API (Enterprise) and append snapshots to SQLite.
//!
//! Runs hourly via GitHub Actions. Requires the `CURSOR_API_KEY` secret.
//! Rust port of the original `scripts/fetch_cursor_usage.py`; the on-disk data
//! contract (`data/cursor_usage.db` + `data/cursor_usage_latest.json`) is unchanged
//! so the profile badge keeps working.
//!
//! Cursor Analytics API: <https://cursor.com/docs/account/teams/analytics-api>

use std::path::{Path, PathBuf};
use std::process::ExitCode;

use base64::Engine as _;
use chrono::Utc;
use rusqlite::Connection;
use serde_json::{json, Map, Value};

const API_BASE: &str = "https://api.cursor.com";

/// Internal reason recorded when the API rejects the key (Pro plan -> 401/403).
pub const ENTERPRISE_REQUIRED: &str = "NEED TO UPGRADE TO ENTERPRISE PLAN TO RETURN VALUE";

/// Schema embedded at build time as a fallback if `data/schema.sql` is missing at runtime.
const EMBEDDED_SCHEMA: &str = include_str!("../../data/schema.sql");

/// Classified fetch failure so the badge can show a short, honest label.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FetchError {
    /// 401/403 - the key works but the account lacks the Analytics entitlement.
    Auth,
    /// Anything else (network, 5xx, malformed payload).
    Other(String),
}

impl FetchError {
    /// Concise value shown on the README badge (`$.display_value`).
    fn badge_value(&self) -> &str {
        match self {
            FetchError::Auth => "enterprise only",
            FetchError::Other(_) => "unavailable",
        }
    }

    /// Full reason kept in the summary `detail` field and logs.
    fn detail(&self) -> String {
        match self {
            FetchError::Auth => ENTERPRISE_REQUIRED.to_string(),
            FetchError::Other(msg) => msg.clone(),
        }
    }
}

/// Build the HTTP Basic auth header value: base64("<key>:").
pub fn auth_header(api_key: &str) -> String {
    let creds = base64::engine::general_purpose::STANDARD.encode(format!("{api_key}:"));
    format!("Basic {creds}")
}

/// Resolve the directory holding the SQLite DB and summary JSON.
/// Honors `CURSOR_USAGE_DATA_DIR`, otherwise `./data` relative to the CWD.
fn data_dir() -> PathBuf {
    std::env::var_os("CURSOR_USAGE_DATA_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("data"))
}

/// Apply the schema (idempotent) so the snapshots table/indexes exist.
fn init_db(conn: &Connection, data_dir: &Path) -> rusqlite::Result<()> {
    let schema = std::fs::read_to_string(data_dir.join("schema.sql"))
        .unwrap_or_else(|_| EMBEDDED_SCHEMA.to_string());
    conn.execute_batch(&schema)
}

// ---------------------------------------------------------------------------
// HTTP
// ---------------------------------------------------------------------------

/// GET a JSON endpoint with Basic auth, mapping 401/403 to `FetchError::Auth`.
fn get(url: &str, api_key: &str) -> Result<Value, FetchError> {
    let resp = ureq::get(url)
        .set("Authorization", &auth_header(api_key))
        .timeout(std::time::Duration::from_secs(30))
        .call();

    match resp {
        Ok(r) => r
            .into_json::<Value>()
            .map_err(|e| FetchError::Other(e.to_string())),
        Err(ureq::Error::Status(401 | 403, _)) => Err(FetchError::Auth),
        Err(ureq::Error::Status(code, _)) => Err(FetchError::Other(format!("HTTP {code}"))),
        Err(e) => Err(FetchError::Other(e.to_string())),
    }
}

fn fetch_agent_edits(api_key: &str, start: &str, end: &str) -> Result<Value, FetchError> {
    get(&format!("{API_BASE}/analytics/team/agent-edits?startDate={start}&endDate={end}"), api_key)
}

fn fetch_tabs(api_key: &str, start: &str, end: &str) -> Result<Value, FetchError> {
    get(&format!("{API_BASE}/analytics/team/tabs?startDate={start}&endDate={end}"), api_key)
}

fn fetch_ai_commits(api_key: &str, start: &str, end: &str) -> Result<Value, FetchError> {
    get(
        &format!("{API_BASE}/analytics/ai-code/commits?startDate={start}&endDate={end}&pageSize=100"),
        api_key,
    )
}

// ---------------------------------------------------------------------------
// Persistence (network-free, unit tested)
// ---------------------------------------------------------------------------

/// Insert each element of `data["data"]` as a simple analytics snapshot row.
fn process_simple(
    conn: &Connection,
    hour_bucket: &str,
    payload: &Value,
    metric_type: &str,
    source: &str,
) -> rusqlite::Result<usize> {
    let mut inserted = 0;
    if let Some(rows) = payload.get("data").and_then(Value::as_array) {
        for row in rows {
            conn.execute(
                "INSERT INTO usage_snapshots (recorded_at, metric_type, project, value_json, source)
                 VALUES (?1, ?2, NULL, ?3, ?4)",
                rusqlite::params![hour_bucket, metric_type, row.to_string(), source],
            )?;
            inserted += 1;
        }
    }
    Ok(inserted)
}

/// Insert each AI-code commit, allocating to a project (repoName||repository)
/// and user (userEmail||userId).
fn process_ai_commits(
    conn: &Connection,
    hour_bucket: &str,
    payload: &Value,
) -> rusqlite::Result<usize> {
    let mut inserted = 0;
    if let Some(commits) = payload.get("commits").and_then(Value::as_array) {
        for commit in commits {
            let project = commit
                .get("repoName")
                .or_else(|| commit.get("repository"))
                .and_then(Value::as_str);
            let user = commit
                .get("userEmail")
                .or_else(|| commit.get("userId"))
                .and_then(Value::as_str);
            conn.execute(
                "INSERT INTO usage_snapshots (recorded_at, metric_type, project, user_id, value_json, source)
                 VALUES (?1, 'ai_commits', ?2, ?3, ?4, 'cursor_ai_code')",
                rusqlite::params![hour_bucket, project, user, commit.to_string()],
            )?;
            inserted += 1;
        }
    }
    Ok(inserted)
}

/// Build the success summary JSON (rolling 7-day counts + project allocation).
fn build_summary(conn: &Connection, hour_bucket: &str, inserted: usize) -> rusqlite::Result<Value> {
    let mut last_7d = Map::new();
    let mut stmt = conn.prepare(
        "SELECT metric_type, COUNT(*) FROM usage_snapshots
         WHERE recorded_at >= datetime('now', '-7 days')
         GROUP BY metric_type",
    )?;
    let rows = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))?;
    for row in rows {
        let (metric, count) = row?;
        last_7d.insert(metric, json!(count));
    }

    let mut by_project = Map::new();
    let mut stmt = conn.prepare(
        "SELECT project, COUNT(*) FROM usage_snapshots
         WHERE recorded_at >= datetime('now', '-7 days') AND project IS NOT NULL
         GROUP BY project ORDER BY 2 DESC LIMIT 20",
    )?;
    let rows = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))?;
    for row in rows {
        let (project, count) = row?;
        by_project.insert(project, json!(count));
    }

    Ok(json!({
        "display_value": inserted.to_string(),
        "recorded_at": hour_bucket,
        "snapshots_today": inserted,
        "last_7d": Value::Object(last_7d),
        "by_project": Value::Object(by_project),
    }))
}

fn write_summary(data_dir: &Path, summary: &Value) -> std::io::Result<()> {
    std::fs::create_dir_all(data_dir)?;
    let path = data_dir.join("cursor_usage_latest.json");
    std::fs::write(path, serde_json::to_string_pretty(summary).unwrap_or_default())
}

// ---------------------------------------------------------------------------
// Orchestration
// ---------------------------------------------------------------------------

fn run() -> ExitCode {
    let dir = data_dir();

    let api_key = std::env::var("CURSOR_API_KEY").unwrap_or_default();
    if api_key.trim().is_empty() {
        println!("CURSOR_API_KEY not set; skipping Cursor usage fetch");
        let summary = json!({ "display_value": "CURSOR_API_KEY not set", "snapshots_today": 0 });
        let _ = write_summary(&dir, &summary);
        return ExitCode::SUCCESS;
    }

    let conn = match Connection::open(dir.join("cursor_usage.db")) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("Failed to open DB: {e}");
            return ExitCode::SUCCESS; // workflow uses continue-on-error; never hard-fail
        }
    };
    if let Err(e) = init_db(&conn, &dir) {
        eprintln!("Failed to init schema: {e}");
        return ExitCode::SUCCESS;
    }

    let now = Utc::now();
    let hour_bucket = now.format("%Y-%m-%dT%H:00:00Z").to_string();
    let today = now.format("%Y-%m-%d").to_string();

    let mut inserted = 0usize;
    let mut api_error: Option<FetchError> = None;

    // Agent edits (daily aggregates).
    match fetch_agent_edits(&api_key, &today, &today) {
        Ok(data) => {
            inserted += process_simple(&conn, &hour_bucket, &data, "agent_edits", "cursor_analytics")
                .unwrap_or(0)
        }
        Err(e) => api_error = Some(e),
    }

    // Tab autocomplete usage.
    if api_error.is_none() {
        match fetch_tabs(&api_key, &today, &today) {
            Ok(data) => {
                inserted +=
                    process_simple(&conn, &hour_bucket, &data, "tabs", "cursor_analytics").unwrap_or(0)
            }
            Err(e) => api_error = Some(e),
        }
    }

    // AI-code commits (per-repo project allocation).
    if api_error.is_none() {
        match fetch_ai_commits(&api_key, &today, &today) {
            Ok(data) => inserted += process_ai_commits(&conn, &hour_bucket, &data).unwrap_or(0),
            Err(e) => api_error = Some(e),
        }
    }

    // On API error, persist whatever was inserted and write a short badge value.
    if let Some(err) = api_error {
        let summary = json!({
            "display_value": err.badge_value(),
            "snapshots_today": 0,
            "recorded_at": hour_bucket,
            "last_7d": {},
            "by_project": {},
            "detail": err.detail(),
        });
        let _ = write_summary(&dir, &summary);
        println!("API error ({}); wrote badge value '{}'", err.detail(), err.badge_value());
        return ExitCode::SUCCESS;
    }

    let summary = match build_summary(&conn, &hour_bucket, inserted) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("Failed to build summary: {e}");
            return ExitCode::SUCCESS;
        }
    };
    let _ = write_summary(&dir, &summary);
    println!("Inserted {inserted} snapshots; summary written to {}", dir.display());
    ExitCode::SUCCESS
}

fn main() -> ExitCode {
    run()
}

// ===========================================================================
// Tests (network-free; mirror the original Python unittest coverage)
// ===========================================================================

#[cfg(test)]
mod tests {
    use super::*;

    fn mem_db() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(EMBEDDED_SCHEMA).unwrap();
        conn
    }

    #[test]
    fn auth_header_uses_basic_auth_with_trailing_colon() {
        let expected = base64::engine::general_purpose::STANDARD.encode("test-api-key:");
        assert_eq!(auth_header("test-api-key"), format!("Basic {expected}"));
    }

    #[test]
    fn auth_error_maps_to_enterprise_detail_and_short_badge() {
        let e = FetchError::Auth;
        assert_eq!(e.badge_value(), "enterprise only");
        assert_eq!(e.detail(), ENTERPRISE_REQUIRED);
    }

    #[test]
    fn other_error_maps_to_unavailable_badge() {
        let e = FetchError::Other("HTTP 500".into());
        assert_eq!(e.badge_value(), "unavailable");
        assert_eq!(e.detail(), "HTTP 500");
    }

    #[test]
    fn simple_payload_inserts_one_row_per_data_entry() {
        let conn = mem_db();
        let payload = json!({ "data": [{ "acceptedLinesAdded": 10 }, { "acceptedLinesAdded": 2 }] });
        let n = process_simple(&conn, "2026-01-01T00:00:00Z", &payload, "agent_edits", "cursor_analytics")
            .unwrap();
        assert_eq!(n, 2);
        let total: i64 = conn
            .query_row("SELECT COUNT(*) FROM usage_snapshots", [], |r| r.get(0))
            .unwrap();
        assert_eq!(total, 2);
    }

    #[test]
    fn missing_data_key_inserts_nothing() {
        let conn = mem_db();
        let n = process_simple(&conn, "2026-01-01T00:00:00Z", &json!({}), "tabs", "cursor_analytics")
            .unwrap();
        assert_eq!(n, 0);
    }

    #[test]
    fn ai_commit_project_and_user_fallbacks() {
        let conn = mem_db();
        let payload = json!({
            "commits": [
                { "repoName": "repo-by-name", "userEmail": "mail@example.com" },
                { "repository": "repo-by-repository", "userId": "fallback-user" },
                { "userId": "no-project" }
            ]
        });
        let n = process_ai_commits(&conn, "2026-01-01T00:00:00Z", &payload).unwrap();
        assert_eq!(n, 3);

        let mut stmt = conn
            .prepare("SELECT project, user_id FROM usage_snapshots WHERE metric_type='ai_commits' ORDER BY id")
            .unwrap();
        let rows: Vec<(Option<String>, Option<String>)> = stmt
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
            .unwrap()
            .map(Result::unwrap)
            .collect();
        assert_eq!(
            rows,
            vec![
                (Some("repo-by-name".into()), Some("mail@example.com".into())),
                (Some("repo-by-repository".into()), Some("fallback-user".into())),
                (None, Some("no-project".into())),
            ]
        );
    }

    #[test]
    fn build_summary_aggregates_metrics_and_projects() {
        let conn = mem_db();
        // Use a current bucket so rows fall inside the rolling 7-day window.
        let hour = Utc::now().format("%Y-%m-%dT%H:00:00Z").to_string();
        let hour = hour.as_str();
        process_simple(&conn, hour, &json!({"data":[{},{}]}), "agent_edits", "cursor_analytics").unwrap();
        process_simple(&conn, hour, &json!({"data":[{}]}), "tabs", "cursor_analytics").unwrap();
        process_ai_commits(
            &conn,
            hour,
            &json!({"commits":[
                {"repoName":"repo-a","userEmail":"d1@x.com"},
                {"repository":"repo-b","userId":"d2"},
                {"repoName":"repo-a","userId":"d3"}
            ]}),
        )
        .unwrap();

        let summary = build_summary(&conn, hour, 6).unwrap();
        assert_eq!(summary["display_value"], "6");
        assert_eq!(summary["snapshots_today"], 6);
        assert_eq!(summary["last_7d"]["agent_edits"], 2);
        assert_eq!(summary["last_7d"]["tabs"], 1);
        assert_eq!(summary["last_7d"]["ai_commits"], 3);
        assert_eq!(summary["by_project"]["repo-a"], 2);
        assert_eq!(summary["by_project"]["repo-b"], 1);
    }
}
