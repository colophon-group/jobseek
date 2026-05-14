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

Currency = str  # ISO 4217: USD, CAD, EUR, GBP, CHF, PLN, CZK, SEK, DKK, HUF, RON, BGN
# HRK intentionally omitted — Croatia adopted EUR 2023-01; recon (#3263) confirmed
# zero HRK literals across 352 active Croatian postings. Revisit only on pre-2023 backfill.


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
    # Polish
    "rocznie": "yearly",
    "miesięcznie": "monthly",
    "miesiecznie": "monthly",  # diacritic-free fallback
    "mies.": "monthly",
    "/mies": "monthly",
    "na godzinę": "hourly",
    # Czech
    "ročně": "yearly",
    "rocne": "yearly",
    "měsíčně": "monthly",
    "mesicne": "monthly",
    "měs.": "monthly",
    "/měs": "monthly",
    "na hodinu": "hourly",
    "/hod": "hourly",
    # Swedish
    "årligen": "yearly",
    "per år": "yearly",
    "per månad": "monthly",
    "kr/månad": "monthly",
    "per timme": "hourly",
    # Danish
    "årligt": "yearly",
    "pr. år": "yearly",
    "månedlig": "monthly",
    "pr. måned": "monthly",
    "pr. time": "hourly",
    # Hungarian
    "évente": "yearly",
    "évi": "yearly",
    "/év": "yearly",
    "havi": "monthly",
    "havonta": "monthly",
    "/hó": "monthly",
    "óránként": "hourly",
    "/óra": "hourly",
    # Romanian
    "anual": "yearly",
    "pe an": "yearly",
    "/an": "yearly",
    "lunar": "monthly",
    "pe lună": "monthly",
    "pe luna": "monthly",
    "/lună": "monthly",
    "pe oră": "hourly",
    "/oră": "hourly",
    # Bulgarian
    "годишно": "yearly",
    "на година": "yearly",
    "месечно": "monthly",
    "на месец": "monthly",
    "на час": "hourly",
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


# ── Patterns 8-14: Non-Eurozone EU currencies ────────────────────────
#
# Adds: PLN, CZK, SEK, DKK, HUF, RON, BGN. Recon catalog: #3263 comment.
# HRK skipped (Croatia adopted EUR 2023-01, zero literals in our crawl).
#
# Design notes:
#  * All seven share enough structural similarity (number → ISO/symbol with
#    optional range, or ISO/symbol → number) that we go through a single
#    parameterised helper `_extract_eu_currency` rather than seven copy-pastes.
#  * Number-parsing must handle four locale variants seen in production:
#      - English: 1,234.56  (comma=thousands, dot=decimal)
#      - German/Danish/Swedish "dot-locale": 1.234,56  (dot=thousands, comma=decimal)
#      - Polish/Hungarian space-locale: 1 234,56  (space=thousands, comma=decimal)
#      - Bare digits: 5172
#  * Period detection is context-window based — we look 200 chars around the
#    match for the native period word (`miesięcznie`, `měsíčně`, etc.) or
#    the English equivalent, falling back to a per-currency magnitude
#    heuristic only when context is silent.
#  * Brutto/netto: when "net" is asserted *without* a "gross" marker in the
#    surrounding window, we skip the extraction entirely (per #3264 brief —
#    don't gross-up here, defer to a follow-up).
#  * Per-currency disqualify lists cover the highest-frequency perks (meal
#    vouchers, cafeteria budgets, Multisport, L&D budget, transport allowance)
#    and revenue/turnover prose (SEK/DKK billion-dollar boilerplate). These
#    are the single biggest source of false positives in the recon sample.


def _parse_eu_number(raw: str) -> float | None:
    """Parse a number string that may use any of: 1,234.56 / 1.234,56 / 1 234,56 / 5172.

    Returns None when the string is empty, not numeric, or has an ambiguous
    shape we'd rather decline than guess on.
    """
    if not raw:
        return None
    s = raw.strip()
    # Strip a trailing decimal-zero idiom "110,- Kč" → "110"
    s = re.sub(r",\s*-+$", "", s)
    if not s or not any(c.isdigit() for c in s):
        return None

    # Trailing sentence period defence (matches _parse_number)
    s = s.rstrip(".")

    # Normalise non-breaking & thin spaces to plain space
    s = s.replace(" ", " ").replace(" ", " ").replace(" ", " ")

    has_comma = "," in s
    has_dot = "." in s
    has_space = " " in s

    # Strip apostrophes (Swiss style, occasional in our data) for safety
    s = s.replace("'", "").replace("’", "")

    try:
        if has_comma and has_dot:
            # Two separators present — the last one is decimal.
            if s.rfind(",") > s.rfind("."):
                # 1.234,56 → "1234.56"
                s = s.replace(".", "").replace(",", ".")
            else:
                # 1,234.56 → "1234.56"
                s = s.replace(",", "")
        elif has_comma and not has_dot:
            # 1,234 (Eng thousands) vs 1234,56 (EU decimal).
            # If exactly one comma and 3 digits follow → thousands; else decimal.
            after = s.split(",")[-1]
            if len(after) == 3 and s.count(",") >= 1 and not has_space:
                s = s.replace(",", "")
            else:
                s = s.replace(",", ".")
        elif has_dot and not has_comma:
            # 1234.56 (Eng decimal) vs 1.234 (EU thousands) vs 1.234.567 (EU multi-thousands).
            after = s.split(".")[-1]
            dot_count = s.count(".")
            # Multiple dots → unambiguously thousands separators.
            # Single dot + 3-digit tail + >3 digits total → thousands (e.g. "1.234").
            # Single dot + non-3-digit tail → decimal (e.g. "12.34").
            if dot_count > 1:
                s = s.replace(".", "")
            elif len(after) == 3 and dot_count == 1 and len(s.replace(".", "")) > 3:
                # 1.234 or 12.345 → thousands. 12.34 is decimal.
                s = s.replace(".", "")
            # else: leave as decimal
        if has_space:
            s = s.replace(" ", "")
        return float(s)
    except ValueError:
        return None


# Native period markers per currency family, matched in a context window.
_EU_PERIOD_RE = re.compile(
    r"per\s+(?:hour|month|year)|hourly|monthly|yearly|annually|annual|/(?:hour|hr|month|mo|year|yr)|"
    r"p\.a\.|"
    # Polish
    r"miesięcznie|miesiecznie|miesiąc|miesiac|mies\.|/mies|rocznie|rok|na\s+godzinę|"
    # Czech
    r"měsíčně|mesicne|měs\.|/měs|ročně|rocne|/hod|na\s+hodinu|"
    # Swedish/Danish
    r"per\s+(?:år|månad|timme)|kr/(?:år|månad|timme)|årligen|årligt|månedlig|pr\.\s*(?:år|måned|time)|"
    # Hungarian
    r"havonta|havi|/hó|évente|évi|/év|óránként|/óra|"
    # Romanian
    r"pe\s+lună|lunar|pe\s+an|anual|pe\s+oră|/(?:lună|oră|an)|"
    # Bulgarian
    r"месечно|годишно|на\s+(?:час|месец|година)",
    re.IGNORECASE,
)

# Brutto markers — at least one of these in the window is sufficient to
# accept the extraction even when an explicit "net" word also appears.
_EU_GROSS_RE = re.compile(
    r"\b(?:gross|brut|brutto|bruttó|bruttoló|hrubého|"
    r"bruttoløn|bruttolön|"
    # Bulgarian Cyrillic
    r"бруто)\b",
    re.IGNORECASE,
)

# Net markers — if any of these are present in the window *and* no gross marker
# is, we skip the extraction. We DO NOT gross-up — that's a separate follow-up.
_EU_NET_RE = re.compile(
    r"\b(?:net|netto|nettó|čistého|"
    r"nettoløn|nettolön|"
    r"нето)\b",
    re.IGNORECASE,
)

# Perk / non-salary phrases shared across currencies. Matched against the
# context window (300 chars around the number).
_EU_PERK_RE = re.compile(
    # Vouchers, cards, budgets, allowances
    r"voucher|vouchere|stravenk|stravné|cafeteria|multisport|edenred|sodexo|"
    r"allowance|appartement|wellness|wellbeing|"
    r"l&d\s+budget|learning\s+budget|personal\s+budget|annual\s+budget|"
    r"food\s+ticket|meal\s+ticket|meal\s+voucher|meal\s+allowance|"
    r"tichete\s+de\s+masă|bonuri\s+de\s+masa|"
    r"transport\s+allowance|decont|"
    # Hungarian, Czech meal/cafeteria
    r"étkezési|szép\s+kártya|příspěvek|příspěvky|"
    # Polish allowance/perk words
    r"dodatek|dodatków|"
    # Romanian allowance/perk words
    r"diurnă|deplasare|"
    # Bulgarian Cyrillic perk words
    r"бонус|ваучер|"
    # Generic English perks/discount
    r"referral\s+(?:bonus|reward|fee|program)|"
    r"sign[-\s]?on\s+bonus|"
    r"gift\s+card",
    re.IGNORECASE,
)

# Disqualifiers for corporate-revenue prose — most damaging for SEK/DKK,
# which the recon flagged ("SEK 134 billion", "DKK 130 billion").
_EU_REVENUE_RE = re.compile(
    r"\b(?:revenue|turnover|sales\s+of|"
    r"billion|bn\b|million|mn\b|"
    # Danish billion = "milliarder", abbrev "mia."
    r"milliarder|mia\.?|"
    # Swedish billion = "miljarder"
    r"miljarder|mdr\.?|"
    # Polish billion = "miliardów", Czech = "miliard", Hungarian = "milliárd",
    # Romanian = "miliarde", Bulgarian = "милиарда"
    r"miliard|miliardów|miliárd|miliarde|milliárd|милиард)",
    re.IGNORECASE,
)


# Currency descriptor table.
#
# Fields:
#   iso        — ISO 4217 code.
#   symbols    — extra spellings that anchor the regex (e.g. zł, Kč, Ft, лв).
#                Listed in regex form, exact case unless `iso_ci` is True.
#   word_break — `True` to require a leading word boundary for the suffix
#                form (e.g. avoid "HUFFMAN" matching "HUF").
#   range_min  — typed lower bound for an annual gross salary in this currency
#                (filters tiny perk amounts that slipped past the perk regex).
#   range_max  — typed upper bound.
#   monthly_min/max — same, for monthly salaries.
#   hourly_min/max — same, for hourly rates (used only when explicit).
#
# Magnitudes are calibrated against the recon TL;DR — e.g. PLN monthly is
# typically 4k-30k zł, annual 40k-400k. Hungarian numbers are 400× higher
# than EUR equivalents so HUF needs its own thresholds.

_EU_CURRENCIES: dict[str, dict] = {
    "PLN": {
        "iso": "PLN",
        # zł is the most common, ZŁ uppercase appears in some ATS templates.
        "symbols": [r"zł", r"ZŁ", r"PLN"],
        "range_min": 30_000,
        "range_max": 1_500_000,
        "monthly_min": 2_000,
        "monthly_max": 150_000,
        "hourly_min": 15,
        "hourly_max": 1_000,
    },
    "CZK": {
        "iso": "CZK",
        "symbols": [r"Kč", r"CZK"],
        "range_min": 200_000,
        "range_max": 5_000_000,
        "monthly_min": 15_000,
        "monthly_max": 500_000,
        "hourly_min": 80,
        "hourly_max": 3_000,
    },
    "SEK": {
        "iso": "SEK",
        # `kr` is ambiguous between NOK/SEK/DKK — recon (#3263) says SEK regex
        # requires explicit `SEK` or `kronor`; bare `kr` is too noisy.
        "symbols": [r"SEK", r"kronor"],
        "range_min": 200_000,
        "range_max": 5_000_000,
        "monthly_min": 15_000,
        "monthly_max": 500_000,
        "hourly_min": 80,
        "hourly_max": 3_000,
    },
    "DKK": {
        "iso": "DKK",
        # Recon: bare `kr.` is ambiguous with NOK/SEK/ISK; require explicit DKK.
        "symbols": [r"DKK"],
        "range_min": 200_000,
        "range_max": 5_000_000,
        "monthly_min": 15_000,
        "monthly_max": 500_000,
        "hourly_min": 80,
        "hourly_max": 3_000,
    },
    "HUF": {
        "iso": "HUF",
        # Ft must be word-anchored to avoid "Ft. Walton Beach" / "HUFFMAN" hits.
        "symbols": [r"HUF", r"Ft"],
        "word_break": True,
        # HUF salaries are large (~400× EUR for the same purchasing power).
        "range_min": 1_500_000,
        "range_max": 200_000_000,
        "monthly_min": 200_000,
        "monthly_max": 20_000_000,
        "hourly_min": 1_500,
        "hourly_max": 50_000,
    },
    "RON": {
        "iso": "RON",
        # `lei` must avoid "leisure", "Israeli" prose, etc — require a digit
        # left-neighbour and a strong salary/period context word.
        "symbols": [r"RON", r"lei"],
        "word_break": True,
        "range_min": 24_000,  # ~RON 2000/month × 12
        "range_max": 1_000_000,
        "monthly_min": 2_000,
        "monthly_max": 100_000,
        "hourly_min": 10,
        "hourly_max": 500,
    },
    "BGN": {
        "iso": "BGN",
        # лв is the Cyrillic short form. The recon noted near-zero primary-salary
        # hits — most BGN matches are perks — but we still ship the regex.
        "symbols": [r"BGN", r"лв\.?"],
        "range_min": 12_000,  # ~BGN 1000/month × 12 — Bulgarian minimum wage neighbourhood
        "range_max": 400_000,
        "monthly_min": 1_000,
        "monthly_max": 40_000,
        "hourly_min": 5,
        "hourly_max": 200,
    },
}


# Salary-confirming context words (incl. native EU words).
# A match in the context window is required for every emission — this is the
# precision-skewed lever that gates against perks/prose.
_EU_SALARY_CONTEXT_RE = re.compile(
    r"salary|salaire|salario|salariu|płaca|wynagrodzenie|wynagrodzeni|plat|"
    r"mzda|mzd[aу]|fizetés|lön|løn|заплата|"
    r"compensation|base\s+pay|pay\s+range|pay:|pay\b|"
    # German salary words (Gehalt/Vergütung — for ATS templates in Polish/Czech mixed locales)
    r"gehalt|vergütung|"
    # Period markers count as context too
    r"gross|net\b|brut|brutto|bruttó|hrubého|hrub[éy]|nett|netto|nettó|čistého|"
    r"per\s+(?:hour|month|year)|hourly|monthly|yearly|annually|annual|"
    r"miesięcznie|miesiecznie|rocznie|měsíčně|ročně|"
    r"havi|havonta|évi|évente|lunar|anual|месечно|годишно|"
    r"per\s+(?:år|månad)|kr/(?:år|månad)|årligen|årligt|månedlig",
    re.IGNORECASE,
)


def _detect_period_in_window(window: str) -> str | None:
    """Look for a native or English period marker in a context window."""
    m = _EU_PERIOD_RE.search(window)
    if not m:
        return None
    raw = m.group(0).lower().strip()
    # Map raw multilingual matches to the canonical period.
    hourly_tokens = (
        "hour",
        "hr",
        "hod",
        "godzin",
        "óra",
        "óránk",
        "oră",
        "timme",
        "stunde",
        "час",
        "/h",
    )
    monthly_tokens = (
        "month",
        "mo",
        "mies",
        "měs",
        "havi",
        "havonta",
        "/hó",
        "lună",
        "luna",
        "lunar",
        "månad",
        "måned",
        "месечно",
    )
    yearly_tokens = (
        "year",
        "yr",
        "annual",
        "annually",
        "p.a.",
        "rok",
        "rocz",
        "ročn",
        "/év",
        "évi",
        "éven",
        "/an",
        "anual",
        "år",
        "годишно",
    )
    if any(t in raw for t in hourly_tokens):
        return "hourly"
    if any(t in raw for t in monthly_tokens):
        return "monthly"
    if any(t in raw for t in yearly_tokens):
        return "yearly"
    return None


def _build_eu_currency_regex(symbols: list[str], word_break: bool) -> re.Pattern[str]:
    """Build a precision regex for `(symbol|iso)`-anchored numbers.

    Matches three shapes:
      1.  `<symbol> <num>` (prefix)        e.g. "PLN 14 600", "CZK 34 000"
      2.  `<num> <symbol>` (suffix)        e.g. "5 000 zł", "70 000 Kč"
      3.  `<num> - <num> <symbol>` (range; symbol may appear before or after each endpoint)
    """
    sym = "(?:" + "|".join(symbols) + ")"
    # Number token: at least 2 digits to avoid "5 lei" sub-amounts; allows
    # space/dot/comma thousands and an optional decimal tail.
    # We keep this deliberately loose; _parse_eu_number does the heavy lifting.
    num = r"\d{1,3}(?:[  .,]\d{3})*(?:[.,]\d+)?|\d{2,}(?:[.,]\d+)?"
    # Suffix form needs a non-letter left neighbour so we don't pick up "PLN"
    # in "ERPLN" or "HUF" in "HUFFMAN".
    left_guard = r"(?<![A-Za-zÀ-ž])" if word_break else r"(?<![A-Za-z0-9])"
    # Right guard — same idea on the symbol side; HUF/Ft especially.
    right_guard = r"(?![A-Za-zÀ-ž])"
    range_sep = r"(?:-|–|—|to|do|til|à|–|—)"
    pat = (
        r"(?:"
        # Prefix shape:   PLN 14,000 - PLN 20,000   |   PLN 14,000 - 20,000
        rf"{left_guard}{sym}\s*({num})"
        rf"(?:\s*{range_sep}\s*(?:{sym}\s*)?({num}))?{right_guard}"
        r"|"
        # Double-suffix range:   14,000 zł - 20,000 zł
        rf"{left_guard}({num})\s*{sym}\s*{range_sep}\s*({num})\s*{sym}{right_guard}"
        r"|"
        # Single-trailing-suffix range:   14,000 - 20,000 zł
        rf"{left_guard}({num})\s*{range_sep}\s*({num})\s*{sym}{right_guard}"
        r"|"
        # Single-suffix:  14,000 zł
        rf"{left_guard}({num})\s*{sym}{right_guard}"
        r")"
    )
    return re.compile(pat, re.IGNORECASE)


# Compile per-currency regexes once at import time.
_EU_RES: dict[str, re.Pattern[str]] = {
    code: _build_eu_currency_regex(spec["symbols"], spec.get("word_break", False))
    for code, spec in _EU_CURRENCIES.items()
}


def _extract_eu_currency(text: str, code: str) -> list[SalaryRange]:
    """Extract salary ranges for one non-Eurozone EU currency.

    Precision rules (all must hold for an emission):
      1.  At least one salary/period context word in a ±200-char window.
      2.  No perk word (voucher / Multisport / cafeteria budget / allowance).
      3.  No revenue/turnover word (billion / mia. / miljarder / miliard*).
      4.  If a net marker is present *and* no gross marker, skip.
      5.  Parsed value falls within the per-currency magnitude window for the
          detected (or inferred) period.
    """
    spec = _EU_CURRENCIES[code]
    pat = _EU_RES[code]
    results: list[SalaryRange] = []

    for m in pat.finditer(text):
        # Capture groups follow regex alternative order:
        #   (prefix_lo, prefix_hi,
        #    double_suffix_lo, double_suffix_hi,
        #    single_trail_lo, single_trail_hi,
        #    suffix_single)
        g = m.groups()
        prefix_lo, prefix_hi = g[0], g[1]
        dbl_lo, dbl_hi = g[2], g[3]
        sgl_lo, sgl_hi = g[4], g[5]
        suf_one = g[6]
        if prefix_lo:
            raw_lo, raw_hi = prefix_lo, prefix_hi
        elif dbl_lo:
            raw_lo, raw_hi = dbl_lo, dbl_hi
        elif sgl_lo:
            raw_lo, raw_hi = sgl_lo, sgl_hi
        else:
            raw_lo, raw_hi = suf_one, None

        if not raw_lo:
            continue

        lo = _parse_eu_number(raw_lo)
        hi = _parse_eu_number(raw_hi) if raw_hi else None
        if lo is None or (raw_hi and hi is None):
            continue

        # Context window for precision gating.
        start = max(0, m.start() - 200)
        end = min(len(text), m.end() + 200)
        window = text[start:end]

        # 1. Salary context word required.
        if not _EU_SALARY_CONTEXT_RE.search(window):
            continue

        # 2. Perk disqualifier.
        if _EU_PERK_RE.search(window):
            continue

        # 3. Revenue/turnover disqualifier (most relevant for SEK/DKK).
        if _EU_REVENUE_RE.search(window):
            continue

        # 4. Net-only → skip (#3264 brief: no gross-up here).
        if _EU_NET_RE.search(window) and not _EU_GROSS_RE.search(window):
            continue

        # 5. Period detection.
        period = _detect_period_in_window(window)
        if period is None:
            # Heuristic: pick period from magnitude when context is silent.
            # Compare against per-currency thresholds.
            if lo >= spec["range_min"]:
                period = "yearly"
            elif lo >= spec["monthly_min"]:
                period = "monthly"
            else:
                # Below monthly_min — too small for a primary salary; bail.
                continue

        # Magnitude sanity per period.
        if period == "yearly":
            if lo < spec["range_min"] or lo > spec["range_max"]:
                continue
            if hi is not None and (hi < lo or hi > spec["range_max"] * 1.2):
                continue
        elif period == "monthly":
            if lo < spec["monthly_min"] or lo > spec["monthly_max"]:
                continue
            if hi is not None and (hi < lo or hi > spec["monthly_max"] * 1.2):
                continue
        elif period == "hourly":
            if lo < spec["hourly_min"] or lo > spec["hourly_max"]:
                continue
            if hi is not None and (hi < lo or hi > spec["hourly_max"] * 1.2):
                continue

        # Hourly is stored as cents internally (same convention as USD/EUR/CHF).
        scale = 100 if period == "hourly" else 1
        results.append(
            SalaryRange(
                min=int(lo * scale),
                max=(int(hi * scale) if hi is not None else None),
                currency=code,
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

    # Non-Eurozone EU currencies (PLN/CZK/SEK/DKK/HUF/RON/BGN)
    eu_extra: list[SalaryRange] = []
    for code in _EU_CURRENCIES:
        eu_extra.extend(_extract_eu_currency(text, code))

    all_results = dollar_ranges + dollar_singles + eur + gbp + chf + eu_extra

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
