"""Freeze BrassRing and Njoyn decisions through the delivered Python routines."""

from __future__ import annotations

import asyncio
import html
import json
from dataclasses import asdict
from pathlib import Path

from src.core.monitors import brassring, njoyn
from src.shared.html_normalize import normalize_description_html

BRASS = "https://sjobs.brassring.com/TGnewUI/Search/Home/Home?partnerid=25416&siteid=5998"
NJOYN = "https://cgi.njoyn.com/corp/xweb/XWeb.asp?CLID=21001&Page=JobListing"


def job_url(index: int) -> str:
    return (
        "https://cgi.njoyn.com/corp/xweb/XWeb.asp?"
        f"CLID=21001&Page=JobDetails&Jobid=J{index}&BRID={index}&lang=1"
    )


def outcome(call):
    try:
        return dict(expected=call(), error=False, snapshot_change=False)
    except Exception as exc:
        return dict(
            expected=None,
            error=True,
            snapshot_change=isinstance(
                exc, (brassring._SnapshotChanged, njoyn._ListingSnapshotChanged)
            ),
        )


fixtures = {
    "brass_boards": [],
    "brass_jobs": [],
    "brass_details": [],
    "brass_pages": [],
    "njoyn_urls": [],
    "njoyn_snapshots": [],
    "njoyn_shapes": [],
    "njoyn_reconcile": [],
    "normalizations": {},
}
for raw in [
    BRASS,
    BRASS.replace("partnerid", "partnerId").replace("siteid", "siteId"),
    "https://example.com/jobs?partnerid=1&siteid=2",
    BRASS.replace("25416", "bad"),
    BRASS.replace("5998", ""),
    BRASS.replace("https://", "http://"),
]:
    fixtures["brass_boards"].append(
        dict(source=raw, **outcome(lambda raw=raw: brassring._board_ids(raw)))
    )

base = dict(
    reqid="3383626",
    jobtitle="  Engineer &amp; Support  ",
    jobdescription="<strong>Build</strong><ul><li>Help</li></ul>",
    formtext8="Evansville ",
    formtext9="IN - Indiana",
    formtext10="US",
    lastupdated="12-Aug-2026",
    department="Sales",
)
changes = [
    ("rich", {}),
    ("fallback-description", dict(jobdescription="", formtext3="<p>Global description</p>")),
    ("optional-description", dict(jobdescription=None)),
    ("non-string-description", dict(jobdescription=1)),
    ("numeric-zero-description", dict(jobdescription=0, formtext3="Fallback")),
    ("no-title", dict(jobtitle="")),
    ("numeric-id", dict(reqid=3383626)),
    ("bad-id", dict(reqid="x")),
    ("empty-id", dict(reqid="")),
    ("unicode-space", dict(jobtitle="A\x1cB\u00a0C")),
    ("casefold-location", dict(formtext8="Straße", formtext9="STRASSE", formtext10="")),
    (
        "fallback-location",
        dict(formtext8="", formtext9=None, formtext10="", location="Geneva &amp; Zürich"),
    ),
    ("missing-location", dict(formtext8="", formtext9="", formtext10="")),
    ("invalid-date", dict(lastupdated="not a date")),
    ("lowercase-date", dict(lastupdated="12-aug-2026")),
    ("blank-department", dict(department=" ")),
]
for name, changed in changes:
    values = base | changed
    row = dict(
        Questions=[dict(QuestionName=k, Value=v) for k, v in values.items()],
        Link="https://sjobs.brassring.com/TGnewUI/Search/home/HomeWithPreLoad?partnerid=25416&siteid=5998&PageType=JobDetails&jobid="
        + str(values["reqid"]),
    )
    raw_description = values.get("jobdescription") or values.get("formtext3")
    text = raw_description if isinstance(raw_description, str) else ""
    fixtures["normalizations"][text] = normalize_description_html(text)
    result = outcome(lambda row=row: brassring._parse_job(row, "25416", "5998"))
    if result["expected"] is not None:
        result["expected"] = {
            k: asdict(result["expected"])[k]
            for k in ("url", "title", "description", "locations", "date_posted", "metadata")
        }
    fixtures["brass_jobs"].append(dict(name=name, row=row, **result))
for name, row in [
    ("nonobject", []),
    (
        "wrong-link-id",
        dict(Questions=fixtures["brass_jobs"][0]["row"]["Questions"], Link=BRASS + "&jobid=other"),
    ),
    (
        "foreign-tenant",
        dict(
            Questions=fixtures["brass_jobs"][0]["row"]["Questions"],
            Link=fixtures["brass_jobs"][0]["row"]["Link"].replace("5998", "3"),
        ),
    ),
]:
    result = outcome(lambda row=row: brassring._parse_job(row, "25416", "5998"))
    fixtures["brass_jobs"].append(dict(name=name, row=row, **result))

for name, payload in [
    (
        "location",
        dict(
            JobId="3383626",
            Jobdetails=dict(
                JobDetailQuestions=[dict(VerityZone="location", AnswerValue="Plant & Zürich")]
            ),
        ),
    ),
    (
        "components",
        dict(
            JobId=3383626,
            Jobdetails=dict(
                JobDetailQuestions=[
                    dict(VerityZone="FORMTEXT8", AnswerValue="Straße"),
                    dict(VerityZone="formtext9", AnswerValue="STRASSE"),
                    dict(VerityZone="formtext10", AnswerValue="DE"),
                ]
            ),
        ),
    ),
    ("foreign-id", dict(JobId="foreign", Jobdetails=dict(JobDetailQuestions=[]))),
    ("missing-questions", dict(JobId="3383626")),
    ("missing-location", dict(JobId="3383626", Jobdetails=dict(JobDetailQuestions=[]))),
]:
    body = '<input id="preLoadJSON" value="' + html.escape(json.dumps(payload), quote=True) + '">'
    fixtures["brass_details"].append(
        dict(
            name=name,
            body=body,
            **outcome(lambda body=body: brassring._detail_location(body, "3383626")),
        )
    )
for payload in [
    dict(JobsCount=0),
    dict(JobsCount=0, Jobs=dict(Job=[])),
    dict(JobsCount=1, Jobs=dict(Job=[dict()])),
    dict(JobsCount=True),
    dict(JobsCount=1.0),
    dict(JobsCount=-1),
    dict(JobsCount="1"),
    dict(JobsCount=1, Jobs=dict(Job=None)),
    [],
    None,
]:
    fixtures["brass_pages"].append(
        dict(payload=payload, **outcome(lambda payload=payload: brassring._parse_page(payload)))
    )

for raw in [
    job_url(1),
    job_url(1).replace("JobDetails", "jobdetails"),
    job_url(1).replace("https", "http"),
    job_url(1).replace("cgi.njoyn.com", "user@cgi.njoyn.com"),
    job_url(1).replace("cgi.njoyn.com", "cgi.njoyn.com:444"),
    job_url(1).replace("21001", "22002"),
    job_url(1).replace("CLID=21001", "CLID=21001&CLID=22002"),
    job_url(1).replace("Jobid=J1", "Jobid="),
    job_url(1).replace("BRID=1", "BRID="),
    NJOYN,
]:
    fixtures["njoyn_urls"].append(
        dict(source=raw, expected=njoyn._is_job_detail_url(raw, board_url=NJOYN))
    )


class PageState:
    def __init__(self, state):
        self.state = state

    async def evaluate(self, _):
        return self.state


for name, text, page, links in [
    ("first", "Search Results (3,071) Page 1 of 62", "1", [job_url(1), job_url(2), NJOYN]),
    ("empty", "Search Results (0) Page 1 of 1", "1", []),
    ("duplicate-link", "Search Results (1) Page 1 of 1", "1", [job_url(1), job_url(1)]),
    ("missing-count", "Current opportunities Page 1 of 1", "1", []),
    ("missing-pages", "Search Results (1)", "1", [job_url(1)]),
    ("numeric-page-missing", "Search Results (1) Page 1 of 1", "", [job_url(1)]),
    ("page-mismatch", "Search Results (1) Page 2 of 2", "1", [job_url(1)]),
    ("over-cap", "Search Results (50,001) Page 1 of 1001", "1", [job_url(1)]),
]:
    state = dict(text=text, pageNumber=page, links=links)
    result = outcome(lambda state=state: asyncio.run(njoyn._page_snapshot(PageState(state), NJOYN)))
    if result["expected"] is not None:
        value = result["expected"]
        result["expected"] = dict(
            urls=sorted(value.urls),
            total=value.result_total,
            page_number=value.page_number,
            page_count=value.page_count,
        )
    fixtures["njoyn_snapshots"].append(dict(name=name, **state, **result))

for name, total, page, pages, count, size, limit in [
    ("full", 5, 1, 3, 2, 2, 200),
    ("tail", 5, 3, 3, 1, 2, 200),
    ("bad-tail", 5, 3, 3, 2, 2, 200),
    ("empty", 0, 1, 1, 0, 0, 200),
    ("false-empty", 0, 1, 1, 1, 1, 200),
    ("cap", 5, 1, 3, 2, 2, 2),
    ("no-links", 5, 1, 3, 0, 0, 200),
    ("bad-pages", 5, 1, 2, 2, 2, 200),
]:
    snapshot = njoyn._PageSnapshot(frozenset(job_url(i) for i in range(count)), total, page, pages)
    fixtures["njoyn_shapes"].append(
        dict(
            name=name,
            total=total,
            page_number=page,
            page_count=pages,
            count=count,
            size=size,
            limit=limit,
            **outcome(
                lambda snapshot=snapshot, size=size, limit=limit: njoyn._validate_page_shape(
                    snapshot, page_size=size, max_pages=limit
                )
            ),
        )
    )

for name, first_ids, second_ids, first_totals, second_totals in [
    ("exact", [1, 2, 3], [1, 2, 3], [3], [3]),
    ("head-churn", [1, 2, 3], [2, 3, 4], [3], [3]),
    ("one-growth", [1, 2, 3], [1, 2, 3, 4], [3], [4]),
    ("too-much-churn", [1, 2, 3], [4, 5, 6], [3], [3]),
    ("total-drift", [1, 2, 3], [1, 2, 3], [3], [6]),
    ("inventory-gap", [1, 2], [1, 2], [4], [4]),
    ("empty", [], [], [0], [0]),
]:
    first = njoyn._ListingPass(frozenset(job_url(i) for i in first_ids), tuple(first_totals), 1)
    second = njoyn._ListingPass(frozenset(job_url(i) for i in second_ids), tuple(second_totals), 1)
    result = outcome(
        lambda first=first, second=second: sorted(njoyn._reconcile_listing_passes(first, second))
    )
    fixtures["njoyn_reconcile"].append(
        dict(
            name=name,
            first_urls=sorted(first.urls),
            second_urls=sorted(second.urls),
            first_totals=first_totals,
            second_totals=second_totals,
            **result,
        )
    )

Path(__file__).with_name("python_session_provider_core.json").write_text(
    json.dumps(fixtures, ensure_ascii=False, indent=2) + "\n"
)
print({name: len(value) for name, value in fixtures.items()})
