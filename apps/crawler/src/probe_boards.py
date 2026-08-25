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
from urllib.parse import urljoin, urlparse

import httpx

from src.shared.adp import adp_board_from_metadata, adp_board_from_url
from src.shared.avature import (
    avature_board_from_metadata,
    avature_board_from_url,
    parse_avature_page,
)
from src.shared.beisen import (
    beisen_board_from_metadata,
    beisen_board_from_url,
    extract_beisen_bootstrap,
)
from src.shared.cornerstone import (
    cornerstone_board_from_metadata,
    cornerstone_board_from_url,
    extract_cornerstone_context,
)
from src.shared.darwinbox import darwinbox_board_from_metadata, darwinbox_board_from_url
from src.shared.dayforce import (
    dayforce_board_from_metadata,
    dayforce_board_from_url,
    dayforce_listing_culture_from_url,
    extract_dayforce_site,
    resolve_dayforce_listing_redirect,
)
from src.shared.gupy import normalize_gupy_tenant
from src.shared.http import DEFAULT_ACCEPT, DEFAULT_USER_AGENT
from src.shared.http_retry import PaginationFetchError
from src.shared.jazzhr import jazzhr_listing_url, resolve_jazzhr_tenant
from src.shared.jobvite import (
    is_jobvite_invalid_redirect,
    jobvite_board_from_metadata,
    jobvite_board_from_url,
)
from src.shared.keka import (
    extract_keka_identifier,
    is_keka_forbidden_redirect,
    keka_board_from_metadata,
    keka_board_from_url,
)
from src.shared.pageup import pageup_board_from_metadata, pageup_board_from_url
from src.shared.recruiterbox import (
    recruiterbox_board_from_metadata,
    recruiterbox_board_from_url,
    recruiterbox_inactive_from_html,
    recruiterbox_total_from_html,
)
from src.shared.successfactors import (
    successfactors_legacy_board_from_metadata,
    successfactors_legacy_board_from_url,
)
from src.shared.taleo import (
    taleo_board_from_metadata,
    taleo_board_from_url,
    taleo_listing_marker_from_html,
    taleo_total_from_html,
)
from src.shared.ukg import ukg_board_from_metadata, ukg_board_from_url

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


async def _probe_bamboohr(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    tenant = _token_from_config(row["monitor_config"], "tenant")
    if not tenant:
        host = (urlparse(row["board_url"]).hostname or "").lower()
        match = re.fullmatch(r"([a-z0-9][a-z0-9-]*)\.bamboohr\.com", host)
        tenant = match.group(1) if match else None
    if not tenant or tenant in {"api", "app", "help", "static", "www"}:
        return ProbeResult(
            row["board_slug"],
            "bamboohr",
            row["board_url"],
            "warn",
            "no tenant in monitor_config or BambooHR URL",
        )
    url = f"https://{tenant}.bamboohr.com/careers/list"
    resp = await _retry(lambda: _get(client, url, follow_redirects=False))
    if isinstance(resp, httpx.Response) and resp.is_redirect:
        location = resp.headers.get("location", "")
        redirect_host = (urlparse(urljoin(url, location)).hostname or "").lower()
        if redirect_host in {"bamboohr.com", "www.bamboohr.com"}:
            return ProbeResult(
                row["board_slug"],
                "bamboohr",
                url,
                "fail",
                "redirected to BambooHR marketing site",
            )
    return _classify(row, "bamboohr", url, resp)


async def _probe_paycom(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    from src.core.monitors import BoardGoneError
    from src.core.monitors.paycom import (
        fetch_paycom_job_count,
        paycom_portal_url,
        resolve_paycom_token,
    )
    from src.shared.tdm import TDMReservedError

    try:
        decoded = json.loads(row["monitor_config"] or "{}")
    except (json.JSONDecodeError, TypeError):
        decoded = {}
    metadata = decoded if isinstance(decoded, dict) else {}
    try:
        token = resolve_paycom_token(row["board_url"], metadata)
    except ValueError as exc:
        return ProbeResult(
            row["board_slug"],
            "paycom",
            row["board_url"],
            "fail",
            str(exc),
        )
    if token is None:
        return ProbeResult(
            row["board_slug"],
            "paycom",
            row["board_url"],
            "warn",
            "no valid token in monitor_config or Paycom URL",
        )

    url = paycom_portal_url(token)
    try:
        total = await fetch_paycom_job_count(token, client)
    except TDMReservedError:
        raise
    except BoardGoneError as exc:
        message = "board unavailable" if exc.status_code == 200 else "board not found"
        return ProbeResult(row["board_slug"], "paycom", url, "fail", message)
    except Exception:
        return ProbeResult(
            row["board_slug"],
            "paycom",
            url,
            "fail",
            "Paycom bootstrap or listing search validation failed",
        )
    return ProbeResult(row["board_slug"], "paycom", url, "ok", f"{total} jobs")


async def _probe_adp(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    try:
        config = json.loads(row["monitor_config"] or "{}")
    except (json.JSONDecodeError, TypeError):
        config = {}
    board = (
        adp_board_from_metadata(config) if isinstance(config, dict) else None
    ) or adp_board_from_url(row["board_url"])
    if board is None:
        return ProbeResult(
            row["board_slug"],
            "adp",
            row["board_url"],
            "warn",
            "no valid cid/cc_id/locale in monitor_config or ADP URL",
        )

    url = board.search_url(start=1)
    headers = {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-Forwarded-Host": "workforcenow.adp.com",
    }
    resp = await _retry(lambda: _get(client, url, headers=headers))
    if isinstance(resp, httpx.Response) and resp.status_code == 200:
        try:
            payload = resp.json()
        except ValueError:
            return ProbeResult(row["board_slug"], "adp", url, "warn", "invalid JSON")
        rows = payload.get("jobRequisitions") if isinstance(payload, dict) else None
        meta = payload.get("meta") if isinstance(payload, dict) else None
        total = meta.get("totalNumber") if isinstance(meta, dict) else None
        if not isinstance(rows, list) or isinstance(total, bool) or not isinstance(total, int):
            return ProbeResult(row["board_slug"], "adp", url, "warn", "invalid listing shape")
        return ProbeResult(row["board_slug"], "adp", url, "ok", f"200, {total} jobs")
    return _classify(row, "adp", url, resp)


async def _probe_jazzhr(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    try:
        decoded = json.loads(row["monitor_config"] or "{}")
    except (json.JSONDecodeError, TypeError):
        decoded = {}
    metadata = decoded if isinstance(decoded, dict) else {}
    try:
        tenant = resolve_jazzhr_tenant(row["board_url"], metadata)
    except ValueError as exc:
        return ProbeResult(
            row["board_slug"],
            "jazzhr",
            row["board_url"],
            "fail",
            str(exc),
        )
    if tenant is None:
        return ProbeResult(
            row["board_slug"],
            "jazzhr",
            row["board_url"],
            "warn",
            "no valid tenant in monitor_config or JazzHR URL",
        )

    url = jazzhr_listing_url(tenant)
    resp = await _retry(lambda: _get(client, url, follow_redirects=False))
    if isinstance(resp, httpx.Response):
        if resp.status_code in {404, 410}:
            return ProbeResult(row["board_slug"], "jazzhr", url, "fail", "board not found")
        if resp.is_redirect:
            location = resp.headers.get("location", "")
            redirect_host = (urlparse(urljoin(url, location)).hostname or "").lower()
            if redirect_host in {"jazzhr.com", "www.jazzhr.com"}:
                return ProbeResult(
                    row["board_slug"],
                    "jazzhr",
                    url,
                    "fail",
                    "redirected to JazzHR marketing site",
                )
        if resp.status_code == 200 and 'id="job_listings_wrapper"' not in resp.text:
            return ProbeResult(
                row["board_slug"],
                "jazzhr",
                url,
                "warn",
                "JazzHR listing marker missing",
            )
    return _classify(row, "jazzhr", url, resp)


async def _probe_icims(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    host = _token_from_config(row["monitor_config"], "host")
    host = host.strip().lower().rstrip(".") if host else None
    if not host:
        from src.workspace._compat import detect_ats_from_url

        parsed = urlparse(row["board_url"])
        host = (
            (parsed.hostname or "").lower()
            if detect_ats_from_url(row["board_url"]) == "icims"
            else None
        )
    reserved = {
        "api.icims.com",
        "app.icims.com",
        "help.icims.com",
        "support.icims.com",
        "www.icims.com",
    }
    if (
        not host
        or host in reserved
        or re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.icims\.com",
            host,
        )
        is None
    ):
        return ProbeResult(
            row["board_slug"],
            "icims",
            row["board_url"],
            "warn",
            "no valid host in monitor_config or iCIMS URL",
        )

    url = f"https://{host}/jobs/search?ss=1&in_iframe=1"
    resp = await _retry(lambda: _get(client, url, follow_redirects=False))
    if isinstance(resp, httpx.Response):
        if resp.status_code in {404, 410}:
            return ProbeResult(row["board_slug"], "icims", url, "fail", "board not found")
        if resp.is_redirect:
            return ProbeResult(row["board_slug"], "icims", url, "warn", "unexpected redirect")
        if resp.status_code == 200 and "icims_listingspage" not in resp.text.casefold():
            return ProbeResult(
                row["board_slug"],
                "icims",
                url,
                "warn",
                "iCIMS listing marker missing (possibly migrated to a custom site)",
            )
    return _classify(row, "icims", url, resp)


async def _probe_herp(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    slug = _token_from_config(row["monitor_config"], "slug")
    slug = slug.strip().lower() if slug else None
    if not slug:
        from src.workspace._compat import detect_ats_from_url

        parsed = urlparse(row["board_url"])
        segments = [segment for segment in parsed.path.split("/") if segment]
        slug = (
            segments[1].lower()
            if detect_ats_from_url(row["board_url"]) == "herp" and len(segments) >= 2
            else None
        )
    if not slug or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", slug) is None:
        return ProbeResult(
            row["board_slug"],
            "herp",
            row["board_url"],
            "warn",
            "no valid slug in monitor_config or HERP URL",
        )

    url = f"https://herp.careers/v1/{slug}"
    resp = await _retry(lambda: _get(client, url, follow_redirects=False))
    if isinstance(resp, httpx.Response):
        if resp.status_code in {404, 410}:
            return ProbeResult(row["board_slug"], "herp", url, "fail", "board not found")
        if resp.is_redirect:
            return ProbeResult(row["board_slug"], "herp", url, "warn", "unexpected redirect")
        if resp.status_code == 200 and 'class="requisition-list"' not in resp.text:
            return ProbeResult(
                row["board_slug"],
                "herp",
                url,
                "warn",
                "HERP listing marker missing",
            )
    return _classify(row, "herp", url, resp)


async def _probe_gupy(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    tenant = _token_from_config(row["monitor_config"], "tenant")
    tenant = normalize_gupy_tenant(tenant)
    if not tenant:
        from src.workspace._compat import detect_ats_from_url

        parsed = urlparse(row["board_url"])
        host = (parsed.hostname or "").lower()
        tenant = (
            host.removesuffix(".gupy.io")
            if detect_ats_from_url(row["board_url"]) == "gupy"
            else None
        )
    if tenant is None:
        return ProbeResult(
            row["board_slug"],
            "gupy",
            row["board_url"],
            "warn",
            "no valid tenant in monitor_config or Gupy URL",
        )

    url = f"https://{tenant}.gupy.io/"
    resp = await _retry(lambda: _get(client, url, follow_redirects=False))
    if isinstance(resp, httpx.Response):
        if resp.status_code in {404, 410}:
            return ProbeResult(row["board_slug"], "gupy", url, "fail", "board not found")
        if resp.is_redirect:
            return ProbeResult(row["board_slug"], "gupy", url, "warn", "unexpected redirect")
        if resp.status_code == 200 and 'id="__NEXT_DATA__"' not in resp.text:
            return ProbeResult(
                row["board_slug"],
                "gupy",
                url,
                "warn",
                "Gupy NextData marker missing",
            )
    return _classify(row, "gupy", url, resp)


async def _probe_cornerstone(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    try:
        raw_config = json.loads(row["monitor_config"]) if row["monitor_config"] else {}
    except (json.JSONDecodeError, TypeError):
        raw_config = {}
    config = raw_config if isinstance(raw_config, dict) else {}
    board = cornerstone_board_from_metadata(config) or cornerstone_board_from_url(row["board_url"])
    if board is None:
        return ProbeResult(
            row["board_slug"],
            "cornerstone",
            row["board_url"],
            "warn",
            "no valid tenant/site_id/corp in monitor_config or Cornerstone URL",
        )

    url = board.listing_url()
    resp = await _retry(lambda: _get(client, url, follow_redirects=False))
    if isinstance(resp, httpx.Response):
        if resp.status_code in {404, 410}:
            return ProbeResult(row["board_slug"], "cornerstone", url, "fail", "board not found")
        if resp.is_redirect:
            return ProbeResult(row["board_slug"], "cornerstone", url, "warn", "unexpected redirect")
        if resp.status_code == 200:
            try:
                extract_cornerstone_context(resp.text, board)
            except ValueError as exc:
                return ProbeResult(row["board_slug"], "cornerstone", url, "warn", str(exc))
    return _classify(row, "cornerstone", url, resp)


async def _probe_dayforce(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    try:
        raw_config = json.loads(row["monitor_config"]) if row["monitor_config"] else {}
    except (json.JSONDecodeError, TypeError):
        raw_config = {}
    config = raw_config if isinstance(raw_config, dict) else {}
    board = dayforce_board_from_metadata(config) or dayforce_board_from_url(row["board_url"])
    if board is None:
        return ProbeResult(
            row["board_slug"],
            "dayforce",
            row["board_url"],
            "warn",
            "no valid tenant/portal in monitor_config or Dayforce URL",
        )

    url = board.listing_url()
    resp = await _retry(lambda: _get(client, url, follow_redirects=False))
    if isinstance(resp, httpx.Response):
        if resp.status_code in {404, 410}:
            return ProbeResult(row["board_slug"], "dayforce", url, "fail", "board not found")
        if resp.is_redirect:
            target = resolve_dayforce_listing_redirect(board, url, resp.headers.get("location"))
            if target is None:
                return ProbeResult(
                    row["board_slug"],
                    "dayforce",
                    url,
                    "warn",
                    "unexpected redirect",
                )
            url = target
            resp = await _retry(lambda: _get(client, url, follow_redirects=False))
        if isinstance(resp, httpx.Response) and resp.status_code == 200:
            try:
                site = extract_dayforce_site(resp.text, board)
            except ValueError as exc:
                return ProbeResult(row["board_slug"], "dayforce", url, "warn", str(exc))
            redirect_culture = dayforce_listing_culture_from_url(url)
            if redirect_culture and redirect_culture.casefold() != site.culture.casefold():
                return ProbeResult(
                    row["board_slug"],
                    "dayforce",
                    url,
                    "warn",
                    "localized redirect does not match listing culture",
                )
            if site.disabled:
                return ProbeResult(row["board_slug"], "dayforce", url, "fail", "board disabled")
    return _classify(row, "dayforce", url, resp)


async def _probe_darwinbox(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    try:
        raw_config = json.loads(row["monitor_config"]) if row["monitor_config"] else {}
    except (json.JSONDecodeError, TypeError):
        raw_config = {}
    config = raw_config if isinstance(raw_config, dict) else {}
    board = darwinbox_board_from_metadata(config) or darwinbox_board_from_url(row["board_url"])
    if board is None:
        return ProbeResult(
            row["board_slug"],
            "darwinbox",
            row["board_url"],
            "warn",
            "no valid host/company_id in monitor_config or Darwinbox URL",
        )

    url = board.listing_url()
    resp = await _retry(lambda: _get(client, url, follow_redirects=False))
    if isinstance(resp, httpx.Response) and resp.status_code in {404, 410}:
        return ProbeResult(row["board_slug"], "darwinbox", url, "fail", "board not found")
    return _classify(row, "darwinbox", url, resp)


async def _probe_hrmos(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    tenant = _token_from_config(row["monitor_config"], "tenant")
    tenant = tenant.strip().lower() if tenant else None
    if not tenant:
        from src.workspace._compat import detect_ats_from_url

        parsed = urlparse(row["board_url"])
        segments = [segment for segment in parsed.path.split("/") if segment]
        tenant = (
            segments[1].lower()
            if detect_ats_from_url(row["board_url"]) == "hrmos" and len(segments) >= 3
            else None
        )
    if not tenant or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", tenant) is None:
        return ProbeResult(
            row["board_slug"],
            "hrmos",
            row["board_url"],
            "warn",
            "no valid tenant in monitor_config or HRMOS URL",
        )

    url = f"https://hrmos.co/pages/{tenant}/jobs"
    resp = await _retry(lambda: _get(client, url, follow_redirects=False))
    if isinstance(resp, httpx.Response):
        if resp.status_code in {404, 410}:
            return ProbeResult(row["board_slug"], "hrmos", url, "fail", "board not found")
        if resp.is_redirect:
            return ProbeResult(row["board_slug"], "hrmos", url, "warn", "unexpected redirect")
        if (
            resp.status_code == 200
            and re.search(r"\bid=[\"']jsi-joblist[\"']", resp.text, re.IGNORECASE) is None
        ):
            return ProbeResult(
                row["board_slug"],
                "hrmos",
                url,
                "warn",
                "HRMOS listing marker missing",
            )
    return _classify(row, "hrmos", url, resp)


async def _probe_recruitee(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    api_base = _token_from_config(row["monitor_config"], "api_base")
    slug = _token_from_config(row["monitor_config"], "slug")
    if api_base:
        parsed_api_base = urlparse(api_base)
        if parsed_api_base.scheme not in {"http", "https"} or not parsed_api_base.hostname:
            return ProbeResult(
                row["board_slug"],
                "recruitee",
                row["board_url"],
                "warn",
                "invalid api_base in monitor_config",
            )
        base = api_base.rstrip("/")
    elif slug:
        base = f"https://{slug}.recruitee.com"
    else:
        host = urlparse(row["board_url"]).hostname or ""
        base = f"https://{host}" if host else ""

    if not base:
        return ProbeResult(
            row["board_slug"],
            "recruitee",
            row["board_url"],
            "warn",
            "cannot parse host",
        )
    url = f"{base}/api/offers/"
    resp = await _retry(lambda: _get(client, url))
    return _classify(row, "recruitee", url, resp)


async def _probe_recruiterbox(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    cfg: dict[str, object] = {}
    if row["monitor_config"]:
        with contextlib.suppress(json.JSONDecodeError):
            decoded = json.loads(row["monitor_config"])
            if isinstance(decoded, dict):
                cfg = decoded
    board = recruiterbox_board_from_metadata(cfg) or recruiterbox_board_from_url(row["board_url"])
    if board is None:
        return ProbeResult(
            row["board_slug"],
            "recruiterbox",
            row["board_url"],
            "warn",
            "no valid tenant in monitor_config or Recruiterbox URL",
        )

    url = board.page_url(1)
    resp = await _retry(lambda: _get(client, url, follow_redirects=False))
    if isinstance(resp, httpx.Response) and resp.status_code == 200:
        if recruiterbox_inactive_from_html(resp.text):
            return ProbeResult(
                row["board_slug"],
                "recruiterbox",
                url,
                "fail",
                "Recruiterbox account is inactive",
            )
        total = recruiterbox_total_from_html(resp.text)
        if total is None:
            return ProbeResult(
                row["board_slug"],
                "recruiterbox",
                url,
                "warn",
                "Recruiterbox listing marker or job total missing",
            )
        return ProbeResult(
            row["board_slug"],
            "recruiterbox",
            url,
            "ok",
            f"200 ({total} jobs)",
        )
    return _classify(row, "recruiterbox", url, resp)


async def _probe_keka(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    cfg: dict[str, object] = {}
    if row["monitor_config"]:
        with contextlib.suppress(json.JSONDecodeError):
            decoded = json.loads(row["monitor_config"])
            if isinstance(decoded, dict):
                cfg = decoded
    configured = keka_board_from_metadata(cfg)
    direct = keka_board_from_url(row["board_url"])
    if configured is None and any(key in cfg for key in ("tenant", "portal", "identifier")):
        return ProbeResult(
            row["board_slug"],
            "keka",
            row["board_url"],
            "warn",
            "invalid Keka portal identity in monitor_config",
        )
    if (
        configured is not None
        and direct is not None
        and (configured.tenant != direct.tenant or configured.portal != direct.portal)
    ):
        return ProbeResult(
            row["board_slug"],
            "keka",
            row["board_url"],
            "fail",
            "configured Keka portal does not match board URL",
        )
    board = configured or direct
    if board is None:
        return ProbeResult(
            row["board_slug"],
            "keka",
            row["board_url"],
            "warn",
            "no valid Keka tenant/portal in monitor_config or URL",
        )

    url = board.listing_url()
    resp = await _retry(lambda: _get(client, url, follow_redirects=False))
    if isinstance(resp, httpx.Response):
        if resp.status_code in {404, 410}:
            return ProbeResult(row["board_slug"], "keka", url, "fail", "portal not found")
        if resp.is_redirect and is_keka_forbidden_redirect(board, resp.headers.get("location")):
            return ProbeResult(
                row["board_slug"],
                "keka",
                url,
                "fail",
                "Keka portal is forbidden",
            )
        if resp.status_code == 200:
            identifier = extract_keka_identifier(resp.text)
            if identifier is None:
                return ProbeResult(
                    row["board_slug"],
                    "keka",
                    url,
                    "warn",
                    "Keka career-portal identity missing",
                )
            if board.identifier is not None and board.identifier != identifier:
                return ProbeResult(
                    row["board_slug"],
                    "keka",
                    url,
                    "fail",
                    "Keka live portal identity changed",
                )
            return ProbeResult(row["board_slug"], "keka", url, "ok", "200 (identity verified)")
    return _classify(row, "keka", url, resp)


async def _probe_taleo(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    cfg: dict[str, object] = {}
    if row["monitor_config"]:
        with contextlib.suppress(json.JSONDecodeError):
            decoded = json.loads(row["monitor_config"])
            if isinstance(decoded, dict):
                cfg = decoded
    board = taleo_board_from_metadata(cfg) or taleo_board_from_url(row["board_url"])
    if board is None:
        return ProbeResult(
            row["board_slug"],
            "taleo",
            row["board_url"],
            "warn",
            "no valid identity in monitor_config or Taleo TBE URL",
        )

    url = board.listing_url()
    resp = await _retry(lambda: _get(client, url, follow_redirects=False))
    if isinstance(resp, httpx.Response) and resp.status_code == 200:
        total = taleo_total_from_html(resp.text)
        if total is None:
            if taleo_listing_marker_from_html(resp.text):
                return ProbeResult(
                    row["board_slug"],
                    "taleo",
                    url,
                    "ok",
                    "200 (cursor listing verified)",
                )
            return ProbeResult(
                row["board_slug"],
                "taleo",
                url,
                "warn",
                "Taleo listing marker or job total missing",
            )
        return ProbeResult(row["board_slug"], "taleo", url, "ok", f"200 ({total} jobs)")
    return _classify(row, "taleo", url, resp)


async def _probe_avature(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    cfg: dict[str, object] = {}
    if row["monitor_config"]:
        with contextlib.suppress(json.JSONDecodeError):
            decoded = json.loads(row["monitor_config"])
            if isinstance(decoded, dict):
                cfg = decoded
    configured = avature_board_from_metadata(cfg)
    direct = avature_board_from_url(row["board_url"], allow_custom_host=True)
    if configured is None and "listing_url" in cfg:
        return ProbeResult(
            row["board_slug"],
            "avature",
            row["board_url"],
            "warn",
            "invalid Avature listing_url in monitor_config",
        )
    if (
        configured is not None
        and direct is not None
        and configured.listing_url.casefold() != direct.listing_url.casefold()
    ):
        return ProbeResult(
            row["board_slug"],
            "avature",
            configured.listing_url,
            "fail",
            "configured Avature portal does not match board URL",
        )
    board = configured or direct
    if board is None:
        return ProbeResult(
            row["board_slug"],
            "avature",
            row["board_url"],
            "warn",
            "no valid Avature listing identity",
        )

    url = board.listing_url
    resp = await _retry(lambda: _get(client, url, follow_redirects=True))
    if isinstance(resp, httpx.Response) and resp.status_code == 200:
        page = parse_avature_page(resp.text, url)
        if page is None:
            return ProbeResult(
                row["board_slug"],
                "avature",
                url,
                "warn",
                "Avature portal metadata missing",
            )
        if configured is not None and page.board.listing_url.casefold() != url.casefold():
            return ProbeResult(
                row["board_slug"],
                "avature",
                url,
                "fail",
                "configured Avature portal redirected to another identity",
            )
        configured_portal_id = cfg.get("portal_id")
        if configured_portal_id is not None and str(configured_portal_id) != page.portal_id:
            return ProbeResult(
                row["board_slug"],
                "avature",
                url,
                "fail",
                "configured Avature portal ID changed",
            )
        if page.board.page == "SearchJobsMaps":
            return ProbeResult(
                row["board_slug"],
                "avature",
                url,
                "ok",
                "200 (map listing verified)",
            )
        if page.total is None or (page.total > 0 and not page.jobs):
            return ProbeResult(
                row["board_slug"],
                "avature",
                url,
                "warn",
                "Avature result count or first-page job links missing",
            )
        suffix = "+" if not page.total_exact else ""
        return ProbeResult(
            row["board_slug"],
            "avature",
            url,
            "ok",
            f"200 ({page.total}{suffix} jobs)",
        )
    return _classify(row, "avature", url, resp)


async def _probe_jobvite(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    cfg: dict[str, object] = {}
    if row["monitor_config"]:
        with contextlib.suppress(json.JSONDecodeError):
            decoded = json.loads(row["monitor_config"])
            if isinstance(decoded, dict):
                cfg = decoded
    configured = jobvite_board_from_metadata(cfg)
    direct = jobvite_board_from_url(row["board_url"])
    if configured is None and ({"tenant", "listing_url"} & cfg.keys()):
        return ProbeResult(
            row["board_slug"],
            "jobvite",
            row["board_url"],
            "warn",
            "invalid Jobvite tenant/listing_url in monitor_config",
        )
    if configured is not None and direct is not None and configured.tenant != direct.tenant:
        return ProbeResult(
            row["board_slug"],
            "jobvite",
            configured.listing_url,
            "fail",
            "configured Jobvite tenant does not match board URL",
        )
    board = configured or direct
    if board is None:
        return ProbeResult(
            row["board_slug"],
            "jobvite",
            row["board_url"],
            "warn",
            "no valid Jobvite listing identity",
        )

    from src.core.monitors.jobvite import resolve_listing

    try:
        resolved, jobs = await resolve_listing(board, client, terminal=False)
    except PaginationFetchError as exc:
        if exc.last_status in {404, 410} or is_jobvite_invalid_redirect(exc.last_location):
            return ProbeResult(
                row["board_slug"],
                "jobvite",
                board.listing_url,
                "fail",
                f"terminal Jobvite listing response ({exc.last_status})",
            )
        return ProbeResult(
            row["board_slug"],
            "jobvite",
            board.listing_url,
            "warn",
            f"Jobvite listing fetch failed ({exc.last_status or exc.last_error})",
        )
    except ValueError as exc:
        return ProbeResult(
            row["board_slug"],
            "jobvite",
            board.listing_url,
            "warn",
            str(exc),
        )
    return ProbeResult(
        row["board_slug"],
        "jobvite",
        resolved.listing_url,
        "ok",
        f"200 ({len(jobs)} jobs)",
    )


async def _probe_pageup(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    cfg: dict[str, object] = {}
    if row["monitor_config"]:
        with contextlib.suppress(json.JSONDecodeError):
            decoded = json.loads(row["monitor_config"])
            if isinstance(decoded, dict):
                cfg = decoded
    configured = pageup_board_from_metadata(cfg)
    direct = pageup_board_from_url(row["board_url"])
    identity_keys = {"instance", "source_pointer", "locale", "listing_url"}
    if configured is None and identity_keys & cfg.keys():
        return ProbeResult(
            row["board_slug"],
            "pageup",
            row["board_url"],
            "warn",
            "invalid PageUp identity in monitor_config",
        )
    if configured is not None and direct is not None and configured != direct:
        return ProbeResult(
            row["board_slug"],
            "pageup",
            configured.listing_url,
            "fail",
            "configured PageUp identity does not match board URL",
        )
    board = configured or direct
    if board is None:
        return ProbeResult(
            row["board_slug"],
            "pageup",
            row["board_url"],
            "warn",
            "no valid PageUp listing identity",
        )

    from src.core.monitors.pageup import (
        PROBE_PAGE_SIZE,
        _fetch_listing_page,
        _parse_listing_page,
    )

    try:
        url, document = await _fetch_listing_page(
            board,
            client,
            page=1,
            page_size=PROBE_PAGE_SIZE,
            terminal=False,
        )
        _jobs, total, _has_next = _parse_listing_page(
            document,
            url,
            board,
            page=1,
            page_size=PROBE_PAGE_SIZE,
            expected_total=None,
        )
    except PaginationFetchError as exc:
        status = "fail" if exc.last_status in {404, 410} else "warn"
        return ProbeResult(
            row["board_slug"],
            "pageup",
            board.listing_url,
            status,
            f"PageUp listing fetch failed ({exc.last_status or exc.last_error})",
        )
    except ValueError as exc:
        return ProbeResult(
            row["board_slug"],
            "pageup",
            board.listing_url,
            "warn",
            str(exc),
        )
    return ProbeResult(
        row["board_slug"],
        "pageup",
        board.listing_url,
        "ok",
        f"200 ({total} jobs)",
    )


async def _probe_ukg(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    cfg: dict[str, object] = {}
    if row["monitor_config"]:
        with contextlib.suppress(json.JSONDecodeError):
            decoded = json.loads(row["monitor_config"])
            if isinstance(decoded, dict):
                cfg = decoded
    configured = ukg_board_from_metadata(cfg)
    direct = ukg_board_from_url(row["board_url"])
    if configured is not None and direct is not None and configured != direct:
        return ProbeResult(
            row["board_slug"],
            "ukg",
            configured.listing_url(),
            "fail",
            "configured UKG board does not match board URL",
        )
    board = configured or direct
    if board is None:
        return ProbeResult(
            row["board_slug"],
            "ukg",
            row["board_url"],
            "warn",
            "no valid UKG board identity",
        )

    url = board.search_url()
    payload = {"opportunitySearch": {"Top": 1, "Skip": 0, "QueryString": "", "Filters": []}}
    resp = await _retry(
        lambda: _get(
            client,
            url,
            method="POST",
            json=payload,
            headers={"content-type": "application/json"},
            follow_redirects=False,
        )
    )
    if isinstance(resp, httpx.Response) and resp.status_code == 200:
        try:
            data = resp.json()
        except ValueError:
            data = None
        total = data.get("totalCount") if isinstance(data, dict) else None
        rows = data.get("opportunities") if isinstance(data, dict) else None
        if (
            not isinstance(total, int)
            or isinstance(total, bool)
            or total < 0
            or not isinstance(rows, list)
            or len(rows) > 1
        ):
            return ProbeResult(
                row["board_slug"],
                "ukg",
                url,
                "warn",
                "UKG search response shape changed",
            )
        return ProbeResult(row["board_slug"], "ukg", url, "ok", f"200 ({total} jobs)")
    return _classify(row, "ukg", url, resp)


async def _probe_beisen(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    cfg: dict[str, object] = {}
    if row["monitor_config"]:
        with contextlib.suppress(json.JSONDecodeError):
            decoded = json.loads(row["monitor_config"])
            if isinstance(decoded, dict):
                cfg = decoded
    configured = beisen_board_from_metadata(cfg)
    direct = beisen_board_from_url(row["board_url"])
    board = configured or direct
    if board is None:
        return ProbeResult(
            row["board_slug"],
            "beisen",
            row["board_url"],
            "warn",
            "no valid tenant or portal metadata",
        )
    url = board.listing_url() if configured and configured.variant == "legacy" else board.root_url()
    resp = await _retry(
        lambda: _get(
            client,
            url,
            follow_redirects=False,
            headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": DEFAULT_ACCEPT},
        )
    )
    if isinstance(resp, httpx.Response) and resp.status_code == 200:
        if configured and configured.variant == "legacy":
            valid = "new_zhiye_com" in resp.text
            disabled = False
        else:
            try:
                bootstrap = extract_beisen_bootstrap(resp.text, board.tenant)
            except ValueError as exc:
                return ProbeResult(row["board_slug"], "beisen", url, "warn", str(exc))
            valid = bootstrap is not None
            disabled = bootstrap is not None and not bootstrap[1]
        if disabled:
            return ProbeResult(row["board_slug"], "beisen", url, "fail", "portal disabled")
        if not valid:
            return ProbeResult(
                row["board_slug"],
                "beisen",
                url,
                "warn",
                "Beisen portal marker missing",
            )
        return ProbeResult(row["board_slug"], "beisen", url, "ok", "200")
    return _classify(row, "beisen", url, resp)


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


async def _probe_rss(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    cfg: dict = {}
    if row["monitor_config"]:
        try:
            decoded = json.loads(row["monitor_config"])
        except (json.JSONDecodeError, TypeError):
            decoded = None
        if isinstance(decoded, dict):
            cfg = decoded

    if cfg.get("preset") == "successfactors" and cfg.get("variant") == "legacy":
        identity = successfactors_legacy_board_from_metadata(cfg)
        direct = successfactors_legacy_board_from_url(row["board_url"])
        if identity is None:
            identity = direct
        if identity is None or (direct is not None and direct != identity):
            return ProbeResult(
                row["board_slug"],
                "rss",
                row["board_url"],
                "fail",
                "invalid SuccessFactors legacy identity",
            )
        from src.core.monitors import BoardGoneError
        from src.core.monitors._successfactors_legacy import (
            SuccessFactorsLegacyProtocolError,
            probe_legacy,
        )

        try:
            result = await probe_legacy(identity, client)
        except BoardGoneError:
            return ProbeResult(
                row["board_slug"],
                "rss",
                identity.listing_url,
                "fail",
                "legacy SuccessFactors board is gone",
            )
        except (PaginationFetchError, SuccessFactorsLegacyProtocolError) as exc:
            return ProbeResult(
                row["board_slug"],
                "rss",
                identity.listing_url,
                "fail",
                f"legacy SuccessFactors probe failed: {exc}",
            )
        return ProbeResult(
            row["board_slug"],
            "rss",
            identity.listing_url,
            "ok",
            f"legacy SuccessFactors DWR: {result['jobs']} jobs",
        )

    feed_url = cfg.get("feed_url")
    if not isinstance(feed_url, str) or not feed_url:
        preset = cfg.get("preset")
        suffix = "/jobs.rss" if preset == "teamtailor" else "/googlefeed.xml"
        parsed = urlparse(row["board_url"])
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            feed_url = f"{parsed.scheme}://{parsed.netloc}{suffix}"
    if not isinstance(feed_url, str) or not feed_url:
        return ProbeResult(
            row["board_slug"],
            "rss",
            row["board_url"],
            "warn",
            "no feed_url in monitor_config",
        )
    # Reuse the runtime's streamed XML parser so a retired feed returning an
    # HTML landing page with HTTP 200 cannot be reported healthy here.
    from src.core.monitors.rss import _probe_feed

    valid, count = await _probe_feed(feed_url, client, cfg.get("preset"))
    if not valid:
        return ProbeResult(
            row["board_slug"],
            "rss",
            feed_url,
            "fail",
            "feed did not return valid RSS/XML",
        )
    return ProbeResult(
        row["board_slug"],
        "rss",
        feed_url,
        "ok",
        f"valid RSS/XML: {count or 0} jobs",
    )


async def _probe_dom(row: dict, client: httpx.AsyncClient) -> ProbeResult:
    """Probe bounded provider-specific static DOM configurations."""
    try:
        decoded = json.loads(row["monitor_config"] or "{}")
    except (json.JSONDecodeError, TypeError):
        decoded = {}
    cfg = decoded if isinstance(decoded, dict) else {}
    configured_medium = cfg.get("prospective_board")
    if configured_medium is None:
        return ProbeResult(
            row["board_slug"],
            "unsupported",
            row["board_url"],
            "skipped",
            "no targeted probe for this DOM configuration",
        )
    if (
        not isinstance(configured_medium, str)
        or re.fullmatch(r"[1-9]\d{0,11}", configured_medium) is None
    ):
        return ProbeResult(
            row["board_slug"],
            "dom",
            row["board_url"],
            "fail",
            "invalid Prospective medium identity",
        )

    from src.core.monitors.dom import _prospective_probe_config
    from src.shared.http_retry import ResponseBodyTooLargeError, fetch_text_page_with_retry

    try:
        html = await fetch_text_page_with_retry(
            client,
            row["board_url"],
            retryable_statuses={202, 401, 403},
            end_of_pagination_statuses=set(),
            require_nonempty=True,
            max_bytes=2 * 1024 * 1024,
        )
    except PaginationFetchError as exc:
        status = "fail" if exc.last_status in {404, 410} else "warn"
        return ProbeResult(
            row["board_slug"],
            "dom",
            row["board_url"],
            status,
            f"Prospective listing fetch failed ({exc.last_status or exc.last_error})",
        )
    except ResponseBodyTooLargeError:
        return ProbeResult(
            row["board_slug"],
            "dom",
            row["board_url"],
            "fail",
            "Prospective listing exceeded the bounded response size",
        )

    detected = _prospective_probe_config(html or "", row["board_url"])
    if detected is None:
        return ProbeResult(
            row["board_slug"],
            "dom",
            row["board_url"],
            "fail",
            "Prospective provider identity or inventory contract failed",
        )
    if detected["prospective_board"] != configured_medium:
        return ProbeResult(
            row["board_slug"],
            "dom",
            row["board_url"],
            "fail",
            "configured Prospective medium does not match listing assets",
        )
    preset_keys = ["url_filter", "rich_rows", "empty_states"]
    if detected["urls"]:
        preset_keys.append("prospective_canonical_path")
    for key in preset_keys:
        if cfg.get(key) != detected.get(key):
            return ProbeResult(
                row["board_slug"],
                "dom",
                row["board_url"],
                "fail",
                f"configured Prospective {key} does not match the provider preset",
            )
    return ProbeResult(
        row["board_slug"],
        "dom",
        row["board_url"],
        "ok",
        f"valid Prospective listing: {detected['urls']} jobs",
    )


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
    "adp": _probe_adp,
    "avature": _probe_avature,
    "jobvite": _probe_jobvite,
    "pageup": _probe_pageup,
    "ukg": _probe_ukg,
    "greenhouse": _probe_greenhouse,
    "lever": _probe_lever,
    "ashby": _probe_ashby,
    "bamboohr": _probe_bamboohr,
    "beisen": _probe_beisen,
    "paycom": _probe_paycom,
    "jazzhr": _probe_jazzhr,
    "icims": _probe_icims,
    "gupy": _probe_gupy,
    "cornerstone": _probe_cornerstone,
    "darwinbox": _probe_darwinbox,
    "dayforce": _probe_dayforce,
    "dom": _probe_dom,
    "herp": _probe_herp,
    "hrmos": _probe_hrmos,
    "recruitee": _probe_recruitee,
    "recruiterbox": _probe_recruiterbox,
    "keka": _probe_keka,
    "taleo": _probe_taleo,
    "rippling": _probe_rippling,
    "smartrecruiters": _probe_smartrecruiters,
    "workday": _probe_workday,
    "rss": _probe_rss,
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
