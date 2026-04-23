"""Tests for the Jobylon monitor (embed + can_handle)."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import httpx
import pytest

from src.core.monitors import BoardGoneError, DiscoveredJob
from src.core.monitors.jobylon import (
    _embed_url,
    _ids_from_url,
    _js_object_literal_to_json,
    _parse_job,
    _parse_jobs_block,
    _parse_localized_date,
    can_handle,
    discover,
)

FIXTURES = Path(__file__).parent / "fixtures"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _embed_html(jobs_literal: str, *, extra_head: str = "") -> str:
    """Wrap a jobs JS-object literal into a complete embed HTML shell.

    The real Jobylon embed contains ~60 unrelated scripts; we keep this
    shell minimal but realistic (angular directives, JBL.embed_v2
    namespace, ``JBL.embed_v2['jobs'] = [...];``).
    """
    return dedent(
        f"""\
        <!DOCTYPE html>
        <html lang="en">
        <head>{extra_head}</head>
        <body ng-app="embed_v2" ng-controller="JobListCtrl">
        <script>
            var JBL = JBL || {{}};
            JBL.embed_v2 = {{}};
            JBL.embed_v2['jobs'] = {jobs_literal};
        </script>
        </body>
        </html>
        """
    )


SAMPLE_JOBS_LITERAL = """[
    {
        id: '101',
        url: '/jobs/101-mcdonalds-sverige-job-one/',
        title: 'McDonald\\u0027s Store A s\\u00f6ker medarbetare',
        company: 'McDonald\\u2019s Sverige',
        company_id: '1955',
        klass: {
            'job-id-101': true,
            'job-lang-sv': true,
            'experience-id-1': true,
            'function-id-48': true,
            'internal': false,
        },
        locations: [
            'Stockholm',
            'Solna',
        ],
        locations_text: 'Stockholm / Solna',
        departments: [
            '001 \\u002D Central',
        ],
        workspace: 'Arbete p\\u00e5 plats',
        experience: 'Ing\\u00e5ngsniv\\u00e5',
        employment_type: 'Deltid',
        function: 'Restaurang \\u0026 Servering',
        language: 'Swedish',
        summary: 'None',
        to_date: '30 april 2026',
        published_date: '21 april 2026',
        is_internal: false
    },
    {
        id: '102',
        url: '/jobs/102-remote-engineer/',
        title: 'Remote Engineer',
        company: 'Example AB',
        company_id: '1955',
        klass: {
            'job-id-102': true,
            'job-lang-en': true,
        },
        locations: ['Remote'],
        locations_text: 'Remote',
        workspace: 'Remote',
        experience: 'Senior',
        employment_type: 'Heltid',
        function: 'Engineering',
        language: 'English',
        summary: 'None',
        to_date: '',
        published_date: 'April 21, 2026',
        is_internal: false
    }
]"""


# ---------------------------------------------------------------------------
# Pure-function helpers
# ---------------------------------------------------------------------------


class TestIdsFromUrl:
    def test_company(self):
        cid, gid = _ids_from_url("https://cdn.jobylon.com/jobs/companies/1955/embed/v2/")
        assert cid == "1955"
        assert gid is None

    def test_company_group(self):
        cid, gid = _ids_from_url("https://cdn.jobylon.com/jobs/company-groups/241/embed/v2/")
        assert cid is None
        assert gid == "241"

    def test_iframe_src_like(self):
        # ``iframe src="..."`` attribute body
        cid, gid = _ids_from_url('src="//cdn.jobylon.com/jobs/companies/9/embed/v2/"')
        assert cid == "9"
        assert gid is None

    def test_non_match(self):
        assert _ids_from_url("https://example.com/careers") == (None, None)


class TestEmbedUrl:
    def test_company(self):
        assert _embed_url("1955", None) == ("https://cdn.jobylon.com/jobs/companies/1955/embed/v2/")

    def test_group_wins(self):
        # Group id takes precedence over company id
        assert _embed_url("1955", "241") == (
            "https://cdn.jobylon.com/jobs/company-groups/241/embed/v2/"
        )

    def test_requires_id(self):
        with pytest.raises(ValueError):
            _embed_url(None, None)


class TestJsObjectLiteralToJson:
    def test_quotes_keys(self):
        src = "{id: '1', title: 'X'}"
        out = _js_object_literal_to_json(src)
        assert '"id":' in out
        assert '"title":' in out

    def test_preserves_booleans(self):
        src = "{a: true, b: false}"
        out = _js_object_literal_to_json(src)
        assert '"a":true' in out.replace(" ", "")
        assert '"b":false' in out.replace(" ", "")

    def test_decodes_escape_sequences(self):
        src = "{title: 'McDonald\\u0027s'}"
        out = _js_object_literal_to_json(src)
        # After JSON parsing, the string should contain an apostrophe
        import json

        parsed = json.loads(out)
        assert parsed["title"] == "McDonald's"


class TestParseLocalizedDate:
    def test_swedish(self):
        assert _parse_localized_date("21 april 2026") == "2026-04-21"

    def test_english(self):
        assert _parse_localized_date("April 21, 2026") == "2026-04-21"

    def test_danish_with_period(self):
        assert _parse_localized_date("20. april 2026") == "2026-04-20"

    def test_finnish(self):
        assert _parse_localized_date("21 huhtikuuta 2026") == "2026-04-21"

    def test_empty_returns_none(self):
        assert _parse_localized_date("") is None
        assert _parse_localized_date(None) is None

    def test_garbage_returns_none(self):
        assert _parse_localized_date("not a date") is None


class TestParseJobsBlock:
    def test_returns_list(self):
        html = _embed_html(SAMPLE_JOBS_LITERAL)
        jobs = _parse_jobs_block(html)
        assert len(jobs) == 2
        assert jobs[0]["id"] == "101"
        assert jobs[1]["id"] == "102"

    def test_missing_block(self):
        html = "<html><body>no embed here</body></html>"
        assert _parse_jobs_block(html) == []

    def test_apostrophe_decoded(self):
        html = _embed_html(SAMPLE_JOBS_LITERAL)
        jobs = _parse_jobs_block(html)
        assert "McDonald's" in jobs[0]["title"]

    def test_string_value_containing_close_bracket_semicolon(self):
        """B1: a job title with ``];`` inside a string must not truncate
        the array. The previous regex-only extraction (``\\[.*?\\];``)
        stopped at the first ``];`` — even inside a quoted value.
        """
        literal = (
            "["
            "{id: '1', title: 'lead with ];', url: '/jobs/1-x/'},"
            "{id: '2', title: 'second', url: '/jobs/2-y/'}"
            "]"
        )
        html = _embed_html(literal)
        jobs = _parse_jobs_block(html)
        assert len(jobs) == 2
        assert jobs[0]["title"] == "lead with ];"
        assert jobs[1]["id"] == "2"

    def test_string_value_containing_unquoted_key_pattern(self):
        """B2: a description containing ``{foo: bar}`` patterns must not
        get its interior keys quoted. Unquoted-key rewriting has to run
        *after* single→double-quote conversion so every ``foo:``
        candidate inside a string is already enclosed in double-quotes
        and thus ignored by the key regex.
        """
        literal = (
            "[{id: '1', url: '/jobs/1-x/', summary: 'config snippet: {host: localhost, port: 80}'}]"
        )
        html = _embed_html(literal)
        jobs = _parse_jobs_block(html)
        assert len(jobs) == 1
        assert jobs[0]["summary"] == "config snippet: {host: localhost, port: 80}"

    def test_unterminated_array_returns_empty(self):
        """Malformed embed (no closing ``]``) is logged and skipped,
        not a crash. Ensures ``_find_jobs_array_extent`` fails safely.
        """
        html = "<script>JBL.embed_v2['jobs'] = [{id: '1'</script>"
        assert _parse_jobs_block(html) == []


class TestParseJob:
    def test_basic_mapping(self):
        raw = {
            "id": "101",
            "url": "/jobs/101-foo/",
            "title": "A role",
            "company_id": "1955",
            "company": "ACME",
            "klass": {"job-lang-sv": True, "job-id-101": True},
            "locations": ["Stockholm"],
            "departments": ["Central"],
            "workspace": "Remote",
            "function": "Engineering",
            "experience": "Senior",
            "employment_type": "Full-time",
            "published_date": "21 april 2026",
            "to_date": "30 april 2026",
        }
        job = _parse_job(raw)
        assert isinstance(job, DiscoveredJob)
        assert job.url == "https://emp.jobylon.com/jobs/101-foo/"
        assert job.title == "A role"
        assert job.language == "sv"
        assert job.locations == ["Stockholm"]
        assert job.date_posted == "2026-04-21"
        assert job.job_location_type == "TELECOMMUTE"
        assert job.metadata is not None
        assert job.metadata["id"] == "101"
        assert job.metadata["company_id"] == "1955"
        assert job.metadata["company"] == "ACME"
        assert job.metadata["function"] == "Engineering"
        assert job.metadata["experience"] == "Senior"
        assert job.metadata["employment_type_label"] == "Full-time"
        assert job.metadata["workspace"] == "Remote"
        assert job.metadata["departments"] == ["Central"]
        assert job.metadata["to_date"] == "30 april 2026"
        assert job.metadata["published_date_raw"] == "21 april 2026"

    def test_falls_back_to_locations_text(self):
        raw = {
            "id": "9",
            "url": "/jobs/9/",
            "title": "T",
            "locations_text": "Lund",
        }
        job = _parse_job(raw)
        assert job is not None
        assert job.locations == ["Lund"]

    def test_skips_none_summary(self):
        raw = {
            "id": "9",
            "url": "/jobs/9/",
            "title": "T",
            "function": "None",
        }
        job = _parse_job(raw)
        assert job is not None
        assert "function" not in (job.metadata or {})

    def test_missing_id_or_url(self):
        assert _parse_job({"url": "/jobs/9/"}) is None
        assert _parse_job({"id": "9"}) is None

    def test_absolute_url_passthrough(self):
        raw = {"id": "9", "url": "https://emp.jobylon.com/jobs/9-foo/"}
        job = _parse_job(raw)
        assert job is not None
        assert job.url == "https://emp.jobylon.com/jobs/9-foo/"


# ---------------------------------------------------------------------------
# discover()
# ---------------------------------------------------------------------------


def _transport_for(html: str, *, status: int = 200):
    def handler(request):
        if status == 404:
            return httpx.Response(404, text=html)
        return httpx.Response(status, text=html)

    return httpx.MockTransport(handler)


class TestDiscover:
    async def test_basic(self):
        html = _embed_html(SAMPLE_JOBS_LITERAL)
        board = {
            "board_url": "https://mcdonalds.example/",
            "metadata": {"company_id": "1955"},
        }
        async with httpx.AsyncClient(transport=_transport_for(html)) as client:
            jobs = await discover(board, client)
        assert len(jobs) == 2
        assert all(isinstance(j, DiscoveredJob) for j in jobs)
        assert jobs[0].url.startswith("https://emp.jobylon.com/jobs/")

    async def test_group_id_routed_to_group_endpoint(self):
        captured_urls: list[str] = []

        def handler(request):
            captured_urls.append(str(request.url))
            return httpx.Response(200, text=_embed_html("[]"))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://mcdonalds.example/",
                "metadata": {"company_group_id": "241"},
            }
            await discover(board, client)
        assert captured_urls
        assert "company-groups/241" in captured_urls[0]

    async def test_missing_ids_raises(self):
        async with httpx.AsyncClient(transport=_transport_for("")) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {},
            }
            with pytest.raises(ValueError, match="company_id"):
                await discover(board, client)

    async def test_ids_derived_from_board_url(self):
        html = _embed_html(SAMPLE_JOBS_LITERAL)
        board = {
            "board_url": "https://cdn.jobylon.com/jobs/companies/1955/embed/v2/",
            "metadata": {},
        }
        async with httpx.AsyncClient(transport=_transport_for(html)) as client:
            jobs = await discover(board, client)
        assert len(jobs) == 2

    async def test_404_raises_board_gone(self):
        async with httpx.AsyncClient(transport=_transport_for("", status=404)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {"company_id": "99999999"},
            }
            with pytest.raises(BoardGoneError):
                await discover(board, client)

    async def test_empty_jobs_block(self):
        html = _embed_html("[]")
        board = {
            "board_url": "https://mcdonalds.example/",
            "metadata": {"company_id": "1955"},
        }
        async with httpx.AsyncClient(transport=_transport_for(html)) as client:
            jobs = await discover(board, client)
        assert jobs == []

    async def test_dedupes_by_url(self):
        dup_literal = (
            "[" + ",".join([SAMPLE_JOBS_LITERAL.strip().lstrip("[").rstrip("]")] * 2) + "]"
        )
        html = _embed_html(dup_literal)
        board = {
            "board_url": "https://mcdonalds.example/",
            "metadata": {"company_id": "1955"},
        }
        async with httpx.AsyncClient(transport=_transport_for(html)) as client:
            jobs = await discover(board, client)
        # Duplicates collapse to 2 distinct URLs
        assert len({j.url for j in jobs}) == 2


# ---------------------------------------------------------------------------
# can_handle()
# ---------------------------------------------------------------------------


class TestCanHandle:
    async def test_direct_company_url_no_client(self):
        result = await can_handle("https://cdn.jobylon.com/jobs/companies/1955/embed/v2/")
        assert result == {"company_id": "1955"}

    async def test_direct_group_url_with_probe(self):
        html = _embed_html(SAMPLE_JOBS_LITERAL)
        async with httpx.AsyncClient(transport=_transport_for(html)) as client:
            result = await can_handle(
                "https://cdn.jobylon.com/jobs/company-groups/241/embed/v2/",
                client,
            )
        assert result is not None
        assert result["company_group_id"] == "241"
        assert result["jobs"] == 2

    async def test_probe_returns_marker_without_count_on_404(self):
        async with httpx.AsyncClient(transport=_transport_for("", status=404)) as client:
            result = await can_handle(
                "https://cdn.jobylon.com/jobs/companies/1955/embed/v2/",
                client,
            )
        assert result == {"company_id": "1955"}

    async def test_page_scan_finds_iframe(self):
        host_page = (
            "<html><body>"
            '<iframe src="https://cdn.jobylon.com/jobs/companies/1955/embed/v2/">'
            "</iframe></body></html>"
        )
        embed_html = _embed_html(SAMPLE_JOBS_LITERAL)

        def handler(request):
            host = str(request.url)
            if "cdn.jobylon.com" in host:
                return httpx.Response(200, text=embed_html)
            return httpx.Response(200, text=host_page)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://mcdonalds.example/careers", client)
        assert result is not None
        assert result["company_id"] == "1955"
        assert result["jobs"] == 2

    async def test_non_jobylon_url_no_client(self):
        assert await can_handle("https://example.com/careers") is None

    async def test_no_marker_returns_none(self):
        def handler(request):
            return httpx.Response(200, text="<html><body>no jobylon refs</body></html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
        assert result is None


# ---------------------------------------------------------------------------
# Registry / compat parity
# ---------------------------------------------------------------------------


class TestRegistryParity:
    def test_registered_in_core(self):
        from src.core.monitors import _REGISTRY

        names = {m.name for m in _REGISTRY}
        assert "jobylon" in names

    def test_listed_in_compat(self):
        from src.workspace._compat import all_monitor_types, api_monitor_types

        assert "jobylon" in all_monitor_types()
        assert "jobylon" in api_monitor_types()

    def test_help_card_present(self):
        from src.workspace.commands.help import MONITOR_CARDS

        assert "jobylon" in MONITOR_CARDS
        card = MONITOR_CARDS["jobylon"]
        assert "cdn.jobylon.com" in card

    def test_detect_ats_from_url(self):
        from src.workspace._compat import detect_ats_from_url

        assert detect_ats_from_url("https://cdn.jobylon.com/jobs/companies/1955/embed/v2/") == (
            "jobylon"
        )
        assert detect_ats_from_url("https://emp.jobylon.com/jobs/1/") == "jobylon"


# ---------------------------------------------------------------------------
# Workspace / remote detection (substring-based)
# ---------------------------------------------------------------------------


class TestWorkspaceRemoteDetection:
    """Verify ``workspace`` strings map to the correct ``job_location_type``.

    Jobylon embeds occasionally wrap the raw label with city qualifiers
    (``Remote (Stockholm)``) or national synonyms (``Arbete distans``,
    ``fjernarbejde``, ``etätyö``).  An exact-match check missed these;
    substring detection is what the production data actually requires.
    """

    @pytest.mark.parametrize(
        "workspace",
        [
            "Remote",
            "REMOTE",
            "Remote (Stockholm)",
            "Fully remote",
            "Remote work",
            "Distans",
            "Arbete distans",
            "Distansarbete",
            "Etätyö",
            "etätyö kotoa",
            "Hjemmefra",
            "Jobb hjemmefra",
            "Fjernarbejde",
            "fjernarbejde fra hjemmet",
        ],
    )
    def test_remote_variants_classify_as_telecommute(self, workspace: str):
        job = _parse_job({"id": "1", "url": "/jobs/1/", "workspace": workspace})
        assert job is not None
        assert job.job_location_type == "TELECOMMUTE", (
            f"workspace={workspace!r} should map to TELECOMMUTE"
        )

    @pytest.mark.parametrize(
        "workspace",
        [
            "Hybrid",
            "Hybrid (Copenhagen)",
            "Hybrid - Oslo",
            "HYBRID",
        ],
    )
    def test_hybrid_variants_unset_location_type(self, workspace: str):
        # Hybrid leaves ``job_location_type`` unset (schema.org has no
        # HYBRID code) but the raw label is preserved in metadata for
        # downstream normalization.
        job = _parse_job({"id": "1", "url": "/jobs/1/", "workspace": workspace})
        assert job is not None
        assert job.job_location_type is None
        assert job.metadata is not None
        assert job.metadata["workspace"] == workspace

    @pytest.mark.parametrize(
        "workspace",
        [
            "On-site",
            "Onsite",
            "Arbete på plats",
            "Lähityö",
        ],
    )
    def test_onsite_variants_leave_none(self, workspace: str):
        job = _parse_job({"id": "1", "url": "/jobs/1/", "workspace": workspace})
        assert job is not None
        assert job.job_location_type is None


# ---------------------------------------------------------------------------
# Tolerant locations parsing
# ---------------------------------------------------------------------------


class TestParseLocationsTolerance:
    """The embed is authored by hand — ``locations`` shows up as list,
    dict, int, or missing.  The parser must not raise on any of these.
    """

    def test_list_of_strings(self):
        job = _parse_job(
            {
                "id": "1",
                "url": "/jobs/1/",
                "locations": ["Stockholm", "Solna"],
            }
        )
        assert job is not None
        assert job.locations == ["Stockholm", "Solna"]

    def test_dict_falls_back_to_locations_text(self):
        # Non-list ``locations`` (e.g. dict from an older widget schema)
        # is ignored; ``locations_text`` is used as fallback.
        job = _parse_job(
            {
                "id": "1",
                "url": "/jobs/1/",
                "locations": {"city": "Stockholm"},
                "locations_text": "Stockholm",
            }
        )
        assert job is not None
        assert job.locations == ["Stockholm"]

    def test_none_locations_uses_fallback(self):
        job = _parse_job(
            {
                "id": "1",
                "url": "/jobs/1/",
                "locations": None,
                "locations_text": "Oslo",
            }
        )
        assert job is not None
        assert job.locations == ["Oslo"]

    def test_int_locations_no_fallback_returns_none(self):
        # Pathological data (e.g. a numeric id where a list should be) +
        # no locations_text → ``locations`` is None, nothing crashes.
        job = _parse_job({"id": "1", "url": "/jobs/1/", "locations": 42})
        assert job is not None
        assert job.locations is None

    def test_empty_list_falls_back_to_text(self):
        job = _parse_job(
            {
                "id": "1",
                "url": "/jobs/1/",
                "locations": [],
                "locations_text": "Malmö",
            }
        )
        assert job is not None
        assert job.locations == ["Malmö"]


# ---------------------------------------------------------------------------
# Transient errors (5xx)
# ---------------------------------------------------------------------------


class TestTransientErrors:
    """``_fetch_embed`` maps 404 → BoardGoneError (permanent); any
    other non-2xx propagates as :class:`httpx.HTTPStatusError` so the
    worker treats it as retriable rather than a dead board.
    """

    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    async def test_5xx_propagates_as_http_status_error(self, status: int):
        async with httpx.AsyncClient(transport=_transport_for("", status=status)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {"company_id": "1955"},
            }
            with pytest.raises(httpx.HTTPStatusError):
                await discover(board, client)

    async def test_429_propagates_as_http_status_error(self):
        async with httpx.AsyncClient(transport=_transport_for("", status=429)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {"company_id": "1955"},
            }
            with pytest.raises(httpx.HTTPStatusError):
                await discover(board, client)

    async def test_transport_error_propagates(self):
        # Transport errors (ConnectError, ReadTimeout, etc.) must
        # propagate as retriable, NOT map to BoardGoneError. A
        # Cloudflare hiccup or a Jobylon outage would otherwise flip a
        # live board to "gone" and trigger spurious delistings.
        def handler(request):
            raise httpx.ConnectError("boom")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {"company_id": "1955"},
            }
            with pytest.raises(httpx.ConnectError):
                await discover(board, client)


# ---------------------------------------------------------------------------
# Real-embed fixture (McDonald's SE)
# ---------------------------------------------------------------------------


class TestRealEmbedFixture:
    """Smoke-test the parser against a real Jobylon widget capture.

    The fixture was fetched from
    ``https://cdn.jobylon.com/jobs/companies/1955/embed/v2/?page_size=1000``
    (McDonald's Sverige) on 2026-04-22.  It exercises the JS→JSON
    translation, date/locale decoding, and ``workspace`` labels against
    live markup rather than hand-crafted shells.
    """

    def _html(self) -> str:
        path = FIXTURES / "jobylon_mcdonalds_se.html"
        return path.read_text(encoding="utf-8")

    def test_fixture_exists(self):
        path = FIXTURES / "jobylon_mcdonalds_se.html"
        assert path.exists(), f"missing fixture: {path}"
        assert path.stat().st_size > 10_000

    def test_parse_jobs_block_returns_many(self):
        jobs = _parse_jobs_block(self._html())
        # McDonald's SE has ~120 postings on a typical day; keep the
        # lower bound loose so seasonal dips don't break the suite.
        assert len(jobs) >= 20
        assert all("id" in j and "url" in j for j in jobs)

    def test_parse_job_produces_discovered_jobs(self):
        jobs = _parse_jobs_block(self._html())
        parsed = [_parse_job(j) for j in jobs]
        parsed_non_null = [p for p in parsed if p is not None]
        assert len(parsed_non_null) == len(jobs)
        # Every job has an absolute emp.jobylon.com URL.
        assert all(p.url.startswith("https://emp.jobylon.com/jobs/") for p in parsed_non_null)
        # Every job carries a string title.
        assert all(isinstance(p.title, str) and p.title for p in parsed_non_null)
        # Language maps from klass → sv for a Swedish company.
        assert any(p.language == "sv" for p in parsed_non_null)

    async def test_discover_end_to_end_with_fixture(self):
        html = self._html()

        def handler(request):
            return httpx.Response(200, text=html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://cdn.jobylon.com/jobs/companies/1955/embed/v2/",
                "metadata": {"company_id": "1955"},
            }
            jobs = await discover(board, client)
        assert len(jobs) >= 20
        # Dedupe invariant holds.
        assert len({j.url for j in jobs}) == len(jobs)


# ---------------------------------------------------------------------------
# Host allowlist (narrow can_handle)
# ---------------------------------------------------------------------------


class TestHostAllowlist:
    """The ``can_handle`` short-circuit must only trigger on known
    first-party Jobylon hosts.  An attacker-controlled subdomain like
    ``blog.jobylon.com`` or a look-alike like ``jobylon.com.evil.org``
    must not be treated as a direct embed URL.
    """

    async def test_known_host_cdn(self):
        result = await can_handle("https://cdn.jobylon.com/jobs/companies/1955/embed/v2/")
        assert result == {"company_id": "1955"}

    async def test_known_host_emp(self):
        # emp.jobylon.com is in the allowlist but the URL shape has no
        # embed id -> no direct match; ``can_handle`` with no client
        # short-circuits to None.
        result = await can_handle("https://emp.jobylon.com/jobs/12345-foo/")
        assert result is None

    async def test_unknown_subdomain_rejected(self):
        # ``blog.jobylon.com`` and ``admin.jobylon.com`` are not in the
        # allowlist — even though the old code's
        # ``endswith('.jobylon.com')`` would have accepted them.
        assert await can_handle("https://blog.jobylon.com/jobs/companies/1955/embed/v2/") is None
        assert (
            await can_handle("https://admin.jobylon.com/jobs/company-groups/241/embed/v2/") is None
        )

    async def test_lookalike_host_rejected(self):
        # Classic suffix-confusion attacks.
        assert (
            await can_handle("https://cdn.jobylon.com.attacker.example/jobs/companies/1/embed/v2/")
            is None
        )
