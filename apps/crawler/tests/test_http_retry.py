"""Tests for ``src.shared.http_retry`` — the bounded retry utility used by
paginating monitors (#2722) to distinguish transient errors from
legitimate end-of-pagination."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from prometheus_client import REGISTRY

from src.shared.http_retry import (
    _RETRYABLE_STATUSES,
    END_OF_PAGINATION_STATUSES,
    PaginationFetchError,
    fetch_with_retry,
)


def _samples_for(metric_name: str) -> list[dict[str, Any]]:
    """Return all current value samples for a Prometheus counter."""
    out: list[dict[str, Any]] = []
    for metric in REGISTRY.collect():
        if metric.name != metric_name:
            continue
        for sample in metric.samples:
            # Counter exposes both ``<name>_total`` and ``<name>_created`` —
            # we only want the cumulative value samples.
            if sample.name.endswith("_created"):
                continue
            out.append({"labels": dict(sample.labels), "value": sample.value})
    return out


def _value_for(metric_name: str, **labels: str) -> float:
    """Sum samples matching all label kwargs (subset match)."""
    total = 0.0
    for s in _samples_for(metric_name):
        if all(s["labels"].get(k) == v for k, v in labels.items()):
            total += s["value"]
    return total


def _resp(status: int, text: str = "") -> httpx.Response:
    """Build an httpx.Response for stubbing AsyncMock returns."""
    return httpx.Response(status, text=text, request=httpx.Request("GET", "https://x"))


class TestFetchWithRetry:
    async def test_returns_text_on_200(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, "<html>ok</html>"))

        out = await fetch_with_retry(client, "https://example.com/p2")

        assert out == "<html>ok</html>"
        assert client.get.await_count == 1

    async def test_truncates_to_max_chars(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, "x" * 1000))

        out = await fetch_with_retry(client, "https://example.com", max_chars=10)

        assert out == "x" * 10

    async def test_returns_none_on_404(self):
        """404 / 410 are legitimate end-of-pagination — return None, no retry."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(404))

        out = await fetch_with_retry(client, "https://example.com/past-end")

        assert out is None
        assert client.get.await_count == 1

    async def test_returns_none_on_410(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(410))

        out = await fetch_with_retry(client, "https://example.com/gone")

        assert out is None

    async def test_returns_none_on_non_retryable_4xx(self):
        """Non-retryable 4xx (403, etc.) returns None — same lenient
        semantics as the prior ``fetch_page_text``. Logged as anomaly."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(403))

        out = await fetch_with_retry(client, "https://example.com/forbidden")

        assert out is None
        assert client.get.await_count == 1

    async def test_retries_on_503_then_succeeds(self):
        """Transient 503 retries, then 200 returns text."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[_resp(503), _resp(503), _resp(200, "<html>recovered</html>")]
        )

        out = await fetch_with_retry(client, "https://example.com", base_delay=0.001)

        assert out == "<html>recovered</html>"
        assert client.get.await_count == 3

    async def test_retries_on_429_then_succeeds(self):
        """429 (rate-limited) is retryable."""
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[_resp(429), _resp(200, "ok")])

        out = await fetch_with_retry(client, "https://example.com", base_delay=0.001)

        assert out == "ok"
        assert client.get.await_count == 2

    async def test_raises_after_persistent_5xx(self):
        """Persistent 5xx exhausts retries -> raises PaginationFetchError."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(503))

        with pytest.raises(PaginationFetchError) as exc_info:
            await fetch_with_retry(client, "https://example.com/flaky", retries=3, base_delay=0.001)

        assert exc_info.value.url == "https://example.com/flaky"
        assert exc_info.value.attempts == 3
        assert exc_info.value.last_status == 503
        assert client.get.await_count == 3

    async def test_raises_after_persistent_timeout(self):
        """Timeout exhausts retries -> raises PaginationFetchError with last_error."""
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.TimeoutException("read timeout"))

        with pytest.raises(PaginationFetchError) as exc_info:
            await fetch_with_retry(client, "https://example.com", retries=3, base_delay=0.001)

        assert exc_info.value.last_error == "TimeoutException"
        assert exc_info.value.last_status is None
        assert client.get.await_count == 3

    async def test_raises_after_persistent_network_error(self):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("conn refused"))

        with pytest.raises(PaginationFetchError):
            await fetch_with_retry(client, "https://example.com", retries=2, base_delay=0.001)

        assert client.get.await_count == 2

    async def test_recovers_from_timeout(self):
        """Transient timeout, then success on retry."""
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[httpx.TimeoutException("t/o"), _resp(200, "ok")])

        out = await fetch_with_retry(client, "https://example.com", base_delay=0.001)

        assert out == "ok"
        assert client.get.await_count == 2

    async def test_passes_custom_headers(self):
        """``headers`` kwarg is forwarded to client.get — needed for
        sitemap monitor's bot-friendly UA override (#2624)."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, "ok"))

        await fetch_with_retry(
            client,
            "https://example.com",
            headers={"User-Agent": "jobseek-crawler"},
        )

        call_kwargs = client.get.await_args.kwargs
        assert call_kwargs["headers"] == {"User-Agent": "jobseek-crawler"}

    async def test_constants_disjoint(self):
        """Sanity: a status can't be both retryable and end-of-pagination."""
        assert set() == _RETRYABLE_STATUSES & END_OF_PAGINATION_STATUSES

    async def test_cloudflare_5xx_codes_retry(self):
        """Cloudflare-origin 5xx codes (520-526, 530) are retried — they
        showed up as a silent-truncation hole in PR #2736 review when
        the explicit allow-list missed them. Range-based check now
        covers any 5xx; this test pins that contract.
        """
        for status in (520, 521, 522, 523, 524, 525, 526, 530):
            client = AsyncMock()
            client.get = AsyncMock(side_effect=[_resp(status), _resp(200, "ok")])

            out = await fetch_with_retry(client, "https://example.com/cf", base_delay=0.001)

            assert out == "ok", f"status {status} should be retried"
            assert client.get.await_count == 2

    async def test_recovers_from_empty_200(self):
        """Single empty-200 (anti-bot challenge dropping body / partial
        CDN response) is treated as transient (#2739): retry, then
        return the non-empty body on success. The bug shape: ``""`` is
        falsy, so ``_paginate_urls``'s ``if not html: break`` would
        treat it as legitimate end-of-pagination and tombstone the
        un-fetched tail.
        """
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[_resp(200, ""), _resp(200, "<html>ok</html>")])

        out = await fetch_with_retry(client, "https://example.com", base_delay=0.001)

        assert out == "<html>ok</html>"
        assert client.get.await_count == 2

    async def test_raises_after_persistent_empty_200(self):
        """Persistent empty-200 exhausts retries and raises with
        ``last_status=200`` (#2739) — operators pattern-match this in
        logs as the empty-body signal. Returning ``""`` would silently
        truncate pagination on the caller side.
        """
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, ""))

        with pytest.raises(PaginationFetchError) as exc_info:
            await fetch_with_retry(client, "https://example.com/empty", retries=3, base_delay=0.001)

        assert exc_info.value.url == "https://example.com/empty"
        assert exc_info.value.attempts == 3
        assert exc_info.value.last_status == 200
        assert exc_info.value.last_error is None
        assert client.get.await_count == 3

    async def test_non_empty_200_returns_unchanged(self):
        """Pinning the canonical happy path against the empty-200 fix:
        a non-empty 200 still returns the body on the first attempt,
        no extra retries.
        """
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, "x"))  # one byte

        out = await fetch_with_retry(client, "https://example.com")

        assert out == "x"
        assert client.get.await_count == 1

    async def test_zero_retries_raises_immediately(self):
        """``retries=0`` means no attempts are made — function raises
        without consulting the network. Defensive: callers shouldn't
        configure 0, but the contract is at least predictable.
        """
        client = AsyncMock()
        client.get = AsyncMock()

        with pytest.raises(PaginationFetchError):
            await fetch_with_retry(client, "https://example.com", retries=0)

        assert client.get.await_count == 0

    # ── transient_403 opt-in (#2994) ─────────────────────────────────────

    async def test_default_403_returns_none_no_retry(self):
        """Default behaviour preserves the dom-monitor pagination contract:
        a 403 means "this URL is permanently blocked, drop it" — return
        ``None`` so the caller stops paginating without flagging the run
        as a failure. Pinned so the #2994 fix doesn't accidentally
        flip dom-monitor semantics for Indeed and similar.
        """
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(403))

        out = await fetch_with_retry(client, "https://example.com/blocked")

        assert out is None
        assert client.get.await_count == 1

    async def test_transient_403_retries_then_succeeds(self):
        """``transient_403=True``: 403 retries, then 200 returns text.
        Mirrors the 5xx contract — a transient WAF block on a sitemap
        shard often clears within the retry budget.
        """
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[_resp(403), _resp(200, "<urlset/>")])

        out = await fetch_with_retry(
            client,
            "https://example.com/sitemap-shard.xml",
            base_delay=0.001,
            transient_403=True,
        )

        assert out == "<urlset/>"
        assert client.get.await_count == 2

    async def test_transient_403_raises_after_persistent(self):
        """``transient_403=True`` with persistent 403 → raises
        ``PaginationFetchError`` carrying ``last_status=403`` so
        operators can pattern-match the WAF-block signal in logs. This
        is the load-bearing assertion for the mchire flap fix (#2994):
        the call propagates instead of returning ``None``, so the
        monitor cycle records as a failure instead of silently
        dropping the shard.
        """
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(403))

        with pytest.raises(PaginationFetchError) as exc_info:
            await fetch_with_retry(
                client,
                "https://example.com/sitemap-shard.xml",
                retries=3,
                base_delay=0.001,
                transient_403=True,
            )

        assert exc_info.value.last_status == 403
        assert exc_info.value.attempts == 3
        assert client.get.await_count == 3

    async def test_transient_403_also_retries_401(self):
        """401 (unauthorized) shares the WAF/anti-bot semantics for shard
        fetches — covered by the same opt-in. Some CDNs return 401
        instead of 403 for the same block.
        """
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(401))

        with pytest.raises(PaginationFetchError) as exc_info:
            await fetch_with_retry(
                client,
                "https://example.com/sitemap-shard.xml",
                retries=2,
                base_delay=0.001,
                transient_403=True,
            )

        assert exc_info.value.last_status == 401
        assert client.get.await_count == 2

    async def test_transient_403_does_not_affect_404(self):
        """``transient_403`` only changes 401/403 — 404/410 remain
        legitimate end-of-pagination signals. Defensive: don't expand
        the opt-in's scope by accident.
        """
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(404))

        out = await fetch_with_retry(
            client,
            "https://example.com/sitemap-missing.xml",
            transient_403=True,
        )

        assert out is None
        assert client.get.await_count == 1


# ─── Retry observability (#3210) ────────────────────────────────────────


class TestRetryMetrics:
    """End-to-end emission of the retry counters added in #3210.

    Counters are global ``prometheus_client.Counter`` objects in
    ``src.metrics``; each test pins a unique hostname so other tests'
    samples can't contaminate the assertion. We read samples through
    ``REGISTRY.collect()`` (the surface a Grafana scrape sees) rather
    than poking at private counter state.
    """

    async def test_empty_200_storm_emits_per_attempt_then_recovers(self):
        """Two empty-200s then a 200 — the canonical anti-bot retry storm.

        Pins the brief: ``empty_200_total{host} == 2``,
        ``attempts_total{host, outcome="retry"} == 2``,
        ``attempts_total{host, outcome="recovered"} == 1``. Each empty
        body bumps BOTH the anti-bot-specific counter AND the generic
        retry counter so dashboards can aggregate by outcome OR by
        signal without double-bookkeeping.
        """
        host = "storm.empty200.example.com"
        url = f"https://{host}/listings?page=1"

        base_empty = _value_for("crawler_http_retry_empty_200", host=host)
        base_retry = _value_for("crawler_http_retry_attempts", host=host, outcome="retry")
        base_recovered = _value_for("crawler_http_retry_attempts", host=host, outcome="recovered")

        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[_resp(200, ""), _resp(200, ""), _resp(200, "<html>ok</html>")]
        )

        out = await fetch_with_retry(client, url, retries=3, base_delay=0.001)

        assert out == "<html>ok</html>"
        assert client.get.await_count == 3

        assert _value_for("crawler_http_retry_empty_200", host=host) - base_empty == 2.0, (
            "expected exactly 2 empty-200 increments"
        )
        assert (
            _value_for("crawler_http_retry_attempts", host=host, outcome="retry") - base_retry
            == 2.0
        ), "expected exactly 2 retry-outcome increments (one per empty-200)"
        assert (
            _value_for("crawler_http_retry_attempts", host=host, outcome="recovered")
            - base_recovered
            == 1.0
        ), "expected exactly 1 recovered-outcome increment after final 200"

    async def test_exhausted_persistent_5xx_emits_exhausted_outcome(self):
        """Persistent 503 across the entire retry budget → one
        ``exhausted`` increment (and N ``retry`` increments) before
        ``PaginationFetchError`` propagates. Pins the brief assertion
        for the exhaustion case.
        """
        host = "storm.exhausted.example.com"
        url = f"https://{host}/api/list?offset=0"

        base_retry = _value_for("crawler_http_retry_attempts", host=host, outcome="retry")
        base_exhausted = _value_for("crawler_http_retry_attempts", host=host, outcome="exhausted")

        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(503))

        with pytest.raises(PaginationFetchError):
            await fetch_with_retry(client, url, retries=3, base_delay=0.001)

        assert (
            _value_for("crawler_http_retry_attempts", host=host, outcome="exhausted")
            - base_exhausted
            == 1.0
        ), "expected exactly 1 exhausted-outcome increment"
        # Sanity: the 3 retryable 5xx attempts all bumped ``retry``. The
        # brief asserts ``exhausted == 1`` as the load-bearing claim;
        # this extra check pins the symmetric retry-count side so the
        # two outcomes stay aligned in dashboards.
        assert (
            _value_for("crawler_http_retry_attempts", host=host, outcome="retry") - base_retry
            == 3.0
        )

    async def test_transient_403_emits_anti_bot_counter(self):
        """``transient_403=True`` storm increments the anti-bot
        ``transient_403_total`` AND the generic ``attempts_total{retry}``
        — matching the symmetric brief for empty-200.
        """
        host = "storm.transient403.example.com"
        url = f"https://{host}/sitemap-shard.xml"

        base_403 = _value_for("crawler_http_retry_transient_403", host=host)
        base_retry = _value_for("crawler_http_retry_attempts", host=host, outcome="retry")

        client = AsyncMock()
        client.get = AsyncMock(side_effect=[_resp(403), _resp(200, "<urlset/>")])

        out = await fetch_with_retry(client, url, retries=3, base_delay=0.001, transient_403=True)

        assert out == "<urlset/>"
        assert _value_for("crawler_http_retry_transient_403", host=host) - base_403 == 1.0
        assert (
            _value_for("crawler_http_retry_attempts", host=host, outcome="retry") - base_retry
            == 1.0
        )

    async def test_happy_path_emits_no_counters(self):
        """A successful first-attempt 200 must NOT touch any retry
        counter — observability is opt-in for storms, not steady-state
        noise. Pins this so the counter doesn't accidentally bloat
        Prometheus storage with every successful fetch.
        """
        host = "happy.example.com"
        url = f"https://{host}/healthy"

        base_retry = _value_for("crawler_http_retry_attempts", host=host, outcome="retry")
        base_recovered = _value_for("crawler_http_retry_attempts", host=host, outcome="recovered")
        base_exhausted = _value_for("crawler_http_retry_attempts", host=host, outcome="exhausted")

        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, "<html>ok</html>"))

        out = await fetch_with_retry(client, url)

        assert out == "<html>ok</html>"
        assert (
            _value_for("crawler_http_retry_attempts", host=host, outcome="retry") - base_retry
            == 0.0
        )
        assert (
            _value_for("crawler_http_retry_attempts", host=host, outcome="recovered")
            - base_recovered
            == 0.0
        )
        assert (
            _value_for("crawler_http_retry_attempts", host=host, outcome="exhausted")
            - base_exhausted
            == 0.0
        )

    async def test_host_label_lowercases_and_strips_port(self):
        """``http_retry_host`` normalises the URL to the bare hostname
        for cardinality discipline. A URL with uppercase host + port +
        path must land under the lowercased bare-host series, not three
        distinct series — operators query ``by (host)`` and we don't
        want one host to splinter across casing/port variants.
        """
        from src.metrics import http_retry_host

        assert http_retry_host("https://Example.COM:8443/foo?bar=1") == "example.com"
        # Defensive: malformed input degrades to "unknown" rather than
        # raising at the emission site.
        assert http_retry_host("not-a-url") == "unknown"
        assert http_retry_host("") == "unknown"
