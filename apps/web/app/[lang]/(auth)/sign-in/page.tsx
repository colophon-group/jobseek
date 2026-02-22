"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Divider from "@mui/material/Divider";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import GitHubIcon from "@mui/icons-material/GitHub";
import GoogleIcon from "@mui/icons-material/Google";
import { authClient } from "@/lib/auth-client";
import { useLocalePath } from "@/lib/useLocalePath";

export default function SignInPage() {
  const router = useRouter();
  const { t } = useLingui();
  const lp = useLocalePath();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const dashboardUrl = lp("/dashboard");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    const { error } = await authClient.signIn.email({
      email,
      password,
      callbackURL: dashboardUrl,
    });

    if (error) {
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

  async function handleOAuth(provider: "github" | "google") {
    await authClient.signIn.social({
      provider,
      callbackURL: dashboardUrl,
    });
  }

  return (
    <>
      <Typography variant="h5" fontWeight={700} textAlign="center" gutterBottom>
        <Trans id="auth.signIn.title" comment="Sign-in page heading">Sign in</Trans>
      </Typography>
      <Typography variant="body2" color="text.secondary" textAlign="center" sx={{ mb: 3 }}>
        <Trans id="auth.signIn.subtitle" comment="Sign-in page subtitle">Welcome back</Trans>
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      <Box component="form" onSubmit={handleSubmit} noValidate>
        <TextField
          label={t({ id: "auth.field.email", comment: "Email input label", message: "Email" })}
          type="email"
          fullWidth
          required
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          sx={{ mb: 2 }}
        />
        <TextField
          label={t({ id: "auth.field.password", comment: "Password input label", message: "Password" })}
          type="password"
          fullWidth
          required
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          sx={{ mb: 3 }}
        />
        <Button
          type="submit"
          variant="contained"
          fullWidth
          size="large"
          disabled={loading}
        >
          {loading
            ? t({ id: "auth.signIn.button.loading", comment: "Sign-in button while loading", message: "Signing in..." })
            : t({ id: "auth.signIn.button.submit", comment: "Sign-in submit button", message: "Sign in" })}
        </Button>
      </Box>

      <Divider sx={{ my: 3 }}>
        <Trans id="auth.divider.or" comment="Divider between form and OAuth buttons">or</Trans>
      </Divider>

      <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        <Button
          variant="outlined"
          fullWidth
          startIcon={<GitHubIcon />}
          onClick={() => handleOAuth("github")}
        >
          <Trans id="auth.oauth.github" comment="GitHub OAuth button">Continue with GitHub</Trans>
        </Button>
        <Button
          variant="outlined"
          fullWidth
          startIcon={<GoogleIcon />}
          onClick={() => handleOAuth("google")}
        >
          <Trans id="auth.oauth.google" comment="Google OAuth button">Continue with Google</Trans>
        </Button>
      </Box>

      <Typography variant="body2" textAlign="center" sx={{ mt: 3 }}>
        <Trans id="auth.signIn.noAccount" comment="Link to sign-up page from sign-in">
          Don&apos;t have an account?{" "}
          <Link href={lp("/sign-up")} style={{ fontWeight: 600 }}>
            Sign up
          </Link>
        </Trans>
      </Typography>
    </>
  );
}
