#!/usr/bin/env python3
"""Parse trusted, head-bound crawl-stat evidence from paginated PR comments."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from typing import Any

TRUSTED_ASSOCIATIONS = {"MEMBER"}
MARKER = re.compile(r"<!-- crawl-stats (.*?) -->", re.DOTALL)
HEAD_SHA = re.compile(r"[0-9a-f]{40}")


def _validate_metrics(stats: dict[str, Any]) -> tuple[int, int | float]:
    jobs = stats["jobs"]
    monitor_time = stats["monitor_time"]
    if type(jobs) is not int or jobs < 0:
        raise SystemExit("trusted crawl-stats jobs must be a non-negative integer")
    if (
        type(monitor_time) not in {int, float}
        or not math.isfinite(monitor_time)
        or monitor_time < 0
    ):
        raise SystemExit(
            "trusted crawl-stats monitor_time must be finite and non-negative"
        )
    return jobs, monitor_time


def main() -> None:
    if len(sys.argv) != 2 or HEAD_SHA.fullmatch(sys.argv[1]) is None:
        raise SystemExit("expected an exact lowercase PR head SHA")
    expected_head = sys.argv[1]

    pages = json.load(sys.stdin)
    if not isinstance(pages, list) or not all(isinstance(page, list) for page in pages):
        raise SystemExit("crawl-stats comments response is malformed")

    candidates: list[tuple[str, int, int, int | float, str]] = []
    for comment in (item for page in pages for item in page):
        if not isinstance(comment, dict):
            raise SystemExit("crawl-stats comment entry is malformed")
        body = comment.get("body")
        association = comment.get("author_association")
        if not isinstance(body, str) or association not in TRUSTED_ASSOCIATIONS:
            continue

        marker_mentions = body.count("<!-- crawl-stats")
        if marker_mentions == 0:
            continue
        matches = MARKER.findall(body)
        if marker_mentions != 1 or len(matches) != 1:
            raise SystemExit(
                "trusted crawl-stats comment must contain exactly one marker"
            )
        try:
            stats = json.loads(matches[0])
        except json.JSONDecodeError as exc:
            raise SystemExit("trusted crawl-stats marker contains invalid JSON") from exc
        if not isinstance(stats, dict):
            raise SystemExit("trusted crawl-stats marker has an invalid schema")

        # Comments produced before evidence was bound to a PR revision cannot
        # authorize a merge. Ignore that one exact legacy shape so existing PRs
        # degrade to review-size; all other schema drift fails closed.
        if set(stats) == {"jobs", "monitor_time"}:
            _validate_metrics(stats)
            continue
        if set(stats) != {"head_sha", "jobs", "monitor_time"}:
            raise SystemExit("trusted crawl-stats marker has an invalid schema")

        jobs, monitor_time = _validate_metrics(stats)
        head_sha = stats["head_sha"]
        if not isinstance(head_sha, str) or HEAD_SHA.fullmatch(head_sha) is None:
            raise SystemExit("trusted crawl-stats head_sha must be an exact lowercase SHA")
        if head_sha != expected_head:
            continue

        comment_id = comment.get("id")
        updated_at = comment.get("updated_at")
        if type(comment_id) is not int or not isinstance(updated_at, str) or not updated_at:
            raise SystemExit("trusted crawl-stats comment identity is malformed")
        evidence = {
            "author_association": association,
            "body": body,
            "id": comment_id,
            "updated_at": updated_at,
        }
        fingerprint = hashlib.sha256(
            json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        candidates.append((updated_at, comment_id, jobs, monitor_time, fingerprint))

    if len(candidates) > 1:
        raise SystemExit("multiple trusted crawl-stats comments exist for the current head")
    if candidates:
        _, _, jobs, monitor_time, fingerprint = max(candidates)
        result = {
            "fingerprint": fingerprint,
            "found": True,
            "jobs": jobs,
            "monitor_time": monitor_time,
        }
    else:
        result = {
            "fingerprint": hashlib.sha256(b"no-trusted-crawl-stats").hexdigest(),
            "found": False,
            "jobs": 0,
            "monitor_time": 0,
        }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
