from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from src.core.monitor import MonitorResult
from src.core.monitors import DiscoveredJob
from src.core.monitors.typify import PageConfig, _page_config, _parse_job, can_handle, discover
from src.shared.tdm import TDMReservedError
from src.workspace._compat import auto_scraper_type, auto_skip_crawler_types

BOARD_URL = "https://jobs.example.com/vacatures"
API_URL = "https://jobs.example.com/api/vacancies"
PAGE = """
<script src="/modules/typify/vacancy_search/js/view-vacancies.js"></script>
<input class="checkbox-button cb-function" data-id="14" name="function[14]">
<input class="checkbox-button cb-function" data-id="19" name="function[19]">
<script>window.typify = {language: 'nl'};</script>
"""


def _row(job_id: int, *, title: str | None = None) -> dict:
    return {
        "title": title or f"Job {job_id}",
        "url": f"/vacature/job-{job_id}",
        "location": {"id": str(job_id), "label": f"City {job_id}"},
    }


def _payload(rows: list[object], *, total: int | None = None, total_pages: int = 1) -> dict:
    return {
        "errors": [],
        "results": rows,
        "pagination": {
            "total": str(len(rows) if total is None else total),
            "total_pages": total_pages,
        },
    }


def _request_form(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode(), keep_blank_values=True)


def test_page_config_extracts_live_partitions_and_locale_api():
    assert _page_config(PAGE, BOARD_URL) == PageConfig(
        api_url=API_URL,
        function_ids=("14", "19"),
    )
    assert _page_config(PAGE, "https://www.dominosjobs.de/de/jobs") == PageConfig(
        api_url="https://www.dominosjobs.de/de/api/vacancies",
        function_ids=("14", "19"),
    )
    french = PAGE.replace("language: 'nl'", "language: 'fr'")
    assert _page_config(french, "https://jobs.example.com/fr/jobs") == PageConfig(
        api_url="https://jobs.example.com/fr/api/vacancies",
        function_ids=("14", "19"),
    )
    assert _page_config("<html></html>", BOARD_URL) is None


def test_typify_auto_enriches_descriptions():
    assert auto_scraper_type("typify", {}) == (
        "json-ld",
        {"enrich": ["description"]},
    )
    assert "typify" not in auto_skip_crawler_types()


def test_parse_job_maps_same_origin_title_and_location():
    assert _parse_job(_row(1), BOARD_URL) == DiscoveredJob(
        url="https://jobs.example.com/vacature/job-1",
        title="Job 1",
        locations=["City 1"],
    )
    assert _parse_job({"url": "/vacature/1"}, BOARD_URL) is None
    assert _parse_job({"title": "Job", "url": "https://evil.example/job"}, BOARD_URL) is None


def _happy_handler(request: httpx.Request) -> httpx.Response:
    if request.method == "GET":
        return httpx.Response(200, text=PAGE)
    assert str(request.url) == API_URL
    assert request.headers["x-requested-with"] == "XMLHttpRequest"
    assert request.headers["content-type"] == "application/x-www-form-urlencoded; charset=UTF-8"
    form = _request_form(request)
    function_ids = form.get("field_function[]")
    if not function_ids:
        if form["map"] == ["0"]:
            return httpx.Response(200, json=_payload([_row(1)], total=2, total_pages=2))
        return httpx.Response(200, json=_payload([_row(1), _row(2)]))
    assert form["map"] == ["1"]
    rows = {"14": [_row(1)], "19": [_row(2)]}[function_ids[0]]
    return httpx.Response(200, json=_payload(rows))


async def test_can_handle_verifies_widget_and_api():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_happy_handler)) as client:
        result = await can_handle(BOARD_URL, client)

    assert result == {"api_url": API_URL, "jobs": 2}


async def test_discover_unions_every_live_function_partition(monkeypatch):
    monkeypatch.setattr("src.core.monitors.typify.MAX_MAP_RESULTS", 1)
    board = {"board_url": BOARD_URL, "metadata": {"api_url": API_URL}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(_happy_handler)) as client:
        jobs = await discover(board, client)

    assert [job.url for job in jobs] == [
        "https://jobs.example.com/vacature/job-1",
        "https://jobs.example.com/vacature/job-2",
    ]
    assert [job.locations for job in jobs] == [["City 1"], ["City 2"]]


async def test_discover_recursively_splits_oversized_function_groups(monkeypatch):
    monkeypatch.setattr("src.core.monitors.typify.MAX_MAP_RESULTS", 0)
    page = PAGE.replace(
        '<input class="checkbox-button cb-function" data-id="14" name="function[14]">',
        "".join(
            f'<input class="checkbox-button cb-function" data-id="{value}">'
            for value in range(1, 5)
        ),
    ).replace(
        '<input class="checkbox-button cb-function" data-id="19" name="function[19]">',
        "",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=page)
        function_ids = _request_form(request).get("field_function[]")
        if not function_ids:
            return httpx.Response(200, json=_payload([], total=4, total_pages=1))
        if len(function_ids) > 1:
            return httpx.Response(200, json=_payload([_row(1)], total=2, total_pages=2))
        return httpx.Response(200, json=_payload([_row(int(function_ids[0]))]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = await discover({"board_url": BOARD_URL}, client)

    assert {job.url for job in jobs} == {
        f"https://jobs.example.com/vacature/job-{value}" for value in range(1, 5)
    }


async def test_discover_rejects_stale_configured_api_url():
    board = {
        "board_url": BOARD_URL,
        "metadata": {"api_url": "https://jobs.example.com/old-api"},
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(_happy_handler)) as client:
        with pytest.raises(ValueError, match="no longer matches"):
            await discover(board, client)


@pytest.mark.parametrize(
    ("partition_payload", "message"),
    [
        (_payload([_row(1)], total=2, total_pages=2), "stable single-response"),
        (_payload([{"url": "/vacature/1"}]), "invalid job"),
    ],
)
async def test_discover_rejects_partial_or_invalid_partitions(
    monkeypatch, partition_payload, message
):
    monkeypatch.setattr("src.core.monitors.typify.MAX_MAP_RESULTS", 0)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=PAGE)
        form = _request_form(request)
        if not form.get("field_function[]"):
            return httpx.Response(200, json=_payload([], total=2, total_pages=1))
        return httpx.Response(200, json=partition_payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match=message):
            await discover({"board_url": BOARD_URL}, client)


async def test_discover_deduplicates_identical_jobs_across_partitions(monkeypatch):
    monkeypatch.setattr("src.core.monitors.typify.MAX_MAP_RESULTS", 0)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=PAGE)
        form = _request_form(request)
        if not form.get("field_function[]"):
            return httpx.Response(200, json=_payload([], total=2, total_pages=1))
        return httpx.Response(200, json=_payload([_row(1)]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = await discover({"board_url": BOARD_URL}, client)

    assert [job.url for job in jobs] == ["https://jobs.example.com/vacature/job-1"]


async def test_discover_rejects_conflicting_duplicate_jobs(monkeypatch):
    monkeypatch.setattr("src.core.monitors.typify.MAX_MAP_RESULTS", 0)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=PAGE)
        form = _request_form(request)
        function_ids = form.get("field_function[]")
        if not function_ids:
            return httpx.Response(200, json=_payload([], total=2, total_pages=1))
        title = "First" if function_ids == ["14"] else "Second"
        return httpx.Response(200, json=_payload([_row(1, title=title)]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="conflicting duplicate"):
            await discover({"board_url": BOARD_URL}, client)


async def test_discover_rejects_partition_union_count_mismatch(monkeypatch):
    monkeypatch.setattr("src.core.monitors.typify.MAX_MAP_RESULTS", 0)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=PAGE)
        form = _request_form(request)
        function_ids = form.get("field_function[]")
        if not function_ids:
            return httpx.Response(200, json=_payload([], total=3, total_pages=1))
        job_id = 1 if function_ids == ["14"] else 2
        return httpx.Response(200, json=_payload([_row(job_id)]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="advertised total"):
            await discover({"board_url": BOARD_URL}, client)


async def test_cap_suppresses_tombstoning(monkeypatch):
    monkeypatch.setattr("src.core.monitors.typify.MAX_JOBS", 1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(_happy_handler)) as client:
        result = await discover({"board_url": BOARD_URL}, client)

    assert isinstance(result, MonitorResult)
    assert result.truncated is True
    assert len(result.urls) == 2


async def test_can_handle_propagates_tdm_reservation():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=PAGE)
        return httpx.Response(200, headers={"TDM-Reservation": "1"}, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TDMReservedError):
            await can_handle(BOARD_URL, client)
