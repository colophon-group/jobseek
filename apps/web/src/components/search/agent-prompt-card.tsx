"use client";

/**
 * Result card shown when a user successfully kicks off a Murmur run via
 * `POST /api/web/companies/request`. Pure presentational component: takes
 * already-localized strings as props, no lingui hooks. The parent component
 * resolves the catalog entries and passes plain strings down so this card is
 * trivially testable without an i18n provider.
 *
 * Accessibility:
 *  - The prompt block has `role="region"` + an `aria-label` from the
 *    `promptRegionLabel` prop.
 *  - The copy button is a real `<button>` so it is keyboard-reachable
 *    (Tab + Enter/Space).
 *  - Copy success is announced via an `aria-live="polite"` region that
 *    swaps to the `copied` label briefly.
 *
 * @see colophon-group/jobseek#2802
 */
import { useState } from "react";
import { Copy, Check } from "lucide-react";

export interface AgentPromptCardProps {
  /** The exact `company_name` the user submitted (verbatim, no trimming). */
  companyName: string;
  /** The opaque run id returned by Murmur via `startRun`. */
  runId: string;
  /** Pre-formatted prompt text from `buildAgentPrompt` on the server. */
  agentPrompt: string;
  /**
   * All visible labels in one bag so the parent can resolve lingui catalog
   * entries up-front. Keeps this component free of any i18n dependency.
   */
  labels: {
    /** "We're working on adding {{company_name}}" — body interpolates `companyName`. */
    headingPrefix: string;
    /** "You can speed this up by asking your AI agent to complete it via Murmur." */
    body: string;
    /** "Copy prompt" */
    copyButton: string;
    /** "Copied" — shown briefly after a successful clipboard write. */
    copied: string;
    /** "Copy failed" — shown when `navigator.clipboard.writeText` rejects. */
    copyFailed: string;
    /** "Run id" — the small label preceding the selectable run id text. */
    runIdLabel: string;
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

export function AgentPromptCard({
  companyName,
  runId,
  agentPrompt,
  labels,
  writeToClipboard,
}: AgentPromptCardProps) {
  const [status, setStatus] = useState<"idle" | "copied" | "failed">("idle");

  async function handleCopy() {
    const writer =
      writeToClipboard ??
      (async (text: string) => {
        if (typeof navigator === "undefined" || !navigator.clipboard) {
          throw new Error("clipboard unavailable");
        }
        await navigator.clipboard.writeText(text);
      });
    try {
      await writer(agentPrompt);
      setStatus("copied");
      window.setTimeout(() => setStatus("idle"), COPIED_TOAST_MS);
    } catch {
      setStatus("failed");
      window.setTimeout(() => setStatus("idle"), COPIED_TOAST_MS);
    }
  }

  return (
    <div
      role="status"
      className="mb-4 flex flex-col gap-3 rounded-md border border-success-border bg-success-bg px-4 py-3 text-sm text-success"
    >
      <h3 className="text-base font-semibold">
        {labels.headingPrefix} {companyName}
      </h3>
      <p className="text-sm opacity-90">{labels.body}</p>

      <section
        role="region"
        aria-label={labels.promptRegionLabel}
        className="relative"
      >
        <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md border border-success-border bg-background px-3 py-2 pr-12 text-xs leading-relaxed text-foreground">
          <code>{agentPrompt}</code>
        </pre>
        <button
          type="button"
          onClick={handleCopy}
          className="absolute right-2 top-2 inline-flex items-center gap-1 rounded-md border border-divider bg-surface px-2 py-1 text-xs text-foreground transition-colors hover:bg-border-soft focus:outline-none focus:ring-2 focus:ring-primary cursor-pointer"
          aria-label={labels.copyButton}
        >
          {status === "copied" ? (
            <Check size={12} aria-hidden="true" />
          ) : (
            <Copy size={12} aria-hidden="true" />
          )}
          <span>{labels.copyButton}</span>
        </button>
      </section>

      <p
        aria-live="polite"
        className="min-h-[1em] text-xs"
        data-testid="agent-prompt-card-toast"
      >
        {status === "copied" ? labels.copied : status === "failed" ? labels.copyFailed : ""}
      </p>

      <p className="text-xs opacity-80">
        <span className="opacity-80">{labels.runIdLabel}:</span>{" "}
        <code
          data-testid="agent-prompt-card-run-id"
          className="select-all rounded bg-background px-1.5 py-0.5 font-mono text-foreground"
        >
          {runId}
        </code>
      </p>
    </div>
  );
}
