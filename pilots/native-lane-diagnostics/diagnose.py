"""Read-only field fingerprints for the disposable whole-lane comparison."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def serialized(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(serialized(value)).hexdigest()


def publication_probe(
    postings: list[dict[str, Any]],
    descriptions: list[dict[str, Any]],
    profiles: dict[str, str],
    target: str,
) -> list[dict[str, Any]]:
    """Test the original failed digest against bounded upload-publication seams."""
    if len(postings) > 1024:
        raise RuntimeError("publication probe exceeds its fixture bound")
    rows = [{k: v for k, v in row.items() if k != "id"} for row in postings]
    descriptions = [{k: v for k, v in row.items() if k != "id"} for row in descriptions]
    if digest({"postings": rows, "descriptions": descriptions}) == target:
        return []
    encoded = [serialized(row) for row in rows]
    prefix = hashlib.sha256(b'{"descriptions":' + serialized(descriptions) + b',"postings":[')
    variants = {
        "pre_upload": {"description_r2_hash": None, "to_be_enriched": False},
        "unpublished_pointer": {"description_r2_hash": None},
        "enrichment_pending": {"to_be_enriched": True},
        "enrichment_settled": {"to_be_enriched": False},
    }
    matches = []
    for index, row in enumerate(rows):
        if index:
            prefix.update(b",")
        suffix = (b"," + b",".join(encoded[index + 1 :]) if index + 1 < len(rows) else b"") + b"]}"
        for kind, changes in variants.items():
            candidate = prefix.copy()
            candidate.update(serialized(dict(row, **changes)))
            candidate.update(suffix)
            if candidate.hexdigest() == target:
                matches.append({"profile": profiles[row["source_url"]], "kind": kind, "rows": 1})
        prefix.update(encoded[index])
    for profile in [*sorted(set(profiles.values())), "all"]:
        selected = [
            row for row in rows if profile == "all" or profiles[row["source_url"]] == profile
        ]
        if len(selected) <= 1:
            continue
        for kind, changes in variants.items():
            altered = [dict(row, **changes) if row in selected else row for row in rows]
            if digest({"postings": altered, "descriptions": descriptions}) == target:
                matches.append({"profile": profile, "kind": kind, "rows": len(selected)})
    return matches


def fingerprints(
    postings: list[dict[str, Any]],
    descriptions: list[dict[str, Any]],
    profiles: dict[str, str],
) -> dict[str, Any]:
    """Retain ordered field hashes, never identifiers, text or field values."""
    if len(profiles) > 4096 or len(set(profiles.values())) > 32:
        raise RuntimeError("diagnostic fixture exceeds its bounded scope")
    payload = {
        table: [{k: v for k, v in row.items() if k != "id"} for row in rows]
        for table, rows in (("postings", postings), ("descriptions", descriptions))
    }
    result: dict[str, Any] = {}
    for table, rows in payload.items():
        if len(rows) > len(profiles):
            raise RuntimeError("diagnostic fixture row count differs")
        for profile in sorted(set(profiles.values())):
            selected = [row for row in rows if profiles[row["source_url"]] == profile]
            columns = sorted({key for row in selected for key in row})
            result.setdefault(profile, {})[table] = {
                "rows": len(selected),
                "fields": {
                    column: digest(
                        [[row["source_url"], column in row, row.get(column)] for row in selected]
                    )
                    for column in columns
                },
            }
    return {"canonical_sha256": digest(payload), "profiles": result}


def summarize(arms: list[dict[str, Any]]) -> dict[str, Any]:
    diagnostics = [arm.get("output", {}).get("canonical_diagnostics") for arm in arms]
    available = [d for d in diagnostics if d is not None]
    paths = {
        (profile, table, column)
        for d in available
        for profile, tables in d["profiles"].items()
        for table, values in tables.items()
        for column in values["fields"]
    }
    differences = []
    for profile, table, column in sorted(paths):
        hashes = [
            d["profiles"].get(profile, {}).get(table, {}).get("fields", {}).get(column)
            if d is not None
            else None
            for d in diagnostics
        ]
        if len(set(hashes)) != 1:
            differences.append(
                {"profile": profile, "table": table, "column": column, "hashes": hashes}
            )
    return {
        "arms": len(arms),
        "diagnosed_arms": len(available),
        "snapshots_match_collected": [
            d.get("snapshot_matches_collected") if d is not None else None for d in diagnostics
        ],
        "differing_fields": differences,
        "publication_probe_matches": [
            d.get("publication_probe_matches") if d is not None else None for d in diagnostics
        ],
    }


async def snapshot() -> None:
    import asyncpg

    from admission.native_lane import driver
    from src.lightpanda.admission import STABLE_COLUMNS

    driver.validate_environment()
    pool = await asyncpg.connect(
        driver.DATABASE,
        command_timeout=5,
        server_settings={"default_transaction_read_only": "on"},
    )
    try:
        async with pool.transaction(isolation="repeatable_read", readonly=True):
            rows = await pool.fetch(
                "SELECT "
                + STABLE_COLUMNS
                + ",description_r2_hash,tdm_reserved FROM job_posting ORDER BY source_url"
            )
            descriptions = await pool.fetch("""
                SELECT p.id::text,p.source_url,d.locale,d.html,d.hash::text,d.r2_uploaded
                FROM descriptions d JOIN job_posting p ON p.id=d.posting_id
                ORDER BY p.source_url,d.locale
            """)
    finally:
        await pool.close()
    profiles = {row["url"]: row["profile"] for row in driver.jobs()}
    result = fingerprints(
        [dict(row) for row in rows], [dict(row) for row in descriptions], profiles
    )
    result["publication_probe_matches"] = publication_probe(
        [dict(row) for row in rows],
        [dict(row) for row in descriptions],
        profiles,
        "931686f1f31dab56d769a8912ddd9d3b0e1d0c8dd110caa98c2c86025f1934b3",
    )
    print("NATIVE_LANE_FIELDS=" + json.dumps(result, sort_keys=True, separators=(",", ":")))


def main() -> int:
    if sys.argv[1:] == ["--snapshot"]:
        asyncio.run(snapshot())
        return 0

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps/crawler"))
    from admission.native_lane import controller

    original_runner = controller.runner
    program = Path(__file__).read_text()

    def runner(env: dict[str, str], phase: str, *, timeout: int = 60) -> dict[str, Any]:
        result = original_runner(env, phase, timeout=timeout)
        if phase == "collect":
            raw = controller.compose(
                env,
                "run",
                "--rm",
                "--no-deps",
                "runner",
                "/app/.venv/bin/python",
                "-c",
                program,
                "--snapshot",
                timeout=30,
            )
            lines = [
                line.removeprefix("NATIVE_LANE_FIELDS=")
                for line in raw.splitlines()
                if line.startswith("NATIVE_LANE_FIELDS=")
            ]
            if len(lines) != 1:
                raise RuntimeError("diagnostic runner omitted its unique result")
            evidence = json.loads(lines[0])
            evidence["snapshot_matches_collected"] = (
                evidence["canonical_sha256"] == result["canonical_sha256"]
            )
            result["canonical_diagnostics"] = evidence
        return result

    controller.runner = runner
    return controller.main()


if __name__ == "__main__":
    raise SystemExit(main())
