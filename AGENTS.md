# AGENTS.md

## Cursor Cloud specific instructions

This repository is a **GitHub Profile README** (the special `username/username` repo). It contains no application code, no build system, and no runtime dependencies.

### Repository contents

| File | Purpose |
|------|---------|
| `README.md` | The profile page rendered by GitHub |
| `.github/mcp-instructions.md` | AI content-maintenance guidelines |
| `github/workflows/codecov.yml` | Placeholder CI workflow (non-functional; references missing `requirements.txt` and `tests/`) |

### Linting

```
markdownlint README.md .github/mcp-instructions.md
```

The README uses extensive inline HTML for layout (badges, tables, collapsible sections), so many `MD033/no-inline-html` warnings are expected and intentional.

### Local preview

```
grip README.md 0.0.0.0:6419
```

Then open `http://localhost:6419/` in Chrome. `grip` renders GitHub-flavored Markdown using GitHub's own CSS. Note: grip may hit GitHub API rate limits for unauthenticated requests; set `GITHUB_TOKEN` env var if that happens.

### Gotchas

- The CI workflow file lives at `github/workflows/codecov.yml` (missing the `.` prefix), so GitHub Actions will not pick it up.
- All dynamic badges/stats in the README are powered by external services (Vercel, Heroku); they require no local setup.
