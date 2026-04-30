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

type CopyStatus = "idle" | "copied" | "failed";

export function AgentPromptCard({
  companyName,
  runId,
  installCommand,
  promptText,
  labels,
  writeToClipboard,
}: AgentPromptCardProps) {
  const [installStatus, setInstallStatus] = useState<CopyStatus>("idle");
  const [promptStatus, setPromptStatus] = useState<CopyStatus>("idle");
  // Tracks which button most recently changed status, so the shared
  // aria-live region can announce only the latest event.
  const [lastTouched, setLastTouched] = useState<"install" | "prompt" | null>(
    null,
  );

  function defaultWriter(text: string): Promise<void> {
    if (typeof navigator === "undefined" || !navigator.clipboard) {
      return Promise.reject(new Error("clipboard unavailable"));
    }
    return navigator.clipboard.writeText(text);
  }

  async function copyBlock(
    text: string,
    setStatus: (s: CopyStatus) => void,
    which: "install" | "prompt",
  ) {
    const writer = writeToClipboard ?? defaultWriter;
    setLastTouched(which);
    try {
      await writer(text);
      setStatus("copied");
      window.setTimeout(() => setStatus("idle"), COPIED_TOAST_MS);
    } catch {
      setStatus("failed");
      window.setTimeout(() => setStatus("idle"), COPIED_TOAST_MS);
    }
  }

  const toastStatus =
    lastTouched === "install"
      ? installStatus
      : lastTouched === "prompt"
        ? promptStatus
        : "idle";
  const toastText =
    toastStatus === "copied"
      ? labels.copied
      : toastStatus === "failed"
        ? labels.copyFailed
        : "";

  return (
    <div
      role="status"
      className="mb-4 flex flex-col gap-3 rounded-md border border-success-border bg-success-bg px-4 py-3 text-sm text-success"
    >
      <h3 className="text-base font-semibold">
        {labels.headingPrefix} {companyName}
      </h3>
      <p className="text-sm opacity-90">{labels.body}</p>

      {/* Section 1: Install MCP */}
      <div className="flex flex-col gap-1.5">
        <h4 className="text-sm font-semibold">{labels.installHeading}</h4>
        <section
          role="region"
          aria-label={labels.installRegionLabel}
          className="relative"
        >
          <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-all rounded-md border border-success-border bg-background px-3 py-2 pr-12 text-xs leading-relaxed text-foreground">
            <code>{installCommand}</code>
          </pre>
          <button
            type="button"
            onClick={() =>
              copyBlock(installCommand, setInstallStatus, "install")
            }
            className="absolute right-2 top-2 inline-flex items-center gap-1 rounded-md border border-divider bg-surface px-2 py-1 text-xs text-foreground transition-colors hover:bg-border-soft focus:outline-none focus:ring-2 focus:ring-primary cursor-pointer"
            aria-label={labels.copyInstallButton}
          >
            {installStatus === "copied" ? (
              <Check size={12} aria-hidden="true" />
            ) : (
              <Copy size={12} aria-hidden="true" />
            )}
            <span>{labels.copyInstallButton}</span>
          </button>
        </section>
        <p className="text-xs opacity-80">{labels.tokenCaveat}</p>
      </div>

      {/* Section 2: Run the prompt */}
      <div className="flex flex-col gap-1.5">
        <h4 className="text-sm font-semibold">{labels.runHeading}</h4>
        <section
          role="region"
          aria-label={labels.promptRegionLabel}
          className="relative"
        >
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md border border-success-border bg-background px-3 py-2 pr-12 text-xs leading-relaxed text-foreground">
            <code>{promptText}</code>
          </pre>
          <button
            type="button"
            onClick={() => copyBlock(promptText, setPromptStatus, "prompt")}
            className="absolute right-2 top-2 inline-flex items-center gap-1 rounded-md border border-divider bg-surface px-2 py-1 text-xs text-foreground transition-colors hover:bg-border-soft focus:outline-none focus:ring-2 focus:ring-primary cursor-pointer"
            aria-label={labels.copyPromptButton}
          >
            {promptStatus === "copied" ? (
              <Check size={12} aria-hidden="true" />
            ) : (
              <Copy size={12} aria-hidden="true" />
            )}
            <span>{labels.copyPromptButton}</span>
          </button>
        </section>
      </div>

      {/* Shared aria-live region for the most recent copy event. */}
      <p
        aria-live="polite"
        className="min-h-[1em] text-xs"
        data-testid="agent-prompt-card-toast"
      >
        {toastText}
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

