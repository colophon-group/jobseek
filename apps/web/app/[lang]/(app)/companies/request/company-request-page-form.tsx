"use client";

/**
 * Client form for the `/[lang]/companies/request` landing page.
 *
 * Mirrors the inline forms in `progress` and `explore` (single freeform
 * `input` field, server action + parallel agent-run call, render
 * `RequestCompanySuccess` on success). The only addition is the optional
 * `defaultName` / `defaultWebsite` props used to prefill the input from the
 * page's `?name=` / `?website=` query string.
 *
 * The legacy single-field `input` is URL-aware (see `parseRequestInput`):
 *  - When both name and website are provided, we prefill with the URL so the
 *    agent-run path fires automatically.
 *  - When only name is provided, we prefill with the raw name; the user can
 *    add a URL or submit as-is (legacy GH-issue path).
 *
 * Out of scope: changes to the shared `RequestCompanyPrompt` component.
 *
 * @throws never
 */
export interface CompanyRequestPageFormProps {
  /** Locale code from the route params (used for the hidden `locale` field). */
  locale: string;
  /** Optional prefill from `?name=` query param. */
  defaultName?: string;
  /** Optional prefill from `?website=` query param. */
  defaultWebsite?: string;
}

export function CompanyRequestPageForm(_: CompanyRequestPageFormProps) {
  throw new Error("not implemented");
}
