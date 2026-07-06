"""Issue #3194 — hourly→yearly annualization parity test.

The web's `apps/web/src/lib/salary.ts::TO_YEARLY` mirrors this constant
for cross-period display conversions. If you change one, change both.
"""

from __future__ import annotations

from src.processing.cpu import _extract_salary_fields


def test_hourly_annualization_uses_2080_hours() -> None:
    """`salary_eur` (filter column) must annualize hourly at 2080 hours/year.

    Source of truth: 2080 = 52 weeks × 40 hours (US convention).
    """
    # $25/hr (stored as 2500 cents) at parity USD→EUR (rate 1.0).
    html = "<p>USA, NV, Sparks - 25.00 - 30.00 USD hourly</p>"
    rates = {"USD": 1.0}

    s_min, s_max, s_cur, s_per, s_eur = _extract_salary_fields(html, rates)

    assert s_per == "hourly"
    assert s_cur == "USD"
    assert s_min == 2500  # cents
    assert s_max == 3000  # cents
    # 25 * 2080 = 52000 — the annualized minimum, used by the salary filter.
    assert s_eur == 52000


def test_hourly_annualization_with_currency_conversion() -> None:
    """The annualization step uses 2080 regardless of currency."""
    # $50/hr (5000 cents) → 50 * 2080 = 104,000 USD/yr → 0.9 EUR/USD = 93,600 EUR.
    html = "<p>Compensation: $50.00/hour</p>"
    rates = {"USD": 0.9}

    _, _, _, s_per, s_eur = _extract_salary_fields(html, rates)
    assert s_per == "hourly"
    assert s_eur == round(50 * 2080 * 0.9)
    assert s_eur == 93600


def test_monthly_annualization_unchanged() -> None:
    """Monthly continues to annualize as period × 12 (regression guard)."""
    # Add explicit "Salary" context so the EUR pattern accepts the match.
    html = "<p>Salary: 5000 EUR per month</p>"
    rates = {"EUR": 1.0}

    _, _, _, s_per, s_eur = _extract_salary_fields(html, rates)
    assert s_per == "monthly"
    assert s_eur == 5000 * 12
