from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import httpx
import pytest

from src.core.monitors.dom import dom_discover
from src.core.scrapers.pdf import (
    _download_verified_fingerprinted_pdf,
    _extract_named_fields,
    _extract_pattern,
    _location_from_url,
    _normalize_captured_text,
    _ocr_pdf,
    _text_to_html,
    _title_from_text,
    _title_from_url,
    can_handle,
    parse_bytes,
    scrape,
)
from src.shared.response_fingerprint import (
    ResponseFingerprintValidators,
    build_response_fingerprint_url,
)
from src.shared.tdm import TDMReservedError


def _make_pdf(text: str) -> bytes:
    """Create a minimal valid PDF with the given text content."""
    import pypdf
    from pypdf.generic import (
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
    )

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)

    page = writer.pages[0]

    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())

    font_dict = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    resources = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_dict})}
    )
    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = writer._add_object(stream)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _fingerprinted_pdf_url(
    source_url: str,
    content: bytes,
    *,
    etag: str = '"0123456789abcdef"',
) -> tuple[str, dict[str, str]]:
    validators = ResponseFingerprintValidators(
        etag=etag,
        last_modified="2026-07-23T14:06:14+00:00",
        content_length=len(content),
        content_type="application/pdf",
    )
    url = build_response_fingerprint_url(source_url, validators)
    headers = {
        "content-type": "application/pdf",
        "etag": etag,
        "last-modified": "Thu, 23 Jul 2026 14:06:14 GMT",
        "content-length": str(len(content)),
    }
    return url, headers


def _board_scraper_config(board_slug: str) -> dict:
    boards_path = Path(__file__).resolve().parents[1] / "data" / "boards.csv"
    with boards_path.open(newline="", encoding="utf-8") as handle:
        row = next(row for row in csv.DictReader(handle) if row["board_slug"] == board_slug)
    return json.loads(row["scraper_config"])


def _icj_scraper_config() -> dict:
    return _board_scraper_config("international-commission-of-jurists-careers")


class TestTitleFromUrl:
    def test_simple_filename(self):
        url = "https://example.com/files/Job_Description_Engineer.pdf"
        assert _title_from_url(url) == "Job Description Engineer"

    def test_webflow_hash_prefix(self):
        url = "https://cdn.example.com/628b7a4e032635973ac7105e/69aee02861955031e857b52c_Junior%20busdev.pdf"
        assert _title_from_url(url) == "Junior busdev"

    def test_no_extension(self):
        url = "https://example.com/files/job"
        assert _title_from_url(url) == "job"

    def test_empty_after_strip(self):
        url = "https://example.com/.pdf"
        assert _title_from_url(url) is None

    def test_url_encoded(self):
        url = "https://example.com/Senior%20BusDev_LinkedIN%20(1).pdf"
        result = _title_from_url(url)
        assert "Senior BusDev" in result

    def test_with_pattern(self):
        url = "https://example.com/69aee02861955031e857b52c_Company%20jobs%20-%20Engineer.pdf"
        result = _title_from_url(url, pattern=r"-\s*(.+)$")
        assert result == "Engineer"

    def test_pattern_normalizes_filename_separators(self):
        url = "https://example.com/03_2025_Senior-QA-Engineer_DE.pdf"
        result = _title_from_url(url, pattern=r"^\d{2}_\d{4}_(.+?)_DE$")
        assert result == "Senior QA Engineer"

    def test_pattern_no_match_returns_full(self):
        url = "https://example.com/Engineer.pdf"
        result = _title_from_url(url, pattern=r"NOMATCH_(\w+)")
        assert result == "Engineer"


class TestLocationFromUrl:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            (
                "https://example.com/Faculty%3F-Vacancy-Singapore.pdf?download=1#current",
                "Singapore",
            ),
            (
                "https://example.com/Faculty%23-Vacancy-Lausanne.pdf?download=1#current",
                "Lausanne",
            ),
        ],
    )
    def test_decodes_reserved_filename_characters_after_parsing_url(self, url, expected):
        assert _location_from_url(url, r"-(Lausanne|Singapore)\.pdf$") == expected


class TestInternationalBoxingAssociationPdfConfig:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            (
                "2024-Job-Vacancy-Administrative-Assistant.pdf",
                "Administrative Assistant",
            ),
            (
                "2024-Job-Vacancy-International-Relations-Director-of-IBA-2.pdf",
                "International Relations Director of IBA",
            ),
        ],
    )
    def test_historical_wordpress_filenames_produce_clean_titles(self, filename, expected):
        config = _board_scraper_config("international-boxing-association-careers")

        assert (
            _title_from_url(
                f"https://www.iba.sport/wp-content/uploads/2024/01/{filename}",
                config["title_pattern"],
            )
            == expected
        )


class TestTitleFromText:
    def test_first_line(self):
        assert _title_from_text("Software Engineer\nGreat role") == "Software Engineer"

    def test_skips_bullets(self):
        assert _title_from_text("•First bullet\nReal Title") == "Real Title"

    def test_skips_lowercase(self):
        assert _title_from_text("continued sentence\nReal Title") == "Real Title"

    def test_skips_very_long(self):
        long_line = "A" * 150
        assert _title_from_text(long_line) is None

    def test_returns_none_when_no_candidate(self):
        assert _title_from_text("•bullet\n•bullet\n•bullet") is None

    def test_empty_text(self):
        assert _title_from_text("") is None


class TestCapturedText:
    def test_collapses_layout_whitespace(self):
        value = " Senior \n Battery Pack \n Manufacturing Engineer "
        assert _normalize_captured_text(value) == "Senior Battery Pack Manufacturing Engineer"

    def test_rejoins_hyphenated_line_break(self):
        assert _normalize_captured_text("large-\n scale transition") == "large-scale transition"

    def test_rejoins_split_capitalized_word(self):
        assert (
            _normalize_captured_text("M\nechanical Engineer", repair_split_initial=True)
            == "Mechanical Engineer"
        )

    def test_rejoins_horizontally_split_capitalized_word(self):
        assert (
            _normalize_captured_text("S enior Structural Expert", repair_split_initial=True)
            == "Senior Structural Expert"
        )

    def test_rejoins_split_capitalized_word_with_accent(self):
        assert (
            _normalize_captured_text("R égleur / R égleuse", repair_split_initial=True)
            == "Régleur / Régleuse"
        )

    def test_does_not_rejoin_ambiguous_initial_by_default(self):
        assert _normalize_captured_text("A\nrole") == "A role"

    def test_extracts_and_normalizes_capture_group(self):
        text = "Location\n Sawston,\n Cambridge\nWho we are"
        pattern = r"(?s)Location\s*(.*?)\s*Who we are"
        assert _extract_pattern(text, pattern) == "Sawston, Cambridge"

    def test_extracts_named_fields_from_table_layout(self):
        text = "Role Location\nProject Manager\nTokyo\nSalary"
        pattern = (
            r"(?s)Role\s+Location\s+(?P<title>.+?)\s+"
            r"(?P<location>Tokyo.*?)\s+Salary"
        )
        assert _extract_named_fields(text, pattern) == {
            "title": "Project Manager",
            "location": "Tokyo",
        }

    def test_extracts_only_configured_named_field(self):
        assert _extract_named_fields(
            "Role\nProject Manager\nSalary",
            r"(?s)Role\s+(?P<title>.+?)\s+Salary",
        ) == {"title": "Project Manager"}


class TestTextToHtml:
    def test_single_paragraph(self):
        assert _text_to_html("hello world") == "<p>hello world</p>"

    def test_multiple_paragraphs(self):
        text = "First paragraph.\n\nSecond paragraph."
        result = _text_to_html(text)
        assert "<p>First paragraph.</p>" in result
        assert "<p>Second paragraph.</p>" in result

    def test_empty_text(self):
        assert _text_to_html("") == ""

    def test_whitespace_only(self):
        assert _text_to_html("   \n\n   ") == ""

    def test_wrapped_lines(self):
        text = "Line one\nstill paragraph one.\n\nNew paragraph."
        result = _text_to_html(text)
        assert "<p>Line one still paragraph one.</p>" in result
        assert "<p>New paragraph.</p>" in result


class TestCanHandle:
    def test_detects_pdf(self):
        assert can_handle(["%PDF-1.4 rest of pdf"]) == {}

    def test_detects_majority_pdf(self):
        assert can_handle(["%PDF-1.4", "%PDF-1.7", "<html>"]) == {}

    def test_rejects_html(self):
        assert can_handle(["<html>", "<html>"]) is None

    def test_rejects_empty(self):
        assert can_handle([]) is None

    def test_whitespace_before_magic(self):
        assert can_handle(["  %PDF-1.4"]) == {}


class TestScrape:
    async def test_extracts_text_from_pdf(self):
        pdf_bytes = _make_pdf("Software Engineer")

        def handler(request):
            return httpx.Response(200, content=pdf_bytes)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/job.pdf", {}, client)
            assert result.title is not None
            assert result.description is not None

    async def test_request_headers_override_client_defaults(self):
        pdf_bytes = _make_pdf("Junior Science Project Coordinator")

        def handler(request):
            assert request.headers["user-agent"] == "jobseek-crawler (+https://jseek.co/)"
            assert request.headers["accept"] == "application/pdf"
            return httpx.Response(200, content=pdf_bytes, request=request)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"User-Agent": "blocked-browser-agent"},
        ) as client:
            result = await scrape(
                "https://example.com/job.pdf",
                {
                    "title_source": "text",
                    "request_headers": {
                        "User-Agent": "jobseek-crawler (+https://jseek.co/)",
                        "Accept": "application/pdf",
                    },
                },
                client,
            )

        assert result.title == "Junior Science Project Coordinator"

    async def test_request_headers_must_map_strings_to_strings(self):
        transport = httpx.MockTransport(lambda _request: None)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="request_headers must map strings to strings"):
                await scrape(
                    "https://example.com/job.pdf",
                    {"request_headers": {"Accept": 123}},
                    client,
                )

    @pytest.mark.parametrize("header", ["Authorization", "Cookie", "Connection"])
    async def test_request_headers_reject_secrets_and_transport_headers(self, header):
        transport = httpx.MockTransport(lambda _request: None)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="unsafe header"):
                await scrape(
                    "https://example.com/job.pdf",
                    {"request_headers": {header: "secret"}},
                    client,
                )

    async def test_request_headers_refuse_cross_origin_redirect(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.host != "example.com":
                raise AssertionError("cross-origin redirect must not be requested")
            return httpx.Response(
                302,
                headers={"Location": "https://attacker.example/job.pdf"},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="cross-origin redirect"):
                await scrape(
                    "https://example.com/job.pdf",
                    {"request_headers": {"User-Agent": "jobseek-crawler"}},
                    client,
                )

        assert [str(request.url) for request in requests] == ["https://example.com/job.pdf"]

    async def test_fingerprinted_pdf_binds_get_validators_to_identity(self):
        pdf_bytes = _make_pdf("Verified Engineer")
        url, headers = _fingerprinted_pdf_url(
            "https://assets.example.com/job.pdf?download=1",
            pdf_bytes,
        )
        requests: list[httpx.Request] = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, headers=headers, content=pdf_bytes, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(url, {"title_source": "text"}, client)

        assert result.title == "Verified Engineer"
        assert len(requests) == 1
        assert requests[0].method == "GET"
        assert requests[0].url.params.get("download") == "1"
        assert requests[0].url.params.get("_jobseek_fp") is not None

    async def test_configured_fiba_monitor_identity_is_verified_by_pdf_scraper(self):
        with Path("data/boards.csv").open() as file:
            board = next(
                row
                for row in csv.DictReader(file)
                if row["board_slug"] == "international-basketball-federation-hq"
            )
        document_url = "https://assets.fiba.basketball/image/upload/v1/job-description.pdf"
        listing = (
            "<article data-testid='rich-text-entries'>"
            "<h2 id='h-job_openings_at_fiba'>Jobs</h2>"
            f"<ul><li><a href='{document_url}'>FIBA Engineer</a></li></ul>"
            "</article>"
        )
        pdf_bytes = _make_pdf("FIBA Engineer")
        _, headers = _fingerprinted_pdf_url(document_url, pdf_bytes)

        def handler(request):
            if str(request.url) == board["board_url"]:
                return httpx.Response(200, text=listing, request=request)
            if request.method == "HEAD":
                return httpx.Response(200, headers=headers, request=request)
            return httpx.Response(200, headers=headers, content=pdf_bytes, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            discovered = await dom_discover(
                {
                    "board_url": board["board_url"],
                    "metadata": json.loads(board["monitor_config"]),
                },
                client,
            )
            assert isinstance(discovered, set)
            assert len(discovered) == 1
            result = await scrape(
                next(iter(discovered)),
                json.loads(board["scraper_config"]),
                client,
            )

        assert result.title == "FIBA Engineer"

    async def test_fingerprinted_pdf_rejects_changed_get_validators(self):
        pdf_bytes = _make_pdf("Changed Engineer")
        url, headers = _fingerprinted_pdf_url(
            "https://assets.example.com/job.pdf",
            pdf_bytes,
        )
        headers["etag"] = '"fedcba9876543210"'

        def handler(request):
            return httpx.Response(200, headers=headers, content=pdf_bytes, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="no longer match"):
                await scrape(url, {}, client)

    async def test_fingerprinted_pdf_checks_tdm_on_get(self):
        pdf_bytes = _make_pdf("Reserved Engineer")
        url, headers = _fingerprinted_pdf_url(
            "https://assets.example.com/job.pdf",
            pdf_bytes,
        )
        headers["tdm-reservation"] = "1"

        def handler(request):
            return httpx.Response(200, headers=headers, content=pdf_bytes, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(TDMReservedError):
                await scrape(url, {}, client)

    async def test_fingerprinted_pdf_rejects_cross_origin_redirect_before_following(self):
        pdf_bytes = _make_pdf("Redirected Engineer")
        url, _ = _fingerprinted_pdf_url(
            "https://assets.example.com/job.pdf",
            pdf_bytes,
        )
        requests: list[httpx.Request] = []

        def handler(request):
            requests.append(request)
            return httpx.Response(
                302,
                headers={"location": "https://other.example/job.pdf"},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="cross-origin redirect"):
                await scrape(url, {}, client)

        assert [request.url.host for request in requests] == ["assets.example.com"]

    async def test_fingerprinted_pdf_rejects_body_length_mismatch(self):
        pdf_bytes = _make_pdf("Truncated Engineer")
        url, headers = _fingerprinted_pdf_url(
            "https://assets.example.com/job.pdf",
            pdf_bytes,
        )
        truncated = pdf_bytes[:-1]

        def handler(request):
            return httpx.Response(200, headers=headers, content=truncated, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises((ValueError, httpx.RemoteProtocolError)):
                await _download_verified_fingerprinted_pdf(url, client)

    async def test_default_title_from_url(self):
        """Default title_source='url' uses the filename."""
        pdf_bytes = _make_pdf("Some PDF text here")

        def handler(request):
            return httpx.Response(200, content=pdf_bytes)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/Marketing_Intern.pdf", {}, client)
            assert result.title == "Marketing Intern"

    async def test_title_source_text(self):
        """title_source='text' extracts from PDF content."""
        pdf_bytes = _make_pdf("Senior Engineer")

        def handler(request):
            return httpx.Response(200, content=pdf_bytes)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://example.com/job.pdf",
                {"title_source": "text"},
                client,
            )
            assert result.title == "Senior Engineer"

    @pytest.mark.parametrize(
        ("text", "expected_title"),
        [
            (
                "Medizinische Fakultät\nInstitut für Pathologie\n"
                "Zur Unterstützung unseres Teams suchen wir per sofort oder nach "
                "Vereinbarung eine:n\n"
                "Teamleiter:in Administration Management 80 - 100%\n\nIhre Aufgaben\n"
                "Fachliche Führung",
                "Teamleiter:in Administration Management 80 - 100%",
            ),
            (
                "Institut für Pathologie\nZur Unterstützung unseres Ärzteteams suchen wir "
                "per sofort oder nach Vereinbarung eine:n\n"
                "Oberaerztin/Oberarzt in der Klinischen Pathologie 60-100%\n\n"
                "Ihre Aufgaben\nDiagnostische Tätigkeit",
                "Oberaerztin/Oberarzt in der Klinischen Pathologie 60-100%",
            ),
            (
                "Institut für Pathologie\nZur Ergänzung unseres Ärzteteams auf der Abteilung "
                "Klinische Pathologie suchen wir per sofort oder nach Vereinbarung eine:n\n\n"
                "Facharzt/-aerztin FMH Pathologie:\nWeiterbildungsstelle zur Erlangung des "
                "Schwerpunkts\nZytopathologie 80-100%\n\nDiese Position richtet sich an "
                "Ärztinnen und Ärzte",
                (
                    "Facharzt/-aerztin FMH Pathologie: Weiterbildungsstelle zur Erlangung "
                    "des Schwerpunkts Zytopathologie 80-100%"
                ),
            ),
        ],
    )
    async def test_title_source_text_prefers_german_recruiting_lead_in(
        self,
        text,
        expected_title,
    ):
        pdf_bytes = _make_pdf(text)

        def handler(request):
            return httpx.Response(200, content=pdf_bytes)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://example.com/vacancy.pdf",
                {"title_source": "text"},
                client,
            )

        assert result.title == expected_title

    async def test_empty_pdf_falls_back_to_url_title(self):
        """When PDF text extraction yields nothing, title comes from URL."""
        import pypdf

        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=612, height=792)
        buf = io.BytesIO()
        writer.write(buf)
        empty_pdf = buf.getvalue()

        def handler(request):
            return httpx.Response(200, content=empty_pdf)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/Marketing_Intern.pdf", {}, client)
            assert result.title == "Marketing Intern"

    async def test_empty_pdf_can_fall_back_to_url_location(self):
        """Filename location fallback also applies when the PDF has no text."""
        import pypdf

        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=612, height=792)
        buf = io.BytesIO()
        writer.write(buf)

        def handler(request):
            return httpx.Response(200, content=buf.getvalue())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://example.com/Faculty-Vacancy-Finance-Lausanne.pdf",
                {"location_url_pattern": r"-(Lausanne|Singapore)\.pdf$"},
                client,
            )

        assert result.locations == ["Lausanne"]

    async def test_empty_pdf_can_use_opt_in_ocr(self, monkeypatch):
        import pypdf

        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=612, height=792)
        buf = io.BytesIO()
        writer.write(buf)

        def handler(request):
            return httpx.Response(200, content=buf.getvalue())

        def fake_ocr(content: bytes, *, languages: str, scale: int) -> str:
            assert content == buf.getvalue()
            assert languages == "deu+eng"
            assert scale == 3
            return (
                "Location: Neuhausen am Rheinfall, CH\n"
                "DIRECTOR PEOPLE & CULTURE\n\nRole description"
            )

        monkeypatch.setattr("src.core.scrapers.pdf._ocr_pdf", fake_ocr)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://example.com/request.pdf",
                {
                    "ocr": True,
                    "ocr_languages": "deu+eng",
                    "ocr_scale": 3,
                    "title_source": "text",
                    "title_pattern": r"(DIRECTOR PEOPLE & CULTURE)",
                    "location_pattern": r"Location:\s*([^\n]+)",
                },
                client,
            )

        assert result.title == "DIRECTOR PEOPLE & CULTURE"
        assert result.locations == ["Neuhausen am Rheinfall, CH"]
        assert result.description is not None
        assert "Role description" in result.description

    @pytest.mark.parametrize("scale", [0, 5, "not-a-number"])
    async def test_ocr_rejects_unbounded_render_scale(self, scale):
        import pypdf

        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=612, height=792)
        buf = io.BytesIO()
        writer.write(buf)

        def handler(request):
            return httpx.Response(200, content=buf.getvalue())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="ocr_scale"):
                await scrape(
                    "https://example.com/request.pdf",
                    {"ocr": True, "ocr_scale": scale},
                    client,
                )

    async def test_ocr_rejects_invalid_language_expression(self):
        import pypdf

        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=612, height=792)
        buf = io.BytesIO()
        writer.write(buf)

        def handler(request):
            return httpx.Response(200, content=buf.getvalue())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="ocr_languages"):
                await scrape(
                    "https://example.com/request.pdf",
                    {"ocr": True, "ocr_languages": "deu; rm -rf /"},
                    client,
                )

    def test_ocr_rejects_excessive_page_count(self):
        import pypdf

        writer = pypdf.PdfWriter()
        for _ in range(21):
            writer.add_blank_page(width=612, height=792)
        buf = io.BytesIO()
        writer.write(buf)

        with pytest.raises(ValueError, match="limited to 20 pages"):
            _ocr_pdf(buf.getvalue(), languages="eng", scale=2)

    def test_ocr_rejects_excessive_render_dimensions(self):
        import pypdf

        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=10_000, height=10_000)
        buf = io.BytesIO()
        writer.write(buf)

        with pytest.raises(ValueError, match="would render"):
            _ocr_pdf(buf.getvalue(), languages="eng", scale=2)

    async def test_saves_artifact(self, tmp_path):
        pdf_bytes = _make_pdf("Test Job")

        def handler(request):
            return httpx.Response(200, content=pdf_bytes)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await scrape("https://example.com/job.pdf", {}, client, artifact_dir=tmp_path)
            assert (tmp_path / "source.pdf").exists()

    async def test_title_pattern_applied_to_text(self):
        """title_pattern is applied to raw PDF text when title_source='text'."""
        pdf_bytes = _make_pdf("Company Inc\nis looking for\nResearch Engineer\n[ref: 123]")

        def handler(request):
            return httpx.Response(200, content=pdf_bytes)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://example.com/job.pdf",
                {
                    "title_source": "text",
                    "title_pattern": r"is looking for\s*\n\s*(.+?)\s*\n",
                },
                client,
            )
            assert result.title == "Research Engineer"

    async def test_title_replace_repairs_source_specific_pdf_spacing(self):
        pdf_bytes = _make_pdf(
            "PhD-candidate in Ex perimental Financial Accounting\nJOB QUALIFICATIONS"
        )

        def handler(request):
            return httpx.Response(200, content=pdf_bytes)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://example.com/job.pdf",
                {
                    "title_source": "text",
                    "title_pattern": r"(PhD-candidate in Ex\s+perimental Financial Accounting)",
                    "title_replace": {"find": r"Ex\s+perimental", "replace": "Experimental"},
                },
                client,
            )

        assert result.title == "PhD-candidate in Experimental Financial Accounting"

    @pytest.mark.parametrize(
        "value",
        [True, {}, {"find": "x"}, {"find": "(", "replace": "x"}],
    )
    async def test_title_replace_rejects_invalid_config(self, value):
        pdf_bytes = _make_pdf("Research Engineer")

        def handler(request):
            return httpx.Response(200, content=pdf_bytes)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="title_replace"):
                await scrape(
                    "https://example.com/job.pdf",
                    {"title_source": "text", "title_replace": value},
                    client,
                )

    async def test_location_pattern_applied_to_text(self):
        pdf_bytes = _make_pdf(
            "Job Title\nM\nechanical Engineer\nReports to\nLead\n"
            "Location\nSawston, Cambridge\nWho we are"
        )

        def handler(request):
            return httpx.Response(200, content=pdf_bytes)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://example.com/job.pdf",
                {
                    "title_source": "text",
                    "title_pattern": r"(?s)Job Title\s*(.*?)\s*Reports to",
                    "repair_split_initial": True,
                    "location_pattern": r"(?s)Location\s*(.*?)\s*Who we are",
                },
                client,
            )
            assert result.title == "Mechanical Engineer"
            assert result.locations == ["Sawston, Cambridge"]

    async def test_missing_location_uses_board_default(self):
        result = await parse_bytes(
            _make_pdf("Research engineer role"),
            "https://example.com/research-engineer.pdf",
            {"defaults": {"locations": ["Lausanne, Switzerland"]}},
        )

        assert result.locations == ["Lausanne, Switzerland"]

    async def test_extracted_location_wins_over_board_default(self):
        result = await parse_bytes(
            _make_pdf("Research role Location: Sion, Switzerland"),
            "https://example.com/research-role.pdf",
            {
                "location_pattern": r"Location:\s*(Sion, Switzerland)",
                "defaults": {"locations": ["Lausanne, Switzerland"]},
            },
        )

        assert result.locations == ["Sion, Switzerland"]

    async def test_pdf_defaults_reject_unknown_fields(self):
        with pytest.raises(ValueError, match="JobContent fields"):
            await parse_bytes(
                _make_pdf("Research engineer role"),
                "https://example.com/research-engineer.pdf",
                {"defaults": {"locatons": ["Lausanne, Switzerland"]}},
            )

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("locations", "Lausanne, Switzerland", "non-empty list"),
            ("locations", [""], "non-empty list"),
            ("employment_type", "permanent", "canonical value"),
            ("job_location_type", "office", "onsite, remote, or hybrid"),
            ("date_posted", "August 1, 2026", "ISO date"),
            ("language", "english", "ISO 639-1"),
            ("base_salary", {"currency": "chf", "min": 100}, "ISO 4217"),
            ("base_salary", {"currency": "CHF"}, "must contain min or max"),
            ("base_salary", {"min": -1}, "finite non-negative"),
            ("base_salary", {"min": float("nan")}, "finite non-negative"),
            ("base_salary", {"min": 100, "max": 50}, "min cannot exceed max"),
            ("extras", [], "must be an object"),
        ],
    )
    async def test_pdf_defaults_reject_invalid_typed_values(self, field, value, message):
        with pytest.raises(ValueError, match=message):
            await parse_bytes(
                _make_pdf("Research engineer role"),
                "https://example.com/research-engineer.pdf",
                {"defaults": {field: value}},
            )

    async def test_pdf_defaults_accept_canonical_typed_values(self):
        result = await parse_bytes(
            _make_pdf("Research engineer role"),
            "https://example.com/research-engineer.pdf",
            {
                "defaults": {
                    "locations": ["Lausanne, Switzerland"],
                    "employment_type": "full_time",
                    "job_location_type": "onsite",
                    "date_posted": "2026-08-01",
                    "language": "en",
                    "base_salary": {
                        "currency": "CHF",
                        "min": 80_000,
                        "max": 100_000,
                        "unit": "year",
                    },
                    "extras": {"source": "board default"},
                    "metadata": {"team": "Research"},
                }
            },
        )

        assert result.locations == ["Lausanne, Switzerland"]
        assert result.employment_type == "full_time"
        assert result.job_location_type == "onsite"
        assert result.date_posted == "2026-08-01"
        assert result.language == "en"
        assert result.base_salary == {
            "currency": "CHF",
            "min": 80_000,
            "max": 100_000,
            "unit": "year",
        }
        assert result.extras == {"source": "board default"}
        assert result.metadata == {"team": "Research"}

    async def test_location_url_pattern_falls_back_to_decoded_filename(self):
        pdf_bytes = _make_pdf("Professor of Family Business\nFull job description")

        def handler(request):
            return httpx.Response(200, content=pdf_bytes)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://example.com/Faculty%20Vacancy-Family-Business-Singapore.pdf",
                {
                    "title_source": "text",
                    "location_pattern": r"Location:\s*([^\n]+)",
                    "location_url_pattern": r"-(Lausanne|Singapore)\.pdf$",
                },
                client,
            )

        assert result.locations == ["Singapore"]

    async def test_location_pattern_takes_precedence_over_url_fallback(self):
        pdf_bytes = _make_pdf("Engineer\nLocation: Zurich")

        def handler(request):
            return httpx.Response(200, content=pdf_bytes)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://example.com/Engineer-Singapore.pdf",
                {
                    "location_pattern": r"Location:\s*([^\n]+)",
                    "location_url_pattern": r"-(Lausanne|Singapore)\.pdf$",
                },
                client,
            )

        assert result.locations == ["Zurich"]

    async def test_named_fields_pattern_applied_to_table_layout(self):
        pdf_bytes = _make_pdf("Role Location\nField Service Engineer\nOsaka or Shizuoka\nSalary")

        def handler(request):
            return httpx.Response(200, content=pdf_bytes)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://example.com/r06_job01.pdf",
                {
                    "fields_pattern": (
                        r"(?s)Role\s+Location\s+(?P<title>.+?)\s+"
                        r"(?P<location>Osaka.*?)\s+Salary"
                    )
                },
                client,
            )
            assert result.title == "Field Service Engineer"
            assert result.locations == ["Osaka or Shizuoka"]

    async def test_named_fields_take_precedence_with_per_field_fallbacks(self):
        pdf_bytes = _make_pdf("Role Location\nNamed Title\nTokyo\nLegacy location: Zurich\nSalary")

        def handler(request):
            return httpx.Response(200, content=pdf_bytes)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://example.com/Filename_Title.pdf",
                {
                    "fields_pattern": r"(?s)Role\s+Location\s+(?P<title>.+?)\s+Tokyo",
                    "location_pattern": r"Legacy location:\s*([^\n]+)",
                },
                client,
            )

        assert result.title == "Named Title"
        assert result.locations == ["Zurich"]

    async def test_legacy_location_pattern_does_not_repair_single_letter_word(self):
        pdf_bytes = _make_pdf("Role\nLocation: A Coruna")

        def handler(request):
            return httpx.Response(200, content=pdf_bytes)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://example.com/Engineer.pdf",
                {
                    "repair_split_initial": True,
                    "location_pattern": r"Location:\s*([^\n]+)",
                },
                client,
            )

        assert result.locations == ["A Coruna"]

    async def test_title_pattern_no_match_falls_back(self):
        """When title_pattern doesn't match text, falls back to heading heuristic."""
        pdf_bytes = _make_pdf("Software Engineer\nGreat role description")

        def handler(request):
            return httpx.Response(200, content=pdf_bytes)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://example.com/job.pdf",
                {
                    "title_source": "text",
                    "title_pattern": r"NOMATCH (.+)",
                },
                client,
            )
            assert result.title == "Software Engineer"

    async def test_http_error_raises(self):
        def handler(request):
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await scrape("https://example.com/missing.pdf", {}, client)


class TestInternationalCanoeFederationPdfConfig:
    @pytest.mark.parametrize(
        ("text", "expected_title", "expected_location"),
        [
            (
                "Digital Channel Manager\n"
                "Reporting to Head of Digital\n"
                "Location: Budapest Headquarters, with regular collaboration with the "
                "Hangzhou Office and\n"
                "occasional international travel\n"
                "Responsible for:\nDigital operations",
                "Digital Channel Manager",
                (
                    "Budapest Headquarters, with regular collaboration with the Hangzhou "
                    "Office and occasional international travel"
                ),
            ),
            (
                "Communications Coordinator\n"
                "Head of Communication & Public Relations\n"
                "Location: Budapest Headquarters, with flexibility for occasional travel and "
                "cross-office coordination\n"
                "Responsible for:\nMedia relations",
                "Communications Coordinator",
                (
                    "Budapest Headquarters, with flexibility for occasional travel and "
                    "cross-office coordination"
                ),
            ),
            (
                "Job Title: Asia and Oceania Continental Manager\n"
                "Employment Type: Full-Time\n"
                "Start Date: March 2025\n"
                "Location: ICF Hangzhou Office, China (refer to website for details).\n"
                "Working Hours: Full time",
                "Asia and Oceania Continental Manager",
                "ICF Hangzhou Office, China (refer to website for details).",
            ),
            (
                "Office Administration Manager\n"
                "Reporting to Chief Operating Officer\n"
                "Location: Budapest, Hungary\n\n"
                "Position Overview:\nSupport the office",
                "Office Administration Manager",
                "Budapest, Hungary",
            ),
        ],
    )
    def test_known_official_templates_extract_bounded_fields(
        self,
        text,
        expected_title,
        expected_location,
    ):
        config = _board_scraper_config("international-canoe-federation-careers")

        assert config["require_title_pattern"] is True
        assert _extract_pattern(text, config["title_pattern"]) == expected_title
        assert _extract_pattern(text, config["location_pattern"]) == expected_location

    @pytest.mark.parametrize(
        "text",
        [
            "International Canoe Federation\nCandidate Brief\nUnstructured body",
            "A" * 161 + "\nReporting to Chief Operating Officer",
            "Digital Channel Manager\nUnrecognized next field",
        ],
    )
    def test_unrecognized_title_layouts_fail_closed(self, text):
        config = _board_scraper_config("international-canoe-federation-careers")

        assert config["require_title_pattern"] is True
        assert _extract_pattern(text, config["title_pattern"]) is None


class TestImdPdfConfig:
    async def test_family_business_uses_filename_location_fallback(self):
        config = _board_scraper_config("imd-faculty")
        pdf_bytes = _make_pdf(
            "Professor of Family Business v 20/05/2026\n"
            "Faculty Recruiting: Professor of Family Business\n"
            "Full job description"
        )

        result = await parse_bytes(
            pdf_bytes,
            (
                "https://www.imd.org/wp-content/uploads/2026/06/"
                "20260520-Faculty-Vacancy-Family-Business-Singapore.pdf"
            ),
            config,
        )

        assert result.title == "Professor of Family Business"
        assert result.locations == ["Singapore"]


class TestIcjPdfConfig:
    @pytest.mark.parametrize(
        ("text", "expected_title", "expected_location"),
        [
            (
                "TERMS OF REFERENCE\n"
                "Legal Adviser Consultant, Myanmar project\n"
                "Type of contract: Individual Consultancy.\n"
                "Location: Home-based, close to the Bangkok time zone.\n"
                "Duration of contract: 15 months.\n"
                "Summary\nRole description",
                "Legal Adviser Consultant, Myanmar project",
                "Home-based, close to the Bangkok time zone.",
            ),
            (
                "TERMS OF REFERENCE\n"
                "Final external evaluation\n"
                "Project name\n"
                "Type of contract: Consultancy\n"
                "Location: Home-based in Geneva\n"
                "Duration of contract: 35 working days\n"
                "Summary\n"
                "The evaluation may lead to recommendations for future work.",
                "Final external evaluation",
                "Home-based in Geneva",
            ),
        ],
    )
    async def test_structural_heading_extracts_official_icj_templates(
        self,
        text,
        expected_title,
        expected_location,
    ):
        result = await parse_bytes(
            _make_pdf(text),
            "https://www.icj.org/wp-content/uploads/2026/08/document.pdf",
            _icj_scraper_config(),
        )

        assert result.title == expected_title
        assert result.locations == [expected_location]

    async def test_unmatched_template_fails_before_body_or_filename_fallback(self):
        text = (
            "ICJ recruitment document\n"
            "Summary\n"
            "This assignment will lead research and produce recommendations."
        )

        with pytest.raises(ValueError, match="title_pattern did not match"):
            await parse_bytes(
                _make_pdf(text),
                "https://www.icj.org/wp-content/uploads/2026/08/Misleading_Filename.pdf",
                _icj_scraper_config(),
            )
