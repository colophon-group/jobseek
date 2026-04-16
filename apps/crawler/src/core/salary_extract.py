"""Heuristic extraction of salary ranges from job description HTML.

Design goal: **zero false positives**.  We only extract salary data when
the surrounding context leaves no ambiguity that the numbers represent
compensation.  We intentionally sacrifice recall for precision.

Each pattern returns a list of ``SalaryRange`` dataclasses.  When multiple
ranges are found (e.g. Amazon posts with per-location lines), the caller
decides how to aggregate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── Result type ──────────────────────────────────────────────────────

Currency = str  # ISO 4217: USD, CAD, EUR, GBP, CHF


@dataclass(frozen=True, slots=True)
class SalaryRange:
    min: int  # cents (for hourly) or whole units (for annual)
    max: int | None
    currency: Currency
    period: str  # "yearly" | "monthly" | "hourly"


# ── HTML → plain text ────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")


def _html_to_text(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    return _WS_RE.sub(" ", text).strip()


# ── Utility ──────────────────────────────────────────────────────────


def _parse_number(s: str) -> float:
    """Parse '137,300.00' or '137300' or '1 800' or '120'000' → float."""
    s = s.replace(",", "").replace(" ", "").replace("'", "").replace("\u2019", "")
    # Defense in depth: currency regexes are tightened to forbid trailing
    # dots, but if any capture still arrives with a sentence-ending dot
    # (e.g. "£115,500.00."), it's never a decimal point on its own.
    s = s.rstrip(".")
    if s.upper().endswith("K"):
        return float(s[:-1]) * 1000
    return float(s)


_PERIOD_MAP: dict[str, str] = {
    "annually": "yearly",
    "annual": "yearly",
    "per year": "yearly",
    "per annum": "yearly",
    "/year": "yearly",
    "/yr": "yearly",
    "p.a.": "yearly",
    "yearly": "yearly",
    "year": "yearly",
    "(yr)": "yearly",
    "hourly": "hourly",
    "per hour": "hourly",
    "/hour": "hourly",
    "/hr": "hourly",
    "hr": "hourly",
    "monthly": "monthly",
    "per month": "monthly",
    "/month": "monthly",
    "/mo": "monthly",
}


def _detect_period(text: str) -> str | None:
    """Detect salary period from text following a numeric range."""
    lower = text.lower().strip()
    for token, period in _PERIOD_MAP.items():
        if lower.startswith(token):
            return period
    return None


# ── Pattern 1: Location-prefixed salary line ────────────────────────
#   "USA, WA, Redmond - 137,300.00 - 185,700.00 USD annually"
#   "CAN, ON, Toronto - 114,800.00 - 191,800.00 CAD annually"
#   Common ATS format across many US/CA employers.

_LOCATION_SALARY_RE = re.compile(
    r"[A-Z]{2,3},\s*[A-Z]{0,2},?\s*[\w .'-]*"  # location prefix (state can be empty)
    r"\s*-\s*"
    r"([\d,]+\.?\d*)"  # min
    r"\s*-\s*"
    r"([\d,]+\.?\d*)"  # max
    r"\s+(USD|CAD|EUR|GBP|CHF)\s+"
    r"(annually|hourly)",
    re.IGNORECASE,
)


def _extract_location_prefixed(text: str) -> list[SalaryRange]:
    results = []
    for m in _LOCATION_SALARY_RE.finditer(text):
        lo = _parse_number(m.group(1))
        hi = _parse_number(m.group(2))
        currency = m.group(3).upper()
        period = "yearly" if m.group(4).lower() == "annually" else "hourly"
        results.append(
            SalaryRange(
                min=int(lo) if period == "yearly" else int(lo * 100),
                max=int(hi) if period == "yearly" else int(hi * 100),
                currency=currency,
                period=period,
            )
        )
    return results


# ── Pattern 2: $X-$Y (+ bonus/equity/benefits context) ──────────────
#   "$174,000-$252,000 + bonus + equity + benefits"
#   "$100,000 - $150,000 CAD"
#   "$105,000-$149,000 + bonus"

_DOLLAR_RANGE_RE = re.compile(
    r"\$([\d,]+[Kk]?)\s*[-–—]\s*\$?([\d,]+[Kk]?)"
    r"(\s*.{0,80})",  # capture trailing context
)

# Context words that confirm this is a salary, not revenue/funding
_SALARY_CONTEXT_RE = re.compile(
    r"bonus|equity|benefits|salary|compensation|base pay|"
    r"annually|annual|per year|hourly|per hour|/year|/yr|/hr|"
    r"USD|CAD|GBP|total comp|"
    r"range|pay",
    re.IGNORECASE,
)

# Words that disqualify — revenue, funding, etc.
_NOT_SALARY_RE = re.compile(
    r"revenue|billion|million|funding|raised|ipo|valuation|"
    r"market cap|investment|assets|turnover",
    re.IGNORECASE,
)


def _extract_dollar_range(text: str) -> list[SalaryRange]:
    results = []
    for m in _DOLLAR_RANGE_RE.finditer(text):
        lo = _parse_number(m.group(1))
        hi = _parse_number(m.group(2))
        trailing = m.group(3)

        # Disqualify tiny amounts (below $15,000) and absurdly large ones
        if lo < 15000 or hi > 1_000_000:
            continue

        # Require salary-confirming context within trailing text
        # OR within the 100 chars before the match
        start = max(0, m.start() - 100)
        surrounding = text[start : m.end()] + trailing

        if _NOT_SALARY_RE.search(surrounding):
            continue

        if not _SALARY_CONTEXT_RE.search(surrounding):
            continue

        # Detect currency (default USD)
        currency = "USD"
        if "CAD" in trailing[:20].upper():
            currency = "CAD"

        # Detect period
        period = _detect_period(trailing.lstrip(" +").lstrip())
        if period is None:
            # Check for period in trailing after "bonus + equity +"
            period_text = re.sub(
                r"[\+,]?\s*(bonus|equity|benefits|stock)\s*", "", trailing, flags=re.IGNORECASE
            )
            period = _detect_period(period_text.lstrip(" +.,").lstrip())
        if period is None:
            period = "yearly"  # dollar ranges in this magnitude are almost always annual

        results.append(
            SalaryRange(
                min=int(lo),
                max=int(hi),
                currency=currency,
                period=period,
            )
        )
    return results


# ── Pattern 3: X - Y USD/CAD/EUR period (no currency symbol prefix) ──
#   "112,341 - 140,500 (yr)"

_BARE_RANGE_CURRENCY_RE = re.compile(
    r"([\d,]+\.?\d*)\s*[-–—]\s*([\d,]+\.?\d*)"
    r"\s+\(?(USD|CAD|EUR|GBP|CHF)\)?"
    r"(?:\s*\+\s*(?:bonus|equity|benefits|stock)\s*)*"  # optional trailing
    r"(?:\s+(annually|hourly|per year|per hour|per annum|monthly|per month))?"
    r"|"  # OR: amount (yr) pattern
    r"([\d,]+\.?\d*)\s*[-–—]\s*([\d,]+\.?\d*)"
    r"\s+\(yr\)",
    re.IGNORECASE,
)


def _extract_bare_range(text: str) -> list[SalaryRange]:
    results = []
    for m in _BARE_RANGE_CURRENCY_RE.finditer(text):
        if m.group(1):  # first alternative
            lo = _parse_number(m.group(1))
            hi = _parse_number(m.group(2))
            currency = m.group(3).upper()
            period_str = m.group(4)
        else:  # (yr) alternative
            lo = _parse_number(m.group(5))
            hi = _parse_number(m.group(6))
            currency = "USD"
            period_str = "yr"

        # Could be hourly if small — but without explicit hourly label, skip
        if (lo < 10000 or hi > 1_000_000) and not (period_str and "hour" in period_str.lower()):
            continue

        period = "yearly"
        if period_str:
            p = _detect_period(period_str)
            if p:
                period = p

        if period == "hourly":
            results.append(
                SalaryRange(
                    min=int(lo * 100),
                    max=int(hi * 100),
                    currency=currency,
                    period=period,
                )
            )
        else:
            results.append(
                SalaryRange(
                    min=int(lo),
                    max=int(hi),
                    currency=currency,
                    period=period,
                )
            )
    return results


# ── Pattern 4: $X/year or $X/hr (single amounts with explicit period) ──
#   "$107.40/hr", "$120,000 per year", "$105,000 Annually"

_SINGLE_DOLLAR_PERIOD_RE = re.compile(
    r"\$([\d,]+(?:\.\d+)?)\s*"
    r"(per year|per annum|annually|annual|/year|/yr|"
    r"per hour|hourly|/hour|/hr)",
    re.IGNORECASE,
)


def _extract_single_dollar(text: str) -> list[SalaryRange]:
    results = []
    for m in _SINGLE_DOLLAR_PERIOD_RE.finditer(text):
        val = _parse_number(m.group(1))
        period = _detect_period(m.group(2))
        if period is None:
            continue
        if period == "yearly" and val < 15000:
            continue
        if period == "hourly" and (val < 7 or val > 500):
            continue
        results.append(
            SalaryRange(
                min=int(val * 100) if period == "hourly" else int(val),
                max=None,
                currency="USD",
                period=period,
            )
        )
    return results


# ── Pattern 5: EUR/month salary lines ────────────────────────────────
#   "Salary: From 1800 EUR/month"
#   "EUR 3850 gross per month"
#   "€47000"  → only with explicit salary/gehalt context
#   "17.41€/hour"

_EUR_SALARY_RE = re.compile(
    r"(?:"
    # "Salary: From/Starting from XXXX EUR"
    r"(?:salary|gehalt|salaire|stipendio|salario|salaris|lön|løn|"
    r"wynagrodzenie|plat|mzda|vergütung|retribuzione)\s*:?\s*"
    r"(?:from|ab|starting from|à partir de|von|mindestens|da|vanaf)?\s*"
    r"([\d,. ]+)\s*(?:EUR|€)"
    r"|"
    # "EUR XXXX" with salary context nearby
    r"(?:EUR|€)\s*([\d,. ]+)"
    r"|"
    # "XXXX€" with salary context
    r"([\d,. ]+)\s*€"
    r")",
    re.IGNORECASE,
)

_EUR_CONTEXT_RE = re.compile(
    # salary words across European languages
    r"salary|gehalt|salaire|stipendio|salario|salaris|lön|løn|"
    r"wynagrodzenie|plat|mzda|fizetés|palk|"
    r"remuneration|rémunération|vergütung|retribuzione|"
    # gross/net indicators
    r"gross|brutto|brut|lordo|netto|net\b|"
    # period indicators
    r"per month|monatlich|mensuel|mensile|monthly|/month|"
    r"per year|jährlich|annuel|annuale|annually|yearly|/year|"
    r"per hour|hourly|/hour|stündlich|/hr",
    re.IGNORECASE,
)


def _extract_eur(text: str) -> list[SalaryRange]:
    results = []
    for m in _EUR_SALARY_RE.finditer(text):
        raw = m.group(1) or m.group(2) or m.group(3)
        if not raw:
            continue
        raw = raw.strip()
        if not raw or not any(c.isdigit() for c in raw):
            continue

        # Check for salary context in surrounding text
        start = max(0, m.start() - 150)
        end = min(len(text), m.end() + 100)
        surrounding = text[start:end]

        if not _EUR_CONTEXT_RE.search(surrounding):
            continue

        # Disqualify benefit/perk line items (not primary salary)
        disqualify = re.search(
            r"transport.{0,10}(compensation|allowance|Zuschuss)|"
            r"referral.{0,10}(bonus|reward|program)|"
            r"recommend.{0,10}(reward|bonus)|"
            r"empfehlung|"
            r"newborn.{0,10}bonus|"
            r"child.{0,10}(bonus|benefit|allowance)|"
            r"compensation for .{0,20}(tennis|gym|language|sport)|"
            r"Zuschuss|"
            r"commuting.{0,10}allowance",
            surrounding,
            re.IGNORECASE,
        )
        if disqualify:
            continue

        try:
            # Handle European number format: 39.243 means 39243, 39.243,00 means 39243
            cleaned = raw.replace(" ", "")
            # European format: dots as thousand separators
            if re.match(r"^\d{1,3}\.\d{3}(,\d+)?$", cleaned):
                cleaned = cleaned.replace(".", "").replace(",", ".")
            else:
                cleaned = cleaned.replace(",", "")
            val = float(cleaned)
        except ValueError:
            continue

        if val < 800:  # too small for any salary (filters out benefit amounts)
            continue

        # Detect period from context
        period = None
        period_match = re.search(
            r"per hour|hourly|/hour|/hr|stündlich|"
            r"per month|monthly|/month|monatlich|mensuel|"
            r"per year|annually|yearly|/year|jährlich|annuel|"
            r"p\.a\.",
            surrounding,
            re.IGNORECASE,
        )
        if period_match:
            period = _detect_period(period_match.group(0))
        if period is None:
            # Heuristic: < 10000 → likely monthly; >= 10000 → likely yearly
            period = "monthly" if val < 10000 else "yearly"

        if period == "hourly" and (val < 5 or val > 200):
            continue
        if period == "monthly" and (val < 500 or val > 30000):
            continue
        if period == "yearly" and (val < 10000 or val > 500000):
            continue

        results.append(
            SalaryRange(
                min=int(val * 100) if period == "hourly" else int(val),
                max=None,
                currency="EUR",
                period=period,
            )
        )
    return results


# ── Pattern 6: GBP ──────────────────────────────────────────────────

_GBP_RANGE_RE = re.compile(
    r"£([\d,]+(?:\.\d+)?)\s*[-–—]\s*£?([\d,]+(?:\.\d+)?)"
    r"(\s*.{0,50})",
)

_GBP_SINGLE_RE = re.compile(
    r"£([\d,]+(?:\.\d+)?)\s*(per hour|hourly|per year|annually|/hr|/hour|/year)"
)


def _extract_gbp(text: str) -> list[SalaryRange]:
    results = []

    for m in _GBP_RANGE_RE.finditer(text):
        lo = _parse_number(m.group(1))
        hi = _parse_number(m.group(2))
        trailing = m.group(3)

        if _NOT_SALARY_RE.search(trailing):
            continue

        # Require salary-confirming context
        start = max(0, m.start() - 100)
        surrounding = text[start : m.end()] + trailing
        if not re.search(
            r"salary|pay|compensation|per year|annually|per hour|hourly|dependent on",
            surrounding,
            re.IGNORECASE,
        ):
            continue

        if lo < 10000 or hi > 500000:
            # Could be hourly
            if lo < 7 or lo > 200:
                continue
            period = "hourly"
            lo_val = int(lo * 100)
            hi_val = int(hi * 100)
        else:
            period = "yearly"
            lo_val = int(lo)
            hi_val = int(hi)

        results.append(SalaryRange(min=lo_val, max=hi_val, currency="GBP", period=period))

    for m in _GBP_SINGLE_RE.finditer(text):
        val = _parse_number(m.group(1))
        period = _detect_period(m.group(2))
        if period is None:
            continue
        if period == "hourly" and (val < 5 or val > 200):
            continue
        results.append(
            SalaryRange(
                min=int(val * 100) if period == "hourly" else int(val),
                max=None,
                currency="GBP",
                period=period,
            )
        )

    return results


# ── Pattern 7: CHF (Swiss franc) ─────────────────────────────────────
#   "CHF 120'000 - 150'000"  (apostrophe thousands)
#   "CHF 8'500 pro Monat"

_CHF_RE = re.compile(
    r"CHF\s*([\d]['''\d.,\s]*\d)"  # min amount (must start and end with digit)
    r"(?:\s*[-–—]\s*([\d]['''\d.,\s]*\d))?"  # optional max
    r"(\s*.{0,80})",
    re.IGNORECASE,
)

_CHF_CONTEXT_RE = re.compile(
    r"salary|gehalt|salaire|stipendio|lohn|salaris|vergütung|"
    r"gross|brutto|brut|"
    r"per month|monatlich|pro monat|monthly|/month|"
    r"per year|jährlich|pro jahr|annually|yearly|/year|"
    r"per hour|pro stunde|hourly|/hour|stündlich",
    re.IGNORECASE,
)


def _extract_chf(text: str) -> list[SalaryRange]:
    results = []
    for m in _CHF_RE.finditer(text):
        raw_lo = m.group(1).strip()
        raw_hi = m.group(2)
        m.group(3)

        if not any(c.isdigit() for c in raw_lo):
            continue

        start = max(0, m.start() - 150)
        end = min(len(text), m.end() + 100)
        surrounding = text[start:end]

        if not _CHF_CONTEXT_RE.search(surrounding):
            continue

        lo = _parse_number(raw_lo)
        hi = _parse_number(raw_hi) if raw_hi else None

        # Detect period
        period = None
        period_match = re.search(
            r"pro stunde|per hour|hourly|stündlich|/hour|/hr|"
            r"pro monat|per month|monthly|monatlich|/month|"
            r"pro jahr|per year|annually|yearly|jährlich|/year",
            surrounding,
            re.IGNORECASE,
        )
        if period_match:
            period = _detect_period(
                period_match.group(0)
                .replace("pro monat", "per month")
                .replace("pro jahr", "per year")
                .replace("pro stunde", "per hour")
            )
        if period is None:
            if lo < 500:
                period = "hourly"
            elif lo < 15000:
                period = "monthly"
            else:
                period = "yearly"

        if period == "hourly" and (lo < 15 or lo > 300):
            continue
        if period == "monthly" and (lo < 2000 or lo > 30000):
            continue
        if period == "yearly" and (lo < 30000 or (lo > 500000)):
            continue

        results.append(
            SalaryRange(
                min=int(lo * 100) if period == "hourly" else int(lo),
                max=(int(hi * 100) if period == "hourly" else int(hi)) if hi else None,
                currency="CHF",
                period=period,
            )
        )
    return results


# ── Public API ───────────────────────────────────────────────────────


def extract_salary(html: str) -> list[SalaryRange]:
    """Extract salary ranges from job description HTML.

    Returns all high-confidence salary ranges found.  An empty list means
    no salary information could be reliably identified.
    """
    text = _html_to_text(html)

    # Try patterns in order of specificity (most structured first)
    results = _extract_location_prefixed(text)
    if results:
        return results

    results = _extract_bare_range(text)
    if results:
        return results

    # Dollar patterns
    dollar_ranges = _extract_dollar_range(text)
    dollar_singles = _extract_single_dollar(text)

    # EUR patterns
    eur = _extract_eur(text)

    # GBP patterns
    gbp = _extract_gbp(text)

    # CHF patterns
    chf = _extract_chf(text)

    all_results = dollar_ranges + dollar_singles + eur + gbp + chf

    # Deduplicate: if we have both a range and a single that overlaps, prefer the range
    if len(all_results) > 1:
        ranges = [r for r in all_results if r.max is not None]
        singles = [r for r in all_results if r.max is None]
        # Keep singles only if their value isn't the min or max of an existing range
        range_vals = set()
        for r in ranges:
            range_vals.add(r.min)
            if r.max is not None:
                range_vals.add(r.max)
        filtered_singles = [s for s in singles if s.min not in range_vals]
        all_results = ranges + filtered_singles

    return all_results


def extract_salary_unified(html: str) -> SalaryRange | None:
    """Extract a single best salary range from HTML.

    When multiple ranges exist (e.g. per-location), returns the widest
    range (lowest min, highest max) to represent the overall band.
    """
    ranges = extract_salary(html)
    if not ranges:
        return None

    # Group by (currency, period)
    by_key: dict[tuple[str, str], list[SalaryRange]] = {}
    for r in ranges:
        key = (r.currency, r.period)
        by_key.setdefault(key, []).append(r)

    # Pick the group with the most entries (likely the primary salary)
    best_group = max(by_key.values(), key=len)

    lo = min(r.min for r in best_group)
    hi_candidates = [r.max for r in best_group if r.max is not None]
    hi = max(hi_candidates) if hi_candidates else None

    return SalaryRange(
        min=lo,
        max=hi,
        currency=best_group[0].currency,
        period=best_group[0].period,
    )


_PERIOD_TO_UNIT = {"yearly": "year", "monthly": "month", "hourly": "hour"}


def parse_salary_text(text: str) -> dict | None:
    """Parse a salary string into a ``base_salary`` dict.

    Accepts any text containing salary information (plain text or HTML):
      ``"$136,800 - $273,600 annually"``
      ``"€50.000 - €70.000 per year"``
      ``"$30/hour"``

    Returns ``{"currency": "USD", "min": 136800, "max": 273600, "unit": "year"}``
    or ``None`` if no salary is found.

    This is a thin wrapper around :func:`extract_salary_unified` that
    converts the internal ``SalaryRange`` to the standard ``base_salary``
    dict used by scrapers and monitors.
    """
    sr = extract_salary_unified(text)
    if sr is None:
        return None
    sal_min = sr.min
    sal_max = sr.max
    # Hourly values are stored in cents internally — convert back
    if sr.period == "hourly":
        sal_min = round(sr.min / 100, 2)
        if sal_max is not None:
            sal_max = round(sr.max / 100, 2)
    return {
        "currency": sr.currency,
        "min": sal_min,
        "max": sal_max,
        "unit": _PERIOD_TO_UNIT.get(sr.period, sr.period),
    }
