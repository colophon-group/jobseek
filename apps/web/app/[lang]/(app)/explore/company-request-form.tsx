"use client";

import { useActionState, useEffect, useRef, useState, useTransition } from "react";
import { useLingui } from "@lingui/react";
import { msg } from "@lingui/core/macro";
import { Trans } from "@lingui/react/macro";
import { requestCompany } from "@/lib/actions/request-company";
import { Button } from "@/components/ui/Button";
import { ErrorAlert } from "@/components/ui/ErrorAlert";
import { RequestCompanySuccess } from "@/components/search/request-company-success";
import {
  requestAgentRun,
  type AgentRunRequestResult,
} from "@/lib/companies/request-agent-run";
import { parseRequestInput } from "@/lib/companies/parse-request-input";

const errorMessages = {
  empty: msg({ id: "app.home.request.error.empty", comment: "Error when company request input is empty", message: "Please enter a company name or URL." }),
  too_short: msg({ id: "app.home.request.error.tooShort", comment: "Error when company request input is too short", message: "Input is too short." }),
  too_long: msg({ id: "app.home.request.error.tooLong", comment: "Error when company request input is too long", message: "Input is too long." }),
  invalid: msg({ id: "app.home.request.error.invalid", comment: "Error when company request input has no alphanumeric characters", message: "Please enter a valid company name or URL." }),
  unknown: msg({ id: "app.home.request.error.unknown", comment: "Generic error when company request fails", message: "Something went wrong. Please try again." }),
} as const;

export function CompanyRequestForm({ locale }: { locale: string }) {
  const { _: t } = useLingui();
  const [state, action, isPending] = useActionState(requestCompany, null);
  const formRef = useRef<HTMLFormElement>(null);
  const [agentRun, setAgentRun] = useState<AgentRunRequestResult | null>(null);
  const [submittedName, setSubmittedName] = useState<string>("");
  const [, startTransition] = useTransition();

  useEffect(() => {
    if (state?.success) {
      formRef.current?.reset();
    }
  }, [state]);

  const errorMessage = state?.errorCode ? t(errorMessages[state.errorCode]) : "";

  function handleSubmit(formData: FormData) {
    setAgentRun(null);
    const raw = (formData.get("input") as string | null) ?? "";
    const trimmed = raw.trim();

    // Use the derived company_name (e.g. "stripe.com") in the success card
    // heading when the input parsed as a URL, falling back to the raw trimmed
    // input only when we couldn't derive a name (legacy GH-issue path).
    const fields = parseRequestInput(raw);
    setSubmittedName(fields?.company_name ?? trimmed);

    startTransition(() => {
      action(formData);
    });

    if (fields) {
      void requestAgentRun({
        companyName: fields.company_name,
        website: fields.website,
      }).then(setAgentRun);
    }
  }

  return (
    <div className="mt-4 w-full max-w-md">
      <form
        ref={formRef}
        action={handleSubmit}
        className="flex flex-col gap-4 min-[480px]:flex-row min-[480px]:items-end"
      >
        <input type="hidden" name="locale" value={locale} />
        <div className="flex-1">
          <input
            name="input"
            type="text"
            required
            minLength={2}
            maxLength={200}
            placeholder={t(msg({
              id: "app.home.request.placeholder",
              comment: "Placeholder for the company request input field",
              message: "e.g. https://boards.greenhouse.io/stripe",
            }))}
            className="w-full rounded-md border border-divider bg-background px-3 py-1.5 text-sm text-foreground outline-none focus:border-primary"
            disabled={isPending}
          />
        </div>
        <Button type="submit" disabled={isPending} size="sm">
          {isPending
            ? t(msg({ id: "app.home.request.submitting", comment: "Submit button while request is in progress", message: "Submitting..." }))
            : <Trans id="app.home.request.submit" comment="Submit button for the company request form">Submit</Trans>}
        </Button>
      </form>
      <div className="mt-3">
        {state?.success && (
          <RequestCompanySuccess
            companyName={submittedName}
            agentRun={agentRun}
            serverActionState={{
              issueNumber: state.issueNumber,
              issueCreationFailed: state.issueCreationFailed,
            }}
          />
        )}
        <ErrorAlert message={errorMessage} />
      </div>
    </div>
  );
}
