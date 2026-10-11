"""Record actual Python RSS alias resolution using anonymous mocked HEAD responses."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from src.core.monitors import DiscoveredJob, rss


async def main() -> None:
    cases = []
    for name, source, statuses, location in [
        ("relative", "https://example.com/job/Alias/7/", [302], "/job/Engineer/123-de_DE/"),
        (
            "absolute",
            "https://example.com/job/Alias/7/",
            [301],
            "https://example.com/job/Engineer/123-en_US/",
        ),
        ("retry", "https://example.com/job/Alias/7/", [503, 403, 307], "/job/Engineer/123-en_US/"),
        (
            "retry-timeout-early",
            "https://example.com/job/Alias/7/",
            [408, 425, 307],
            "/job/Engineer/123-en_US/",
        ),
        (
            "foreign-source",
            "https://foreign.example/job/Alias/7/",
            [302],
            "/job/Engineer/123-en_US/",
        ),
        (
            "foreign-target",
            "https://example.com/job/Alias/7/",
            [302],
            "https://foreign.example/job/Engineer/123-en_US/",
        ),
        ("nonredirect", "https://example.com/job/Alias/7/", [200], "/job/Engineer/123-en_US/"),
        ("query", "https://example.com/job/Alias/7/", [302], "/job/Engineer/123-en_US/?x=1"),
        ("fragment", "https://example.com/job/Alias/7/", [302], "/job/Engineer/123-en_US/#x"),
        ("zero-id", "https://example.com/job/Alias/7/", [302], "/job/Engineer/0-en_US/"),
        (
            "long-id",
            "https://example.com/job/Alias/7/",
            [302],
            "/job/Engineer/1234567890123-en_US/",
        ),
        ("wrong-locale", "https://example.com/job/Alias/7/", [302], "/job/Engineer/123-en-us/"),
        ("missing-location", "https://example.com/job/Alias/7/", [308], ""),
        ("exhausted", "https://example.com/job/Alias/7/", [429, 429, 429], ""),
    ]:
        calls = []

        def respond(
            request: httpx.Request, *, requests=calls, codes=statuses, target=location
        ) -> httpx.Response:
            requests.append({"method": request.method, "url": str(request.url)})
            return httpx.Response(
                codes[min(len(requests) - 1, len(codes) - 1)],
                headers={"Location": target} if target else {},
            )

        async def no_sleep(delay: float) -> None:
            pass

        original_sleep = rss._sleep
        rss._sleep = no_sleep
        job = DiscoveredJob(url=source, metadata={"id": "feed-7", "company": "Main"})
        error = False
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                await rss._resolve_sf_job_invite_identities(
                    [job], client, feed_url="https://example.com/googlefeed.xml"
                )
        except rss.SuccessFactorsJobIdentityError:
            error = True
        finally:
            rss._sleep = original_sleep
        cases.append(
            {
                "name": name,
                "source": source,
                "statuses": statuses,
                "location": location,
                "calls": calls,
                "error": error,
                "expected_url": job.url,
                "expected_metadata": job.metadata,
            }
        )
    Path(__file__).with_name("python_sf_job_identity.json").write_text(
        json.dumps(
            {
                "reference": (
                    "actual original RSS _resolve_sf_job_invite_identities; "
                    "mocked HTTP boundary only"
                ),
                "cases": cases,
            },
            indent=2,
        )
        + "\n"
    )


asyncio.run(main())
