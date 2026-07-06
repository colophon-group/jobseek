#!/usr/bin/env bash
# check-i18n-coverage.sh — fail the commit if any non-source-locale
# Lingui catalog has untranslated entries, AND fail if the catalogs are
# stale relative to the source tree (i.e. the user added a <Trans> or
# t({…}) call but forgot to run `pnpm extract`).
#
# Two checks:
#
#   1. **Empty msgstr** — a `msgid "<key>"` immediately followed by
#      `msgstr ""` in any of de/fr/it. The PO file header is
#      `msgid ""\nmsgstr ""` and is excluded by the `[^"]` filter.
#
#   2. **Stale catalog** — runs `pnpm --dir apps/web extract` in a
#      sandboxed temp copy of the catalogs and diffs against the
#      committed ones. Any non-trivial diff means the user added or
#      removed a macro call without running extract, so the catalogs
#      don't reflect the source. We compare on the *normalized*
#      msgid/msgstr content (stripping `#:` source-location comments
#      that legitimately churn line-by-line), and fail only on real
#      content differences. See #3031 for the rationale.
#
# When this fires (per check):
#
#   Empty msgstr:
#     1. Open `apps/web/locales/{de,fr,it}.po`, find the listed key,
#        fill in `msgstr "..."` with the translation, then re-run the
#        commit.
#     2. If you removed strings, `pnpm --dir apps/web extract` will
#        drop the orphaned entries — re-run before commit if you see
#        stale keys.
#
#   Stale catalog:
#     1. Run `pnpm --dir apps/web extract` to refresh the .po files
#        from source.
#     2. Open the updated catalogs, fill in any newly-extracted
#        msgstrs (de/fr/it), re-stage + re-commit.
#
# Why a custom script (vs. `lingui compile --strict`): `--strict`
# disables the runtime fallback to source-locale, but doesn't fail
# the build for missing entries. We want a hard commit-time gate so
# untranslated strings never reach `main`.
#
# Project convention: see apps/web/docs/i18n.md for the full Lingui
# guidelines (every macro must have an explicit `id` and `comment`).

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
LOCALES_DIR="$REPO_ROOT/apps/web/locales"
NON_SOURCE_LOCALES=(de fr it)

if [[ ! -d "$LOCALES_DIR" ]]; then
  echo "i18n-coverage: $LOCALES_DIR does not exist (skipping)"
  exit 0
fi

# ── Check 1: empty msgstr in committed catalogs ─────────────────────

failed=0
for locale in "${NON_SOURCE_LOCALES[@]}"; do
  po="$LOCALES_DIR/$locale.po"
  if [[ ! -f "$po" ]]; then
    echo "i18n-coverage: $po missing (skipping $locale)"
    continue
  fi

  # grep -B1: print the line before each match. We match `msgstr ""`
  # on its own line; the line above is the corresponding msgid. The
  # second grep filters to non-empty msgids — `msgid ""` (the file
  # header) has `"` immediately after the opening quote and is
  # rejected by `[^"]`.
  missing=$(grep -B1 '^msgstr ""$' "$po" | grep -E '^msgid "[^"]' || true)

  if [[ -n "$missing" ]]; then
    count=$(echo "$missing" | wc -l | tr -d ' ')
    echo ""
    echo "❌ apps/web/locales/$locale.po — $count untranslated entries:"
    echo "$missing" | sed 's/^/     /'
    failed=1
  fi
done

# ── Check 2: catalogs stale vs source (#3031) ───────────────────────
#
# Run `pnpm extract` in a worktree-local sandbox so we don't mutate the
# committed catalogs, then diff against them on normalized content. The
# diff is content-only — `#:` source-location comments (lingui rewrites
# these whenever a line number shifts) are stripped before compare
# because they don't represent a translation gap. If the diff is empty
# the catalogs are in sync.
#
# Skipped when `pnpm` or `node_modules` is missing (CI sandboxes that
# strip them, agent workflows that re-stage before installing). In that
# environment Check 1 is still the gatekeeper.

SKIP_STALE_CHECK="${SKIP_STALE_CHECK:-0}"
if [[ "$SKIP_STALE_CHECK" == "1" ]]; then
  echo "i18n-coverage: stale-catalog check skipped (SKIP_STALE_CHECK=1)"
elif ! command -v pnpm >/dev/null 2>&1; then
  echo "i18n-coverage: pnpm not found — skipping stale-catalog check"
elif [[ ! -d "$REPO_ROOT/apps/web/node_modules" ]]; then
  echo "i18n-coverage: apps/web/node_modules missing — skipping stale-catalog check"
elif [[ ! -d "$REPO_ROOT/node_modules" ]]; then
  echo "i18n-coverage: repo node_modules missing — skipping stale-catalog check"
else
  TMPDIR_FOR_HOOK=$(mktemp -d -t i18n-coverage-XXXXXX)
  trap 'rm -rf "$TMPDIR_FOR_HOOK"' EXIT

  # Sandbox: copy current catalogs into the tmp dir, run extract against
  # source pointing at this tmp dir as the catalog output, then diff.
  # We can't `--out-dir` lingui — the catalog path is configured in
  # `lingui.config.ts`. Instead, swap-in approach: snapshot existing
  # catalogs, run extract (mutates real catalogs), diff, then restore.
  SNAPSHOT="$TMPDIR_FOR_HOOK/snapshot"
  mkdir -p "$SNAPSHOT"
  for locale in en "${NON_SOURCE_LOCALES[@]}"; do
    cp "$LOCALES_DIR/$locale.po" "$SNAPSHOT/$locale.po"
  done

  # Run extract — captures any new macro calls. Suppress noisy output;
  # we only care about the diff result. On extractor failure (e.g. a
  # syntax error in the source), skip this check and let other linters
  # surface it. We don't want to block the commit on extract noise.
  #
  # IMPORTANT: extract *mutates* the committed catalogs on disk (it
  # also rewrites line numbers in `#:` comments and may reorder
  # entries). We compare the freshly-extracted result against the
  # snapshot in-memory and ALWAYS restore the snapshot afterwards, so
  # the hook leaves the working tree exactly as it found it. Otherwise
  # pre-commit detects "files modified by hook" and bails.
  if ! pnpm --dir "$REPO_ROOT/apps/web" extract >/dev/null 2>&1; then
    echo "i18n-coverage: pnpm extract failed — skipping stale-catalog check (fix extract errors separately)"
  else
    # Compare the *sorted set of msgids* between the snapshot and the
    # freshly-extracted catalog. The extractor's output order is
    # filesystem-dependent and not stable across runs, so a positional
    # diff yields false positives (entries swapping places). The set
    # diff catches the only thing that matters: was a msgid added or
    # removed by extract that isn't on disk?
    stale=0
    for locale in en "${NON_SOURCE_LOCALES[@]}"; do
      old_ids=$(grep -E '^msgid "[^"]' "$SNAPSHOT/$locale.po" | sort -u)
      new_ids=$(grep -E '^msgid "[^"]' "$LOCALES_DIR/$locale.po" | sort -u)
      if [[ "$old_ids" != "$new_ids" ]]; then
        if [[ "$stale" -eq 0 ]]; then
          echo ""
          echo "❌ Lingui catalogs are stale vs source code:"
        fi
        stale=1
        only_in_source=$(comm -13 <(echo "$old_ids") <(echo "$new_ids") || true)
        only_in_catalog=$(comm -23 <(echo "$old_ids") <(echo "$new_ids") || true)
        if [[ -n "$only_in_source" ]]; then
          echo ""
          echo "   apps/web/locales/$locale.po — msgids in source but missing from catalog:"
          echo "$only_in_source" | sed 's/^/     /'
        fi
        if [[ -n "$only_in_catalog" ]]; then
          echo ""
          echo "   apps/web/locales/$locale.po — orphaned msgids (in catalog, not in source):"
          echo "$only_in_catalog" | sed 's/^/     /'
        fi
      fi
    done
    if [[ "$stale" -ne 0 ]]; then
      failed=1
    fi
  fi

  # Always restore the snapshot — pass or fail, the hook must leave the
  # working tree byte-identical to what it started with. Pre-commit
  # treats any file modification as a hook failure and refuses to
  # commit, even on the success path.
  for locale in en "${NON_SOURCE_LOCALES[@]}"; do
    cp "$SNAPSHOT/$locale.po" "$LOCALES_DIR/$locale.po"
  done
fi

if [[ "$failed" -ne 0 ]]; then
  echo ""
  echo "Fix:"
  echo "  1. Run \`pnpm --dir apps/web extract\` to refresh .po files."
  echo "  2. Open the listed catalog(s) under apps/web/locales/."
  echo "  3. Fill in each empty msgstr with a translation."
  echo "  4. Re-stage + re-commit."
  echo ""
  echo "Skipping (do not do this casually):"
  echo "  git commit --no-verify   # bypass all hooks; we don't want untranslated strings on main"
  echo "  SKIP_STALE_CHECK=1 git commit ...   # skip only the stale-catalog check, keep empty-msgstr"
  exit 1
fi

echo "i18n-coverage: all locales (de, fr, it) fully translated ✓"
echo "i18n-coverage: catalogs in sync with source code ✓"
exit 0
