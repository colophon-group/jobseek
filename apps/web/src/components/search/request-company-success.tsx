"use client";

/**
 * Renders the success UI for the request-a-company flow. There are three
 * branches:
 *
 *   1. Murmur run was triggered (`agentRun.kind === "ok"`):
 *      Show the `AgentPromptCard` with the prompt + run id; suppress the
 *      legacy "tracked in issue #N" copy.
 *
 *   2. Murmur was rate-limited (`agentRun.kind === "rate_limited"`):
 *      Show a clear "you've hit the limit, try later" message. The legacy
 *      GH-issue copy is intentionally suppressed here so the user doesn't
 *      see two conflicting calls to action.
 *
 *   3. Anything else (`disabled`, `unauthorized`, `validation`, `error`,
 *      `null`): fall through to the legacy "tracked in issue" copy. The
 *      server action ran in parallel and either created the GH issue
 *      (-> link) or saved the request DB-only (-> generic ack).
 *
 * Per jobseek#2802, this component owns ALL success-state rendering for the
 * three call-sites: `request-company.tsx`, the `(app)/explore` form, and
 * the `(app)/progress` form.
 */
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react";
import { msg } from "@lingui/core/macro";
import { useParams } from "next/navigation";
import {
  AgentPromptCard,
  type AgentPromptCardRunStatus,
} from "@/components/search/agent-prompt-card";
import type { AgentRunRequestResult } from "@/lib/companies/request-agent-run";
import { useMurmurRunStatus } from "@/lib/companies/use-murmur-run-status";

const GITHUB_ISSUE_URL = "https://github.com/colophon-group/jobseek/issues";

export interface ServerActionState {
  issueNumber?: number;
  issueCreationFailed?: boolean;
}

export interface RequestCompanySuccessProps {
  /**
   * Display name used as the company name in the agent card heading. Callers
   * should pass the derived `company_name` (e.g. "stripe.com") when the input
   * parsed as a URL, falling back to the raw trimmed input only when no name
   * could be derived. See request-company.tsx and the (app) form siblings.
   */
  companyName: string;
  /** Result from the new `POST /api/web/companies/request` (or null when not yet attempted). */
  agentRun: AgentRunRequestResult | null;
  /** Result from the legacy `requestCompany` server action — already complete. */
  serverActionState: ServerActionState;
}

export function RequestCompanySuccess({
  companyName,
  agentRun,
  serverActionState,
}: RequestCompanySuccessProps) {
  const { _: t } = useLingui();
  const params = useParams();
  const lang = (params?.lang as string | undefined) ?? "en";

  // The hook is unconditionally called (rules-of-hooks) but it short-circuits
  // to `state: "idle"` when the runId is null. The agent-run branch below
  // projects the result into the card's `runStatus` shape only when we have
  // a successful run trigger.
  const runId = agentRun?.kind === "ok" ? agentRun.runId : null;
  const pollResult = useMurmurRunStatus(runId);

  if (agentRun?.kind === "ok") {
    const runStatus: AgentPromptCardRunStatus = {
      state: pollResult.state,
      slug: pollResult.slug,
      successHref: pollResult.slug
        ? `/${lang}/company/${pollResult.slug}`
        : undefined,
    };
    return (
      <AgentPromptCard
        companyName={companyName}
        runId={agentRun.runId}
        installCommand={agentRun.installCommand}
        promptText={agentRun.promptText}
        runStatus={runStatus}
        labels={{
          headingPrefix: t(
            msg({
              id: "app.home.request.agent.headingPrefix",
              comment: "Heading prefix on the agent-prompt success card; followed by the company name",
              message: "We're working on adding",
            }),
          ),
          body: t(
            msg({
              id: "app.home.request.agent.body",
              comment: "Body of the agent-prompt success card",
              message:
                "You can speed this up by asking your AI agent to complete it via Murmur.",
            }),
          ),
          installHeading: t(
            msg({
              id: "app.home.request.agent.install_heading",
              comment:
                "Heading for the 'install the Murmur MCP server' step on the agent-prompt success card",
              message: "1. Install MCP",
            }),
          ),
          runHeading: t(
            msg({
              id: "app.home.request.agent.run_heading",
              comment:
                "Heading for the 'paste this into Claude Code' step on the agent-prompt success card",
              message: "2. Run the prompt",
            }),
          ),
          tokenCaveat: t(
            msg({
              id: "app.home.request.agent.token_caveat",
              comment:
                "Footnote on the agent-prompt success card explaining that the user must replace the literal placeholder <token-from-jobseek-team> with a token handed out by the jobseek team before running the install command",
              message:
                "You'll need a token from the jobseek team — replace <token-from-jobseek-team> before running the install command.",
            }),
          ),
          copyInstallButton: t(
            msg({
              id: "app.home.request.agent.copyInstallButton",
              comment:
                "Accessible label for the button that copies the MCP install one-liner",
              message: "Copy command",
            }),
          ),
          copyPromptButton: t(
            msg({
              id: "app.home.request.agent.copyButton",
              comment: "Label for the copy-prompt button on the agent-prompt success card",
              message: "Copy prompt",
            }),
          ),
          copied: t(
            msg({
              id: "app.home.request.agent.copied",
              comment: "Toast shown briefly after the agent prompt is copied",
              message: "Copied",
            }),
          ),
          copyFailed: t(
            msg({
              id: "app.home.request.agent.copyFailed",
              comment: "Toast shown when copying the agent prompt to the clipboard failed",
              message: "Copy failed",
            }),
          ),
          runIdLabel: t(
            msg({
              id: "app.home.request.agent.runIdLabel",
              comment: "Label preceding the Murmur run id on the agent-prompt success card",
              message: "Run id",
            }),
          ),
          installRegionLabel: t(
            msg({
              id: "app.home.request.agent.installRegionLabel",
              comment: "Aria label for the region containing the MCP install one-liner",
              message: "MCP install command",
            }),
          ),
          promptRegionLabel: t(
            msg({
              id: "app.home.request.agent.promptRegionLabel",
              comment: "Aria label for the region containing the agent prompt code block",
              message: "Agent prompt",
            }),
          ),
          pollingLabel: t(
            msg({
              id: "app.home.request.agent.pollingLabel",
              comment:
                "Footer shown on the agent-prompt success card while we wait for the user's agent to complete the Murmur run",
              message: "Waiting for your agent to finish...",
            }),
          ),
          companyAddedLabel: t(
            msg({
              id: "app.home.request.agent.companyAddedLabel",
              comment:
                "Suffix shown on the success link after the Murmur run completes; the link text reads '{company name} {this label}'",
              message: "added — open it",
            }),
          ),
          givenUpLabel: t(
            msg({
              id: "app.home.request.agent.givenUpLabel",
              comment:
                "Footer shown on the agent-prompt success card after 30 minutes of polling without seeing the run complete",
              message: "Still running… refresh later to check progress.",
            }),
          ),
        }}
      />
    );
  }

  if (agentRun?.kind === "rate_limited") {
    return (
      <div
        role="status"
        className="mb-4 rounded-md border border-warning-border bg-warning-bg px-4 py-3 text-sm text-warning"
      >
        <Trans
          id="app.home.request.agent.rateLimited"
          comment="Message shown when the user has hit the per-hour limit on triggering Murmur company-add runs"
        >
          You&apos;ve hit the hourly limit for company requests. Please try again later.
        </Trans>
      </div>
    );
  }

  // Fallback: legacy GH-issue success copy.
  return (
    <div
      role="status"
      className="mb-4 rounded-md border border-success-border bg-success-bg px-4 py-3 text-sm text-success"
    >
      <p>
        {serverActionState.issueNumber
          ? t(
              msg({
                id: "app.home.request.success.withIssue",
                comment:
                  "Success message after submitting a company request, with link to GitHub issue for tracking",
                message: "Request submitted! Track progress here:",
              }),
            )
          : t(
              msg({
                id: "app.home.request.success.noIssue",
                comment:
                  "Success message when request was saved but GitHub issue could not be created",
                message: "Request submitted! We'll start tracking this company soon.",
              }),
            )}
        {serverActionState.issueNumber && (
          <>
            {" "}
            <a
              href={`${GITHUB_ISSUE_URL}/${serverActionState.issueNumber}`}
              target="_blank"
              rel="noopener noreferrer"
              className="underline font-medium"
            >
              #{serverActionState.issueNumber}
            </a>
          </>
        )}
      </p>
      {serverActionState.issueCreationFailed && (
        <p className="mt-1 text-xs opacity-80">
          <Trans
            id="app.home.request.issueWarning"
            comment="Warning shown when the request was saved in DB but GitHub issue creation failed"
          >
            Note: We couldn&apos;t create a tracking issue, but your request was saved.
          </Trans>
        </p>
      )}
    </div>
  );
}
