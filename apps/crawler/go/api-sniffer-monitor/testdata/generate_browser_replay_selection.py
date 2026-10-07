"""Freeze the capture-selection block from the actual Python replay function."""

from __future__ import annotations

import inspect
import json
import textwrap
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

from src.core.monitors import api_sniffer


class QuietLog:
    def info(self, *_args, **_kwargs):
        pass


source = inspect.getsource(api_sniffer._discover_replay_once)
start = source.index("            matching_exchanges: list[tuple[int, Exchange]] = []")
end = source.index("        # Replay the API call", start)
selection = compile(textwrap.dedent(source[start:end]), "python-replay-selection", "exec")
exchange = {
    "url": "https://example.com/api?live=1",
    "method": "POST",
    "body": {"jobs": [{"id": 1}]},
    "request_headers": {"Authorization": "fixture-one"},
}
cases = [
    ("positive", [exchange], "jobs"),
    ("no-match", [dict(exchange, url="https://other.com/api")], "jobs"),
    ("wrong-method", [dict(exchange, method="GET")], "jobs"),
    ("method-case", [dict(exchange, method="post")], "jobs"),
    ("host-case", [dict(exchange, url="https://EXAMPLE.com/api")], "jobs"),
    ("encoded-path", [dict(exchange, url="https://example.com/%61pi")], "jobs"),
    ("zero-score", [dict(exchange, body={"jobs": [1, "text", None]})], "jobs"),
    ("dictionary", [dict(exchange, body={"jobs": {"one": {"id": 1}}})], "jobs"),
    ("root", [dict(exchange, body={"jobs": []})], "$"),
    (
        "rank-and-first-tie",
        [
            exchange,
            dict(
                exchange,
                body={"jobs": [{"id": 1}, {"id": 2}, 3]},
                request_headers={"Authorization": "fixture-best"},
            ),
            dict(
                exchange,
                body={"jobs": [{"id": 3}, {"id": 4}]},
                request_headers={"Authorization": "fixture-tie"},
            ),
        ],
        "jobs",
    ),
]
out = []
for name, rows, path in cases:
    scope = {
        "api_parsed": urlparse("https://example.com/api?stored=1"),
        "method": "POST",
        "json_path": path,
        "nav_exchanges": [SimpleNamespace(**row) for row in rows],
        "urlparse": urlparse,
        "resolve_path": api_sniffer.resolve_path,
        "log": QuietLog(),
        "captured_data": None,
        "request_headers": {"Authorization": "fixture-stored"},
    }
    exec(selection, scope)
    selected = scope.get("captured_exchange")
    out.append(
        {
            "name": name,
            "path": path,
            "exchanges": rows,
            "expected": {
                "matched": selected is not None,
                "headers": scope["request_headers"] if selected is not None else None,
                "body": scope["captured_data"],
            },
        }
    )

Path(__file__).with_name("python_browser_replay_selection.json").write_text(
    json.dumps(out, indent=2) + "\n"
)
