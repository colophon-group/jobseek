"""VeryEast (最佳东方) employer-board detail scraper."""

from __future__ import annotations

import re
from html import escape

import httpx
from selectolax.lexbor import LexborHTMLParser

from src.core.enum_normalize import normalize_employment_type
from src.core.scrapers import JobContent, register


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _fields(tree: LexborHTMLParser) -> dict[str, str]:
    fields: dict[str, str] = {}
    for node in tree.css("#textword > ul > li"):
        text = _clean(node.text(separator=" ", strip=True))
        parts = re.split(r"[:：]", text, maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            fields[re.sub(r"\s+", "", parts[0])] = parts[1].strip()
    return fields


def parse_html(html: str, config: dict | None = None) -> JobContent:
    """Parse one server-rendered VeryEast posting."""
    _ = config
    tree = LexborHTMLParser(html)

    title_node = tree.css_first("#textword > ul > h3")
    title = _clean(title_node.text(strip=True)) if title_node is not None else None
    title = re.sub(r"^职位\s*[:：]\s*", "", title or "") or None

    fields = _fields(tree)
    location = fields.get("工作地区")
    employment_type = normalize_employment_type(fields.get("职位性质"))

    description_node = tree.css_first("#textword .describe > p")
    description_inner = description_node.inner_html if description_node is not None else None
    fact_keys = ("职位性质", "工作地区", "招聘人数", "学历", "工作经验", "薪资待遇")
    fact_items = "".join(
        f"<li><strong>{escape(key)}:</strong> {escape(fields[key])}</li>"
        for key in fact_keys
        if fields.get(key)
    )
    fragments = [f"<h3>岗位信息</h3><ul>{fact_items}</ul>"] if fact_items else []
    if description_inner:
        fragments.append(f"<h3>岗位职责/职位描述</h3><p>{description_inner.strip()}</p>")
    description = "\n".join(fragments) or None

    date_posted = None
    extras: dict[str, str] = {}
    valid_range = fields.get("职位有效期", "")
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", valid_range)
    if dates:
        date_posted = dates[0]
    if len(dates) > 1:
        extras["valid_through"] = dates[1]

    metadata = {
        key: value
        for key, value in (
            ("quantity", fields.get("招聘人数")),
            ("degree", fields.get("学历")),
            ("experience", fields.get("工作经验")),
        )
        if value
    }

    return JobContent(
        title=title,
        description=description,
        locations=[location] if location else None,
        employment_type=employment_type,
        date_posted=date_posted,
        language="zh",
        extras=extras or None,
        metadata=metadata or None,
    )


def can_handle(htmls: list[str]) -> dict | None:
    """Detect VeryEast detail pages without matching generic portal pages."""
    if htmls and all(
        "job.veryeast.cn" in html and 'id="textword"' in html and 'class="describe"' in html
        for html in htmls
    ):
        return {}
    return None


async def scrape(url: str, config: dict, http: httpx.AsyncClient, **kwargs) -> JobContent:
    """Fetch and parse one VeryEast detail page."""
    _ = kwargs
    response = await http.get(url, follow_redirects=True)
    response.raise_for_status()
    return parse_html(response.text, config)


register("veryeast", scrape, can_handle=can_handle, parse_html=parse_html)
