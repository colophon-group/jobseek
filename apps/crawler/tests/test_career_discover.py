"""Tests for career_discover module — HTML parsing and link extraction."""

from __future__ import annotations

import asyncio

import yaml

from src.workspace.career_discover import (
    CareerPageCandidate,
    _blind_probe_all,
    _dedup_candidates,
    _extract_links,
    _extract_links_with_hreflang,
    _ExtractedLink,
    _filter_career_hreflang,
    _HreflangLink,
    _hubness_allows_candidate,
    _ProbeLinkResult,
    _scan_ats_urls_in_html,
    discover_career_pages,
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

    def test_greenhouse_regional_link(self):
        html = """
        <html><head></head><body>
        <a href="https://job-boards.eu.greenhouse.io/brainrocketltd">Jobs at BrainRocket</a>
        </body></html>
        """
        links = _extract(html)
        ats = [lnk for lnk in links if lnk.source == "ats_embed"]
        assert len(ats) >= 1
        assert any("job-boards.eu.greenhouse.io/brainrocketltd" in lnk.url for lnk in ats)

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

    def test_breezy_domain(self):
        html = """
        <html><head></head><body>
        <a href="https://acme.breezy.hr/">Careers</a>
        </body></html>
        """
        links = _extract(html)
        ats = [lnk for lnk in links if lnk.source == "ats_embed"]
        assert len(ats) >= 1
        assert any("acme.breezy.hr" in lnk.url for lnk in ats)

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
    def test_head_canonical_link_ignored(self):
        """Canonical links in <head> (no hreflang) should not be extracted."""
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


class TestBlindProbeFiltering:
    def test_blind_probe_excludes_zero_jobs(self, monkeypatch):
        async def _fake_handler(_url, _client):
            return {"token": "acme", "jobs": 0}

        monkeypatch.setattr("src.core.monitors.get_can_handle", lambda _name: _fake_handler)
        monkeypatch.setattr("src.core.monitors._build_comment", lambda _name, _result: "ok")

        candidates = asyncio.run(_blind_probe_all("acme", client=None))  # type: ignore[arg-type]
        assert candidates == []

    def test_blind_probe_keeps_positive_jobs(self, monkeypatch):
        async def _fake_handler(_url, _client):
            return {"token": "acme", "jobs": 3}

        monkeypatch.setattr("src.core.monitors.get_can_handle", lambda _name: _fake_handler)
        monkeypatch.setattr("src.core.monitors._build_comment", lambda _name, _result: "ok")

        candidates = asyncio.run(_blind_probe_all("acme", client=None))  # type: ignore[arg-type]
        assert len(candidates) >= 1
        assert all(c.source == "blind_probe" for c in candidates)
        assert all(c.monitor_config.get("jobs") == 3 for c in candidates)


class TestDiscoverFollowups:
    def test_discovers_jobs_child_via_one_hop_expansion(self, monkeypatch):
        seen_calls: list[tuple[str, bool]] = []

        async def _fake_probe_link(link, _client, *, collect_followups=False):
            seen_calls.append((link.url, collect_followups))
            if link.url == "https://careers.accelclub.com":
                return _ProbeLinkResult(
                    followup_links=[
                        _ExtractedLink(
                            url="https://careers.accelclub.com/jobs",
                            source="career_link",
                            context="body",
                            text="Jobs",
                            base_score=0.55,
                        )
                    ]
                )
            if link.url == "https://careers.accelclub.com/jobs":
                return _ProbeLinkResult(
                    candidates=[
                        CareerPageCandidate(
                            url=link.url,
                            source="homepage_link",
                            monitor_type="dom",
                            monitor_config={"urls": 12},
                            score=0.50,
                            comment="DOM",
                        )
                    ]
                )
            return _ProbeLinkResult()

        async def _fake_blind_probe(_slug, _client):
            return []

        monkeypatch.setattr("src.workspace.career_discover._probe_link", _fake_probe_link)
        monkeypatch.setattr("src.workspace.career_discover._blind_probe_all", _fake_blind_probe)
        monkeypatch.setattr("src.core.monitors.slugs_from_url", lambda _url: [])

        candidates = asyncio.run(
            discover_career_pages(
                "https://careers.accelclub.com",
                '<html><head></head><body><a href="https://careers.accelclub.com">Careers</a></body></html>',
                client=None,  # type: ignore[arg-type]
            )
        )

        assert any(url == "https://careers.accelclub.com/jobs" for url, _ in seen_calls)
        assert any(collect for _, collect in seen_calls)
        assert len(candidates) == 1
        assert candidates[0].url == "https://careers.accelclub.com/jobs"

    def test_discovery_writes_state_file(self, tmp_path, monkeypatch):
        seen_calls: list[str] = []

        async def _fake_probe_link(link, _client, *, collect_followups=False):
            seen_calls.append(link.url)
            return _ProbeLinkResult(
                candidates=[
                    CareerPageCandidate(
                        url=link.url,
                        source="homepage_link",
                        monitor_type="dom",
                        monitor_config={"urls": 4},
                        score=0.55,
                        comment="DOM",
                        job_link_hub=4,
                        job_link_pattern=r"^https://example.com/jobs/",
                    )
                ],
                page={
                    "requested_url": link.url,
                    "final_url": link.url,
                    "source": link.source,
                    "status_code": 200,
                    "fetch_ok": True,
                    "outgoing_links": 8,
                    "likely_job_links": 4,
                    "job_link_pattern": r"^https://example.com/jobs/",
                    "detected_monitors": ["dom"],
                },
            )

        async def _fake_blind_probe(_slug, _client):
            return []

        monkeypatch.setattr("src.workspace.career_discover._probe_link", _fake_probe_link)
        monkeypatch.setattr("src.workspace.career_discover._blind_probe_all", _fake_blind_probe)
        monkeypatch.setattr("src.core.monitors.slugs_from_url", lambda _url: [])

        state_path = tmp_path / "discovery.state.yaml"
        candidates = asyncio.run(
            discover_career_pages(
                "https://example.com",
                '<html><head></head><body><a href="/jobs">Jobs</a></body></html>',
                client=None,  # type: ignore[arg-type]
                state_path=state_path,
            )
        )

        assert candidates
        assert state_path.exists()
        data = yaml.safe_load(state_path.read_text()) or {}
        assert isinstance(data.get("pages"), list)
        assert isinstance(data.get("candidates"), list)
        assert any(url.endswith("/careers") for url in seen_calls)


# ── Hreflang extraction ──────────────────────────────────────────


class TestHreflangExtraction:
    def test_career_path_hreflang_extracted(self):
        """Hreflang links with career paths should be extracted."""
        html = """
        <html><head>
        <link rel="alternate" hreflang="de-DE" href="https://example.com/de/karriere">
        <link rel="alternate" hreflang="fr-FR" href="https://example.com/fr/carrieres">
        </head><body></body></html>
        """
        links = _extract(html)
        hreflang = [lnk for lnk in links if lnk.source == "hreflang"]
        assert len(hreflang) == 2
        urls = {lnk.url for lnk in hreflang}
        assert "https://example.com/de/karriere" in urls
        assert "https://example.com/fr/carrieres" in urls

    def test_non_career_path_hreflang_filtered_out(self):
        """Hreflang links without career paths should not appear in extracted links."""
        html = """
        <html><head>
        <link rel="alternate" hreflang="de-DE" href="https://example.com/de/about">
        <link rel="alternate" hreflang="fr-FR" href="https://example.com/fr/products">
        </head><body></body></html>
        """
        links = _extract(html)
        hreflang = [lnk for lnk in links if lnk.source == "hreflang"]
        assert len(hreflang) == 0

    def test_ats_domain_hreflang_extracted(self):
        """Hreflang links pointing to ATS domains should be extracted regardless of path.

        Note: ATS URLs in raw HTML are also found by _scan_ats_urls_in_html (score 0.90),
        which wins the dedup over hreflang (0.70). The URL is still extracted — just as
        ats_embed source. We verify the hreflang filter itself recognizes it.
        """
        html = """
        <html><head>
        <link rel="alternate" hreflang="en-US" href="https://boards.greenhouse.io/acme">
        </head><body></body></html>
        """
        links = _extract(html)
        # The URL is present (found by both hreflang and raw ATS scan; ATS wins dedup)
        ats = [lnk for lnk in links if "boards.greenhouse.io/acme" in lnk.url]
        assert len(ats) == 1
        # Verify the filter function itself recognizes ATS hreflang
        hl = [
            _HreflangLink(
                url="https://boards.greenhouse.io/acme", hreflang="en-US", source_page=BASE
            )
        ]
        filtered = _filter_career_hreflang(hl)
        assert len(filtered) == 1
        assert filtered[0].source == "hreflang"

    def test_dedup_body_link_wins_over_hreflang(self):
        """Nav career link (0.85) should win over hreflang (0.70) for same URL."""
        html = """
        <html><head>
        <link rel="alternate" hreflang="en" href="https://example.com/careers">
        </head><body>
        <nav><a href="/careers">Careers</a></nav>
        </body></html>
        """
        links = _extract(html)
        careers = [lnk for lnk in links if lnk.url == "https://example.com/careers"]
        assert len(careers) == 1
        assert careers[0].base_score == 0.85
        assert careers[0].source == "career_link"

    def test_hreflang_score_and_context(self):
        """Hreflang links should have score 0.70 and context 'head'."""
        html = """
        <html><head>
        <link rel="alternate" hreflang="es-ES" href="https://example.com/es/empleo">
        </head><body></body></html>
        """
        links = _extract(html)
        hreflang = [lnk for lnk in links if lnk.source == "hreflang"]
        assert len(hreflang) == 1
        assert hreflang[0].base_score == 0.70
        assert hreflang[0].context == "head"

    def test_hreflang_text_is_language_code(self):
        """Hreflang link text should be the language code."""
        html = """
        <html><head>
        <link rel="alternate" hreflang="pt-BR" href="https://example.com/pt/vagas">
        </head><body></body></html>
        """
        links = _extract(html)
        hreflang = [lnk for lnk in links if lnk.source == "hreflang"]
        assert len(hreflang) == 1
        assert hreflang[0].text == "pt-BR"

    def test_x_default_with_career_path_included(self):
        """x-default hreflang with career path should be extracted."""
        html = """
        <html><head>
        <link rel="alternate" hreflang="x-default" href="https://example.com/careers">
        </head><body></body></html>
        """
        links = _extract(html)
        hreflang = [lnk for lnk in links if lnk.source == "hreflang"]
        assert len(hreflang) == 1
        assert hreflang[0].text == "x-default"

    def test_non_alternate_head_links_still_ignored(self):
        """Stylesheet and canonical links in head should still be ignored."""
        html = """
        <html><head>
        <link rel="stylesheet" href="https://example.com/styles.css">
        <link rel="canonical" href="https://example.com/careers">
        <link rel="alternate" hreflang="de" href="https://example.com/de/karriere">
        </head><body></body></html>
        """
        links = _extract(html)
        assert len(links) == 1
        assert links[0].source == "hreflang"
        assert "karriere" in links[0].url


class TestFilterCareerHreflang:
    def test_career_path_kept(self):
        """Links with career path should pass through the filter."""
        hl = [_HreflangLink(url="https://example.com/careers", hreflang="en", source_page=BASE)]
        result = _filter_career_hreflang(hl)
        assert len(result) == 1
        assert result[0].source == "hreflang"
        assert result[0].base_score == 0.70

    def test_non_career_path_removed(self):
        """Links without career path should be filtered out."""
        hl = [_HreflangLink(url="https://example.com/about", hreflang="en", source_page=BASE)]
        result = _filter_career_hreflang(hl)
        assert len(result) == 0

    def test_ats_url_kept(self):
        """ATS domain URLs should pass even without career path."""
        hl = [
            _HreflangLink(
                url="https://boards.greenhouse.io/acme",
                hreflang="en",
                source_page=BASE,
            )
        ]
        result = _filter_career_hreflang(hl)
        assert len(result) == 1

    def test_dedup_by_url(self):
        """Duplicate URLs should be deduplicated."""
        hl = [
            _HreflangLink(url="https://example.com/careers", hreflang="en", source_page=BASE),
            _HreflangLink(url="https://example.com/careers", hreflang="en-US", source_page=BASE),
        ]
        result = _filter_career_hreflang(hl)
        assert len(result) == 1

    def test_multilingual_career_paths(self):
        """Various language career paths should be recognized."""
        hl = [
            _HreflangLink(url="https://example.com/de/karriere", hreflang="de", source_page=BASE),
            _HreflangLink(url="https://example.com/nl/vacatures", hreflang="nl", source_page=BASE),
            _HreflangLink(
                url="https://example.com/sv/lediga-jobb", hreflang="sv", source_page=BASE
            ),
        ]
        result = _filter_career_hreflang(hl)
        assert len(result) == 3


class TestHreflangRawExtraction:
    def test_raw_hreflang_from_extract_with_hreflang(self):
        """_extract_links_with_hreflang should return raw hreflang links."""
        html = """
        <html><head>
        <link rel="alternate" hreflang="de-DE" href="https://example.com/de/about">
        <link rel="alternate" hreflang="en-US" href="https://example.com/en/careers">
        </head><body></body></html>
        """
        links, raw_hreflang = _extract_links_with_hreflang(html, BASE)
        # Raw hreflang should contain both (including non-career)
        assert len(raw_hreflang) == 2
        # Only career-path one should appear in extracted links
        hreflang_links = [lnk for lnk in links if lnk.source == "hreflang"]
        assert len(hreflang_links) == 1
        assert "careers" in hreflang_links[0].url
