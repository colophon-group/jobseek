"""Freeze original provider-marked DOM inventories and CareerCenter proofs."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import dataclasses
import json
import sys
from pathlib import Path

import httpx
import structlog

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src.core.monitors import dom  # noqa: E402

structlog.configure(logger_factory=structlog.ReturnLoggerFactory())


def originals():
    tree = ast.parse((ROOT / "tests/test_dom_pagination.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TestCanHandle")
    values = {}
    for node in cls.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    with contextlib.suppress(ValueError, TypeError):
                        values[target.id] = ast.literal_eval(node.value)
    return values


async def main():
    values = originals()
    html, url = values["PROSPECTIVE_HTML"], values["PROSPECTIVE_URL"]
    positive = dom._prospective_probe_config(html, url)
    assert positive is not None
    zero = (
        '<html><head><link href="/careercenter/1000973/assets/css/company.css"></head>'
        '<body class="career-center"><span class="jobs-total"><span class="total">0</span>'
        '</span><div id="jobs-list"></div></body></html>'
    )
    cases = []

    def add(name, source, board_url=url, metadata=None):
        config = dict(positive if metadata is None else metadata)
        config.pop("urls", None)
        config["scraper_type"] = "dom"
        cases.append((name, source, board_url, config))

    add("prospective-complete", html)
    add(
        "prospective-uppercase-uuid",
        html.replace(
            "5f1e0316-6225-4e57-b40c-cb605e046331", "5F1E0316-6225-4E57-B40C-CB605E046331"
        ),
    )
    add(
        "prospective-canonical-asset",
        html.replace(
            "/careercenter/1000973/assets/css/company.css",
            "https://ohws.prospective.ch/public/v1/careercenter/1000973/assets/css/company.css",
        ),
    )
    add("prospective-wrong-medium", html.replace("1000973", "1000974"))
    add(
        "prospective-foreign-asset",
        html.replace(
            "/careercenter/1000973/assets/css/company.css",
            "https://attacker.example/careercenter/1000973/assets/css/company.css",
        ),
    )
    add(
        "prospective-ambiguous-medium",
        html.replace(
            "</head>", '<script src="/careercenter/1000974/assets/app.js"></script></head>'
        ),
    )
    add("prospective-encoded-asset", html.replace("/careercenter/", "/%63areercenter/"))
    add("prospective-missing-list", zero.replace('<div id="jobs-list"></div>', ""))
    add(
        "prospective-missing-total",
        zero.replace('<span class="jobs-total"><span class="total">0</span></span>', ""),
    )
    add("prospective-zero", zero)
    add("prospective-incorrect-zero", zero.replace(">0<", ">3<"))
    add(
        "prospective-foreign-row",
        html.replace(
            "/emplois-vacantes/analyste/5f1e0316-6225-4e57-b40c-cb605e046331",
            "https://attacker.example/jobs/5f1e0316-6225-4e57-b40c-cb605e046331",
        ),
    )
    without_path = dict(positive)
    without_path.pop("prospective_canonical_path")
    add("prospective-positive-without-canonical-path", html, metadata=without_path)
    add("prospective-zero-without-canonical-path", zero, metadata=without_path)
    for label, source in [
        ("lucca-complete", values["LUCCA_HTML"]),
        ("lucca-explicit-zero", values["LUCCA_EMPTY_HTML"]),
    ]:
        config = dom._lucca_probe_config(source, values["LUCCA_URL"])
        assert config is not None
        add(label, source, values["LUCCA_URL"], config)
    dualoo = dom._dualoo_probe_config(values["DUALOO_HTML"], values["DUALOO_URL"])
    assert dualoo is not None
    add("dualoo-original-preset", values["DUALOO_HTML"], values["DUALOO_URL"], dualoo)
    for key, value in {
        "lucca_board": True,
        "lg_portal": True,
        "bunge_bigredsky_board": True,
        "vagas_tenant": "fixture",
        "hotelcareer_profile": "hotel-fixture-123",
        "dualoo_portal": "fixture",
        "jobtoolz_tenant": "fixture",
        "yousty_organization": "123-fixture",
    }.items():
        add(
            "inert-marker-" + key,
            '<html><a href="/jobs/101">Engineer</a></html>',
            "https://jobs.example.com/",
            {key: value, "url_filter": r"^https://jobs\.example\.com/jobs/\d+$"},
        )

    output = []
    for name, source, board_url, config in cases:
        exchanges = []

        def handle(request, source=source, exchanges=exchanges):
            exchanges.append(
                {
                    "method": request.method,
                    "url": str(request.url),
                    "body": request.content.decode(),
                    "response": {
                        "status": 200,
                        "headers": {"content-type": "text/html; charset=utf-8"},
                        "body": source,
                    },
                }
            )
            return httpx.Response(200, text=source, request=request)

        row = {
            "name": name,
            "board": {"provider": "dom", "board_url": board_url, "metadata": config},
            "response": source,
            "exchanges": exchanges,
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            try:
                jobs = await dom.dom_discover({"board_url": board_url, "metadata": config}, client)
                row["expected"] = (
                    [{"url": u} for u in sorted(jobs)]
                    if isinstance(jobs, set)
                    else [dataclasses.asdict(job) for job in jobs]
                )
                row["status"] = "complete"
            except ValueError as error:
                row["expected"] = []
                row["status"] = "ValueError"
                row["error_kind"] = type(error).__name__
        output.append(row)
    Path(__file__).with_name("python_dom_provider_contracts.json").write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n"
    )
    print(f"Frozen {len(output)} original DOM provider contracts")


if __name__ == "__main__":
    asyncio.run(main())
