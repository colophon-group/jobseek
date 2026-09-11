"""Browser-backend-neutral navigation failure types."""

from __future__ import annotations


class BrowserNavigationHTTPStatusError(RuntimeError):
    """A browser navigation completed with an HTTP error document."""

    def __init__(
        self,
        *,
        requested_url: str,
        response_url: str,
        status: int,
        phase: str,
    ) -> None:
        self.requested_url = requested_url
        self.response_url = response_url
        self.status = status
        self.phase = phase
        super().__init__(
            f"Browser navigation returned HTTP {status} during {phase} navigation "
            f"(requested_url={requested_url!r}, response_url={response_url!r})"
        )


__all__ = ["BrowserNavigationHTTPStatusError"]
