"""Shared identity helpers for ADP Workforce Now public career boards."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse

_HOST = "workforcenow.adp.com"
_LISTING_PATH = "/mascsr/default/mdf/recruitment/recruitment.html"
_SEARCH_PATH = "/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions"
_CID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_CC_ID_RE = re.compile(r"^[0-9]{1,32}_[0-9]{1,12}$")
_LOCALE_RE = re.compile(r"^[a-z]{2}_[A-Z]{2}$")
_JOB_ID_RE = re.compile(r"^[0-9]{1,32}_[0-9]{1,12}$")


def normalize_adp_cid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cid = value.strip().lower()
    return cid if _CID_RE.fullmatch(cid) is not None else None


def normalize_adp_cc_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cc_id = value.strip()
    return cc_id if _CC_ID_RE.fullmatch(cc_id) is not None else None


def normalize_adp_locale(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    locale = value.strip()
    if re.fullmatch(r"[A-Za-z]{2}_[A-Za-z]{2}", locale) is None:
        return None
    normalized = f"{locale[:2].lower()}_{locale[3:].upper()}"
    return normalized if _LOCALE_RE.fullmatch(normalized) is not None else None


def normalize_adp_job_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    job_id = value.strip()
    return job_id if _JOB_ID_RE.fullmatch(job_id) is not None else None


@dataclass(frozen=True, slots=True)
class AdpBoard:
    cid: str
    cc_id: str
    locale: str

    def listing_url(self) -> str:
        query = urlencode(
            {
                "cid": self.cid,
                "ccId": self.cc_id,
                "lang": self.locale,
                "selectedMenuKey": "CareerCenter",
            }
        )
        return f"https://{_HOST}{_LISTING_PATH}?{query}"

    def search_url(self, *, start: int = 1) -> str:
        if isinstance(start, bool) or not isinstance(start, int) or start < 1:
            raise ValueError(f"Invalid ADP start sequence: {start!r}")
        query = urlencode(
            {
                "cid": self.cid,
                "ccId": self.cc_id,
                "lang": self.locale,
                "locale": self.locale,
                "$skip": start,
            }
        )
        return f"https://{_HOST}{_SEARCH_PATH}?{query}"

    def job_url(self, job_id: str) -> str:
        normalized = normalize_adp_job_id(job_id)
        if normalized is None:
            raise ValueError(f"Invalid ADP job ID: {job_id!r}")
        query = urlencode(
            {
                "cid": self.cid,
                "ccId": self.cc_id,
                "lang": self.locale,
                "selectedMenuKey": "CareerCenter",
                "jobId": normalized,
            }
        )
        return f"https://{_HOST}{_LISTING_PATH}?{query}"


def adp_board_from_metadata(metadata: Mapping[str, object]) -> AdpBoard | None:
    cid = normalize_adp_cid(metadata.get("cid"))
    cc_id = normalize_adp_cc_id(metadata.get("cc_id") or metadata.get("ccId"))
    locale = normalize_adp_locale(metadata.get("locale") or metadata.get("lang"))
    if cid is None or cc_id is None or locale is None:
        return None
    return AdpBoard(cid=cid, cc_id=cc_id, locale=locale)


def adp_board_from_url(url: str) -> AdpBoard | None:
    """Parse an unfiltered public ADP listing or detail URL."""
    if not isinstance(url, str) or len(url) > 4096:
        return None
    try:
        parsed = urlparse(url)
        port = parsed.port
        pairs = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=12)
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != _HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.path != _LISTING_PATH
        or parsed.fragment
    ):
        return None

    params: dict[str, str] = {}
    for name, value in pairs:
        if name in params:
            return None
        params[name] = value
    if not {"cid", "ccId", "lang"}.issubset(params):
        return None
    if not set(params).issubset({"cid", "ccId", "lang", "selectedMenuKey", "jobId"}):
        return None
    if params.get("selectedMenuKey", "CareerCenter") != "CareerCenter":
        return None
    if "jobId" in params and normalize_adp_job_id(params["jobId"]) is None:
        return None

    cid = normalize_adp_cid(params["cid"])
    cc_id = normalize_adp_cc_id(params["ccId"])
    locale = normalize_adp_locale(params["lang"])
    if cid is None or cc_id is None or locale is None:
        return None
    return AdpBoard(cid=cid, cc_id=cc_id, locale=locale)


def adp_start_from_search_url(url: str, board: AdpBoard) -> int | None:
    """Validate a generated search URL and return its one-based start sequence."""
    try:
        parsed = urlparse(url)
        port = parsed.port
        pairs = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=12)
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != _HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.path != _SEARCH_PATH
        or parsed.fragment
    ):
        return None
    params: dict[str, str] = {}
    for name, value in pairs:
        if name in params:
            return None
        params[name] = value
    if set(params) != {"cid", "ccId", "lang", "locale", "$skip"}:
        return None
    if (
        normalize_adp_cid(params["cid"]) != board.cid
        or normalize_adp_cc_id(params["ccId"]) != board.cc_id
        or normalize_adp_locale(params["lang"]) != board.locale
        or normalize_adp_locale(params["locale"]) != board.locale
        or not params["$skip"].isdigit()
    ):
        return None
    start = int(params["$skip"])
    return start if start >= 1 else None
