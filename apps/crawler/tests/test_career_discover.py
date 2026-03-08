"""Tests for career_discover module — HTML parsing and link extraction."""

from __future__ import annotations

from src.workspace.career_discover import (
    CareerPageCandidate,
    _dedup_candidates,
    _extract_links,
    _hubness_allows_candidate,
    _scan_ats_urls_in_html,
)

BASE = "https://example.com"


def _extract(html: str) -> list:
    return _extract_links(html, BASE)


# ── Career link extraction ─────────────────────────────────────────


class TestCareerPathDetection:
    def test_careers_path(self):
        html = """
        <html><head></head><body>
        <nav><a href="/careers">Careers</a></nav>
        </body></html>
        """
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) == 1
        assert career[0].url == "https://example.com/careers"

    def test_jobs_path(self):
        html = """
        <html><head></head><body>
        <a href="/jobs">Browse Jobs</a>
        </body></html>
        """
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) == 1
        assert career[0].url == "https://example.com/jobs"

    def test_join_us_path(self):
        html = """
        <html><head></head><body>
        <a href="/join-us">Join Us</a>
        </body></html>
        """
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) >= 1

    def test_open_positions_path(self):
        html = """
        <html><head></head><body>
        <a href="/open-positions">Open Positions</a>
        </body></html>
        """
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) >= 1

    def test_german_karriere_path(self):
        html = """
        <html><head></head><body>
        <a href="/karriere">Karriere</a>
        </body></html>
        """
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) >= 1

    def test_non_career_link_ignored(self):
        html = """
        <html><head></head><body>
        <a href="/about">About Us</a>
        <a href="/products">Products</a>
        </body></html>
        """
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) == 0


class TestMultilingualPaths:
    """Career path detection for EU languages."""

    def test_french_recrutement(self):
        html = '<html><head></head><body><a href="/recrutement">Recrutement</a></body></html>'
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) >= 1

    def test_french_offres_emploi(self):
        html = '<html><head></head><body><a href="/offres-d-emploi">Nos offres</a></body></html>'
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) >= 1

    def test_italian_lavora_con_noi(self):
        html = '<html><head></head><body><a href="/lavora-con-noi">Lavora con noi</a></body></html>'
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) >= 1

    def test_italian_posizioni_aperte(self):
        html = (
            "<html><head></head><body>"
            '<a href="/posizioni-aperte">Posizioni aperte</a></body></html>'
        )
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) >= 1

    def test_spanish_empleo(self):
        html = '<html><head></head><body><a href="/empleo">Empleo</a></body></html>'
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) >= 1

    def test_spanish_trabaja_con_nosotros(self):
        html = (
            "<html><head></head><body>"
            '<a href="/trabaja-con-nosotros">Trabaja con nosotros</a></body></html>'
        )
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) >= 1

    def test_dutch_vacatures(self):
        html = '<html><head></head><body><a href="/vacatures">Vacatures</a></body></html>'
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) >= 1

    def test_dutch_werken_bij(self):
        html = '<html><head></head><body><a href="/werken-bij">Werken bij</a></body></html>'
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) >= 1

    def test_portuguese_vagas(self):
        html = '<html><head></head><body><a href="/vagas">Vagas</a></body></html>'
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) >= 1

    def test_swedish_lediga_jobb(self):
        html = '<html><head></head><body><a href="/lediga-jobb">Lediga jobb</a></body></html>'
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) >= 1

    def test_polish_kariera(self):
        html = '<html><head></head><body><a href="/kariera">Kariera</a></body></html>'
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) >= 1

    def test_polish_oferty_pracy(self):
        html = '<html><head></head><body><a href="/oferty-pracy">Oferty pracy</a></body></html>'
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) >= 1

    def test_german_stellenangebote_text(self):
        """German keyword detected from anchor text, not just path."""
        html = '<html><head></head><body><a href="/team">Stellenangebote</a></body></html>'
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) >= 1

    def test_french_rejoignez_nous_text(self):
        html = '<html><head></head><body><a href="/team">Rejoignez-nous</a></body></html>'
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) >= 1

    def test_italian_unisciti_text(self):
        html = '<html><head></head><body><a href="/team">Unisciti a noi</a></body></html>'
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) >= 1


class TestCareerTextDetection:
    def test_careers_text(self):
        html = """
        <html><head></head><body>
        <a href="/company/work">Careers</a>
        </body></html>
        """
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) == 1
        assert career[0].text == "Careers"

    def test_join_our_team_text(self):
        html = """
        <html><head></head><body>
        <a href="/team">Join our team</a>
        </body></html>
        """
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) == 1

    def test_were_hiring_text(self):
        html = """
        <html><head></head><body>
        <a href="/company/hiring">We're hiring</a>
        </body></html>
        """
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) == 1

    def test_open_positions_text(self):
        html = """
        <html><head></head><body>
        <footer><a href="/work">Open positions</a></footer>
        </body></html>
        """
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) == 1


# ── Context scoring ────────────────────────────────────────────────


class TestContextScoring:
    def test_nav_link_scores_high(self):
        html = """
        <html><head></head><body>
        <nav><a href="/careers">Careers</a></nav>
        </body></html>
        """
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) == 1
        assert career[0].base_score >= 0.85
        assert career[0].context == "nav"

    def test_header_link_scores_high(self):
        html = """
        <html><head></head><body>
        <header><a href="/careers">Careers</a></header>
        </body></html>
        """
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) == 1
        assert career[0].base_score >= 0.85
        assert career[0].context == "header"

    def test_footer_link_scores_lower(self):
        html = """
        <html><head></head><body>
        <footer><a href="/careers">Careers</a></footer>
        </body></html>
        """
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) == 1
        assert career[0].base_score == 0.65
        assert career[0].context == "footer"

    def test_body_link_scores_lowest(self):
        html = """
        <html><head></head><body>
        <div><a href="/careers">Careers</a></div>
        </body></html>
        """
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) == 1
        assert career[0].base_score == 0.55
        assert career[0].context == "body"


# ── ATS embed detection ───────────────────────────────────────────


class TestAtsEmbed:
    def test_greenhouse_link(self):
        html = """
        <html><head></head><body>
        <a href="https://boards.greenhouse.io/acme">Jobs at Acme</a>
        </body></html>
        """
        links = _extract(html)
        ats = [lnk for lnk in links if lnk.source == "ats_embed"]
        assert len(ats) >= 1
        assert any("boards.greenhouse.io/acme" in lnk.url for lnk in ats)

    def test_lever_link(self):
        html = """
        <html><head></head><body>
        <a href="https://jobs.lever.co/acme">View openings</a>
        </body></html>
        """
        links = _extract(html)
        ats = [lnk for lnk in links if lnk.source == "ats_embed"]
        assert len(ats) >= 1
        assert any("jobs.lever.co/acme" in lnk.url for lnk in ats)

    def test_ashby_link(self):
        html = """
        <html><head></head><body>
        <a href="https://jobs.ashbyhq.com/acme">Careers</a>
        </body></html>
        """
        links = _extract(html)
        ats = [lnk for lnk in links if lnk.source == "ats_embed"]
        assert len(ats) >= 1
        assert any("jobs.ashbyhq.com/acme" in lnk.url for lnk in ats)

    def test_greenhouse_iframe(self):
        html = """
        <html><head></head><body>
        <iframe src="https://boards.greenhouse.io/acme"></iframe>
        </body></html>
        """
        links = _extract(html)
        ats = [lnk for lnk in links if lnk.source == "ats_embed"]
        assert len(ats) >= 1
        assert any("boards.greenhouse.io/acme" in lnk.url for lnk in ats)

    def test_ats_in_nav_scores_highest(self):
        html = """
        <html><head></head><body>
        <nav><a href="https://boards.greenhouse.io/acme">Jobs</a></nav>
        </body></html>
        """
        links = _extract(html)
        ats = [lnk for lnk in links if lnk.source == "ats_embed"]
        assert len(ats) >= 1
        assert ats[0].base_score >= 0.95

    def test_recruitee_domain(self):
        html = """
        <html><head></head><body>
        <a href="https://acme.recruitee.com/o">Jobs</a>
        </body></html>
        """
        links = _extract(html)
        ats = [lnk for lnk in links if lnk.source == "ats_embed"]
        assert len(ats) >= 1
        assert any("acme.recruitee.com" in lnk.url for lnk in ats)

    def test_personio_domain(self):
        html = """
        <html><head></head><body>
        <a href="https://acme.jobs.personio.de/job/12345">Job</a>
        </body></html>
        """
        links = _extract(html)
        ats = [lnk for lnk in links if lnk.source == "ats_embed"]
        assert len(ats) >= 1
        assert any("acme.jobs.personio.de" in lnk.url for lnk in ats)

    def test_workday_domain(self):
        html = """
        <html><head></head><body>
        <a href="https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite">Jobs</a>
        </body></html>
        """
        links = _extract(html)
        ats = [lnk for lnk in links if lnk.source == "ats_embed"]
        assert len(ats) >= 1
        assert any("myworkdayjobs.com" in lnk.url for lnk in ats)

    def test_smartrecruiters_domain(self):
        html = """
        <html><head></head><body>
        <a href="https://jobs.smartrecruiters.com/acme">Careers</a>
        </body></html>
        """
        links = _extract(html)
        ats = [lnk for lnk in links if lnk.source == "ats_embed"]
        assert len(ats) >= 1
        assert any("smartrecruiters.com/acme" in lnk.url for lnk in ats)

    def test_workable_domain(self):
        html = """
        <html><head></head><body>
        <a href="https://apply.workable.com/acme">Careers</a>
        </body></html>
        """
        links = _extract(html)
        ats = [lnk for lnk in links if lnk.source == "ats_embed"]
        assert len(ats) >= 1
        assert any("apply.workable.com/acme" in lnk.url for lnk in ats)

    def test_pinpoint_domain(self):
        html = """
        <html><head></head><body>
        <a href="https://acme.pinpointhq.com/en/jobs">Careers</a>
        </body></html>
        """
        links = _extract(html)
        ats = [lnk for lnk in links if lnk.source == "ats_embed"]
        assert len(ats) >= 1
        assert any("acme.pinpointhq.com" in lnk.url for lnk in ats)

    def test_rippling_domain(self):
        html = """
        <html><head></head><body>
        <a href="https://ats.rippling.com/acme/jobs">Careers</a>
        </body></html>
        """
        links = _extract(html)
        ats = [lnk for lnk in links if lnk.source == "ats_embed"]
        assert len(ats) >= 1
        assert any("ats.rippling.com/acme" in lnk.url for lnk in ats)


# ── Raw HTML ATS scanning ─────────────────────────────────────────


class TestRawAtsScanning:
    def test_ats_url_in_script(self):
        html = """
        <html><head></head><body>
        <script>var boardUrl = "https://boards.greenhouse.io/stripe";</script>
        </body></html>
        """
        found = _scan_ats_urls_in_html(html)
        assert len(found) >= 1
        assert any("boards.greenhouse.io/stripe" in f.url for f in found)

    def test_ats_url_in_comment(self):
        html = """
        <html><head></head><body>
        <!-- Powered by https://jobs.lever.co/company -->
        </body></html>
        """
        found = _scan_ats_urls_in_html(html)
        assert len(found) >= 1
        assert any("jobs.lever.co/company" in f.url for f in found)

    def test_deduplicates_same_url(self):
        html = """
        <html><head></head><body>
        <a href="https://boards.greenhouse.io/acme">Jobs</a>
        <script>var url = "https://boards.greenhouse.io/acme";</script>
        </body></html>
        """
        found = _scan_ats_urls_in_html(html)
        urls = [f.url for f in found]
        # Should be deduplicated
        assert len(set(urls)) == len(urls)


# ── Dedup ──────────────────────────────────────────────────────────


class TestDedup:
    def test_same_monitor_and_token_deduped(self):
        candidates = [
            CareerPageCandidate(
                url="https://boards.greenhouse.io/stripe",
                source="ats_embed",
                monitor_type="greenhouse",
                monitor_config={"token": "stripe", "jobs": 42},
                score=0.95,
                comment="Greenhouse API",
            ),
            CareerPageCandidate(
                url="https://boards.greenhouse.io/stripe",
                source="blind_probe",
                monitor_type="greenhouse",
                monitor_config={"token": "stripe", "jobs": 42},
                score=0.50,
                comment="Greenhouse API",
            ),
        ]
        result = _dedup_candidates(candidates)
        assert len(result) == 1
        assert result[0].score == 0.95  # Keeps higher score

    def test_different_monitors_kept(self):
        candidates = [
            CareerPageCandidate(
                url="https://boards.greenhouse.io/acme",
                source="ats_embed",
                monitor_type="greenhouse",
                monitor_config={"token": "acme"},
                score=0.95,
                comment="Greenhouse",
            ),
            CareerPageCandidate(
                url="https://jobs.ashbyhq.com/acme",
                source="blind_probe",
                monitor_type="ashby",
                monitor_config={"token": "acme"},
                score=0.50,
                comment="Ashby",
            ),
        ]
        result = _dedup_candidates(candidates)
        assert len(result) == 2


# ── URL resolution ─────────────────────────────────────────────────


class TestUrlResolution:
    def test_relative_career_link_resolved(self):
        html = """
        <html><head></head><body>
        <a href="/careers">Careers</a>
        </body></html>
        """
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) == 1
        assert career[0].url == "https://example.com/careers"

    def test_absolute_career_link_preserved(self):
        html = """
        <html><head></head><body>
        <a href="https://careers.example.com/jobs">Jobs</a>
        </body></html>
        """
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) == 1
        assert career[0].url == "https://careers.example.com/jobs"


# ── Filtering ──────────────────────────────────────────────────────


class TestFiltering:
    def test_head_links_ignored(self):
        """Links in <head> should not be extracted."""
        html = """
        <html><head>
        <link rel="canonical" href="https://example.com/careers">
        </head><body></body></html>
        """
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) == 0

    def test_javascript_href_ignored(self):
        html = """
        <html><head></head><body>
        <a href="javascript:void(0)">Careers</a>
        </body></html>
        """
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        assert len(career) == 0

    def test_empty_html(self):
        links = _extract("<html><head></head><body></body></html>")
        assert len(links) == 0


# ── Sorting ────────────────────────────────────────────────────────


class TestSorting:
    def test_sorted_by_score_descending(self):
        html = """
        <html><head></head><body>
        <nav><a href="https://boards.greenhouse.io/acme">Jobs</a></nav>
        <footer><a href="/careers">Careers</a></footer>
        <div><a href="/jobs">Jobs</a></div>
        </body></html>
        """
        links = _extract(html)
        scores = [lnk.base_score for lnk in links]
        assert scores == sorted(scores, reverse=True)


# ── Mixed extraction ───────────────────────────────────────────────


class TestMixedExtraction:
    def test_career_link_and_ats_embed_both_extracted(self):
        html = """
        <html><head></head><body>
        <nav>
          <a href="/careers">Careers</a>
        </nav>
        <div>
          <iframe src="https://boards.greenhouse.io/acme"></iframe>
        </div>
        </body></html>
        """
        links = _extract(html)
        career = [lnk for lnk in links if lnk.source == "career_link"]
        ats = [lnk for lnk in links if lnk.source == "ats_embed"]
        assert len(career) >= 1
        assert len(ats) >= 1

    def test_dedup_across_parser_and_raw_scan(self):
        """Same ATS URL found by parser and raw scan should be deduplicated."""
        html = """
        <html><head></head><body>
        <a href="https://boards.greenhouse.io/acme">Jobs</a>
        </body></html>
        """
        links = _extract(html)
        greenhouse_links = [lnk for lnk in links if "boards.greenhouse.io/acme" in lnk.url]
        assert len(greenhouse_links) == 1


class TestHubnessRequirement:
    def test_homepage_candidate_requires_pattern_or_jobs_hint(self):
        assert not _hubness_allows_candidate(
            source="homepage_link",
            hub_links=2,
            inferred_pattern=None,
            jobs_hint_n=0,
        )

    def test_homepage_candidate_allowed_with_pattern(self):
        assert _hubness_allows_candidate(
            source="homepage_link",
            hub_links=2,
            inferred_pattern=r"^https?://example.com/jobs/",
            jobs_hint_n=0,
        )

    def test_homepage_candidate_allowed_with_monitor_jobs_hint(self):
        assert _hubness_allows_candidate(
            source="homepage_link",
            hub_links=1,
            inferred_pattern=None,
            jobs_hint_n=10,
        )

    def test_blind_probe_candidate_not_subject_to_hubness_gate(self):
        assert _hubness_allows_candidate(
            source="blind_probe",
            hub_links=0,
            inferred_pattern=None,
            jobs_hint_n=0,
        )
