"""Enrichment schema, prompt, and user message builder.

Defines the Pydantic model for structured extraction from job posting HTML,
the system prompt, and the helper that builds the user message.

See docs/09-enrichment.md for field definitions and accepted values.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

ENRICH_VERSION = 2

MAX_INPUT_CHARS = 60_000  # ~15K tokens

# ── Enum types ───────────────────────────────────────────────────────

Seniority = Literal[
    "intern",
    "entry",
    "mid",
    "senior",
    "lead",
    "staff",
    "principal",
    "director",
    "executive",
]

Education = Literal[
    "none",
    "vocational",
    "associate",
    "bachelor",
    "master",
    "doctorate",
]

VisaSponsorship = Literal["yes", "no"]

Benefit = Literal[
    "equity",
    "bonus",
    "retirement",
    "signing_bonus",
    "relocation",
    "health_insurance",
    "dental",
    "vision",
    "life_insurance",
    "disability_insurance",
    "mental_health",
    "gym",
    "pto",
    "parental_leave",
    "childcare",
    "vacation_extra",
    "sabbatical",
    "flexible_hours",
    "remote_budget",
    "education_budget",
    "meal_allowance",
    "public_transport",
    "bike_leasing",
    "company_car",
]


# ── Result model ─────────────────────────────────────────────────────


class Experience(BaseModel):
    min: int | None = None
    max: int | None = None


class EnrichmentResult(BaseModel):
    """Structured fields extracted from a job posting by an LLM."""

    seniority: Seniority | None = None
    education: Education | None = None
    experience: Experience | None = None
    visa_sponsorship: VisaSponsorship | None = None
    technologies: list[str] | None = None
    keywords: list[str] | None = None
    benefits: list[Benefit] | None = None


# ── Prompt ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a structured data extractor for job postings. Extract information \
from the HTML job description and metadata provided.

Rules:
- Extract only what is explicitly stated or clearly implied. Do not guess.
- Return null for any field where the information is absent or ambiguous.

Field-specific guidance:

seniority — Infer from job title + requirements combined.
  "intern": internship, working student, Werkstudent, Praktikum, stage.
  "entry": junior, graduate program, 0-2 years experience, trainee.
  "mid": 2-5 years, no seniority qualifier in title.
  "senior": senior, 5+ years, Sr.
  "lead": team lead, tech lead, engineering lead.
  "staff": staff engineer/designer.
  "principal": principal engineer/architect.
  "director": director-level, VP of Engineering (non-C-suite).
  "executive": C-suite, Managing Director, CEO, CTO, CFO.

education — Minimum stated requirement. "or equivalent experience" still \
counts as that level.
  "none": explicitly states no degree required.
  "vocational": apprenticeship, trade school, Ausbildung, CFC/EFZ, BTS, \
Berufsausbildung, apprendistato.
  "associate": associate degree, community college, DUT, DEUG.
  "bachelor": bachelor's, licence, Fachhochschule, laurea triennale.
  "master": master's, Diplom, DEA, laurea magistrale.
  "doctorate": PhD, Dr., Promotion, doctorat.

experience — Integer years only.
  "3+ years" → {"min": 3, "max": null}.
  "1-2 years" → {"min": 1, "max": 2}.
  If only a single number, set both min and max to that value.

visa_sponsorship — "yes" only if explicitly offered ("we sponsor visas", \
"visa support available"). "Must have existing work authorization" or \
"valid work permit required" → "no". If not mentioned → null.

technologies — Specific named tools, frameworks, and languages only. \
Use proper casing (e.g. "PostgreSQL" not "postgres", "React" not "react"). \
Do not include generic categories ("databases", "cloud").

keywords — 5-10 lowercase terms a job seeker would search for. Include \
role function, domain, and industry terms. Do NOT include technology names \
(those go in technologies) or generic words ("job", "company", "team").

benefits — Pick only from the allowed values. Map common synonyms:
  equity: stock options, RSUs, ESOP, VSOP, shares.
  bonus: performance bonus, annual bonus, 13th/14th salary, Gratifikation.
  retirement: company pension, 401(k), Betriebsrente, prévoyance, LPP/BVG, \
pilier, Pensionskasse.
  signing_bonus: sign-on bonus, welcome bonus.
  relocation: relocation package, moving assistance.
  health_insurance: supplementary health, private health, Zusatzversicherung.
  dental: dental plan, Zahnzusatzversicherung.
  vision: vision plan, eye care.
  life_insurance: life insurance, AD&D.
  disability_insurance: short/long-term disability, Invalidenversicherung.
  mental_health: therapy, EAP, mental health support, psychological support.
  gym: fitness membership, Urban Sports Club, sports budget.
  pto: paid time off, unlimited PTO (US-specific, only when explicitly named).
  parental_leave: parental leave beyond statutory, extended maternity/paternity.
  childcare: Kita subsidy, childcare vouchers, crèche, on-site nursery.
  vacation_extra: extra vacation days above statutory minimum.
  sabbatical: sabbatical, extended unpaid leave option.
  flexible_hours: flextime, Gleitzeit, core hours, flexible schedule.
  remote_budget: home office stipend, equipment budget, WFH allowance.
  education_budget: learning budget, conference budget, training allowance, \
Weiterbildungsbudget.
  meal_allowance: meal vouchers, lunch subsidy, Essenszulage, tickets restaurant.
  public_transport: transit pass, Jobticket, Navigo, SBB GA/Halbtax.
  bike_leasing: JobRad, bicycle leasing, Swapfiets, bike benefit.
  company_car: company car, car allowance, Firmenwagen.
"""

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(html: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    text = _TAG_RE.sub(" ", html)
    return _WS_RE.sub(" ", text).strip()


def build_user_message(
    html: str,
    *,
    title: str | None = None,
    locations: list[str] | None = None,
    employment_type: str | None = None,
) -> str:
    """Build the user message for the LLM from HTML + metadata context."""
    parts: list[str] = []

    if title:
        parts.append(f"Job title: {title}")
    if locations:
        parts.append(f"Locations: {', '.join(locations)}")
    if employment_type:
        parts.append(f"Employment type: {employment_type}")

    text = _strip_html(html)
    if len(text) > MAX_INPUT_CHARS:
        text = text[:MAX_INPUT_CHARS] + "... [truncated]"

    parts.append(f"\nJob description:\n{text}")
    return "\n".join(parts)
