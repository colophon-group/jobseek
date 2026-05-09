from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.core.monitors.oracle_hcm import _RETRY_ATTEMPTS, _get_with_retry
from src.core.scrapers.oracle_hcm import _build_detail_url, scrape


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("GET", "https://example.com/"))


class TestGetWithRetry:
    @pytest.mark.parametrize("status", [200, 204, 404, 410])
    async def test_returns_immediately_on_non_transient_status(self, status):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=_response(status))

        resp = await _get_with_retry(client, "https://example.com/")

        assert resp.status_code == status
        assert client.get.await_count == 1

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    async def test_retries_on_transient_status_then_succeeds(self, status):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[_response(status), _response(status), _response(200)])

        with patch("src.core.monitors.oracle_hcm.asyncio.sleep", new_callable=AsyncMock):
            resp = await _get_with_retry(client, "https://example.com/")

        assert resp.status_code == 200
        assert client.get.await_count == 3

    async def test_returns_final_transient_response_after_exhaustion(self):
        """After _RETRY_ATTEMPTS transient responses, return the last one (not
        raise) so the caller's raise_for_status() still triggers the board-level
        _RECORD_FAILURE path with the correct status code."""
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=_response(503))

        with patch("src.core.monitors.oracle_hcm.asyncio.sleep", new_callable=AsyncMock):
            resp = await _get_with_retry(client, "https://example.com/")

        assert resp.status_code == 503
        assert client.get.await_count == _RETRY_ATTEMPTS

    async def test_does_not_sleep_after_final_failed_attempt(self):
        """Sleep is for back-off between attempts — sleeping after the last
        attempt (when we're giving up anyway) just pointlessly delays the
        caller's error handling."""
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=_response(503))
        sleep = AsyncMock()

        with patch("src.core.monitors.oracle_hcm.asyncio.sleep", sleep):
            await _get_with_retry(client, "https://example.com/")

        # _RETRY_ATTEMPTS attempts → _RETRY_ATTEMPTS - 1 sleeps between them
        assert sleep.await_count == _RETRY_ATTEMPTS - 1


# ── Vanity-domain config-driven scraper path ────────────────────────
#
# Regression for #2920: Nokia (jobs.nokia.com) and TI (careers.ti.com) host
# Oracle HCM Cloud SPAs on vanity domains. The `_ORACLE_HCM_URL_RE` auto-detect
# regex requires a *.fa.*.oraclecloud.com host, which vanity URLs don't have —
# so boards.csv must point at the canonical tenant via explicit `host` + `site`
# config. This path bypasses the regex entirely and goes straight through
# `_build_detail_url(host, site)`.
#
# The job id has to be extracted from the *vanity* URL (not the canonical
# host), so `_JOB_ID_RE` must match `/en/job/{id}` paths regardless of host.


def _oracle_hcm_payload(
    req_id: str,
    title: str,
    *,
    description: str = "<p>Role description</p>",
    location: str = "United States",
) -> dict:
    """Mimic the shape of the Oracle HCM `recruitingCEJobRequisitionDetails` REST response."""
    return {
        "items": [
            {
                "Id": req_id,
                "Title": title,
                "PrimaryLocation": location,
                "ExternalDescriptionStr": description,
                "ExternalQualificationsStr": "",
                "ExternalResponsibilitiesStr": "",
                "ExternalPostedStartDate": "2026-05-01",
                "JobSchedule": "Full-time",
            }
        ],
    }


class TestBuildDetailUrl:
    def test_uses_canonical_tenant_not_vanity_host(self):
        # Caller passes the canonical Oracle tenant — even when the job URL
        # lives on a vanity domain. The vanity host (jobs.nokia.com) does NOT
        # appear in the API URL because the vanity host returns 405 from the
        # Oracle API Gateway.
        url = _build_detail_url("fa-evmr-saasfaprod1.fa.ocs.oraclecloud.com", "CX_1")
        assert "fa-evmr-saasfaprod1.fa.ocs.oraclecloud.com" in url
        assert "siteNumber=CX_1" in url
        assert "jobs.nokia.com" not in url

    def test_url_pattern_compatible_with_api_sniffer(self):
        # Sanity: the {req_id} placeholder is the one api_sniffer substitutes
        # via `url_pattern` — must be present unchanged so the substitution
        # actually fires.
        url = _build_detail_url("edbz.fa.us2.oraclecloud.com", "CX")
        assert "{req_id}" in url


@pytest.mark.asyncio
class TestVanityDomainConfigPath:
    """End-to-end through `oracle_hcm.scrape` → `api_sniffer._scrape_http`."""

    async def test_nokia_vanity_url_with_explicit_config(self):
        """Nokia URL on jobs.nokia.com routes to the canonical tenant via config."""
        api_calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            api_calls.append(url)
            return httpx.Response(
                200,
                json=_oracle_hcm_payload("36037", "Machine Learning Test Capability Eng."),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            content = await scrape(
                "https://jobs.nokia.com/en/job/36037",
                {"host": "fa-evmr-saasfaprod1.fa.ocs.oraclecloud.com", "site": "CX_1"},
                client,
            )

        assert len(api_calls) == 1
        # API must hit the canonical tenant, not the vanity host
        assert "fa-evmr-saasfaprod1.fa.ocs.oraclecloud.com" in api_calls[0]
        assert "jobs.nokia.com" not in api_calls[0]
        # req_id from vanity URL must land in the API URL (not the literal `{req_id}`)
        assert '"36037"' in api_calls[0] or "%2236037%22" in api_calls[0]
        assert "siteNumber=CX_1" in api_calls[0]
        assert content.title == "Machine Learning Test Capability Eng."
        assert content.locations == ["United States"]
        assert content.description

    async def test_ti_vanity_url_with_explicit_config(self):
        """TI URL on careers.ti.com routes to its own canonical tenant via config."""
        api_calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            api_calls.append(url)
            return httpx.Response(
                200,
                json=_oracle_hcm_payload(
                    "25009746",
                    "Lead Software Engineer | Radar SDK",
                    description="<p>Role details</p>",
                    location="Bengaluru, India",
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            content = await scrape(
                "https://careers.ti.com/en/job/25009746",
                {"host": "edbz.fa.us2.oraclecloud.com", "site": "CX"},
                client,
            )

        assert len(api_calls) == 1
        assert "edbz.fa.us2.oraclecloud.com" in api_calls[0]
        assert "careers.ti.com" not in api_calls[0]
        assert "siteNumber=CX" in api_calls[0]
        assert content.title == "Lead Software Engineer | Radar SDK"
        assert content.locations == ["Bengaluru, India"]
