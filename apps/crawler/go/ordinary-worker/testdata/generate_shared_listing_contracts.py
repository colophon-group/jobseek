"""Capture current DOM whole-list totals and explicit API field/URL contracts."""

from __future__ import annotations

import asyncio
import csv
import json
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from src.core.monitors import api_sniffer, dom

ROOT = Path(__file__).resolve().parents[3]
SLUGS = {
    "alten-poland",
    "arthur-d-little-global-careers",
    "cambridge-aerospace-careers-api",
    "carrefour-france-careers-fr-franchise",
    "coop-careers-service7000",
    "cushman-wakefield-brazil",
    "cushman-wakefield-chile",
    "cushman-wakefield-mexico",
    "cushman-wakefield-peru",
    "fidelity-investments-careers-temporary",
    "maruti-suzuki-careers",
    "qnb-group-turkey",
    "vista-global-global",
}


async def main():
    out = []
    for row in csv.DictReader((ROOT / "data/boards.csv").open()):
        slug = row["board_slug"]
        if slug not in SLUGS:
            continue
        md = json.loads(row["monitor_config"])
        md["scraper_type"] = row["scraper_type"]
        if row["scraper_config"]:
            md["scraper_config"] = json.loads(row["scraper_config"])
        board = dict(provider=row["monitor_type"], board_url=row["board_url"], metadata=md)
        if board["provider"] == "api_sniffer":
            item = dict(
                title="Senior Software Engineer",
                description="<p>Build systems.</p>",
                locations=["Zurich"],
                created_at="2026-10-01",
                job_type="Full-time",
                category="Engineering",
                jobAdSite="Zurich",
                workspaceTypeId="hybrid",
                employmentType="Full-time",
                department="Engineering",
                pictureUrl="https://example.com/logo.png",
                links={"apply": "https://example.com/apply/101", "canonical": "/jobs/101"},
                JobTitle="Senior Software Engineer",
                PublicationUrlAbacusJobPortal="https://example.com/jobs/101",
                Organization="&lt;p&gt;Organisation&lt;/p&gt;",
                Tasks="&lt;p&gt;Build systems.&lt;/p&gt;",
                Requirements="&lt;p&gt;Experience&lt;/p&gt;",
                Benefits="&lt;p&gt;Learning&lt;/p&gt;",
                PlaceOfWorkCity="Zurich",
                PlaceOfWorkCountry="Switzerland",
                PublicationStartDate="2026-10-01",
                JobId="101",
                CompanyName="Fixture",
                PositionLevelOfEmployment="100%",
            )
            if slug == "maruti-suzuki-careers":
                payload = {"data": {"engineering": {"jobs": [item]}}}
            elif slug == "qnb-group-turkey":
                payload = {"ilanlar": [item]}
            else:
                payload = [item]
            body = json.dumps(payload, ensure_ascii=False)
            pages = None
        else:

            def job_url(i, board=board, slug=slug):
                origin = "https://" + urlsplit(board["board_url"]).netloc
                if slug.startswith("cushman-"):
                    return origin + f"/Detail/{i}"
                if slug == "alten-poland":
                    return origin + f"/jobs/{i}-{i}"
                if slug.startswith("carrefour-"):
                    return origin + f"/{i}/1/engineer"
                if slug.startswith("fidelity-"):
                    return origin + f"/job-search/{i}/engineer/?job-id={i}"
                if slug == "vista-global-global":
                    return f"https://careers-vistaglobal.icims.com/jobs/{i}/engineer/job"
                return origin + f"/jobs/{i}/engineer/job"

            def listing(i, total, slug=slug, job_url=job_url):
                link = f'<a href="{job_url(i)}">Engineer</a>'
                if slug == "alten-poland":
                    link = f'<div role="listitem"><a href="/jobs/{i}-{i}">Engineer</a></div>'
                if slug.startswith("fidelity-"):
                    link = (
                        '<div class="card-job"><h2 class="card-title">'
                        f'<a class="js-view-job" href="{job_url(i)}">Engineer</a></h2></div>'
                    )
                text = "2 vagas de emprego" if slug.endswith("brazil") else "2 ofertas de empleo"
                proof = (
                    f'<div id="pager-total-results">{total}</div>'
                    f"<main><header><p>Znaleziono {total} oferty</p></header></main>"
                    '<div id="VacancySection">'
                    f'<h2 class="color-title font-3xl">{text}</h2></div>'
                    f'<h2 class="job-count">{total} jobs available</h2>'
                )
                return "<html><body>" + proof + link + "</body></html>"

            pages = [listing(101, 2), listing(102, 2)]
            body = pages[0]
        exchanges = []
        listing_calls = 0

        async def handler(request, board=board, body=body, pages=pages, md=md, exchanges=exchanges):
            nonlocal listing_calls
            if board["provider"] == "api_sniffer":
                reply, status = body, 200
            elif md.get("require_jsonld_jobposting") and "/Detail/" in str(request.url):
                reply = (
                    '<script type="application/ld+json">'
                    + json.dumps(
                        {
                            "@context": "https://schema.org",
                            "@type": "JobPosting",
                            "title": "Engineer",
                            "description": "<p>Build systems.</p>",
                            "url": str(request.url),
                        }
                    )
                    + "</script>"
                )
                status = 200
            else:
                reply = pages[min(listing_calls, 1)]
                status = 200 if listing_calls < 2 else 404
                listing_calls += 1
            headers = {
                "content-type": "application/json"
                if board["provider"] == "api_sniffer"
                else "text/html"
            }
            exchanges.append(
                dict(
                    method=request.method,
                    url=str(request.url),
                    body=(await request.aread()).decode(),
                    response=dict(status=status, body=reply, headers=headers),
                )
            )
            return httpx.Response(status, text=reply, headers=headers)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            try:
                result = await (
                    api_sniffer.discover(board, client)
                    if board["provider"] == "api_sniffer"
                    else dom.dom_discover(board, client)
                )
            except Exception as e:
                raise RuntimeError("original fixture failed for " + slug) from e
        if isinstance(result, set):
            jobs = [dict(url=u) for u in sorted(result)]
        else:
            jobs = [asdict(j) for j in result]
        assert jobs, (slug, "empty original fixture")
        out.append(dict(name=slug, board=board, response=body, expected=jobs, exchanges=exchanges))
    assert len(out) == 13
    Path(__file__).with_name("python_shared_listing_contracts.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
