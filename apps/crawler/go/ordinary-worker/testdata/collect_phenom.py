"""Freeze actual Phenom sitemap request, locale and failure semantics offline."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

import httpx

from src.core.monitors.phenom import discover

ORIGIN = "https://example.com"


def xml(kind, values, namespace=False):
    entry = "sitemap" if kind == "sitemapindex" else "url"
    ns = ' xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"' if namespace else ""
    return (
        f"<{kind}{ns}>"
        + "".join(f"<{entry}><loc>{escape(v)}</loc></{entry}>" for v in values)
        + f"</{kind}>"
    )


def response(body, status=200, headers=None):
    return {
        "body": body,
        "status": status,
        "headers": {"Content-Type": "text/xml", **(headers or {})},
    }


flat = xml(
    "urlset",
    [
        ORIGIN + "/job/one?utm_source=test&x=1",
        ORIGIN + "/Job?job_id=two",
        ORIGIN + "/home",
        ORIGIN + "/job/one?x=1",
    ],
    True,
)
en, de, es, content = [
    ORIGIN + name
    for name in (
        "/sitemap-aa-en.xml",
        "/sitemap-bb-de.xml",
        "/sitemap-cc-es-mx.xml",
        "/sitemap-content.xml",
    )
]
index = response(xml("sitemapindex", [en, de, es, content]))
pages = {
    "/sitemap.xml": index,
    "/sitemap-aa-en.xml": response(xml("urlset", [ORIGIN + "/job/en"])),
    "/sitemap-bb-de.xml": response(xml("urlset", [ORIGIN + "/job/de"])),
    "/sitemap-cc-es-mx.xml": response(xml("urlset", [ORIGIN + "/job/es"])),
    "/sitemap-content.xml": response(xml("urlset", [ORIGIN + "/home"])),
}
cases = [
    ("trailing-xml-comment", {"/sitemap.xml": response(flat + "<!-- carrier -->")}, {}),
    ("flat-normalization", {"/sitemap.xml": response(flat)}, {}),
    ("exclude", {"/sitemap.xml": response(flat)}, {"url_exclude": "one"}),
    ("default-locales", pages, {}),
    ("custom-locales", pages, {"keep_languages": ["EN", "ES-MX"]}),
    ("empty-locales-default", pages, {"keep_languages": []}),
    (
        "single-language-shards",
        {
            "/sitemap.xml": response(xml("sitemapindex", [es, ORIGIN + "/sitemap-dd-es-mx.xml"])),
            "/sitemap-cc-es-mx.xml": response(xml("urlset", [ORIGIN + "/job/one"])),
            "/sitemap-dd-es-mx.xml": response(xml("urlset", [ORIGIN + "/job/two"])),
        },
        {},
    ),
    (
        "nested",
        {
            "/sitemap.xml": response(xml("sitemapindex", [ORIGIN + "/nested.xml"])),
            "/nested.xml": index,
            **{k: v for k, v in pages.items() if k != "/sitemap.xml"},
        },
        {},
    ),
    ("root-nonxml", {"/sitemap.xml": response(flat, headers={"Content-Type": "text/html"})}, {}),
    ("root-invalid", {"/sitemap.xml": response("<bad")}, {}),
    ("root-trailing-xml", {"/sitemap.xml": response(flat + "<urlset/>")}, {}),
    ("root-missing", {"/sitemap.xml": response("", 404, {"TDM-Reservation": "1"})}, {}),
    (
        "root-header-policy",
        {
            "/sitemap.xml": response(
                "<bad", headers={"TDM-Reservation": "1", "TDM-Policy": "root-policy"}
            )
        },
        {},
    ),
    ("child-missing", {**pages, "/sitemap-aa-en.xml": response("", 410)}, {}),
    ("child-invalid", {**pages, "/sitemap-aa-en.xml": response("<bad")}, {}),
    ("child-fail503", {**pages, "/sitemap-aa-en.xml": response("", 503)}, {}),
    (
        "child-fail403",
        {**pages, "/sitemap-aa-en.xml": response("", 403, {"TDM-Reservation": "1"})},
        {},
    ),
    (
        "child-header-policy",
        {
            **pages,
            "/sitemap-aa-en.xml": response(
                "<bad", headers={"TDM-Reservation": "1", "TDM-Policy": "child-policy"}
            ),
        },
        {},
    ),
    (
        "child-meta-policy",
        {**pages, "/sitemap-aa-en.xml": response('<meta name="tdm-reservation" content="1">')},
        {},
    ),
]


async def no_sleep(_):
    pass


async def main():
    output = []
    for name, resources, options in cases:
        requests = []

        def respond(request, requests=requests, resources=resources):
            requests.append({"method": request.method, "url": str(request.url)})
            v = resources.get(request.url.path, response("", 404))
            return httpx.Response(v["status"], text=v["body"], headers=v["headers"])

        metadata = {"sitemap_url": ORIGIN + "/sitemap.xml", **options}
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with patch("src.shared.http_retry.asyncio.sleep", no_sleep):
                try:
                    urls, new_url = await discover(
                        {"board_url": ORIGIN + "/careers", "metadata": metadata}, client
                    )
                    expected = {"error": False, "urls": sorted(urls), "new_sitemap_url": new_url}
                except Exception as exc:
                    expected = {"error": True, "kind": type(exc).__name__}
        output.append(
            {
                "name": name,
                "resources": resources,
                "metadata": metadata,
                "requests": requests,
                "expected": expected,
            }
        )
    Path(__file__).with_name("python_phenom.json").write_text(
        json.dumps({"cases": output}, indent=2, ensure_ascii=False) + "\n"
    )


asyncio.run(main())
