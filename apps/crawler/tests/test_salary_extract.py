"""Tests for salary extraction heuristics."""

from __future__ import annotations

from src.core.salary_extract import SalaryRange, extract_salary, extract_salary_unified


class TestAmazonFormat:
    def test_annual_usd(self):
        html = "<p>USA, WA, Redmond - 137,300.00 - 185,700.00 USD annually</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0] == SalaryRange(min=137300, max=185700, currency="USD", period="yearly")

    def test_annual_cad(self):
        html = "<p>CAN, ON, Toronto - 114,800.00 - 191,800.00 CAD annually</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0] == SalaryRange(min=114800, max=191800, currency="CAD", period="yearly")

    def test_hourly(self):
        html = "<p>USA, NV, Sparks - 27.00 - 48.00 USD hourly</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0] == SalaryRange(min=2700, max=4800, currency="USD", period="hourly")

    def test_multiple_locations(self):
        html = (
            "<p>USA, CO, Denver - 153,600.00 - 207,800.00 USD annually<br>"
            "USA, NM, Virtual Location - New Mexico - 138,200.00 - 187,000.00 USD annually</p>"
        )
        result = extract_salary(html)
        assert len(result) == 2

    def test_unified_picks_widest(self):
        html = (
            "<p>USA, CO, Denver - 153,600.00 - 207,800.00 USD annually<br>"
            "USA, NM, Virtual Location - New Mexico - 138,200.00 - 187,000.00 USD annually</p>"
        )
        result = extract_salary_unified(html)
        assert result is not None
        assert result.min == 138200
        assert result.max == 207800


class TestDollarRange:
    def test_google_style(self):
        html = (
            "<p>The US base salary range for this full-time position"
            " is $174,000-$252,000 + bonus + equity + benefits.</p>"
        )
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0] == SalaryRange(min=174000, max=252000, currency="USD", period="yearly")

    def test_spaced_range(self):
        html = "<p>The base salary range is $100,000 - $130,000/annual</p>"
        result = extract_salary(html)
        assert len(result) >= 1
        r = result[0]
        assert r.min == 100000
        assert r.max == 130000

    def test_em_dash(self):
        html = "<p>The salary range for this position is $102,000—$210,000 USD</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0].min == 102000
        assert result[0].max == 210000

    def test_no_comma(self):
        html = "<p>Pay range: $115600 - $246900</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0].min == 115600
        assert result[0].max == 246900


class TestSingleDollar:
    def test_annually(self):
        html = "<p>Salary: $105,000 Annually</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0].min == 105000
        assert result[0].max is None
        assert result[0].period == "yearly"

    def test_per_year(self):
        html = "<p>Base salary $120,000 per year</p>"
        result = extract_salary(html)
        assert len(result) >= 1
        r = [x for x in result if x.max is None]
        assert any(x.min == 120000 for x in r)

    def test_hourly(self):
        html = "<p>Rate: $107.40/hr</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0].min == 10740
        assert result[0].period == "hourly"


class TestEUR:
    def test_salary_from_eur(self):
        html = "<p><b>Salary:</b> From 1800 EUR/month</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0].min == 1800
        assert result[0].currency == "EUR"
        assert result[0].period == "monthly"

    def test_gehalt_eur(self):
        html = "<p>Das Mindestgehalt liegt bei mindestens EUR 46.000 brutto jährlich</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0].min == 46000
        assert result[0].period == "yearly"

    def test_eur_monthly_intern(self):
        html = "<p>For this position we offer a monthly salary of EUR 1507 gross per month.</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0].min == 1507
        assert result[0].period == "monthly"


class TestGBP:
    def test_range(self):
        html = "<p>Salary: £25,500 – £28,000 dependent on programme and location.</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0] == SalaryRange(min=25500, max=28000, currency="GBP", period="yearly")

    def test_hourly(self):
        html = "<p>Pay: £12.85 per hour</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0].min == 1285
        assert result[0].period == "hourly"

    def test_range_trailing_sentence_period(self):
        # Regression: Asana Greenhouse posting had
        # "...between £98,000.00-£115,500.00. The actual base salary..."
        # The sentence-ending dot was glued onto the max value and the
        # greedy [\d,.]+ capture pulled it into _parse_number, raising
        # "could not convert string to float: '115500.00.'" every cycle.
        html = (
            "<p>For this role, the estimated base salary range is between "
            "£98,000.00-£115,500.00. The actual base salary will vary.</p>"
        )
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0] == SalaryRange(min=98000, max=115500, currency="GBP", period="yearly")


class TestFalsePositives:
    """These must return empty — 0 false positives is the goal."""

    def test_revenue(self):
        html = "<p>Our company generates $781M in annual revenue.</p>"
        assert extract_salary(html) == []

    def test_funding(self):
        html = "<p>We raised $35 million in Series B funding.</p>"
        assert extract_salary(html) == []

    def test_company_valuation(self):
        html = "<p>market cap of $11B</p>"
        assert extract_salary(html) == []

    def test_transport_compensation_eur(self):
        html = "<p>Transport compensation - 300 euros net per month.</p>"
        assert extract_salary(html) == []

    def test_referral_bonus_eur(self):
        html = (
            "<p>you can receive a reward from 300 to 1000 EUR,"
            " depending on the seniority of the candidate</p>"
        )
        assert extract_salary(html) == []

    def test_gym_benefit(self):
        html = "<p>£40 for gym and fitness memberships monthly.</p>"
        assert extract_salary(html) == []

    def test_billion_revenue(self):
        html = "<p>deals ranging from £50m to over £5bn and our clients include...</p>"
        assert extract_salary(html) == []

    def test_small_amounts(self):
        html = "<p>Salary: $500 per week</p>"
        assert extract_salary(html) == []

    def test_no_salary_info(self):
        html = (
            "<p>We are looking for a software engineer to join our"
            " team. 5+ years of experience required.</p>"
        )
        assert extract_salary(html) == []

    def test_eur_benefit_not_salary(self):
        html = "<p>Essenszuschuss i. H. v. bis zu 112,50 EUR</p>"
        assert extract_salary(html) == []

    def test_newborn_bonus_not_salary(self):
        html = "<li>Newborn bonus (€500 per child)</li>"
        assert extract_salary(html) == []

    def test_referral_reward_not_salary(self):
        html = "<p>you can receive a reward from 300 to 1000 EUR, depending on the seniority</p>"
        assert extract_salary(html) == []


class TestKSuffix:
    def test_dollar_k_range(self):
        html = "<p>Base salary range: $85K-$120K + equity + benefits</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0].min == 85000
        assert result[0].max == 120000

    def test_dollar_k_range_lowercase(self):
        html = "<p>Salary: $90k - $130k annually</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0].min == 90000
        assert result[0].max == 130000


class TestCHF:
    def test_chf_apostrophe_range(self):
        html = "<p>Lohn: CHF 120'000 - 150'000 brutto jährlich</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0] == SalaryRange(min=120000, max=150000, currency="CHF", period="yearly")

    def test_chf_monthly(self):
        html = "<p>Gehalt: CHF 8'500 pro Monat brutto</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0].min == 8500
        assert result[0].currency == "CHF"
        assert result[0].period == "monthly"


class TestEURBonus:
    """EUR salary lines that mention bonus should NOT be disqualified."""

    def test_salary_plus_bonus(self):
        html = "<p><b>Salary:</b> From 3000 EUR/month + bonus</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0].min == 3000


class TestNonUSDDollarMarkers:
    """In-text `$` currency markers (closes #3191).

    Pre-fix: ``$120K AUD`` was stored as USD then inflated +80% to EUR.
    The detector now recognises AUD/NZD/SGD/HKD/BRL/MXN markers in two
    positions:
      1. Pre-amount prefix:  ``A$80,000``, ``S$100k``, ``HK$500K``, ``R$50.000``
      2. Post-amount ISO code adjacent to the amount: ``$120K AUD``
    USD remains the fallback when no marker is present.
    """

    # ── Suffix ISO code (range form) ──

    def test_dollar_range_aud_suffix(self):
        html = "<p>The base salary range is $120,000-$150,000 AUD per year</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0] == SalaryRange(min=120000, max=150000, currency="AUD", period="yearly")

    def test_dollar_range_nzd_suffix(self):
        html = "<p>Salary: $90,000 - $120,000 NZD annually + benefits</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0].currency == "NZD"
        assert result[0].min == 90000
        assert result[0].max == 120000

    # ── Prefix marker (single amount) ──

    def test_prefix_a_dollar_single_aud(self):
        html = "<p>Salary: A$80,000 per year</p>"
        result = extract_salary(html)
        aud = [r for r in result if r.currency == "AUD"]
        assert len(aud) == 1
        assert aud[0].min == 80000
        assert aud[0].period == "yearly"

    def test_prefix_au_dollar_single_aud(self):
        html = "<p>Compensation: AU$95,000 annually</p>"
        result = extract_salary(html)
        aud = [r for r in result if r.currency == "AUD"]
        assert len(aud) == 1
        assert aud[0].min == 95000

    def test_prefix_s_dollar_single_sgd(self):
        html = "<p>Salary: S$100k annually + bonus</p>"
        result = extract_salary(html)
        sgd = [r for r in result if r.currency == "SGD"]
        assert len(sgd) == 1
        assert sgd[0].min == 100000

    def test_prefix_hk_dollar_single_hkd(self):
        html = "<p>Compensation: HK$500K per year</p>"
        result = extract_salary(html)
        hkd = [r for r in result if r.currency == "HKD"]
        assert len(hkd) == 1
        assert hkd[0].min == 500000

    def test_prefix_r_dollar_brl_european_decimal(self):
        # Brazilian decimal uses "." as thousands separator: R$50.000 = 50000.
        html = "<p>Annual salary: R$50.000 per year</p>"
        result = extract_salary(html)
        brl = [r for r in result if r.currency == "BRL"]
        assert len(brl) == 1
        assert brl[0].min == 50000
        assert brl[0].period == "yearly"

    def test_prefix_mx_dollar_mxn(self):
        html = "<p>Salary: MX$800,000 annually</p>"
        result = extract_salary(html)
        mxn = [r for r in result if r.currency == "MXN"]
        assert len(mxn) == 1
        assert mxn[0].min == 800000

    def test_prefix_single_defaults_yearly_without_period(self):
        html = "<p>Salary: A$80,000 plus benefits</p>"
        result = extract_salary(html)
        aud = [r for r in result if r.currency == "AUD"]
        assert len(aud) == 1
        assert aud[0].min == 80000
        assert aud[0].period == "yearly"

    def test_prefix_single_defaults_monthly_without_period(self):
        html = "<p>Compensation: S$8,000 plus bonus</p>"
        result = extract_salary(html)
        sgd = [r for r in result if r.currency == "SGD"]
        assert len(sgd) == 1
        assert sgd[0].min == 8000
        assert sgd[0].period == "monthly"

    def test_prefix_single_defaults_hourly_without_period(self):
        html = "<p>Pay rate: S$250 plus shift allowance</p>"
        result = extract_salary(html)
        sgd = [r for r in result if r.currency == "SGD"]
        assert len(sgd) == 1
        assert sgd[0].min == 25000
        assert sgd[0].period == "hourly"

    def test_prefix_single_defaults_hourly_below_monthly_boundary(self):
        html = "<p>Pay rate: S$499 plus shift allowance</p>"
        result = extract_salary(html)
        sgd = [r for r in result if r.currency == "SGD"]
        assert len(sgd) == 1
        assert sgd[0].min == 49900
        assert sgd[0].period == "hourly"

    def test_prefix_single_defaults_monthly_at_boundary(self):
        html = "<p>Compensation: S$500 plus bonus</p>"
        result = extract_salary(html)
        sgd = [r for r in result if r.currency == "SGD"]
        assert len(sgd) == 1
        assert sgd[0].min == 500
        assert sgd[0].period == "monthly"

    def test_prefix_single_rejects_tiny_amount_without_period(self):
        html = "<p>Salary: A$4 plus benefits</p>"
        assert extract_salary(html) == []

    # ── Prefix marker (range form) ──

    def test_prefix_a_dollar_range_aud(self):
        html = "<p>The base salary range is A$120,000 - A$150,000 per year</p>"
        result = extract_salary(html)
        aud = [r for r in result if r.currency == "AUD"]
        assert len(aud) == 1
        assert aud[0].min == 120000
        assert aud[0].max == 150000

    # ── USD preservation (no marker → still USD) ──

    def test_plain_dollar_remains_usd(self):
        html = "<p>Salary range: $80K-$120K + equity + benefits</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0].currency == "USD"
        assert result[0].min == 80000

    def test_dollar_with_cad_suffix(self):
        html = "<p>The salary range for this position is $100,000 - $150,000 CAD</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0].currency == "CAD"
        assert result[0].min == 100000

    # ── Adjacency rule: ISO code far from amount should NOT flip currency ──

    def test_non_adjacent_aud_does_not_flip_currency(self):
        # A US posting that mentions AUD later in the description (e.g.
        # "AUD performance bonus") must NOT be classified as AUD.
        html = (
            "<p>The US base salary range for this full-time position is "
            "$80,000 - $120,000 + bonus + equity + benefits. "
            "International candidates may also be eligible for an AUD "
            "performance bonus depending on region.</p>"
        )
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0].currency == "USD"
        assert result[0].min == 80000
        assert result[0].max == 120000

    def test_adjacent_after_token_does_not_flip(self):
        # "+" / "plus" / "and" between amount and ISO code must not
        # propagate the ISO code onto the amount.
        html = "<p>Salary range: $80,000 - $120,000 plus AUD performance bonus</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0].currency == "USD"

    # ── Inflation bug from #3191 ──

    def test_3191_inflation_bug_scenario(self):
        # Pre-fix: this was stored as USD ($120,000) then inflated +53%
        # when converted to EUR (~110,400 instead of the correct ~72,000).
        html = (
            "<p>Sydney, AU. Base salary range: $120,000 - $150,000 AUD "
            "+ bonus + equity + benefits.</p>"
        )
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0] == SalaryRange(min=120000, max=150000, currency="AUD", period="yearly")

    # ── US$/C$/CDN$ regression (#3191 follow-up) ──
    # The original PR's word-boundary lookbehind on `_DOLLAR_RANGE_RE` and
    # `_SINGLE_DOLLAR_PERIOD_RE` was too tight — it rejected any letter
    # before `$`, including the legitimate ``US$``, ``C$``, ``CDN$`` and
    # ``CA$`` conventions seen in international postings.

    def test_us_dollar_prefix_returns_usd(self):
        html = "<p>Salary: US$120,000 per year</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0].currency == "USD"
        assert result[0].min == 120000
        assert result[0].period == "yearly"

    def test_us_dollar_range(self):
        html = "<p>Salary: US$100,000 - US$150,000 per year</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0] == SalaryRange(min=100000, max=150000, currency="USD", period="yearly")

    def test_c_dollar_prefix_returns_cad(self):
        html = "<p>Salary: C$120,000 per year</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0].currency == "CAD"
        assert result[0].min == 120000
        assert result[0].period == "yearly"

    def test_cdn_dollar_prefix_returns_cad(self):
        html = "<p>Salary: CDN$120,000 per year</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0].currency == "CAD"
        assert result[0].min == 120000
        assert result[0].period == "yearly"

    def test_ca_dollar_prefix_returns_cad(self):
        # ``CA$`` is the less-common but still seen Canadian dollar prefix.
        html = "<p>Compensation: CA$95,000 annually</p>"
        result = extract_salary(html)
        assert len(result) == 1
        assert result[0].currency == "CAD"
        assert result[0].min == 95000

    def test_bare_n_dollar_is_not_nzd(self):
        # ``N$`` alone is ambiguous (could be Namibian Dollar). Only
        # ``NZ$`` is treated as a New Zealand marker — ``N$`` falls back
        # to USD (no explicit prefix mapping).
        html = "<p>Salary: NZ$80,000 per year</p>"
        result = extract_salary(html)
        nzd = [r for r in result if r.currency == "NZD"]
        assert len(nzd) == 1
        assert nzd[0].min == 80000
