#!/usr/bin/env bash
set -euo pipefail

echo "Refresh mode: ${REFRESH_EXECUTION_MODE:-unspecified}"

mkdir -p assets/stats

CURL_CONNECT_TIMEOUT="${CURL_CONNECT_TIMEOUT:-25}"
CURL_MAX_TIME="${CURL_MAX_TIME:-120}"
CURL_RETRY="${CURL_RETRY:-8}"
CURL_RETRY_DELAY="${CURL_RETRY_DELAY:-5}"
CURL_RETRY_MAX_TIME="${CURL_RETRY_MAX_TIME:-300}"

fetch_svg() {
  local output="$1"
  local url="$2"
  local tmp
  tmp="$(mktemp)"

  if curl -fsSL \
    --connect-timeout "$CURL_CONNECT_TIMEOUT" \
    --max-time "$CURL_MAX_TIME" \
    --retry "$CURL_RETRY" \
    --retry-delay "$CURL_RETRY_DELAY" \
    --retry-max-time "$CURL_RETRY_MAX_TIME" \
    "$url" -o "$tmp"; then
    if grep -qi "<svg" "$tmp"; then
      mv "$tmp" "$output"
      echo "Updated $(basename "$output")"
      return 0
    fi
    echo "::warning::Non-SVG payload returned for $(basename "$output"); keeping previous file."
  else
    echo "::warning::Fetch failed for $(basename "$output"); keeping previous file."
  fi

  rm -f "$tmp"
  return 1
}

successes=0
failures=0

if fetch_svg "assets/stats/github-stats.svg" "https://github-readme-stats.vercel.app/api?username=MAKaminski&show_icons=true&theme=radical&include_all_commits=true&count_private=true&rank_icon=github"; then successes=$((successes + 1)); else failures=$((failures + 1)); fi
if fetch_svg "assets/stats/github-streak.svg" "https://github-readme-streak-stats.herokuapp.com/?user=MAKaminski&theme=radical&border_radius=10"; then successes=$((successes + 1)); else failures=$((failures + 1)); fi
if fetch_svg "assets/stats/profile-details.svg" "https://github-profile-summary-cards.vercel.app/api/cards/profile-details?username=MAKaminski&theme=radical"; then successes=$((successes + 1)); else failures=$((failures + 1)); fi
if fetch_svg "assets/stats/productive-time.svg" "https://github-profile-summary-cards.vercel.app/api/cards/productive-time?username=MAKaminski&theme=radical"; then successes=$((successes + 1)); else failures=$((failures + 1)); fi
if fetch_svg "assets/stats/repos-per-language.svg" "https://github-profile-summary-cards.vercel.app/api/cards/repos-per-language?username=MAKaminski&theme=radical"; then successes=$((successes + 1)); else failures=$((failures + 1)); fi
if fetch_svg "assets/stats/top-langs.svg" "https://github-readme-stats.vercel.app/api/top-langs/?username=MAKaminski&layout=compact&theme=radical&border_radius=10&langs_count=8"; then successes=$((successes + 1)); else failures=$((failures + 1)); fi
if fetch_svg "assets/stats/most-commit-language.svg" "https://github-profile-summary-cards.vercel.app/api/cards/most-commit-language?username=MAKaminski&theme=radical"; then successes=$((successes + 1)); else failures=$((failures + 1)); fi
if fetch_svg "assets/stats/activity-graph.svg" "https://github-readme-activity-graph.vercel.app/graph?username=MAKaminski&theme=react-dark&hide_border=true"; then successes=$((successes + 1)); else failures=$((failures + 1)); fi

echo "Stats refresh summary: success=${successes}, failed=${failures}"
if [ "$successes" -eq 0 ]; then
  echo "::warning::No stat cards refreshed this run."
fi

tmp_trophy="$(mktemp)"
if curl -fsSL \
  --connect-timeout "$CURL_CONNECT_TIMEOUT" \
  --max-time "$CURL_MAX_TIME" \
  --retry "$CURL_RETRY" \
  --retry-delay "$CURL_RETRY_DELAY" \
  --retry-max-time "$CURL_RETRY_MAX_TIME" \
  "https://github-profile-trophy.vercel.app/?username=MAKaminski&theme=radical&row=2&column=3&title=Followers,Stars,Commits,Repositories,PullRequest,MultiLanguage" \
  -o "$tmp_trophy" && grep -qi "<svg" "$tmp_trophy"; then
  mv "$tmp_trophy" assets/stats/trophies.svg
  echo "Updated trophies.svg"
else
  rm -f "$tmp_trophy"
  echo "::warning::Trophies endpoint unavailable; keeping previous file."
fi

if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "DRY_RUN enabled; skipping commit/push."
  exit 0
fi

git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"
git add assets/stats/
if git diff --staged --quiet; then
  echo "No changes to commit"
else
  git commit -m "chore: refresh profile stats [automated]"
  if git pull --rebase origin main && git push; then
    echo "Push succeeded"
    exit 0
  fi

  for delay in 4 8 16 32; do
    echo "::warning::Push failed; retrying in ${delay}s"
    sleep "$delay"
    if git pull --rebase origin main && git push; then
      echo "Push succeeded after retry"
      exit 0
    fi
  done

  echo "::warning::All push attempts failed; refresh commit remains local to this run."
fi
