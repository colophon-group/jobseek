from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Protocol, TypeVar

import httpx
import structlog

from src.core.monitors import fetch_page_text, slugs_from_url

log = structlog.get_logger()

ProbeCount = int | str
ProbeResult = tuple[bool, ProbeCount | None]
Context = TypeVar("Context")


class CountFetcher(Protocol[Context]):
    def __call__(
        self,
        token: str,
        client: httpx.AsyncClient,
        context: Context,
    ) -> Awaitable[ProbeCount | None]: ...


class ApiProbe(Protocol[Context]):
    def __call__(
        self,
        token: str,
        client: httpx.AsyncClient,
        context: Context,
    ) -> Awaitable[ProbeResult]: ...


def token_result(token: str, count: ProbeCount | None, context: object = None) -> dict:
    _ = context
    result: dict = {"token": token}
    if count is not None:
        result["jobs"] = count
    return result


async def ats_can_handle[Context](
    url: str,
    client: httpx.AsyncClient | None,
    *,
    monitor_name: str,
    token_from_url: Callable[[str], str | None],
    page_patterns: Sequence[re.Pattern[str]],
    ignore_tokens: frozenset[str],
    fetch_job_count: CountFetcher[Context],
    api_probe: ApiProbe[Context],
    initial_context: Context,
    result_builder: Callable[[str, ProbeCount | None, Context], dict] = token_result,
    context_from_match: Callable[[re.Match[str], Context], Context] | None = None,
    page_token_probe: ApiProbe[Context] | None = None,
    extra_probe_tokens: Callable[[str, str, Context], Iterable[str]] | None = None,
    extra_probe_log_event: str | None = None,
    allow_slug_guess: bool = True,
    log_token_field: str = "board_token",
) -> dict | None:
    """Shared can_handle flow for public ATS API monitors.

    The helper owns the common detection order:

    1. extract a direct token from the submitted URL
    2. scan the page HTML for known ATS URLs or embedded config
    3. optionally probe candidate slugs derived from the page host

    Monitor-specific API URLs, JSON shape, regional context, and result
    metadata stay in callbacks so this helper does not become another ATS
    implementation.
    """
    token = token_from_url(url)
    if token:
        if client is not None:
            count = await fetch_job_count(token, client, initial_context)
            if count is not None:
                return result_builder(token, count, initial_context)
        return result_builder(token, None, initial_context)

    if client is None:
        return None

    html = await fetch_page_text(url, client)
    if html:
        for pattern in page_patterns:
            match = pattern.search(html)
            if not match:
                continue
            found = match.group(1)
            if found in ignore_tokens:
                continue
            context = (
                context_from_match(match, initial_context)
                if context_from_match is not None
                else initial_context
            )
            log.info(f"{monitor_name}.detected_in_page", url=url, **{log_token_field: found})
            if page_token_probe is not None:
                valid, count = await page_token_probe(found, client, context)
                if not valid:
                    continue
            else:
                count = await fetch_job_count(found, client, context)
            return result_builder(found, count, context)

        if extra_probe_tokens is not None:
            for candidate in extra_probe_tokens(url, html, initial_context):
                if candidate in ignore_tokens:
                    continue
                found, count = await api_probe(candidate, client, initial_context)
                if found:
                    log.info(
                        extra_probe_log_event or f"{monitor_name}.detected_by_extra_probe",
                        url=url,
                        **{log_token_field: candidate},
                    )
                    return result_builder(candidate, count, initial_context)

    if allow_slug_guess:
        for slug in slugs_from_url(url):
            found, count = await api_probe(slug, client, initial_context)
            if found:
                log.info(f"{monitor_name}.detected_by_probe", url=url, **{log_token_field: slug})
                return result_builder(slug, count, initial_context)

    return None
