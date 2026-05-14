"""Tests for non-Eurozone EU currency extraction.

All test inputs are real strings taken from the Hetzner production crawl
(recon: #3263). The harness covers seven currencies — PLN, CZK, SEK, DKK,
HUF, RON, BGN — across prefix/suffix/range/single shapes, four locale
number formats, native period words, brutto/netto policy, and the
high-volume false-positive classes recon flagged (meal vouchers,
cafeteria budgets, revenue prose, Multisport allowances).
"""

from __future__ import annotations

from src.core.salary_extract import (
    extract_salary,
    extract_salary_unified,
    parse_salary_text,
)

# ── PLN — Poland (highest volume per recon: zł=1,249 / PLN=904) ────────


class TestPLN:
    def test_pln_iso_monthly_gross(self):
        # Real snippet, pid=18aa0405
        html = "<p>In this position, you will earn no less than 21,845 PLN gross per month.</p>"
        result = extract_salary(html)
        plns = [r for r in result if r.currency == "PLN"]
        assert len(plns) >= 1
        assert plns[0].min == 21845
        assert plns[0].period == "monthly"

    def test_pln_iso_annual_range(self):
        # Real snippet, pid=2a026cca
        html = (
            "<p>The anticipated base pay range for this position is"
            " 121 000 to 198 000 PLN gross annually.</p>"
        )
        result = extract_salary(html)
        plns = [r for r in result if r.currency == "PLN"]
        assert len(plns) == 1
        assert plns[0].min == 121000
        assert plns[0].max == 198000
        assert plns[0].period == "yearly"

    def test_pln_zl_range_with_european_decimal(self):
        # Real snippet, pid=0019a18e: "120 200,00 zł - 190 000,00 zł"
        html = "<p>Salary Range: 120 200,00 zł - 190 000,00 zł</p>"
        result = extract_salary(html)
        plns = [r for r in result if r.currency == "PLN"]
        assert len(plns) == 1
        assert plns[0].min == 120200
        assert plns[0].max == 190000
        assert plns[0].period == "yearly"

    def test_pln_zl_prefix_english_format(self):
        # Real snippet, pid=1635b74b
        html = "<p>Salary Range: zł330,630.00 - zł562,970.00 per year</p>"
        result = extract_salary(html)
        plns = [r for r in result if r.currency == "PLN"]
        assert len(plns) == 1
        assert plns[0].min == 330630
        assert plns[0].max == 562970
        assert plns[0].period == "yearly"

    def test_pln_polish_native_monthly(self):
        # Real snippet, pid=08298ce6 + pid=18aa0405 hybrid
        html = "<p>Wynagrodzenie podstawowe: 7 916,66 PLN miesięcznie brutto.</p>"
        result = extract_salary(html)
        plns = [r for r in result if r.currency == "PLN"]
        assert len(plns) >= 1
        assert plns[0].min == 7917 or plns[0].min == 7916  # rounding tolerance
        assert plns[0].period == "monthly"

    def test_pln_amazon_style_prefix(self):
        # Real snippet, pid=00f06242
        html = "<p>PL base pay range per year: PLN 358 000 - PLN 458 000</p>"
        result = extract_salary(html)
        plns = [r for r in result if r.currency == "PLN"]
        assert len(plns) == 1
        assert plns[0].min == 358000
        assert plns[0].max == 458000
        assert plns[0].period == "yearly"


# ── CZK — Czechia (1,125 Kč + 149 CZK per recon) ──────────────────────


class TestCZK:
    def test_czk_monthly_kc_suffix(self):
        # Real snippet, pid=7dd37d37: "až 70 000 Kč měsíčně"
        html = (
            "<p>Naši nejlepší prodejci berou domů až 70 000 Kč měsíčně,"
            " běžný průměr je kolem 40 000 Kč mzda fixní.</p>"
        )
        result = extract_salary(html)
        czks = [r for r in result if r.currency == "CZK"]
        assert len(czks) >= 1
        # The 70k or the 40k can come first depending on regex order
        mins = {r.min for r in czks}
        assert 70000 in mins or 40000 in mins
        for r in czks:
            assert r.period == "monthly"

    def test_czk_iso_monthly_gross(self):
        # Real recon snippet (pid=2eb4c127 shape): "63,120 CZK gross/month"
        html = "<p>Compensation: 63,120 CZK gross/month</p>"
        result = extract_salary(html)
        czks = [r for r in result if r.currency == "CZK"]
        assert len(czks) == 1
        assert czks[0].min == 63120
        assert czks[0].period == "monthly"

    def test_czk_iso_annual_range(self):
        # Real snippet, pid=0ce02047: "Czechia: 688,000.00 CZK - 1,032,000.00 CZK"
        html = "<p>Czechia: 688,000.00 CZK - 1,032,000.00 CZK annually gross</p>"
        result = extract_salary(html)
        czks = [r for r in result if r.currency == "CZK"]
        assert len(czks) == 1
        assert czks[0].min == 688000
        assert czks[0].max == 1032000
        assert czks[0].period == "yearly"

    def test_czk_range_space_locale(self):
        # Real snippet, pid=00e5fc60: "737 000,00 Kč - 1 143 000,00 Kč"
        html = "<p>Salary Range: 737 000,00 Kč - 1 143 000,00 Kč ročně hrubého</p>"
        result = extract_salary(html)
        czks = [r for r in result if r.currency == "CZK"]
        assert len(czks) == 1
        assert czks[0].min == 737000
        assert czks[0].max == 1143000
        assert czks[0].period == "yearly"

    def test_czk_meal_voucher_skipped(self):
        # Real snippet, pid=9082e468/d0b3935a/4cd2d527 (4 dupes seen)
        html = (
            "<p>Daily meal vouchers for restaurants and groceries (180 CZK"
            " per working day). Flexible cafeteria platform with thousands"
            " of lifestyle benefit options.</p>"
        )
        result = extract_salary(html)
        czks = [r for r in result if r.currency == "CZK"]
        assert czks == []

    def test_czk_cafeteria_budget_skipped(self):
        # Real snippet, pid=b4b0439e
        html = (
            "<p>well-being aktivity (příspěvek do Cafeterie 9600 Kč na rok, Multisport karta).</p>"
        )
        result = extract_salary(html)
        czks = [r for r in result if r.currency == "CZK"]
        assert czks == []


# ── SEK — Sweden (29 hits per recon; English ATS templates dominate) ──


class TestSEK:
    def test_sek_iso_annual_range(self):
        # Real snippet, pid=11ae7500
        html = (
            "<p>In Sweden, the compensation range for this role is"
            " SEK 609,985 - SEK 762,481 per year.</p>"
        )
        result = extract_salary(html)
        seks = [r for r in result if r.currency == "SEK"]
        assert len(seks) == 1
        assert seks[0].min == 609985
        assert seks[0].max == 762481
        assert seks[0].period == "yearly"

    def test_sek_iso_large_annual_range(self):
        # Real snippet, pid=cbec2051
        html = (
            "<p>For this role, the Base compensation range is"
            " SEK 898,000 - SEK 1,098,000 annually.</p>"
        )
        result = extract_salary(html)
        seks = [r for r in result if r.currency == "SEK"]
        assert len(seks) == 1
        assert seks[0].min == 898000
        assert seks[0].max == 1098000

    def test_sek_swedish_dot_locale(self):
        # Real snippet, pid=1122009c: "511.200 - 1.098.200 SEK yearly"
        html = "<p>Annual base salary: 511.200 - 1.098.200 SEK yearly gross.</p>"
        result = extract_salary(html)
        seks = [r for r in result if r.currency == "SEK"]
        assert len(seks) == 1
        assert seks[0].min == 511200
        assert seks[0].max == 1098200
        assert seks[0].period == "yearly"

    def test_sek_revenue_billion_skipped(self):
        # Real snippet, pid=8ebd01b1: corporate revenue, NOT salary
        html = (
            "<p>In 2023 Electrolux Group had sales of SEK 134 billion"
            " and employed 45,000 people around the world.</p>"
        )
        result = extract_salary(html)
        seks = [r for r in result if r.currency == "SEK"]
        assert seks == []

    def test_sek_wellness_allowance_skipped(self):
        # Real snippet, pid=d6d17beb
        html = (
            "<p>In Sweden, our benefits include a SEK 3,000 wellness allowance,"
            " a 37.5-hour workweek, and 30 days of vacation.</p>"
        )
        result = extract_salary(html)
        seks = [r for r in result if r.currency == "SEK"]
        assert seks == []


# ── DKK — Denmark (91 hits per recon) ──────────────────────────────────


class TestDKK:
    def test_dkk_annual_range_suffix(self):
        # Real snippet, pid=e125eaf6
        html = "<p>Annual Base Salary ranges from 651,000 DKK to 956,900 DKK gross per year.</p>"
        result = extract_salary(html)
        dkks = [r for r in result if r.currency == "DKK"]
        assert len(dkks) == 1
        assert dkks[0].min == 651000
        assert dkks[0].max == 956900
        assert dkks[0].period == "yearly"

    def test_dkk_annual_range_two_decimals(self):
        # Real snippet, pid=047321a2
        html = (
            "<p>For this role, the Annual Base Salary ranges from"
            " 742,600.00 to 1,091,600.00 DKK per year.</p>"
        )
        result = extract_salary(html)
        dkks = [r for r in result if r.currency == "DKK"]
        assert len(dkks) == 1
        assert dkks[0].min == 742600
        assert dkks[0].max == 1091600

    def test_dkk_hourly(self):
        # Real snippet, pid=15779e7e: "the hourly pay will be 205,17 DKK"
        html = "<p>Salary: For this position, the hourly pay will be 205,17 DKK gross.</p>"
        result = extract_salary(html)
        dkks = [r for r in result if r.currency == "DKK"]
        assert len(dkks) == 1
        # Hourly stored in cents
        assert dkks[0].min == 20517
        assert dkks[0].period == "hourly"

    def test_dkk_danish_dot_locale(self):
        # Real snippet, pid=03b8ce51 shape
        html = "<p>Den årlige grundløn er mellem 437.600 til 643.200 DKK bruttoløn per år.</p>"
        result = extract_salary(html)
        dkks = [r for r in result if r.currency == "DKK"]
        assert len(dkks) == 1
        assert dkks[0].min == 437600
        assert dkks[0].max == 643200
        assert dkks[0].period == "yearly"

    def test_dkk_revenue_billion_skipped(self):
        # Real snippet, pid=8a6fe8d8: "DKK 130 billion"
        html = (
            "<p>Corporate Procurement manages Novo Nordisk's global indirect"
            " spend of approximately DKK 130 billion.</p>"
        )
        result = extract_salary(html)
        dkks = [r for r in result if r.currency == "DKK"]
        assert dkks == []


# ── HUF — Hungary (85 HUF + 67 Ft per recon) ──────────────────────────


class TestHUF:
    def test_huf_iso_annual_range(self):
        # Real snippet, pid=09ef10e9 shape
        html = "<p>Annual Base Salary ranges from 16,174,040 to 25,878,470 HUF gross per year.</p>"
        result = extract_salary(html)
        hufs = [r for r in result if r.currency == "HUF"]
        assert len(hufs) == 1
        assert hufs[0].min == 16174040
        assert hufs[0].max == 25878470
        assert hufs[0].period == "yearly"

    def test_huf_iso_pay_range(self):
        # Real snippet, pid=e516f2ba
        html = "<p>pay range: 14,000,000 - 18,000,000 HUF gross annually</p>"
        result = extract_salary(html)
        hufs = [r for r in result if r.currency == "HUF"]
        assert len(hufs) == 1
        assert hufs[0].min == 14000000
        assert hufs[0].max == 18000000

    def test_huf_ft_european_decimal(self):
        # Real snippet, pid=addeee00: "10 627 270,00 Ft - 17 818 330,00 Ft"
        html = "<p>Salary Range: 10 627 270,00 Ft - 17 818 330,00 Ft annually</p>"
        result = extract_salary(html)
        hufs = [r for r in result if r.currency == "HUF"]
        assert len(hufs) == 1
        assert hufs[0].min == 10627270
        assert hufs[0].max == 17818330

    def test_huf_ft_single_monthly_brutto(self):
        # Real snippet, pid=be200709 shape
        html = "<p>Starting Salary (brutto): 1,130,000 HUF per month gross</p>"
        result = extract_salary(html)
        hufs = [r for r in result if r.currency == "HUF"]
        assert len(hufs) == 1
        assert hufs[0].min == 1130000
        assert hufs[0].period == "monthly"

    def test_huf_ft_not_walton_beach(self):
        # Real snippet, pid=e90f1023: "Ft. Walton Beach, Florida"
        # MUST NOT extract — Ft. is a location abbreviation, no digit prefix.
        html = (
            "<p>Intuition is headquartered in Sunnyvale, California, with offices"
            " in Washington, D.C.; San Diego; Ft. Walton Beach, Florida.</p>"
        )
        result = extract_salary(html)
        hufs = [r for r in result if r.currency == "HUF"]
        assert hufs == []

    def test_huf_huffman_road_not_currency(self):
        # Real snippet, pid=f05833e1: "1320 HUFFMAN RD" must not match.
        html = (
            "<p>Location: 1320 HUFFMAN RD, ANCHORAGE, AK, 99515 -Overtime Pay!!"
            " salary range available on request.</p>"
        )
        result = extract_salary(html)
        hufs = [r for r in result if r.currency == "HUF"]
        assert hufs == []

    def test_huf_sports_pass_perk_skipped(self):
        # Real snippet, pid=f0b84bc8
        html = (
            "<p>All You Can Move sports pass with 9500 HUF monthly allowance"
            " — included in the benefit package.</p>"
        )
        result = extract_salary(html)
        hufs = [r for r in result if r.currency == "HUF"]
        assert hufs == []


# ── RON — Romania (60 RON + 76 lei per recon) ─────────────────────────


class TestRON:
    def test_ron_iso_monthly_gross(self):
        # Real snippet, pid=02d790fe
        html = "<p>The starting salary: 9,200 RON gross/month for full-time role.</p>"
        result = extract_salary(html)
        rons = [r for r in result if r.currency == "RON"]
        assert len(rons) == 1
        assert rons[0].min == 9200
        assert rons[0].period == "monthly"

    def test_ron_iso_dot_locale(self):
        # Real snippet, pid=753d8a32: "9.200 RON Gross"
        html = "<p>The monthly starting salary is 9.200 RON Gross per month.</p>"
        result = extract_salary(html)
        rons = [r for r in result if r.currency == "RON"]
        assert len(rons) == 1
        assert rons[0].min == 9200
        assert rons[0].period == "monthly"

    def test_ron_iso_annual_range(self):
        # Real snippet, pid=0ce02047
        html = "<p>Romania: 124,160.00 RON - 186,240.00 RON gross annually.</p>"
        result = extract_salary(html)
        rons = [r for r in result if r.currency == "RON"]
        assert len(rons) == 1
        assert rons[0].min == 124160
        assert rons[0].max == 186240
        assert rons[0].period == "yearly"

    def test_ron_meal_voucher_skipped(self):
        # Real snippet, pid=0f2c84f1: "tichete de masă în valoare de 41,18 lei"
        html = (
            "<p>Beneficii: tichete de masă în valoare de 41,18 lei pentru"
            " fiecare zi lucrată. Decont pe transport de 365 de lei pe luna.</p>"
        )
        result = extract_salary(html)
        rons = [r for r in result if r.currency == "RON"]
        assert rons == []

    def test_ron_meal_voucher_english_skipped(self):
        # Real snippet, pid=02d790fe shape
        html = "<p>meal allowance: 40 lei per worked day, plus transport.</p>"
        result = extract_salary(html)
        rons = [r for r in result if r.currency == "RON"]
        assert rons == []

    def test_ron_leisure_prose_not_extracted(self):
        # Real snippet, pid=7513e15a: "leisure facilities" near "RON" is noise.
        # Word boundary on "lei" must reject "leisure".
        html = (
            "<p>We offer development opportunities and a wide range of"
            " benefits, including on-site leisure facilities, shopping"
            " concourse and day nurseries.</p>"
        )
        result = extract_salary(html)
        rons = [r for r in result if r.currency == "RON"]
        assert rons == []

    def test_ron_iso_in_word_not_extracted(self):
        # Real snippet, pid=5799a71d: "SENECA TRAIL SOUTH, RONCEVERTE"
        # Bare RON inside a place name must not match.
        html = (
            "<p>Location: 8721 SENECA TRAIL SOUTH, RONCEVERTE, WV, 24970"
            " — salary commensurate with experience.</p>"
        )
        result = extract_salary(html)
        rons = [r for r in result if r.currency == "RON"]
        assert rons == []


# ── BGN — Bulgaria (8 BGN per recon; almost all perks) ────────────────


class TestBGN:
    def test_bgn_ld_budget_perk_skipped(self):
        # Real snippet, pid=88e65d03 (5 dupes seen)
        html = (
            "<p>Personal L&D budget in the amount of 1000 BGN per year"
            " Additional health insurance and Mental wellbeing platform.</p>"
        )
        result = extract_salary(html)
        bgns = [r for r in result if r.currency == "BGN"]
        assert bgns == []

    def test_bgn_food_voucher_skipped(self):
        # Real snippet, pid=a2061e03
        html = (
            "<p>WFH Setup allowance, Laptop incentive scheme,"
            " Fully covered Multisports card, Food vouchers (120 BGN).</p>"
        )
        result = extract_salary(html)
        bgns = [r for r in result if r.currency == "BGN"]
        assert bgns == []

    def test_bgn_real_salary_extracted(self):
        # Synthetic — recon noted near-zero primary salary BGN hits, but the
        # regex must still emit for the rare valid case.
        html = "<p>Compensation: 3,500 BGN gross per month for senior engineers based in Sofia.</p>"
        result = extract_salary(html)
        bgns = [r for r in result if r.currency == "BGN"]
        assert len(bgns) == 1
        assert bgns[0].min == 3500
        assert bgns[0].period == "monthly"

    def test_bgn_annual_range_extracted(self):
        # Synthetic — minority real shape; range form.
        html = (
            "<p>Salary range: BGN 42,000 - BGN 60,000 annually gross for"
            " this role in our Sofia office.</p>"
        )
        result = extract_salary(html)
        bgns = [r for r in result if r.currency == "BGN"]
        assert len(bgns) == 1
        assert bgns[0].min == 42000
        assert bgns[0].max == 60000
        assert bgns[0].period == "yearly"

    def test_bgn_cyrillic_short_form(self):
        # Synthetic — Cyrillic short form, salary context in English.
        html = "<p>Compensation: 3,000 лв brutto per month for this position.</p>"
        result = extract_salary(html)
        bgns = [r for r in result if r.currency == "BGN"]
        assert len(bgns) == 1
        assert bgns[0].min == 3000
        assert bgns[0].period == "monthly"


# ── Brutto/netto policy ───────────────────────────────────────────────


class TestNetSkipped:
    """Per #3264: when "net" is asserted *without* a gross marker, skip."""

    def test_pln_net_skipped(self):
        # Real snippet, pid=053bfbc0 shape (mostly RON but PLN seen in the wild too)
        html = "<p>Salariul lunar net de 5,000 PLN per month, no other benefits.</p>"
        result = extract_salary(html)
        plns = [r for r in result if r.currency == "PLN"]
        assert plns == []

    def test_ron_net_skipped(self):
        # Real snippet, pid=053bfbc0: "Salariul lunar net de 1,330 lei"
        # 1330 is below RON monthly_min anyway, but using a higher number
        # to specifically test the net-skip path.
        html = "<p>Salariul lunar net de 5,500 lei per month.</p>"
        result = extract_salary(html)
        rons = [r for r in result if r.currency == "RON"]
        assert rons == []

    def test_pln_brutto_extracted_even_if_net_word_appears(self):
        # If both gross AND net words are present, prefer gross.
        html = (
            "<p>Salary: 12 000 PLN brutto per month. Net equivalent is shown"
            " in a separate table.</p>"
        )
        result = extract_salary(html)
        plns = [r for r in result if r.currency == "PLN"]
        assert len(plns) == 1
        assert plns[0].min == 12000

    def test_huf_brutto_extracted(self):
        html = "<p>Starting Salary (bruttó): 800,000 HUF per month brutto.</p>"
        result = extract_salary(html)
        hufs = [r for r in result if r.currency == "HUF"]
        assert len(hufs) == 1
        assert hufs[0].min == 800000


# ── Integration: unified picker + parse_salary_text ───────────────────


class TestEUUnified:
    def test_pln_unified_returns_yearly(self):
        html = "<p>Salary range: 121 000 to 198 000 PLN gross annually.</p>"
        sr = extract_salary_unified(html)
        assert sr is not None
        assert sr.currency == "PLN"
        assert sr.min == 121000
        assert sr.max == 198000
        assert sr.period == "yearly"

    def test_czk_parse_salary_text(self):
        text = "Compensation: 63,120 CZK gross per month."
        d = parse_salary_text(text)
        assert d is not None
        assert d["currency"] == "CZK"
        assert d["min"] == 63120
        assert d["unit"] == "month"

    def test_huf_unified_picks_yearly_range(self):
        # Yearly range over single monthly is picked by group-size preference.
        html = "<p>Annual base salary: 14,000,000 - 18,000,000 HUF gross.</p>"
        sr = extract_salary_unified(html)
        assert sr is not None
        assert sr.currency == "HUF"
        assert sr.period == "yearly"
        assert sr.min == 14000000


# ── Cross-currency no-bleed regressions ───────────────────────────────


class TestNoCrossBleed:
    def test_eur_in_polish_context_still_eur(self):
        # Recon: Polish postings often quote EUR for senior roles, PLN for
        # juniors. EUR pattern must continue to fire even with PLN tokens
        # in surrounding text.
        html = (
            "<p>Salary: From 4000 EUR/month gross. The Polish-market range"
            " starts at 16,000 PLN gross per month for juniors.</p>"
        )
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR"]
        plns = [r for r in result if r.currency == "PLN"]
        assert len(eurs) >= 1
        assert any(e.min == 4000 for e in eurs)
        assert len(plns) >= 1
        assert any(p.min == 16000 for p in plns)

    def test_dollar_range_still_works_with_eu_block_present(self):
        html = "<p>$120,000 - $180,000 per year + bonus</p>"
        result = extract_salary(html)
        usds = [r for r in result if r.currency == "USD"]
        assert len(usds) == 1
        assert usds[0].min == 120000
        assert usds[0].max == 180000
