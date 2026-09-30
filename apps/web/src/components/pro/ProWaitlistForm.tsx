"use client";

import { useId, useState, useTransition, type FormEvent } from "react";
import { Trans, useLingui } from "@lingui/react/macro";
import { joinProWaitlist, type ProWaitlistResult } from "@/lib/actions/pro-waitlist";
import { useLocalePath } from "@/lib/useLocalePath";
import { useSession } from "@/components/providers/SessionProvider";
import { Button } from "@/components/ui/Button";
import { ErrorAlert } from "@/components/ui/ErrorAlert";
import { FormField } from "@/components/ui/FormField";
import { SuccessAlert } from "@/components/ui/SuccessAlert";

export function ProWaitlistForm() {
  const { t, i18n } = useLingui();
  const lp = useLocalePath();
  const { user } = useSession();
  const descriptionId = useId();
  const [email, setEmail] = useState<string>();
  const [result, setResult] = useState<ProWaitlistResult | null>(null);
  const [pending, startTransition] = useTransition();

  const error = result && "error" in result ? result.error : null;
  const errorMessage = error === "invalid_email"
    ? t({ id: "pro.waitlist.invalidEmail", comment: "Waiting list email validation error", message: "Enter a valid email address." })
    : error === "rate_limited"
      ? t({ id: "pro.waitlist.rateLimited", comment: "Too many waiting list signup attempts", message: "Too many attempts. Please try again in an hour." })
      : error
        ? t({ id: "pro.waitlist.unavailable", comment: "Waiting list signup could not be saved", message: "We couldn’t save your signup. Please try again." })
        : "";

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setResult(null);
    startTransition(async () => {
      try {
        setResult(await joinProWaitlist(email ?? user?.email ?? "", i18n.locale));
      } catch {
        setResult({ error: "unavailable" });
      }
    });
  }

  if (result && "success" in result) {
    return <SuccessAlert message={t({ id: "pro.waitlist.success", comment: "Confirmation after a saved or duplicate Pro waiting list signup", message: "You’re on the list. We’ll email you when Pro opens for signup." })} />;
  }

  return (
    <form onSubmit={handleSubmit} aria-label={t({ id: "pro.waitlist.label", comment: "Accessible name of the Pro waiting list form", message: "Pro waiting list" })} aria-busy={pending} className="w-full space-y-3">
      <p id={descriptionId} className="text-sm text-muted"><Trans id="pro.waitlist.description" comment="Explains what joining the Pro waiting list does">Get an email when Pro opens for signup. No account or payment needed.</Trans></p>
      {errorMessage && <ErrorAlert message={errorMessage} focusOnRender />}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <FormField
          label={t({ id: "pro.waitlist.email", comment: "Email field in the Pro waiting list form", message: "Email address" })}
          type="email"
          name="email"
          autoComplete="email"
          required
          maxLength={254}
          value={email ?? user?.email ?? ""}
          onChange={(event) => setEmail(event.target.value)}
          disabled={pending}
          aria-describedby={descriptionId}
          aria-invalid={error === "invalid_email" || undefined}
          className="min-w-0 flex-1"
        />
        <Button type="submit" disabled={pending} className="max-w-full whitespace-normal! text-center">
          {pending
            ? t({ id: "pro.waitlist.joining", comment: "Waiting list submit button while saving", message: "Joining…" })
            : t({ id: "pro.waitlist.join", comment: "Submit button for the Pro waiting list", message: "Join the waiting list" })}
        </Button>
      </div>
      <p className="text-xs text-muted"><Trans id="pro.waitlist.consent" comment="Consent notice beneath the waiting list form, limited to Pro launch news">By joining, you agree to receive an email about the Pro launch.</Trans>{" "}<a href={lp("/privacy-policy")} className="underline underline-offset-4"><Trans id="common.footer.privacyLink" comment="Footer link to privacy policy page">Privacy</Trans></a></p>
    </form>
  );
}
