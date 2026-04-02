"""Bulk-create company requests in the Supabase DB + GitHub issues.

Reads the list from /tmp/bulk_company_requests.json (built by earlier analysis).
Checks for existing DB rows and GitHub issues before creating new ones.
Respects GitHub API rate limits (core: 5000/hr for app auth).

Usage:
    uv run python scripts/bulk_company_requests.py [--dry-run]
"""
from __future__ import annotations

import json
import os
import sys
import time
import re
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# Load credentials from crawler .env.local (check worktree and main repo)
script_dir = Path(__file__).resolve().parent.parent
env_path = script_dir / "apps" / "crawler" / ".env.local"
if not env_path.exists():
    env_path = Path.home() / "jobseek" / "apps" / "crawler" / ".env.local"
load_dotenv(env_path)

DATABASE_URL = os.environ["DATABASE_URL"]
GITHUB_APP_ID = os.environ["GITHUB_APP_ID"]
GITHUB_APP_PRIVATE_KEY = os.environ["GITHUB_APP_PRIVATE_KEY"]
GITHUB_APP_INSTALLATION_ID = os.environ["GITHUB_APP_INSTALLATION_ID"]

REPO_OWNER = "colophon-group"
REPO_NAME = "jobseek"

DRY_RUN = "--dry-run" in sys.argv

# ── GitHub App auth ──────────────────────────────────────────────────

import jwt
import requests

def get_installation_token() -> str:
    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + 600, "iss": GITHUB_APP_ID}
    token = jwt.encode(payload, GITHUB_APP_PRIVATE_KEY, algorithm="RS256")
    resp = requests.post(
        f"https://api.github.com/app/installations/{GITHUB_APP_INSTALLATION_ID}/access_tokens",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    resp.raise_for_status()
    return resp.json()["token"]


class GitHubClient:
    def __init__(self):
        self.token = get_installation_token()
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github+json",
        })
        self.requests_made = 0
        self.rate_remaining = 5000
        self.rate_reset = 0

    def _update_rate_limit(self, resp: requests.Response):
        self.rate_remaining = int(resp.headers.get("x-ratelimit-remaining", 5000))
        self.rate_reset = int(resp.headers.get("x-ratelimit-reset", 0))

    def _wait_if_needed(self):
        if self.rate_remaining < 100:
            wait = max(self.rate_reset - int(time.time()), 1)
            print(f"  Rate limit low ({self.rate_remaining} remaining), sleeping {wait}s...")
            time.sleep(wait)

    def fetch_all_company_request_issues(self) -> dict[str, int]:
        """Fetch ALL company-request issues in bulk. Returns {lowercase_title: issue_number}."""
        issues = {}
        page = 1
        while True:
            resp = self.session.get(
                f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/issues",
                params={
                    "labels": "company-request",
                    "state": "all",
                    "per_page": 100,
                    "page": page,
                },
            )
            self.requests_made += 1
            self._update_rate_limit(resp)
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            for issue in batch:
                issues[issue["title"].lower()] = issue["number"]
            print(f"  Fetched page {page}: {len(batch)} issues (total: {len(issues)})")
            page += 1
            time.sleep(0.5)  # gentle pacing
        return issues

    def create_issue(self, title: str, body: str, labels: list[str]) -> int:
        resp = self.session.post(
            f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/issues",
            json={"title": title, "body": body, "labels": labels},
        )
        self.requests_made += 1
        self._update_rate_limit(resp)
        if resp.status_code == 403:
            wait = max(self.rate_reset - int(time.time()), 60)
            print(f"  Rate limited, sleeping {wait}s...")
            time.sleep(wait)
            return self.create_issue(title, body, labels)
        resp.raise_for_status()
        self._wait_if_needed()
        time.sleep(1)  # 1 req/sec pacing for creates
        return resp.json()["number"]


# ── Normalization (matches the web app logic) ────────────────────────

TRACKING_PARAMS = re.compile(r"^(utm_\w+|ref|source|fbclid|gclid|mc_[a-z]+)$", re.I)

def normalize_url(raw: str) -> str:
    from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
    try:
        p = urlparse(raw)
        params = {k: v for k, v in parse_qs(p.query).items() if not TRACKING_PARAMS.match(k)}
        query = urlencode(params, doseq=True)
        path = p.path.rstrip("/") if len(p.path) > 1 else p.path
        return urlunparse((p.scheme, p.hostname or p.netloc, path, "", query, ""))
    except Exception:
        return raw

def normalize_input(raw: str) -> str:
    trimmed = " ".join(raw.strip().split())
    if re.match(r"^https?://", trimmed, re.I):
        return normalize_url(trimmed)
    return trimmed.lower()


def build_issue_body(input_text: str, name: str, estimated_jobs: int, source: str) -> str:
    lines = [
        "A user requested to add a company or fix an existing scraper.",
        "",
        "### User request",
        "",
        input_text,
        "",
        "### User context",
        "",
        f"- **Source:** Apify dataset ({source})",
        f"- **Company name:** {name}",
        f"- **Estimated jobs:** {estimated_jobs:,}",
        "",
    ]
    return "\n".join(lines)


def main():
    with open("/tmp/bulk_company_requests.json") as f:
        companies = json.load(f)

    print(f"Loaded {len(companies)} companies to process")
    if DRY_RUN:
        print("DRY RUN — no DB writes or GitHub issues will be created\n")

    # Connect to Supabase
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Check existing DB entries
    cur.execute("SELECT input, github_issue_number FROM company_request")
    existing_db = {row["input"]: row["github_issue_number"] for row in cur.fetchall()}
    print(f"Existing DB entries: {len(existing_db)}")

    # Fetch all existing company-request issues from GitHub in bulk
    gh = GitHubClient()
    print("Fetching existing company-request issues from GitHub...")
    existing_issues = gh.fetch_all_company_request_issues()
    print(f"Found {len(existing_issues)} existing GitHub issues\n")

    created = 0
    skipped_db = 0
    skipped_gh = 0
    backfilled = 0
    errors = 0

    for i, company in enumerate(companies):
        raw_input = company["input"]
        normalized = normalize_input(raw_input)
        name = company["name"]
        estimated_jobs = company["estimated_jobs"]
        source = company["source"]
        title = f"Add company: {name}"

        print(f"[{i+1}/{len(companies)}] {name}")

        # 1. Check DB
        if normalized in existing_db:
            issue_num = existing_db[normalized]
            if issue_num:
                print(f"  SKIP: already in DB with issue #{issue_num}")
                skipped_db += 1
                continue
            else:
                print(f"  DB entry exists but no issue — will backfill")
                if not DRY_RUN:
                    body = build_issue_body(normalized, name, estimated_jobs, source)
                    try:
                        issue_number = gh.create_issue(title, body, ["company-request"])
                        cur.execute(
                            "UPDATE company_request SET github_issue_number = %s, updated_at = now() WHERE input = %s",
                            (issue_number, normalized),
                        )
                        print(f"  BACKFILLED: issue #{issue_number}")
                        backfilled += 1
                    except Exception as e:
                        print(f"  ERROR creating issue: {e}")
                        errors += 1
                else:
                    print(f"  DRY RUN: would backfill issue")
                    backfilled += 1
                continue

        # 2. Check GitHub issue index (pre-fetched in bulk)
        existing_issue_num = existing_issues.get(title.lower())
        if existing_issue_num:
            print(f"  SKIP: GitHub issue already exists: #{existing_issue_num}")
            if not DRY_RUN:
                try:
                    cur.execute(
                        """INSERT INTO company_request (input, last_user_hint, github_issue_number)
                           VALUES (%s, %s, %s)
                           ON CONFLICT (input) DO UPDATE SET
                             github_issue_number = EXCLUDED.github_issue_number,
                             updated_at = now()""",
                        (normalized, json.dumps({"source": f"apify-{source}"}), existing_issue_num),
                    )
                except Exception as e:
                    print(f"  ERROR inserting DB entry: {e}")
            skipped_gh += 1
            continue

        # 3. Create new: DB entry + GitHub issue
        if DRY_RUN:
            print(f"  DRY RUN: would create DB entry + GitHub issue")
            created += 1
            continue

        try:
            cur.execute(
                """INSERT INTO company_request (input, last_user_hint)
                   VALUES (%s, %s)
                   ON CONFLICT (input) DO NOTHING
                   RETURNING id""",
                (normalized, json.dumps({"source": f"apify-{source}"})),
            )
            row = cur.fetchone()
            if not row:
                print(f"  SKIP: race condition — already in DB")
                skipped_db += 1
                continue

            body = build_issue_body(normalized, name, estimated_jobs, source)
            issue_number = gh.create_issue(title, body, ["company-request"])

            cur.execute(
                "UPDATE company_request SET github_issue_number = %s WHERE id = %s",
                (issue_number, row["id"]),
            )
            print(f"  CREATED: issue #{issue_number}")
            created += 1

        except Exception as e:
            print(f"  ERROR: {e}")
            errors += 1

    cur.close()
    conn.close()

    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"  Created:          {created}")
    print(f"  Skipped (in DB):  {skipped_db}")
    print(f"  Skipped (GH):     {skipped_gh}")
    print(f"  Backfilled:       {backfilled}")
    print(f"  Errors:           {errors}")
    print(f"  GH requests made: {gh.requests_made}")
    print(f"  GH rate remaining:{gh.rate_remaining}")


if __name__ == "__main__":
    main()
