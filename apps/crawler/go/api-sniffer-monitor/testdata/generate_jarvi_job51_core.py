"""Freeze original Jarvi and 51job parsing and public request signatures."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict
from pathlib import Path

from src.core.monitors import jarvi, job51

cases = []


def freeze(name, provider, operation, inputs, call):
    try:
        result = call()
        if hasattr(result, "__dataclass_fields__"):
            result = {k: v for k, v in asdict(result).items() if v is not None}
        cases.append(
            dict(name=name, provider=provider, operation=operation, inputs=inputs, expected=result)
        )
    except (ValueError, TypeError):
        cases.append(
            dict(name=name, provider=provider, operation=operation, inputs=inputs, error=True)
        )


def field(purpose, value=None, **extra):
    return dict(field=dict(purpose=purpose), value=value, **extra)


raw = dict(
    id="offer-id",
    shortId="j123",
    name="Fallback title",
    publishedAt="2026-10-08",
    updatedAt="2026-10-09",
    fieldsValues=[
        field("joboffer_title", " Développeur &amp; Straße Ι "),
        field("joboffer_company_description", "<p>Company</p>"),
        field("joboffer_description", "<div>Build</div>"),
        field("joboffer_profile_description", "<p>Localisation : Genève - Contrat : CDI</p>"),
        field("joboffer_is_fulltime", "TRUE"),
        field("joboffer_remote_days_per_week", "2,5"),
        field("joboffer_salary_is_public", "true"),
        field("joboffer_salary_per_year_min", "50000,50"),
        field("joboffer_salary_per_year_max", "70000"),
        field("joboffer_min_years_of_experience", "3"),
    ],
)
board = "https://fixture.invalid/careers?z=last&a=first&z=again&q=old&blank=#fragment"
for mode in [
    "rich",
    "no-title",
    "numeric-id",
    "fallback-title",
    "locations",
    "whitespace-locations",
    "choice",
    "private-salary",
    "bad-number",
    "no-short-id",
    "empty-description",
    "unicode",
]:
    row = copy.deepcopy(raw)
    if mode == "no-title":
        row["name"] = ""
        row["fieldsValues"] = []
    elif mode == "numeric-id":
        row["shortId"] = 123
    elif mode == "fallback-title":
        row["fieldsValues"] = []
    elif mode in {"locations", "whitespace-locations"}:
        name = " Genève " if mode == "whitespace-locations" else "Genève"
        row["fieldsValues"] += [
            field("joboffer_location", location={"formattedAddress": name}),
            field("joboffer_location", location={"formattedAddress": name}),
            field("joboffer_location", location={"search": "Paris"}),
        ]
    elif mode == "choice":
        row["fieldsValues"].append(
            field(
                "joboffer_contract_type", fieldValue={"technicalValue": "contract", "name": "CDD"}
            )
        )
    elif mode == "private-salary":
        row["fieldsValues"] = [
            v for v in row["fieldsValues"] if v["field"]["purpose"] != "joboffer_salary_is_public"
        ]
    elif mode == "bad-number":
        for value in row["fieldsValues"]:
            if "salary_per_year" in value["field"]["purpose"]:
                value["value"] = "unknown"
    elif mode == "no-short-id":
        row["shortId"] = ""
    elif mode == "empty-description":
        row["fieldsValues"] = [field("joboffer_title", "Engineer")]
    elif mode == "unicode":
        row["fieldsValues"] = [field("joboffer_title", "Straße İstanbul 中文 Engineer")]
    freeze(
        mode,
        "jarvi",
        "fields",
        dict(row=row, board=board, currency="CHF"),
        lambda row=row: jarvi._parse_job(row, board, "CHF"),
    )

for page in [
    "<div data-sdk='jarvi' data-public-api-key='public_fixture_key' data-currency='chf'>",
    '<div data-sdk="JARVI" data-public-api-key="fixture&amp;key">',
    "<div data-public-api-key='fixture'>",
    "<div data-sdk='jarvi'>",
    "<div data-sdk='jarvi\" data-public-api-key='fixture'>",
]:
    freeze(
        "embed", "jarvi", "embed", dict(page=page), lambda page=page: jarvi._embed_metadata(page)
    )

for page in [1, 2, 2500]:
    freeze(
        "list-sign",
        "job51",
        "list-request",
        dict(ctmid=12345, page=page),
        lambda page=page: job51._signed_url("job_list.php", job51._list_params(12345, page)),
    )
for identity in ["000123", "12345678901234567890"]:
    freeze(
        "detail-sign",
        "job51",
        "detail-request",
        dict(id=identity),
        lambda identity=identity: job51._signed_url("job_detail.php", dict(jobid=identity)),
    )
for body in [
    'jsoncallback({"status":"1","resultbody":{"totalnum":"0","joblist":[]}})',
    'jsoncallback({"status":"0","resultbody":{}})',
    'jsoncallback({"status":"1","resultbody":[]})',
    'jsoncallback({"status":"1","resultbody":{}});',
]:
    freeze("jsonp", "job51", "jsonp", dict(body=body), lambda body=body: job51._parse_jsonp(body))

row = dict(
    jobid="000123",
    ctmid="12345",
    jobname=" 工程師 ",
    jobinfo="<div>开发<br><br>任职要求：<strong>経験</strong><script>private</script></div>",
    jobareaname=" 上海 ",
    term=" 全职 ",
    issuedate="2026-10-08 12:00:00",
    jkeyword=" Python Go Python ",
    coname=" 公司 ",
    divname=" 平台 ",
    address="地址",
    funtype="技术",
    workyearname="三年",
    degreefrom="本科",
    providesalarname="面议",
    jobwelf="假期",
)
for mode in [
    "rich",
    "wrong-employer",
    "wrong-id",
    "empty-title",
    "empty-description",
    "no-qualification",
    "alternate-location",
]:
    job = copy.deepcopy(row)
    if mode == "wrong-employer":
        job["ctmid"] = "12346"
    elif mode == "wrong-id":
        job["jobid"] = "123"
    elif mode == "empty-title":
        job["jobname"] = " "
    elif mode == "empty-description":
        job["jobinfo"] = "<script>private</script>"
    elif mode == "no-qualification":
        job["jobinfo"] = "<p>Build</p>"
    elif mode == "alternate-location":
        job["jobareaname"] = ""
        job["workareaname"] = "北京"
    freeze(
        mode,
        "job51",
        "fields",
        dict(row=job, ctmid=12345, id="000123"),
        lambda job=job: job51._parse_job(job, ctmid=12345, expected_job_id="000123"),
    )

Path(__file__).with_name("python_jarvi_job51_core.json").write_text(
    json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
)
print(f"Frozen {len(cases)} actual Python cases")
