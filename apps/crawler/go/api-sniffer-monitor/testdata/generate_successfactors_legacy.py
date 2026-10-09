"""Freeze restricted DWR parsing, session requests and original inventory outcomes."""

from __future__ import annotations

import ast
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import httpx

from src.core.monitors import _successfactors_legacy as legacy

root = Path(__file__).resolve().parents[3]
tree = ast.parse((root / "tests/test_successfactors_legacy.py").read_text())
names = {
    "_quote",
    "_Graph",
    "_filters",
    "_pagination",
    "_posting",
    "_initial_response",
    "_search_response",
    "_bootstrap_response",
    "_board",
}
namespace = {"json": json, "httpx": httpx}
nodes = [
    node
    for node in tree.body
    if isinstance(node, ast.FunctionDef | ast.ClassDef) and node.name in names
]
exec(
    compile(ast.Module(body=nodes, type_ignores=[]), "<original-legacy-fixtures>", "exec"),
    namespace,
)
initial = namespace["_initial_response"]
search = namespace["_search_response"]
board = namespace["_board"]()


async def main():
    parsers = []
    samples = [
        ("initial-zero", initial(0), 0, True),
        ("initial-one", initial(1), 0, True),
        ("search-zero", search(0, 1, []), 1, False),
        ("search-one", search(1, 1, [123]), 1, False),
    ]
    base = search(1, 1, [123])
    for name, text in [
        ("no-marker", base.replace("//#DWR-REPLY", "")),
        ("bad-callback", base.replace("Callback('1'", "Callback('2'")),
        ("callback-tail", base + "alert('unsafe');"),
        ("unsupported-code", base.replace("var s0={};", "var s0={};alert('unsafe');")),
        ("undeclared-reference", base.replace("s0.configs=s1;", "s0.configs=s99999;")),
        ("duplicate-declaration", base.replace("var s0={};", "var s0={};var s0={};")),
        (
            "duplicate-property",
            base.replace('s0.postingCount="1";', 's0.postingCount="1";s0.postingCount="1";'),
        ),
        ("outsize-index", base.replace("s2[0]=", "s2[200001]=")),
    ]:
        samples.append((name, text, 1, False))
    samples.extend(
        [
            ("apostrophe-escape", base.replace("Engineer 123", "Engineer \\'123"), 1, False),
            ("no-throw-prelude", base[base.index("//#DWR-REPLY") :], 1, False),
        ]
    )
    for name, text, batch, is_initial in samples:
        try:
            expected = legacy._parse_dwr(text, batch=batch, initial=is_initial)
            error = False
        except ValueError:
            expected, error = None, True
        parsers.append(
            dict(
                name=name,
                body=text,
                batch=batch,
                initial=is_initial,
                expected=expected,
                error=error,
            )
        )

    inventories = []
    for name, total, pages, change in [
        ("zero", 0, [], None),
        ("one", 1, [search(1, 1, [123])], None),
        ("two-pages", 101, [search(101, 1, list(range(1, 101))), search(101, 2, [101])], None),
        ("short-later-page", 101, [search(101, 1, list(range(1, 101))), search(101, 2, [])], None),
        (
            "repeated-later-id",
            101,
            [search(101, 1, list(range(1, 101))), search(101, 2, [1])],
            None,
        ),
        (
            "changed-total",
            101,
            [search(101, 1, list(range(1, 101))), search(101, 2, [101], reported_total=102)],
            None,
        ),
        (
            "foreign-detail",
            1,
            [search(1, 1, [123]).replace("company=Acme", "company=Foreign")],
            None,
        ),
        ("bad-bootstrap", 1, [search(1, 1, [123])], "token"),
        ("bad-event", 1, [search(1, 1, [123])], "event"),
    ]:
        bootstrap = namespace["_bootstrap_response"]()
        text = bootstrap.text
        headers = dict(bootstrap.headers)
        if change == "token":
            text = text.replace("ajaxSecKey", "missingSecKey")
        if change == "event":
            headers["x-event-id"] = "bad"
        responses = [
            dict(body=text, headers=headers),
            dict(body=initial(total), headers={}),
            *[dict(body=page, headers={}) for page in pages],
        ]
        requests = []

        def handle(request, requests=requests, responses=responses):
            index = len(requests)
            requests.append(
                dict(
                    method=request.method,
                    url=str(request.url),
                    body=request.content.decode(),
                    headers={
                        key: value
                        for key, value in request.headers.items()
                        if key
                        in {
                            "content-type",
                            "origin",
                            "referer",
                            "viewid",
                            "x-ajax-token",
                            "x-csrf-token",
                            "x-event-id",
                            "x-sap-page-info",
                            "x-subaction",
                        }
                    },
                )
            )
            if index >= len(responses):
                raise ValueError("unexpected request")
            response = responses[index]
            return httpx.Response(200, text=response["body"], headers=response["headers"])

        try:
            with patch.object(legacy.secrets, "token_hex", return_value="0123456789abcdef01234567"):
                async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                    result = await legacy.discover_legacy(board, client)
            expected = [
                {key: value for key, value in asdict(job).items() if value is not None}
                for job in (result.jobs_by_url or {}).values()
            ]
            truncated, error = result.truncated, False
        except ValueError:
            expected, truncated, error = None, False, True
        inventories.append(
            dict(
                name=name,
                board=board,
                responses=responses,
                requests=requests,
                expected=expected,
                truncated=truncated,
                error=error,
            )
        )

    Path(__file__).with_name("python_successfactors_legacy.json").write_text(
        json.dumps(dict(parsers=parsers, inventories=inventories), ensure_ascii=False, indent=2)
        + "\n"
    )
    print(
        json.dumps(
            {"original_parser_cases": len(parsers), "original_inventory_cases": len(inventories)}
        )
    )


asyncio.run(main())
