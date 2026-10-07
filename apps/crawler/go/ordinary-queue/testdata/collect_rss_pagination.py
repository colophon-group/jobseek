"""Freeze actual legacy generic and WordPress RSS pagination options."""

from __future__ import annotations

import json
from pathlib import Path

from src.core.monitors.rss import (
    _PRESETS,
    _generic_paginated_preset,
    _Preset,
    validate_generic_rss_config,
)

base = {"param_name": "page", "page_size": 20, "max_pages": 1000}
cases = []
inputs = [
    ("generic", {}),
    ("generic", {"pagination": None}),
    ("generic", {"pagination": base}),
    ("wp_job_manager", {}),
    ("wp_job_manager", {"pagination": None}),
    ("successfactors", {"pagination": base}),
]
for key, value in [
    ("start", False),
    ("start", 0),
    ("increment", 1.0),
    ("increment", -1),
    ("page_size", 1001),
    ("max_pages", 10001),
    ("param_name", "1page"),
    ("unknown", 1),
    ("start", 10000000),
]:
    inputs.append(("generic", {"pagination": {**base, key: value}}))
inputs.append(("generic", {"pagination": {"param_name": "page"}}))
for n, (preset, md) in enumerate(inputs):
    expected = None
    try:
        validate_generic_rss_config({"preset": preset, **md})
        p = (
            _generic_paginated_preset(_Preset([], [], {}), md)
            if preset == "generic"
            else _PRESETS[preset]
        )
        if p.paginated:
            expected = {
                "Param": p.page_query_param,
                "Start": p.page_start,
                "Increment": p.page_increment,
                "PageSize": p.page_size,
                "MaxPages": p.max_pages or 0,
            }
        error = False
    except ValueError:
        error = True
    cases.append(
        {
            "name": f"{preset}-{n}",
            "preset": preset,
            "metadata": md,
            "expected": expected,
            "error": error,
        }
    )
Path(__file__).with_name("python_rss_pagination.json").write_text(
    json.dumps({"cases": cases}, indent=2) + "\n"
)
print(f"Frozen {len(cases)} actual Python pagination cases")
