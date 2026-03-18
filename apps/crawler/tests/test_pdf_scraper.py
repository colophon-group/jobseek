from __future__ import annotations

import io

import httpx
import pytest

from src.core.scrapers.pdf import (
    _text_to_html,
    _title_from_text,
    _title_from_url,
    can_handle,
    scrape,
)


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
    stream.set_data(
        f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    )

    font_dict = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    resources = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font_dict}
            )
        }
    )
    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = writer._add_object(stream)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


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

    def test_pattern_no_match_returns_full(self):
        url = "https://example.com/Engineer.pdf"
        result = _title_from_url(url, pattern=r"NOMATCH_(\w+)")
        assert result == "Engineer"


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

    async def test_default_title_from_url(self):
        """Default title_source='url' uses the filename."""
        pdf_bytes = _make_pdf("Some PDF text here")

        def handler(request):
            return httpx.Response(200, content=pdf_bytes)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://example.com/Marketing_Intern.pdf", {}, client
            )
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
            result = await scrape(
                "https://example.com/Marketing_Intern.pdf", {}, client
            )
            assert result.title == "Marketing Intern"

    async def test_saves_artifact(self, tmp_path):
        pdf_bytes = _make_pdf("Test Job")

        def handler(request):
            return httpx.Response(200, content=pdf_bytes)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await scrape(
                "https://example.com/job.pdf", {}, client, artifact_dir=tmp_path
            )
            assert (tmp_path / "source.pdf").exists()

    async def test_http_error_raises(self):
        def handler(request):
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await scrape("https://example.com/missing.pdf", {}, client)
