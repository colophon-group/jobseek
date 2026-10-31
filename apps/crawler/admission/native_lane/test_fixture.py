from __future__ import annotations

import hashlib
import http.client
import http.server
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from admission.native_lane.fixture_server import FixtureState, handler
from admission.native_lane.workload import (
    ORIGINS,
    PROFILES,
    boards,
    detail_body,
    jobs,
    listing_body,
    workload_sha256,
)


@contextmanager
def server(state: FixtureState, *, storage: bool = False) -> Iterator[http.client.HTTPConnection]:
    service = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler(state, storage=storage))
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    client = http.client.HTTPConnection("127.0.0.1", service.server_port, timeout=3)
    try:
        yield client
    finally:
        client.close()
        service.shutdown()
        service.server_close()
        thread.join(timeout=3)


def test_workload_has_four_distinct_effective_routes_and_stable_identity() -> None:
    rows = jobs()
    assert len(rows) == len({row["url"] for row in rows}) == 536
    assert {row["profile"] for row in rows} == set(PROFILES)
    assert {row["origin"] for row in rows} == set(ORIGINS)
    assert workload_sha256() == "1a00139aee761ab7e6de570f1cfea1c6658c52459a94936bf239937ce62fa82d"
    assert all(
        {row["size"] for row in rows if row["profile"] == profile} == {"normal", "slow", "large"}
        for profile in PROFILES
    )
    configurations = {board["slug"]: board for board in boards()}
    assert configurations["api_rich"]["crawler_type"] == "api_sniffer"
    assert configurations["dom_rendered"]["monitor_needs_browser"] is True
    assert configurations["dom_rendered"]["scraper_needs_browser"] is True
    assert configurations["dom_direct"]["scraper_needs_browser"] is False
    assert configurations["jsonld_direct"]["monitor_seeded"] is False


def test_workload_salary_expectations_match_the_retained_python_parser() -> None:
    from src.core.salary_extract import _extract_salary_unified_python

    # Establish that the intended positive case exists in the original parser,
    # independently of either lane's persistence and the comparison assertions.
    for description in {row["description"] for row in jobs()}:
        salary = _extract_salary_unified_python(description)
        assert salary is not None
        assert (salary.min, salary.max, salary.currency, salary.period) == (
            100000,
            120000,
            "USD",
            "yearly",
        )


def test_rendered_inputs_require_script_execution() -> None:
    row = next(row for row in jobs() if row["profile"] == "dom_rendered")
    raw = detail_body(row)
    assert b'<script type="application/ld+json">' not in raw
    assert b"document.createElement('script')" in raw
    listing = listing_body("dom_rendered")
    assert b"document.body.innerHTML=" in listing
    # The fixture has no parsed anchor element before JavaScript runs.
    from bs4 import BeautifulSoup

    assert BeautifulSoup(listing, "html.parser").find("a") is None


@pytest.mark.parametrize("profile", ["dom_direct", "jsonld_direct"])
def test_direct_inputs_contain_complete_job_content(profile: str) -> None:
    from bs4 import BeautifulSoup

    for row in [row for row in jobs() if row["profile"] == profile]:
        script = BeautifulSoup(detail_body(row), "html.parser").find(
            "script", type="application/ld+json"
        )
        assert script is not None
        document = json.loads(script.string or "")
        assert document["@type"] == "JobPosting"
        assert document["title"] == row["title"]
        assert document["description"] == row["description"]
        assert document["employmentType"] == "FULL_TIME"
        assert document["jobLocationType"] == "TELECOMMUTE"


def test_api_listing_contains_rich_jobs_without_detail_fetches() -> None:
    document = json.loads(listing_body("api_rich"))
    assert len(document["jobs"]) == 500
    assert {row["url"] for row in document["jobs"]} == {
        row["url"] for row in jobs() if row["profile"] == "api_rich"
    }
    assert all(
        row["name"] and row["body"] and row["city"] == "United States" for row in document["jobs"]
    )


def test_upstream_rejects_cross_origin_and_unknown_paths() -> None:
    state = FixtureState()
    with server(state) as client:
        client.request("GET", "/jobs/dom_direct/0", headers={"Host": ORIGINS[0]})
        response = client.getresponse()
        assert response.status == 404
        response.read()
        client.request("GET", "/not-a-job", headers={"Host": ORIGINS[1]})
        response = client.getresponse()
        assert response.status == 404
        response.read()
    assert state.report()["unexpected_requests"] == 2


def test_upstream_returns_exact_stored_bytes_and_numeric_accounting() -> None:
    state = FixtureState()
    row = next(row for row in jobs() if row["profile"] == "jsonld_direct")
    with server(state) as client:
        client.request("GET", row["path"], headers={"Host": row["origin"]})
        response = client.getresponse()
        assert response.status == 200
        assert response.read() == detail_body(row)
    report = state.report()
    assert report["requests"] == {row["origin"] + row["path"]: 1}
    assert report["peak_upstream_inflight"] == 1
    assert report["unexpected_requests"] == 0


def test_description_sink_preserves_bytes_and_exposes_only_hashes() -> None:
    state = FixtureState()
    path = "/descriptions/job/fixture/en/latest.html"
    body = "<p>Développer Go services.</p>".encode()
    with server(state, storage=True) as client:
        client.request("PUT", path, body)
        response = client.getresponse()
        assert response.status == 200
        response.read()
        client.request("GET", path)
        response = client.getresponse()
        assert response.status == 200
        assert response.read() == body
        client.request("GET", "/report")
        response = client.getresponse()
        report = json.loads(response.read())
    assert report["uploaded_objects"] == {path: hashlib.sha256(body).hexdigest()}
    assert body.decode() not in json.dumps(report)


@pytest.mark.parametrize(
    "path,size",
    [
        ("/wrong/fixture", 1),
        ("/descriptions/job/fixture", 0),
        ("/descriptions/job/fixture", 8 * 1024 * 1024 + 1),
    ],
)
def test_sink_rejects_unbounded_or_wrongly_routed_puts_without_reading_body(
    path: str, size: int
) -> None:
    state = FixtureState()
    with server(state, storage=True) as client:
        client.request("PUT", path, headers={"Content-Length": str(size)})
        response = client.getresponse()
        assert response.status == 400
        response.read()
    assert state.report()["uploaded_objects"] == {}
