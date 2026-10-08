"""Freeze Python API automatic array discovery, scoring and stable selection."""

from __future__ import annotations

import json
from pathlib import Path

from src.core.monitors.api_sniffer import pick_best_array, score_array
from src.shared.api_sniff import find_arrays


def jobs(count=3, **fields):
    return [
        dict(id=str(i), title=f"Engineer {i}", url=f"https://example.com/job/{i}", **fields)
        for i in range(count)
    ]


cases = [
    ("root", jobs()),
    ("two-root", jobs(2)),
    ("empty-root", []),
    ("mixed-root", [*jobs(), None, 1]),
    ("wrapped", {"data": {"jobs": jobs(5)}}),
    ("small-wrappers", {"actions": [{"other": []}, {"returnValue": {"jobs": jobs()}}]}),
    ("quoted-dot", {"items.v2": jobs()}),
    ("quoted-space", {"job entries": jobs()}),
    ("quoted-bracket", {"jobs[0]": jobs()}),
    ("quoted-quote", {'job"s': jobs()}),
    ("quoted-backslash", {"job\\s": jobs()}),
    ("html-char", {"jobs<": jobs()}),
    ("unicode", {"emplois é": jobs()}),
    ("stable-tie", {"first": jobs(), "second": jobs()}),
    ("longer-tie", {"first": jobs(), "second": jobs(4)}),
    (
        "job-path-wins",
        {"locations": [{"name": "Zurich", "id": str(i)} for i in range(10)], "jobs": jobs()},
    ),
    (
        "job-title-wins",
        {"first": [{"id": str(i), "city": "Zurich"} for i in range(3)], "second": jobs()},
    ),
    (
        "nested-url",
        [
            {"id": str(i), "title": "Engineer", "links": {"directlink": f"/jobs/{i}"}}
            for i in range(3)
        ],
    ),
    (
        "picture-not-job",
        [
            {"id": str(i), "title": "Engineer", "PictureUrl": f"https://example.com/image/{i}"}
            for i in range(3)
        ],
    ),
    (
        "artwork-punctuation",
        [
            {"id": str(i), "title": "Engineer", "i_m-a-g-eUrl": f"https://example.com/image/{i}"}
            for i in range(3)
        ],
    ),
    (
        "fallback-url",
        [{"id": str(i), "title": "Engineer", "strange": f"/jobs/{i}"} for i in range(3)],
    ),
    (
        "partial-url",
        [
            {"title": "Engineer", "url": "/jobs/1"},
            {"title": "Engineer", "url": None},
            {"title": "Engineer", "url": "/jobs/3"},
        ],
    ),
    ("no-three-wrapped", {"jobs": jobs(2)}),
    ("turkish-title", [{"id": str(i), "TİTLE": "Engineer", "url": f"/jobs/{i}"} for i in range(3)]),
    ("long-s-path", {"poſitions": jobs()}),
    ("not-objects", {"jobs": [1, 2, 3, 4]}),
    ("array-50", [{"child": jobs()} for _ in range(50)]),
    ("array-51", [{"child": jobs()} for _ in range(51)]),
    ("nested-reference", {"refs": [{"label": "Engineering", "values": jobs()} for _ in range(3)]}),
]
results = []
for name, payload in cases:
    for endpoint in ("https://example.com/api", "https://example.com/jobs"):
        arrays = find_arrays(payload)
        selected = pick_best_array(arrays, endpoint)[0] if arrays else None
        results.append(
            dict(
                name=name + ("-jobs" if endpoint.endswith("/jobs") else "-api"),
                payload=payload,
                endpoint=endpoint,
                candidates=[
                    dict(path=p, items=x, score=score_array(p, x, endpoint)) for p, x in arrays
                ],
                selected=selected,
            )
        )
Path(__file__).with_name("python_auto_path.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=2) + "\n"
)
