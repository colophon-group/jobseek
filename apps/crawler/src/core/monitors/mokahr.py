"""Mokahr ATS monitor.

Mokahr (app.mokahr.com) is a Chinese ATS used by companies like ZTE.
The API encrypts responses with AES-128-CBC using a per-response key
(``necromancer``) and a per-site IV embedded in the SPA HTML.

Config keys:
    org_id   — organisation slug (e.g. "zte")
    site_id  — numeric site ID (e.g. 47588)
    locale   — API locale (default "zh-CN")
"""

from __future__ import annotations

import base64
import json
import re
from html import unescape

import httpx
import structlog

from src.core.monitors import DiscoveredJob, register

log = structlog.get_logger()

_API_URL = "https://app.mokahr.com/api/outer/ats-apply/website/jobs/v2"
_DETAIL_URL = "https://app.mokahr.com/api/outer/ats-apply/website/job"
_PAGE_SIZE = 20
_MAX_JOBS = 50_000

# Map Mokahr commitment values to standard employment types.
_COMMITMENT_MAP: dict[str, str] = {
    "fullTime": "Full-time",
    "partTime": "Part-time",
    "intern": "Intern",
    "contract": "Contract",
}


def _decrypt(data_b64: str, key_str: str, iv_str: str) -> dict:
    """Decrypt an AES-128-CBC Mokahr response.

    Mokahr uses 16-character ASCII strings as the AES key and IV
    (not hex-encoded byte sequences).
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    key = key_str.encode("ascii")
    iv = iv_str.encode("ascii")
    ct = base64.b64decode(data_b64)

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ct) + decryptor.finalize()

    unpadder = PKCS7(128).unpadder()
    plaintext = unpadder.update(padded) + unpadder.finalize()
    return json.loads(plaintext)


async def _get_iv(page_url: str, client: httpx.AsyncClient) -> str | None:
    """Extract the AES IV from the SPA's ``init-data`` element."""
    resp = await client.get(page_url, follow_redirects=True)
    if resp.status_code != 200:
        return None
    m = re.search(r'id="init-data"[^>]*value="([^"]*)"', resp.text)
    if not m:
        return None
    raw = unescape(m.group(1))
    try:
        init = json.loads(raw)
        return init.get("aesIv")
    except (json.JSONDecodeError, TypeError):
        return None


def _build_board_url(org_id: str, site_id: int, path: str = "social-recruitment") -> str:
    return f"https://app.mokahr.com/{path}/{org_id}/{site_id}"


def _parse_locations(job: dict) -> list[str] | None:
    locs = job.get("locations")
    if not locs or not isinstance(locs, list):
        return None
    parts: list[str] = []
    seen: set[str] = set()
    for loc in locs:
        if isinstance(loc, dict):
            city = loc.get("cityName", "")
            country = loc.get("country", "")
            s = ", ".join(p for p in (city, country) if p)
        elif isinstance(loc, str):
            s = loc
        else:
            continue
        if s and s not in seen:
            parts.append(s)
            seen.add(s)
    return parts or None


def _job_url(org_id: str, site_id: int, job_id: str) -> str:
    return f"https://app.mokahr.com/social-recruitment/{org_id}/{site_id}#/job/{job_id}"


def _parse_job(job: dict, org_id: str, site_id: int) -> DiscoveredJob | None:
    job_id = job.get("id")
    title = job.get("title")
    if not job_id or not title:
        return None

    commitment = job.get("commitment", "")
    employment_type = _COMMITMENT_MAP.get(commitment)

    published = job.get("publishedAt")

    metadata: dict = {}
    dept = job.get("department")
    if isinstance(dept, dict) and dept.get("name"):
        metadata["department"] = dept["name"]
    elif isinstance(dept, str) and dept:
        metadata["department"] = dept

    return DiscoveredJob(
        url=_job_url(org_id, site_id, job_id),
        title=title,
        description=job.get("jobDescription"),
        locations=_parse_locations(job),
        employment_type=employment_type,
        date_posted=published,
        metadata=metadata or None,
    )


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch all jobs from Mokahr's encrypted API."""
    config = board.get("metadata") or {}
    if isinstance(config, str):
        config = json.loads(config) if config else {}

    org_id = config.get("org_id")
    site_id = config.get("site_id")
    locale = config.get("locale", "zh-CN")

    if not org_id or not site_id:
        raise ValueError("mokahr monitor requires org_id and site_id in config")

    # Determine the recruitment path from the board URL.
    board_url = board.get("board_url", "")
    m = re.search(r"app\.mokahr\.com/((?:social|campus)[_-](?:recruitment|apply))/", board_url)
    path = (
        m.group(1)
        if m
        else ("campus-recruitment" if "campus" in board_url else "social-recruitment")
    )

    page_url = _build_board_url(org_id, site_id, path)
    iv = await _get_iv(page_url, client)
    if not iv:
        raise RuntimeError(f"Could not extract AES IV from {page_url}")

    jobs: list[DiscoveredJob] = []
    offset = 0

    while len(jobs) < _MAX_JOBS:
        body = {
            "orgId": org_id,
            "siteId": site_id,
            "limit": _PAGE_SIZE,
            "offset": offset,
            "needStat": offset == 0,
            "locale": locale,
        }
        resp = await client.post(_API_URL, json=body)
        resp.raise_for_status()
        envelope = resp.json()

        data_b64 = envelope.get("data")
        key_hex = envelope.get("necromancer")
        if not data_b64 or not key_hex:
            log.warning("mokahr.missing_encryption_fields", offset=offset)
            break

        payload = _decrypt(data_b64, key_hex, iv)
        inner = payload.get("data", {})
        raw_jobs = inner.get("jobs", [])

        if not raw_jobs:
            break

        for raw in raw_jobs:
            parsed = _parse_job(raw, org_id, site_id)
            if parsed:
                jobs.append(parsed)

        log.debug("mokahr.page", offset=offset, fetched=len(raw_jobs), total=len(jobs))
        offset += _PAGE_SIZE

        if len(raw_jobs) < _PAGE_SIZE:
            break

    log.info("mokahr.complete", org_id=org_id, site_id=site_id, total=len(jobs))
    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Mokahr from URL pattern."""
    m = re.search(
        r"app\.mokahr\.com/(?:social|campus)[_-](?:recruitment|apply)/([\w-]+)/(\d+)", url
    )
    if not m:
        return None
    return {"org_id": m.group(1), "site_id": int(m.group(2))}


register("mokahr", discover, cost=10, can_handle=can_handle, rich=True)
