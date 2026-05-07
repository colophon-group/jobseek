#!/usr/bin/env bash
# check-i18n-coverage.sh — fail the commit if any non-source-locale
# Lingui catalog has untranslated entries. Source locale (en) gets its
# msgstr filled by `pnpm extract` from the macro `message` argument,
# so we only check de/fr/it.
#
# What "untranslated" means: a `msgid "<key>"` followed immediately by
# `msgstr ""`. The PO file header is `msgid ""\nmsgstr ""` — excluded
# because its msgid value is empty (the [^"] filter catches it).
#
# When this fires:
#   1. You added a `<Trans>` / `t({...})` / `i18n._({...})` call in
#      source code. Lingui extracted it; the en.po got the message
#      filled in; de/fr/it.po got an empty msgstr waiting for you.
#   2. Open `apps/web/locales/{de,fr,it}.po`, find the listed key,
#      fill in `msgstr "..."` with the translation, then re-run the
#      commit.
#   3. If you removed strings, `pnpm --dir apps/web extract` will
#      drop the orphaned entries — re-run before commit if you see
#      stale keys.
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
  exit 1
fi

echo "i18n-coverage: all locales (de, fr, it) fully translated ✓"
exit 0
