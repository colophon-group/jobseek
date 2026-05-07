import type { ReactNode } from "react";
import { vi } from "vitest";

/**
 * Mocks for the full Lingui surface that component tests typically
 * need. Side-effect import: register at the top of a test file (above
 * imports of components that use Lingui hooks):
 *
 *     import "@/test-utils/lingui-mock";
 *
 * Why side-effect: `vi.mock` is hoisted to the top of its containing
 * module. Applying mocks via a helper module (rather than inline in
 * each test) lets the next contributor add a Lingui-aware test by
 * adding one import, instead of copying the boilerplate from prior
 * tests where it has already drifted slightly between sites.
 *
 * Translation behavior: every macro/hook returns the descriptor's
 * `message` (or its `id` as a fallback). That's enough for every
 * assertion that just compares against the rendered text — none of
 * the suites care about pluralization or interpolation today.
 *
 * Filed as #2814 follow-up to #2812.
 */

type MessageLike = { message?: string; id?: string } | string;

const fromMessage = (input: MessageLike): string =>
  typeof input === "string" ? input : (input.message ?? input.id ?? "");

vi.mock("@lingui/react", () => ({
  useLingui: () => ({ _: fromMessage }),
}));

vi.mock("@lingui/react/macro", () => ({
  useLingui: () => ({ t: fromMessage }),
  Trans: ({ children }: { children: ReactNode }) => children,
}));

vi.mock("@lingui/core/macro", () => ({
  msg: (m: MessageLike) => m,
}));
