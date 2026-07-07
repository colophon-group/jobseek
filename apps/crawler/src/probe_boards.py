"""Network probes for ``boards.csv`` rows.

Used by CI and local tooling to catch boards whose ATS endpoint returns 404 —
typically stale slugs left behind when a company renames or migrates ATS.

For the most common ATS types, makes one lightweight HTTP request to the list
endpoint and classifies the response. Unsupported monitor types are skipped
(reported as ``status="skipped"``). Network errors are retried once.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

# Literal set of statuses a probe can return. CI treats "fail" as a hard error
# and "skipped" / "ok" / "warn" as non-blocking.
ProbeStatus = str  # "ok" | "fail" | "skipped" | "warn"


@dataclass
class ProbeResult:
    board_slug: str
    monitor_type: str
    probe_url: str
    status: ProbeStatus
    message: str


_DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_RETRY_STATUSES = {429, 500, 502, 503, 504}


async def _get(
    client: httpx.AsyncClient,
    url: str,
    *,
    method: str = "GET",
    **kwargs,
) -> httpx.Response | Exception:
    try:
        if method == "POST":
            return await client.post(url, **kwargs)
        return await client.get(url, **kwargs)
    except Exception as exc:  # noqa: BLE001 - we want to report any error
        return exc


async def _retry(
    fn: Callable[[], Awaitable[httpx.Response | Exception]],
) -> httpx.Response | Exception:
    first = await fn()
    if isinstance(first, httpx.Response) and first.status_code not in _RETRY_STATUSES:
        return first
    if isinstance(first, Exception) and not isinstance(first, httpx.HTTPError):
        return first
    await asyncio.sleep(1.0)
    return await fn()


def _token_from_config(monitor_config: str, *keys: str) -> str | None:
    if not monitor_config:
        return None
    try:
        cfg = json.loads(monitor_config)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(cfg, dict):
        return None
    for k in keys:
        v = cfg.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def _lever_region_from_config(monitor_config: str) -> str | None:
    if not monitor_config:
        return None
    try:
        cfg = json.loads(monitor_config)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(cfg, dict):
        return None
    region = cfg.get("region")
    return region if region == "eu" else None


def _lever_region_from_url(board_url: str) -> str | None:
    host = urlparse(board_url).hostname or ""
    return "eu" if host.endswith(".eu.lever.co") else None


def _ok(status: int) -> bool:
    return 200 <= status < 300


async def _probe_greenhouse(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    token = _token_from_config(row["monitor_config"], "token", "slug")
    if not token:
        m = re.search(r"greenhouse\.io/(?:embed/job_board/js\?for=)?([\w-]+)", row["board_url"])
        token = m.group(1) if m else None
    if not token:
        return ProbeResult(
            row["board_slug"],
            "greenhouse",
            row["board_url"],
            "warn",
            "no token in monitor_config or URL",
        )
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=false"
    resp = await _retry(lambda: _get(client, url))
    return _classify(row, "greenhouse", url, resp)


async def _probe_lever(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    token = _token_from_config(row["monitor_config"], "token", "site", "slug")
    if not token:
        m = re.search(r"(?:api\.)?(?:eu\.)?lever\.co/(?:v0/postings/)?([\w-]+)", row["board_url"])
        token = m.group(1) if m else None
        if not token:
            m = re.search(r"jobs\.(?:eu\.)?lever\.co/([\w-]+)", row["board_url"])
            token = m.group(1) if m else None
    if not token:
        return ProbeResult(
            row["board_slug"],
            "lever",
            row["board_url"],
            "warn",
            "no token in monitor_config or URL",
        )
    region = _lever_region_from_config(row["monitor_config"]) or _lever_region_from_url(
        row["board_url"]
    )
    host = "api.eu.lever.co" if region == "eu" else "api.lever.co"
    url = f"https://{host}/v0/postings/{token}?limit=1&mode=json"
    resp = await _retry(lambda: _get(client, url))
    return _classify(row, "lever", url, resp)


async def _probe_ashby(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    token = _token_from_config(row["monitor_config"], "token", "slug")
    if not token:
        m = re.search(r"ashbyhq\.com/(?:posting-api/job-board/)?([\w-]+)", row["board_url"])
        token = m.group(1) if m else None
    if not token:
        return ProbeResult(
            row["board_slug"],
            "ashby",
            row["board_url"],
            "warn",
            "no token in monitor_config or URL",
        )
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}"
    resp = await _retry(lambda: _get(client, url))
    return _classify(row, "ashby", url, resp)


async def _probe_recruitee(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    host = urlparse(row["board_url"]).hostname or ""
    if not host:
        return ProbeResult(
            row["board_slug"],
            "recruitee",
            row["board_url"],
            "warn",
            "cannot parse host",
        )
    url = f"https://{host}/api/offers/"
    resp = await _retry(lambda: _get(client, url))
    return _classify(row, "recruitee", url, resp)


async def _probe_rippling(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    slug = _token_from_config(row["monitor_config"], "slug", "token")
    if not slug:
        m = re.search(
            r"ats\.(?:\w+\.)?rippling\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)",
            row["board_url"],
        )
        slug = m.group(1) if m else None
    if not slug:
        return ProbeResult(
            row["board_slug"],
            "rippling",
            row["board_url"],
            "warn",
            "no slug in monitor_config or URL",
        )
    url = f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs"
    resp = await _retry(lambda: _get(client, url))
    return _classify(row, "rippling", url, resp)


async def _probe_smartrecruiters(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    slug = _token_from_config(row["monitor_config"], "slug", "token", "company")
    if not slug:
        m = re.search(r"smartrecruiters\.com/([\w-]+)", row["board_url"])
        slug = m.group(1) if m else None
    if not slug:
        return ProbeResult(
            row["board_slug"],
            "smartrecruiters",
            row["board_url"],
            "warn",
            "no slug in monitor_config or URL",
        )
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1"
    resp = await _retry(lambda: _get(client, url))
    return _classify(row, "smartrecruiters", url, resp)


async def _probe_workday(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    cfg: dict = {}
    if row["monitor_config"]:
        with contextlib.suppress(json.JSONDecodeError):
            cfg = json.loads(row["monitor_config"]) or {}
    company = cfg.get("company")
    wd_instance = cfg.get("wd_instance")
    site = cfg.get("site")
    if not (company and wd_instance and site):
        m = re.search(
            r"([\w-]+)\.wd(\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?(.+?)/?$",
            row["board_url"],
        )
        if m:
            company = company or m.group(1)
            wd_instance = wd_instance or f"wd{m.group(2)}"
            site = site or m.group(3)
    if not (company and wd_instance and site):
        return ProbeResult(
            row["board_slug"],
            "workday",
            row["board_url"],
            "warn",
            "cannot parse workday components from URL or monitor_config",
        )
    url = f"https://{company}.{wd_instance}.myworkdayjobs.com/wday/cxs/{company}/{site}/jobs"
    resp = await _retry(
        lambda: _get(
            client,
            url,
            method="POST",
            json={"limit": 1, "offset": 0, "searchText": ""},
            headers={"Content-Type": "application/json"},
        )
    )
    return _classify(row, "workday", url, resp)


def _classify(
    row: dict,
    monitor_type: str,
    url: str,
    resp: httpx.Response | Exception,
) -> ProbeResult:
    slug = row["board_slug"]
    if isinstance(resp, Exception):
        return ProbeResult(
            slug,
            monitor_type,
            url,
            "warn",
            f"network error: {type(resp).__name__}: {resp}",
        )
    if resp.status_code == 404:
        return ProbeResult(slug, monitor_type, url, "fail", "404 Not Found")
    if _ok(resp.status_code):
        return ProbeResult(slug, monitor_type, url, "ok", f"{resp.status_code}")
    # Other non-2xx (401, 403, 500, etc.) are non-blocking warnings
    return ProbeResult(
        slug,
        monitor_type,
        url,
        "warn",
        f"unexpected status {resp.status_code}",
    )


# Monitor types we know how to probe. Others are skipped by probe_row.
PROBES: dict[str, Callable[[dict, httpx.AsyncClient], Awaitable[ProbeResult]]] = {
    "greenhouse": _probe_greenhouse,
    "lever": _probe_lever,
    "ashby": _probe_ashby,
    "recruitee": _probe_recruitee,
    "rippling": _probe_rippling,
    "smartrecruiters": _probe_smartrecruiters,
    "workday": _probe_workday,
}


async def probe_row(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    """Probe one board row. Returns a ProbeResult with status ok/fail/warn/skipped."""
    mon = (row.get("monitor_type") or "").strip()
    probe = PROBES.get(mon)
    if probe is None:
        return ProbeResult(
            row.get("board_slug", ""),
            mon,
            row.get("board_url", ""),
            "skipped",
            f"no probe configured for monitor_type={mon!r}",
        )
    return await probe(row, client)


async def probe_rows(rows: list[dict], *, concurrency: int = 5) -> list[ProbeResult]:
    """Probe many rows with bounded concurrency. Preserves input order."""
    sem = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(
        timeout=_DEFAULT_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": "jobseek-probe/1.0 (+https://github.com/colophon-group/jobseek)"},
    ) as client:

        async def _one(row: dict) -> ProbeResult:
            async with sem:
                return await probe_row(row, client)

        return list(await asyncio.gather(*[_one(r) for r in rows]))


def rows_added_or_changed(
    base_rows: list[dict],
    head_rows: list[dict],
) -> list[dict]:
    """Return rows in head whose board_slug is new OR whose probe-relevant fields
    differ from base. Probe-relevant fields: board_url, monitor_type, monitor_config."""
    base_by_slug: dict[str, dict] = {r.get("board_slug", ""): r for r in base_rows}
    relevant = ("board_url", "monitor_type", "monitor_config")
    out: list[dict] = []
    for r in head_rows:
        slug = r.get("board_slug", "")
        base = base_by_slug.get(slug)
        if base is None:
            out.append(r)
            continue
        if any((base.get(k) or "") != (r.get(k) or "") for k in relevant):
            out.append(r)
    return out
