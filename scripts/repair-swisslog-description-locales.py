"""Restore nine verified Swisslog primary locales without fetching publishers.

Run from the attested crawler image under the host maintenance lock, after
supported B0 rollback and selector cleanup. Dry-run is the default. Mount this
script and its reviewed manifest read-only; credentials come from the normal
maintenance environment. Never run against a different runtime revision.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from uuid import UUID

import asyncpg


def validate_posting(
    row: dict, bodies: list[dict], expected: dict, company_id: str
) -> bool:
    """Fail closed on changed scope/body; return whether the locale needs repair."""
    if (
        str(row["company_id"]) != company_id
        or row["source_url"] != expected["source_url"]
        or not row["is_active"]
        or row["scrape_is_leased"]
    ):
        raise ValueError("posting ownership, source, activity or lease changed")
    if len(bodies) != 1:
        raise ValueError(
            "posting must have exactly one authoritative description locale"
        )
    body = bodies[0]
    if (
        body["locale"] != expected["locale"]
        or body["r2_uploaded"] is not True
        or hashlib.sha256(body["html"].encode()).hexdigest() != expected["html_sha256"]
    ):
        raise ValueError(
            "stored description no longer matches the verified uploaded body"
        )
    if row["locales"] == [expected["locale"]]:
        return False
    if row["locales"] != expected["old_locales"]:
        raise ValueError("posting locales changed since the reviewed snapshot")
    return True


async def run(args: argparse.Namespace) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_crawler_revision):
        raise ValueError("expected crawler revision must be a full commit SHA")
    if os.environ.get("JOBSEEK_DEPLOY_REVISION") != args.expected_crawler_revision:
        raise ValueError("runtime revision does not match the approved repair revision")
    manifest = json.loads(Path(args.manifest).read_text())
    expected = manifest["postings"]
    if len(expected) != 9 or len({p["id"] for p in expected}) != 9:
        raise ValueError(
            "repair requires the nine distinct reviewed posting identities"
        )
    company_id = str(UUID(manifest["company_id"]))
    if any(
        p["old_locales"] != ["en"] or p["locale"] not in {"de", "sv"} for p in expected
    ):
        raise ValueError("unexpected locale transition")
    connection = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"], timeout=10)
    try:
        async with connection.transaction(readonly=not args.apply):
            await connection.execute("SET LOCAL statement_timeout = '15s'")
            await connection.execute("SET LOCAL lock_timeout = '5s'")
            rows = await connection.fetch(
                """SELECT id, company_id, source_url, locales, is_active,
                          COALESCE(leased_until > now(), false) AS scrape_is_leased
                   FROM job_posting WHERE id = ANY($1::uuid[]) ORDER BY id"""
                + (" FOR UPDATE" if args.apply else ""),
                [UUID(p["id"]) for p in expected],
            )
            if len(rows) != 9:
                raise ValueError("one or more reviewed postings are missing")
            by_id = {str(r["id"]): dict(r) for r in rows}
            proposals = []
            for p in expected:
                bodies = await connection.fetch(
                    "SELECT locale, html, r2_uploaded FROM descriptions WHERE posting_id=$1"
                    + (" FOR SHARE" if args.apply else ""),
                    UUID(p["id"]),
                )
                needs_change = validate_posting(
                    by_id[p["id"]], list(bodies), p, company_id
                )
                proposals.append(
                    {
                        "id": p["id"],
                        "from": ["en"],
                        "to": [p["locale"]],
                        "needs_change": needs_change,
                    }
                )
            if args.apply:
                for p in proposals:
                    if not p["needs_change"]:
                        continue
                    status = await connection.execute(
                        """UPDATE job_posting SET locales=$2, updated_at=now()
                           WHERE id=$1 AND locales=$3""",
                        UUID(p["id"]),
                        p["to"],
                        p["from"],
                    )
                    if status != "UPDATE 1":
                        raise ValueError("reviewed locale update lost its precondition")
            return {
                "mode": "apply" if args.apply else "dry-run",
                "verified": 9,
                "changed": sum(p["needs_change"] for p in proposals)
                if args.apply
                else 0,
                "proposals": proposals,
            }
    finally:
        await connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--expected-crawler-revision", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), sort_keys=True))
    except (ValueError, KeyError) as error:
        raise SystemExit(f"Repair refused: {error}") from None
    except Exception as error:  # noqa: BLE001 - never expose connection credentials
        # Connection errors may contain addresses/credentials; report class only.
        raise SystemExit(f"Repair failed: {type(error).__name__}") from None


if __name__ == "__main__":
    main()
