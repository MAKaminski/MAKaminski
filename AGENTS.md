# AGENTS.md

## Repository purpose
This repo is the special `username/username` profile repository for **MAKaminski**.
The main artifact is `README.md`, which renders directly on the GitHub profile.

## Files that matter
- `README.md` - public profile content
- `.github/mcp-instructions.md` - guardrails for profile edits

## Working rules for agents
1. Prefer factual accuracy over visual flair.
2. Do not add claims about employment history, awards, sponsorships, or projects unless they are verifiable from public profile context the user provided.
3. Do not reintroduce removed legacy background sections unless the user explicitly asks.
4. Keep dynamic dashboard cards reliable (avoid broken widgets and unstable embeds).
5. Preserve existing user-approved wording unless there is a direct request to change it.

## Source hierarchy for profile facts
1. Explicit user instruction in chat
2. Public profile context the user points to (website, LinkedIn, GitHub)
3. Existing approved README content

If sources conflict, ask for clarification or choose the most conservative wording.

## Editing guidance
- Keep markdown valid for GitHub rendering.
- Avoid placeholders like `yourlinkedin`.
- Prefer HTTPS links.
- Avoid hardcoded metrics that can drift (or clearly label them as static).

## Non-goals
- Do not add application code, build tooling, or runtime dependencies *beyond the sanctioned profile-automation tooling* in `cursor-usage/` (a small Rust binary that powers the Cursor-usage badge) and the GitHub Actions under `.github/workflows/`.
- Do not invent timelines, titles, or company affiliations.

## Profile automation (`cursor-usage/`)
- The Cursor-usage badge is backed by a Rust binary (`cursor-usage/`, tested with `cargo test`).
- Keep the on-disk data contract stable: `data/cursor_usage.db` (SQLite via `data/schema.sql`) and `data/cursor_usage_latest.json` (badge reads `$.display_value`).
- Prefer graceful, honest fallbacks for the badge (`enterprise only`, `unavailable`) over broken-looking placeholder text.
