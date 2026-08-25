from __future__ import annotations

import httpx

from src.core.scrapers.phuketall import can_handle, parse_html, scrape

HTML = """
<html><head><meta property="og:url" content="https://www.phuketall.com/jobs/1" /></head>
<body>
  <div class="title-feedbox"><h2>ตำแหน่ง : <span>Medical Nurse</span></h2></div>
  <div class="jobs-derails-content">
    <div class="col-md-12"><strong>รายละเอียด</strong></div>
    <div class="col-md-12">Care for guests.<br>Maintain clinical records.</div>
    <div class="detailsbox">
      <div><strong>แผนก:</strong></div><div>MEDICAL</div>
      <div><strong>จำนวน:</strong></div><div>1 อัตรา</div>
      <div><strong>เวลาทำงาน:</strong></div><div>งานประจำ</div>
      <div><strong>ลงประกาศเมื่อ:</strong></div><div>21 ส.ค. 69</div>
    </div>
    <p>46/6 หมู่ 3 อำเภอถลาง ภูเก็ต 83110</p>
  </div>
</body></html>
"""


def test_can_handle_and_parse_thai_detail_page():
    assert can_handle([HTML]) == {}
    content = parse_html(HTML)

    assert content.title == "Medical Nurse"
    assert content.description == "<p>Care for guests.<br>Maintain clinical records.</p>"
    assert content.locations == ["46/6 หมู่ 3 อำเภอถลาง ภูเก็ต 83110"]
    assert content.employment_type == "full_time"
    assert content.date_posted == "2026-08-21"
    assert content.metadata == {"department": "MEDICAL", "quantity": "1 อัตรา"}


async def test_scrape_fetches_detail_page():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=HTML, request=request))
    async with httpx.AsyncClient(transport=transport) as client:
        content = await scrape("https://www.phuketall.com/jobs/1", {}, client)

    assert content.title == "Medical Nurse"
    assert content.description
