"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { authClient } from "@/lib/auth-client";
import { useLocalePath } from "@/lib/useLocalePath";
import { AuthShell } from "@/components/AuthShell";
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
  const calledRef = useRef(false);

  useEffect(() => {
    if (calledRef.current) return;

    if (!token) {
      setStatus("error");
      setError("Invalid verification link");
      return;
    }

    calledRef.current = true;
    authClient.verifyEmail({ query: { token } }).then(({ error }) => {
      if (error) {
        setStatus("error");
        setError(error.message ?? "Verification failed");
      } else {
        setStatus("success");
      }
    });
  }, [token]);

  if (status === "loading") {
    return (
      <AuthShell>
        <div className="text-center">
          <p className="text-sm text-muted">
            <Trans id="auth.verify.loading" comment="Shown while verifying email token">
              Verifying your email...
            </Trans>
          </p>
        </div>
      </AuthShell>
    );
  }

  if (status === "error") {
    return (
      <AuthShell>
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
      </AuthShell>
    );
  }

  return (
    <AuthShell>
      <div className="text-center">
        <CircleCheck className="mx-auto mb-4 size-10 text-green-500" />
        <h2 className="text-xl font-bold">
          <Trans id="auth.verify.success.title" comment="Heading when email is successfully verified">
            Email verified
          </Trans>
        </h2>
        <p className="mt-2 text-sm text-muted">
          <Trans id="auth.verify.success.description" comment="Description after successful email verification">
            Your email has been verified.
          </Trans>
        </p>
        <Button href={lp("/app")} prefetch={false} className="mt-4">
          <Trans id="auth.verify.success.continue" comment="Button to continue to app after verification">
            Continue
          </Trans>
        </Button>
      </div>
    </AuthShell>
  );
}
