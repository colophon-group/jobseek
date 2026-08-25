from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

_CRAWLER = Path(__file__).parents[1]
_ROOT = _CRAWLER.parents[1]


@dataclass(frozen=True)
class _AuditedJob:
    provider: str
    provider_id: str
    country_code: str
    vacancy_signature: str


# Complete live inventory audited on 2026-08-25. Signatures are review-only
# labels for vacancies whose descriptions were compared; production routing
# uses provider ownership and never title similarity or dated ID lists.
_RAW_INVENTORY = (
    _AuditedJob("linkedin", "4453689816", "CHE", "hr-generalist-montreux"),
    _AuditedJob("linkedin", "4457317622", "CHE", "ai-transformation-intern"),
    _AuditedJob("linkedin", "4457321546", "THA", "marketing-director-phuket"),
    _AuditedJob("linkedin", "4458171439", "SAU", "paymaster-umluj"),
    _AuditedJob("jobs_ch", "8da62f2b-4b90-4e89-a5a0-53028cd83d36", "CHE", "hr-generalist-montreux"),
    _AuditedJob("phuketall", "057289-213283", "THA", "marketing-director-phuket"),
    _AuditedJob("phuketall", "057289-211035", "THA", "sales-manager-phuket"),
    _AuditedJob("phuketall", "057289-213282", "THA", "medical-nurse-phuket"),
    _AuditedJob("phuketall", "057289-213281", "THA", "longevity-coach-phuket"),
    _AuditedJob("phuketall", "057289-210326", "THA", "security-manager-phuket"),
    _AuditedJob("phuketall", "057289-209297", "THA", "finance-controller-phuket"),
    _AuditedJob("veryeast", "2836817", "CHN", "movement-coach-anji"),
    _AuditedJob("veryeast", "2599163", "CHN", "aesthetic-nurse-anji"),
    _AuditedJob("veryeast", "1824261", "CHN", "restaurant-attendant-anji"),
    _AuditedJob("veryeast", "1824244", "CHN", "front-desk-anji"),
    _AuditedJob("veryeast", "1820779", "CHN", "spa-supervisor-anji"),
)


def _boards() -> list[dict[str, str]]:
    with (_CRAWLER / "data" / "boards.csv").open(newline="") as handle:
        return [
            row for row in csv.DictReader(handle) if row["company_slug"] == "clinique-la-prairie"
        ]


def _canonical(job: _AuditedJob) -> str:
    if job.provider == "linkedin":
        return f"https://www.linkedin.com/jobs/view/{job.provider_id}"
    if job.provider == "phuketall":
        employer, posting = job.provider_id.split("-", 1)
        return f"https://www.phuketall.com/jobs/{employer}-{posting}-phuket/{posting}.html"
    return f"{job.provider}:{job.provider_id}"


def test_provider_ownership_preserves_fourteen_unique_live_vacancies() -> None:
    boards = _boards()
    by_provider = {row["scraper_type"]: row for row in boards}

    assert len(boards) == 3
    assert set(by_provider) == {"linkedin", "phuketall", "veryeast"}
    assert all(row["monitor_type"] != "jobs_ch" for row in boards)
    linkedin_config = json.loads(by_provider["linkedin"]["monitor_config"])
    assert linkedin_config["source_ownership_excluded_country_codes"] == ["THA"]

    selected = [
        job
        for job in _RAW_INVENTORY
        if job.provider in {"phuketall", "veryeast"}
        or (job.provider == "linkedin" and job.country_code != "THA")
    ]

    assert len(_RAW_INVENTORY) == 16
    assert len(selected) == 14
    assert len({_canonical(job) for job in selected}) == 14
    assert len({job.vacancy_signature for job in selected}) == 14
    assert {job.vacancy_signature for job in selected} == {
        "hr-generalist-montreux",
        "ai-transformation-intern",
        "paymaster-umluj",
        "marketing-director-phuket",
        "sales-manager-phuket",
        "medical-nurse-phuket",
        "longevity-coach-phuket",
        "security-manager-phuket",
        "finance-controller-phuket",
        "movement-coach-anji",
        "aesthetic-nurse-anji",
        "restaurant-attendant-anji",
        "front-desk-anji",
        "spa-supervisor-anji",
    }


def test_new_provider_scrapers_are_narrowly_trusted_by_label_workflow() -> None:
    script = (_ROOT / ".github" / "scripts" / "label-pr.sh").read_text()
    match = re.search(r"^VALID_SCRAPER_TYPES='([^']+)'$", script, re.MULTILINE)
    assert match is not None
    trusted = set(match.group(1).split("|"))

    assert {"phuketall", "veryeast"} <= trusted
    monitor_match = re.search(r"^VALID_MONITOR_TYPES='([^']+)'$", script, re.MULTILINE)
    assert monitor_match is not None
    assert "veryeast" not in monitor_match.group(1).split("|")
