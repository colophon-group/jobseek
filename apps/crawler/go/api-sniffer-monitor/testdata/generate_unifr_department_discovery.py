"""Freeze complete departmental duplicate checks using the actual Python monitor."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

import httpx

from src.core.monitors import unifr

root = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "unifr_reference_tests", root / "tests/test_unifr_monitor.py"
)
assert spec is not None and spec.loader is not None
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


async def main():
    cases = []
    for name in ("physics", "ses"):
        source = unifr._ACCORDION_SOURCES[name]
        for mode in ("complete", "missing-central", "failed-locale"):
            items = [
                (identifier, "Researcher", "Apply by September 1st, 2050.")
                for identifier in sorted(source.expected_ids)
            ]
            central = [
                (identifier, "Central role") for identifier in source.excluded_central_ids.values()
            ]
            if mode == "missing-central":
                central = [("1885", "Another role")]
            responses = {
                source.url: helpers._accordion_html(
                    title="Jobs " + source.page_title_suffix,
                    heading=source.heading,
                    items=items,
                    list_class="light" if name == "ses" else "brandedstyle",
                ),
                unifr._CENTRAL_FR: helpers._central_html("fr", central),
                unifr._CENTRAL_DE: helpers._central_html("de", central),
            }
            if mode == "failed-locale":
                responses[unifr._CENTRAL_DE] = "<html>Unavailable</html>"

            def handler(request, responses=responses):
                assert str(request.url) in responses
                return httpx.Response(
                    200, text=responses[str(request.url)], headers={"content-type": "text/html"}
                )

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                try:
                    jobs = await unifr._accordion_jobs(client, source, date(2026, 8, 26))
                    output, error = [asdict(j) for j in jobs], False
                except ValueError:
                    output, error = None, True
            cases.append(
                dict(source=name, name=mode, responses=responses, output=output, error=error)
            )
    Path(__file__).with_name("python_unifr_department_discovery.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
    )


asyncio.run(main())
