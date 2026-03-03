"""URL validation helpers for advisory checks on company/image URLs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class UrlCheckResult:
    """Result of checking a URL."""

    reachable: bool
    status_code: int | None = None
    final_url: str | None = None
    content_type: str | None = None
    content_length: int | None = None
    error: str | None = None


def check_url(url: str, timeout: float = 10) -> UrlCheckResult:
    """Check if a URL is reachable. Returns status info."""
    try:
        import httpx

        resp = httpx.head(url, follow_redirects=True, timeout=timeout)
        return UrlCheckResult(
            reachable=resp.status_code < 400,
            status_code=resp.status_code,
            final_url=str(resp.url) if str(resp.url) != url else None,
            content_type=resp.headers.get("content-type"),
        )
    except Exception as e:
        return UrlCheckResult(reachable=False, error=str(e))


def check_image(url: str, timeout: float = 10) -> UrlCheckResult:
    """Check if a URL points to a downloadable image."""
    try:
        import httpx

        resp = httpx.get(url, follow_redirects=True, timeout=timeout)
        ct = resp.headers.get("content-type", "")
        return UrlCheckResult(
            reachable="image" in ct or "svg" in ct,
            status_code=resp.status_code,
            content_type=ct,
            content_length=len(resp.content),
        )
    except Exception as e:
        return UrlCheckResult(reachable=False, error=str(e))
