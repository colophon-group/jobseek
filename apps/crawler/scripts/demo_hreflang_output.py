"""Demo script showing hreflang discovery output for different company patterns.

Run: cd apps/crawler && uv run python scripts/demo_hreflang_output.py
"""

from __future__ import annotations

import textwrap
from urllib.parse import urlparse

from src.workspace.career_discover import (
    _ATS_URL_RE,
    _CAREER_PATH_RE,
    _extract_links_with_hreflang,
    _filter_career_hreflang,
)


def _banner(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def _show_results(label: str, html: str, base_url: str) -> None:
    print(f"\n--- {label} ---")
    print(f"Homepage: {base_url}")

    links, raw_hreflang = _extract_links_with_hreflang(html, base_url)
    career_filtered = _filter_career_hreflang(raw_hreflang)

    # Raw hreflang
    print(f"\nRaw hreflang links found: {len(raw_hreflang)}")
    for hl in raw_hreflang[:8]:
        tag = (
            "✓ career"
            if any(
                _CAREER_PATH_RE.search(urlparse(hl.url).path) or _ATS_URL_RE.search(hl.url)
                for _ in [None]
            )
            else "✗ filtered"
        )
        print(f"  [{tag}] {hl.hreflang:10s} → {hl.url}")
    if len(raw_hreflang) > 8:
        print(f"  ... and {len(raw_hreflang) - 8} more")

    # Career-filtered
    print(f"\nCareer-filtered hreflang: {len(career_filtered)}")

    # Merged extracted links
    hl_in = [lnk for lnk in links if lnk.source == "hreflang"]
    career_in = [lnk for lnk in links if lnk.source == "career_link"]
    ats_in = [lnk for lnk in links if lnk.source == "ats_embed"]
    print(f"\nMerged extracted links: {len(links)} total")
    print(f"  hreflang: {len(hl_in)}, career_link: {len(career_in)}, ats_embed: {len(ats_in)}")

    for lnk in links[:6]:
        print(
            f"  score={lnk.base_score:.2f} src={lnk.source:12s}"
            f" ctx={lnk.context:6s} text={lnk.text or '—':10s} {lnk.url}"
        )
    if len(links) > 6:
        print(f"  ... and {len(links) - 6} more")

    # Simulate the display output (what the agent sees)
    total = len(set(hl.url for hl in raw_hreflang))
    career_count = len(career_filtered)
    regions = sorted({hl.hreflang for hl in raw_hreflang})

    print("\n┌─── Agent display output ───────────────────────────────────")
    if total > 0:
        preview = ", ".join(regions[:10])
        more = f" (+{len(regions) - 10} more)" if len(regions) > 10 else ""
        print(
            f"│ [careers] Hreflang regional variants: {total} declared,"
            f" {career_count} with career paths."
        )
        if regions:
            print(f"│ [careers]   Regions: {preview}{more}")

        # Centralized ATS noise detection
        if career_count > 3:
            career_hosts = set()
            for hl in raw_hreflang:
                parsed = urlparse(hl.url)
                if _CAREER_PATH_RE.search(parsed.path) or _ATS_URL_RE.search(hl.url):
                    career_hosts.add(parsed.hostname)
            if len(career_hosts) == 1:
                print(
                    "│ ⚠ [careers] All career hreflang URLs share the same host"
                    " — likely a centralized ATS (one board may suffice)."
                )
            elif len(career_hosts) > 1:
                print(
                    f"│ [careers] Career URLs span {len(career_hosts)}"
                    " distinct hosts — likely separate regional boards."
                )
    else:
        print("│ (no hreflang tags found)")
    print("└────────────────────────────────────────────────────────────")


# ── Pattern A: Accenture-like (same domain, many regional career paths) ──

PATTERN_A_HTML = textwrap.dedent("""\
<html><head>
<link rel="alternate" hreflang="x-default" href="https://www.accenture.com/us-en/careers">
<link rel="alternate" hreflang="en-US" href="https://www.accenture.com/us-en/careers">
<link rel="alternate" hreflang="en-GB" href="https://www.accenture.com/gb-en/careers">
<link rel="alternate" hreflang="en-AU" href="https://www.accenture.com/au-en/careers">
<link rel="alternate" hreflang="en-SG" href="https://www.accenture.com/sg-en/careers">
<link rel="alternate" hreflang="en-AE" href="https://www.accenture.com/ae-en/careers">
<link rel="alternate" hreflang="de-DE" href="https://www.accenture.com/de-de/careers/karriere">
<link rel="alternate" hreflang="de-AT" href="https://www.accenture.com/at-de/careers/karriere">
<link rel="alternate" hreflang="de-CH" href="https://www.accenture.com/ch-de/careers/karriere">
<link rel="alternate" hreflang="fr-FR" href="https://www.accenture.com/fr-fr/careers/carrieres">
<link rel="alternate" hreflang="fr-CA" href="https://www.accenture.com/ca-fr/careers/carrieres">
<link rel="alternate" hreflang="fr-BE" href="https://www.accenture.com/be-fr/careers/carrieres">
<link rel="alternate" hreflang="it-IT" href="https://www.accenture.com/it-it/careers/carriera">
<link rel="alternate" hreflang="es-ES" href="https://www.accenture.com/es-es/careers/empleo">
<link rel="alternate" hreflang="pt-BR" href="https://www.accenture.com/br-pt/careers/vagas">
<link rel="alternate" hreflang="nl-NL" href="https://www.accenture.com/nl-nl/careers/vacatures">
<link rel="alternate" hreflang="ja-JP" href="https://www.accenture.com/jp-ja/careers">
<link rel="alternate" hreflang="zh-CN" href="https://www.accenture.com/cn-zh/careers">
</head><body>
<nav><a href="/us-en/careers">Careers</a></nav>
</body></html>
""")


# ── Pattern B: Henkel-like (different domains per region) ──

PATTERN_B_HTML = textwrap.dedent("""\
<html><head>
<link rel="alternate" hreflang="en" href="https://www.henkel.com/careers">
<link rel="alternate" hreflang="de" href="https://www.henkel.de/karriere">
<link rel="alternate" hreflang="fr" href="https://www.henkel.fr/carrieres">
<link rel="alternate" hreflang="es" href="https://www.henkel.es/empleo">
<link rel="alternate" hreflang="it" href="https://www.henkel.it/carriera">
<link rel="alternate" hreflang="nl" href="https://www.henkel.nl/vacatures">
<link rel="alternate" hreflang="pt" href="https://www.henkel.com.br/vagas">
<link rel="alternate" hreflang="x-default" href="https://www.henkel.com/careers">
</head><body>
<nav><a href="/careers">Careers</a></nav>
</body></html>
""")


# ── Pattern C: Oracle/IBM-like (centralized ATS, same host) ──

PATTERN_C_HTML = textwrap.dedent("""\
<html><head>
<link rel="alternate" hreflang="en" href="https://www.oracle.com/careers/">
<link rel="alternate" hreflang="de" href="https://www.oracle.com/de/careers/">
<link rel="alternate" hreflang="fr" href="https://www.oracle.com/fr/careers/">
<link rel="alternate" hreflang="es" href="https://www.oracle.com/es/careers/">
<link rel="alternate" hreflang="it" href="https://www.oracle.com/it/careers/">
<link rel="alternate" hreflang="pt" href="https://www.oracle.com/pt/careers/">
<link rel="alternate" hreflang="ja" href="https://www.oracle.com/jp/careers/">
<link rel="alternate" hreflang="ko" href="https://www.oracle.com/kr/careers/">
<link rel="alternate" hreflang="zh-CN" href="https://www.oracle.com/cn/careers/">
<link rel="alternate" hreflang="x-default" href="https://www.oracle.com/careers/">
</head><body>
<nav><a href="/careers/">Careers</a></nav>
<iframe src="https://oracle.wd1.myworkdayjobs.com/en/search"></iframe>
</body></html>
""")


# ── Pattern D: Cisco-like (hreflang to non-career pages) ──

PATTERN_D_HTML = textwrap.dedent("""\
<html><head>
<link rel="alternate" hreflang="en" href="https://www.cisco.com/">
<link rel="alternate" hreflang="de" href="https://www.cisco.com/site/de/de/index.html">
<link rel="alternate" hreflang="fr" href="https://www.cisco.com/site/fr/fr/index.html">
<link rel="alternate" hreflang="es" href="https://www.cisco.com/site/es/es/index.html">
<link rel="alternate" hreflang="ja" href="https://www.cisco.com/site/jp/ja/index.html">
<link rel="alternate" hreflang="ko" href="https://www.cisco.com/site/kr/ko/index.html">
<link rel="alternate" hreflang="x-default" href="https://www.cisco.com/">
</head><body>
<nav><a href="https://jobs.cisco.com">Careers</a></nav>
</body></html>
""")


# ── Pattern E: Mixed — some career hreflang + body ATS embed ──

PATTERN_E_HTML = textwrap.dedent("""\
<html><head>
<link rel="alternate" hreflang="en" href="https://company.com/en/jobs">
<link rel="alternate" hreflang="de" href="https://company.com/de/jobs">
<link rel="alternate" hreflang="fr" href="https://company.com/fr/about">
<link rel="alternate" hreflang="es" href="https://company.com/es/about">
<link rel="stylesheet" href="https://company.com/styles.css">
<link rel="canonical" href="https://company.com/">
</head><body>
<nav><a href="/en/jobs">Careers</a></nav>
<div><a href="https://boards.greenhouse.io/companyxyz">Open roles</a></div>
</body></html>
""")


# ── No hreflang at all ──

NO_HREFLANG_HTML = textwrap.dedent("""\
<html><head>
<link rel="canonical" href="https://startup.io/">
<link rel="stylesheet" href="/styles.css">
</head><body>
<nav><a href="https://jobs.lever.co/startup">Careers</a></nav>
</body></html>
""")


if __name__ == "__main__":
    _banner("Pattern A — Accenture-like: same domain, many regional career paths")
    _show_results("Accenture", PATTERN_A_HTML, "https://www.accenture.com/us-en/careers")

    _banner("Pattern B — Henkel-like: different domains per region, career paths")
    _show_results("Henkel", PATTERN_B_HTML, "https://www.henkel.com/careers")

    _banner("Pattern C — Oracle-like: centralized ATS, same host (noise)")
    _show_results("Oracle", PATTERN_C_HTML, "https://www.oracle.com/")

    _banner("Pattern D — Cisco-like: hreflang to non-career pages (ignore)")
    _show_results("Cisco", PATTERN_D_HTML, "https://www.cisco.com/")

    _banner("Pattern E — Mixed: some career hreflang + body ATS embed")
    _show_results("Mixed company", PATTERN_E_HTML, "https://company.com/")

    _banner("No hreflang — typical small company")
    _show_results("Startup", NO_HREFLANG_HTML, "https://startup.io/")
