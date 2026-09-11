"""Closed B0 scrape runtime for one held Lightpanda service reservation."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlsplit

from src.core.job_content import JobContent
from src.core.jsonld import parse_rendered_html
from src.lightpanda.client import LightpandaB0Reservation
from src.lightpanda.routing import resolve_render_assignment
from src.shared.navigation_errors import BrowserNavigationHTTPStatusError

if TYPE_CHECKING:
    import httpx

    from src.lightpanda_queue import LightpandaB0Task

HTML_LIMIT_BYTES: Final = 1024 * 1024
HTML_CHUNK_LIMIT_BYTES: Final = 64 * 1024
_FINAL_URL_LIMIT_BYTES: Final = 8192
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")


class LightpandaResultError(RuntimeError):
    """A fail-closed B0 result or inline HTML manifest rejection."""


class LightpandaB0ScrapeRuntime:
    """Use one reservation for one frozen JSON-LD render assignment."""

    implementation = "go"

    def __init__(
        self,
        task: LightpandaB0Task,
        reservation: LightpandaB0Reservation,
    ) -> None:
        self._task = task
        self._reservation = reservation
        self._used = False

    async def scrape(
        self,
        url: str,
        scraper_type: str,
        scraper_config: dict | None,
        http: httpx.AsyncClient,
        *,
        pw: object | None = None,
        artifact_dir: Path | None = None,
    ) -> JobContent:
        del http
        if self._used:
            raise LightpandaResultError("Lightpanda B0 scrape runtime is one-shot")
        self._used = True
        expected_config = _thaw_json(self._task.assignment.config)
        if (
            not isinstance(scraper_config, dict)
            or url != self._task.source_url
            or scraper_type != "json-ld"
            or scraper_type != self._task.assignment.scraper_type
            or scraper_config != expected_config
            or pw is not None
            or artifact_dir is not None
        ):
            raise LightpandaResultError("scrape invocation differs from the frozen B0 assignment")

        try:
            resolved = resolve_render_assignment(scraper_type, scraper_config, scraper_step=0)
        except ValueError as exc:
            raise LightpandaResultError("scrape invocation has an invalid B0 assignment") from exc
        expected = self._task.assignment
        if (
            resolved is None
            or resolved.browser_backend != expected.browser_backend
            or resolved.routing_revision != expected.routing_revision
            or resolved.scraper_type != expected.scraper_type
            or resolved.scraper_step != expected.scraper_step
            or resolved.timeout_ms != expected.timeout_ms
            or resolved.config != expected.config
            or resolved.config_digest_sha256 != expected.config_digest_sha256
        ):
            raise LightpandaResultError("scrape invocation changed the frozen B0 identity")

        result = await self._reservation.execute(self._task)
        html = _validated_rendered_html(result, requested_url=url)
        return parse_rendered_html(url, scraper_config, html)


def _validated_rendered_html(result: Any, *, requested_url: str) -> str:
    if result.WhichOneof("outcome") != "success":
        raise LightpandaResultError("Lightpanda B0 render did not succeed")
    success = result.success
    if (
        not _valid_final_url(success.final_url)
        or not success.HasField("status")
        or not 100 <= success.status <= 599
        or not success.HasField("html")
        or success.action_outcomes
        or success.captures
        or success.evaluations
        or success.artifacts
    ):
        raise LightpandaResultError("Lightpanda B0 success contains an invalid output shape")
    manifest = success.html
    expected_chunks = (
        manifest.total_size_bytes + HTML_CHUNK_LIMIT_BYTES - 1
    ) // HTML_CHUNK_LIMIT_BYTES
    if (
        not manifest.complete
        or manifest.total_size_bytes > HTML_LIMIT_BYTES
        or len(manifest.chunks) != expected_chunks
        or _SHA256_RE.fullmatch(manifest.total_sha256) is None
    ):
        raise LightpandaResultError("Lightpanda B0 HTML manifest header is invalid")

    parts: list[bytes] = []
    observed_size = 0
    for expected_sequence, chunk in enumerate(manifest.chunks):
        if chunk.sequence != expected_sequence or chunk.WhichOneof("storage") != "inline_body":
            raise LightpandaResultError("Lightpanda B0 HTML chunks are not sequential inline data")
        body = bytes(chunk.inline_body)
        expected_size = min(HTML_CHUNK_LIMIT_BYTES, manifest.total_size_bytes - observed_size)
        if (
            len(body) != expected_size
            or chunk.size_bytes != len(body)
            or _SHA256_RE.fullmatch(chunk.sha256) is None
            or hashlib.sha256(body).hexdigest() != chunk.sha256
            or observed_size > HTML_LIMIT_BYTES - len(body)
        ):
            raise LightpandaResultError("Lightpanda B0 HTML chunk is invalid")
        observed_size += len(body)
        parts.append(body)

    if observed_size != manifest.total_size_bytes:
        raise LightpandaResultError("Lightpanda B0 HTML manifest size is invalid")
    rendered = b"".join(parts)
    if hashlib.sha256(rendered).hexdigest() != manifest.total_sha256:
        raise LightpandaResultError("Lightpanda B0 HTML manifest digest is invalid")
    try:
        html = rendered.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise LightpandaResultError("Lightpanda B0 HTML is not strict UTF-8") from exc
    if 400 <= success.status <= 599:
        raise BrowserNavigationHTTPStatusError(
            requested_url=requested_url,
            response_url=success.final_url,
            status=success.status,
            phase="primary",
        )
    return html


def _valid_final_url(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > _FINAL_URL_LIMIT_BYTES
    ):
        return False
    if any(character in value for character in "\r\n"):
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except (TypeError, UnicodeError, ValueError):
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and parsed.fragment == ""
    )


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_thaw_json(item) for item in value]
    return value


__all__ = [
    "HTML_LIMIT_BYTES",
    "LightpandaB0ScrapeRuntime",
    "LightpandaResultError",
]
