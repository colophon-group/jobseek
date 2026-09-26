"""Opt-in one-run Booking DOM diagnostic from the existing scheduled visit.

The main navigation body and final DOM are different inputs. This capture can
show whether an HTTP route is viable, but it does not prove browser request or
JavaScript parity. It never initiates a publisher request.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import structlog

if TYPE_CHECKING:
    from playwright.async_api import Page, Response

log = structlog.get_logger()

_BOARD_URL = "https://jobs.booking.com/booking/jobs"
_PATH = Path("/tmp/jobseek-booking-dom-replay.json")
_MAX_MAIN_BODY = 2_000_000
_MAX_RENDERED_HTML = 4_000_000
_MAX_LINKS = 1_000
_MAX_LINK_BYTES = 1_000_000
_MAX_API_RESPONSES = 16
_MAX_API_BODY = 2_000_000
_MAX_API_TOTAL = 4_000_000


class BookingDOMCapture:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.main_response = None
        self.api_responses = []
        self.api_overflow = False

    def on_response(self, response: Response) -> None:
        try:
            if response.frame == self.page.main_frame and response.request.is_navigation_request():
                self.main_response = response
            elif (
                response.request.method == "GET"
                and urlsplit(response.url).scheme == "https"
                and urlsplit(response.url).netloc == "jobs.booking.com"
                and urlsplit(response.url).path == "/api/jobs"
            ):
                if len(self.api_responses) < _MAX_API_RESPONSES:
                    self.api_responses.append(response)
                else:
                    self.api_overflow = True
        except (AttributeError, TypeError, ValueError):
            return

    async def finish(self, *, page: Page, html: str, links: list[str], urls: set[str]) -> None:
        if _PATH.exists() or _PATH.with_suffix(".json.partial").exists():
            return
        response = self.main_response
        if response is None or len(links) > _MAX_LINKS or self.api_overflow:
            log.warning("dom.replay_capture_incomplete", reason="missing_response_or_links_cap")
            return
        try:
            content_length = response.headers.get("content-length", "")
            if content_length.isdecimal() and int(content_length) > _MAX_MAIN_BODY:
                log.warning("dom.replay_capture_limit")
                return
            if len(html) > _MAX_RENDERED_HTML or sum(len(link) for link in links) > _MAX_LINK_BYTES:
                log.warning("dom.replay_capture_limit")
                return
            body = await asyncio.wait_for(response.body(), timeout=5)
            html_bytes = html.encode("utf-8")
            if len(body) > _MAX_MAIN_BODY or len(html_bytes) > _MAX_RENDERED_HTML:
                log.warning("dom.replay_capture_limit")
                return
            api_records = []
            api_bytes = 0
            for api_response in self.api_responses:
                api_length = api_response.headers.get("content-length", "")
                if api_length.isdecimal() and int(api_length) > _MAX_API_BODY:
                    log.warning("dom.replay_capture_api_limit")
                    return
                api_body = await asyncio.wait_for(api_response.body(), timeout=5)
                api_bytes += len(api_body)
                if len(api_body) > _MAX_API_BODY or api_bytes > _MAX_API_TOTAL:
                    log.warning("dom.replay_capture_api_limit")
                    return
                api_records.append(
                    {
                        "method": "GET",
                        "url": api_response.url,
                        "status": api_response.status,
                        "content_type": api_response.headers.get("content-type", ""),
                        "tdm_reservation": api_response.headers.get("tdm-reservation", ""),
                        "tdm_policy": api_response.headers.get("tdm-policy", ""),
                        "body_b64": base64.b64encode(api_body).decode("ascii"),
                    }
                )
            record = {
                "schema": "jobseek.dom-replay/v1",
                "board_url": _BOARD_URL,
                "final_url": page.url,
                "main_url": response.url,
                "main_method": response.request.method,
                "main_status": response.status,
                "main_content_type": response.headers.get("content-type", ""),
                "tdm_reservation": response.headers.get("tdm-reservation", ""),
                "tdm_policy": response.headers.get("tdm-policy", ""),
                "main_body_b64": base64.b64encode(body).decode("ascii"),
                "rendered_html": html,
                "resolved_links": sorted(links),
                "matched_urls": sorted(urls),
                "api_responses": api_records,
            }
            path = _PATH.with_suffix(".json.partial")
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    json.dump(record, output, ensure_ascii=False, separators=(",", ":"))
                    output.flush()
                    os.fsync(output.fileno())
                path.replace(_PATH)
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            log.info("dom.replay_capture_complete", urls=len(urls))
        except Exception:
            log.warning("dom.replay_capture_failed", exc_info=True)


def start_booking_capture(board_url: str, page: Page) -> BookingDOMCapture | None:
    if os.environ.get("BOOKING_DOM_REPLAY_CAPTURE") != "1" or board_url != _BOARD_URL:
        return None
    if _PATH.exists() or _PATH.with_suffix(".json.partial").exists():
        return None
    return BookingDOMCapture(page)
