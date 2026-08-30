"""Tests for src.core.scrapers.dom — mock-based, no real browser needed."""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.scrapers import JobContent

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _make_document_pdf(text: str) -> bytes:
    import pypdf
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    page = writer.pages[0]
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _make_document_docx(*, location: str | None = None) -> bytes:
    location_paragraph = (
        f"<w:p><w:r><w:t>Location: {location}</w:t></w:r></w:p>" if location else ""
    )
    document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>
      <w:r><w:t>Strategic Communications Officer</w:t></w:r>
    </w:p>
    {location_paragraph}
    <w:p><w:r><w:t>Lead conservation communications worldwide.</w:t></w:r></w:p>
  </w:body>
</w:document>""".encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("word/document.xml", document)
    return output.getvalue()


# ---------------------------------------------------------------------------
# Helpers (same pattern as test_browser_shared.py)
# ---------------------------------------------------------------------------


def _make_page(html: str = "<html></html>") -> MagicMock:
    page = MagicMock()
    page.goto = AsyncMock()
    page.evaluate = AsyncMock()
    page.content = AsyncMock(return_value=html)

    locator_first = MagicMock()
    locator_first.count = AsyncMock(return_value=1)
    locator_first.click = AsyncMock()
    locator = MagicMock()
    locator.first = locator_first
    page.locator = MagicMock(return_value=locator)
    return page


def _make_pw(page: MagicMock | None = None) -> MagicMock:
    page = page or _make_page()
    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()

    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()

    pw = MagicMock()
    pw.chromium = MagicMock()
    pw.chromium.launch = AsyncMock(return_value=browser)
    return pw


def _patch_playwright(page: MagicMock):
    """Return a patch context for async_playwright that yields our mock."""
    mock_pw = _make_pw(page)
    mock_async_pw = MagicMock()
    mock_async_pw.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_async_pw.__aexit__ = AsyncMock(return_value=False)
    return patch("playwright.async_api.async_playwright", return_value=mock_async_pw)


FIXTURE_HTML = """
<html><body>
<h1>Software Engineer</h1>
<div class="location">
<h2>Location</h2>
<p>London, UK</p>
</div>
<div class="about">
<h2>About the role</h2>
<p>Build amazing things.</p>
<p>Work with great people.</p>
<h2>Requirements</h2>
<ul>
<li>Python</li>
<li>JavaScript</li>
</ul>
</div>
<div class="meta">
<h3>Team</h3>
<p>Platform</p>
</div>
</body></html>
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDomScraper:
    async def test_request_headers_select_public_gateway_representation(self):
        from src.core.scrapers.dom import scrape

        requested: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(request)
            return httpx.Response(200, text="<html><body><h1>Gateway role</h1></body></html>")

        config = {
            "request_headers": {"X-Return-Format": "html"},
            "steps": [{"tag": "h1", "field": "title"}],
        }
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer secret"},
        ) as client:
            result = await scrape("https://gateway.example/jobs/42", config, client)

        assert result.title == "Gateway role"
        assert requested[0].headers["x-return-format"] == "html"
        assert "authorization" not in requested[0].headers

    async def test_request_headers_reject_actions_that_enable_rendering(self):
        from src.core.scrapers.dom import scrape

        config = {
            "actions": [{"action": "dismiss_overlays"}],
            "request_headers": {"X-Return-Format": "html"},
            "steps": [{"tag": "h1", "field": "title"}],
        }
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="only when render=false"):
                await scrape("https://gateway.example/jobs/42", config, client)

    async def test_fetch_url_transform_reads_gateway_without_changing_extraction(self):
        from src.core.scrapers.dom import scrape

        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(200, text="<html><body><h1>Gateway role</h1></body></html>")

        canonical = "https://blocked.example/jobs/42"
        config = {
            "fetch_url_transform": {
                "find": r"^https://blocked\.example(/.*)$",
                "replace": r"https://blocked-example.translate.test\1",
            },
            "steps": [{"tag": "h1", "field": "title"}],
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(canonical, config, client)

        assert result.title == "Gateway role"
        assert requested == ["https://blocked-example.translate.test/jobs/42"]

    async def test_fetch_url_transform_requires_exactly_one_match(self):
        from src.core.scrapers.dom import scrape

        config = {
            "fetch_url_transform": {
                "find": r"^https://other\.example/",
                "replace": "https://gateway.example/",
            },
            "steps": [{"tag": "h1", "field": "title"}],
        }
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="exactly once"):
                await scrape("https://blocked.example/jobs/42", config, client)

    def test_prospective_detail_preset_extracts_complete_scoped_description(self):
        from src.core.scrapers.dom import parse_html
        from src.workspace._compat import auto_scraper_type

        html = """
        <html><body>
          <section id="job">
            <h4>Employer introduction outside the vacancy description.</h4>
            <h1 id="title">Application Manager (m/w/d)</h1>
            <span class="pensum">80-100%</span>
            <div id="place-of-work">Bern oder Zürich</div>
            <p>Own the lifecycle of our core applications.</p>
            <h3>Main tasks</h3><ul><li>Operate reliable services.</li></ul>
            <h3>Profile</h3><ul><li>Experience with distributed systems.</li></ul>
          </section>
          <section id="contact"><p>Recruiter phone number</p></section>
        </body></html>
        """
        auto = auto_scraper_type("dom", {"prospective_board": "1000973"})
        assert auto is not None
        scraper_type, config = auto

        assert scraper_type == "dom"
        assert config is not None
        result = parse_html(html, config)
        assert result.title == "Application Manager (m/w/d)"
        assert result.description is not None
        assert "Own the lifecycle" in result.description
        assert "Operate reliable services" in result.description
        assert "Experience with distributed systems" in result.description
        assert "Employer introduction" not in result.description
        assert "Recruiter phone number" not in result.description

    def test_lucca_detail_preset_extracts_title_and_description(self):
        from src.core.scrapers.dom import can_handle, parse_html

        html = """
        <html><body><article class="jobOffer-article">
          <header>
            <h1 data-testid="job-offer-title">Athlete Intern</h1>
            <span data-testid="job-offer-location">Lausanne</span>
          </header>
          <div class="jobOffer-article-content">
            <h2>Job description</h2>
            <p>Help deliver international aquatics events.</p>
            <h2>Profile required</h2>
            <p>Experience coordinating sports programmes.</p>
            <h2>Company description</h2>
            <p>World Aquatics is the global governing body for aquatic sports.</p>
            <p data-testid="job-offer-publication-date">On 7/22/2026</p>
          </div>
        </article></body></html>
        """

        config = can_handle([html])
        assert config is not None
        assert config["scope"] == ".jobOffer-article"

        result = parse_html(html, config)
        assert result.title == "Athlete Intern"
        # Location is authoritative rich-row data from the paired DOM monitor.
        assert result.locations is None
        assert result.description is not None
        assert "Help deliver international aquatics events." in result.description
        assert "Profile required" in result.description
        assert "On 7/22/2026" not in result.description

    def test_city_of_zurich_preset_defaults_city_and_preserves_regional_roles(self):
        from src.core.scrapers.dom import can_handle, parse_html

        def page(title: str) -> str:
            return f"""
            <html><head>
              <title>{title} | Stadt Zürich</title>
              <link rel="canonical" href="/jobs/job-detailseite.61398.html">
            </head><body>
              <stzh-pagetitle>
                <stzh-heading slot="heading">{title}</stzh-heading>
                <stzh-text slot="lead">Dauerstelle</stzh-text>
                <stzh-text slot="lead">Elektrizitätswerk</stzh-text>
              </stzh-pagetitle>
              <stzh-pagecontent>
                <stzh-richtext><p>Gestalten Sie die Energieversorgung mit.</p></stzh-richtext>
                <stzh-richtext>
                  <h2>Aufgaben</h2><ul><li>Betreiben Sie sichere Netze.</li></ul>
                </stzh-richtext>
                <stzh-cta href="https://career2.successfactors.eu/career?career_job_req_id=51398"></stzh-cta>
                <stzh-text><p>Referenz-Nr.: 51398</p></stzh-text>
                <stzh-heading level="2" slot="heading">Arbeiten bei der Stadt</stzh-heading>
              </stzh-pagecontent>
            </body></html>
            """

        zurich_html = page("Projektleiter*in Energie, 80–100 %")
        graubuenden_html = page(
            "Lehrstelle Polymechaniker*in, Standort Sils im Domleschg GR, 100 %"
        )
        winterthur_html = page("Projektleiter*in, Standort Winterthur ZH, 80–100 %")
        region_html = page("Netzelektriker*in Mittelbünden, 80–100 %")

        config = can_handle([zurich_html, graubuenden_html, winterthur_html, region_html])
        assert config is not None
        assert config["defaults"] == {"locations": ["Zurich, Switzerland"]}

        zurich = parse_html(zurich_html, config)
        assert zurich.title == "Projektleiter*in Energie, 80–100 %"
        assert zurich.locations == ["Zurich, Switzerland"]
        assert zurich.employment_type == "Dauerstelle"
        assert zurich.metadata == {"department": "Elektrizitätswerk"}
        assert zurich.description is not None
        assert "Gestalten Sie die Energieversorgung" in zurich.description
        assert "Betreiben Sie sichere Netze" in zurich.description
        assert "Arbeiten bei der Stadt" not in zurich.description

        graubuenden = parse_html(graubuenden_html, config)
        assert graubuenden.locations == ["Sils im Domleschg GR"]

        winterthur = parse_html(winterthur_html, config)
        assert winterthur.locations == ["Winterthur ZH"]

        region = parse_html(region_html, config)
        assert region.locations == ["Mittelbünden"]

    def test_clinch_probe_uses_job_component_layout(self):
        from src.core.scrapers.dom import can_handle, parse_html

        html = """
        <html><body>
          <h1>Build something bigger than your career</h1>
          <div class="block-wrapper job-description-container">
            <h3 class="job-title">Corporate Strategy Associate</h3>
            <ul>
              <li class="job-component-icon-and-text job-component-workplace-type">Hybrid</li>
              <li class="job-component-icon-and-text job-component-location">
                Boston, Massachusetts, United States
              </li>
              <li class="job-component-icon-and-text job-component-department">CEO Office</li>
              <li class="job-component-icon-and-text job-component-employment-type">Full-time</li>
            </ul>
            <div class="job-description-controls">Add to favorites</div>
            <div class="job-description">
              <p>Shape the company's long-term growth strategy.</p>
              <h4>Requirements</h4>
              <ul><li>Strategy consulting experience.</li></ul>
            </div>
          </div>
        </body></html>
        """

        config = can_handle([html])
        assert config is not None
        assert config["scope"] == ".job-description-container"

        result = parse_html(html, config)
        assert result.title == "Corporate Strategy Associate"
        assert result.locations == ["Boston, Massachusetts, United States"]
        assert result.job_location_type == "Hybrid"
        assert result.employment_type == "Full-time"
        assert result.metadata == {"department": "CEO Office"}
        assert result.description is not None
        assert "long-term growth strategy" in result.description
        assert "Strategy consulting experience" in result.description
        assert "Add to favorites" not in result.description

    def test_tribepad_probe_scopes_description_and_labeled_metadata(self):
        from src.core.scrapers.dom import can_handle, parse_html

        html = """
        <html><body>
          <div id="content" class="scroll job_page">
            <h1 class="job_title">Multi Skilled Operative</h1>
            <div class="main"><div class="box fr-view" id="job_main">
              <div>
                <h3>Job Introduction</h3>
                <p>Operate and maintain the asphalt plant safely.</p>
                <h3>What we are looking for</h3>
                <ul><li>Experience in an industrial environment.</li></ul>
              </div>
              <div><p>Tarmac Trading Limited</p></div>
              <div class="related" id="docs">
                <h3>Attached documents</h3><a href="benefits.pdf">Benefits</a>
              </div>
              <a class="btn btn-apply">Apply</a>
            </div></div>
            <div class="sidebar">
              <table class="details">
                <tr><td class="label">Salary</td><td>Competitive</td></tr>
                <tr><td class="label">Contract Type</td><td>Permanent</td></tr>
                <tr><td class="label">Closing Date</td><td>25 September, 2026</td></tr>
                <tr><td class="label">Location</td><td>Stoke-on-Trent, United Kingdom</td></tr>
                <tr><td class="label">Posted on</td><td>27 August, 2026</td></tr>
              </table>
              <p>Directions to</p><p>Print this job</p>
            </div>
          </div>
          <span class="powered_by">Powered by
            <a href="https://www.tribepad.com/looking-for-a-job/">Tribepad</a>
          </span>
          <footer>Acquisition Software | Cookies Policy</footer>
        </body></html>
        """

        config = can_handle([html])
        assert config is not None

        result = parse_html(html, config)
        assert result.title == "Multi Skilled Operative"
        assert result.locations == ["Stoke-on-Trent, United Kingdom"]
        assert result.employment_type == "Permanent"
        assert result.date_posted == "2026-08-27"
        assert result.extras["valid_through"] == "2026-09-25"
        assert result.metadata == {"salary": "Competitive"}
        assert result.description is not None
        assert "Operate and maintain" in result.description
        assert "industrial environment" in result.description
        assert "Attached documents" not in result.description
        assert "Directions to" not in result.description
        assert "Acquisition Software" not in result.description

    def test_tpf_board_config_covers_pagination_and_labeled_details(self):
        from src.core.scrapers.dom import parse_html
        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        row = next(
            item for item in rows if item["board_slug"] == "transports-publics-fribourgeois-emploi"
        )
        monitor_config = json.loads(row["monitor_config"])
        scraper_config = json.loads(row["scraper_config"])

        assert monitor_config["pagination"] == {
            "param_name": "p",
            "start": 1,
            "increment": 1,
            "max_pages": 100,
        }
        assert monitor_config["url_filter"] == r"[?&]page=advertisement_display&id=\d+"

        html = """
        <html><body>
          <h1>Nos offres d'emploi</h1>
          <h2>Gestionnaire foncier (60-100%)</h2>
          <div>Postuler</div>
          <p>Gérer les procédures foncières des projets ferroviaires.</p>
          <h3>Votre profil</h3><ul><li>Expérience en droit réel.</li></ul>
          <p>Lieu de travail : Givisiez</p>
          <p>Entrée en fonction : date à convenir</p>
          <div>Postuler</div><address>TPF Holding, Givisiez</address>
        </body></html>
        """
        result = parse_html(html, scraper_config)
        assert result.title == "Gestionnaire foncier (60-100%)"
        assert result.locations == ["Givisiez"]
        assert "Gérer les procédures foncières" in result.description
        assert "TPF Holding" not in result.description

        locationless = parse_html(
            html.replace("<p>Lieu de travail : Givisiez</p>", ""),
            scraper_config,
        )
        assert locationless.locations == ["Canton of Fribourg, Switzerland"]

    def test_probe_handles_branded_title_and_inline_french_location(self):
        from src.core.scrapers.dom import can_handle, parse_html

        html = """
        <html><head>
          <title>TPF emploi | Nos offres - Gestionnaire foncier (60-100%)</title>
        </head><body>
          <h1>Nos offres d'emploi</h1>
          <a>Retour</a>
          <h2>Gestionnaire foncier (60-100%)</h2>
          <div>Postuler</div>
          <p>Gérer les procédures foncières des projets ferroviaires.</p>
          <h3>Votre profil</h3>
          <ul><li>Expérience en droit réel.</li></ul>
          <p>Lieu de travail : Givisiez</p>
          <p>Entrée en fonction : date à convenir</p>
        </body></html>
        """

        locationless_html = html.replace(
            "<p>Lieu de travail : Givisiez</p>",
            "<p>Entrée en fonction : date à convenir</p>",
        )
        config = can_handle([locationless_html, html])
        assert config is not None

        result = parse_html(html, config)
        assert result.title == "Gestionnaire foncier (60-100%)"
        assert result.locations == ["Givisiez"]
        assert "Gérer les procédures foncières" in result.description

    def test_probe_handles_short_french_location_label(self):
        """Oleeo/Hireserve pages label their location with bare ``Lieu``."""
        from src.core.scrapers.dom import can_handle, parse_html

        html = """
        <html><head><title>Infirmier-ère - CHUV</title></head><body>
          <div class="job_description">
            <h1>Infirmier-ère</h1>
            <div class="job_classifications">
              <div class="classification">
                <div class="class_type">Lieu</div>
                <div class="class_value">Lausanne</div>
              </div>
            </div>
            <h2>Mission</h2>
            <p>Assurer des soins spécialisés aux patientes et patients.</p>
          </div>
        </body></html>
        """

        config = can_handle([html])
        assert config is not None

        result = parse_html(html, config)
        assert result.title == "Infirmier-ère"
        assert result.locations == ["Lausanne"]
        assert result.description is not None
        assert "soins spécialisés" in result.description

    @pytest.mark.parametrize("separator", [":", "："])
    def test_probe_handles_inline_short_french_location_label(self, separator: str):
        from src.core.scrapers.dom import can_handle, parse_html

        html = f"""
        <html><head><title>Infirmier-ère - CHUV</title></head><body>
          <h1>Infirmier-ère</h1>
          <p>Lieu{separator} Lausanne</p>
          <h2>Mission</h2>
          <p>Assurer des soins spécialisés aux patientes et patients.</p>
        </body></html>
        """

        config = can_handle([html])
        assert config is not None

        result = parse_html(html, config)
        assert result.title == "Infirmier-ère"
        assert result.locations == ["Lausanne"]

    def test_probe_does_not_treat_lieutenant_as_french_location_label(self):
        from src.core.scrapers.dom import can_handle, parse_html

        html = """
        <html><head><title>Lieutenant de sécurité - Example</title></head><body>
          <h1>Lieutenant de sécurité</h1>
          <p>Protéger le site et coordonner les équipes de sécurité.</p>
        </body></html>
        """

        config = can_handle([html])
        assert config is not None
        assert not any(step.get("field") == "location" for step in config["steps"])

        result = parse_html(html, config)
        assert result.title == "Lieutenant de sécurité"
        assert result.locations is None

    @pytest.mark.parametrize(
        "label",
        [
            "job location",
            "location",
            "workplace",
            "lieu de travail",
            "lieu",
            "arbeitsort",
            "arbeitsplatz",
            "luogo di lavoro",
        ],
    )
    @pytest.mark.parametrize(
        ("label_html_template", "matched_label_template"),
        [
            ("<p>{label}</p><p>Lausanne</p>", "{label}"),
            ("<p>{label}: Lausanne</p>", "{label}: Lausanne"),
            ("<p>{label}： Lausanne</p>", "{label}： Lausanne"),
            ("<p>{label}:</p><p>Lausanne</p>", "{label}:"),
            ("<p>{label}：</p><p>Lausanne</p>", "{label}："),
        ],
        ids=[
            "standalone",
            "inline-ascii-colon",
            "inline-fullwidth-colon",
            "separate-ascii-colon",
            "separate-fullwidth-colon",
        ],
    )
    def test_probe_location_step_preserves_labels_and_skips_earlier_prefix_text(
        self,
        label: str,
        label_html_template: str,
        matched_label_template: str,
    ):
        from src.core.scrapers.dom import can_handle, parse_html

        earlier_text = {
            "job location": "Job locations evolve",
            "location": "Locationless role",
            "workplace": "Workplace culture matters",
            "lieu de travail": "Lieu de travailleur social",
            "lieu": "Lieutenant de sécurité",
            "arbeitsort": "Arbeitsordnung beachten",
            "arbeitsplatz": "Arbeitsplatzgestaltung",
            "luogo di lavoro": "Luogo di lavorazione",
        }[label]
        label_html = label_html_template.format(label=label)
        matched_label = matched_label_template.format(label=label)
        html = f"""
        <html><head><title>Security Officer - Example</title></head><body>
          <h1>Security Officer</h1>
          <p>{earlier_text}</p>
          {label_html}
          <h2>Mission</h2>
          <p>Assurer la sécurité des patientes et patients.</p>
        </body></html>
        """

        config = can_handle([html])
        assert config is not None
        location_step = next(step for step in config["steps"] if step.get("field") == "location")
        match_regex = location_step["match_regex"]
        assert re.fullmatch(match_regex, earlier_text) is None
        assert re.fullmatch(match_regex, matched_label) is not None

        result = parse_html(html, config)
        assert result.title == "Security Officer"
        assert result.locations == ["Lausanne"]

    def test_talentsoft_probe_builds_locale_independent_config(self):
        from src.core.scrapers.dom import can_handle, parse_html

        html = """
        <html><body>
          <h1 class="ts-offer-page__title ts-title">Credit Risk Analyst</h1>
          <div class="ts-offer-page__body">
            <h2 class="JobDescription">Description du poste</h2>
            <h3>Type de contrat</h3>
            <p id="fldjobdescription_contract">CDI</p>
            <h3>Missions</h3>
            <div><p>Review applications.</p><ul><li>Assess credit risk.</li></ul></div>
            <h2 class="Location">Localisation du poste</h2>
            <h3>Zone géographique</h3>
            <p id="fldlocation_location_geographicalareacollection">Europe, Monaco</p>
            <h2 class="ApplicantCriteria">Critères candidat</h2>
            <h3>Formation</h3><p>Master in finance.</p>
            <h2 class="FooterSection">Informations générales</h2>
          </div>
        </body></html>
        """

        config = can_handle([html])
        assert config is not None
        result = parse_html(html, config)

        assert result.title == "Credit Risk Analyst"
        assert result.description == (
            "<h2>Description du poste</h2><h3>Type de contrat</h3>"
            "<p>CDI</p><h3>Missions</h3><p>Review applications.</p>"
            "<ul><li>Assess credit risk.</li></ul>"
        )
        assert result.locations == ["Europe, Monaco"]
        assert result.employment_type == "CDI"
        assert result.extras == {
            "qualifications": [
                "<h2>Critères candidat</h2><h3>Formation</h3><p>Master in finance.</p>"
            ]
        }

    def test_safran_uses_partitioned_talentsoft_source(self):
        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        row = next(item for item in rows if item["board_slug"] == "safran-global")
        monitor_config = json.loads(row["monitor_config"])
        scraper_config = json.loads(row["scraper_config"])

        assert row["board_url"].startswith("https://careers.safran-group.com/job/")
        assert monitor_config["render"] is False
        assert monitor_config["pagination"] == {
            "param_name": "page",
            "max_pages": 1_000,
            "partition_selector": "ul.facette-titre-niv1 a[href*='facet_Contract=']",
            "partition_fallback_selector": ("ul.facette-titre-niv1 a[href*='facet_JobFamily=']"),
            "partition_count_regex": r"\((\d+)\s+(?:vacancies|offres)",
            "partition_result_limit": 1_000,
            "partition_validate_total": True,
            "partition_drop_params": ["changefacet"],
            "partition_stateless": True,
            "transient_403": True,
        }
        assert scraper_config["render"] is False
        assert any(
            step.get("attr") == "id=fldlocation_location_geographicalareacollection"
            for step in scraper_config["steps"]
        )

    def test_parse_html_applies_defaults_only_to_missing_fields(self):
        from src.core.scrapers.dom import parse_html

        result = parse_html(
            "<html><body><h1>Sales Assistant</h1></body></html>",
            {
                "steps": [{"tag": "h1", "field": "title"}],
                "defaults": {
                    "title": "Fallback title",
                    "locations": ["Malta"],
                },
            },
        )

        assert result.title == "Sales Assistant"
        assert result.locations == ["Malta"]

    def test_parse_html_rejects_non_object_defaults(self):
        from src.core.scrapers.dom import parse_html

        with pytest.raises(ValueError, match="defaults must be an object"):
            parse_html(
                "<html><body><h1>Sales Assistant</h1></body></html>",
                {
                    "steps": [{"tag": "h1", "field": "title"}],
                    "defaults": ["Malta"],
                },
            )

    def test_kontact_probe_builds_clean_extraction_config(self):
        from src.core.scrapers.dom import can_handle, parse_html

        html = """
        <html><head>
          <meta name="Author" content="KontactIntelligence.com">
        </head><body>
          <nav><h1>Navigation title</h1></nav>
          <div id="content">
            <div class="bold">Location:</div><div>Rome, GA</div>
            <h1 class="opportunityTitle">Cardiothoracic Surgery APP</h1>
            <div>Print Opportunity</div>
            <h4>Overview</h4>
            <p>Join a collaborative surgical team.</p>
            <h4>Job Requirements</h4>
            <p>Current Georgia license.</p>
            <div>Print Opportunity</div>
            <div>Forward Opportunity</div>
          </div>
        </body></html>
        """

        config = can_handle([html])
        assert config is not None
        assert config["scope"] == "#content"

        result = parse_html(html, config)
        assert result.title == "Cardiothoracic Surgery APP"
        assert result.locations == ["Rome, GA"]
        assert result.description == (
            "<p>Join a collaborative surgical team.</p>"
            "<h4>Job Requirements</h4>"
            "<p>Current Georgia license.</p>"
        )

    def test_kontact_probe_supports_h2_opportunity_title(self):
        from src.core.scrapers.dom import can_handle, parse_html

        html = """
        <html><head>
          <meta name="Author" content="KontactIntelligence.com">
        </head><body><div id="content">
          <div>Location:</div><div>Orlando, FL 32804</div>
          <h2 class="opportunityTitle">Family Medicine Physician</h2>
          <h4>Overview</h4><p>Lead an outpatient practice.</p>
          <h4>Client Description</h4><p>Employer boilerplate.</p>
          <div>Print Opportunity</div><div>Search Results</div>
        </div></body></html>
        """

        config = can_handle([html])
        assert config is not None
        result = parse_html(html, config)
        assert result.title == "Family Medicine Physician"
        assert result.locations == ["Orlando, FL 32804"]
        assert result.description == (
            "<p>Lead an outpatient practice.</p>"
            "<h4>Client Description</h4>"
            "<p>Employer boilerplate.</p>"
        )

    def test_elvium_probe_builds_scoped_retrying_extraction_config(self):
        from src.core.scrapers.dom import can_handle, parse_html

        html = """
        <html><head><title>Broker Assistant at Howden Denmark</title></head><body>
          <section class="career-page job-posting-layout">
            <div class="job-posting-widget">
              <p>Help clients navigate a changing risk landscape.</p>
              <p>Support clients with insurance and risk-management solutions.</p>
              <h2>What we offer</h2><p>Join an employee-owned global group.</p>
            </div>
            <div class="contact-info-widget">
              <h3>Kontakt Info</h3>
              <p>Howden Denmark<br>Dokken 10<br>6700 Esbjerg<br>Denmark</p>
            </div>
          </section>
        </body></html>
        """

        config = can_handle([html])
        assert config is not None
        assert config["scope"] == "section.job-posting-layout"
        assert config["include_document_title"] is True
        assert config["retry_statuses"] == {"429": 3}

        result = parse_html(html, config)
        assert result.title == "Broker Assistant"
        assert result.locations == ["Howden Denmark Dokken 10 6700 Esbjerg Denmark"]
        assert result.description == (
            "<p>Help clients navigate a changing risk landscape.</p>"
            "<p>Support clients with insurance and risk-management solutions.</p>"
            "<h2>What we offer</h2>"
            "<p>Join an employee-owned global group.</p>"
        )

    def test_solique_probe_extracts_static_publication_content(self):
        from src.core.scrapers.dom import can_handle, parse_html

        html = """
        <html><head><title>Chauffeur Kat. CE (m/w/d) - 80-100%</title></head><body>
          <header>
            <div class="job-title">Chauffeur Kat. CE (m/w/d)</div>
            <a href="https://live.solique.ch/acme/apply/id/123">Apply</a>
          </header>
          <div class="intro">Move goods safely for our customers.</div>
          <div class="tasks-profile-wrapper">
            <h3 class="tasks-title">What you do</h3>
            <ul><li>Drive scheduled routes.</li><li>Load the vehicle.</li></ul>
            <h3 class="profile-title">What you bring</h3>
            <ul><li>Category CE licence.</li></ul>
          </div>
          <h3 class="contact-title">Your contact</h3>
          <div class="contact">Recruiting Team</div>
          <h3 class="location-title">Workplace</h3>
          <div class="location">Industriestrasse 1 8000 Zurich</div>
        </body></html>
        """

        config = can_handle([html])
        assert config is not None

        result = parse_html(html, config)
        assert result.title == "Chauffeur Kat. CE (m/w/d)"
        assert result.description == (
            "<div>Move goods safely for our customers.</div>"
            "<h3>What you do</h3>"
            "<ul><li>Drive scheduled routes.</li><li>Load the vehicle.</li></ul>"
            "<h3>What you bring</h3>"
            "<ul><li>Category CE licence.</li></ul>"
        )
        assert result.locations == ["Industriestrasse 1 8000 Zurich"]

    def test_rexx_portal7_probe_extracts_stable_detail_fields(self):
        from src.core.scrapers.dom import can_handle, parse_html

        html = """
        <html><head>
          <title>Stellenangebot Inside Sales Manager (m/w/d) bei Jobportal</title>
          <meta name="generator" content="Rexx Recruitment - Portal7">
          <meta name="description" content="Vertrieb in Mannheim">
        </head><body>
          <div id="jobTplContainer" class="ck_content">
            <p>VAG develops valves for water infrastructure.</p>
            <h3>Ihre Aufgaben</h3>
            <ul><li>Advise customers.</li><li>Prepare quotations.</li></ul>
          </div>
          <div id="footer_links">Apply</div>
        </body></html>
        """

        config = can_handle([html])
        assert config is not None
        assert config["scope"] == "#jobTplContainer"
        assert config["include_document_title"] is True
        assert config["include_document_description"] is True

        result = parse_html(html, config)
        assert result.title == "Inside Sales Manager (m/w/d)"
        assert result.locations == ["Mannheim"]
        assert result.description == (
            "<p>VAG develops valves for water infrastructure.</p>"
            "<h3>Ihre Aufgaben</h3>"
            "<ul><li>Advise customers.</li><li>Prepare quotations.</li></ul>"
        )

    def test_rexx_portal7_keeps_full_body_when_location_metadata_is_absent(self):
        from src.core.scrapers.dom import can_handle, parse_html

        paragraphs = "".join(f"<p>Detail section {index}.</p>" for index in range(225))
        html = f"""
        <html><head>
          <title>Job offer Service Engineer at Jobportal</title>
          <meta name="generator" content="Rexx Recruitment - Portal7">
        </head><body>
          <div id="jobTplContainer" class="ck_content">
            {paragraphs}
            <h3>How to apply</h3>
            <p>Send the complete application.</p>
          </div>
        </body></html>
        """

        config = can_handle([html])
        assert config is not None
        result = parse_html(html, config)

        assert result.title == "Service Engineer"
        assert result.locations is None
        assert result.description is not None
        assert "Detail section 224." in result.description
        assert "Send the complete application." in result.description

    def test_georg_fischer_vag_czechia_config_keeps_complete_scoped_article(self):
        from src.core.scrapers.dom import parse_html
        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        row = next(item for item in rows if item["board_slug"] == "georg-fischer-vag-czechia")
        config = json.loads(row["scraper_config"])
        middle = "".join(f"<p>Responsibility {index}.</p>" for index in range(30))
        html = f"""
        <html><body>
          <nav><p>Navigation text.</p></nav>
          <div class="Content article">
            <h1>Process Engineer</h1>
            <p>Design reliable water infrastructure.</p>
            {middle}
            <h2>Benefits</h2>
            <p>Flexible working and professional development.</p>
            <h2>How to apply</h2>
            <p>Send your complete application to VAG.</p>
          </div>
          <footer><p>Unrelated footer content.</p></footer>
        </body></html>
        """

        result = parse_html(html, config)

        assert result.title == "Process Engineer"
        assert result.locations == ["Czechia"]
        assert result.description is not None
        assert "Responsibility 29." in result.description
        assert "Flexible working and professional development." in result.description
        assert "Send your complete application to VAG." in result.description
        assert "Unrelated footer content." not in result.description

    def test_solique_probe_accepts_class_tokens_and_single_quotes(self):
        from src.core.scrapers.dom import can_handle

        html = """
        <html><head><title>Dispatcher - 100%</title></head><body>
          <header><div class='branded job-title'>Dispatcher</div></header>
          <a href='https://live.solique.ch/acme/apply/id/123'>Apply</a>
          <div class='intro'>Introduction</div>
          <div class='layout tasks-profile-wrapper expanded'>Tasks</div>
          <h3 class='contact-title'>Contact</h3>
          <div class='location'>Zurich</div>
        </body></html>
        """

        assert can_handle([html]) is not None

    async def test_missing_steps_returns_empty(self):
        """No 'steps' key → empty JobContent."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        with _patch_playwright(page):
            result = await scrape("https://example.com/job/1", {}, httpx.AsyncClient())
        assert result == JobContent()

    async def test_static_document_fallback_parses_pdf(self):
        from src.core.scrapers.dom import scrape

        pdf = _make_document_pdf("Consultant Role")

        def handler(request):
            return httpx.Response(200, content=pdf)

        config = {
            "render": False,
            "steps": [{"tag": "h1", "field": "title"}],
            "document_fallback": {"pdf": {"title_source": "text"}},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/download/42", config, client)

        assert result.title == "Consultant Role"
        assert result.description == "<p>Consultant Role</p>"

    async def test_static_document_fallback_fetches_fingerprinted_url_unchanged(self):
        from src.core.scrapers.dom import scrape

        pdf = _make_document_pdf("Validator Identity Role")
        url = "https://example.com/download/job.pdf?_jobseek_fp=0123456789abcdef"
        requested_urls: list[str] = []

        def handler(request):
            requested_urls.append(str(request.url))
            return httpx.Response(200, content=pdf, request=request)

        config = {
            "steps": [{"tag": "h1", "field": "title"}],
            "document_fallback": {"pdf": {"title_source": "text"}},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(url, config, client)

        assert requested_urls == [url]
        assert result.title == "Validator Identity Role"

    async def test_static_document_fallback_parses_docx_with_format_defaults(self):
        from src.core.scrapers.dom import scrape

        docx = _make_document_docx()

        def handler(request):
            return httpx.Response(200, content=docx)

        config = {
            "render": False,
            "steps": [{"tag": "h1", "field": "title"}],
            "document_fallback": {
                "docx": {
                    "title_source": "text",
                    "defaults": {
                        "locations": ["Remote"],
                        "job_location_type": "remote",
                    },
                }
            },
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/download/43", config, client)

        assert result.title == "Strategic Communications Officer"
        assert result.locations == ["Remote"]
        assert result.job_location_type == "remote"
        assert result.description is not None
        assert "Lead conservation communications worldwide." in result.description

    async def test_static_document_location_pattern_wins_over_format_defaults(self):
        from src.core.scrapers.dom import scrape

        docx = _make_document_docx(location="Zurich")

        def handler(request):
            return httpx.Response(200, content=docx)

        config = {
            "render": False,
            "steps": [{"tag": "h1", "field": "title"}],
            "document_fallback": {
                "docx": {
                    "title_source": "text",
                    "location_pattern": r"(?m)^Location:\s*(.+)$",
                    "defaults": {"locations": ["Remote"]},
                }
            },
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/download/44", config, client)

        assert result.locations == ["Zurich"]

    async def test_static_document_fallback_keeps_html_step_extraction(self):
        from src.core.scrapers.dom import scrape

        def handler(request):
            return httpx.Response(200, text=FIXTURE_HTML)

        config = {
            "render": False,
            "steps": [{"tag": "h1", "field": "title"}],
            "document_fallback": {"pdf": {}, "docx": {}},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/job/1", config, client)

        assert result.title == "Software Engineer"

    @pytest.mark.parametrize(
        "document_fallback",
        [True, {"zip": {}}, {"pdf": "not-an-object"}],
    )
    async def test_document_fallback_rejects_invalid_config(self, document_fallback):
        from src.core.scrapers.dom import scrape

        config = {
            "steps": [{"tag": "h1", "field": "title"}],
            "document_fallback": document_fallback,
        }
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="document_fallback"):
                await scrape("https://example.com/job/1", config, client)

    async def test_document_fallback_rejects_rendered_mode(self):
        from src.core.scrapers.dom import scrape

        config = {
            "render": True,
            "steps": [{"tag": "h1", "field": "title"}],
            "document_fallback": {"pdf": {}},
        }
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="render=false"):
                await scrape("https://example.com/job/1", config, client)

    @pytest.mark.parametrize("value", ["true", 1, None])
    async def test_same_origin_redirects_rejects_non_boolean_config(self, value):
        from src.core.scrapers.dom import scrape

        config = {
            "steps": [{"tag": "h1", "field": "title"}],
            "same_origin_redirects": value,
        }
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="must be a boolean"):
                await scrape("https://example.com/job/1", config, client)

    @pytest.mark.parametrize("action_config", [{"render": True}, {"actions": [{"wait": 1}]}])
    async def test_same_origin_redirects_rejects_rendered_mode(self, action_config):
        from src.core.scrapers.dom import scrape

        config = {
            **action_config,
            "steps": [{"tag": "h1", "field": "title"}],
            "same_origin_redirects": True,
        }
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="requires render=false"):
                await scrape("https://example.com/job/1", config, client)

    async def test_same_origin_redirect_extracts_without_replacing_source_identity(self):
        from src.core.scrapers.dom import scrape

        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            if request.url.path.endswith("/Description"):
                return httpx.Response(302, headers={"Location": "Description/2"})
            return httpx.Response(200, text="<html><body><h1>Stable job</h1></body></html>")

        stable_url = "https://jobs.example/Vacancies/42/Description"
        config = {
            "steps": [{"tag": "h1", "field": "title"}],
            "same_origin_redirects": True,
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(stable_url, config, client)

        assert result.title == "Stable job"
        assert stable_url == "https://jobs.example/Vacancies/42/Description"
        assert requested == [stable_url, f"{stable_url}/2"]

    async def test_title_extraction(self):
        """Step with tag: h1 extracts the title."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        config = {"render": True, "steps": [{"tag": "h1", "field": "title"}]}
        with _patch_playwright(page):
            result = await scrape("https://example.com/job/1", config, httpx.AsyncClient())
        assert result.title == "Software Engineer"

    def test_scope_limits_static_parser_to_matching_container(self):
        from src.core.scrapers.dom import parse_html

        html = """
        <html><body><h2>Navigation title</h2>
        <div id="job-content"><h2>Scoped role</h2><p>Scoped description.</p></div>
        <h2>Footer title</h2></body></html>
        """
        result = parse_html(
            html,
            {
                "scope": "#job-content",
                "steps": [
                    {"tag": "h2", "field": "title"},
                    {"tag": "p", "field": "description", "html": True, "stop_count": 1},
                ],
            },
        )
        assert result.title == "Scoped role"
        assert result.description == "<p>Scoped description.</p>"

    def test_scope_extracts_semantic_noscript_fallback(self):
        from src.core.scrapers.dom import parse_html

        html = """
        <div class="job-page">
          <div id="javascript-widget"></div>
          <noscript>
            <h1>Apprentice Chef</h1>
            <h2>Job description</h2>
            <p>Learn professional kitchen operations.</p>
            <h2>Work location</h2>
            <p>Passugg, Switzerland</p>
          </noscript>
        </div>
        """
        result = parse_html(
            html,
            {
                "scope": ".job-page noscript",
                "steps": [
                    {"tag": "h1", "field": "title"},
                    {
                        "tag": "h2",
                        "text": "Job description",
                        "field": "description",
                        "html": True,
                        "stop": "Work location",
                    },
                    {
                        "tag": "h2",
                        "text": "Work location",
                        "offset": 1,
                        "field": "locations",
                    },
                ],
            },
        )

        assert result.title == "Apprentice Chef"
        assert "Learn professional kitchen operations" in (result.description or "")
        assert result.locations == ["Passugg, Switzerland"]

    @pytest.mark.parametrize("scope", ["", 123, "#missing"])
    def test_invalid_or_missing_scope_fails_closed(self, scope):
        from src.core.scrapers.dom import parse_html

        with pytest.raises(ValueError, match="scope"):
            parse_html(
                '<div id="job-content"><h2>Role</h2></div>',
                {"scope": scope, "steps": [{"tag": "h2", "field": "title"}]},
            )

    @pytest.mark.parametrize(
        "config",
        [
            {"scope": "#job-content", "include_document_title": "yes"},
            {"include_document_title": True},
            {"scope": "#job-content", "include_document_description": "yes"},
            {"include_document_description": True},
        ],
    )
    def test_document_metadata_options_require_boolean_and_scope(self, config):
        from src.core.scrapers.dom import parse_html

        with pytest.raises(ValueError, match="document|include_document"):
            parse_html(
                '<div id="job-content"><h2>Role</h2></div>',
                {**config, "steps": [{"tag": "h2", "field": "title"}]},
            )

    async def test_title_extraction_from_semantic_header(self):
        """Animated headings nested in a page header remain extractable."""
        from src.core.scrapers.dom import scrape

        html = """
        <html><body>
        <header>
          <nav>Menu</nav>
          <h1><span><span>H</span><span>e</span><span>a</span><span>d</span></span>
          <span>of Sales</span></h1>
        </header>
        <p>Lead the sales team.</p>
        </body></html>
        """
        page = _make_page(html)
        config = {"render": True, "steps": [{"tag": "h1", "field": "title"}]}
        with _patch_playwright(page):
            result = await scrape("https://example.com/job/1", config, httpx.AsyncClient())
        assert result.title == "Head of Sales"

    async def test_description_html(self):
        """html: true step produces an HTML fragment."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        config = {
            "render": True,
            "steps": [
                {
                    "text": "About the role",
                    "field": "description",
                    "stop": "Requirements",
                    "html": True,
                },
            ],
        }
        with _patch_playwright(page):
            result = await scrape("https://example.com/job/1", config, httpx.AsyncClient())
        assert result.description is not None
        assert "<" in result.description  # contains HTML tags

    async def test_location_single(self):
        """Singular 'location' field gets wrapped into locations list."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        config = {
            "render": True,
            "steps": [
                {"text": "Location", "offset": 1, "field": "location"},
            ],
        }
        with _patch_playwright(page):
            result = await scrape("https://example.com/job/1", config, httpx.AsyncClient())
        assert result.locations is not None
        assert isinstance(result.locations, list)
        assert "London, UK" in result.locations[0]

    async def test_locations_split(self):
        """split step produces a list."""
        from src.core.scrapers.dom import scrape

        html = "<html><body><h2>Locations</h2><p>London | Berlin | Remote</p></body></html>"
        page = _make_page(html)
        config = {
            "render": True,
            "steps": [
                {"text": "Locations", "offset": 1, "field": "locations", "split": " | "},
            ],
        }
        with _patch_playwright(page):
            result = await scrape("https://example.com/job/1", config, httpx.AsyncClient())
        assert result.locations == ["London", "Berlin", "Remote"]

    async def test_metadata_fields(self):
        """metadata.team goes to JobContent.metadata."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        config = {
            "render": True,
            "steps": [
                {"text": "Team", "offset": 1, "field": "metadata.team"},
            ],
        }
        with _patch_playwright(page):
            result = await scrape("https://example.com/job/1", config, httpx.AsyncClient())
        assert result.metadata is not None
        assert result.metadata["team"] == "Platform"

    async def test_qualifications_list(self):
        """List field extraction."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        config = {
            "render": True,
            "steps": [
                {"text": "Requirements", "field": "qualifications", "stop_count": 3, "split": "\n"},
            ],
        }
        with _patch_playwright(page):
            result = await scrape("https://example.com/job/1", config, httpx.AsyncClient())
        assert result.extras is not None
        assert isinstance(result.extras.get("qualifications"), list)

    async def test_browser_config_passed(self):
        """wait/timeout forwarded to navigate."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        config = {
            "render": True,
            "wait": "load",
            "timeout": 5000,
            "steps": [{"tag": "h1", "field": "title"}],
        }
        with _patch_playwright(page):
            await scrape("https://example.com/job/1", config, httpx.AsyncClient())
        page.goto.assert_awaited_once_with(
            "https://example.com/job/1", wait_until="load", timeout=5000
        )

    async def test_actions_executed(self):
        """Action pipeline runs before extraction."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        config = {
            "actions": [{"action": "dismiss_overlays"}],
            "steps": [{"tag": "h1", "field": "title"}],
        }
        with _patch_playwright(page):
            await scrape("https://example.com/job/1", config, httpx.AsyncClient())
        # dismiss_overlays calls page.evaluate
        page.evaluate.assert_awaited_once()

    async def test_static_fetch_title(self):
        """render: false uses HTTP instead of Playwright."""
        from src.core.scrapers.dom import scrape

        page_html = "<html><body><h1>Static Title</h1></body></html>"

        def handler(request):
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://example.com/job/1",
                {"render": False, "steps": [{"tag": "h1", "field": "title"}]},
                client,
            )
        assert result.title == "Static Title"

    async def test_static_fetch_uses_allowlisted_public_request_headers(self):
        from src.core.scrapers.dom import scrape

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["user-agent"] == "jobseek-crawler (+https://jseek.co/)"
            assert "authorization" not in request.headers
            return httpx.Response(200, text="<html><body><h1>Public role</h1></body></html>")

        config = {
            "request_headers": {"User-Agent": "jobseek-crawler (+https://jseek.co/)"},
            "steps": [{"tag": "h1", "field": "title"}],
        }
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer private"},
        ) as client:
            result = await scrape("https://example.com/job/1", config, client)

        assert result.title == "Public role"

    @pytest.mark.parametrize("render_config", [{"render": True}, {"actions": []}])
    async def test_public_request_headers_are_static_only(self, render_config):
        from src.core.scrapers.dom import scrape

        config = {
            **render_config,
            "request_headers": {"User-Agent": "jobseek-crawler (+https://jseek.co/)"},
            "steps": [{"tag": "h1", "field": "title"}],
        }
        if "actions" in render_config:
            config["actions"] = [{"action": "dismiss_overlays"}]

        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="only when render=false"):
                await scrape("https://example.com/job/1", config, client)

    async def test_static_avature_406_retries_and_recovers(self):
        """Avature uses bursty 406s as a throttle on otherwise-live pages."""
        from src.core.scrapers.dom import scrape

        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(406, text="temporarily unavailable")
            return httpx.Response(200, text="<html><body><h1>Recovered</h1></body></html>")

        url = "https://jobs.totalenergies.com/en_US/careers/JobDetail/Role/123"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                url,
                {"render": False, "steps": [{"tag": "h1", "field": "title"}]},
                client,
            )

        assert calls["n"] == 2
        assert result.title == "Recovered"

    async def test_static_configured_status_retry_covers_branded_avature_route(self):
        """A monitor preset can retry Avature custom hosts without URL guessing."""
        from src.core.scrapers.dom import scrape

        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(406, text="temporarily unavailable")
            return httpx.Response(200, text="<html><body><h2>Recovered</h2></body></html>")

        url = "https://jobs.example.com/en_US/jobsGlobal/FolderDetail/Role/123"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                url,
                {
                    "render": False,
                    "retry_statuses": {"406": 2},
                    "steps": [{"tag": "h2", "field": "title"}],
                },
                client,
            )

        assert calls["n"] == 2
        assert result.title == "Recovered"

    async def test_static_fetch_multiple_fields(self):
        """render: false extracts multiple fields from static HTML."""
        from src.core.scrapers.dom import scrape

        page_html = """<html><body>
        <h1>Data Engineer</h1>
        <div class="location">
        <h2>Location</h2>
        <p>Berlin, Germany</p>
        </div>
        <div class="desc">
        <h2>About</h2>
        <p>Build data pipelines.</p>
        </div>
        </body></html>"""

        def handler(request):
            return httpx.Response(200, text=page_html)

        config = {
            "render": False,
            "steps": [
                {"tag": "h1", "field": "title"},
                {"text": "Location", "offset": 1, "field": "location"},
                {"text": "About", "offset": 1, "field": "description"},
            ],
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/job/1", config, client)
        assert result.title == "Data Engineer"
        assert result.locations is not None
        assert "Berlin" in result.locations[0]
        assert result.description is not None
        assert "pipelines" in result.description

    async def test_static_fetch_honors_configured_legacy_encoding(self):
        """Legacy ATS pages can override an unsupported charset alias."""
        from src.core.scrapers.dom import scrape

        page_html = """<html><body>
        <h4 class="jobnames">ソフトウェアエンジニア</h4>
        <table class="jobtable">
          <tr><td>職務内容</td><td>製品を開発します</td></tr>
          <tr><td>勤務地</td><td>東京</td></tr>
        </table></body></html>"""

        def handler(request):
            return httpx.Response(
                200,
                content=page_html.encode("euc_jp"),
                headers={"content-type": "text/html; charset=CP51932"},
                request=request,
            )

        config = {
            "encoding": "euc_jp",
            "steps": [
                {"tag": "h4", "attr": "class=jobnames", "field": "title"},
                {"text": "職務内容", "offset": 1, "field": "description"},
                {"text": "勤務地", "offset": 1, "field": "location", "from": 0},
            ],
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://example.jposting.net/u/job.phtml?job_code=13",
                config,
                client,
            )

        assert result.title == "ソフトウェアエンジニア"
        assert result.description == "製品を開発します"
        assert result.locations == ["東京"]

    async def test_static_fetch_no_steps_returns_empty(self):
        """render: false with no steps returns empty JobContent."""
        from src.core.scrapers.dom import scrape

        result = await scrape(
            "https://example.com/job/1",
            {"render": False},
            httpx.AsyncClient(),
        )
        assert result == JobContent()

    async def test_actions_override_render_false(self):
        """actions + render: false overrides to render: true with warning."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        config = {
            "render": False,
            "actions": [{"action": "dismiss_overlays"}],
            "steps": [{"tag": "h1", "field": "title"}],
        }
        with _patch_playwright(page):
            result = await scrape("https://example.com/job/1", config, httpx.AsyncClient())
        assert result.title == "Software Engineer"
        # Confirms Playwright was used (page.evaluate called by dismiss_overlays)
        page.evaluate.assert_awaited_once()

    async def test_static_verification_challenge_raises(self):
        """A 200 verification shell is transient, not an empty job detail."""
        from src.core.monitors.dom import BotChallengeError
        from src.core.scrapers.dom import scrape

        challenge = (
            "<html><head><title>Verifying...</title></head>"
            "<body>Please wait while your request is being verified...</body></html>"
        )

        def handler(request):
            return httpx.Response(200, text=challenge, request=request)

        config = {"steps": [{"tag": "h1", "field": "title"}]}
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(BotChallengeError, match="proxy transport"):
                await scrape("https://blocked.example/job/1", config, client)

    async def test_rendered_incapsula_interstitial_retries_once(self):
        """A transient full-page Incapsula iframe gets one fresh context."""
        from src.core.scrapers.dom import scrape

        challenge = (
            '<html><body><iframe id="main-iframe" '
            'src="/_Incapsula_Resource?CWUDNSAI=23&incident_id=6110">'
            "</iframe></body></html>"
        )
        page = _make_page()
        page.content = AsyncMock(side_effect=[challenge, FIXTURE_HTML])
        config = {"render": True, "steps": [{"tag": "h1", "field": "title"}]}

        with _patch_playwright(page):
            result = await scrape("https://example.com/job/1", config, httpx.AsyncClient())

        assert result.title == "Software Engineer"
        assert page.content.await_count == 2

    async def test_playwright_import_error(self):
        """Raises RuntimeError when playwright is not installed."""
        from src.core.scrapers.dom import scrape

        config = {"render": True, "steps": [{"tag": "h1", "field": "title"}]}
        http = httpx.AsyncClient()

        real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def fake_import(name, *args, **kwargs):
            if name == "playwright.async_api":
                raise ImportError("no playwright")
            return real_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=fake_import),
            pytest.raises(RuntimeError, match="playwright is required"),
        ):
            await scrape("https://example.com/job/1", config, http)


# ---------------------------------------------------------------------------
# PeopleStrong (Larsen & Toubro / Bajaj Finserv) — issue #2952
#
# The peoplestrong career portal renders job detail pages as a single-page
# Angular app behind Incapsula. Static HTML is just the empty ``<app-root>``
# shell, so the dom config MUST set ``render: true`` for Playwright to
# execute the JS. The rendered DOM uses ``<h2 data-testid=...>`` for the job
# title (NOT ``<h1>``) — early dom configs that keyed off ``h1`` matched
# nothing and produced 0 descriptions across thousands of postings.
#
# These tests exercise the SHARED config now used by both larsen-toubro and
# bajaj-finserv against captured-from-prod fixtures.
# ---------------------------------------------------------------------------


# Shared dom config used in boards.csv for both peoplestrong companies.
# Kept here so any change to the live config is mirrored by the tests.
PEOPLESTRONG_DOM_CONFIG = {
    "render": True,
    "wait": "networkidle",
    "enrich": ["description"],
    "steps": [
        {
            "tag": "h2",
            "attr": "data-testid=job-detail-top-h2-page-1",
            "field": "title",
        },
        {
            "text": "Job Description",
            "offset": 1,
            "field": "description",
            "stop": "expand_less",
            "html": True,
            "optional": True,
        },
    ],
}


class TestPeopleStrongDomScraper:
    """Verify the dom scraper extracts title + description from a captured
    rendered peoplestrong detail page using the boards.csv config.
    """

    def test_larsen_toubro_extraction(self):
        """L&T detail page yields title + non-trivial HTML description."""
        from src.core.scrapers.dom import parse_html

        html = (FIXTURES_DIR / "peoplestrong_larsen_toubro.html").read_text()
        result = parse_html(html, PEOPLESTRONG_DOM_CONFIG)

        assert result.title == "Assistant Manager - Strategic Sourcing"
        assert result.description is not None
        # Description should be non-trivial HTML with the expected structure
        assert len(result.description) > 200
        assert "<ul>" in result.description
        assert "Strategic Sourcing" in result.description
        # The trailing 'expand_less' Material icon must NOT leak in
        assert "expand_less" not in result.description

    def test_bajaj_finserv_extraction(self):
        """Bajaj detail page yields title + non-trivial description.

        Bajaj's 'JOB DESCRIPTION' heading is uppercase; the matcher in
        walk_steps is case-insensitive so the same step config matches.
        """
        from src.core.scrapers.dom import parse_html

        html = (FIXTURES_DIR / "peoplestrong_bajaj_finserv.html").read_text()
        result = parse_html(html, PEOPLESTRONG_DOM_CONFIG)

        assert result.title == "Manager - Professional Loans"
        assert result.description is not None
        assert len(result.description) > 200
        assert "expand_less" not in result.description

    def test_old_h1_config_was_broken(self, recwarn):
        """Regression guard: the prior <h1>-based config extracts nothing
        from peoplestrong pages. Ensures we don't accidentally revert.
        """
        from src.core.scrapers.dom import parse_html

        old_config = {
            "steps": [
                {"tag": "h1", "field": "title"},
                {
                    "text": "Location",
                    "offset": 1,
                    "field": "location",
                    "optional": True,
                },
                {
                    "tag": "h1",
                    "offset": 1,
                    "field": "description",
                    "stop": "Apply",
                    "html": True,
                    "optional": True,
                },
            ]
        }
        html = (FIXTURES_DIR / "peoplestrong_larsen_toubro.html").read_text()
        result = parse_html(html, old_config)
        # Old config yields no title and no description — what the live
        # crawler observed before this fix. ``recwarn`` swallows the
        # expected ``step ... not found`` UserWarning.
        assert result.title is None
        assert result.description is None

    def test_peoplestrong_config_routes_to_browser_queue(self):
        """The dom config sets render: true, so workers must dispatch it
        to the browser queue (slim HTTP workers can't load Chromium).
        """
        from src.core.scrapers import scraper_needs_browser

        assert scraper_needs_browser("dom", PEOPLESTRONG_DOM_CONFIG) is True

    @pytest.mark.parametrize(
        "board_slug",
        ("bajaj-finserv-careers-ps-jobs", "larsen-toubro-careers"),
    )
    def test_peoplestrong_boards_declare_description_enrich(self, board_slug):
        """PeopleStrong listings are rich, so detail scrapes need explicit enrich."""
        from src.processing.scrape import _board_has_enrich
        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        by_slug = {row["board_slug"]: row for row in rows}
        row = by_slug.get(board_slug)

        assert row is not None, f"{board_slug!r} row missing from boards.csv"
        assert row.get("monitor_type") == "api_sniffer"
        assert row.get("scraper_type") == "dom"

        scraper_config = json.loads(row.get("scraper_config") or "{}")
        assert scraper_config == PEOPLESTRONG_DOM_CONFIG
        metadata = {
            "scraper_type": row.get("scraper_type"),
            "scraper_config": scraper_config,
        }
        assert _board_has_enrich(metadata) == ["description"]


# ---------------------------------------------------------------------------
# gone_url_pattern — issue #2963
#
# L'Oréal's careers site keeps stale URLs in its sitemap that 302-redirect
# to ``/jobs/Error`` once the upstream posting is removed. The dom scraper's
# selectors don't match the error page, so without help the pipeline burns
# three transient backoffs on each (``last_scraped_at`` updates, but
# ``description_r2_hash`` stays NULL) and lands at ``next_scrape_at IS NULL``,
# stranding the row as ``is_active=true`` indefinitely.
#
# ``gone_url_pattern`` checks the FINAL URL after redirects and raises
# ``HTTPStatusError(410)`` so the existing ``_is_permanent_gone`` classifier
# in ``processing/scrape.py`` tombstones on the first failure.
# ---------------------------------------------------------------------------


class TestDomGoneUrlPattern:
    async def test_render_path_raises_410_on_gone_redirect(self):
        """Render path: when ``page.url`` matches gone_url_pattern,
        scrape() raises ``httpx.HTTPStatusError`` with status 410."""
        from src.core.scrapers.dom import scrape

        page = _make_page("<html></html>")
        page.url = "https://careers.loreal.com/en_US/jobs/Error"
        config = {
            "render": True,
            "gone_url_pattern": "/jobs/Error(?:[/?]|$)",
            "steps": [{"tag": "h1", "field": "title"}],
        }
        with _patch_playwright(page), pytest.raises(httpx.HTTPStatusError) as exc_info:
            await scrape(
                "https://careers.loreal.com/jobs/JobDetail/Foo/123",
                config,
                httpx.AsyncClient(),
            )
        assert exc_info.value.response.status_code == 410

    async def test_render_path_skips_actions_on_gone_redirect(self):
        """When gone is detected, run_actions is skipped — actions can
        run an evaluate() pipeline that itself raises on the error page."""
        from src.core.scrapers.dom import scrape

        page = _make_page("<html></html>")
        page.url = "https://careers.loreal.com/en_US/jobs/Error"
        config = {
            "render": True,
            "gone_url_pattern": "/jobs/Error(?:[/?]|$)",
            "actions": [{"action": "dismiss_overlays"}],
            "steps": [{"tag": "h1", "field": "title"}],
        }
        with _patch_playwright(page), pytest.raises(httpx.HTTPStatusError):
            await scrape(
                "https://careers.loreal.com/jobs/JobDetail/Foo/123",
                config,
                httpx.AsyncClient(),
            )
        page.evaluate.assert_not_called()

    async def test_render_path_no_pattern_extracts_normally(self):
        """No gone_url_pattern config -> existing behaviour preserved."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        # Even on the error URL, with no pattern set we don't classify
        # as gone -- extraction proceeds normally (and would land on the
        # transient path via empty extraction, the legacy behaviour).
        page.url = "https://careers.loreal.com/en_US/jobs/Error"
        config = {
            "render": True,
            "steps": [{"tag": "h1", "field": "title"}],
        }
        with _patch_playwright(page):
            result = await scrape(
                "https://careers.loreal.com/jobs/JobDetail/Foo/123",
                config,
                httpx.AsyncClient(),
            )
        assert result.title == "Software Engineer"

    async def test_render_path_pattern_no_match_extracts_normally(self):
        """Pattern set, but final URL doesn't match -> extraction proceeds."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        page.url = "https://careers.loreal.com/en_US/jobs/JobDetail/Foo/123"
        config = {
            "render": True,
            "gone_url_pattern": "/jobs/Error(?:[/?]|$)",
            "steps": [{"tag": "h1", "field": "title"}],
        }
        with _patch_playwright(page):
            result = await scrape(
                "https://careers.loreal.com/jobs/JobDetail/Foo/123",
                config,
                httpx.AsyncClient(),
            )
        assert result.title == "Software Engineer"

    async def test_static_path_raises_410_on_gone_redirect(self):
        """Static HTTP path: when the final URL after follow_redirects
        matches gone_url_pattern, scrape() raises HTTPStatusError(410).

        The redirect chain may end on a 200 (rendered "this posting was
        removed" page), so status alone never reveals gone-ness on these
        hosts -- we must inspect the final URL.
        """
        from src.core.scrapers.dom import scrape

        config = {
            "render": False,
            "gone_url_pattern": "/jobs/Error(?:[/?]|$)",
            "steps": [{"tag": "h1", "field": "title"}],
        }

        # Patch httpx.AsyncClient.get to return a Response whose .url
        # reports the post-redirect error page. (httpx.MockTransport
        # always reports request.url as response.url, which would defeat
        # the test, so we patch the higher-level client method.)
        async def fake_get(self_client, url, **kwargs):
            final_req = httpx.Request("GET", "https://careers.loreal.com/en_US/jobs/Error")
            return httpx.Response(200, text="error page", request=final_req)

        with patch.object(httpx.AsyncClient, "get", new=fake_get):
            client = httpx.AsyncClient()
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await scrape(
                    "https://careers.loreal.com/jobs/JobDetail/Foo/123",
                    config,
                    client,
                )
        assert exc_info.value.response.status_code == 410

    async def test_static_path_no_match_extracts_normally(self):
        """Static path: final URL doesn't match -> 200 response is consumed
        normally and steps run."""
        from src.core.scrapers.dom import scrape

        page_html = "<html><body><h1>Real Job</h1></body></html>"

        def handler(request):
            return httpx.Response(200, text=page_html)

        config = {
            "render": False,
            "gone_url_pattern": "/jobs/Error(?:[/?]|$)",
            "steps": [{"tag": "h1", "field": "title"}],
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://careers.loreal.com/en_US/jobs/JobDetail/Foo/123",
                config,
                client,
            )
        assert result.title == "Real Job"

    async def test_invalid_regex_logs_and_does_not_raise(self):
        """A malformed gone_url_pattern is logged but does not break
        extraction -- the absent guard is preferable to an outage."""
        from src.core.scrapers.dom import scrape

        page = _make_page(FIXTURE_HTML)
        page.url = "https://careers.loreal.com/en_US/jobs/Error"
        config = {
            "render": True,
            "gone_url_pattern": "[unterminated",
            "steps": [{"tag": "h1", "field": "title"}],
        }
        with _patch_playwright(page):
            result = await scrape(
                "https://careers.loreal.com/jobs/JobDetail/Foo/123",
                config,
                httpx.AsyncClient(),
            )
        # Extraction proceeds despite the bad regex.
        assert result.title == "Software Engineer"

    def test_loreal_csv_config_pattern_matches_error_redirect(self):
        """Verify the live boards.csv config pattern actually matches the
        L'Oreal error redirect URL we observed in production probes."""
        import csv
        import re

        from src.shared.constants import DATA_DIR

        with open(DATA_DIR / "boards.csv") as f:
            for row in csv.DictReader(f):
                if row["board_slug"] == "loreal-careers":
                    cfg = json.loads(row["scraper_config"])
                    pat = cfg.get("gone_url_pattern")
                    assert pat, "loreal-careers must define gone_url_pattern"
                    # Empirically observed redirect chains (Hetzner egress,
                    # 2026-05-09): a removed posting 302s to /en_US/jobs/Error.
                    assert re.search(pat, "https://careers.loreal.com/en_US/jobs/Error")
                    assert re.search(pat, "https://careers.loreal.com/en_US/jobs/Error?x=1")
                    # Must NOT match a real posting URL.
                    assert not re.search(
                        pat,
                        "https://careers.loreal.com/en_US/jobs/JobDetail/Foo/123",
                    )
                    return
        raise AssertionError("loreal-careers row not found in boards.csv")


# ---------------------------------------------------------------------------
# Decathlon talentclue.com — kept as a live mirror of the boards.csv config
# so a future bulk-edit of the row gets caught by the suite.
# ---------------------------------------------------------------------------

DECATHLON_DOM_CONFIG = {
    "render": False,
    "steps": [
        {"tag": "title", "field": "title"},
        {
            "tag": "h2",
            "attr": "class=job-description__title",
            "field": "description",
            "html": True,
            "stop": "tienes perfil en",
            "optional": True,
        },
    ],
}


class TestDecathlonDomScraper:
    """Verify the dom scraper extracts title + description from a captured
    decathlon.talentclue.com (Drupal 7) detail page using the boards.csv
    config (#2952).

    Empirical findings that motivated the fix:

    1. The previous selectors used ``class~=job-page__header-title`` syntax
       — the dom scraper's attr matcher splits on ``=`` once, so it ended
       up looking for an attribute literally named ``class~`` and never
       matched anything (0/2 fields extracted on every posting).
    2. Even with the syntax corrected, the title <h1> sits inside a
       ``<header>`` element which ``flatten()`` filters as NOISE_TAGS,
       and the description container is not a single <div>. The working
       config reads the ``<title>`` tag for the headline and starts the
       description range at the first ``<h2 class="job-description__title">``
       block, stopping before the apply UI ("¿Ya tienes perfil en ?").
    """

    def test_decathlon_extraction_vendor(self):
        """Vendor posting yields title + non-trivial description."""
        from src.core.scrapers.dom import parse_html

        html = (FIXTURES_DIR / "decathlon_talentclue_jobpage.html").read_text()
        result = parse_html(html, DECATHLON_DOM_CONFIG)

        assert result.title == "VENDEDOR/A DEPORTES DE AGUA Decathlon Albacete"
        assert result.description is not None
        assert len(result.description) > 500
        # Description should include the company intro + actual job copy
        assert "DECATHLON" in result.description
        assert "Requisitos" in result.description
        # The apply-UI fragment must NOT leak into the description range
        assert "Autocompletar" not in result.description
        assert "Inscríbete" not in result.description

    def test_decathlon_extraction_taller(self):
        """A second posting (workshop technician) extracts cleanly too —
        guards against over-fitting the config to one job's structure."""
        from src.core.scrapers.dom import parse_html

        html = (FIXTURES_DIR / "decathlon_talentclue_jobpage_taller.html").read_text()
        result = parse_html(html, DECATHLON_DOM_CONFIG)

        assert result.title == "TÉCNICO/A DE TALLER Decathlon Lugones"
        assert result.description is not None
        assert len(result.description) > 500
        assert "DECATHLON" in result.description
        assert "Autocompletar" not in result.description

    def test_old_class_tilde_config_was_broken(self, recwarn):
        """Regression guard: the prior ``class~=...`` config extracts
        nothing from decathlon talentclue pages. Ensures we don't revert.
        """
        from src.core.scrapers.dom import parse_html

        old_config = {
            "steps": [
                {
                    "tag": "h1",
                    "attr": "class~=job-page__header-title",
                    "field": "title",
                },
                {
                    "tag": "div",
                    "attr": "class~=job-page__content",
                    "field": "description",
                    "html": True,
                },
            ]
        }
        html = (FIXTURES_DIR / "decathlon_talentclue_jobpage.html").read_text()
        result = parse_html(html, old_config)
        # 0/2 fields extracted — what the live crawler observed for 557
        # active postings before this fix (all has_content=false in
        # Typesense, all next_scrape_at=NULL in Postgres).
        assert result.title is None
        assert result.description is None

    def test_decathlon_config_routes_to_http_queue(self):
        """``render: false`` keeps the scraper on the slim HTTP worker —
        the talentclue page is fully rendered server-side (Drupal 7) so
        Playwright is unnecessary.
        """
        from src.core.scrapers import scraper_needs_browser

        assert scraper_needs_browser("dom", DECATHLON_DOM_CONFIG) is False


# ---------------------------------------------------------------------------
# ayuda-en-accion talentclue.com — sibling cluster of Decathlon (#2962/#2963).
# Same Drupal 7 talentclue page structure; the ``stop`` marker differs
# because ayuda-en-accion has no b4work integration, so the apply CTA
# falls through to the Spanish "Inscríbete" button.
# ---------------------------------------------------------------------------

AYUDA_EN_ACCION_DOM_CONFIG = {
    "render": False,
    "steps": [
        {"tag": "title", "field": "title"},
        {
            "tag": "h2",
            "attr": "class=job-description__title",
            "field": "description",
            "html": True,
            "stop": "Inscríbete",
            "optional": True,
        },
    ],
}


class TestAyudaEnAccionDomScraper:
    """Verify the dom scraper extracts title + description from a captured
    empleoayudaenaccion.talentclue.com (Drupal 7) detail page using the
    boards.csv config (sibling of #2952 / #2962, tracked in #2963).

    Same Drupal 7 talentclue layout as Decathlon: <h1 class="job-page__
    header-title"> sits inside a <header> (filtered as NOISE_TAGS by
    flatten()), and the description is a series of <h2 class=
    "job-description__title"> blocks rather than a single <div>.

    The original ``class~=...`` syntax was a parser bug — the dom scraper's
    attr matcher splits on the first ``=`` only, so it looked for an
    attribute literally named ``class~`` and never matched anything
    (0/2 fields extracted on every posting → 130 active rows with
    has_content=false in Typesense).
    """

    def test_ayuda_en_accion_extraction_consultoria(self):
        """Consultoría posting yields title + non-trivial description."""
        from src.core.scrapers.dom import parse_html

        html = (FIXTURES_DIR / "ayuda_en_accion_talentclue_jobpage.html").read_text()
        result = parse_html(html, AYUDA_EN_ACCION_DOM_CONFIG)

        assert result.title is not None
        assert "AGROTECH BOLIVIA 2026" in result.title
        assert result.description is not None
        assert len(result.description) > 500
        # Description should include the company intro + the actual job copy
        assert "Ayuda en Acción" in result.description
        # The apply UI / footer must NOT leak into the description range
        assert "Inscríbete" not in result.description
        assert "Mira el resto" not in result.description
        assert "Powered by" not in result.description

    def test_ayuda_en_accion_extraction_coordinador(self):
        """A second posting (Coordinador/a Territorial) extracts cleanly
        too — guards against over-fitting the config to one job's structure.
        """
        from src.core.scrapers.dom import parse_html

        html = (FIXTURES_DIR / "ayuda_en_accion_talentclue_jobpage_coord.html").read_text()
        result = parse_html(html, AYUDA_EN_ACCION_DOM_CONFIG)

        assert result.title == "Coordinador/a Territorial"
        assert result.description is not None
        assert len(result.description) > 500
        assert "Ayuda en Acción" in result.description
        assert "Inscríbete" not in result.description
        assert "Mira el resto" not in result.description

    def test_ayuda_en_accion_old_class_tilde_config_was_broken(self, recwarn):
        """Regression guard: the prior ``class~=...`` config extracts
        nothing from ayuda-en-accion talentclue pages. Ensures we don't
        revert the pre-#2963 boards.csv row.
        """
        from src.core.scrapers.dom import parse_html

        old_config = {
            "steps": [
                {
                    "tag": "h1",
                    "attr": "class~=job-page__header-title",
                    "field": "title",
                },
                {
                    "tag": "div",
                    "attr": "class~=job-page__content",
                    "field": "description",
                    "html": True,
                },
            ]
        }
        html = (FIXTURES_DIR / "ayuda_en_accion_talentclue_jobpage.html").read_text()
        result = parse_html(html, old_config)
        # 0/2 fields extracted — the live state for 130 active postings
        # before this fix (all has_content=false in Typesense).
        assert result.title is None
        assert result.description is None

    def test_ayuda_en_accion_config_routes_to_http_queue(self):
        """``render: false`` keeps the scraper on the slim HTTP worker —
        the talentclue page is fully rendered server-side (Drupal 7) so
        Playwright is unnecessary.
        """
        from src.core.scrapers import scraper_needs_browser

        assert scraper_needs_browser("dom", AYUDA_EN_ACCION_DOM_CONFIG) is False


# ---------------------------------------------------------------------------
# barcelona-activa talentclue.com — sibling cluster of Decathlon (#2962/#2963).
# Same Drupal 7 talentclue page structure; barcelona-activa runs the
# Catalan UI (Inscriu-t'hi) but exposes the b4work integration, so the
# Spanish "¿Ya tienes perfil en ?" line still appears on every page —
# we use ``stop: "tienes perfil en"`` to match Decathlon's anchor.
# ---------------------------------------------------------------------------

BARCELONA_ACTIVA_DOM_CONFIG = {
    "render": False,
    "steps": [
        {"tag": "title", "field": "title"},
        {
            "tag": "h2",
            "attr": "class=job-description__title",
            "field": "description",
            "html": True,
            "stop": "tienes perfil en",
            "optional": True,
        },
    ],
}


class TestBarcelonaActivaDomScraper:
    """Verify the dom scraper extracts title + description from a captured
    barcelonactiva.talentclue.com (Drupal 7) detail page using the
    boards.csv config (sibling of #2952 / #2962, tracked in #2963).

    Same root cause as Decathlon and ayuda-en-accion: the prior
    ``class~=...`` selector was a parser bug. 171 active rows had
    has_content=false in Typesense before this fix.
    """

    def test_barcelona_activa_extraction_monitor(self):
        """Monitor d'Oci Infantil posting yields title + non-trivial
        description (Catalan content)."""
        from src.core.scrapers.dom import parse_html

        html = (FIXTURES_DIR / "barcelona_activa_talentclue_jobpage.html").read_text()
        result = parse_html(html, BARCELONA_ACTIVA_DOM_CONFIG)

        assert result.title is not None
        assert "Monitor" in result.title
        assert result.description is not None
        assert len(result.description) > 500
        # Description should include the company intro
        assert "Barcelona Activa" in result.description
        # The apply UI / footer must NOT leak into the description range
        assert "tienes perfil en" not in result.description
        assert "Autocompletar" not in result.description
        assert "Powered by" not in result.description

    def test_barcelona_activa_extraction_admin(self):
        """A second posting (Administratiu/iva de recepció) extracts
        cleanly too — guards against over-fitting the config to one
        job's structure.
        """
        from src.core.scrapers.dom import parse_html

        html = (FIXTURES_DIR / "barcelona_activa_talentclue_jobpage_admin.html").read_text()
        result = parse_html(html, BARCELONA_ACTIVA_DOM_CONFIG)

        assert result.title is not None
        assert "Administratiu" in result.title
        assert result.description is not None
        assert len(result.description) > 500
        assert "Barcelona Activa" in result.description
        assert "tienes perfil en" not in result.description
        assert "Autocompletar" not in result.description

    def test_barcelona_activa_old_class_tilde_config_was_broken(self, recwarn):
        """Regression guard: the prior ``class~=...`` config extracts
        nothing from barcelona-activa talentclue pages. Ensures we don't
        revert the pre-#2963 boards.csv row.
        """
        from src.core.scrapers.dom import parse_html

        old_config = {
            "steps": [
                {
                    "tag": "h1",
                    "attr": "class~=job-page__header-title",
                    "field": "title",
                },
                {
                    "tag": "div",
                    "attr": "class~=job-page__content",
                    "field": "description",
                    "html": True,
                },
            ]
        }
        html = (FIXTURES_DIR / "barcelona_activa_talentclue_jobpage.html").read_text()
        result = parse_html(html, old_config)
        # 0/2 fields extracted — the live state for 171 active postings
        # before this fix (all has_content=false in Typesense).
        assert result.title is None
        assert result.description is None

    def test_barcelona_activa_config_routes_to_http_queue(self):
        """``render: false`` keeps the scraper on the slim HTTP worker —
        the talentclue page is fully rendered server-side (Drupal 7) so
        Playwright is unnecessary.
        """
        from src.core.scrapers import scraper_needs_browser

        assert scraper_needs_browser("dom", BARCELONA_ACTIVA_DOM_CONFIG) is False
