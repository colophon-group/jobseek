#!/usr/bin/env bash
#
# Determine and apply labels to a company-request PR.
#
# Checks changed files, diff size, and content sanity, then parses
# the crawl-stats comment to decide:
#   auto-merge    — config-only, sane diff, <500 jobs, crawl <=60s
#   review-size   — >=500 jobs (or missing stats)
#   review-load   — monitor crawl >60s
#   review-code   — unexpected files or suspicious diff content
#
# Required env vars: GH_TOKEN, PR, REPO, GITHUB_OUTPUT

set -euo pipefail

: "${PR:?PR is required}"
: "${REPO:?REPO is required}"

ALLOWED_FILES="apps/crawler/data/companies.csv apps/crawler/data/boards.csv apps/crawler/VERSION"
VALID_MONITOR_TYPES="amazon|ashby|gem|greenhouse|hireology|join|lever|personio|pinpoint|recruitee|rippling|rss|smartrecruiters|workable|workday|sitemap|nextdata|dom|api_sniffer"
VALID_SCRAPER_TYPES="json-ld|dom|nextdata|embedded|api_sniffer"
SLUG_RE='^[a-z0-9]+(-[a-z0-9]+)*$'
URL_RE='^https?://'
MAX_ADDED_LINES=5

# --- Check changed files ---

FILES=$(gh pr diff "$PR" --repo "$REPO" --name-only)
echo "Changed files:"
echo "$FILES"

CONFIG_ONLY=true
while IFS= read -r f; do
  [ -z "$f" ] && continue
  MATCH=false
  for ALLOWED in $ALLOWED_FILES; do
    if [ "$f" = "$ALLOWED" ]; then
      MATCH=true
      break
    fi
  done
  # Allow image files under data/images/
  if [ "$MATCH" != "true" ]; then
    case "$f" in
      apps/crawler/data/images/*) MATCH=true ;;
      apps/crawler/src/workspace/kb/*.md) MATCH=true ;;
    esac
  fi
  if [ "$MATCH" != "true" ]; then
    echo "::warning::Unexpected file: $f"
    CONFIG_ONLY=false
  fi
done <<< "$FILES"

# --- Check diff size and content ---

DIFF=$(gh pr diff "$PR" --repo "$REPO")
# Count/validate only CSV hunks so non-CSV assets (e.g. KB markdown) don't affect CSV checks.
CSV_DIFF=$(echo "$DIFF" | awk '
  BEGIN { in_csv = 0 }
  /^diff --git / {
    in_csv = ($0 ~ /^diff --git a\/apps\/crawler\/data\/(companies|boards)\.csv b\/apps\/crawler\/data\/(companies|boards)\.csv$/)
  }
  in_csv { print }
')
RAW_ADDED=$(echo "$CSV_DIFF" | grep -c '^+[^+]' || true)
RAW_REMOVED=$(echo "$CSV_DIFF" | grep -c '^-[^-]' || true)
ADDED_LINES=$((RAW_ADDED - RAW_REMOVED))
[ "$ADDED_LINES" -lt 0 ] && ADDED_LINES=0
echo "Net added lines (CSVs only): $ADDED_LINES (max $MAX_ADDED_LINES)"

DIFF_OK=true

if [ "$ADDED_LINES" -gt "$MAX_ADDED_LINES" ]; then
  echo "::warning::Too many added lines ($ADDED_LINES > $MAX_ADDED_LINES)"
  DIFF_OK=false
fi

# parse_csv_line: use Python's csv module to correctly handle quoted fields
# containing commas (e.g. JSON config). Outputs fields tab-separated.
parse_csv_line() {
  python3 -c "
import csv, sys
for row in csv.reader([sys.argv[1]]):
    print('\t'.join(row))
" "$1"
}

# Validate each added line in the diff (skip diff headers)
while IFS= read -r line; do
  # Strip the leading "+"
  content="${line:1}"

  # Skip empty lines and CSV headers
  [ -z "$content" ] && continue
  echo "$content" | grep -qE '^(slug,|company_slug,)' && continue

  # Parse with CSV-aware splitter
  PARSED=$(parse_csv_line "$content")
  FIELD_COUNT=$(echo "$PARSED" | awk -F'\t' '{print NF}')

  if [ "$FIELD_COUNT" -ne 6 ] && [ "$FIELD_COUNT" -ne 7 ]; then
    echo "::warning::Unexpected field count ($FIELD_COUNT): $content"
    DIFF_OK=false
    continue
  fi

  if [ "$FIELD_COUNT" -eq 6 ]; then
    # companies.csv: slug,name,website,logo_url,icon_url,logo_type
    SLUG=$(echo "$PARSED" | cut -d$'\t' -f1)
    WEBSITE=$(echo "$PARSED" | cut -d$'\t' -f3)

    if ! echo "$SLUG" | grep -qE "$SLUG_RE"; then
      echo "::warning::Invalid slug: $SLUG"
      DIFF_OK=false
    fi
    if [ -n "$WEBSITE" ] && ! echo "$WEBSITE" | grep -qE "$URL_RE"; then
      echo "::warning::Invalid website URL: $WEBSITE"
      DIFF_OK=false
    fi
  fi

  if [ "$FIELD_COUNT" -eq 7 ]; then
    # boards.csv: company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config
    SLUG=$(echo "$PARSED" | cut -d$'\t' -f1)
    BOARD_URL=$(echo "$PARSED" | cut -d$'\t' -f3)
    MONITOR=$(echo "$PARSED" | cut -d$'\t' -f4)
    SCRAPER=$(echo "$PARSED" | cut -d$'\t' -f6)

    if ! echo "$SLUG" | grep -qE "$SLUG_RE"; then
      echo "::warning::Invalid company_slug: $SLUG"
      DIFF_OK=false
    fi
    if ! echo "$BOARD_URL" | grep -qE "$URL_RE"; then
      echo "::warning::Invalid board_url: $BOARD_URL"
      DIFF_OK=false
    fi
    if ! echo "$MONITOR" | grep -qE "^($VALID_MONITOR_TYPES)$"; then
      echo "::warning::Invalid monitor_type: $MONITOR"
      DIFF_OK=false
    fi
    if [ -n "$SCRAPER" ] && ! echo "$SCRAPER" | grep -qE "^($VALID_SCRAPER_TYPES)$"; then
      echo "::warning::Invalid scraper_type: $SCRAPER"
      DIFF_OK=false
    fi
  fi
done < <(echo "$CSV_DIFF" | grep '^+[^+]' || true)

# --- Check PR completeness (full file inspection) ---

INCOMPLETE=false

COMPLETENESS=$(python3 - "$PR" "$REPO" <<'PYEOF'
import csv, io, json, subprocess, sys

pr, repo = sys.argv[1], sys.argv[2]

branch = subprocess.check_output(
    ["gh", "pr", "view", pr, "--repo", repo, "--json", "headRefName", "-q", ".headRefName"],
    text=True,
).strip()

def get_raw(ref, path):
    try:
        return subprocess.check_output(
            ["gh", "api", f"repos/{repo}/contents/{path}",
             "-H", "Accept: application/vnd.github.raw+json",
             "-f", f"ref={ref}"],
            text=True, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return ""

def parse_csv(text):
    if not text.strip():
        return []
    return list(csv.DictReader(io.StringIO(text)))

pr_companies = parse_csv(get_raw(branch, "apps/crawler/data/companies.csv"))
pr_boards = parse_csv(get_raw(branch, "apps/crawler/data/boards.csv"))
main_companies = parse_csv(get_raw("main", "apps/crawler/data/companies.csv"))

main_slugs = {r["slug"] for r in main_companies}
new_slugs = {r["slug"] for r in pr_companies if r["slug"] not in main_slugs}

if not new_slugs:
    print("ok")
    sys.exit(0)

issues = []
for slug in sorted(new_slugs):
    co = next((r for r in pr_companies if r["slug"] == slug), None)
    if not co:
        continue

    if not co.get("name", "").strip():
        issues.append(f"{slug}: missing name")
    if not co.get("website", "").strip():
        issues.append(f"{slug}: missing website")
    if not co.get("logo_url", "").strip():
        issues.append(f"{slug}: missing logo_url")
    if not co.get("icon_url", "").strip():
        issues.append(f"{slug}: missing icon_url")

    boards = [r for r in pr_boards if r.get("company_slug") == slug]
    if not boards:
        issues.append(f"{slug}: no boards configured")
    else:
        for b in boards:
            alias = b.get("board_slug", "?")
            if not b.get("board_url", "").strip():
                issues.append(f"{slug}/{alias}: missing board_url")
            if not b.get("monitor_type", "").strip():
                issues.append(f"{slug}/{alias}: missing monitor_type")

if issues:
    for i in issues:
        print(f"incomplete:{i}")
else:
    print("ok")
PYEOF
)

while IFS= read -r line; do
  case "$line" in
    incomplete:*)
      echo "::warning::Incomplete: ${line#incomplete:}"
      INCOMPLETE=true
      ;;
  esac
done <<< "$COMPLETENESS"

# --- Parse crawl-stats comment ---

STATS_FOUND=false
JOBS=0
MONITOR_TIME=0

COMMENTS=$(gh api "repos/$REPO/issues/$PR/comments" --jq '.[].body')
STATS_LINE=$(echo "$COMMENTS" | grep '<!-- crawl-stats' | tail -1 || true)

if [ -n "$STATS_LINE" ]; then
  JSON=$(echo "$STATS_LINE" | sed 's/.*<!-- crawl-stats //;s/ -->.*//')
  echo "Crawl stats: $JSON"

  JOBS=$(echo "$JSON" | jq -r '.jobs // 0')
  MONITOR_TIME=$(echo "$JSON" | jq -r '.monitor_time // 0')
  STATS_FOUND=true
else
  echo "::warning::No crawl-stats comment found"
fi

# --- Determine labels ---

LABELS=""

if [ "$INCOMPLETE" = "true" ]; then
  LABELS="incomplete"
elif [ "$CONFIG_ONLY" != "true" ]; then
  LABELS="review-code"
elif [ "$DIFF_OK" != "true" ]; then
  LABELS="review-code"
elif [ "$STATS_FOUND" != "true" ]; then
  LABELS="review-size"
elif [ "$JOBS" -ge 500 ] 2>/dev/null; then
  LABELS="review-size"
else
  LABELS="auto-merge"
fi

# review-load is independent — based on monitor crawl time
if [ "$STATS_FOUND" = "true" ]; then
  SLOW=$(echo "$MONITOR_TIME > 60" | bc -l 2>/dev/null || echo 0)
  if [ "$SLOW" = "1" ]; then
    LABELS="$LABELS,review-load"
  fi
fi

# --- Apply labels (remove stale ones first) ---

ALL_DECISION_LABELS="auto-merge review-code review-size review-load incomplete"
for L in $ALL_DECISION_LABELS; do
  gh pr edit "$PR" --repo "$REPO" --remove-label "$L" 2>/dev/null || true
done

IFS=',' read -ra LABEL_ARR <<< "$LABELS"
for L in "${LABEL_ARR[@]}"; do
  gh label create "$L" --repo "$REPO" 2>/dev/null || true
  gh pr edit "$PR" --repo "$REPO" --add-label "$L"
done

echo "Applied labels: $LABELS"
echo "labels=$LABELS" >> "$GITHUB_OUTPUT"
