"use client";

import { useActionState, useEffect, useRef } from "react";
import { useLingui } from "@lingui/react";
import { msg } from "@lingui/core/macro";
import { Trans } from "@lingui/react/macro";
import { requestCompany } from "@/lib/actions/stats";
import { Button } from "@/components/ui/Button";
import { ErrorAlert } from "@/components/ui/ErrorAlert";

const GITHUB_ISSUE_URL = "https://github.com/colophon-group/jobseek/issues";

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

  useEffect(() => {
    if (state?.success) {
      formRef.current?.reset();
    }
  }, [state]);

  const errorMessage = state?.errorCode ? t(errorMessages[state.errorCode]) : "";

  return (
    <div className="mt-4 w-full max-w-md">
      <form ref={formRef} action={action} className="flex flex-col gap-4 min-[480px]:flex-row min-[480px]:items-end">
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
          <div role="status" className="mb-4 rounded-md border border-success-border bg-success-bg px-4 py-3 text-sm text-success">
            <p>
              {state.issueNumber
                ? t(msg({
                    id: "app.home.request.success.withIssue",
                    comment: "Success message after submitting a company request, with link to GitHub issue for tracking",
                    message: "Request submitted! Track progress here:",
                  }))
                : t(msg({
                    id: "app.home.request.success.noIssue",
                    comment: "Success message when request was saved but GitHub issue could not be created",
                    message: "Request submitted! We'll start tracking this company soon.",
                  }))}
              {state.issueNumber && (
                <>
                  {" "}
                  <a
                    href={`${GITHUB_ISSUE_URL}/${state.issueNumber}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="underline font-medium"
                  >
                    #{state.issueNumber}
                  </a>
                </>
              )}
            </p>
            {state.issueCreationFailed && (
              <p className="mt-1 text-xs opacity-80">
                <Trans id="app.home.request.issueWarning" comment="Warning shown when the request was saved in DB but GitHub issue creation failed">
                  Note: We couldn&apos;t create a tracking issue, but your request was saved.
                </Trans>
              </p>
            )}
          </div>
        )}
        <ErrorAlert message={errorMessage} />
      </div>
    </div>
  );
}
