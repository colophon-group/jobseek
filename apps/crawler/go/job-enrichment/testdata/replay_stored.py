"""Offline replay of stored posting titles/HTML; never fetches publisher content."""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import sys
import time
from pathlib import Path

from src.core.enum_normalize import employment_type_implies_intern_level
from src.core.experience_extract import extract_experience
from src.core.occupation_resolve import match_occupation
from src.core.seniority_resolve import match_seniority
from src.core.technology_resolve import match_technologies
from src.runtime.job_enrichment_go import GoJobEnrichment

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("sample", type=Path)
parser.add_argument("--engine", choices=("compare", "python", "go"), default="compare")
parser.add_argument("--binary", default="/usr/local/bin/job-enrichment")
parser.add_argument("--data-dir", type=Path, required=True)
parser.add_argument("--rounds", type=int, default=1)
parser.add_argument("--scope", choices=("classification", "experience"), default="classification")
args = parser.parse_args()
assert 1 <= args.rounds <= 100
raw = args.sample.read_bytes()
rows = json.loads(raw)
client = GoJobEnrichment(args.binary, args.data_dir) if args.engine in {"compare", "go"} else None


def legacy(row):
    if args.scope == "experience":
        result = extract_experience(row["html"])
        return {
            "experience_min": result.min_years if result else None,
            "experience_max": result.max_years if result else None,
        }
    return {
        "titles": [
            {"occupation": match_occupation(t), "seniority": match_seniority(t)}
            for t in row["titles"]
        ],
        "intern": employment_type_implies_intern_level(row.get("employment_type")),
        "technologies": match_technologies(row["html"]),
    }


def native(row):
    assert client is not None
    if args.scope == "experience":
        result = client.request("experience", description=row["html"])
        return {
            key: float(result[key]) if result.get(key) is not None else None
            for key in ("experience_min", "experience_max")
        }
    title = client.request(
        "occupation_seniority",
        titles=row["titles"],
        employment_type=row.get("employment_type") or "",
    )
    tech = client.request("technology", description=row["html"])
    return {
        "titles": title.get("titles", []),
        "intern": title["intern"],
        "technologies": tech["technologies"],
    }


start = time.monotonic()
cpu = time.process_time()
outputs = []
try:
    for _ in range(args.rounds):
        for index, row in enumerate(rows):
            result = native(row) if client else legacy(row)
            if args.engine == "compare":
                expected = legacy(row)
                if result != expected:
                    raise AssertionError(f"classification mismatch at sample row {index}")
            outputs.append(result)
finally:
    if client:
        client.close()
child = resource.getrusage(resource.RUSAGE_CHILDREN)
# RSS units differ by platform. Parent/child maxima are separate, not a
# simultaneous aggregate or a claim about production worker memory.
rss_unit = 1 if sys.platform == "darwin" else 1024
print(
    json.dumps(
        {
            "engine": args.engine,
            "scope": args.scope,
            "sample_rows": len(rows),
            "boards": len({r.get("board_id") for r in rows}),
            "locales": sorted({r.get("locale", "") for r in rows}),
            "completions": len(outputs),
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "output_sha256": hashlib.sha256(
                json.dumps(outputs, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest(),
            "wall_seconds": time.monotonic() - start,
            "cpu_seconds": time.process_time() - cpu + child.ru_utime + child.ru_stime,
            "parent_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * rss_unit,
            "child_peak_rss_bytes": child.ru_maxrss * rss_unit,
        }
    )
)
