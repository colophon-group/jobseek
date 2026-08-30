"""Infor Global HR / Lawson CandidateSelfService detail scraper."""

from __future__ import annotations

import httpx
import structlog

from src.core.monitors.infor import (
    _date,
    bootstrap_session,
    detail_api_url,
    normalize_location,
    parse_candidate_url,
)
from src.core.scrapers import JobContent, register

log = structlog.get_logger()


def _detail_params(hr_organization: str, job_requisition: str, job_posting: str) -> dict[str, str]:
    return {
        "JobPosting": job_posting,
        "JobRequisition": job_requisition,
        "Description": " ",
        "__Description_translation___": " ",
        "PositionDescription": " ",
        "__PositionDescription_translation___": " ",
        "LocationOfJob.Description": " ",
        "__LocationOfJob.Description_translation___": " ",
        "Category.Description": " ",
        "RelationshipToOrganization.Description": " ",
        "PostingDateRange.Begin": " ",
        "PostingDateRange.End": " ",
        "SalaryRangeAmount": " ",
        "SalaryRange.BeginningPay": " ",
        "SalaryRange.EndingPay": " ",
        "SalaryEntered": " ",
        "StatusSwitchForCandidateSpace": " ",
        "SalaryRange.PayRangeCurrencyCode": " ",
        "Live": " ",
        "JobRequisitionLocation": " ",
        "JobRequisitionConsentAgreement": " ",
        "JobRequisitionAcknowledgement": " ",
        "JobRequisitionSelfIdConfiguration": " ",
        "JobRequisitionShowDependents": " ",
        "JobReqTSAssessment": " ",
        "csk.IsoLocale": "en",
        "HROrganization": hr_organization,
        "JobRequisitionApplicationProcessEntered": " ",
    }


def _detail(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Infor detail response is not an object")
    detail = payload.get("Find_PostingDisplay_FormOperationResponse")
    if not isinstance(detail, dict):
        raise ValueError("Infor detail response is missing the posting object")
    return detail


def parse_detail(detail: dict) -> JobContent:
    title = detail.get("__Description_translation___") or detail.get("Description")
    description = detail.get("__PositionDescription_translation___") or detail.get(
        "PositionDescription"
    )
    location = detail.get("LocationOfJob.Description") or detail.get(
        "__LocationOfJob.Description_translation___"
    )
    metadata: dict[str, str] = {}
    for source, target in (
        ("JobRequisition", "job_requisition"),
        ("JobPosting", "job_posting"),
        ("Category.Description", "category"),
        ("RelationshipToOrganization.Description", "relationship"),
        ("JobRequisitionLocation", "job_requisition_location"),
    ):
        value = detail.get(source)
        if isinstance(value, str) and value.strip():
            metadata[target] = value.strip()

    return JobContent(
        title=title.strip() if isinstance(title, str) and title.strip() else None,
        description=(
            description.strip() if isinstance(description, str) and description.strip() else None
        ),
        locations=normalize_location(location),
        date_posted=_date(detail.get("PostingDateRange.Begin")),
        language="en",
        metadata=metadata or None,
    )


async def can_handle(url: str, client: httpx.AsyncClient) -> dict | None:
    return {} if parse_candidate_url(url, require_job=True) is not None else None


async def scrape(
    url: str,
    config: dict,
    http: httpx.AsyncClient,
    pw=None,
    **kwargs,
) -> JobContent:
    candidate = parse_candidate_url(url, require_job=True)
    if candidate is None:
        log.warning("infor.scraper.invalid_job_url", url=url)
        return JobContent()
    site, job_requisition, job_posting = candidate
    assert job_requisition is not None and job_posting is not None

    headers = await bootstrap_session(url, http)
    response = await http.get(
        detail_api_url(site),
        params=_detail_params(site.hr_organization, job_requisition, job_posting),
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    return parse_detail(_detail(response.json()))


register("infor", scrape, can_handle=can_handle)
