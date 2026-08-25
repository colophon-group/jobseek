from __future__ import annotations

import httpx
import pytest

from src.core.scrapers.veryeast import can_handle, parse_html, scrape
from src.shared.http_retry import ResponseBodyTooLargeError

HTML = """
<html><head><script>var page = {"pcUrl":"https://job.veryeast.cn/8018193/2836817"};</script></head>
<body><div id="textword">
  <ul>
    <h3>职位：Movement Coach 健身教练</h3>
    <li>职位性质：全职</li>
    <li>工作地区：湖州市安吉县</li>
    <li>招聘人数：1人</li>
    <li>学　　历：本科</li>
    <li>工作经验：3年以上</li>
    <li>职位有效期：2026-07-28至2026-10-26</li>
  </ul>
  <div class="describe"><h3>岗位职责/职位描述</h3>
    <p>Coach resort guests.<br>维护运动器材。</p>
    <p>Deliver individual movement plans.</p>
    <ul><li>Document guest progress.</li></ul>
  </div>
</div></body></html>
"""


def test_can_handle_and_parse_detail_page():
    assert can_handle([HTML]) == {}
    content = parse_html(HTML)

    assert content.title == "Movement Coach 健身教练"
    assert content.description is not None
    assert "<strong>工作地区:</strong> 湖州市安吉县" in content.description
    assert "Coach resort guests.<br>维护运动器材。" in content.description
    assert "<p>Deliver individual movement plans.</p>" in content.description
    assert "<ul><li>Document guest progress.</li></ul>" in content.description
    assert content.locations == ["湖州市安吉县"]
    assert content.employment_type == "full_time"
    assert content.date_posted == "2026-07-28"
    assert content.extras == {"valid_through": "2026-10-26"}
    assert content.metadata == {
        "quantity": "1人",
        "degree": "本科",
        "experience": "3年以上",
    }


async def test_scrape_fetches_detail_page():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=HTML, request=request))
    async with httpx.AsyncClient(transport=transport) as client:
        content = await scrape("https://job.veryeast.cn/8018193/2836817", {}, client)

    assert content.title == "Movement Coach 健身教练"
    assert content.description


async def test_scrape_rejects_oversized_detail_instead_of_truncating_sections():
    oversized = HTML + ("x" * (2 * 1024 * 1024))
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=oversized, request=request)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ResponseBodyTooLargeError):
            await scrape("https://job.veryeast.cn/8018193/2599163", {}, client)
