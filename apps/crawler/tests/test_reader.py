from __future__ import annotations

import httpx
import pytest

from src.core.scrapers.reader import parse_payload, scrape

CONFIG = {
    "title_suffix": " - Citadel Securities",
    "location_after_title": True,
    "require_location": True,
    "description_start": "Job Description",
    "description_stop": "About Citadel Securities",
}


def test_parse_payload_extracts_configured_fields() -> None:
    payload = {
        "data": {
            "title": "Quantitative Research Engineer - Citadel Securities",
            "text": """Skip to Content
Careers
Quantitative Research Engineer

Miami

Job Description
Build and optimize production trading systems.
Work closely with quantitative researchers.
About Citadel Securities
Company boilerplate.
""",
        }
    }

    content = parse_payload(payload, CONFIG)

    assert content.title == "Quantitative Research Engineer"
    assert content.locations == ["Miami"]
    assert content.description == (
        "<p>Build and optimize production trading systems.</p>\n"
        "<p>Work closely with quantitative researchers.</p>"
    )


def test_parse_payload_does_not_guess_location_when_description_follows_title() -> None:
    payload = {
        "data": {
            "title": "Remote Role - Example",
            "text": "Remote Role\nJob Description\nDo useful work.\nAbout Example",
        }
    }
    config = {
        "title_suffix": " - Example",
        "location_after_title": True,
        "description_start": "Job Description",
        "description_stop": "About Example",
    }

    content = parse_payload(payload, config)

    assert content.locations is None
    assert content.description == "<p>Do useful work.</p>"


def test_parse_payload_matches_reader_dash_variants() -> None:
    payload = {
        "data": {
            "title": "Machine Learning Researcher - Europe - Citadel Securities",
            "text": (
                "Machine Learning Researcher – Europe\nLondon, Zurich\n"
                "Job Description\nBuild models.\nAbout Citadel Securities"
            ),
        }
    }

    content = parse_payload(payload, CONFIG)

    assert content.locations == ["London, Zurich"]


@pytest.mark.asyncio
async def test_scrape_uses_reader_text_json(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["accept"] = request.headers["accept"]
        seen["respond_with"] = request.headers["x-respond-with"]
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "title": "Trader - Citadel Securities",
                    "text": (
                        "Trader\nNew York\nJob Description\nTrade markets.\n"
                        "About Citadel Securities"
                    ),
                }
            },
        )

    guarded: list[str] = []
    monkeypatch.setattr(
        "src.core.scrapers.reader.validate_request_url",
        lambda url: guarded.append(url),
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        content = await scrape(
            "https://www.citadelsecurities.com/careers/details/trader/",
            CONFIG,
            client,
        )

    assert guarded == ["https://www.citadelsecurities.com/careers/details/trader/"]
    assert seen == {
        "url": (
            "https://r.jina.ai/https://www.citadelsecurities.com/"
            "careers/details/trader/"
        ),
        "accept": "application/json",
        "respond_with": "text",
    }
    assert content.title == "Trader"
    assert content.locations == ["New York"]
    assert content.description == "<p>Trade markets.</p>"
