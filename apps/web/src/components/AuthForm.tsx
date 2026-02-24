"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import { authClient } from "@/lib/auth-client";
import { useLocalePath } from "@/lib/useLocalePath";
import { Button } from "@/components/ui/Button";
import { FormField } from "@/components/ui/FormField";
import { ErrorAlert } from "@/components/ui/ErrorAlert";
import { OAuthButtons } from "@/components/ui/OAuthButtons";

type AuthFormProps = {
  mode: "sign-in" | "sign-up";
};

export function AuthForm({ mode }: AuthFormProps) {
  const router = useRouter();
  const { t } = useLingui();
  const lp = useLocalePath();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const isSignUp = mode === "sign-up";
  const dashboardUrl = lp("/dashboard");

  function goToCheckEmail() {
    sessionStorage.setItem("verify-email", email);
    router.push(lp("/check-email"));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");

    // Client-side validation
    if (!email || !password || (isSignUp && !name)) {
      setError(t({
        id: "auth.error.fieldsRequired",
        comment: "Error when required fields are empty",
        message: "Please fill in all fields",
      }));
      return;
    }

    setLoading(true);

    if (isSignUp) {
      const { error } = await authClient.signUp.email({
        name,
        email,
        password,
        callbackURL: dashboardUrl,
      });
      if (error) {
        setError(error.message ?? t({
          id: "auth.signUp.error.generic",
          comment: "Generic sign-up error fallback",
          message: "Sign-up failed",
        }));
        setLoading(false);
        return;
      }
      goToCheckEmail();
    } else {
      const { error } = await authClient.signIn.email({
        email,
        password,
        callbackURL: dashboardUrl,
      });
      if (error) {
        if (error.status === 403) {
          goToCheckEmail();
          return;
        }
        setError(error.message ?? t({
          id: "auth.signIn.error.generic",
          comment: "Generic sign-in error fallback",
          message: "Sign-in failed",
        }));
        setLoading(false);
        return;
      }
      router.push(dashboardUrl);
    }
  }

  async function handleOAuth(provider: "github" | "google" | "linkedin") {
    await authClient.signIn.social({
      provider,
      callbackURL: dashboardUrl,
    });
  }

  return (
    <>
      <h2 className="text-center text-xl font-bold">
        {isSignUp
          ? <Trans id="auth.signUp.title" comment="Sign-up page heading">Create an account</Trans>
          : <Trans id="auth.signIn.title" comment="Sign-in page heading">Sign in</Trans>}
      </h2>
      <p className="mb-6 text-center text-sm text-muted">
        {isSignUp
          ? <Trans id="auth.signUp.subtitle" comment="Sign-up page subtitle">Get started with Job Seek</Trans>
          : <Trans id="auth.signIn.subtitle" comment="Sign-in page subtitle">Welcome back</Trans>}
      </p>

      <ErrorAlert message={error} />

      <form onSubmit={handleSubmit} noValidate>
        {isSignUp && (
          <FormField
            label={t({ id: "auth.field.name", comment: "Name input label", message: "Name" })}
            required
            autoComplete="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mb-4"
          />
        )}
        <FormField
          label={t({ id: "auth.field.email", comment: "Email input label", message: "Email" })}
          type="email"
          required
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="mb-4"
        />
        <FormField
          label={t({ id: "auth.field.password", comment: "Password input label", message: "Password" })}
          type="password"
          required
          autoComplete={isSignUp ? "new-password" : "current-password"}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mb-6"
        />
        <Button type="submit" disabled={loading} className="w-full">
          {isSignUp
            ? (loading
                ? t({ id: "auth.signUp.button.loading", comment: "Sign-up button while loading", message: "Creating account..." })
                : t({ id: "auth.signUp.button.submit", comment: "Sign-up submit button", message: "Sign up" }))
            : (loading
                ? t({ id: "auth.signIn.button.loading", comment: "Sign-in button while loading", message: "Signing in..." })
                : t({ id: "auth.signIn.button.submit", comment: "Sign-in submit button", message: "Sign in" }))}
        </Button>
      </form>

      <OAuthButtons onOAuth={handleOAuth} />

      <p className="mt-6 text-center text-sm">
        {isSignUp ? (
          <Trans id="auth.signUp.hasAccount" comment="Link to sign-in page from sign-up">
            Already have an account?{" "}
            <Link href={lp("/sign-in")} prefetch={false} className="font-semibold transition-colors hover:text-muted">
              Sign in
            </Link>
          </Trans>
        ) : (
          <Trans id="auth.signIn.noAccount" comment="Link to sign-up page from sign-in">
            Don&apos;t have an account?{" "}
            <Link href={lp("/sign-up")} prefetch={false} className="font-semibold transition-colors hover:text-muted">
              Sign up
            </Link>
          </Trans>
        )}
      </p>
    </>
  );
}
