# ADR-005: Lingui Babel Macro Pipeline

Status: implemented

Date: 2026-07-07

## Context

The web app uses Lingui macros for localized UI copy. Translation quality rules
require explicit IDs and translator comments, and CI/build workflows run Lingui
extract/compile steps. Macro expansion is part of the build contract, not just
developer ergonomics.

Next.js uses SWC for most transforms, but the repo keeps a Babel config for
Lingui macro handling:

- `apps/web/babel.config.json` uses `next/babel`.
- `apps/web/babel.config.json` installs
  `@lingui/babel-plugin-lingui-macro`.
- `apps/web/lingui.config.ts` defines the extraction catalog inputs.

## Decision

Keep Lingui macro transformation in the Babel pipeline. Do not replace it with
an SWC-only build path unless a focused migration proves that extraction,
compilation, server rendering, client rendering, and CI all preserve the same
macro semantics.

The Babel config is therefore intentional. Removing it to "simplify" the Next
configuration is a behavior change.

## Consequences

- Next build upgrades must verify Lingui macro extraction and runtime rendering.
- Babel config changes need focused i18n validation, not just TypeScript
  success.
- New UI copy should continue using Lingui macros with explicit IDs and
  comments.
- If Lingui ships a proven SWC-compatible macro path for this app's Next
  version, replace this ADR with a migration ADR that records the evidence.

## References

- [Web i18n guidelines](../../apps/web/docs/i18n.md)
- [`apps/web/babel.config.json`](../../apps/web/babel.config.json)
- [`apps/web/lingui.config.ts`](../../apps/web/lingui.config.ts)
- [`apps/web/src/components/providers/LinguiProvider.tsx`](../../apps/web/src/components/providers/LinguiProvider.tsx)
