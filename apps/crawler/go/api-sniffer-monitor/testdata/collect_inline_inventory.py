"""Freeze complete Inline document inventories by running actual repository tests.

Transport and click expansion have separate proofs. This captures the real
discover function called by existing unparameterized static field tests, plus
explicit cap and all-expired cases. No production requests or writes occur.
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import inspect
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from src.core.monitor import MonitorResult
from src.core.monitors.inline import discover
from src.shared.html_normalize import _normalize_description_html_python

HERE = Path(__file__).parent
TESTS = HERE.parents[2] / "tests" / "test_inline_monitor.py"
spec = importlib.util.spec_from_file_location("inline_actual_tests", TESTS)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
cases: list[dict] = []
current = ""


async def record(board, client=None, pw=None):
    metadata = copy.deepcopy(board.get("metadata") or {})
    assert not metadata.get("render") and pw is None
    source = getattr(client, "_html", "")
    case = {
        "name": f"{current}-{len(cases)}",
        "board_url": board["board_url"],
        "metadata": metadata,
        "html": source,
        "now": datetime.now(UTC).isoformat(),
        "error": False,
        "jobs": [],
        "truncated": False,
        "verified_empty_reason": "",
    }
    try:
        result = await discover(board, client, pw)
        if isinstance(result, MonitorResult):
            case["truncated"] = result.truncated
            case["verified_empty_reason"] = result.verified_empty_reason or ""
            jobs = list((result.jobs_by_url or {}).values())
        else:
            jobs = result
        keys = {
            "url",
            "title",
            "description",
            "locations",
            "employment_type",
            "job_location_type",
            "date_posted",
            "metadata",
            "extras",
        }
        case["jobs"] = [{k: v for k, v in asdict(job).items() if k in keys} for job in jobs]
        case["worker_jobs"] = copy.deepcopy(case["jobs"])
        for job in case["worker_jobs"]:
            job["description"] = _normalize_description_html_python(job["description"])
        return result
    except Exception:
        case["error"] = True
        raise
    finally:
        cases.append(case)


async def main():
    global current
    module.discover = record
    skip = ("render", "click", "fetch_", "json_response", "invalid_transport")
    selected = []
    for name, fn in vars(module).items():
        if not name.startswith("test_discover_") and name not in {
            "test_require_zero_proof_rejects_selector_drift",
            "test_extracted_fields_override_defaults_by_title",
        }:
            continue
        if not inspect.iscoroutinefunction(fn) or inspect.signature(fn).parameters:
            continue
        if any(part in name for part in skip):
            continue
        current = name
        await fn()
        selected.append(name)
    current = "all-expired"
    await record(
        {
            "board_url": "https://example.com/jobs",
            "metadata": {
                "steps": [{"tag": "h2", "field": "title"}],
                "defaults": {"valid_through": "2000-01-01"},
                "exclude_expired": True,
            },
        },
        module._FakeClient("<h2>Expired</h2>"),
    )
    for count, positions in [(501, 1), (251, 2)]:
        current = f"cap-{count}-{positions}"
        await record(
            {
                "board_url": "https://example.com/jobs",
                "metadata": {
                    "steps": [{"tag": "h2", "field": "title"}],
                    "positions_per_listing": positions,
                },
            },
            module._FakeClient("<h2>Engineer</h2>" * count),
        )
    (HERE / "python_inline_inventory.json").write_text(
        json.dumps({"actual_tests": selected, "cases": cases}, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps({"actual_tests": len(selected), "cases": len(cases)}))


asyncio.run(main())
