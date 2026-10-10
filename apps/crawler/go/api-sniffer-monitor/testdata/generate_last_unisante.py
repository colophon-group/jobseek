"""Freeze original Unisanté authoritative listing and visible-detail contracts."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "tests"))
from test_unisante_monitor import _detail, _listing  # noqa: E402

from src.core.monitors import unisante  # noqa: E402

unisante._today = lambda: date(2026, 8, 25)
raw = "https://emploi.unisante.ch/index.php/offres"
listing = []
one = _listing([("1405-role", "Role")])
for name, source in [
    ("normal", one),
    ("zero", _listing([])),
    ("hidden-zero", _listing([], empty_visible=False)),
    ("visible-nonempty", _listing([("1405-role", "Role")], empty_visible=True)),
    ("pagination", _listing([], pagination=True)),
    ("class-drift", _listing([("1405-role", "Role")], broken_classes=True)),
    ("duplicate", _listing([("1405-role", "Role")] * 2)),
    ("missing-empty", one.replace('id="no-ads"', 'id="other"')),
    (
        "cross-origin",
        one.replace("/index.php/offre/1405-role", "https://foreign.example/offre/1405-role"),
    ),
    ("alternate-link", one.replace("/index.php/offre/1405-role", "/offre/1405-role")),
    ("query-link", one.replace("/index.php/offre/1405-role", "/index.php/offre/1405-role?x=1")),
    ("outside-inventory", one.replace("</main>", '<a href="/offre/8-other">Other</a></main>')),
    ("challenge", one + "<title>Just a moment</title>"),
    ("missing-heading", one.replace("Nos offres d'emploi", "Changed")),
]:
    c = {"name": name, "source": source, "url": raw}
    try:
        c["jobs"] = {k: asdict(v) for k, v in unisante._parse_listing(source, raw).items()}
    except (ValueError, RuntimeError):
        c["error"] = True
    listing.append(c)

normalizer = unisante.normalize_description_html
calls = []


def recording(source):
    out = normalizer(source)
    calls.append({"source": source, "description": out})
    return out


unisante.normalize_description_html = recording
rows = []
for name, slug, source in [
    ("normal", "1405-role", _detail("1405")),
    ("evergreen", "medecin-role", _detail("8")),
    ("numeric-mismatch", "1405-role", _detail("8")),
    ("numeric-deadline", "1405-role", _detail("1405", deadline="13-09-2026")),
    ("french-deadline", "1405-role", _detail("1405", deadline="2 décembre 2026")),
    ("expired", "1405-role", _detail("1405", deadline="2 août 2026")),
    ("malformed-deadline", "1405-role", _detail("1405", malformed_deadline=True)),
    ("mojibake", "1405-role", _detail("1405", visible_mojibake=True)),
    ("wrong-owner", "1405-role", _detail("1405").replace("Unisant\\u00e9,", "Foreign,")),
    ("wrong-owner-url", "1405-role", _detail("1405").replace("www.unisante.ch", "foreign.example")),
    ("invalid-posted", "1405-role", _detail("1405").replace("2026-08-20", "2026-02-30")),
    ("missing-main", "1405-role", _detail("1405").replace('id="main"', 'id="other"')),
    (
        "ambiguous-reference",
        "1405-role",
        _detail("1405").replace("Référence : 1405", "Référence : 1405 Référence : 8"),
    ),
]:
    c = {
        "name": name,
        "source": source,
        "slug": slug,
        "title": "Role",
        "today": "2026-08-25",
        "normalizations": [],
    }
    calls.clear()
    try:
        job = unisante._parse_detail(source, unisante._ListingJob(slug, "Role"))
        c["job"] = {k: v for k, v in asdict(job).items() if v is not None} if job else None
    except ValueError:
        c["error"] = True
    c["normalizations"] = list(calls)
    rows.append(c)
Path(__file__).with_name("python_last_unisante.json").write_text(
    json.dumps({"listings": listing, "details": rows}, ensure_ascii=False, indent=2) + "\n"
)
print(f"Original Unisanté frozen contracts: {len(listing)} listings, {len(rows)} details")
