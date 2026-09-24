#!/usr/bin/env python3
"""Run the full-corpus index-pruning rehearsal against an owned local lab."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--settle-seconds", type=int, default=45)
    parser.add_argument("--repeats", type=int, default=15)
    args = parser.parse_args()
    root, source_dir = args.root.resolve(), args.input.resolve()
    if root.exists():
        parser.error("Use a new lab directory; existing evidence cannot be overwritten")
    root.mkdir(mode=0o700, parents=True)
    source = json.loads((source_dir / "corpus.json").read_text())
    script = Path(__file__).with_name("typesense-rss-lab.py")

    def read(label):
        return json.loads((root / (label + ".json")).read_text())

    def run(command, label, *extra):
        print("START", label, flush=True)
        subprocess.run(
            [
                sys.executable,
                str(script),
                command,
                "--root",
                str(root),
                "--label",
                label,
                "--settle-seconds",
                str(args.settle_seconds),
                "--repeats",
                str(args.repeats),
                *extra,
            ],
            check=True,
        )
        result = read(label)
        assert result["complete"], label
        assert not result["os_after"]["events"]["oom_kill"], label
        assert not result["runtime"]["oom_killed_after"], label
        return result

    def fingerprint(label):
        result = run("fingerprint", label)
        for key in ("documents", "document_sha256_modular_sum"):
            assert result[key] == source[key], (label, key)

    def parity(label):
        result = read(label)
        baseline = read(
            "baseline-concurrent"
            if result.get("concurrency") == 4 and label != "baseline-concurrent"
            else "baseline"
        )
        mismatches = [
            name
            for name, query in result["queries"].items()
            if query["repeat_drift"]
            or query["projection_sha256"] != baseline["queries"][name]["projection_sha256"]
        ]
        assert not mismatches, ("Consumed result discrepancy", label, mismatches)

    (root / "host.json").write_text(
        json.dumps(
            {
                "platform": platform.platform(),
                "architecture": platform.machine(),
                "disk_free_bytes": shutil.disk_usage(root).free,
                "candidate_sha": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], text=True
                ).strip(),
            },
            indent=2,
        )
        + "\n"
    )
    subprocess.run(
        [
            sys.executable,
            str(script),
            "init",
            "--root",
            str(root),
            "--schema",
            str(source_dir / "schema.json"),
        ],
        check=True,
    )
    state = read("state")
    state["year_start"] = source["year_start"]
    (root / "state.json").write_text(json.dumps(state, indent=2) + "\n")
    imported = run("import", "import", "--sample", str(source_dir / "postings.jsonl.gz"))
    assert imported["documents"] == source["documents"]
    assert imported["source_sha256"] == source["source_sha256"]
    run("restart", "baseline-rebuild")
    baseline = run("measure", "baseline")
    assert all(not query["repeat_drift"] for query in baseline["queries"].values())
    fingerprint("baseline-fingerprint")
    run("snapshot", "baseline-snapshot")
    concurrent = (
        "--concurrency",
        "4",
        "--cases",
        "keyword_current",
        "keyword_sales",
        "keyword_remote",
    )
    run("measure", "baseline-concurrent", *concurrent)
    parity("baseline-concurrent")
    run("cdc", "baseline-cdc")
    run("measure", "baseline-after-cdc", "--repeats", "3")
    parity("baseline-after-cdc")
    selected = run("setup", "selected-migration")
    assert len(selected["field_patches"]) == 4, "Unexpected migration scope"
    assert selected.get("query_probes"), "No live reads observed during migration"
    assert not selected.get("query_probe_errors"), "Live reads failed during migration"
    assert all(
        probe.get("matches_baseline") and not probe.get("search_cutoff")
        for probe in selected["query_probes"]
    ), "Live query results changed during migration"
    assert not run("setup", "selected-idempotent", "--settle-seconds", "0")["field_patches"]
    run("measure", "selected")
    parity("selected")
    fingerprint("selected-fingerprint")
    run("snapshot", "selected-snapshot")
    run("restart", "selected-rebuild")
    run("measure", "selected-rebuilt")
    parity("selected-rebuilt")
    run("measure", "selected-concurrent", *concurrent)
    parity("selected-concurrent")
    run("cdc", "selected-cdc")
    run("measure", "selected-after-cdc", "--repeats", "3")
    parity("selected-after-cdc")
    fingerprint("selected-rebuilt-fingerprint")
    run("snapshot", "selected-rebuilt-snapshot")
    for patch in reversed(selected["field_patches"]):
        field = patch["payload"]["fields"][0]["name"]
        run("rollback", "rollback-" + field, "--field", field, "--settle-seconds", "5")
    run("measure", "rolled-back")
    parity("rolled-back")
    fingerprint("rolled-back-fingerprint")
    run("restart", "rollback-rebuild")
    run("measure", "rollback-rebuilt")
    parity("rollback-rebuilt")
    fingerprint("rollback-rebuilt-fingerprint")
    run("measure", "rollback-concurrent", *concurrent)
    parity("rollback-concurrent")
    run("snapshot", "rollback-snapshot")
    print(
        "Full-corpus semantic, migration and rollback gates passed; compare RSS/latency separately."
    )


if __name__ == "__main__":
    main()
