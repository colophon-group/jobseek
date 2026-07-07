from __future__ import annotations

import importlib
import inspect
import re

import httpx

from src.core.monitors._ats_template import ProbeCount, ProbeResult, ats_can_handle


async def test_ats_can_handle_uses_direct_token_before_fetching_page():
    async def fetch_count(
        token: str,
        client: httpx.AsyncClient,
        context: str,
    ) -> ProbeCount | None:
        _ = client
        assert token == "acme"
        assert context == "direct"
        return 3

    async def probe_slug(
        token: str,
        client: httpx.AsyncClient,
        context: str,
    ) -> ProbeResult:
        _ = (token, client, context)
        raise AssertionError("direct token detection should not probe slug guesses")

    def result_builder(token: str, count: ProbeCount | None, context: str) -> dict:
        return {"token": token, "jobs": count, "context": context}

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        raise AssertionError("direct token detection should not fetch the page")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ats_can_handle(
            "https://careers.example.com/jobs",
            client,
            monitor_name="example",
            token_from_url=lambda url: "acme",
            page_patterns=[re.compile(r"ats\.example/([\w-]+)")],
            ignore_tokens=frozenset(),
            fetch_job_count=fetch_count,
            api_probe=probe_slug,
            initial_context="direct",
            result_builder=result_builder,
        )

    assert result == {"token": "acme", "jobs": 3, "context": "direct"}


async def test_ats_can_handle_passes_match_context_to_page_detection():
    async def fetch_count(
        token: str,
        client: httpx.AsyncClient,
        context: str | None,
    ) -> ProbeCount | None:
        _ = client
        assert token == "myco"
        assert context == "eu"
        return 7

    async def probe_slug(
        token: str,
        client: httpx.AsyncClient,
        context: str | None,
    ) -> ProbeResult:
        _ = (token, client, context)
        raise AssertionError("HTML detection should not probe slug guesses")

    def context_from_match(match: re.Match[str], context: str | None) -> str | None:
        _ = context
        return "eu" if ".eu." in match.group(0) else None

    def result_builder(token: str, count: ProbeCount | None, context: str | None) -> dict:
        result = {"token": token, "jobs": count}
        if context:
            result["region"] = context
        return result

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://www.example.com/careers"
        return httpx.Response(
            200,
            text='<script src="https://api.eu.ats.example/boards/myco"></script>',
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ats_can_handle(
            "https://www.example.com/careers",
            client,
            monitor_name="example",
            token_from_url=lambda url: None,
            page_patterns=[re.compile(r"api(?:\.eu)?\.ats\.example/boards/([\w-]+)")],
            ignore_tokens=frozenset(),
            fetch_job_count=fetch_count,
            api_probe=probe_slug,
            initial_context=None,
            result_builder=result_builder,
            context_from_match=context_from_match,
        )

    assert result == {"token": "myco", "jobs": 7, "region": "eu"}


async def test_ats_can_handle_can_require_page_token_probe_success():
    async def fetch_count(
        token: str,
        client: httpx.AsyncClient,
        context: None,
    ) -> ProbeCount | None:
        _ = (token, client, context)
        raise AssertionError("page_token_probe should own page-token validation")

    async def probe_token(
        token: str,
        client: httpx.AsyncClient,
        context: None,
    ) -> ProbeResult:
        _ = (client, context)
        if token == "invalid":
            return False, None
        return True, 9

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(200, text="https://ats.example/invalid https://ats.example/real")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ats_can_handle(
            "https://www.example.com/careers",
            client,
            monitor_name="example",
            token_from_url=lambda url: None,
            page_patterns=[
                re.compile(r"ats\.example/(invalid)"),
                re.compile(r"ats\.example/(real)"),
            ],
            ignore_tokens=frozenset(),
            fetch_job_count=fetch_count,
            api_probe=probe_token,
            initial_context=None,
            page_token_probe=probe_token,
        )

    assert result == {"token": "real", "jobs": 9}


async def test_ats_can_handle_can_disable_slug_guessing():
    async def fetch_count(
        token: str,
        client: httpx.AsyncClient,
        context: None,
    ) -> ProbeCount | None:
        _ = (token, client, context)
        raise AssertionError("no page token should be found")

    async def probe_slug(
        token: str,
        client: httpx.AsyncClient,
        context: None,
    ) -> ProbeResult:
        _ = (token, client, context)
        raise AssertionError("slug guessing should be disabled")

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(200, text="<html>no ats references</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ats_can_handle(
            "https://www.example.com/careers",
            client,
            monitor_name="example",
            token_from_url=lambda url: None,
            page_patterns=[re.compile(r"ats\.example/([\w-]+)")],
            ignore_tokens=frozenset(),
            fetch_job_count=fetch_count,
            api_probe=probe_slug,
            initial_context=None,
            allow_slug_guess=False,
        )

    assert result is None


def test_migrated_monitors_delegate_can_handle_flow_to_ats_template():
    migrated = (
        "ashby",
        "dvinci",
        "greenhouse",
        "hireology",
        "lever",
        "pinpoint",
        "rippling",
        "softgarden",
        "traffit",
        "workable",
    )

    for monitor_name in migrated:
        module = importlib.import_module(f"src.core.monitors.{monitor_name}")
        source = inspect.getsource(module.can_handle)
        assert "ats_can_handle" in source
        assert "fetch_page_text(" not in source
        assert "slugs_from_url(" not in source
        assert "for pattern in _PAGE_PATTERNS" not in source
