from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.personio import (
    _parse_description,
    _parse_employment_type,
    _parse_job,
    _slug_from_url,
    can_handle,
    discover,
)


class TestSlugFromUrl:
    def test_de_domain(self):
        assert _slug_from_url("https://acme.jobs.personio.de") == "acme"

    def test_com_domain(self):
        assert _slug_from_url("https://acme.jobs.personio.com") == "acme"

    def test_with_path(self):
        assert _slug_from_url("https://acme.jobs.personio.de/job/12345") == "acme"

    def test_ignored_slug_www(self):
        assert _slug_from_url("https://www.jobs.personio.de") is None

    def test_ignored_slug_api(self):
        assert _slug_from_url("https://api.jobs.personio.de") is None

    def test_unrelated_url(self):
        assert _slug_from_url("https://example.com/careers") is None

    def test_hyphenated_slug(self):
        assert _slug_from_url("https://my-company.jobs.personio.de") == "my-company"

    def test_bare_personio_domain(self):
        assert _slug_from_url("https://personio.de/jobs") is None


class TestParseEmploymentType:
    def test_permanent_fulltime(self):
        xml = (
            "<position><employmentType>permanent</employmentType>"
            "<schedule>full-time</schedule></position>"
        )
        el = ET.fromstring(xml)
        assert _parse_employment_type(el) == "Full-time"

    def test_permanent_parttime(self):
        xml = (
            "<position><employmentType>permanent</employmentType>"
            "<schedule>part-time</schedule></position>"
        )
        el = ET.fromstring(xml)
        assert _parse_employment_type(el) == "Part-time"

    def test_intern(self):
        xml = "<position><employmentType>intern</employmentType></position>"
        el = ET.fromstring(xml)
        assert _parse_employment_type(el) == "Intern"

    def test_trainee(self):
        xml = "<position><employmentType>trainee</employmentType></position>"
        el = ET.fromstring(xml)
        assert _parse_employment_type(el) == "Intern"

    def test_freelance(self):
        xml = "<position><employmentType>freelance</employmentType></position>"
        el = ET.fromstring(xml)
        assert _parse_employment_type(el) == "Contract"

    def test_both_missing(self):
        xml = "<position></position>"
        el = ET.fromstring(xml)
        assert _parse_employment_type(el) is None

    def test_permanent_no_schedule(self):
        xml = "<position><employmentType>permanent</employmentType></position>"
        el = ET.fromstring(xml)
        # permanent maps to None, schedule is empty -> _SCHEDULE_MAP.get("") -> None
        assert _parse_employment_type(el) is None

    def test_unknown_employment_type_with_fulltime_schedule(self):
        xml = (
            "<position><employmentType>other</employmentType>"
            "<schedule>full-time</schedule></position>"
        )
        el = ET.fromstring(xml)
        # "other" not in map -> no mapped value, falls through to schedule
        assert _parse_employment_type(el) == "Full-time"


class TestParseDescription:
    def test_single_section(self):
        xml = """<position>
            <jobDescriptions>
                <jobDescription>
                    <name>About Us</name>
                    <value>&lt;p&gt;Great company&lt;/p&gt;</value>
                </jobDescription>
            </jobDescriptions>
        </position>"""
        el = ET.fromstring(xml)
        result = _parse_description(el)
        assert result is not None
        assert "<h3>About Us</h3>" in result
        assert "<p>Great company</p>" in result

    def test_multiple_sections_with_headers(self):
        xml = """<position>
            <jobDescriptions>
                <jobDescription>
                    <name>Role</name>
                    <value>Role description</value>
                </jobDescription>
                <jobDescription>
                    <name>Requirements</name>
                    <value>Req list</value>
                </jobDescription>
            </jobDescriptions>
        </position>"""
        el = ET.fromstring(xml)
        result = _parse_description(el)
        assert result is not None
        assert "<h3>Role</h3>" in result
        assert "<h3>Requirements</h3>" in result
        assert "Role description" in result
        assert "Req list" in result

    def test_section_without_name(self):
        xml = """<position>
            <jobDescriptions>
                <jobDescription>
                    <value>Just content</value>
                </jobDescription>
            </jobDescriptions>
        </position>"""
        el = ET.fromstring(xml)
        result = _parse_description(el)
        assert result == "Just content"

    def test_empty_job_descriptions(self):
        xml = "<position><jobDescriptions></jobDescriptions></position>"
        el = ET.fromstring(xml)
        assert _parse_description(el) is None

    def test_no_job_descriptions_element(self):
        xml = "<position></position>"
        el = ET.fromstring(xml)
        assert _parse_description(el) is None

    def test_skips_empty_value(self):
        xml = """<position>
            <jobDescriptions>
                <jobDescription>
                    <name>Empty</name>
                    <value></value>
                </jobDescription>
                <jobDescription>
                    <name>Full</name>
                    <value>Content here</value>
                </jobDescription>
            </jobDescriptions>
        </position>"""
        el = ET.fromstring(xml)
        result = _parse_description(el)
        assert result is not None
        assert "Empty" not in result
        assert "<h3>Full</h3>" in result
        assert "Content here" in result


class TestParseJob:
    def test_full_job(self):
        xml = """<position>
            <id>12345</id>
            <name>Software Engineer</name>
            <office>Berlin</office>
            <department>Engineering</department>
            <seniority>Senior</seniority>
            <employmentType>permanent</employmentType>
            <schedule>full-time</schedule>
            <createdAt>2024-01-15</createdAt>
            <jobDescriptions>
                <jobDescription>
                    <name>About</name>
                    <value>Great role</value>
                </jobDescription>
            </jobDescriptions>
        </position>"""
        el = ET.fromstring(xml)
        result = _parse_job(el, "acme")
        assert result is not None
        assert result.url == "https://acme.jobs.personio.de/job/12345"
        assert result.title == "Software Engineer"
        assert result.locations == ["Berlin"]
        assert result.employment_type == "Full-time"
        assert result.date_posted == "2024-01-15"
        assert result.description is not None
        assert "Great role" in result.description
        assert result.metadata["id"] == "12345"
        assert result.metadata["department"] == "Engineering"
        assert result.metadata["seniority"] == "Senior"

    def test_missing_id_returns_none(self):
        xml = "<position><name>No ID</name></position>"
        el = ET.fromstring(xml)
        assert _parse_job(el, "acme") is None

    def test_office_becomes_locations(self):
        xml = "<position><id>1</id><office>Munich</office></position>"
        el = ET.fromstring(xml)
        result = _parse_job(el, "acme")
        assert result.locations == ["Munich"]

    def test_no_office_no_locations(self):
        xml = "<position><id>1</id></position>"
        el = ET.fromstring(xml)
        result = _parse_job(el, "acme")
        assert result.locations is None

    def test_metadata_fields(self):
        xml = """<position>
            <id>99</id>
            <subcompany>Sub Corp</subcompany>
            <recruitingCategory>Tech</recruitingCategory>
            <keywords>python, async</keywords>
            <occupation>Developer</occupation>
            <occupationCategory>IT</occupationCategory>
            <yearsOfExperience>5+</yearsOfExperience>
        </position>"""
        el = ET.fromstring(xml)
        result = _parse_job(el, "acme")
        assert result.metadata["subcompany"] == "Sub Corp"
        assert result.metadata["recruitingCategory"] == "Tech"
        assert result.metadata["keywords"] == "python, async"
        assert result.metadata["occupation"] == "Developer"
        assert result.metadata["occupationCategory"] == "IT"
        assert result.metadata["yearsOfExperience"] == "5+"

    def test_no_metadata(self):
        xml = "<position><id>1</id></position>"
        el = ET.fromstring(xml)
        result = _parse_job(el, "acme")
        # id is always added as metadata
        assert result.metadata == {"id": "1"}

    def test_url_uses_slug(self):
        xml = "<position><id>42</id></position>"
        el = ET.fromstring(xml)
        result = _parse_job(el, "my-company")
        assert result.url == "https://my-company.jobs.personio.de/job/42"


class TestDiscover:
    async def test_returns_jobs(self):
        xml_body = """<?xml version="1.0" encoding="UTF-8"?>
        <workzag-jobs>
            <position>
                <id>1</id>
                <name>Engineer</name>
                <office>Berlin</office>
                <jobDescriptions>
                    <jobDescription><value>Desc</value></jobDescription>
                </jobDescriptions>
            </position>
            <position>
                <id>2</id>
                <name>Designer</name>
                <office>Munich</office>
                <jobDescriptions>
                    <jobDescription><value>Desc 2</value></jobDescription>
                </jobDescriptions>
            </position>
        </workzag-jobs>"""

        def handler(request):
            return httpx.Response(200, text=xml_body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.jobs.personio.de",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 2
            assert all(isinstance(j, DiscoveredJob) for j in jobs)
            assert jobs[0].title == "Engineer"
            assert jobs[1].title == "Designer"

    async def test_empty_response(self):
        xml_body = '<?xml version="1.0" encoding="UTF-8"?><workzag-jobs></workzag-jobs>'

        def handler(request):
            return httpx.Response(200, text=xml_body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.jobs.personio.de",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_no_slug_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot derive Personio slug"):
                await discover(board, client)

    async def test_slug_from_metadata(self):
        xml_body = '<?xml version="1.0" encoding="UTF-8"?><workzag-jobs></workzag-jobs>'

        def handler(request):
            assert "myslug" in str(request.url)
            return httpx.Response(200, text=xml_body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {"slug": "myslug"}}
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_slug_from_board_url(self):
        xml_body = '<?xml version="1.0" encoding="UTF-8"?><workzag-jobs></workzag-jobs>'

        def handler(request):
            assert "acme" in str(request.url)
            return httpx.Response(200, text=xml_body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://acme.jobs.personio.de", "metadata": {}}
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_skips_positions_without_id(self):
        xml_body = """<?xml version="1.0" encoding="UTF-8"?>
        <workzag-jobs>
            <position><name>No ID</name></position>
            <position><id>1</id><name>Has ID</name></position>
        </workzag-jobs>"""

        def handler(request):
            return httpx.Response(200, text=xml_body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.jobs.personio.de",
                "metadata": {"slug": "acme"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 1
            assert jobs[0].title == "Has ID"

    async def test_http_error_raises(self):
        def handler(request):
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.jobs.personio.de",
                "metadata": {"slug": "acme"},
            }
            with pytest.raises(httpx.HTTPStatusError):
                await discover(board, client)


class TestCanHandle:
    async def test_personio_url_de(self):
        result = await can_handle("https://acme.jobs.personio.de")
        assert result == {"slug": "acme"}

    async def test_personio_url_com(self):
        result = await can_handle("https://acme.jobs.personio.com")
        assert result == {"slug": "acme"}

    async def test_non_personio_url_no_client(self):
        result = await can_handle("https://example.com/careers")
        assert result is None

    async def test_detects_in_page_html(self):
        xml_body = """<?xml version="1.0" encoding="UTF-8"?>
        <workzag-jobs>
            <position><id>1</id></position>
        </workzag-jobs>"""

        def handler(request):
            url = str(request.url)
            if "personio.de/xml" in url:
                return httpx.Response(200, text=xml_body)
            return httpx.Response(
                200,
                text='<html><iframe src="https://myco.jobs.personio.de/search"></iframe></html>',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is not None
            assert result.get("slug") == "myco"

    async def test_no_match(self):
        def handler(request):
            url = str(request.url)
            if "personio.de/xml" in url:
                return httpx.Response(404)
            return httpx.Response(200, text="<html>no personio refs</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.example.com/careers", client)
            assert result is None

    async def test_url_match_with_api_probe(self):
        xml_body = """<?xml version="1.0" encoding="UTF-8"?>
        <workzag-jobs>
            <position><id>1</id></position>
            <position><id>2</id></position>
            <position><id>3</id></position>
        </workzag-jobs>"""

        def handler(request):
            return httpx.Response(200, text=xml_body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://acme.jobs.personio.de", client)
            assert result is not None
            assert result["slug"] == "acme"
            assert result["jobs"] == 3
