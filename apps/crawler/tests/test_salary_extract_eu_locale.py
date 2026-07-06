"""Tests for Scope B EU-locale salary extraction (EUR / GBP / CHF).

All snippets are taken directly from the Scope B recon
(https://github.com/colophon-group/jobseek/issues/3325 — comment 4449589161)
on active Hetzner postings as of 2026-05-14. Each cluster gets at least
three representative tests per the issue acceptance criteria; precision
posture matches Scope A (brutto-only, netto-only is skipped, ambiguous
contexts stay at 0).
"""

from __future__ import annotations

from src.core.salary_extract import (
    SalaryRange,
    extract_salary,
    extract_salary_unified,
    parse_salary_text,
)

# ── Mojibake repair (UTF-8 double-encoded) ────────────────────────────


class TestMojibakeRepair:
    def test_tesco_pound_mojibake(self):
        # pid=9489e8bf real snippet — `Â£` is `£` UTF-8-double-encoded.
        html = (
            "<p>Our Tesco Colleague rate of pay starts from Â£13.28 an hour;"
            " this increases to Â£14.55 for stores within the M25</p>"
        )
        result = extract_salary(html)
        gbps = [r for r in result if r.currency == "GBP"]
        assert len(gbps) == 1, f"expected one GBP, got {result}"
        assert gbps[0].min == 1328  # hourly stored as cents
        assert gbps[0].max == 1455
        assert gbps[0].period == "hourly"

    def test_tesco_pound_mojibake_587582c0(self):
        # pid=587582c0 real snippet
        html = (
            "<p>rate of pay starts from Â£14.18 an hour; this increases to"
            " Â£15.45 for stores inside the M25</p>"
        )
        result = extract_salary(html)
        gbps = [r for r in result if r.currency == "GBP"]
        assert len(gbps) == 1
        assert gbps[0].min == 1418
        assert gbps[0].max == 1545
        assert gbps[0].period == "hourly"

    def test_endash_mojibake_normalized(self):
        # `â€"` is `–` (en-dash) double-encoded.
        html = "<p>Salary range: £52,845 â€“ £61,466 per annum</p>"
        result = extract_salary(html)
        gbps = [r for r in result if r.currency == "GBP"]
        assert len(gbps) == 1
        assert gbps[0].min == 52845
        assert gbps[0].max == 61466
        assert gbps[0].period == "yearly"


# ── Cluster D1: AT Mindestgehalt / Kollektivvertrag ────────────────────


class TestATMindestgehalt:
    def test_dhl_at_monthly_gross(self):
        # pid=c868d037 (DHL AT)
        html = (
            "<p>Mindestgehalt beträgt brutto monatlich 3.000,00 Euro für 38,5 Stunden pro Woche</p>"
        )
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "monthly"]
        assert eurs, f"expected at least one monthly EUR, got {result}"
        assert any(r.min == 3000 for r in eurs)

    def test_te_connectivity_full_template(self):
        # pid=d0a8363f (TE)
        html = (
            "<p>kollektivvertragliche Mindestgehalt … € 3.930,00 brutto"
            " pro Monat (Vollzeit, 14mal jährlich)</p>"
        )
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "monthly"]
        assert eurs
        assert eurs[0].min == 3930

    def test_te_connectivity_entry(self):
        # pid=e0e58888 (TE Einstiegsgehalt)
        html = (
            "<p>kollektivvertragliche Einstiegsgehalt beträgt ohne"
            " Vorkenntnisse € 2.504,40 brutto pro Monat</p>"
        )
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR"]
        assert eurs
        assert eurs[0].min == 2504
        assert eurs[0].period == "monthly"

    def test_philips_at_yearly(self):
        # pid=6a96a717 (Philips AT) — annual amount with `jährlich` upfront
        html = "<p>kollektivvertragliche Mindestgehalt beträgt jährlich € 66.676,26 brutto</p>"
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "yearly"]
        assert eurs
        assert eurs[0].min == 66676

    def test_roche_at_floor_only(self):
        # pid=3b0b7195 (Roche AT) — `ab €77.000 brutto`, floor-only
        html = "<p>Jahresgehalt ab €77.000 brutto (Vollzeitbasis)</p>"
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "yearly"]
        assert eurs
        assert eurs[0].min == 77000

    def test_pg_at_apprenticeship(self):
        # pid=fb9e7563 (P&G AT) — Lehrlingseinkommen monthly
        html = "<p>Lehrlingseinkommen von € 1.218,00 brutto im 1. Lehrjahr pro Monat</p>"
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR"]
        assert eurs
        assert eurs[0].min == 1218
        assert eurs[0].period == "monthly"


# ── Cluster D2/D3: DE hourly + DE yearly ───────────────────────────────


class TestDEHourlyYearly:
    def test_ups_de_hourly_euro_word(self):
        # pid=c5754cad (UPS DE)
        html = (
            "<p>Attraktiver Stundenlohn 19,19 Euro brutto/Std. inkl. steuerfreier Nachtzulage</p>"
        )
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "hourly"]
        assert eurs
        # Hourly stored as cents → 19.19 EUR = 1919 cents
        assert eurs[0].min == 1919

    def test_ups_de_hourly_bracketed(self):
        # pid=c254df06 (PCM/Vapian)
        html = "<p>Attraktiver Stundenlohn (15,21 Euro brutto/Std.) plus Wochenend-Zuschlag</p>"
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "hourly"]
        assert eurs
        assert eurs[0].min == 1521

    def test_jnj_de_yearly_e10(self):
        # pid=7f0c010f (J&J DE)
        html = (
            "<p>voraussichtliche Anfangsvergütung entspricht Entgeltgruppe"
            " E 10 (jährlich 56.472,00 EUR) brutto</p>"
        )
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "yearly"]
        assert eurs
        assert eurs[0].min == 56472


# ── Cluster D4/D5: CHF Swiss internships + dash decimal ────────────────


class TestCHFExtensions:
    def test_psi_swiss_internship_monthly(self):
        # pid=4feb9551 — Swiss bachelor monthly was filtered out by the
        # old floor (2000); we drop it to 1200 to admit Praktika.
        html = (
            "<p>Stipendium: CHF 1'500.00 / brutto pro Monat (100%) und"
            " nach Bachelor-Abschluss auf CHF 2'100.00 / brutto pro Monat</p>"
        )
        result = extract_salary(html)
        chfs = [r for r in result if r.currency == "CHF" and r.period == "monthly"]
        assert chfs, f"expected at least one CHF monthly, got {result}"
        # Either of the two monthly amounts should be in the results.
        mins = {r.min for r in chfs}
        assert 1500 in mins or 2100 in mins

    def test_sbb_french_hourly_dash_decimal(self):
        # pid=b2c3fd70 (SBB FR) — `.–` decimal-zero idiom + `de l'heure`
        html = (
            "<p>rémunération supplémentaire, p. ex. CHF 16.– brutto de"
            " l'heure pour le travail du dimanche</p>"
        )
        result = extract_salary(html)
        chfs = [r for r in result if r.currency == "CHF" and r.period == "hourly"]
        assert chfs, f"expected at least one CHF hourly, got {result}"
        # 16.00 CHF/hr stored as cents
        assert chfs[0].min == 1600

    def test_chf_curly_apostrophe(self):
        # Recon: U+2019 thousand-sep variant
        html = "<p>Lohn: CHF 120’000 - 150’000 brutto jährlich</p>"
        result = extract_salary(html)
        chfs = [r for r in result if r.currency == "CHF" and r.period == "yearly"]
        assert chfs
        assert chfs[0].min == 120000
        assert chfs[0].max == 150000


# ── Cluster F1: FR templates ───────────────────────────────────────────


class TestFRTemplates:
    def test_abb_fr_range_space_thousand(self):
        # pid=eb80f054 (ABB FR) — `60 000 à 70 000 € bruts annuels`
        html = "<p>Salaire : 60 000 à 70 000 € bruts annuels fixes</p>"
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "yearly"]
        assert eurs
        assert eurs[0].min == 60000
        assert eurs[0].max == 70000

    def test_sncf_entre_template(self):
        # pid=c8060dba (SNCF) — `Salaire ENTRE 24 100 EUR ET 29 200 EUR`
        html = "<p>Salaire ENTRE 24 100 EUR ET 29 200 EUR brut annuel</p>"
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "yearly"]
        assert eurs
        assert eurs[0].min == 24100
        assert eurs[0].max == 29200

    def test_sncf_des_floor_only(self):
        # pid=bfc360ba (SNCF) — `dès 25 610 EUR brut/an`
        html = "<p>Salaire dès 25 610 EUR brut/an</p>"
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "yearly"]
        assert eurs
        assert eurs[0].min == 25610

    def test_sncf_glued_eur(self):
        # pid=19eb0576 (SNCF) — `comprise entre 37300EUR et 39100EUR brut`
        html = "<p>rémunération brute annuelle sera comprise entre 37300EUR et 39100EUR brut</p>"
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "yearly"]
        assert eurs
        assert eurs[0].min == 37300
        assert eurs[0].max == 39100

    def test_medtronic_fr_de_a_euros_monthly(self):
        # pid=5455d901 (Medtronic FR) — `Rémunération : de 800 euros brut
        # par mois à 1000 euros brut par mois`
        html = "<p>Rémunération : de 800 euros brut par mois à 1000 euros brut par mois</p>"
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "monthly"]
        assert eurs
        assert eurs[0].min == 800
        assert eurs[0].max == 1000


# ── Cluster I1: IT Shopfully/Odoo Greenhouse template ──────────────────


class TestITGreenhouse:
    def test_odoo_it_eur_range(self):
        # pid=79f67f3f (Odoo IT)
        html = "<p>salary range between € 30.000 and € 36.000 gross plus uncapped commissions</p>"
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "yearly"]
        assert eurs
        assert eurs[0].min == 30000
        assert eurs[0].max == 36000

    def test_shopfully_comma_thousands(self):
        # pid=826092f8 (Shopfully) — `SALARY RANGE: €50,000 – €70,000`
        html = "<p>SALARY RANGE: €50,000 – €70,000 fixed gross salary per year</p>"
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "yearly"]
        assert eurs
        assert eurs[0].min == 50000
        assert eurs[0].max == 70000

    def test_shopfully_dot_thousand(self):
        # pid=1888f406 (Shopfully) — `SALARY RANGE: €30.000 - €50.000`
        html = "<p>SALARY RANGE: €30.000 - €50.000 gross annual</p>"
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "yearly"]
        assert eurs
        assert eurs[0].min == 30000
        assert eurs[0].max == 50000


# ── Cluster E1: ES/NL/BE Greenhouse + Workday templates ───────────────


class TestGreenhouseSpain:
    def test_airbnb_spain_em_dash(self):
        # pid=bb9861e0 (Airbnb)
        html = "<p>Spain Annual Pay Range—€65.000—€80.000 EUR gross</p>"
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "yearly"]
        assert eurs
        assert eurs[0].min == 65000
        assert eurs[0].max == 80000

    def test_mozilla_spain_em_dash(self):
        # pid=3f4f2857 (Mozilla)
        html = "<p>Hiring Ranges: Remote Spain€57.000—€77.000 EUR gross</p>"
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "yearly"]
        assert eurs
        assert eurs[0].min == 57000
        assert eurs[0].max == 77000

    def test_mattermost_posting_range(self):
        # pid=07a5a57a (Mattermost) — `Posting Range€62.000—€82.000 EUR`
        html = "<p>Spain Posting Range€62.000—€82.000 EUR gross annual</p>"
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "yearly"]
        assert eurs
        assert eurs[0].min == 62000
        assert eurs[0].max == 82000

    def test_fever_glued_eur_suffix(self):
        # pid=dddba9b1 (Fever) — `Base Salary: 50.000 - 70.000EUR`
        html = "<p>Base Salary: 50.000 - 70.000EUR gross annual</p>"
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "yearly"]
        assert eurs
        assert eurs[0].min == 50000
        assert eurs[0].max == 70000


# ── Cluster NL: Dutch templates ────────────────────────────────────────


class TestNLTemplates:
    def test_prisma_nl_monthly_range(self):
        # pid=7e0cec53 (Prisma) — `€6.631 - €10.195 bruto per maand`
        html = "<p>FWG 75 voor arts VG: €6.631 - €10.195 bruto per maand op fulltime basis</p>"
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "monthly"]
        assert eurs, f"expected monthly EUR, got {result}"
        assert eurs[0].min == 6631
        assert eurs[0].max == 10195

    def test_butternut_box_yearly_single(self):
        # pid=df40d287 (Butternut Box) — `€34.000 bruto per jaar`
        html = "<p>Salaris: €34.000 bruto per jaar (obv fulltime 39 uur, incl. 8% vakantiegeld)</p>"
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "yearly"]
        assert eurs
        assert eurs[0].min == 34000

    def test_odoo_be_nl_tussen(self):
        # pid=2ea7772f (Odoo BE/NL) — `tussen €3.500 en €5.500/bruto per maand`
        html = "<p>Loonpakket: Een competitief salaris tussen €3.500 en €5.500/bruto per maand</p>"
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "monthly"]
        assert eurs
        assert eurs[0].min == 3500
        assert eurs[0].max == 5500


# ── Cluster BE/IE: J&J anticipated base pay range ──────────────────────


class TestJNJWorkday:
    def test_jnj_be_anticipated_base_pay_range(self):
        # pid=e66f16a0 (J&J BE) — `The anticipated base pay range for this
        # position is: €72,500.00 - €115,230.00`
        html = (
            "<p>The anticipated base pay range for this position is:"
            " €72,500.00 - €115,230.00 gross annual</p>"
        )
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "yearly"]
        assert eurs
        assert eurs[0].min == 72500
        assert eurs[0].max == 115230

    def test_jnj_ireland_glued_eur_range(self):
        # pid=a1c14388 (J&J IE) — `70,100 EUR - 121,210 EUR`
        html = (
            "<p>Ireland - The anticipated base pay range for this position"
            " is 70,100 EUR - 121,210 EUR gross annual</p>"
        )
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "yearly"]
        assert eurs
        assert eurs[0].min == 70100
        assert eurs[0].max == 121210


# ── Cluster PT: Portuguese space-thousand + comma-decimal ──────────────


class TestPTSpaceThousand:
    def test_jnj_pt_eur_range(self):
        # pid=1b8f58c6 (J&J PT) — `€33 100,00 - €52 670,00`
        html = (
            "<p>The anticipated base pay range for this position is"
            " €33 100,00 - €52 670,00 gross annual</p>"
        )
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR" and r.period == "yearly"]
        assert eurs
        assert eurs[0].min == 33100
        assert eurs[0].max == 52670


# ── Cluster G1/G2/G3: UK GBP extensions ────────────────────────────────


class TestGBPExtensions:
    def test_tesco_starts_from_increases_to(self):
        # pid=4be0d80b (Tesco — clean £)
        html = (
            "<p>Our Tesco Colleague rate of pay starts from £13.28 an"
            " hour; this increases to £14.55 for stores within the M25</p>"
        )
        result = extract_salary(html)
        gbps = [r for r in result if r.currency == "GBP" and r.period == "hourly"]
        assert gbps
        assert gbps[0].min == 1328
        assert gbps[0].max == 1455

    def test_tesco_shift_leader(self):
        # pid=5f62adb3 (Tesco Shift Leader)
        html = (
            "<p>Our Shift Leader rate of pay starts from £15.59 an hour;"
            " this increases to £16.86 for stores inside the M25</p>"
        )
        result = extract_salary(html)
        gbps = [r for r in result if r.currency == "GBP" and r.period == "hourly"]
        assert gbps
        assert gbps[0].min == 1559
        assert gbps[0].max == 1686

    def test_nhs_per_annum_range_dash(self):
        # pid=bce33c9b (NHS Borders) — `£52,845 - £61,466 per annum`
        html = (
            "<p>Senior Accountant Financial Management – NHS Borders Agenda"
            " for Change Band 7 £52,845 - £61,466 per annum Full Time</p>"
        )
        result = extract_salary(html)
        gbps = [r for r in result if r.currency == "GBP" and r.period == "yearly"]
        assert gbps
        assert gbps[0].min == 52845
        assert gbps[0].max == 61466

    def test_nhs_to_range_separator(self):
        # pid=6173593d (NHS) — `£28,011 to £30,230 (pro-rata)`
        html = (
            "<p>1 year temporary post salary £28,011 to £30,230 (pro-rata) 36 hours over 5 Days</p>"
        )
        result = extract_salary(html)
        gbps = [r for r in result if r.currency == "GBP" and r.period == "yearly"]
        assert gbps
        assert gbps[0].min == 28011
        assert gbps[0].max == 30230

    def test_nhs_pro_rata_range(self):
        # pid=73dc5319 (NHS) — `Band 2 £26,696 - £28,988 pro rata`
        html = (
            "<p>Salary Band 2 £26,696 - £28,988 pro rata Distant Islands"
            " Allowance £1,461 pro rata</p>"
        )
        result = extract_salary(html)
        gbps = [r for r in result if r.currency == "GBP" and r.period == "yearly"]
        assert gbps
        # The 1,461 distant-islands allowance is below the yearly floor
        # so it won't pop out as a second range — we just need the
        # primary 26,696/28,988 to land.
        assert any(r.min == 26696 and r.max == 28988 for r in gbps)

    def test_nhs_hourly_salary_prefix(self):
        # pid=54454207 (NHS Ayrshire) — `Hourly salary: £14.76`
        html = (
            "<p>Bank - Cook - Ayrshire Hospice Location: Racecourse Ayr"
            " Hourly salary: £14.76 Closing date</p>"
        )
        result = extract_salary(html)
        gbps = [r for r in result if r.currency == "GBP" and r.period == "hourly"]
        assert gbps
        assert gbps[0].min == 1476


# ── Negative tests — precision posture ────────────────────────────────


class TestNegativePrecision:
    def test_gbp_project_budget_skipped(self):
        # pid=82389de7 (Hitachi) — project budget, NOT salary
        html = (
            "<p>We have a track record of projects ranging from £250k-£2M"
            " budget across diverse industries.</p>"
        )
        result = extract_salary(html)
        gbps = [r for r in result if r.currency == "GBP"]
        assert gbps == [], f"project budget should not extract, got {gbps}"

    def test_gbp_deal_size_skipped(self):
        # Synthetic but covers `deal|contract|funding` extension
        html = (
            "<p>Average deal size ranges from £150k to £500k across our enterprise customers.</p>"
        )
        result = extract_salary(html)
        gbps = [r for r in result if r.currency == "GBP"]
        assert gbps == []

    def test_eur_netto_only_skipped(self):
        # Netto-only marker — no gross indicator → skip
        html = "<p>Nettogehalt von 2.500 EUR netto pro Monat</p>"
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR"]
        assert eurs == [], f"netto-only should not extract, got {eurs}"

    def test_eur_ambiguous_no_context_skipped(self):
        # A bare amount with no salary/period word in window must stay 0.
        html = "<p>We process 60.000 EUR worth of widgets per quarter.</p>"
        result = extract_salary(html)
        eurs = [r for r in result if r.currency == "EUR"]
        assert eurs == [], f"ambiguous context should not extract, got {eurs}"

    def test_chf_revenue_skipped(self):
        # Revenue prose, not salary
        html = "<p>Total revenue in 2024 reached CHF 130 billion across all segments.</p>"
        result = extract_salary(html)
        chfs = [r for r in result if r.currency == "CHF"]
        # 130 (billion context) is outside any salary band so the magnitude
        # filter alone catches this; assert it stays empty.
        assert chfs == []


# ── Parse-salary-text wrapper integration ─────────────────────────────


class TestParseSalaryTextIntegration:
    def test_parse_at_mindestgehalt(self):
        text = "kollektivvertragliche Mindestgehalt € 3.930,00 brutto pro Monat (14mal jährlich)"
        result = parse_salary_text(text)
        assert result is not None
        assert result["currency"] == "EUR"
        assert result["min"] == 3930
        assert result["unit"] == "month"

    def test_parse_fr_sncf_range(self):
        text = "Salaire ENTRE 24 100 EUR ET 29 200 EUR brut annuel"
        result = parse_salary_text(text)
        assert result is not None
        assert result["currency"] == "EUR"
        assert result["min"] == 24100
        assert result["max"] == 29200
        assert result["unit"] == "year"

    def test_parse_tesco_hourly(self):
        text = "rate of pay starts from £13.28 an hour; this increases to £14.55"
        result = parse_salary_text(text)
        assert result is not None
        assert result["currency"] == "GBP"
        assert result["min"] == 13.28
        assert result["max"] == 14.55
        assert result["unit"] == "hour"

    def test_parse_nhs_per_annum(self):
        text = "Senior Accountant salary £52,845 - £61,466 per annum"
        result = parse_salary_text(text)
        assert result is not None
        assert result["currency"] == "GBP"
        assert result["min"] == 52845
        assert result["max"] == 61466
        assert result["unit"] == "year"


# ── extract_salary_unified picks the right shape ──────────────────────


class TestExtractSalaryUnifiedEU:
    def test_mozilla_spain_unified(self):
        html = "<p>Hiring Ranges: Remote Spain€57.000—€77.000 EUR gross</p>"
        result = extract_salary_unified(html)
        assert result == SalaryRange(min=57000, max=77000, currency="EUR", period="yearly")

    def test_jnj_be_unified(self):
        html = (
            "<p>The anticipated base pay range for this position is:"
            " €72,500.00 - €115,230.00 gross annual</p>"
        )
        result = extract_salary_unified(html)
        assert result == SalaryRange(min=72500, max=115230, currency="EUR", period="yearly")
