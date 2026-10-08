"""Freeze the actual central locale union and whole-inventory failure behavior."""

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


async def freeze():
    cases = []
    for name in (
        "complete",
        "late-identity-failure",
        "locale-owner-failure",
        "missing-description",
    ):
        fr = [("1885", "French only"), ("1891", "Titre français")]
        de = [("1891", "Deutscher Titel"), ("1897", "German only")]
        responses = {
            unifr._CENTRAL_FR: helpers._central_html("fr", fr),
            unifr._CENTRAL_DE: helpers._central_html("de", de),
        }
        for locale, rows in (("fr", fr), ("de", de)):
            for identifier, title in rows:
                payload = helpers._detail_payload(identifier, locale, title)
                if identifier == "1897":
                    if name == "late-identity-failure":
                        payload["id"] = "9999"
                    elif name == "locale-owner-failure":
                        payload["autorite"] = "External"
                    elif name == "missing-description":
                        payload["content"] = ""
                responses[f"{unifr._DETAIL_ROOT}/{locale}/{identifier}"] = json.dumps(payload)
        calls = []

        def handler(request, calls=calls, responses=responses):
            resource = str(request.url)
            calls.append(resource)
            assert resource in responses
            content_type = (
                "application/json" if resource.startswith(unifr._DETAIL_ROOT) else "text/html"
            )
            return httpx.Response(
                200, text=responses[resource], headers={"content-type": content_type}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            try:
                jobs = await unifr._central_jobs(client, date(2026, 8, 26))
                output, error = [asdict(j) for j in jobs], False
            except ValueError:
                output, error = None, True
        cases.append(
            dict(
                name=name,
                today="2026-08-26",
                responses=responses,
                calls=sorted(calls),
                output=output,
                error=error,
            )
        )
    return cases


Path(__file__).with_name("python_unifr_discovery.json").write_text(
    json.dumps(asyncio.run(freeze()), indent=2, ensure_ascii=False) + "\n"
)
