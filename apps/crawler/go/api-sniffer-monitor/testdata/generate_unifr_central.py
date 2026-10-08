from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

from src.core.monitors import unifr

root = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "unifr_reference_tests", root / "tests/test_unifr_monitor.py"
)
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
cases = []
for name, locale, jobs, change in [
    ("fr-valid", "fr", [("1911", "Titre & texte")], ""),
    ("de-valid", "de", [("1911", "Titel")], ""),
    ("zero", "fr", [], ""),
    ("duplicate", "fr", [("1911", "One"), ("1911", "Two")], ""),
    ("pagination", "fr", [("1911", "One")], "pagination"),
    ("bad-id", "fr", [("0", "One")], ""),
    ("missing-owner", "fr", [("1911", "One")], "owner"),
    ("missing-heading", "fr", [("1911", "One")], "heading"),
    ("double-shell", "fr", [("1911", "One")], "shell"),
    ("blank-title", "fr", [("1911", "")], ""),
]:
    body = helpers._central_html(locale, jobs, pagination=change == "pagination")
    if change == "owner":
        body = body.replace("Université de Fribourg", "Other")
    if change == "heading":
        body = body.replace("<h2>", "<h3>").replace("</h2>", "</h3>")
    if change == "shell":
        body = body.replace("</main>", '<ul class="list-group list"></ul></main>')
    try:
        output = unifr._parse_central_listing(body, locale)
        error = False
    except ValueError:
        output = None
        error = True
    cases.append(
        dict(kind="listing", name=name, locale=locale, body=body, output=output, error=error)
    )
for name, changes in [
    ("valid", {}),
    ("numeric-id", dict(id=1911)),
    ("owner", dict(autorite="External")),
    ("id", dict(id="1912")),
    ("wrong-link", dict(link=helpers.DE + "#1911")),
    ("title", dict(fonction="Different")),
    ("empty-description", dict(content="")),
    ("expired", dict(endpublish="2026-08-01T00:00:00+02:00")),
    ("timezone-free", dict(startpublish="2026-08-01T00:00:00")),
    ("utc-expiry", dict(endpublish="2026-08-26T00:00:00+02:00")),
    ("microseconds", dict(startpublish="2026-08-01T00:00:00.010000+00:00")),
    ("bad-date", dict(endpublish="invalid")),
]:
    payload = helpers._detail_payload("1911", "fr", "Researcher", **changes)
    body = json.dumps(payload, ensure_ascii=False)
    try:
        output = unifr._parse_central_detail(
            body,
            identifier="1911",
            locale="fr",
            listing_title="Researcher",
            today=date(2026, 8, 26),
        )
        error = False
    except ValueError:
        output = None
        error = True
    cases.append(
        dict(
            kind="detail",
            name=name,
            locale="fr",
            identifier="1911",
            listing_title="Researcher",
            today="2026-08-26",
            body=body,
            output=output,
            error=error,
        )
    )
p = root / "go/api-sniffer-monitor/testdata/python_unifr_central.json"
p.write_text(json.dumps(cases, indent=2, ensure_ascii=False) + "\n")
print("actual Python central cases", len(cases))
