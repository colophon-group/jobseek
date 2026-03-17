#!/usr/bin/env python3
"""
Extract language autonyms and flag mappings from CLDR data.

Reads cldr-localenames-full (npm devDependency) and writes a static TypeScript
file at src/lib/job-languages.ts containing every language that has an autonym
in CLDR, paired with its primary country flag ISO code.

Usage:
    python3 scripts/generate-language-data.py
"""

import json
import os
import sys

CLDR_BASE = os.path.join(
    os.path.dirname(__file__), "..", "node_modules", "cldr-localenames-full", "main"
)
FLAGS_DIR = os.path.join(os.path.dirname(__file__), "..", "public", "flags")
OUT_FILE = os.path.join(os.path.dirname(__file__), "..", "src", "lib", "job-languages.ts")

# ── Language → primary country flag (ISO 3166-1 alpha-2, lowercase) ──────────
# Maps the language's BCP-47 code to the country whose flag most users
# would associate with that language.  None = no flag available.
LANG_TO_COUNTRY: dict[str, str | None] = {
    "aa": "dj", "ab": "ge", "af": "za", "ak": "gh", "am": "et",
    "an": "es", "ar": "sa", "as": "in", "ast": "es", "az": "az",
    "ba": "ru", "bal": "pk", "bas": "cm", "be": "by", "bem": "zm",
    "bez": "tz", "bg": "bg", "bgc": "in", "bho": "in", "bm": "ml",
    "bn": "bd", "bo": "cn", "br": "fr", "brx": "in", "bs": "ba",
    "ca": "es", "ccp": "bd", "ce": "ru", "ceb": "ph", "ckb": "iq",
    "co": "fr", "cs": "cz", "cu": "bg", "cy": "gb", "da": "dk",
    "de": "de", "doi": "in", "dsb": "de", "dua": "cm", "dz": "bt",
    "ee": "gh", "el": "gr", "en": "gb", "es": "es", "et": "ee",
    "eu": "es", "ewo": "cm", "fa": "ir", "ff": "sn", "fi": "fi",
    "fil": "ph", "fo": "fo", "fr": "fr", "fur": "it", "fy": "nl",
    "ga": "ie", "gd": "gb", "gl": "es", "gn": "py", "gsw": "ch",
    "gu": "in", "guz": "ke", "gv": "im", "ha": "ng", "haw": "us",
    "he": "il", "hi": "in", "hr": "hr", "hsb": "de", "hu": "hu",
    "hy": "am", "id": "id", "ig": "ng", "ii": "cn", "is": "is",
    "it": "it", "ja": "jp", "jmc": "tz", "jv": "id", "ka": "ge",
    "kab": "dz", "kam": "ke", "kde": "tz", "kea": "cv", "kgp": "br",
    "khq": "ml", "ki": "ke", "kk": "kz", "kl": "gl", "km": "kh",
    "kn": "in", "ko": "kr", "kok": "in", "ks": "in", "ksb": "tz",
    "ksf": "cm", "ku": "iq", "kw": "gb", "ky": "kg", "la": "va",
    "lag": "tz", "lb": "lu", "lg": "ug", "li": "nl", "ln": "cd",
    "lo": "la", "lrc": "ir", "lt": "lt", "lu": "cd", "luo": "ke",
    "luy": "ke", "lv": "lv", "mai": "in", "mas": "ke", "mer": "ke",
    "mfe": "mu", "mg": "mg", "mgh": "mz", "mgo": "cm", "mi": "nz",
    "mk": "mk", "ml": "in", "mn": "mn", "mni": "in", "mr": "in",
    "ms": "my", "mt": "mt", "mua": "cm", "my": "mm", "mzn": "ir",
    "naq": "na", "nb": "no", "nd": "zw", "nds": "de", "ne": "np",
    "nl": "nl", "nmg": "cm", "nn": "no", "nnh": "cm", "no": "no",
    "nus": "ss", "ny": "mw", "nyn": "ug", "oc": "fr", "om": "et",
    "or": "in", "os": "ge", "pa": "pk", "pcm": "ng", "pl": "pl",
    "ps": "af", "pt": "pt", "qu": "pe", "raj": "in", "rm": "ch",
    "rn": "bi", "ro": "ro", "rof": "tz", "ru": "ru", "rw": "rw",
    "rwk": "tz", "sa": "in", "sah": "ru", "saq": "ke", "sat": "in",
    "sbp": "tz", "sc": "it", "sd": "pk", "se": "no", "ses": "ml",
    "sg": "cf", "shi": "ma", "si": "lk", "sk": "sk", "sl": "si",
    "sm": "ws", "smn": "fi", "sn": "zw", "so": "so", "sq": "al",
    "sr": "rs", "ss": "sz", "st": "za", "su": "id", "sv": "se",
    "sw": "tz", "szl": "pl", "ta": "in", "te": "in", "teo": "ug",
    "tg": "tj", "th": "th", "ti": "er", "tk": "tm", "to": "to",
    "tr": "tr", "ts": "za", "tt": "ru", "tw": "gh", "twq": "ne",
    "tzm": "ma", "ug": "cn", "uk": "ua", "ur": "pk", "uz": "uz",
    "vai": "lr", "ve": "za", "vi": "vn", "vun": "tz", "wae": "ch",
    "wa": "be", "wo": "sn", "xh": "za", "xog": "ug", "yav": "cm",
    "yi": "il", "yo": "ng", "yrl": "br", "za": "cn", "zgh": "ma",
    "zh": "cn", "zu": "za",
}


def get_autonym(code: str) -> str | None:
    """Read the language's name from its own locale file."""
    locale_dir = os.path.join(CLDR_BASE, code)
    lang_file = os.path.join(locale_dir, "languages.json")
    if not os.path.isfile(lang_file):
        return None
    with open(lang_file) as f:
        data = json.load(f)
    try:
        return data["main"][code]["localeDisplayNames"]["languages"].get(code)
    except KeyError:
        return None


def main() -> None:
    if not os.path.isdir(CLDR_BASE):
        print("ERROR: cldr-localenames-full not found. Run: pnpm add -D cldr-localenames-full", file=sys.stderr)
        sys.exit(1)

    available_flags = {
        f.removesuffix(".svg") for f in os.listdir(FLAGS_DIR) if f.endswith(".svg")
    }

    # Get all base language codes from the English locale
    en_file = os.path.join(CLDR_BASE, "en", "languages.json")
    with open(en_file) as f:
        en_data = json.load(f)
    en_langs: dict[str, str] = en_data["main"]["en"]["localeDisplayNames"]["languages"]

    # Collect languages: only base codes (no region/variant suffixes)
    languages: list[dict[str, str | None]] = []
    seen_codes: set[str] = set()

    for code in sorted(en_langs.keys()):
        # Skip regional variants (en-US), alt forms (az-alt-short)
        if "-" in code:
            continue
        if code in seen_codes:
            continue
        seen_codes.add(code)

        en_name = en_langs[code]
        autonym = get_autonym(code)
        if not autonym:
            # Use English name as fallback label
            autonym = en_name

        flag = LANG_TO_COUNTRY.get(code)
        if flag and flag not in available_flags:
            flag = None

        languages.append({
            "code": code,
            "label": autonym,
            "flag": flag,
        })

    # Write TypeScript file
    lines = [
        "// Auto-generated from CLDR data. Do not edit manually.",
        "// Regenerate: python3 scripts/generate-language-data.py",
        "",
        "export interface JobLanguage {",
        "  code: string;",
        "  /** Language name in its own language (autonym). */",
        "  label: string;",
        "  /** ISO 3166-1 alpha-2 country code for the flag, or null. */",
        "  flag: string | null;",
        "}",
        "",
        f"export const allLanguages: JobLanguage[] = {json.dumps(languages, ensure_ascii=False, indent=2)};",
        "",
        "const _langMap = new Map(allLanguages.map((l) => [l.code, l]));",
        "",
        "export function getLanguage(code: string): JobLanguage | undefined {",
        "  return _langMap.get(code);",
        "}",
        "",
    ]

    with open(OUT_FILE, "w") as f:
        f.write("\n".join(lines))

    print(f"Wrote {len(languages)} languages to {OUT_FILE}")


if __name__ == "__main__":
    main()
