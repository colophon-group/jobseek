"""Summarize immutable lab artifacts without changing the measurement gates."""

from __future__ import annotations

import argparse
import gzip
import json
import statistics
from pathlib import Path


def memory(samples):
    if not samples:
        return None
    end = samples[-1]["time"]
    tail = [s for s in samples if s["time"] >= end - 20]
    return {
        "sample_count": len(samples),
        "observed_seconds": round(end - samples[0]["time"], 3),
        "max_sampling_gap_seconds": round(
            max(
                (b["time"] - a["time"] for a, b in zip(samples, samples[1:], strict=False)),
                default=0,
            ),
            3,
        ),
        "rss_peak_bytes": max(s["rss_bytes"] for s in samples),
        "cgroup_sampled_peak_bytes": max(s["cgroup_bytes"] for s in samples),
        "rss_final_20s_median_bytes": statistics.median(s["rss_bytes"] for s in tail),
        "rss_final_20s_range_bytes": [
            min(s["rss_bytes"] for s in tail),
            max(s["rss_bytes"] for s in tail),
        ],
        "anon_final_20s_median_bytes": statistics.median(s["stat"]["anon"] for s in tail),
        "file_final_20s_median_bytes": statistics.median(s["stat"]["file"] for s in tail),
        "events_before": samples[0]["events"],
        "events_after": samples[-1]["events"],
        "observed_event_increases": {
            key: sum(
                max(0, b["events"].get(key, 0) - a["events"].get(key, 0))
                for a, b in zip(samples, samples[1:], strict=False)
            )
            for key in samples[-1]["events"]
        },
        "event_counter_resets": sum(
            b["events"].get("max", 0) < a["events"].get("max", 0)
            for a, b in zip(samples, samples[1:], strict=False)
        ),
    }


def summarize(root):
    phases = {}
    for path in sorted([*root.glob("*.json"), *root.glob("*.json.gz")]):
        if path.name.endswith(".json.gz"):
            with gzip.open(path, "rt") as source:
                d = json.load(source)
        else:
            d = json.loads(path.read_text())
        if "phase" not in d:
            continue
        if d["phase"] in phases:
            raise ValueError(f"Duplicate phase {d['phase']!r}; use one artifact per phase")
        row = {
            k: d[k]
            for k in (
                "phase",
                "complete",
                "error",
                "started_at",
                "elapsed_seconds",
                "runtime",
                "documents",
                "document_sha256_modular_sum",
                "allocator",
            )
            if k in d
        }
        row["memory"] = memory(d.get("samples", []))
        row["sampling_errors"] = len(d.get("sample_errors", []))
        if "query_probes" in d:
            probes = d["query_probes"]
            row["live_reads"] = {
                "total": len(probes),
                "errors": len(d.get("query_probe_errors", [])),
                "mismatches": sum(p.get("matches_baseline") is False for p in probes),
                "cutoffs": sum(bool(p.get("search_cutoff")) for p in probes),
            }
        if "field_patches" in d:
            row["fields"] = []
            for p in d["field_patches"]:
                start, end = p["started_at"], p.get("completed_at", float("inf"))
                row["fields"].append(
                    {
                        "field": p["payload"]["fields"][0]["name"],
                        "complete": p["complete"],
                        "elapsed_seconds": round(end - start, 3) if end != float("inf") else None,
                        "memory": memory([s for s in d["samples"] if start <= s["time"] <= end]),
                        "allocator_after": p.get("allocator_after"),
                    }
                )
        if "queries" in d:
            row["queries"] = {
                name: {
                    k: q[k]
                    for k in (
                        "median_ms",
                        "p95_ms",
                        "client_median_ms",
                        "client_p95_ms",
                        "projection_sha256",
                        "repeat_drift",
                        "found",
                        "found_docs",
                    )
                }
                for name, q in d["queries"].items()
            }
        phases[d["phase"]] = row
    pairs = []
    baseline = "baseline-checkpoint" if "baseline-checkpoint" in phases else "baseline"
    for base, candidate in (
        (baseline, "selected"),
        (baseline, "selected-rebuilt"),
        (baseline, "rollback-rebuilt"),
        ("baseline-concurrent", "selected-concurrent"),
        ("baseline-concurrent", "rollback-concurrent"),
        ("baseline-after-cdc", "selected"),
        ("baseline-after-cdc", "selected-after-cdc"),
    ):
        if base not in phases or candidate not in phases:
            continue
        b, c = phases[base], phases[candidate]
        checks = {}
        for name, q in c.get("queries", {}).items():
            original = b["queries"][name]
            checks[name] = {
                "result_parity": q["projection_sha256"] == original["projection_sha256"]
                and not q["repeat_drift"]
                and not original["repeat_drift"],
                "median_ms": [original["median_ms"], q["median_ms"]],
                "p95_ms": [original["p95_ms"], q["p95_ms"]],
                "initial_server_latency_screen_pass": not (
                    q["p95_ms"] > original["p95_ms"] * 1.10 and q["p95_ms"] - original["p95_ms"] > 2
                ),
                "initial_client_latency_screen_pass": not (
                    q["client_p95_ms"] > original["client_p95_ms"] * 1.10
                    and q["client_p95_ms"] - original["client_p95_ms"] > 2
                ),
            }
        rss_b = b["memory"]["rss_final_20s_median_bytes"]
        rss_c = c["memory"]["rss_final_20s_median_bytes"]
        pairs.append(
            {
                "baseline": base,
                "candidate": candidate,
                "rss_final_20s_median_bytes": [rss_b, rss_c],
                "rss_delta_bytes": rss_c - rss_b,
                "rss_delta_percent": round((rss_c / rss_b - 1) * 100, 3),
                "queries": checks,
            }
        )
    controls = []
    for a, b, c in (
        (baseline, "selected-rebuilt", "rollback-rebuilt"),
        ("baseline-concurrent", "selected-concurrent", "rollback-concurrent"),
    ):
        if not all(label in phases for label in (a, b, c)):
            continue
        rows = {}
        for name, q in phases[b].get("queries", {}).items():
            first, last = phases[a]["queries"][name], phases[c]["queries"][name]
            row = {
                "result_parity": first["projection_sha256"]
                == q["projection_sha256"]
                == last["projection_sha256"]
            }
            for metric in ("median_ms", "p95_ms", "client_median_ms", "client_p95_ms"):
                values = [first[metric], q[metric], last[metric]]
                control_max = max(values[0], values[2])
                row[metric] = values
                row[metric + "_exceeds_both_controls_by_10pct_and_2ms"] = (
                    values[1] > control_max * 1.1 and values[1] - control_max > 2
                )
            rows[name] = row
        controls.append({"order": [a, b, c], "queries": rows})
    return {
        "units": "bytes and milliseconds unless explicitly named otherwise",
        "root": str(root),
        "phases": phases,
        "comparisons": pairs,
        "counterbalanced_controls": controls,
        "acceptance_note": (
            "Initial screening and A/B/A control comparison, not a statistical guarantee. "
            "Preserve every first-baseline failure and inspect both controls; "
            "no automatic rollout verdict. Rollout additionally requires review of source "
            "parity, peaks, probe availability, architecture, and host headroom."
        ),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.root), indent=2))
