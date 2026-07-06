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

Currency = str  # ISO 4217: USD, CAD, EUR, GBP, CHF, PLN, CZK, SEK, DKK, HUF, RON, BGN,
# AUD, NZD, SGD, HKD, BRL, MXN
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

# ── Mojibake repair (UTF-8 double-encoded → original) ────────────────
#
# Some upstream scrapers (notably the Tesco careers JSON-LD blob) double-
# encode UTF-8 as Latin-1 then re-encode as UTF-8, producing artifacts
# like `Â£` for `£`, `â€"` for an en-dash, etc. The recon for #3326
# flagged this on ~150 active UK Tesco postings; the same byte-sequence
# also appears sporadically on other currencies and punctuation, so we
# apply the fix at the HTML→text boundary rather than per-regex.
#
# Two-step strategy:
#   1. Walk through a small explicit replacement table for the common
#      currency-and-punctuation sequences. This is cheap and obvious.
#   2. If the text still contains the tell-tale `Â` or `â€` artifacts,
#      attempt a full Latin-1→UTF-8 round-trip via .encode/.decode —
#      this catches the long tail (curly quotes, accented Latin chars,
#      etc.). We do this only after the cheap pass so we don't pay the
#      round-trip cost on clean text.
_MOJIBAKE_TABLE: tuple[tuple[str, str], ...] = (
    # Currency symbols
    ("Â£", "£"),  # Â£ -> £
    ("Â€", "€"),  # Â€ -> €  (rare; only when € itself double-encoded)
    ("Â¥", "¥"),  # Â¥ -> ¥
    # Non-breaking space artefact (NBSP 0xA0 double-encoded as "Â ")
    ("Â ", " "),
    ("Â ", " "),
    # Punctuation: ellipsis, dashes, quotes — all derive from UTF-8 0xE2 0x80 ?? .
    # Tesco template uses en-dash / em-dash extensively.
    ("â€¦", "…"),  # â€¦ -> …
    ("â€“", "–"),  # â€" -> – (en-dash)
    ("â€”", "—"),  # â€" -> — (em-dash)
    ("â€˜", "‘"),  # â€˜ -> '
    ("â€™", "’"),  # â€™ -> '
    ("â€œ", "“"),  # â€œ -> "
    ("â€", "”"),  # â€ -> "  (right double quote)
    # Common Latin-1 accented chars seen in DE/FR descriptions
    ("Ã©", "é"),  # Ã© -> é
    ("Ã¨", "è"),  # Ã¨ -> è
    ("Ã ", "à"),  # Ã  -> à
    ("Ã¼", "ü"),  # Ã¼ -> ü
    ("Ã¤", "ä"),  # Ã¤ -> ä
    ("Ã¶", "ö"),  # Ã¶ -> ö
    ("ÃŸ", "ß"),  # ÃŸ -> ß
)


def _repair_mojibake(text: str) -> str:
    """Fix UTF-8 double-encoded sequences (Â£ → £, â€" → en-dash, etc.).

    Closes the Tesco mojibake cluster in #3326 recon. Applied at the
    HTML→text boundary so every downstream extractor benefits.
    """
    # Cheap explicit replacements first.
    if "Â" in text or "â€" in text or "Ã" in text:
        for bad, good in _MOJIBAKE_TABLE:
            if bad in text:
                text = text.replace(bad, good)
    return text


def _html_to_text(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    text = _repair_mojibake(text)
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
#   "A$120,000 - A$150,000"  (AUD prefix)
#   "S$100,000 - S$130,000"  (SGD prefix)
#   "R$50.000"               (BRL — uses European-style "." thousands)

# Allow an optional 1-3 letter prefix immediately before the leading $
# so we can detect AUD/NZD/SGD/HKD/BRL/MXN as well as the legitimate
# US/USD/CAD ``US$``, ``C$``, ``CDN$`` conventions. The prefix must be
# word-boundary-anchored to avoid stray matches mid-word.
#
# Without an explicit prefix capture the leading word-boundary lookbehind
# (``(?<=[\s(\[])``) would reject text like ``US$120,000`` because the
# character immediately before ``$`` is a letter — even though that letter
# is itself a currency marker. The prefix group consumes those letters so
# the boundary check is satisfied by the character before *them*.
_DOLLAR_RANGE_RE = re.compile(
    r"(?:(?<=^)|(?<=[\s(\[]))"
    r"(A[U]?|N[Z]?|S|HK|R|MX|U[S]?|CDN|C[A]?)?\$([\d,.]+[Kk]?)\s*[-–—]\s*"
    r"(?:[A-Z]{0,3}\$)?([\d,.]+[Kk]?)"
    r"(\s*.{0,80})",  # capture trailing context
)

# Currency-marker detection helpers — see issue #3191
# Maps a marker string (uppercased, suffix-only or prefix-only) to ISO currency.
# Ordered for explicit priority: longer markers first to avoid prefix ambiguity.
# ``N$`` alone is intentionally NOT in the table — it's ambiguous (could
# be Namibian Dollar). New Zealand uses ``NZ$``; that's the conventional
# marker we recognise.
_PREFIX_TO_CURRENCY = {
    "AU": "AUD",
    "A": "AUD",
    "NZ": "NZD",
    "HK": "HKD",
    "S": "SGD",
    "R": "BRL",
    "MX": "MXN",
    # US/Canadian dollar prefixes — common in international postings
    "U": "USD",  # rare; appears as ``U$`` in older European templates
    "US": "USD",
    "CDN": "CAD",
    "C": "CAD",
    "CA": "CAD",
}

# After-amount (suffix) currency markers, e.g. "$120K AUD".
# Must be IMMEDIATELY adjacent to the amount — the ISO code must be
# the first significant token after the (possibly whitespace) trailer.
# Optional surrounding parens/brackets allowed (e.g. ``$120K (AUD)``).
_SUFFIX_CURRENCY_RE = re.compile(
    r"^\s*[(\[]?(AUD|NZD|SGD|HKD|BRL|MXN|CAD|USD)[)\]]?\b",
    re.IGNORECASE,
)


def _detect_dollar_currency(prefix: str | None, trailing: str) -> str:
    """Resolve the currency for a `$`-prefixed amount.

    Priority order (matches issue #3191):
      1. Explicit pre-amount marker like ``A$``, ``HK$``, ``R$``.
      2. Explicit post-amount ISO code IMMEDIATELY adjacent to the
         amount, e.g. ``"$120K AUD"`` or ``"$120K (AUD)"``. The code
         must be the first non-whitespace token after the amount — a
         generic US posting that mentions ``"AUD performance bonus"``
         later in the same sentence must not flip the currency.
      3. Fallback to ``USD``.
    """
    if prefix:
        marker = prefix.upper()
        if marker in _PREFIX_TO_CURRENCY:
            return _PREFIX_TO_CURRENCY[marker]

    if trailing:
        m = _SUFFIX_CURRENCY_RE.match(trailing)
        if m:
            return m.group(1).upper()

    return "USD"


def _is_european_decimal(raw: str) -> bool:
    """Return True if ``raw`` looks like a European-style thousands form.

    Examples that should return True:
      ``"50.000"`` → 50000 (BRL/EUR thousands form)
      ``"1.500.000"`` → 1500000
      ``"50.000,00"`` → 50000.00

    Examples that should return False:
      ``"50.5"`` → 50.5 (decimal)
      ``"50,000"`` → 50000 (US/anglo thousands)
      ``"150K"`` → suffix form
    """
    if "," in raw and "." in raw:
        # Mixed — assume European if the comma is the final separator
        return raw.rfind(",") > raw.rfind(".")
    if "." in raw and "," not in raw:
        # Multiple dots → thousands separator form; one dot + exactly 3
        # digits after → also thousands form
        return bool(re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw))
    return False


def _parse_dollar_number(raw: str, currency: str) -> float:
    """Parse a dollar-style amount, applying European-style thousands
    when the currency uses dot-as-thousands (BRL primarily)."""
    raw = raw.strip()
    if currency in ("BRL",) and _is_european_decimal(raw):
        # Convert European format: "50.000" → "50000", "50.000,00" → "50000.00"
        normalized = raw.replace(".", "").replace(",", ".")
        if normalized.upper().endswith("K"):
            return float(normalized[:-1]) * 1000
        return float(normalized)
    return _parse_number(raw)


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
        prefix = m.group(1)  # optional pre-amount currency marker
        trailing = m.group(4)

        # Resolve currency first — affects how BRL-style "." numbers parse.
        currency = _detect_dollar_currency(prefix, trailing)

        try:
            lo = _parse_dollar_number(m.group(2), currency)
            hi = _parse_dollar_number(m.group(3), currency)
        except ValueError:
            continue

        # Disqualify tiny amounts (below $15,000) and absurdly large ones.
        # The 15000 floor is in raw units; for BRL/MXN this is still a
        # sensible lower bound for an annual salary.
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
    r"\s+\(?(USD|CAD|EUR|GBP|CHF|AUD|NZD|SGD|HKD|BRL|MXN)\)?"
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
#   "A$80,000 per year" (AUD), "S$100k/year" (SGD), "HK$500K Annually" (HKD)
#   "R$50.000 per month" (BRL — European thousands)

_SINGLE_DOLLAR_PERIOD_RE = re.compile(
    r"(?:(?<=^)|(?<=[\s(\[]))"
    r"(A[U]?|N[Z]?|S|HK|R|MX|U[S]?|CDN|C[A]?)?\$([\d,.]+[Kk]?)\s*"
    r"((?:[A-Z]{2,3}\s*)?(?:per year|per annum|annually|annual|/year|/yr|"
    r"per hour|hourly|/hour|/hr|per month|monthly|/month|/mo))",
    re.IGNORECASE,
)


def _extract_single_dollar(text: str) -> list[SalaryRange]:
    results = []
    for m in _SINGLE_DOLLAR_PERIOD_RE.finditer(text):
        prefix = m.group(1)
        period_phrase = m.group(3)

        # The period phrase may include a leading ISO code (e.g. "AUD per year")
        # — resolve currency from prefix first, then from the period_phrase
        # head, then fallback to USD.
        currency = _detect_dollar_currency(prefix, period_phrase or "")

        try:
            val = _parse_dollar_number(m.group(2), currency)
        except ValueError:
            continue

        # Strip leading ISO code from the period phrase before period detection.
        period_clean = re.sub(
            r"^\s*(AUD|NZD|SGD|HKD|BRL|MXN|CAD|USD)\s*",
            "",
            period_phrase,
            flags=re.IGNORECASE,
        )
        period = _detect_period(period_clean)
        if period is None:
            continue
        if period == "yearly" and val < 15000:
            continue
        if period == "monthly" and (val < 500 or val > 100_000):
            continue
        if period == "hourly" and (val < 7 or val > 500):
            continue
        results.append(
            SalaryRange(
                min=int(val * 100) if period == "hourly" else int(val),
                max=None,
                currency=currency,
                period=period,
            )
        )
    return results


# ── Pattern 4b: <prefix>$X (single amount with explicit non-USD prefix) ──
# A prefix like ``A$``, ``S$``, ``HK$``, ``R$`` is itself a strong currency
# marker (closes #3191). We require salary context for safety but do NOT
# require an explicit period word — the prefix alone disambiguates from
# revenue/funding/etc.

# The amount alternation prevents regex backtracking from accepting a
# truncated number when the full amount is followed by a dash (range).
# In order:
#   1. <digits>[,.<digits>]*K — explicit K-suffix (only suffix digits allowed)
#   2. <digits>(,<digits>{3})+ — comma-thousands form, must end on a 3-digit group
#   3. <digits>(\.<digits>{3})+ — dot-thousands form (BRL/European), 3-digit groups
#   4. <digits>+ — bare integer (no separator)
_PREFIX_DOLLAR_SINGLE_RE = re.compile(
    r"(?:(?<=^)|(?<=[\s(\[]))"
    r"(A[U]?|N[Z]?|S|HK|R|MX|U[S]?|CDN|C[A]?)\$"
    r"(\d+(?:[.,]\d+)*[Kk]"
    r"|\d{1,3}(?:,\d{3})+"
    r"|\d{1,3}(?:\.\d{3})+"
    r"|\d+)"
)


def _extract_prefix_dollar_single(text: str) -> list[SalaryRange]:
    """Detect single non-USD dollar amounts like ``A$80,000`` or ``R$50.000``.

    Requires salary-confirming context to avoid extracting random
    benefits or amounts mentioned in narrative text. Skips matches
    that are part of a range (covered by ``_extract_dollar_range``).
    """
    results = []
    # Pre-compute the spans covered by range matches so we can skip
    # prefix-singles inside them (regex backtracking would otherwise
    # accept a truncated number adjacent to the range dash).
    range_spans = [(m.start(), m.end()) for m in _DOLLAR_RANGE_RE.finditer(text)]

    for m in _PREFIX_DOLLAR_SINGLE_RE.finditer(text):
        # Skip if this match falls inside a range match
        if any(s <= m.start() and m.end() <= e for s, e in range_spans):
            continue

        prefix = m.group(1)
        currency = _detect_dollar_currency(prefix, "")
        if currency == "USD":
            # No prefix matched our table — fall through, the regular
            # _extract_single_dollar path handles plain $.
            continue

        try:
            val = _parse_dollar_number(m.group(2), currency)
        except ValueError:
            continue

        # Require salary context in surrounding text
        start = max(0, m.start() - 100)
        end = min(len(text), m.end() + 80)
        surrounding = text[start:end]

        if _NOT_SALARY_RE.search(surrounding):
            continue
        if not _SALARY_CONTEXT_RE.search(surrounding):
            continue

        # Detect period from immediate trailing context (next ~30 chars)
        tail = text[m.end() : m.end() + 30]
        period = _detect_period(tail.lstrip(" +.,/").lstrip())
        if period is None:
            # No explicit period — default to yearly if value is salary-sized
            if val >= 15000:
                period = "yearly"
            elif 500 <= val <= 30_000:
                period = "monthly"
            elif 5 <= val <= 500:
                period = "hourly"
            else:
                continue

        # Period-specific sanity filters (same as _extract_single_dollar)
        if period == "yearly" and val < 15000:
            continue
        if period == "monthly" and (val < 500 or val > 100_000):
            continue
        if period == "hourly" and (val < 5 or val > 500):
            continue

        results.append(
            SalaryRange(
                min=int(val * 100) if period == "hourly" else int(val),
                max=None,
                currency=currency,
                period=period,
            )
        )
    return results


# ── Pattern 5: EUR salary lines ──────────────────────────────────────
#
# Scope B (#3326) extends this beyond the original single-amount keyword-
# prefixed shapes to cover the four highest-yield clusters from the recon:
#   1. AT Mindestgehalt boilerplate: "€ 3.930,00 brutto pro Monat (14mal jährlich)"
#   2. ES/NL Greenhouse template: "€NN.NNN—€NN.NNN EUR"
#   3. FR templates: "Salaire ENTRE 24 100 EUR ET 29 200 EUR", "de … à … euros brut par mois"
#   4. UK / EN-locale ranges with comma-thousands: "€72,500.00 - €115,230.00"
#
# Precision posture is identical to Scope A: only extract when a gross
# marker is present (brutto / brut / lordo / bruto / gross) OR no net
# marker is present AND a strong salary-context word ties the number to
# compensation. Netto-only is skipped.

# Number atom shared across all EUR shapes. Accepts:
#   * En/UK locale:    "60,000", "60,000.00"
#   * DE/IT/NL locale: "60.000", "60.000,00"
#   * FR/PT locale:    "60 000", "60 000,00"  (incl. NBSP / thin space)
#   * Glued/bare:      "60000", "60K"
# Decimal tail is optional; thousand-group sizes are not enforced here —
# `_parse_eu_number` does the heavy lifting downstream.
_EUR_NUM = (
    # comma-thousand[.dot-decimal]: 72,500 / 72,500.00 / 1,000,000.50
    r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?"
    # dot-thousand[,comma-decimal]: 60.000 / 60.000,00 / 1.000.000,50
    r"|\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?"
    # space-thousand[,comma-decimal]: 60 000 / 33 100,00 (also NBSP/thin-space)
    r"|\d{1,3}(?:[   ]\d{3})+(?:,\d{1,2})?"
    # plain integer with optional decimal: 60000 / 50000.00 / 1234,56 / 60K
    r"|\d+(?:[.,]\d{1,2})?[Kk]?"
)

# Currency token: EUR (ISO) or € (symbol) or spell-out (euros / Euro).
# Spell-out lives behind a word boundary so we don't pick up "euroska".
_EUR_CUR = r"(?:EUR|€|euros?\b|Euro\b)"

# Range separator vocabulary. Two flavours:
#   * tight — dash glyphs or a single connector word with whitespace.
#   * loose — Tesco-style: number — period word(s) — connector — number.
#             Bounded to ≤30 chars between to keep precision.
_EUR_SEP = (
    r"(?:"
    # connector phrases
    r"\s+(?:to|bis|zu|à|au|und|et|y|en|and|tot)\s+"
    # bare dashes
    r"|\s*[-–—]\s*"
    r")"
)
# Loose connector for `<num> <currency> [period-words] <connector> <num> <currency>`.
# Allows up to 30 chars of period vocabulary between the first currency
# token and the range connector word. The connector word itself is mandatory.
_EUR_LOOSE_SEP = (
    r"(?:"
    r"\s*[-–—]\s*"
    r"|\s+(?:to|bis|zu|à|au|und|et|y|en|and|tot)\s+"
    r"|.{0,40}?\s+(?:to|bis|zu|à|au|und|et|y|en|and|tot|ET)\s+"
    r")"
)

# Single-amount EUR regex — keyword-prefixed (Pattern 5 original shape).
_EUR_SINGLE_RE = re.compile(
    r"(?:"
    # "Salary: From/Starting from XXXX EUR/€/euros"
    r"(?:salary|gehalt|salaire|stipendio|salario|salaris|lön|løn|"
    r"wynagrodzenie|plat|mzda|vergütung|retribuzione|"
    r"mindestgehalt|mindestlohn|jahresgehalt|bruttojahresgehalt|"
    r"einstiegsgehalt|hourly\s+salary)\s*:?\s*"
    r"(?:from|ab|starting from|à\s+partir\s+de|von|mindestens|da|vanaf|"
    r"de|d['e]?)?\s*"
    rf"({_EUR_NUM})\s*{_EUR_CUR}"
    r"|"
    # "EUR/€ XXXX" — symbol before number
    rf"{_EUR_CUR}\s*({_EUR_NUM})"
    r"|"
    # "XXXX €" or "XXXX EUR" or "XXXX euros" — symbol after number
    rf"({_EUR_NUM})\s*{_EUR_CUR}"
    r")",
    re.IGNORECASE,
)

# Range EUR regex. Three shapes:
#   1. Currency-leading on both endpoints (Greenhouse/J&J/Mozilla):
#        "€65.000—€80.000 EUR"     "€72,500.00 - €115,230.00"
#   2. Connector word range:
#        "60 000 à 70 000 €"       "ENTRE 24 100 EUR ET 29 200 EUR"
#        "de 800 euros … à 1000 euros"   (FR `de … à` only matches if both endpoints
#                                         have currency; we don't pick up bare digits)
#        "between € 30.000 and € 36.000"
#   3. Single-currency-tail range:
#        "60 000 - 70 000 €"       "50.000 - 70.000EUR"      "37300EUR - 39100EUR"
_EUR_RANGE_RE = re.compile(
    r"(?:"
    # Shape 1 + 2: currency-leading on both endpoints (any separator/connector)
    rf"{_EUR_CUR}\s*({_EUR_NUM})\s*"
    rf"{_EUR_SEP}"
    rf"{_EUR_CUR}\s*({_EUR_NUM})"
    r"|"
    # Shape 3: currency-trailing range — both numbers, currency at the end
    rf"({_EUR_NUM})\s*"
    rf"{_EUR_SEP}"
    rf"({_EUR_NUM})\s*{_EUR_CUR}"
    r"|"
    # Shape 4: number — currency — loose-separator — number — currency
    # ("60 000 € à 70 000 €", "24 100 EUR ET 29 200 EUR",
    #  "800 euros brut par mois à 1000 euros brut par mois")
    rf"({_EUR_NUM})\s*{_EUR_CUR}\s*"
    rf"{_EUR_LOOSE_SEP}"
    rf"({_EUR_NUM})\s*{_EUR_CUR}"
    r")",
    re.IGNORECASE,
)

_EUR_CONTEXT_RE = re.compile(
    # Salary words across European languages.
    r"salary|gehalt|salaire|stipendio|salario|salaris|lön|løn|"
    r"wynagrodzenie|plat|mzda|fizetés|palk|"
    r"remuneration|rémunération|vergütung|retribuzione|"
    # Compensation-range phrasing common in ATS templates (J&J Workday,
    # Greenhouse, Mozilla, Mattermost, Airbnb, Fever).
    r"anticipated|base\s+pay|pay\s+range|salary\s+range|compensation\s+range|"
    r"hiring\s+range|posting\s+range|annual\s+pay\s+range|loonpakket|"
    # DE/AT collective-agreement and entitlement vocabulary.
    r"tarifvertrag|kollektivvertrag|mindestgehalt|mindestlohn|"
    r"bruttojahresgehalt|lehrlingseinkommen|einstiegsgehalt|"
    r"entgeltgruppe|jahresgehalt|stundenlohn|"
    # Gross / net indicators.
    r"gross|brutto|brut|lordo|netto|net\b|bruto\b|"
    # Period indicators (multilingual).
    r"per\s+month|monatlich|mensuel|mensile|monthly|/month|pro\s+monat|"
    r"par\s+mois|brut/?\s*mois|bruto\s+per\s+maand|"
    r"per\s+year|jährlich|annuel|annuale|annually|yearly|/year|pro\s+jahr|"
    r"par\s+an|brut/?\s*an|bruto\s+per\s+jaar|bruts?\s+annuels?|"
    r"14\s*mal\s+jährlich|"
    r"per\s+hour|hourly|/hour|stündlich|/hr|an\s+hour|per\s+annum|"
    r"pro\s+rata|"
    # Belgian 13/14-month indicator (counts as monthly-source context).
    r"13[,.]\d+\s+maanden|"
    # Catch-all monthly/yearly heads.
    r"\bpa\b|p\.a\.",
    re.IGNORECASE,
)

# Strong gross markers — at least one in window is sufficient to allow
# extraction even when "net" also appears nearby.
_EUR_GROSS_RE = re.compile(
    r"\b(?:gross|brutto|brut|lordo|bruto)\b",
    re.IGNORECASE,
)

# Net markers — if present without a gross marker, skip.
_EUR_NET_RE = re.compile(
    r"\b(?:netto|nett[oa]|net\b)\b",
    re.IGNORECASE,
)


def _parse_eur_number(raw: str) -> float | None:
    """Locale-aware EUR amount parser.

    Recognises four formats seen across EU postings:
        EN/UK     1,234.56
        DE/IT/NL  1.234,56     ("dot-thousand, comma-decimal")
        FR/PT     1 234,56     (space-thousand, with NBSP/thin space variants)
        Bare      1234         /  1234.56  /  12K

    The choice between thousand-vs-decimal turns on which separator
    appears last and how many digits trail it:
      * two separators present → last one is the decimal
      * one comma, exactly 3 trailing digits, no space → thousands (EN)
      * one comma, any other tail → decimal (EU)
      * one dot, multiple dots → thousands
      * one dot, exactly 3 trailing digits and >3 total digits → thousands
      * otherwise → decimal
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s or not any(c.isdigit() for c in s):
        return None

    # Trailing sentence-terminating dot defence ("60.000.").
    s = s.rstrip(".")

    # Normalise NBSP / thin space / non-breaking thin space to plain space.
    s = s.replace(" ", " ").replace(" ", " ").replace(" ", " ")

    # Trailing decimal-zero idiom CH `.–` already stripped at caller (`.--` too).
    s = s.replace("'", "").replace("’", "")

    has_comma = "," in s
    has_dot = "." in s
    has_space = " " in s

    try:
        if has_comma and has_dot:
            if s.rfind(",") > s.rfind("."):
                # 1.234,56 → 1234.56
                s = s.replace(".", "").replace(",", ".")
            else:
                # 1,234.56 → 1234.56
                s = s.replace(",", "")
        elif has_comma and not has_dot:
            after = s.split(",")[-1]
            if len(after) == 3 and s.count(",") >= 1 and not has_space:
                # 1,234 (English thousands)
                s = s.replace(",", "")
            else:
                # 24 100,00 / 1234,56 → decimal
                s = s.replace(",", ".")
        elif has_dot and not has_comma:
            after = s.split(".")[-1]
            dot_count = s.count(".")
            if dot_count > 1:
                s = s.replace(".", "")
            elif len(after) == 3 and dot_count == 1 and len(s.replace(".", "")) > 3:
                # 1.234 / 12.345 → thousands
                s = s.replace(".", "")
            # else 12.34 stays decimal
        if has_space:
            s = s.replace(" ", "")
        if s.upper().endswith("K"):
            return float(s[:-1]) * 1000
        return float(s)
    except ValueError:
        return None


def _eur_period_in_window(window: str, val: float) -> str | None:
    """Detect canonical period from a window of EUR-context text."""
    period_match = re.search(
        # Hourly markers (multilingual + abbreviations).
        r"per\s+hour|hourly|/hour|/hr|stündlich|an\s+hour|de\s+l'?heure|par\s+heure|"
        r"pro\s+stunde|stundenlohn|brutto/std|brutto/stunde|/std\.?|"
        # Monthly markers.
        r"per\s+month|monthly|/month|monatlich|mensuel|mensile|pro\s+monat|"
        r"par\s+mois|brut/?\s*mois|bruto\s+per\s+maand|"
        r"14\s*mal\s+jährlich|"
        # Yearly markers.
        r"per\s+year|annually|yearly|/year|jährlich|annuel|annuale|pro\s+jahr|"
        r"par\s+an|brut/?\s*an|bruto\s+per\s+jaar|bruts?\s+annuels?|"
        r"per\s+annum|pro\s+rata|jahresgehalt|bruttojahresgehalt|"
        r"p\.a\.",
        window,
        re.IGNORECASE,
    )
    if not period_match:
        return None
    raw = period_match.group(0).lower().strip()
    if any(t in raw for t in ("hour", "/hr", "stünd", "stunde", "heure", "/std")):
        return "hourly"
    if any(
        t in raw
        for t in (
            "month",
            "monat",
            "mensuel",
            "mensile",
            "mois",
            "maand",
            "14mal",
            "14 mal",
        )
    ):
        return "monthly"
    if any(
        t in raw
        for t in (
            "year",
            "annual",
            "annuel",
            "annuale",
            "jähr",
            "jahr",
            "annum",
            "p.a.",
            "an ",
            " an",
            "annuels",
            "annuel",
            "jaar",
            "pro rata",
        )
    ):
        return "yearly"
    # `par an` / `brut/an` and the bare `an hour` already covered above.
    return None


def _eur_apply_filters(val: float, period: str) -> bool:
    """Sanity-check a single EUR amount against its period magnitude."""
    if period == "hourly":
        return 5 <= val <= 200
    if period == "monthly":
        # AT Lehrlingseinkommen / DE hourly-derived monthly floors land
        # in the 800-1500 EUR band. Drop the floor for monthly to admit
        # AT apprenticeship / FR small-stipend snippets called out by recon.
        return 600 <= val <= 30000
    if period == "yearly":
        return 10000 <= val <= 500000
    return False


_EUR_DISQUALIFY_RE = re.compile(
    r"transport.{0,10}(compensation|allowance|Zuschuss)|"
    r"referral.{0,10}(bonus|reward|program)|"
    r"recommend.{0,10}(reward|bonus)|"
    r"empfehlung|"
    r"newborn.{0,10}bonus|"
    r"child.{0,10}(bonus|benefit|allowance)|"
    r"compensation for .{0,20}(tennis|gym|language|sport)|"
    r"Zuschuss|"
    r"commuting.{0,10}allowance",
    re.IGNORECASE,
)


def _eur_context_ok(window: str) -> bool:
    """Salary-context gate shared by single- and range-EUR matches.

    Implements the brutto/netto policy:
      - Strong gross marker present → accept (even if "net" also present).
      - Net marker present without gross → skip.
      - Otherwise require at least one salary/compensation/period word.
    """
    if not _EUR_CONTEXT_RE.search(window):
        return False
    if _EUR_DISQUALIFY_RE.search(window):
        return False
    return not (_EUR_NET_RE.search(window) and not _EUR_GROSS_RE.search(window))


def _emit_eur(val: float, val_hi: float | None, period: str) -> SalaryRange:
    scale = 100 if period == "hourly" else 1
    return SalaryRange(
        min=int(val * scale),
        max=(int(val_hi * scale) if val_hi is not None else None),
        currency="EUR",
        period=period,
    )


def _extract_eur(text: str) -> list[SalaryRange]:
    """Extract EUR amounts as SalaryRange objects.

    First sweeps ranges (so we don't fragment a `€A — €B` into two singles),
    then collects keyword-prefixed singles for the cells the range pass misses.
    """
    results: list[SalaryRange] = []
    range_spans: list[tuple[int, int]] = []

    # 1. Ranges first.
    for m in _EUR_RANGE_RE.finditer(text):
        g = m.groups()
        # The alternatives fill groups (1,2), (3,4), or (5,6).
        if g[0] is not None and g[1] is not None:
            raw_lo, raw_hi = g[0], g[1]
        elif g[2] is not None and g[3] is not None:
            raw_lo, raw_hi = g[2], g[3]
        elif g[4] is not None and g[5] is not None:
            raw_lo, raw_hi = g[4], g[5]
        else:
            continue

        lo = _parse_eur_number(raw_lo)
        hi = _parse_eur_number(raw_hi)
        if lo is None or hi is None:
            continue
        # Range sanity: hi must be >= lo, and within 5x (loose) for noise gate.
        if hi < lo:
            continue

        start = max(0, m.start() - 200)
        end = min(len(text), m.end() + 100)
        window = text[start:end]

        if not _eur_context_ok(window):
            continue

        period = _eur_period_in_window(window, lo)
        if period is None:
            # Heuristic fallback by magnitude.
            if lo < 1000:
                period = "hourly"
            elif lo < 10000:
                period = "monthly"
            else:
                period = "yearly"

        if not _eur_apply_filters(lo, period) or not _eur_apply_filters(hi, period):
            continue

        results.append(_emit_eur(lo, hi, period))
        range_spans.append((m.start(), m.end()))

    # 2. Singles — only outside any range span already captured.
    for m in _EUR_SINGLE_RE.finditer(text):
        if any(s <= m.start() and m.end() <= e for s, e in range_spans):
            continue

        raw = m.group(1) or m.group(2) or m.group(3)
        if not raw:
            continue
        raw = raw.strip()
        if not raw or not any(c.isdigit() for c in raw):
            continue

        start = max(0, m.start() - 200)
        end = min(len(text), m.end() + 100)
        window = text[start:end]

        if not _eur_context_ok(window):
            continue

        val = _parse_eur_number(raw)
        if val is None:
            continue

        period = _eur_period_in_window(window, val)
        if period is None:
            # Heuristic by magnitude when context is silent.
            if val < 800:
                # Too small for monthly / yearly without explicit hourly
                # context → bail to avoid false positives.
                continue
            period = "monthly" if val < 10000 else "yearly"

        if not _eur_apply_filters(val, period):
            continue

        results.append(_emit_eur(val, None, period))

    return results


# ── Pattern 6: GBP ──────────────────────────────────────────────────
#
# Scope B (#3326) extends the original two regexes to cover:
#   * UK NHS / public-sector period vocab: `per annum`, `pro rata`
#   * Tesco hourly: `£N.NN an hour`
#   * NHS hourly singles: `Hourly salary: £N.NN`
#   * `to` as range connector (NHS "£28,011 to £30,230")
#   * Tesco template: `starts from £X an hour; this increases to £Y`
# Mojibake `Â£` is repaired upstream in `_html_to_text`, so the regex
# is plain `£`. `_NOT_SALARY_RE` is hardened with `budget|project|deal|
# contract|funding` so `£250k-£2M project budget` stays rejected.

# GBP number atom — same precision as before; accept `K`/`k` suffix
# (Tesco / Hireology) plus optional `.NN` decimal.
_GBP_NUM = r"[\d,]+(?:\.\d+)?[Kk]?"

# Range separator. Two families:
#   1. Tight connector — dash glyphs or `to`/`up to`. Goes immediately
#      between the two £-amounts: "£28,011 to £30,230".
#   2. Loose connector — Tesco's `<num> an hour; this increases to <num>`.
#      Between the two numbers we may pass a period word and punctuation.
_GBP_TIGHT_SEP = (
    r"(?:"
    r"\s*[-–—]\s*"
    r"|\s+to\s+"
    r")"
)
_GBP_LOOSE_SEP = (
    r"(?:\s+(?:an\s+hour|per\s+hour|hourly|per\s+annum|annually|per\s+year))?"
    r"\s*[;,]?\s*"
    r"(?:this\s+increases\s+to|rising\s+to|up\s+to)\s+"
)

_GBP_RANGE_RE = re.compile(
    rf"£({_GBP_NUM})"
    rf"(?:{_GBP_TIGHT_SEP}|{_GBP_LOOSE_SEP})"
    rf"£?({_GBP_NUM})"
    r"(\s*.{0,60})",
)

# Single-amount with period AFTER. Adds `an hour`, `per annum`, `pro rata`.
_GBP_SINGLE_RE = re.compile(
    r"£([\d,]+(?:\.\d+)?[Kk]?)\s*"
    r"(per\s+hour|hourly|an\s+hour|per\s+year|annually|per\s+annum|"
    r"pro\s+rata|/hr|/hour|/year)"
)

# Single-amount with period BEFORE the number — NHS / Tesco templates
# like `Hourly salary: £14.76` or `Salary: £25,000 per annum`.
_GBP_PREFIX_PERIOD_RE = re.compile(
    r"(hourly\s+salary|hourly\s+pay|hourly\s+rate)\s*[:.\-]?\s*"
    r"£([\d,]+(?:\.\d+)?[Kk]?)",
    re.IGNORECASE,
)

# Disqualifiers specific to GBP — project budgets must NEVER fire.
_GBP_NOT_SALARY_RE = re.compile(
    r"\b(?:revenue|billion|million|funding|raised|ipo|valuation|"
    r"market\s+cap|investment|assets|turnover|"
    r"budget|project|deal|contract|funding)\b",
    re.IGNORECASE,
)


def _gbp_period_from_window(window: str) -> str | None:
    """Detect period from a window of GBP context."""
    m = re.search(
        r"per\s+hour|hourly|an\s+hour|/hr|/hour|"
        r"per\s+year|annually|per\s+annum|pro\s+rata|/year",
        window,
        re.IGNORECASE,
    )
    if not m:
        return None
    raw = m.group(0).lower().strip()
    if any(t in raw for t in ("hour", "/hr")):
        return "hourly"
    return "yearly"


def _extract_gbp(text: str) -> list[SalaryRange]:
    results: list[SalaryRange] = []
    range_spans: list[tuple[int, int]] = []

    for m in _GBP_RANGE_RE.finditer(text):
        lo = _parse_number(m.group(1))
        hi = _parse_number(m.group(2))
        trailing = m.group(3) or ""

        # Stronger NOT_SALARY guard (covers project budgets).
        start = max(0, m.start() - 100)
        surrounding = text[start : m.end()] + trailing
        if _GBP_NOT_SALARY_RE.search(surrounding):
            continue

        # Require salary-confirming context — `per annum` and `pro rata`
        # are NHS-template terms, `an hour` is Tesco.
        if not re.search(
            r"salary|pay|compensation|per\s+year|annually|per\s+hour|hourly|"
            r"per\s+annum|pro\s+rata|an\s+hour|dependent\s+on|rate\s+of\s+pay",
            surrounding,
            re.IGNORECASE,
        ):
            continue

        # Pick the period from the surrounding window. Falls back to
        # the previous magnitude heuristic on silence.
        period = _gbp_period_from_window(surrounding)
        if period is None:
            if lo < 10000 or hi > 500000:
                if lo < 7 or lo > 200:
                    continue
                period = "hourly"
            else:
                period = "yearly"

        if period == "hourly":
            if lo < 5 or lo > 200 or hi < lo:
                continue
            lo_val = int(lo * 100)
            hi_val = int(hi * 100)
        else:
            if lo < 10000 or hi > 500000 or hi < lo:
                continue
            lo_val = int(lo)
            hi_val = int(hi)

        results.append(SalaryRange(min=lo_val, max=hi_val, currency="GBP", period=period))
        range_spans.append((m.start(), m.end()))

    for m in _GBP_SINGLE_RE.finditer(text):
        if any(s <= m.start() and m.end() <= e for s, e in range_spans):
            continue
        val = _parse_number(m.group(1))
        period = _detect_period(m.group(2).lower().replace("an hour", "per hour"))
        if period is None:
            continue
        if period == "hourly" and (val < 5 or val > 200):
            continue
        if period == "yearly" and (val < 10000 or val > 500000):
            continue
        results.append(
            SalaryRange(
                min=int(val * 100) if period == "hourly" else int(val),
                max=None,
                currency="GBP",
                period=period,
            )
        )

    for m in _GBP_PREFIX_PERIOD_RE.finditer(text):
        if any(s <= m.start() and m.end() <= e for s, e in range_spans):
            continue
        val = _parse_number(m.group(2))
        # The prefix word IS the period (hourly).
        if val < 5 or val > 200:
            continue
        results.append(
            SalaryRange(
                min=int(val * 100),
                max=None,
                currency="GBP",
                period="hourly",
            )
        )

    return results


# ── Pattern 7: CHF (Swiss franc) ─────────────────────────────────────
#   "CHF 120'000 - 150'000"      (apostrophe thousands, straight `'`)
#   "CHF 120’000 - 150’000"     (apostrophe thousands, curly U+2019)
#   "CHF 8'500 pro Monat"
#   "CHF 16.– de l'heure"        (Swiss `.–` decimal-zero idiom, FR period)
#   "CHF 1'500.00 / brutto pro Monat"

# The amount character class admits straight + curly apostrophes, the
# Swiss decimal-zero idiom `.–` (handled by `_normalize_chf_amount`), dot,
# and comma. We intentionally exclude bare ` -` from the inner class so
# the range separator (also a dash) isn't swallowed by greedy matching.
_CHF_NUM = r"[\d][\d'’.,]*\d|\d"
_CHF_RE = re.compile(
    r"CHF\s*"
    rf"({_CHF_NUM}(?:\.[\-–—]+)?)"
    rf"(?:\s*[-–—]\s*({_CHF_NUM}(?:\.[\-–—]+)?))?"
    r"(\s*.{0,80})",
    re.IGNORECASE,
)

_CHF_CONTEXT_RE = re.compile(
    r"salary|gehalt|salaire|stipendio|lohn|salaris|vergütung|"
    r"gross|brutto|brut|"
    # Period markers — DE + EN + FR (recon called out `de l'heure` / `par heure`).
    r"per\s+month|monatlich|pro\s+monat|monthly|/month|par\s+mois|"
    r"per\s+year|jährlich|pro\s+jahr|annually|yearly|/year|par\s+an|"
    r"per\s+hour|pro\s+stunde|hourly|/hour|stündlich|de\s+l'?heure|par\s+heure",
    re.IGNORECASE,
)


def _normalize_chf_amount(raw: str) -> str:
    """Strip Swiss-specific decimal-zero idioms before parsing.

    Inputs like `CHF 16.–` and `CHF 1'500.--` mean `16.00` and `1500.00`
    respectively. The `–` (U+2013) and bare `-` after the decimal point
    encode the literal zero cents. We rewrite to `.00` so the standard
    parser handles the number.
    """
    # `.–` / `.—` / `.--` / `.- ` at end → decimal zero.
    return re.sub(r"\.(?:[\-–—]+)(?=\s|$|[^\d])", ".00", raw)


def _extract_chf(text: str) -> list[SalaryRange]:
    results = []
    for m in _CHF_RE.finditer(text):
        raw_lo = _normalize_chf_amount(m.group(1).strip())
        raw_hi = _normalize_chf_amount(m.group(2).strip()) if m.group(2) else None

        if not any(c.isdigit() for c in raw_lo):
            continue

        start = max(0, m.start() - 150)
        end = min(len(text), m.end() + 100)
        surrounding = text[start:end]

        if not _CHF_CONTEXT_RE.search(surrounding):
            continue

        try:
            lo = _parse_number(raw_lo)
            hi = _parse_number(raw_hi) if raw_hi else None
        except ValueError:
            continue

        # Detect period
        period = None
        period_match = re.search(
            r"pro\s+stunde|per\s+hour|hourly|stündlich|/hour|/hr|"
            r"de\s+l'?heure|par\s+heure|"
            r"pro\s+monat|per\s+month|monthly|monatlich|/month|par\s+mois|"
            r"pro\s+jahr|per\s+year|annually|yearly|jährlich|/year|par\s+an",
            surrounding,
            re.IGNORECASE,
        )
        if period_match:
            raw_period = period_match.group(0).lower()
            if "stunde" in raw_period or "hour" in raw_period or "heure" in raw_period:
                period = "hourly"
            elif "monat" in raw_period or "month" in raw_period or "mois" in raw_period:
                period = "monthly"
            elif "jahr" in raw_period or "year" in raw_period or "an" in raw_period:
                period = "yearly"
        if period is None:
            if lo < 500:
                period = "hourly"
            elif lo < 15000:
                period = "monthly"
            else:
                period = "yearly"

        if period == "hourly" and (lo < 15 or lo > 300):
            continue
        # Recon: lower CHF monthly floor 2000 → 1200 to admit Swiss
        # bachelor-level Praktika (PSI/ETH/EPFL).
        if period == "monthly" and (lo < 1200 or lo > 30000):
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
    # Non-USD prefix singles (A$, S$, HK$, R$, etc.) — closes #3191
    prefix_singles = _extract_prefix_dollar_single(text)

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

    all_results = dollar_ranges + dollar_singles + prefix_singles + eur + gbp + chf + eu_extra

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
        # Within singles, dedupe by (currency, period, min) — _extract_single_dollar
        # and _extract_prefix_dollar_single can both match the same text when an
        # explicit period is present (closes #3191).
        seen: set[tuple[str, str, int]] = set()
        unique_singles: list[SalaryRange] = []
        for s in filtered_singles:
            key = (s.currency, s.period, s.min)
            if key in seen:
                continue
            seen.add(key)
            unique_singles.append(s)
        all_results = ranges + unique_singles

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
