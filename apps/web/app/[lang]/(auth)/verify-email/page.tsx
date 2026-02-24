"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { authClient } from "@/lib/auth-client";
import { useLocalePath } from "@/lib/useLocalePath";
import { Button } from "@/components/ui/Button";
import { ErrorAlert } from "@/components/ui/ErrorAlert";
import { CircleCheck } from "lucide-react";

export default function VerifyEmailPage() {
  const searchParams = useSearchParams();
  const { t } = useLingui();
  const lp = useLocalePath();
  const token = searchParams.get("token");
  const [status, setStatus] = useState<"loading" | "success" | "error">("loading");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!token) {
      setStatus("error");
      setError(t({
        id: "auth.verify.error.noToken",
        comment: "Error when verification token is missing from URL",
        message: "Invalid verification link",
      }));
      return;
    }

    authClient.verifyEmail({ query: { token } }).then(({ error }) => {
      if (error) {
        setStatus("error");
        setError(error.message ?? t({
          id: "auth.verify.error.generic",
          comment: "Generic email verification error",
          message: "Verification failed",
        }));
      } else {
        setStatus("success");
      }
    });
  }, [token, t]);

  if (status === "loading") {
    return (
      <div className="text-center">
        <p className="text-sm text-muted">
          <Trans id="auth.verify.loading" comment="Shown while verifying email token">
            Verifying your email...
          </Trans>
        </p>
      </div>
    );
  }

  if (status === "error") {
    return (
      <div className="text-center">
        <h2 className="text-xl font-bold">
          <Trans id="auth.verify.error.title" comment="Heading when email verification fails">
            Verification failed
          </Trans>
        </h2>
        <ErrorAlert message={error} />
        <Button href={lp("/sign-in")} prefetch={false} className="mt-4">
          <Trans id="auth.verify.error.backToSignIn" comment="Link back to sign-in after failed verification">
            Back to sign in
          </Trans>
        </Button>
      </div>
    );
  }

  return (
    <div className="text-center">
      <CircleCheck className="mx-auto mb-4 size-10 text-green-500" />
      <h2 className="text-xl font-bold">
        <Trans id="auth.verify.success.title" comment="Heading when email is successfully verified">
          Email verified
        </Trans>
      </h2>
      <p className="mt-2 text-sm text-muted">
        <Trans id="auth.verify.success.description" comment="Description after successful email verification">
          Your email has been verified. You can now sign in.
        </Trans>
      </p>
      <Button href={lp("/dashboard")} prefetch={false} className="mt-4">
        <Trans id="auth.verify.success.continue" comment="Button to continue to dashboard after verification">
          Continue to dashboard
        </Trans>
      </Button>
    </div>
  );
}
