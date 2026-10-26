"""Freeze the original strict Tupu360 parser's output and rejection behavior."""

from __future__ import annotations

import json
from pathlib import Path

from src.core.monitors.tupu360_listing import parse_page

BOARD = "https://fixture.tupu360.com/fixture/position/index"
CARD = (
    '<div class="position-item" pid="000000000000000000000001" '
    'recruitment="SOCIALRECRUITMENT"><div class="position-name"><span class="title">'
    '<span class="txt"> 工程师 &amp; 开发 </span></span></div><div class="e-city">'
    '<span class="txt">工作城市： 上海 </span></div><span class="time">发布于：2026-10-01</span>'
    '<div class="e-salary"><span class="txt">薪资待遇：10000-20000</span></div></div>'
)


def main() -> None:
    page = '<div id="position-list" positioncount="1">' + CARD + "</div>"
    cases = [
        ("rich", page),
        ("nested-text", page.replace("工程师 &amp; 开发", "<b>工程师</b> <i>开发</i>")),
        ("no-salary", page.replace("薪资待遇：10000-20000", "薪资待遇：面议")),
        ("unicode-space", page.replace("工作城市： 上海", "工作城市：\u00a0上海\u00a0")),
        ("zero", '<div id="position-list" positioncount="0"></div>'),
        ("missing-total", page.replace('positioncount="1"', "")),
        ("oversize-total", page.replace('positioncount="1"', 'positioncount="5001"')),
        ("extra-total", page + '<div id="position-list" positioncount="1"></div>'),
        ("invalid-id", page.replace("000000000000000000000001", "ABC")),
        ("invalid-recruitment", page.replace("SOCIALRECRUITMENT", "social")),
        (
            "project",
            page.replace(
                'recruitment="SOCIALRECRUITMENT"',
                'recruitment="SOCIALRECRUITMENT" projectid="project"',
            ),
        ),
        ("missing-title", page.replace('class="position-name"', 'class="other"')),
        ("missing-location", page.replace("工作城市：", "城市：")),
        ("invalid-date", page.replace("2026-10-01", "2026/10/01")),
        ("reversed-salary", page.replace("10000-20000", "20000-10000")),
        ("more-than-total", page.replace('positioncount="1"', 'positioncount="0"')),
    ]
    result = []
    for name, html in cases:
        case = {"name": name, "html": html, "board": BOARD}
        try:
            total, jobs = parse_page(html, BOARD)
        except ValueError:
            case["rejected"] = True
        else:
            case["total"] = total
            case["jobs"] = [
                {
                    key: getattr(job, key)
                    for key in (
                        "url",
                        "title",
                        "locations",
                        "date_posted",
                        "base_salary",
                        "language",
                        "metadata",
                    )
                }
                for job in jobs
            ]
        result.append(case)
    Path(__file__).with_name("python_tupu360_listing.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
