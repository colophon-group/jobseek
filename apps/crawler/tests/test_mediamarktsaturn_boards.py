"""Provider-identity contracts for the MediaMarktSaturn boards."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from src.core.monitor import MonitorResult, _apply_url_allowlist, _apply_url_transform
from src.core.monitors import DiscoveredJob

_BOARDS_PATH = Path(__file__).parents[1] / "data" / "boards.csv"
_GLOBAL_BOARD = "mediamarktsaturn-careers-global"
_DTB_BOARDS = {
    "mediamarktsaturn-dtb-headquarters",
    "mediamarktsaturn-dtb-technicians",
}
_SOURCE_ALLOWLIST = (
    r"^https://careers\.mediamarktsaturn\.com/"
    r"[A-Za-z][A-Za-z0-9]*/job/[^/?#]+/\d+/$"
)
_STABLE_TRANSFORM = {
    "find": (
        r"^https://careers\.mediamarktsaturn\.com/"
        r"[A-Za-z][A-Za-z0-9]*/job/[^/?#]+/(\d+)/$"
    ),
    "replace": r"https://careers.mediamarktsaturn.com/job/_/\1/",
}


def _board_rows() -> dict[str, dict[str, str]]:
    with _BOARDS_PATH.open(newline="") as handle:
        return {
            row["board_slug"]: row
            for row in csv.DictReader(handle)
            if row["company_slug"] == "mediamarktsaturn"
        }


def _config(row: dict[str, str]) -> dict:
    return json.loads(row["monitor_config"])


def _canonicalize(source_urls: set[str]) -> MonitorResult:
    jobs = {
        url: DiscoveredJob(url=url, title=f"Job {index}") for index, url in enumerate(source_urls)
    }
    result = MonitorResult(urls=source_urls, jobs_by_url=jobs)
    result = _apply_url_allowlist(result, {"url_allowlist": _SOURCE_ALLOWLIST})
    return _apply_url_transform(result, {"url_transform": _STABLE_TRANSFORM})


def test_global_successfactors_board_has_fail_closed_numeric_identity_contract():
    config = _config(_board_rows()[_GLOBAL_BOARD])

    assert config["url_allowlist"] == _SOURCE_ALLOWLIST
    assert config["url_transform"] == _STABLE_TRANSFORM


def test_global_successfactors_locale_and_title_variants_converge():
    result = _canonicalize(
        {
            "https://careers.mediamarktsaturn.com/MediaMarktCH/job/"
            "Z%C3%BCrich-Verkaufsberater-DE/123456/",
            "https://careers.mediamarktsaturn.com/MediaMarktSaturn/job/Paris-Conseiller-FR/123456/",
            "https://careers.mediamarktsaturn.com/MediaWorld/job/Milano-Addetto-IT/987654/",
        }
    )

    assert result.security_filtered_count == 0
    assert result.urls == {
        "https://careers.mediamarktsaturn.com/job/_/123456/",
        "https://careers.mediamarktsaturn.com/job/_/987654/",
    }
    assert result.jobs_by_url is not None
    assert set(result.jobs_by_url) == result.urls
    assert all(job.url == url for url, job in result.jobs_by_url.items())


def test_global_successfactors_contract_rejects_unstable_or_foreign_shapes():
    invalid_urls = {
        "https://careers.mediamarktsaturn.com/MediaMarktCH/job/title/not-numeric/",
        "https://careers.mediamarktsaturn.com/MediaMarktCH/job/123456/",
        "https://careers.mediamarktsaturn.com/job/_/123456/",
        "https://evil.example/MediaMarktCH/job/title/123456/",
    }

    result = _canonicalize(invalid_urls)

    assert result.urls == set()
    assert result.jobs_by_url == {}
    assert result.security_filtered_count == len(invalid_urls)


def test_dtb_boards_use_salesforce_record_ids_for_inline_identity():
    rows = _board_rows()

    for board_slug in _DTB_BOARDS:
        config = _config(rows[board_slug])
        assert config["detail_identity_selector"] == 'label[for^="a7u"]'
        assert config["detail_identity_attribute"] == "for"
        assert config["detail_identity_regex"] == r"^(a7u[A-Za-z0-9]{15})-.+$"


def test_turkey_board_keeps_numeric_provider_id_in_discovered_url():
    config = _config(_board_rows()["mediamarktsaturn-careers-turkey"])

    assert "Home/detail" in config["link_selector"]
    assert "skinNo=39815" in config["url_filter"]
    sample = "https://hr-link.net/Home/detail?id=123456&skinNo=39815"
    query = parse_qs(urlparse(sample).query)
    assert query["id"] == ["123456"]
