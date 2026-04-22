from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.talentclue import (
    _api_url,
    _default_filter,
    _encode_filter,
    _extract_subdomain_from_url,
    _extract_widget_metadata,
    _parse_date_posted,
    _parse_employment_type,
    _parse_job,
    _parse_job_location_type,
    _parse_jobs_payload,
    _parse_locations,
    can_handle,
    discover,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_real_fixture() -> dict:
    path = FIXTURES_DIR / "talentclue_mcdonalds_es.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ── Filter encoding ──────────────────────────────────────────────────────


class TestFilter:
    def test_default_filter_has_expected_keys(self):
        obj = _default_filter("es")
        assert obj["op"] == 1
        assert obj["subs"] == "1"
        assert obj["lang"] == "es"
        assert obj["includeOnlyClosedJobs"] is False
        assert obj["showArchivedJobs"] is False

    def test_encode_filter_roundtrip(self):
        obj = {"lang": "en", "subs": "1"}
        encoded = _encode_filter(obj)
        decoded = json.loads(base64.b64decode(encoded).decode())
        assert decoded == obj

    def test_encode_filter_compact_json(self):
        # Must use compact separators so cached URLs line up with the widget.
        obj = {"a": 1, "b": 2}
        encoded = _encode_filter(obj)
        text = base64.b64decode(encoded).decode()
        assert ", " not in text
        assert ": " not in text

    def test_encode_filter_matches_real_widget_output(self):
        """The base64 produced for the documented default filter must be
        consistent and round-trip to the same JSON the widget sends."""
        encoded = _encode_filter(_default_filter("es"))
        roundtripped = json.loads(base64.b64decode(encoded).decode())
        assert roundtripped == _default_filter("es")

    def test_api_url_structure(self):
        url = _api_url("abc123def456" + "0" * 20, _default_filter("es"))
        assert url.startswith("https://api.talentclue.com/jswidget-ajax/jswidget/jobs/")
        # Path must contain the client_id and a base64 chunk.
        parts = url.rsplit("/", 2)
        assert parts[-2] == "abc123def456" + "0" * 20
        # base64 chunk decodes to JSON
        json.loads(base64.b64decode(parts[-1]).decode())


# ── Field parsers ────────────────────────────────────────────────────────


class TestParseLocations:
    def test_city_and_province_joined(self):
        assert _parse_locations({"city": "Maó", "province_label": "Illes Balears"}) == [
            "Maó, Illes Balears"
        ]

    def test_city_same_as_province(self):
        assert _parse_locations({"city": "Madrid", "province_label": "Madrid"}) == ["Madrid"]

    def test_city_only(self):
        assert _parse_locations({"city": "Barcelona"}) == ["Barcelona"]

    def test_province_only(self):
        assert _parse_locations({"province_label": "León"}) == ["León"]

    def test_country_fallback(self):
        assert _parse_locations({"country_label": "España"}) == ["España"]

    def test_empty_returns_none(self):
        assert _parse_locations({}) is None
        assert _parse_locations({"city": "", "country_label": ""}) is None


class TestParseJobLocationType:
    def test_presencial(self):
        assert _parse_job_location_type({"work_modality": "Presencial"}) == "On-site"

    def test_remoto(self):
        assert _parse_job_location_type({"work_modality": "Remoto"}) == "Remote"

    def test_teletrabajo(self):
        assert _parse_job_location_type({"work_modality": "Teletrabajo"}) == "Remote"

    def test_hibrido(self):
        assert _parse_job_location_type({"work_modality": "Híbrido"}) == "Hybrid"

    def test_unknown_returns_none(self):
        assert _parse_job_location_type({"work_modality": "Algo más"}) is None

    def test_missing_returns_none(self):
        assert _parse_job_location_type({}) is None


class TestParseEmploymentType:
    def test_jornada_completa(self):
        assert _parse_employment_type({"shift_label": "Jornada completa"}) == "Full-time"

    def test_jornada_parcial(self):
        assert _parse_employment_type({"shift_label": "Jornada parcial"}) == "Part-time"

    def test_media_jornada(self):
        assert _parse_employment_type({"shift_label": "Media jornada"}) == "Part-time"

    def test_english_labels(self):
        assert _parse_employment_type({"shift_label": "Full-time"}) == "Full-time"

    def test_contract_practicas_overrides_shift(self):
        raw = {"contract_label": "Prácticas", "shift_label": "Jornada completa"}
        assert _parse_employment_type(raw) == "Intern"

    def test_contract_becario(self):
        assert _parse_employment_type({"contract_label": "Becario"}) == "Intern"

    def test_unknown_returns_none(self):
        assert _parse_employment_type({"shift_label": "Flexible"}) is None

    def test_missing_returns_none(self):
        assert _parse_employment_type({}) is None


class TestParseDatePosted:
    def test_dd_mm_yyyy(self):
        assert _parse_date_posted({"post_date": "21/04/2026"}) == "2026-04-21"

    def test_invalid_format_falls_back_to_timestamp(self):
        assert (
            _parse_date_posted({"post_date": "Apr 21", "post_date_timestamp": "1776772661"})
            == "2026-04-21"
        )

    def test_timestamp_only(self):
        assert _parse_date_posted({"post_date_timestamp": "1776772661"}) == "2026-04-21"

    def test_missing_returns_none(self):
        assert _parse_date_posted({}) is None

    def test_garbage_timestamp_returns_none(self):
        assert _parse_date_posted({"post_date_timestamp": "not-a-number"}) is None


# ── Job parsing (real payload) ───────────────────────────────────────────


class TestParseJobRealFixture:
    def test_parses_real_mcdonalds_job(self):
        fixture = _load_real_fixture()
        jid, raw = next(iter(fixture["jobs"].items()))
        job = _parse_job(jid, raw)
        assert isinstance(job, DiscoveredJob)
        assert job.url.startswith("https://mcdonalds.talentclue.com/")
        assert job.title  # non-empty
        assert job.language == "es"
        # date_posted normalised to ISO
        assert job.date_posted is not None
        assert len(job.date_posted) == 10 and job.date_posted[4] == "-"
        # metadata has ID that matches the key
        assert job.metadata is not None and job.metadata["id"] == jid
        # No description from the list API
        assert job.description is None

    def test_parse_jobs_payload_real_fixture(self):
        fixture = _load_real_fixture()
        jobs = _parse_jobs_payload(fixture)
        assert len(jobs) == len(fixture["jobs"])
        for j in jobs:
            assert j.url
            assert j.title

    def test_missing_url_returns_none(self):
        assert _parse_job("1", {"title": "No URL"}) is None

    def test_non_string_url_returns_none(self):
        assert _parse_job("1", {"url": 42, "title": "bad"}) is None

    def test_metadata_strips_empty_values(self):
        raw = {
            "url": "https://foo.talentclue.com/es/node/1/2",
            "title": "X",
            "company_id": "",  # empty → skipped
            "subgroup": "Real",
            "vacancy": "3",
        }
        job = _parse_job("1", raw)
        assert job.metadata is not None
        assert "company_id" not in job.metadata
        assert job.metadata["subgroup"] == "Real"
        assert job.metadata["vacancies"] == "3"

    def test_geolocation_included_when_complete(self):
        raw = {
            "url": "https://foo.talentclue.com/es/node/1/2",
            "geolocation": {"lat": "40.0", "lng": "-3.7"},
        }
        job = _parse_job("1", raw)
        assert job.metadata["geolocation"] == {"lat": "40.0", "lng": "-3.7"}

    def test_geolocation_skipped_when_partial(self):
        raw = {
            "url": "https://foo.talentclue.com/es/node/1/2",
            "geolocation": {"lat": "40.0"},
        }
        job = _parse_job("1", raw)
        assert "geolocation" not in job.metadata


# ── Widget extraction ────────────────────────────────────────────────────


class TestExtractWidgetMetadata:
    _WIDGET_HTML = (
        "<html><body>"
        '<div id="tc-jswidget" data-client-id="3277d5dd7c62b36c4e13b1f9b8a7f3e4" '
        'data-lang="es" data-job-listing="1"></div>'
        '<script src="https://careers.talentclue.com/sites/static/widget/jswidget.min.js"></script>'
        "</body></html>"
    )

    def test_full_widget(self):
        meta = _extract_widget_metadata(self._WIDGET_HTML)
        assert meta == {
            "client_id": "3277d5dd7c62b36c4e13b1f9b8a7f3e4",
            "lang": "es",
        }

    def test_without_lang(self):
        html = (
            '<div id="tc-jswidget" data-client-id="ABCDEF0123456789ABCDEF0123456789"></div>'
            '<script src="https://careers.talentclue.com/sites/static/widget/jswidget.min.js"></script>'
        )
        meta = _extract_widget_metadata(html)
        assert meta is not None
        # client_id is lowercased
        assert meta["client_id"] == "abcdef0123456789abcdef0123456789"
        assert "lang" not in meta

    def test_html_entity_quotes(self):
        # Some pages escape quotes when rendering widget snippets (e.g. via
        # WYSIWYG editors that HTML-entity-escape attributes).
        html = (
            "<div id=&quot;tc-jswidget&quot; "
            "data-client-id=&quot;abcdef0123456789abcdef0123456789&quot; "
            "data-lang=&quot;en&quot;></div>"
            '<script src="https://careers.talentclue.com/sites/static/widget/jswidget.min.js"></script>'
        )
        meta = _extract_widget_metadata(html)
        assert meta == {"client_id": "abcdef0123456789abcdef0123456789", "lang": "en"}

    def test_no_widget(self):
        assert _extract_widget_metadata("<html><body>nothing here</body></html>") is None

    def test_script_without_client_id(self):
        html = '<script src="https://careers.talentclue.com/sites/static/widget/jswidget.min.js"></script>'
        assert _extract_widget_metadata(html) is None


class TestExtractSubdomain:
    def test_customer_subdomain(self):
        assert (
            _extract_subdomain_from_url("https://mcdonalds.talentclue.com/es/node/1/2")
            == "mcdonalds"
        )

    def test_ignored_subdomain(self):
        assert _extract_subdomain_from_url("https://careers.talentclue.com/sites/..") is None
        assert _extract_subdomain_from_url("https://www.talentclue.com/") is None

    def test_non_talentclue(self):
        assert _extract_subdomain_from_url("https://example.com/") is None


# ── discover() ────────────────────────────────────────────────────────────


class TestDiscover:
    async def test_returns_jobs_from_real_payload(self):
        fixture = _load_real_fixture()

        def handler(request: httpx.Request) -> httpx.Response:
            # Verify request shape: POST, correct path, correct Accept.
            assert request.method == "POST"
            assert "/jswidget-ajax/jswidget/jobs/" in str(request.url)
            assert request.headers.get("accept") == "application/json"
            return httpx.Response(200, json=fixture)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://empleo.mcdonalds.es/",
                "metadata": {"client_id": "3277d5dd7c62b36c4e13b1f9b8a7f3e4", "lang": "es"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == len(fixture["jobs"])
            assert all(isinstance(j, DiscoveredJob) for j in jobs)

    async def test_empty_response(self):
        def handler(request):
            return httpx.Response(200, json={"jobs": {}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://empleo.mcdonalds.es/",
                "metadata": {"client_id": "a" * 32},
            }
            jobs = await discover(board, client)
            assert jobs == []

    async def test_no_client_id_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "https://example.com/", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot derive TalentClue client_id"):
                await discover(board, client)

    async def test_http_error_raises(self):
        def handler(request):
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/",
                "metadata": {"client_id": "a" * 32},
            }
            with pytest.raises(httpx.HTTPStatusError):
                await discover(board, client)

    async def test_non_json_body_returns_empty(self):
        """If the API starts returning XML again we don't want a traceback —
        we just surface no jobs so the failure is visible via the usual
        'zero jobs' path."""

        def handler(request):
            return httpx.Response(
                200,
                content=b"<?xml version='1.0'?><result><jobs/></result>",
                headers={"content-type": "text/xml; charset=utf-8"},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/",
                "metadata": {"client_id": "a" * 32},
            }
            jobs = await discover(board, client)
            assert jobs == []

    async def test_default_lang_is_spanish(self):
        seen: dict = {}

        def handler(request):
            seen["url"] = str(request.url)
            return httpx.Response(200, json={"jobs": {}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://empleo.mcdonalds.es/",
                "metadata": {"client_id": "a" * 32},  # no lang set
            }
            await discover(board, client)
            # Decode the base64 chunk from the URL and check lang=es
            b64 = seen["url"].rsplit("/", 1)[-1]
            payload = json.loads(base64.b64decode(b64).decode())
            assert payload["lang"] == "es"


# ── can_handle() ─────────────────────────────────────────────────────────


class TestCanHandle:
    _WIDGET_HTML = (
        "<html><body>"
        '<div id="tc-jswidget" data-client-id="3277d5dd7c62b36c4e13b1f9b8a7f3e4" '
        'data-lang="es"></div>'
        '<script src="https://careers.talentclue.com/sites/static/widget/jswidget.min.js">'
        "</script></body></html>"
    )

    async def test_detects_widget_in_page(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "api.talentclue.com" in url:
                return httpx.Response(
                    200, json={"jobs": {"1": {"url": "https://x.talentclue.com/es/node/1/2"}}}
                )
            return httpx.Response(200, text=self._WIDGET_HTML)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://empleo.mcdonalds.es/", client)
            assert result is not None
            assert result["client_id"] == "3277d5dd7c62b36c4e13b1f9b8a7f3e4"
            assert result["lang"] == "es"
            assert result["jobs"] == 1

    async def test_detects_widget_but_api_unreachable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "api.talentclue.com" in url:
                return httpx.Response(500)
            return httpx.Response(200, text=self._WIDGET_HTML)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://empleo.mcdonalds.es/", client)
            assert result is not None
            assert result["client_id"] == "3277d5dd7c62b36c4e13b1f9b8a7f3e4"
            # No job count when the probe fails
            assert "jobs" not in result

    async def test_subdomain_url_detected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html></html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://mcdonalds.talentclue.com/es/node/1/2", client)
            assert result == {"subdomain": "mcdonalds"}

    async def test_no_client_offline_direct_subdomain(self):
        # With no client we can only detect direct *.talentclue.com URLs
        result = await can_handle("https://mcdonalds.talentclue.com/es/node/1/2")
        assert result == {"subdomain": "mcdonalds"}

    async def test_no_client_no_match(self):
        result = await can_handle("https://example.com/")
        assert result is None

    async def test_unrelated_page(self):
        def handler(request):
            return httpx.Response(200, text="<html>nothing here</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/", client)
            assert result is None


# ── Registration ──────────────────────────────────────────────────────────


def test_monitor_registered():
    from src.core.monitors import _REGISTRY

    names = [m.name for m in _REGISTRY]
    assert "talentclue" in names
    m = next(m for m in _REGISTRY if m.name == "talentclue")
    assert m.cost == 12
    # rich=False because descriptions are not returned by the list API —
    # the scraper still runs to enrich detail pages.
    assert m.rich is False
    assert m.can_handle is not None
