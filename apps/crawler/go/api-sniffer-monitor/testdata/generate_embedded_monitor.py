"""Freeze the real multi-source NextData monitor document parsers offline."""

from __future__ import annotations

import json
from pathlib import Path

from src.shared.nextdata import extract_embedded_json

payload = {"jobs": [{"id": 90071992547409931234, "title": "Engineer ] {x}"}]}
encoded = json.dumps(payload)
rsc = json.dumps("7:" + json.dumps(["$", "$L1", None, payload]) + "\n")
cases = [
    ("nextdata", f'<script id="__NEXT_DATA__">{encoded}</script>', "nextdata"),
    (
        "first-script",
        '<script id="__NEXT_DATA__">{}</script>' + f'<script id="__NEXT_DATA__">{encoded}</script>',
        "nextdata",
    ),
    (
        "wrong-attribute-order",
        f'<script type="application/json" id="__NEXT_DATA__">{encoded}</script>',
        "nextdata",
    ),
    ("single-quotes", f"<script id='__NEXT_DATA__'>{encoded}</script>", "nextdata"),
    ("trailing-comma", '<script id="__NEXT_DATA__">{"jobs":[],}</script>', "nextdata"),
    ("unknown-source-default", f'<script id="__NEXT_DATA__">{encoded}</script>', "unknown"),
    (
        "reactrouter",
        "window.__staticRouterHydrationData = JSON.parse(" + json.dumps(encoded) + ");",
        "reactrouter",
    ),
    ("rsc", f"self.__next_f.push([1,{rsc}])", "rsc"),
    (
        "rsc-overwrite",
        "self.__next_f.push([1," + json.dumps('1:{"jobs":[]}\n2:' + encoded + "\n") + "])",
        "rsc",
    ),
    ("canvas", "phApp.ddo = " + encoded + "; runOtherScript();", "phenom_canvas"),
    ("canvas-first", "phApp.ddo = {}; phApp.ddo = " + encoded + ";", "phenom_canvas"),
    ("canvas-trailing-comma", 'phApp.ddo = {"jobs":[],};', "phenom_canvas"),
    ("canvas-nonobject", "phApp.ddo = [];", "phenom_canvas"),
    ("missing", "No embedded data", "nextdata"),
]
output = [
    {"name": name, "html": source, "source": kind, "expected": extract_embedded_json(source, kind)}
    for name, source, kind in cases
]
Path(__file__).with_name("python_embedded_monitor.json").write_text(
    json.dumps({"cases": output}, indent=2, ensure_ascii=False) + "\n"
)
