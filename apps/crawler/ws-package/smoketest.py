"""Smoke test for the ``jobseek-crawler-setup`` (ws CLI) wheel.

Runs after the wheel has been built and installed in a clean venv. The
goal is to catch the class of bug where someone adds a top-level import
to a file that ships with this slim wheel but pulls in a module the
wheel does *not* ship — typically ``src.metrics`` (which would require
``prometheus_client``) or any of the DB/queue runtime modules.

The ``ws`` CLI loads many modules lazily inside command callbacks, so
``ws --help`` alone does not exercise the import graph. This script
explicitly imports every module that ``ws probe monitor`` /
``ws run monitor`` / ``ws probe scraper`` triggers at runtime, plus
every module the wheel ships in ``src/shared/`` and ``src/core/``.

If a future change introduces a top-level import that breaks the slim
build, this script fails fast in CI before the wheel is published.
"""

from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

# Modules that the workspace CLI loads lazily inside command callbacks.
# Keep this list in sync with ``apps/crawler/src/workspace/commands/`` —
# specifically ``crawl.py``, ``career_discover.py``, and ``config.py``.
LAZY_RUNTIME_IMPORTS: tuple[str, ...] = (
    # ws probe monitor / ws run monitor — triggers full monitor registry
    "src.core.monitors",
    "src.core.monitors.api_sniffer",
    "src.core.monitor",
    # ws probe scraper / ws run scraper — triggers full scraper registry
    "src.core.scrapers",
    # ws probe deep / ws run monitor with browser
    "src.shared.browser",
    "src.shared.api_sniff",
    "src.shared.nextdata",
)

# Modules in the wheel that have known optional deps and should NOT be
# imported by the smoke test (they'd fail with the slim deps and that's
# expected — they only run inside the full crawler runtime).
SKIP_MODULES: frozenset[str] = frozenset(
    {
        # __main__ entry points execute their CLI on import — not a smoke
        # signal, would just trigger click and exit non-zero.
        "src.workspace.__main__",
        # selectolax is not in ws-package deps; only processing/ uses it
        "src.shared.html_normalize",
        # langdetect requires fast_langdetect (heavy native dep)
        "src.shared.langdetect",
        # asyncpg-backed taxonomy resolvers
        "src.core.location_resolve",
        "src.core.occupation_resolve",
        "src.core.seniority_resolve",
        "src.core.technology_resolve",
        "src.core.experience_extract",
        # PDF / pypdf
        "src.core.scrapers.pdf",
        # src.processing.board imports src.redis_queue (server-side runtime
        # only — not used by `ws run scraper` or any agent flow).
        "src.processing.board",
        # enrich providers — optional LLM SDKs imported lazily inside funcs
        "src.core.enrich",
        "src.core.enrich.batch",
        "src.core.enrich.company",
        "src.core.enrich.job",
        "src.core.enrich.taxonomy",
        "src.core.enrich.providers",
        "src.core.enrich.providers.openai",
        "src.core.enrich.providers.anthropic",
        "src.core.enrich.providers.gemini",
    }
)


def discover_shipped_modules() -> list[str]:
    """Return every shipped ``src.*`` module name by walking the installed wheel."""
    import src  # noqa: F401 — namespace package

    src_root: Path | None = None
    # ``src`` is a namespace package; iterate its locations.
    for location in src.__path__:  # type: ignore[attr-defined]
        path = Path(location)
        if (path / "shared" / "browser.py").exists():
            src_root = path
            break
    if src_root is None:
        raise SystemExit("could not locate installed src/ namespace package")

    modules: list[str] = []
    for py in sorted(src_root.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        rel = py.relative_to(src_root.parent).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts.pop()
        modname = ".".join(parts)
        if modname and modname not in SKIP_MODULES:
            modules.append(modname)
    return modules


def main() -> int:
    failures: list[tuple[str, str]] = []

    # First the explicit lazy-runtime imports — these are the codepaths
    # that ``ws probe monitor`` actually triggers.
    print("=== lazy-runtime imports ===")
    for modname in LAZY_RUNTIME_IMPORTS:
        try:
            importlib.import_module(modname)
            print(f"OK   {modname}")
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            failures.append((modname, f"{type(exc).__name__}: {exc}"))
            print(f"FAIL {modname}")

    # Then sweep every other shipped module so a future regression in an
    # unrelated file is caught even if the explicit list above is stale.
    print("\n=== shipped modules sweep ===")
    for modname in discover_shipped_modules():
        if modname in {m for m in LAZY_RUNTIME_IMPORTS}:
            continue
        try:
            importlib.import_module(modname)
            print(f"OK   {modname}")
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            failures.append((modname, f"{type(exc).__name__}: {exc}"))
            print(f"FAIL {modname}")

    if failures:
        print(f"\n{len(failures)} import failure(s):", file=sys.stderr)
        for modname, err in failures:
            print(f"  {modname}: {err}", file=sys.stderr)
        print(
            "\nThe slim ws-package wheel is missing a module that one of the "
            "files above imports at top level. Either:\n"
            "  1. Make the import optional (try/except ImportError + no-op stub),\n"
            "  2. Move the import inside the function that uses it (lazy), or\n"
            "  3. Add the module to apps/crawler/ws-package/pyproject.toml's\n"
            "     force-include section (and add its runtime deps).",
            file=sys.stderr,
        )
        return 1

    print("\nAll modules imported cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
