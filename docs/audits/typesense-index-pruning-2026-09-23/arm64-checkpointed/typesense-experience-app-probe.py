"""Measure the app's complete experience expression with equivalent ordering."""

import importlib.util
import json
import statistics
import time
from pathlib import Path

root = Path("/tmp/typesense-rss-rehearsal-v2-20260923")
while not (root / "experience-order-diagnostic.json").exists():
    time.sleep(2)
script = Path(
    "/Users/Viktor/.codex/worktrees/typesense-rss-pruning/jobseek/scripts/typesense-rss-lab.py"
)
spec = importlib.util.spec_from_file_location("rss_lab", script)
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)
state = json.loads((root / "state.json").read_text())
original = "((experience_min_years:<=6 && experience_max_years:>=3) || experience_min_years:=-1 || (experience_min:<=6 && experience_max:>=3) || experience_min:=-1)"
reordered = "((experience_max_years:>=3 && experience_min_years:<=6) || experience_min_years:=-1 || (experience_max:>=3 && experience_min:<=6) || experience_min:=-1)"
base = "is_active:true && has_content:!=false"
shapes = {
    "ungrouped": {
        "q": "*",
        "query_by": "title",
        "sort_by": "first_seen_at:desc",
        "per_page": 50,
    },
    "grouped": {
        "q": "*",
        "query_by": "title",
        "sort_by": "first_seen_at:desc",
        "group_by": "company_id",
        "group_limit": 10,
        "limit": 11,
        "offset": 0,
    },
    "count": {"q": "*", "query_by": "title", "per_page": 0},
    "stable_candidates": {**lab.query_corpus(state)["stable_candidates"]},
}
result = {
    "diagnostic_only": True,
    "architecture": "arm64",
    "app_filter_source": "apps/web/src/lib/search/typesense-filters.ts:173",
    "cases": {},
}
for case, shape in shapes.items():
    variants = {
        "original": {
            **shape,
            "filter_by": base + " && " + original,
            "use_cache": "false",
        },
        "max_first": {
            **shape,
            "filter_by": base + " && " + reordered,
            "use_cache": "false",
        },
        "experience_first": {
            **shape,
            "filter_by": reordered + " && " + base,
            "use_cache": "false",
        },
    }
    rows = {k: [] for k in variants}
    for i in range(-2, 15):
        order = list(variants)
        order = order[i % 3 :] + order[: i % 3]
        for name in order:
            t = time.monotonic()
            response = lab.lab._search(state["port"], variants[name])
            elapsed = (time.monotonic() - t) * 1000
            assert not response.get("search_cutoff")
            if i >= 0:
                rows[name].append(
                    {
                        "server_ms": response["search_time_ms"],
                        "client_ms": round(elapsed, 3),
                        "projection_sha256": lab.digest(lab.project(case, response)),
                        "found": response.get("found"),
                        "found_docs": response.get("found_docs"),
                    }
                )
    hashes = {x["projection_sha256"] for values in rows.values() for x in values}
    summaries = {
        name: {
            "median_ms": statistics.median(x["server_ms"] for x in values),
            "p95_ms": lab.lab._percentile([x["server_ms"] for x in values], 0.95),
        }
        for name, values in rows.items()
    }
    result["cases"][case] = {
        "parameters": variants,
        "samples": rows,
        "summaries": summaries,
        "all_results_identical": len(hashes) == 1,
    }
    print(
        json.dumps(
            {case: {"summaries": summaries, "all_results_identical": len(hashes) == 1}}
        ),
        flush=True,
    )
target = root / "experience-app-diagnostic.json"
assert not target.exists()
lab.write(target, result)
