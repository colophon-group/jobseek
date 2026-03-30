"""Validate salary + experience extraction against the description cache.

Runs extractors over a sample of real descriptions and prints results
for manual review.  Use --sample N to limit (default 2000).
"""

from __future__ import annotations

import argparse
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.salary_extract import extract_salary, extract_salary_unified
from src.core.experience_extract import extract_experience

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "descriptions_cache")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=2000, help="Number of files to sample")
    parser.add_argument("--show-salary", action="store_true", help="Print salary extractions")
    parser.add_argument(
        "--show-experience", action="store_true", help="Print experience extractions"
    )
    parser.add_argument("--show-all", action="store_true", help="Print all extractions")
    args = parser.parse_args()

    files = [f for f in os.listdir(CACHE_DIR) if f.endswith(".html")]
    random.seed(42)
    sample = random.sample(files, min(args.sample, len(files)))

    salary_hits = 0
    experience_hits = 0
    salary_details: list[tuple[str, object]] = []
    experience_details: list[tuple[str, object]] = []

    for fname in sample:
        path = os.path.join(CACHE_DIR, fname)
        with open(path, encoding="utf-8", errors="replace") as f:
            html = f.read()

        sal = extract_salary_unified(html)
        if sal:
            salary_hits += 1
            salary_details.append((fname, sal))

        exp = extract_experience(html)
        if exp:
            experience_hits += 1
            experience_details.append((fname, exp))

    print(f"Sample size: {len(sample)}")
    print(f"Salary extracted: {salary_hits} ({salary_hits / len(sample) * 100:.1f}%)")
    print(f"Experience extracted: {experience_hits} ({experience_hits / len(sample) * 100:.1f}%)")
    print()

    if args.show_salary or args.show_all:
        print("=== SALARY EXTRACTIONS ===")
        for fname, sal in salary_details[:50]:
            print(f"  {fname}: {sal}")
        if len(salary_details) > 50:
            print(f"  ... and {len(salary_details) - 50} more")
        print()

    if args.show_experience or args.show_all:
        print("=== EXPERIENCE EXTRACTIONS ===")
        for fname, exp in experience_details[:50]:
            print(f"  {fname}: {exp}")
        if len(experience_details) > 50:
            print(f"  ... and {len(experience_details) - 50} more")
        print()


if __name__ == "__main__":
    main()
