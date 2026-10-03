"""Freeze ordinary Greenhouse HTTP outcomes from the actual Python monitor."""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import json
from http.cookiejar import CookieJar
from pathlib import Path

import httpx

from src.core.monitor import MonitorResult
from src.core.monitors import BoardGoneError
from src.core.monitors.greenhouse import discover
from src.shared.http import _CLIENT_DEFAULTS, _Rfc6265CookiePolicy
from src.shared.tdm import TDMReservedError

TOKEN = "fixture"
ENDPOINT = f"https://boards-api.greenhouse.io/v1/boards/{TOKEN}/jobs?content=true"
PAYLOAD = json.dumps(
    {
        "jobs": [
            {
                "absolute_url": "https://job-boards.greenhouse.io/fixture/jobs/1",
                "title": "Senior Café Engineer 東京",
                "content": "<p>Role</p><a>Learn more</a>",
                "location": {"name": " Zürich "},
                "first_published": "2026-09-30T01:00:00Z",
                "language": "en",
                "requisition_id": "1",
            }
        ]
    },
    ensure_ascii=False,
)


def response(status=200, body=None, headers=()):
    return {
        "status": status,
        "body": PAYLOAD.encode() if body is None else body,
        "headers": list(headers),
    }


CASES = [(f"success-{status}", [response(status)]) for status in [200, 201, 202, 203, 206, 299]]
CASES.extend(
    (f"status-{status}", [response(status, b"not JSON")])
    for status in [204, 205, 300, 301, 304, 400, 404, 410, 429, 500]
)
CASES.extend(
    [
        ("reservation-before-404", [response(404, b"bad", [(b"tdm-reservation", b"1")])]),
        ("reservation-before-invalid-json", [response(200, b"bad", [(b"tdm-reservation", b"1")])]),
        (
            "reservation-policy",
            [
                response(
                    headers=[
                        (b"tdm-reservation", b" 1 "),
                        (b"tdm-policy", b"https://policy.invalid/license"),
                    ]
                )
            ],
        ),
        (
            "reservation-unicode-whitespace",
            [response(headers=[(b"tdm-reservation", "\u00a01\u00a0".encode())])],
        ),
        ("reservation-latin1-whitespace", [response(headers=[(b"tdm-reservation", b"\xa01\xa0")])]),
        (
            "reservation-python-strip-controls",
            [response(headers=[(b"tdm-reservation", b"\x1c1\x1f")])],
        ),
        (
            "duplicate-reservations-unset",
            [response(headers=[(b"tdm-reservation", b"1"), (b"tdm-reservation", b"1")])],
        ),
        (
            "duplicate-policy-joined",
            [
                response(
                    headers=[
                        (b"tdm-reservation", b"1"),
                        (b"tdm-policy", b"first"),
                        (b"tdm-policy", b"second"),
                    ]
                )
            ],
        ),
        (
            "empty-policy-none",
            [response(headers=[(b"tdm-reservation", b"1"), (b"tdm-policy", b"")])],
        ),
        (
            "whitespace-policy-retained",
            [response(headers=[(b"tdm-reservation", b"1"), (b"tdm-policy", b" ")])],
        ),
        (
            "latin1-policy",
            [response(headers=[(b"tdm-reservation", b"1"), (b"tdm-policy", b"caf\xe9")])],
        ),
        (
            "unrelated-latin1-header-changes-policy-decoding",
            [
                response(
                    headers=[
                        (b"tdm-reservation", b"1"),
                        (b"tdm-policy", "café".encode()),
                        (b"x-other", b"\xff"),
                    ]
                )
            ],
        ),
        ("nonliteral-reservation-unset", [response(headers=[(b"tdm-reservation", b"true")])]),
        (
            "html-meta-does-not-override-api-header",
            [
                response(
                    body=b'<meta name="tdm-reservation" content="0">',
                    headers=[(b"tdm-reservation", b"1")],
                )
            ],
        ),
        (
            "partial-job-parse-rejects-all",
            [response(body=b'{"jobs":[{"absolute_url":"https://example.com/1"},{}]}')],
        ),
        ("invalid-json", [response(body=b"<html>bad</html>")]),
        ("invalid-utf8", [response(body=b'{"jobs":[],"x":"\xff"}')]),
        ("wrong-root", [response(body=b"[]")]),
        ("missing-jobs", [response(body=b"{}")]),
    ]
)
for encoding in [
    "utf-8-sig",
    "utf-16",
    "utf-16-le",
    "utf-16-be",
    "utf-32",
    "utf-32-le",
    "utf-32-be",
]:
    CASES.append(
        (
            f"json-{encoding}",
            [
                response(
                    body=PAYLOAD.encode(encoding),
                    headers=[(b"content-type", b"application/json; charset=iso-8859-1")],
                )
            ],
        )
    )
CASES.extend(
    [
        (
            "invalid-utf16-surrogate",
            [response(body=b'\xff\xfe{\x00"\x00x\x00"\x00:\x00"\x00\x00\xd8"\x00}\x00')],
        ),
        ("invalid-utf32-scalar", [response(body=b"\xff\xfe\x00\x00\x00\x00\x11\x00")]),
        ("empty-body", [response(body=b"")]),
        ("redirect-success", [response(302, b"", [(b"location", b"/final")]), response()]),
        (
            "redirect-provider-gone",
            [response(307, b"", [(b"location", b"https://redirect.invalid/final")]), response(404)],
        ),
        (
            "redirect-publisher-reserved",
            [
                response(308, b"", [(b"location", b"/reserved")]),
                response(500, headers=[(b"tdm-reservation", b"1")]),
            ],
        ),
        (
            "redirect-header-only-ignored",
            [
                response(301, b"", [(b"location", b"/final"), (b"tdm-reservation", b"1")]),
                response(),
            ],
        ),
        (
            "twenty-redirects",
            [response(302, b"", [(b"location", f"/hop-{i}".encode())]) for i in range(20)]
            + [response()],
        ),
        (
            "twenty-one-redirects-rejected",
            [response(302, b"", [(b"location", f"/hop-{i}".encode())]) for i in range(21)]
            + [response()],
        ),
        ("transport-failure", []),
    ]
)


async def capture(name, responses):
    requests = []

    def handle(request):
        requests.append(str(request.url))
        if not responses:
            raise httpx.ConnectError("private diagnostic must not cross boundary", request=request)
        row = responses[len(requests) - 1]
        return httpx.Response(row["status"], headers=row["headers"], content=row["body"])

    row = {
        "name": name,
        "responses": [
            {
                "status": r["status"],
                "body_base64": base64.b64encode(r["body"]).decode(),
                "headers": [
                    {"name": k.decode("ascii"), "value_base64": base64.b64encode(v).decode()}
                    for k, v in r["headers"]
                ],
            }
            for r in responses
        ],
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle),
        headers=_CLIENT_DEFAULTS["headers"],
        timeout=_CLIENT_DEFAULTS["timeout"],
        follow_redirects=_CLIENT_DEFAULTS["follow_redirects"],
        cookies=CookieJar(policy=_Rfc6265CookiePolicy()),
    ) as client:
        try:
            result = await discover(
                {
                    "board_url": "https://job-boards.greenhouse.io/fixture",
                    "metadata": {"token": TOKEN},
                },
                client,
            )
        except TDMReservedError as exc:
            row.update(kind="publisher_reserved", final_url=exc.url, policy_url=exc.policy_url)
        except BoardGoneError as exc:
            row.update(kind="provider_gone", final_url=exc.url)
        except httpx.HTTPStatusError as exc:
            row.update(kind="http_status", final_url=str(exc.response.url))
        except (httpx.TransportError, httpx.TooManyRedirects):
            row.update(kind="request_failed", final_url="")
        except (ValueError, TypeError, AttributeError):
            row.update(kind="invalid_inventory", final_url=requests[-1])
        else:
            jobs = result.jobs if isinstance(result, MonitorResult) else result
            row.update(
                kind="",
                final_url=requests[-1],
                jobs=[dataclasses.asdict(job) for job in jobs],
                truncated=isinstance(result, MonitorResult) and result.truncated,
            )
    row["requests"] = requests
    return row


async def main():
    captured = [await capture(name, responses) for name, responses in CASES]
    Path(__file__).with_name("python_discovery.json").write_text(
        json.dumps(captured, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
