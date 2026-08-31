"""Sync test: ensure _compat mirrors the runtime monitor/scraper registries."""

from __future__ import annotations

from src.core.monitors import all_monitor_types as core_all
from src.core.monitors import api_monitor_types as core_api
from src.core.monitors import is_rich_monitor as core_is_rich
from src.core.scrapers import _REGISTRY as scraper_registry
from src.workspace._compat import all_monitor_types as compat_all
from src.workspace._compat import all_scraper_types as compat_scraper_all
from src.workspace._compat import api_monitor_types as compat_api
from src.workspace._compat import detect_ats_from_url
from src.workspace._compat import is_rich_monitor as compat_is_rich


def test_all_monitor_types_match():
    assert compat_all() == core_all(), (
        f"_compat.all_monitor_types() drifted from core: "
        f"missing={core_all() - compat_all()}, extra={compat_all() - core_all()}"
    )


def test_api_monitor_types_match():
    assert compat_api() == core_api(), (
        f"_compat.api_monitor_types() drifted from core: "
        f"missing={core_api() - compat_api()}, extra={compat_api() - core_api()}"
    )


def test_is_rich_monitor_consistency():
    for mtype in core_all():
        assert compat_is_rich(mtype) == core_is_rich(mtype), (
            f"is_rich_monitor({mtype!r}) disagrees: "
            f"compat={compat_is_rich(mtype)}, core={core_is_rich(mtype)}"
        )

    # Also test api_sniffer with fields config
    cfg = {"fields": {"title": "name"}}
    assert compat_is_rich("api_sniffer", cfg) == core_is_rich("api_sniffer", cfg)
    assert compat_is_rich("api_sniffer", {}) == core_is_rich("api_sniffer", {})
    assert compat_is_rich("api_sniffer", None) == core_is_rich("api_sniffer", None)

    dom_cfg = {"rich_rows": {"row_selector": ".job", "link_selector": ".job a"}}
    assert compat_is_rich("dom", dom_cfg) == core_is_rich("dom", dom_cfg) is True
    assert compat_is_rich("dom", {}) == core_is_rich("dom", {}) is False

    smartrecruiters_cfg = {"canonical_job_id_url_template": "https://career.hm.com/job/{job_id}/"}
    assert (
        compat_is_rich("smartrecruiters", smartrecruiters_cfg)
        == core_is_rich("smartrecruiters", smartrecruiters_cfg)
        is True
    )
    assert compat_is_rich("smartrecruiters", {}) == core_is_rich("smartrecruiters", {}) is False


def test_all_scraper_types_match():
    core_scraper_all = frozenset(scraper_registry.keys())
    assert compat_scraper_all() == core_scraper_all, (
        f"_compat.all_scraper_types() drifted from core: "
        f"missing={core_scraper_all - compat_scraper_all()}, "
        f"extra={compat_scraper_all() - core_scraper_all}"
    )


def test_detect_ats_greenhouse_regional_host():
    assert detect_ats_from_url("https://job-boards.eu.greenhouse.io/brainrocketltd") == "greenhouse"


def test_detect_ats_breezy_host():
    assert detect_ats_from_url("https://acme.breezy.hr") == "breezy"


def test_detect_governmentjobs_unfiltered_agency_board_as_rss():
    assert detect_ats_from_url("https://www.governmentjobs.com/careers/fleg") == "rss"
    assert detect_ats_from_url("https://governmentjobs.com/careers/fleg/") == "rss"
    assert detect_ats_from_url("https://www.governmentjobs.com/careers/fleg?page=2") is None
    assert detect_ats_from_url("https://www.governmentjobs.com/careers/fleg/jobs/123") is None


def test_detect_dualoo_portal_as_dom():
    assert detect_ats_from_url("https://jobs.dualoo.com/portal/fyuan4bk?lang=DE") == "dom"
    assert detect_ats_from_url("https://jobs.dualoo.com/login") is None
    assert (
        detect_ats_from_url(
            "https://jobs.dualoo.com/portal/fyuan4bk/ef8b03a4-9219-4c19-a351-d01c0e07cc4f/detail"
        )
        is None
    )


def test_detect_lucca_listing_as_dom():
    assert detect_ats_from_url("https://jobs.world.luccasoftware.com/world-aquatics") == "dom"
    assert (
        detect_ats_from_url(
            "https://jobs.world.luccasoftware.com/world-aquatics/"
            "athlete-intern-050521f8-610b-4d01-b201-6007b42b6a93"
        )
        is None
    )


def test_detect_ats_earcu_listing_path():
    assert detect_ats_from_url("https://careers.example.com/jobs/vacancy/find/results/") == "earcu"
    assert (
        detect_ats_from_url("https://careers.example.com/vacancies/vacancy-search-results.aspx")
        == "earcu"
    )


def test_detect_ats_avature_vendor_url_is_strict():
    assert detect_ats_from_url("https://acme.avature.net/en_US/careers/SearchJobs") == "avature"
    assert (
        detect_ats_from_url("https://acme.avature.net/en_US/careers/JobDetail/Role/123")
        == "avature"
    )
    assert detect_ats_from_url("https://jobs.example.com/careers/SearchJobs") is None
    assert (
        detect_ats_from_url("https://acme.avature.net/en_US/careers/SearchJobs?keyword=engineer")
        is None
    )


def test_detect_ats_hibob_host():
    assert detect_ats_from_url("https://acme.careers.hibob.com/") == "hibob"


def test_detect_ats_beehire_career_page():
    assert detect_ats_from_url("https://app.beehire.com/career/gichd") == "beehire"
    assert detect_ats_from_url("https://app.beehire.com/invite/6L-oDP2wk") is None


def test_detect_ats_hirehive_host():
    assert detect_ats_from_url("https://acme.hirehive.com") == "hirehive"


def test_detect_ats_turbohire_host():
    assert (
        detect_ats_from_url(
            "https://acme.turbohire.co/careerpage/4d757ba0-3d57-448a-b82c-238ed87ac90f"
        )
        == "turbohire"
    )


def test_detect_ats_welcometothejungle_company_jobs_url():
    assert (
        detect_ats_from_url("https://www.welcometothejungle.com/fr/companies/wojo/jobs")
        == "welcometothejungle"
    )
    assert detect_ats_from_url("https://www.welcometothejungle.com/fr/companies/wojo") is None


def test_detect_ats_comeet_hosts():
    assert detect_ats_from_url("https://www.comeet.com/jobs/acme/12.345") == "comeet"
    assert (
        detect_ats_from_url("https://www.comeet.co/careers-api/2.0/company/12.345/positions")
        == "comeet"
    )
