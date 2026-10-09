"""Freeze original protocol facts for four public portal provider ports."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path

CRAWLER = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(CRAWLER))
from src.core.monitors import infoniqa, keka, pageup, turbohire  # noqa: E402
from src.shared.keka import KekaBoard  # noqa: E402
from src.shared.pageup import PageUpBoard  # noqa: E402


def fixture(name):
    spec = importlib.util.spec_from_file_location(name, CRAWLER / "tests" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def plain(value):
    if is_dataclass(value):
        return {key: plain(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [plain(item) for item in value]
    return value


cases = []


def freeze(provider, operation, name, inputs, run):
    case = dict(provider=provider, operation=operation, name=name, inputs=inputs)
    try:
        case.update(status="complete", expected=plain(run()))
    except Exception as error:
        case.update(status="failed", error_type=type(error).__name__)
    cases.append(case)


k = fixture("test_keka_monitor")
t = fixture("test_turbohire")
p = fixture("test_pageup")
i = fixture("test_infoniqa_monitor")
kboard = KekaBoard("acme", identifier=k.IDENTIFIER)
for name, patch in [
    ("populated", {}),
    ("unicode-space", {"title": " A\x1c B\u00a0 C "}),
    ("missing-title", {"title": None}),
    ("boolean-id", {"id": True}),
    ("zero-id", {"id": 0}),
    ("string-id", {"id": "123"}),
    ("no-description", {"description": 1}),
    ("part-time", {"jobType": 1}),
    ("boolean-jobtype", {"jobType": True}),
    ("string-jobtype", {"jobType": "2"}),
    ("no-locations", {"jobLocations": None}),
    ("location-fallback", {"jobLocations": [{"name": " Head Office "}, None]}),
    (
        "duplicate-geography",
        {"jobLocations": [{"city": "Bern", "state": " bern ", "countryName": "CH"}]},
    ),
    ("skill-shapes", {"skillNames": [" Go ", "Go", True, "SQL", ""]}),
    ("invalid-date", {"publishedOn": "2026-02-30T00:00:00Z"}),
    ("date-only", {"publishedOn": "2026-10-09"}),
    ("basic-date", {"publishedOn": "20261009"}),
    ("week-date", {"publishedOn": "2026-W41-5"}),
    ("offset-date", {"publishedOn": "2026-10-09T00:01:00+05:30"}),
    ("salary-booleans", {"salaryRange": {"minimum": True, "maximum": 120, "salaryPeriod": True}}),
    ("salary-negative", {"salaryRange": {"minimum": -1, "maximum": 0}}),
]:
    raw = k._job(**patch)
    freeze(
        "keka",
        "fields",
        name,
        dict(row=raw, tenant="acme", portal="default"),
        lambda raw=raw: keka._parse_job(raw, kboard),
    )

for name, patch in [
    ("populated", {}),
    ("unicode-space", {"JobTitle": " A\x1c B\u00a0 C "}),
    ("missing-title", {"JobTitle": None}),
    ("missing-id", {"JobId": None}),
    ("numeric-id", {"JobId": 1}),
    ("path-segment", {"JobIdObfuscated": "../abc?draft=true#section"}),
    ("utf8-segment", {"JobIdObfuscated": "caf%C3%A9/%ZZ"}),
    ("description-fallback", {"JobDescriptionV2": " ", "JobDescription": "<p>Fallback</p>"}),
    ("no-description", {"JobDescriptionV2": None, "JobDescription": None}),
    ("location-text", {"Location": " New Delhi, India "}),
    ("location-object", {"Location": "{}"}),
    ("location-fold", {"Location": json.dumps([{"Address": "Straße"}, {"Address": "STRASSE"}])}),
    ("unspecified", {"JobTypeV2": " Unspecified "}),
    ("employment-fallback", {"JobTypeV2": None, "Type": "Part Time"}),
    ("experience-shapes", {"Experience": {"MinExp": True, "MaxExp": "5"}}),
    ("date-fallback", {"PublishedDates": {}, "PublishedDate": "2026-10-09"}),
    ("numeric-date", {"PublishedDates": {"CAREERPAGE": 1}}),
]:
    raw = copy.deepcopy(t._raw_job())
    raw.update(patch)
    freeze(
        "turbohire",
        "fields",
        name,
        dict(row=raw, origin="https://flipkart.turbohire.co"),
        lambda raw=raw: turbohire._parse_job(raw, portal_origin="https://flipkart.turbohire.co"),
    )

for name, document, total in [
    ("populated", p._page([(560566, "plumber", "Plumber")], total=1, page=1, page_size=500), None),
    ("empty", p._page([], total=0, page=1, page_size=500), None),
    (
        "conflicting-count",
        p._page([(560566, "plumber", "Plumber")], total=2, page=1, page_size=500),
        None,
    ),
    (
        "changed-snapshot",
        p._page([(560566, "plumber", "Plumber")], total=1, page=1, page_size=500),
        2,
    ),
    (
        "cross-tenant",
        p._page(
            [(560566, "plumber", "Plumber")],
            total=1,
            page=1,
            page_size=500,
            source_board=PageUpBoard(999, "cw", "en-us"),
        ),
        None,
    ),
    (
        "generic-title",
        p._page([(560566, "plumber", "Apply now")], total=1, page=1, page_size=500),
        None,
    ),
    ("markerless-empty", "<html><body></body></html>", None),
]:
    freeze(
        "pageup",
        "listing-page",
        name,
        dict(
            body=document,
            instance=873,
            source_pointer="cw",
            locale="en-us",
            page=1,
            page_size=500,
            expected_total=total,
        ),
        lambda document=document, total=total: pageup._parse_listing_page(
            document,
            p.BOARD.page_url(1, page_size=500),
            p.BOARD,
            page=1,
            page_size=500,
            expected_total=total,
        ),
    )

iboard = infoniqa._board_from_url(i.BOARD_URL)
for name, body in [
    ("populated", i._shell()),
    ("cross-employer", i._shell(employer="Another Employer")),
    ("cross-origin", i._shell(quicksearch_host="https://other.infoniqa.io")),
    ("missing-csrf", i._shell().replace('name="_csrf"', 'name="other"')),
    ("invalid-body", i._shell().replace('class="jobOfferList"', 'class="jobOfferList other"')),
]:
    freeze(
        "infoniqa",
        "shell",
        name,
        dict(body=body, board_url=i.BOARD_URL, employer=i.EMPLOYER),
        lambda body=body: infoniqa._parse_shell(body, iboard, i.EMPLOYER),
    )
for name, body in [
    ("populated", i._search(2, jobs="".join(i._job(x) for x in i.JOB_IDS))),
    ("empty", i._search(0)),
    ("conflicting-counts", i._search(2, button_total=1)),
    ("duplicate-id", i._search(2, jobs=i._job(i.JOB_IDS[0]) * 2)),
    ("missing-title", i._search(1, jobs=i._job(i.JOB_IDS[0], title=""))),
]:
    freeze(
        "infoniqa",
        "search",
        name,
        dict(body=body, board_url=i.BOARD_URL),
        lambda body=body: infoniqa._parse_search(body, iboard),
    )

Path(__file__).with_name("python_portal_http_core.json").write_text(
    json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
)
print(f"Frozen {len(cases)} original four-provider protocol cases")
