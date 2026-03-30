"""
Download all job postings and extract 1-3 word keyword phrases.

Usage:
    uv run python scripts/extract_keywords.py [--top N] [--out FILE]

Outputs a ranked list of keyword n-grams (1-3 words) with document frequency.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections import Counter
from html import unescape
from pathlib import Path

import asyncpg

sys.path.insert(0, ".")
from src.config import settings

# ── Stop words (common English words to filter out) ──

STOP_WORDS: set[str] = {
    # ── Function words ──
    "a",
    "an",
    "the",
    "and",
    "or",
    "but",
    "in",
    "on",
    "at",
    "to",
    "for",
    "of",
    "with",
    "by",
    "from",
    "as",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "shall",
    "can",
    "need",
    "must",
    "not",
    "no",
    "nor",
    "so",
    "if",
    "then",
    "than",
    "too",
    "very",
    "just",
    "about",
    "above",
    "after",
    "again",
    "all",
    "also",
    "am",
    "any",
    "because",
    "before",
    "between",
    "both",
    "during",
    "each",
    "few",
    "get",
    "got",
    "he",
    "her",
    "here",
    "him",
    "his",
    "how",
    "i",
    "into",
    "it",
    "its",
    "let",
    "like",
    "make",
    "me",
    "more",
    "most",
    "my",
    "new",
    "now",
    "of",
    "off",
    "old",
    "once",
    "only",
    "other",
    "our",
    "out",
    "over",
    "own",
    "per",
    "put",
    "re",
    "s",
    "same",
    "she",
    "some",
    "still",
    "such",
    "t",
    "take",
    "tell",
    "that",
    "their",
    "them",
    "these",
    "they",
    "this",
    "those",
    "through",
    "under",
    "up",
    "upon",
    "us",
    "use",
    "used",
    "using",
    "we",
    "well",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "whom",
    "why",
    "you",
    "your",
    "ll",
    "ve",
    "don",
    "doesn",
    "didn",
    "won",
    "isn",
    "aren",
    "wasn",
    "weren",
    "hasn",
    "haven",
    "hadn",
    "couldn",
    "wouldn",
    "shouldn",
    "d",
    "m",
    "o",
    # ── Common non-French/German/Italian words ──
    "e",
    "g",
    "etc",
    "ie",
    "de",
    "la",
    "le",
    "les",
    "des",
    "du",
    "en",
    "et",
    "un",
    "une",
    "ou",
    "est",
    "nous",
    "vous",
    "qui",
    "que",
    "dans",
    "pour",
    "sur",
    "avec",
    "plus",
    "par",
    "ce",
    "se",
    "son",
    "ses",
    "aux",
    "der",
    "die",
    "das",
    "und",
    "ist",
    "von",
    "mit",
    "den",
    "dem",
    "ein",
    "eine",
    "zu",
    "auf",
    "im",
    "es",
    "wir",
    "sie",
    "fur",
    "an",
    "als",
    "bei",
    "nach",
    "uber",
    "il",
    "di",
    "che",
    "non",
    "si",
    "lo",
    "al",
    "con",
    "del",
    "da",
    "dei",
    "nella",
    "nella",
    "nel",
    "sono",
    "anche",
    "essere",
    # ── Job posting boilerplate ──
    "apply",
    "job",
    "jobs",
    "position",
    "role",
    "roles",
    "work",
    "working",
    "team",
    "teams",
    "join",
    "company",
    "experience",
    "experiences",
    "ability",
    "required",
    "requirements",
    "preferred",
    "qualifications",
    "responsibilities",
    "opportunity",
    "opportunities",
    "looking",
    "strong",
    "including",
    "please",
    "within",
    "across",
    "ensure",
    "ensuring",
    "support",
    "related",
    "based",
    "help",
    "years",
    "year",
    "ideal",
    "candidate",
    "candidates",
    "skills",
    "knowledge",
    "understanding",
    "environment",
    "provide",
    "providing",
    "develop",
    "developing",
    "maintain",
    "maintaining",
    "manage",
    "managing",
    "create",
    "build",
    "building",
    "include",
    "includes",
    "excellent",
    "good",
    "great",
    "relevant",
    "one",
    "two",
    "three",
    "four",
    "five",
    "part",
    "full",
    "time",
    "day",
    "days",
    # ── HR / legal / benefits boilerplate ──
    "equal",
    "employer",
    "employment",
    "status",
    "disability",
    "veteran",
    "protected",
    "inclusive",
    "culture",
    "diverse",
    "diversity",
    "equity",
    "inclusion",
    "accommodation",
    "adjustment",
    "basis",
    "national",
    "origin",
    "gender",
    "race",
    "religion",
    "sex",
    "sexual",
    "orientation",
    "age",
    "color",
    "marital",
    "genetic",
    "citizenship",
    "identity",
    "expression",
    "pregnancy",
    "law",
    "applicable",
    "federal",
    "state",
    "regardless",
    "prohibited",
    "discrimination",
    "affirmative",
    "action",
    "reasonable",
    "request",
    "notice",
    "accordance",
    "comply",
    "benefits",
    "compensation",
    "salary",
    "range",
    "base",
    "bonus",
    "package",
    "medical",
    "dental",
    "vision",
    "insurance",
    "retirement",
    "401k",
    "pto",
    "paid",
    "leave",
    "vacation",
    "holiday",
    "holidays",
    "stock",
    "equity",
    "options",
    "vesting",
    "competitive",
    "comprehensive",
    "eligible",
    "eligibility",
    "enrollment",
    "coverage",
    "plan",
    "plans",
    "health",
    "wellness",
    "mental",
    "flexible",
    "remote",
    "hybrid",
    "onsite",
    "office",
    # ── Generic business / job verbs and nouns ──
    "deliver",
    "drive",
    "lead",
    "enable",
    "empower",
    "empowers",
    "foster",
    "leverage",
    "optimize",
    "implement",
    "execute",
    "collaborate",
    "communicate",
    "coordinate",
    "facilitate",
    "contribute",
    "identify",
    "evaluate",
    "assess",
    "monitor",
    "track",
    "report",
    "review",
    "analyze",
    "define",
    "establish",
    "improve",
    "enhance",
    "achieve",
    "meet",
    "exceed",
    "grow",
    "scale",
    "innovate",
    "transform",
    "business",
    "services",
    "service",
    "solutions",
    "solution",
    "results",
    "performance",
    "process",
    "processes",
    "operations",
    "operational",
    "strategy",
    "strategic",
    "goals",
    "objectives",
    "standards",
    "quality",
    "value",
    "values",
    "mission",
    "impact",
    "success",
    "growth",
    "innovation",
    "innovative",
    "responsible",
    "critical",
    "key",
    "core",
    "best",
    "high",
    "level",
    "world",
    "class",
    "industry",
    "market",
    "global",
    "internal",
    "external",
    "cross-functional",
    "end-to-end",
    "hands-on",
    "fast-paced",
    "stakeholders",
    "stakeholder",
    "customers",
    "customer",
    "client",
    "clients",
    "partners",
    "partner",
    "partner.",
    "customers.",
    "leadership",
    "professional",
    "technical",
    "complex",
    "multiple",
    "various",
    "specific",
    "specific.",
    "different",
    "appropriate",
    "effective",
    "efficient",
    "significant",
    "continuous",
    "ongoing",
    "current",
    "future",
    "existing",
    "potential",
    "overall",
    # ── Resume / application words ──
    "applying",
    "application",
    "applications",
    "submit",
    "resume",
    "cover",
    "letter",
    "hiring",
    "interview",
    "onboarding",
    "recruiting",
    "recruiter",
    "talent",
    "background",
    "check",
    "screening",
    "offer",
    "listed",
    "contact",
    "information",
    "information.",
    "visit",
    "http",
    "https",
    "www",
    "com",
    "org",
    "net",
    "io",
    # ── Company-specific boilerplate (Amazon, etc.) ──
    "amazonians",
    "amazon",
    "country/region",
    "location.",
    "factors",
    # ── Misc ──
    "bachelor",
    "degree",
    "master",
    "education",
    "equivalent",
    "certification",
    "certified",
    "accredited",
    "basic",
    "advanced",
    "intermediate",
    "proficiency",
    "proficient",
    "minimum",
    "maximum",
    "approximately",
    "ability",
    "capable",
    "proven",
    "demonstrated",
    "deep",
    "solid",
    "extensive",
    "broad",
    "hands",
    "attention",
    "detail",
    "details",
    "passion",
    "passionate",
    "self",
    "driven",
    "motivated",
    "proactive",
    "independent",
    "communication",
    "written",
    "verbal",
    "oral",
    "interpersonal",
    "organizational",
    "analytical",
    "problem",
    "solving",
    "problems",
    "thinking",
    "mindset",
    "approach",
    "focus",
    "focused",
    "oriented",
    "life",
    "career",
    "people",
    "person",
    "individual",
    "individuals",
    "member",
    "members",
    "group",
    "groups",
    "area",
    "areas",
    "field",
    "line",
    "function",
    "department",
    "unit",
    "division",
    "organization",
    "organizations",
    "project",
    "projects",
    "program",
    "programs",
    "initiative",
    "initiatives",
    "effort",
    "efforts",
    "activity",
    "activities",
    "tools",
    "tool",
    "systems",
    "system",
    "platform",
    "platforms",
    "technologies",
    "technology",
    "solutions",
    "products",
    "product",
    "features",
    "feature",
    "capabilities",
    "capability",
    "needs",
    "need",
    "goal",
    "goals",
    "outcome",
    "outcomes",
    "delivery",
    "deliverables",
    "milestones",
    "deadlines",
    "timelines",
    "documentation",
    "docs",
    "records",
    "reports",
    "reporting",
    "training",
    "learn",
    "learning",
    "development",
    "developer",
    "manager",
    "engineers",
    "engineer",
    "analyst",
    "specialist",
    "coordinator",
    "director",
    "associate",
    "consultant",
    "architect",
    "designer",
    "administrator",
    "officer",
    "executive",
    "head",
    "expert",
    "expertise",
    "owner",
    "senior",
    "junior",
    "principal",
    "staff",
    "intern",
    "internship",
    "entry",
    "mid",
    "lead",
    "offers",
    "network",
    "and/or",
}

# ── Domain terms to protect from common-word blacklisting ──
# These appear in common English word lists but are meaningful job keywords.

PROTECTED_TERMS: set[str] = {
    # Programming languages & runtimes
    "python",
    "java",
    "javascript",
    "typescript",
    "ruby",
    "rust",
    "go",
    "swift",
    "kotlin",
    "scala",
    "php",
    "perl",
    "dart",
    "lua",
    "r",
    "c",
    "c++",
    "c#",
    "objective-c",
    "shell",
    "bash",
    "powershell",
    "matlab",
    "fortran",
    "cobol",
    "haskell",
    "elixir",
    "erlang",
    "clojure",
    # Frontend
    "react",
    "angular",
    "vue",
    "svelte",
    "next",
    "nuxt",
    "remix",
    "html",
    "css",
    "sass",
    "tailwind",
    "bootstrap",
    "webpack",
    "vite",
    "figma",
    "sketch",
    # Backend & infra
    "node",
    "express",
    "django",
    "flask",
    "rails",
    "spring",
    "laravel",
    "docker",
    "kubernetes",
    "terraform",
    "ansible",
    "puppet",
    "chef",
    "nginx",
    "apache",
    "consul",
    "vault",
    "istio",
    "envoy",
    # Cloud & platforms
    "aws",
    "azure",
    "gcp",
    "heroku",
    "vercel",
    "netlify",
    "cloudflare",
    "lambda",
    "ec2",
    "s3",
    "rds",
    "dynamodb",
    "sqs",
    "sns",
    "ecs",
    "eks",
    # Data & ML
    "sql",
    "nosql",
    "mongodb",
    "postgres",
    "postgresql",
    "mysql",
    "redis",
    "elasticsearch",
    "kafka",
    "rabbitmq",
    "spark",
    "hadoop",
    "airflow",
    "dbt",
    "snowflake",
    "databricks",
    "bigquery",
    "redshift",
    "tableau",
    "looker",
    "grafana",
    "prometheus",
    "datadog",
    "pytorch",
    "tensorflow",
    "keras",
    "scikit-learn",
    "pandas",
    "numpy",
    "ml",
    "ai",
    "nlp",
    "llm",
    "gpt",
    "bert",
    "transformer",
    "neural",
    "deep learning",
    "machine learning",
    "computer vision",
    "natural language",
    "reinforcement learning",
    # DevOps / SRE
    "ci/cd",
    "jenkins",
    "gitlab",
    "github",
    "bitbucket",
    "circleci",
    "devops",
    "sre",
    "devsecops",
    "observability",
    "monitoring",
    "linux",
    "unix",
    # Protocols & APIs
    "rest",
    "graphql",
    "grpc",
    "websocket",
    "oauth",
    "jwt",
    "saml",
    "api",
    "sdk",
    "cli",
    # Architecture
    "microservices",
    "serverless",
    "monolith",
    "event-driven",
    "distributed",
    "scalable",
    "cloud-native",
    # Mobile
    "ios",
    "android",
    "flutter",
    "react native",
    # Specific tech
    "git",
    "jira",
    "confluence",
    "slack",
    "salesforce",
    "sap",
    "servicenow",
    "workday",
    "oracle",
    "ibm",
    "blockchain",
    "web3",
    "ethereum",
    "solidity",
    "iot",
    "embedded",
    "firmware",
    "fpga",
    "vhdl",
    "robotics",
    "unity",
    "unreal",
    # Data & analytics
    "etl",
    "elt",
    "olap",
    "data warehouse",
    "data lake",
    "data pipeline",
    "data engineering",
    "data science",
    "analytics",
    "bi",
    "power bi",
    # Security
    "security",
    "encryption",
    "firewall",
    "penetration testing",
    "compliance",
    "soc",
    "iso",
    "gdpr",
    "hipaa",
    "pci",
    # Methodologies
    "agile",
    "scrum",
    "kanban",
    "lean",
    "waterfall",
    "sprint",
    # Roles / domain
    "backend",
    "frontend",
    "full-stack",
    "fullstack",
    "devrel",
    "fintech",
    "healthtech",
    "edtech",
    "biotech",
    "medtech",
    "saas",
    "b2b",
    "b2c",
    "e-commerce",
    "ecommerce",
    "automation",
    "testing",
    "qa",
    "cicd",
}

# ── Load common English words ──


def load_common_words(path: str, limit: int = 3000) -> set[str]:
    """Load top N common English words, excluding protected tech terms."""
    p = Path(path)
    if not p.exists():
        print(f"Warning: common words file not found at {path}", file=sys.stderr)
        return set()
    words = set()
    with open(p) as f:
        for i, line in enumerate(f):
            if i >= limit:
                break
            w = line.strip().lower()
            if w and len(w) > 1 and w not in PROTECTED_TERMS:
                words.add(w)
    return words


COMMON_WORDS_FILE = str(Path(__file__).parent / "common_words.txt")


# ── HTML / text cleaning ──

TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
NON_ALPHA_RE = re.compile(r"[^a-z0-9#+./ \-]")
URL_RE = re.compile(r"https?://\S+|www\.\S+|//\S+")
TRAILING_PUNCT_RE = re.compile(r"[.,;:!?/]+$")


def strip_html(html: str) -> str:
    text = TAG_RE.sub(" ", html)
    text = unescape(text)
    text = URL_RE.sub(" ", text)
    return WHITESPACE_RE.sub(" ", text).strip()


def tokenize(text: str) -> list[str]:
    text = text.lower()
    text = NON_ALPHA_RE.sub(" ", text)
    text = WHITESPACE_RE.sub(" ", text).strip()
    tokens = []
    for w in text.split():
        w = TRAILING_PUNCT_RE.sub("", w)
        if w and w not in STOP_WORDS and len(w) > 1 and not w.isdigit():
            tokens.append(w)
    return tokens


def extract_ngrams(tokens: list[str], max_n: int = 3) -> set[str]:
    """Extract unique n-grams (1 to max_n) from a token list."""
    ngrams: set[str] = set()
    for n in range(1, max_n + 1):
        for i in range(len(tokens) - n + 1):
            gram = " ".join(tokens[i : i + n])
            # Skip n-grams that start or end with a stop-ish short word
            parts = gram.split()
            if len(parts) > 1 and (len(parts[0]) <= 2 or len(parts[-1]) <= 2):
                continue
            ngrams.add(gram)
    return ngrams


# ── Main ──


async def main(top_n: int, out_file: str | None) -> None:
    # Load common English words and merge into stop words
    common = load_common_words(COMMON_WORDS_FILE, limit=3000)
    all_stop = STOP_WORDS | common
    print(
        f"Stop words: {len(STOP_WORDS)} manual + {len(common)} common = {len(all_stop)} total",
        file=sys.stderr,
    )

    # Override the module-level tokenize to use expanded stop words
    def tokenize_with_common(text: str) -> list[str]:
        text = text.lower()
        text = NON_ALPHA_RE.sub(" ", text)
        text = WHITESPACE_RE.sub(" ", text).strip()
        tokens = []
        for w in text.split():
            w = TRAILING_PUNCT_RE.sub("", w)
            if w and w not in all_stop and len(w) > 1 and not w.isdigit():
                tokens.append(w)
        return tokens

    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=5)
    assert pool is not None

    print("Fetching postings...", file=sys.stderr)
    rows = await pool.fetch(
        """
        SELECT jp.id, jp.company_id, jp.title, jp.description, jp.employment_type
        FROM job_posting jp
        WHERE jp.status = 'active'
        """
    )
    print(f"Fetched {len(rows)} active postings.", file=sys.stderr)

    # Group postings by company
    company_postings: dict[str, list] = {}
    for row in rows:
        cid = str(row["company_id"])
        if cid not in company_postings:
            company_postings[cid] = []
        company_postings[cid].append(row)

    num_companies = len(company_postings)
    print(f"Across {num_companies} companies.", file=sys.stderr)

    # ── Phase 1: Detect boilerplate ──
    # For companies with >= 50 postings, any n-gram in >70% of their postings
    # is considered boilerplate and blacklisted globally.
    BOILERPLATE_MIN_POSTINGS = 50
    BOILERPLATE_THRESHOLD = 0.70
    boilerplate: set[str] = set()

    large_companies = {
        cid: posts
        for cid, posts in company_postings.items()
        if len(posts) >= BOILERPLATE_MIN_POSTINGS
    }
    print(
        f"Detecting boilerplate from {len(large_companies)} large companies "
        f"(>= {BOILERPLATE_MIN_POSTINGS} postings)...",
        file=sys.stderr,
    )

    for cid, posts in large_companies.items():
        ngram_count: Counter[str] = Counter()
        for row in posts:
            parts: list[str] = []
            if row["title"]:
                parts.append(row["title"])
            if row["description"]:
                parts.append(strip_html(row["description"]))
            if row["employment_type"]:
                parts.append(row["employment_type"])
            tokens = tokenize_with_common(" ".join(parts))
            ngram_count.update(extract_ngrams(tokens, max_n=3))

        threshold = len(posts) * BOILERPLATE_THRESHOLD
        company_boilerplate = {g for g, c in ngram_count.items() if c >= threshold}
        boilerplate |= company_boilerplate

    print(f"Blacklisted {len(boilerplate)} boilerplate n-grams.", file=sys.stderr)

    # ── Phase 2: Count keywords (excluding boilerplate) ──
    company_freq: dict[str, set[str]] = {}
    doc_freq: Counter[str] = Counter()
    total = len(rows)

    for i, row in enumerate(rows):
        parts: list[str] = []
        if row["title"]:
            parts.append(row["title"])
        if row["description"]:
            parts.append(strip_html(row["description"]))
        if row["employment_type"]:
            parts.append(row["employment_type"])

        text = " ".join(parts)
        tokens = tokenize_with_common(text)
        ngrams = extract_ngrams(tokens, max_n=3) - boilerplate
        doc_freq.update(ngrams)

        cid = str(row["company_id"])
        for gram in ngrams:
            if gram not in company_freq:
                company_freq[gram] = set()
            company_freq[gram].add(cid)

        if (i + 1) % 5000 == 0:
            print(f"  processed {i + 1}/{total}...", file=sys.stderr)

    await pool.close()

    # Rank by company frequency (how many companies use this keyword)
    # Filter: must appear in >= 3 companies, skip if too generic
    MIN_COMPANIES = 3
    MAX_COMPANY_PCT = 80
    results = []
    for gram, companies in company_freq.items():
        nc = len(companies)
        pct = 100.0 * nc / num_companies
        if nc >= MIN_COMPANIES and pct <= MAX_COMPANY_PCT:
            results.append((gram, nc, doc_freq[gram]))

    # Sort by company count desc, then doc count desc
    results.sort(key=lambda x: (-x[1], -x[2]))

    # Print results
    out = open(out_file, "w") if out_file else sys.stdout
    out.write(f"{'keyword':<50} {'companies':>9} {'docs':>6} {'% companies':>12}\n")
    out.write("-" * 79 + "\n")
    for gram, nc, dc in results[:top_n]:
        pct = 100.0 * nc / num_companies
        out.write(f"{gram:<50} {nc:>9} {dc:>6} {pct:>11.1f}%\n")

    if out_file:
        out.close()
        print(f"\nWrote {min(top_n, len(results))} keywords to {out_file}", file=sys.stderr)
    else:
        print(f"\n({len(results)} total keywords with doc_freq >= {MIN_DOC_FREQ})", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract keyword n-grams from job postings")
    parser.add_argument("--top", type=int, default=500, help="Number of top keywords to output")
    parser.add_argument("--out", type=str, default=None, help="Output file (default: stdout)")
    args = parser.parse_args()

    asyncio.run(main(args.top, args.out))
