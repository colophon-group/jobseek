from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from src.core.monitors import is_rich_monitor
from src.core.monitors.dom import can_handle, dom_discover
from src.core.monitors.tupu360_listing import discover, is_listing_url, parse_page
from src.shared.http_retry import PaginationFetchError
from src.workspace._compat import auto_scraper_type
from src.workspace._compat import is_rich_monitor as workspace_is_rich_monitor

URL = "https://careersite.tupu360.com/andritz/position/index"


def page(total: int, *positions: tuple[int, str]) -> str:
    rows = "".join(
        f'<div class="position-item" pid="{identity:024x}" recruitment="{kind}">'
        '<div class="position-name"><h4 class="title"><span class="txt">工程师</span></h4></div>'
        '<li class="e-city"><span class="txt">工作城市：广东省-佛山</span></li>'
        '<li class="e-salary"><span class="txt">薪资待遇：10000-14999</span></li>'
        '<div class="time">发布于: 2026-10-15</div></div>'
        for identity, kind in positions
    )
    return f'<div id="position-list" positionCount="{total}">{rows}</div>'


async def test_dom_preset_preserves_listing_fields_and_reconciles_every_recruitment_type():
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        offset = request.url.params.get("offset", "0")
        html = (
            page(3, (1, "SOCIALRECRUITMENT"), (2, "BLUECOLLARRECRUITMENT"))
            if offset == "0"
            else page(3, (3, "CAMPUSRECRUITMENT"))
        )
        return httpx.Response(200, text=html)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        detected = await can_handle(URL, client)
        assert detected == {"tupu360_listing": True, "urls": 3}
        jobs = await dom_discover({"board_url": URL, "metadata": detected}, client)

    assert len(jobs) == 3
    assert jobs[0].title == "工程师"
    assert jobs[0].locations == ["广东省-佛山"]
    assert jobs[0].date_posted == "2026-10-15"
    assert jobs[0].base_salary == {"min": 10000, "max": 14999}
    assert jobs[0].description is None
    assert parse_qs(httpx.URL(jobs[1].url).query.decode())["recruitmentType"] == [
        "BLUECOLLARRECRUITMENT"
    ]
    assert requests[-1].url.params["offset"] == "2"
    assert auto_scraper_type("dom", detected) == (
        "tupu360",
        {"enrich": ["description"], "listing_enrichment": True},
    )
    assert is_rich_monitor("dom", detected)
    assert workspace_is_rich_monitor("dom", detected)


@pytest.mark.parametrize(
    "tail,error",
    [
        (page(2), "ended before"),
        (page(2, (1, "SOCIALRECRUITMENT")), "repeated a position"),
        (page(3, (2, "SOCIALRECRUITMENT")), "total changed"),
        ("<html>challenge</html>", "authoritative count"),
    ],
)
async def test_incomplete_or_unstable_pagination_never_returns_success(tail, error):
    def respond(request):
        html = tail if request.url.params["offset"] != "0" else page(2, (1, "SOCIALRECRUITMENT"))
        return httpx.Response(200, text=html)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError, match=error):
            await discover(URL, client)


async def test_required_page_http_failure_is_not_an_empty_inventory():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(404))
    ) as client:
        with pytest.raises(PaginationFetchError):
            await discover(URL, client)


def test_only_explicit_count_authenticates_empty_and_layout_drift_fails():
    assert parse_page(page(0), URL) == (0, [])
    with pytest.raises(ValueError, match="required field"):
        parse_page(
            page(1, (1, "SOCIALRECRUITMENT")).replace('class="e-city"', 'class="moved"'), URL
        )
    with pytest.raises(ValueError, match="count"):
        parse_page(page(5_001), URL)


def test_salary_preserves_only_source_values_and_rejects_reversed_ranges():
    html = page(1, (1, "SOCIALRECRUITMENT"))
    _, jobs = parse_page(html.replace("10000-14999", "不显示职位月薪"), URL)
    assert jobs[0].base_salary is None
    with pytest.raises(ValueError, match="reversed"):
        parse_page(html.replace("10000-14999", "14999-10000"), URL)


@pytest.mark.parametrize(
    "url",
    [
        URL.replace("https:", "http:"),
        URL.replace("tupu360.com", "tupu360.com.evil.test"),
        URL + "?recruitmentType=SOCIALRECRUITMENT",
        URL + "#fragment",
        URL.replace("careersite.", "user@careersite."),
    ],
)
def test_rejects_untrusted_or_filtered_urls(url):
    assert not is_listing_url(url)
