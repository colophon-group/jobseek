"""Cron execution metrics — emitted at the start/end of one-shot CLI runs (#2704).

The crawler's long-lived containers (workers, exporter, drain) expose
``/metrics`` on a port and Alloy scrapes them every 15s. Cron-style
runs (``crawler refresh-typesense``, ``crawler backfill-typesense``,
the labeller / error-review GitHub Actions routines) start as
``docker run --rm`` containers and exit before any scrape happens —
so silent failures are invisible to Grafana.

This module provides ``cron_run`` as an async context manager that:

1. Always emits structured logs (``cron.start`` / ``cron.complete``
   with ``status`` + ``duration_s``). These hit Loki via the existing
   stdout collection path — no infra change needed to start using
   them. LogQL recording rules can compute counter / gauge series
   from those events directly.

2. Optionally pushes Prometheus metrics to a Pushgateway when
   ``CRAWLER_PUSHGATEWAY_URL`` is set. Two **gauge** series per job
   (3 jobs × 2 = ~6 series total):

   - ``crawler_cron_last_run_ts{job}`` — unix timestamp of the most
     recent completion regardless of status. A missing series is
     itself the "this cron stopped running" signal:
     ``time() - crawler_cron_last_run_ts > 6 * <interval>`` alerts.
   - ``crawler_cron_last_run_status{job}`` — ``1`` on success, ``0``
     on failure. Operators check the most recent run's outcome with
     ``crawler_cron_last_run_status == 0`` for the alert.

   We deliberately do NOT use a Prometheus Counter here. Pushgateway's
   PUT semantics REPLACE the per-grouping-key series on each push, so
   a fresh-registry-per-call counter would always show ``1`` —
   ``rate()`` would be permanently flat. Cumulative run counts come
   from Loki's ``count_over_time({event="cron.complete"}[1d])`` over
   the structured log emissions instead.

Failure mode of the Pushgateway push itself is intentionally swallowed:
emitting metrics is observability, not correctness — a Pushgateway
outage must not propagate into a `crawler refresh-typesense` failure.
The structured logs continue to be emitted regardless.

Usage::

    from src.cron_metrics import cron_run

    async with cron_run("refresh-typesense"):
        await do_the_work()

The context manager records the run as ``failure`` if the body raises
(re-raising the exception unchanged), or ``success`` otherwise.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import AsyncIterator

import structlog

log = structlog.get_logger()

#: Env var that enables Pushgateway emission. Unset / empty → no push,
#: only structured logs are emitted. The deployment side wires this in
#: ``/home/deploy/.env`` once a Pushgateway service is reachable from the
#: Hetzner host (see issue #2704 for the deployment plan).
_PUSHGATEWAY_URL_ENV = "CRAWLER_PUSHGATEWAY_URL"

#: Pushgateway grouping key prefix. Each cron job gets its own grouping
#: key so concurrent runs don't overwrite each other's
#: ``last_run_ts`` between push and scrape.
_PUSHGATEWAY_JOB = "crawler-cron"


def _push_metrics(job: str, status: str, wall_ts: float) -> None:
    """Push the cron metrics to Pushgateway if configured. Errors are
    logged at ``warning`` and swallowed — the cron run itself must not
    fail because the metrics endpoint is unreachable.

    Uses two gauges per job, both written under the same grouping key
    so successive runs of the same job replace the previous values
    (Pushgateway PUT semantics) — exactly what we want for "most recent
    run" telemetry.
    """
    url = os.environ.get(_PUSHGATEWAY_URL_ENV)
    if not url:
        return
    try:
        from prometheus_client import (
            CollectorRegistry,
            Gauge,
            push_to_gateway,
        )

        registry = CollectorRegistry()
        last_ts = Gauge(
            "crawler_cron_last_run_ts",
            "Unix timestamp of the most recent crawler cron run completion",
            ["job"],
            registry=registry,
        )
        last_status = Gauge(
            "crawler_cron_last_run_status",
            "Status of the most recent crawler cron run (1=success, 0=failure)",
            ["job"],
            registry=registry,
        )
        last_ts.labels(job=job).set(wall_ts)
        last_status.labels(job=job).set(1 if status == "success" else 0)

        push_to_gateway(
            url,
            job=_PUSHGATEWAY_JOB,
            grouping_key={"cron_job": job},
            registry=registry,
        )
    except Exception as exc:  # noqa: BLE001 — observability, not correctness
        log.warning(
            "cron_metrics.push_failed",
            job=job,
            status=status,
            error=type(exc).__name__,
            detail=str(exc),
        )


@contextlib.asynccontextmanager
async def cron_run(job: str) -> AsyncIterator[None]:
    """Wrap a cron-style CLI body with start/end metric emission.

    The structured logs (``cron.start``, ``cron.complete``) always
    fire — Loki's existing stdout collection ingests them. The
    Pushgateway push is gated by ``CRAWLER_PUSHGATEWAY_URL``.

    Duration uses ``time.monotonic()`` so an NTP correction or VM
    resume mid-run can't produce a negative value. The Pushgateway
    timestamp uses ``time.time()`` because the gauge value is a
    wall-clock instant ("when did this complete").

    Re-raises any exception from the body unchanged so the CLI
    process exit code still reflects failure.
    """
    start_mono = time.monotonic()
    log.info("cron.start", job=job)
    try:
        yield
    except BaseException:
        elapsed = time.monotonic() - start_mono
        log.exception("cron.complete", job=job, status="failure", duration_s=round(elapsed, 2))
        _push_metrics(job, "failure", time.time())
        raise
    else:
        elapsed = time.monotonic() - start_mono
        log.info("cron.complete", job=job, status="success", duration_s=round(elapsed, 2))
        _push_metrics(job, "success", time.time())
