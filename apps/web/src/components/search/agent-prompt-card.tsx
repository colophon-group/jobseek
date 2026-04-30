"use client";

/**
 * Result card shown when a user successfully kicks off a Murmur run via
 * `POST /api/web/companies/request`. Pure presentational component: takes
 * already-localized strings as props, no lingui hooks. The parent component
 * resolves the catalog entries and passes plain strings down so this card is
 * trivially testable without an i18n provider.
 *
 * The card renders TWO numbered sections (jobseek#2809):
 *   1. "Install MCP" — the `claude mcp add ...` one-liner the user runs once
 *      in their terminal to register Murmur with Claude Code. The bearer
 *      token in this command is the literal placeholder
 *      `<token-from-jobseek-team>`; users get a real token at demo time.
 *   2. "Run the prompt" — the natural-language prompt the user pastes into
 *      Claude Code AFTER step 1.
 *
 * Each section has its own copy button: clicking section 1's button copies
 * ONLY `installCommand`; section 2's button copies ONLY `promptText`. A
 * caveat below the install block reminds the user to swap the placeholder
 * token before running the install command.
 *
 * Accessibility:
 *  - Each block is wrapped in a `role="region"` with its own `aria-label`.
 *  - Both copy buttons are real `<button>`s so they're keyboard-reachable
 *    (Tab + Enter/Space).
 *  - A single shared `aria-live="polite"` toast announces the most recent
 *    copy success/failure for either button.
 *
 * @see colophon-group/jobseek#2802
 * @see colophon-group/jobseek#2809
 */
import { useState } from "react";
import { Copy, Check } from "lucide-react";

export interface AgentPromptCardProps {
  /** The exact `company_name` the user submitted (verbatim, no trimming). */
  companyName: string;
  /** The opaque run id returned by Murmur via `startRun`. */
  runId: string;
  /**
   * `claude mcp add ...` one-liner with the literal placeholder
   * `<token-from-jobseek-team>` (no real token is ever included).
   */
  installCommand: string;
  /**
   * Natural-language prompt the user pastes into Claude Code after running
   * `installCommand`. Mentions the company, website, run id, and `pull_task`.
   */
  promptText: string;
  /**
   * All visible labels in one bag so the parent can resolve lingui catalog
   * entries up-front. Keeps this component free of any i18n dependency.
   */
  labels: {
    /** "We're working on adding {{company_name}}" — body interpolates `companyName`. */
    headingPrefix: string;
    /** "You can speed this up by asking your AI agent to complete it via Murmur." */
    body: string;
    /** "1. Install MCP" — heading on the install-command section. */
    installHeading: string;
    /** "2. Run the prompt" — heading on the prompt-text section. */
    runHeading: string;
    /**
     * Footnote about needing a token from the jobseek team. Should mention
     * the literal `<token-from-jobseek-team>` placeholder so users know what
     * to swap.
     */
    tokenCaveat: string;
    /** "Copy command" — accessible label on the install-section copy button. */
    copyInstallButton: string;
    /** "Copy prompt" — accessible label on the prompt-section copy button. */
    copyPromptButton: string;
    /** "Copied" — shown briefly after a successful clipboard write. */
    copied: string;
    /** "Copy failed" — shown when `navigator.clipboard.writeText` rejects. */
    copyFailed: string;
    /** "Run id" — the small label preceding the selectable run id text. */
    runIdLabel: string;
    /** Aria label on the `role="region"` containing the install block. */
    installRegionLabel: string;
    /** Aria label on the `role="region"` containing the prompt block. */
    promptRegionLabel: string;
  };
  /**
   * Optional injection for unit tests so we don't have to stub the global
   * `navigator.clipboard`. Defaults to `navigator.clipboard.writeText`.
   */
  writeToClipboard?: (text: string) => Promise<void>;
}

/** Duration of the "Copied" toast confirmation in milliseconds. */
const COPIED_TOAST_MS = 2_000;

export function AgentPromptCard(_props: AgentPromptCardProps) {
  // Suppress unused warnings until the implementation lands; the props
  // contract is what step 4 (interfaces first) defines. Step 6 implements.
  void useState;
  void Copy;
  void Check;
  void COPIED_TOAST_MS;
  throw new Error("not implemented");
}
