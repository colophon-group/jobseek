from __future__ import annotations

import httpx
import pytest

from src.core.scrapers.tupu360 import can_handle, parse_html, scrape

POSITION_ID = "6a9fd89d7280da28da0796ae"
URL = (
    "https://careersite.tupu360.com/cummins/position/detail"
    f"?positionId={POSITION_ID}&recruitmentType=SOCIALRECRUITMENT&currentLang=zh_CN"
)
HTML = f"""
<html><head>
  <title>职位详情</title>
  <script src="//cdn.careersite.tupu360.com/csresources/site.js"></script>
</head><body>
  <div class="container position-detail-container">
    <input type="hidden" id="positionName" value="供应商质量工程师">
    <input type="hidden" id="sourcePid" value="1947435">
    <input type="hidden" id="recruitmentType" value="SOCIALRECRUITMENT">
    <div id="positionTitleNode"><h5><span class="txt">供应商质量工程师</span></h5></div>
    <div class="position-description mt-4 mb-4">
      <p>负责供应商质量改进。</p><ul><li>推动纠正措施并验证效果。</li></ul>
    </div>
    <dl class="position-extend">
      <dt>发布时间：</dt><dd>2026-09-08</dd>
    </dl>
    <dl class="position-extend">
      <dt>工作地点：</dt><dd>重庆-渝北区</dd>
    </dl>
    <input id="positionInfoInp" data-id="{POSITION_ID}">
  </div>
</body></html>
"""


def test_can_handle_and_parse_complete_detail():
    assert can_handle([HTML]) == {}

    content = parse_html(HTML)

    assert content.title == "供应商质量工程师"
    assert content.description is not None
    assert "<p>负责供应商质量改进。</p>" in content.description
    assert "<ul><li>推动纠正措施并验证效果。</li></ul>" in content.description
    assert content.locations == ["重庆-渝北区"]
    assert content.date_posted == "2026-09-08"
    assert content.language == "zh"
    assert content.metadata == {
        "position_id": POSITION_ID,
        "source_id": "1947435",
        "recruitment_type": "SOCIALRECRUITMENT",
    }


def test_can_handle_rejects_lookalike_without_provider_markers():
    lookalike = HTML.replace("cdn.careersite.tupu360.com", "cdn.example.com")
    assert can_handle([lookalike]) is None


async def test_scrape_fetches_and_validates_detail_identity():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=HTML, request=request))
    async with httpx.AsyncClient(transport=transport) as client:
        content = await scrape(URL, {}, client)

    assert content.title == "供应商质量工程师"
    assert content.locations == ["重庆-渝北区"]


@pytest.mark.parametrize(
    "url",
    [
        f"http://careersite.tupu360.com/cummins/position/detail?positionId={POSITION_ID}",
        f"https://careersite.tupu360.com.evil.test/cummins/position/detail?positionId={POSITION_ID}",
        "https://careersite.tupu360.com/cummins/position/detail?positionId=not-an-id",
    ],
)
async def test_scrape_rejects_untrusted_detail_urls(url: str):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: None)) as client:
        with pytest.raises(ValueError, match="trusted public detail URL"):
            await scrape(url, {}, client)


async def test_scrape_rejects_mismatched_response_identity():
    mismatched = HTML.replace(POSITION_ID, "aaaaaaaaaaaaaaaaaaaaaaaa")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=mismatched, request=request)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="identity does not match"):
            await scrape(URL, {}, client)
