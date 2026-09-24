"""Test equivalent filter execution orders on the owned pruned ARM64 lab."""

import importlib.util
import json
import statistics
import subprocess
import time
from pathlib import Path

root = Path("/tmp/typesense-rss-rehearsal-v2-20260923")
script = Path(
    "/Users/Viktor/.codex/worktrees/typesense-rss-pruning/jobseek/scripts/typesense-rss-lab.py"
)
spec = importlib.util.spec_from_file_location("rss_lab", script)
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)
state = json.loads((root / "state.json").read_text())
runtime = json.loads(
    subprocess.check_output(["docker", "inspect", state["container"]])
)[0]
assert runtime["Config"]["Labels"].get(lab.LABEL) == str(root.resolve())
assert not runtime["State"]["Running"]
started = time.time()
subprocess.run(["docker", "start", state["container"]], check=True)
lab.lab._wait_for_health(state["port"], timeout=1800)
lab.lab._wait_for_document_count(state["port"], 5554279, timeout=1800)
lab.wait_for_writes(state)
lab.lab._wait_for_semantic_readiness(state["port"], timeout=1800)
print(
    json.dumps({"diagnostic_restart_ready_seconds": round(time.time() - started, 3)}),
    flush=True,
)
time.sleep(45)
query = {**lab.query_corpus(state)["decimal_experience"], "use_cache": "false"}
variants = {
    "original": query,
    "max_first": {
        **query,
        "filter_by": "is_active:true && has_content:!=false && experience_max_years:>=2.5 && experience_min_years:<=6.5",
    },
    "range_first": {
        **query,
        "filter_by": "experience_max_years:>=2.5 && experience_min_years:<=6.5 && is_active:true && has_content:!=false",
    },
}
rows = {k: [] for k in variants}
for i in range(-2, 20):
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
                    "projection_sha256": lab.digest(
                        lab.project("decimal_experience", response)
                    ),
                    "found": response.get("found"),
                }
            )
baseline = json.loads((root / "baseline-checkpoint.json").read_text())["queries"][
    "decimal_experience"
]["projection_sha256"]
result = {
    "diagnostic_only": True,
    "architecture": "arm64",
    "candidate_native_latency_gate_still_failed": True,
    "parameters": variants,
    "samples": rows,
    "summaries": {
        name: {
            "median_ms": statistics.median(x["server_ms"] for x in values),
            "p95_ms": lab.lab._percentile([x["server_ms"] for x in values], 0.95),
            "all_results_match_baseline": all(
                x["projection_sha256"] == baseline for x in values
            ),
        }
        for name, values in rows.items()
    },
    "os_after": lab.capture_os(state["container"]),
}
target = root / "experience-order-diagnostic.json"
assert not target.exists()
lab.write(target, result)
print(json.dumps(result["summaries"]), flush=True)
