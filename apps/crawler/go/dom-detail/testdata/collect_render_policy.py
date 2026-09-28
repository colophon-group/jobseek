"""Freeze canonical rendered DOM decisions without publisher requests."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from src.core.monitors.dom import BotChallengeError, _raise_if_bot_challenge
from src.core.scrapers.dom import _check_gone_redirect

cases = []


def record(html: str, url: str = "https://example.test/job", pattern: str | None = None):
    config = {} if pattern is None else {"gone_url_pattern": pattern}
    classification = "okay"
    try:
        _check_gone_redirect(url, pattern, url)
        _raise_if_bot_challenge(url, html)
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 410
        classification = "gone"
    except BotChallengeError:
        classification = "challenge"
    cases.append(
        {
            "request": {"mode": "classify-rendered", "html": html, "url": url, "config": config},
            "expected": {"classification": classification},
        }
    )


markers = [
    "/.well-known/captcha",
    "/.well-known/sgcaptcha",
    "<title>Just a moment</title>",
    "/cdn-cgi/challenge-platform/ enable javascript and cookies",
    "/cdn-cgi/challenge-platform/ Sorry, you have been blocked",
    "please wait while your request is being verified",
    'id="main-iframe" /_Incapsula_Resource?CWUDNSAI=',
    "validate.perfdrive.com",
    "<title>Radware CAPTCHA Page",
    "botmanager_support@radware.com",
    "captcha.perfdrive.com/captcha-public/",
    '<title>Validation Request</title> user validation required to continue action="/captcha_resp"',
    "safe.liepin.com/ captchapage_ip_pc",
]
for html in markers:
    record(html)
    record(html.upper())
    record("<h1>Engineer</h1>", "https://example.test/" + html.replace(" ", "_"))
    record(html, "https://example.test/gone", r"/gone$")
    record(html, pattern="[")  # Canonical invalid gone regex continues to challenge detection.
for html in (
    "<h1>Engineer</h1><p>Work.</p>",
    "/_Incapsula_Resource?ordinary=sensor",
    "/_Incapsula_Resource?CWUDNSAI=",
    'id="main-iframe"',
    "<title>Validation Request</title> user validation required to continue",
    'user validation required to continue action="/captcha_resp"',
    '<title>Validation Request</title> action="/captcha_resp"',
    "safe.liepin.com/",
    "captchapage_ip_pc",
    "/cdn-cgi/challenge-platform/ ordinary content",
    "enable javascript and cookies",
    "<title>Just a moment".replace("i", "İ"),
):
    record(html)
for url, pattern in (
    ("https://example.test/job/gone", r"(?<=/job/)gone$"),
    ("https://example.test/job/gone-gone", r"(?P<state>gone)-(?P=state)$"),
    ("https://example.test/job/²", r"/\w+$"),
    ("https://example.test/job/²", r"/\d+$"),
    ("https://example.test/job/gone", r"/gone\Z"),
    ("https://example.test/job/gone", "["),
):
    record("<title>Just a moment</title>", url, pattern)
Path(__file__).with_name("python_render_policy.json").write_text(
    json.dumps(cases, indent=2, ensure_ascii=False) + "\n"
)
print(f"Retained {len(cases)} canonical rendered policy cases")
