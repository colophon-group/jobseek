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

export default function SignUpPage() {
  const router = useRouter();
  const { t } = useLingui();
  const lp = useLocalePath();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const dashboardUrl = lp("/dashboard");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

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
        <Trans id="auth.signUp.title" comment="Sign-up page heading">Create an account</Trans>
      </Typography>
      <Typography variant="body2" color="text.secondary" textAlign="center" sx={{ mb: 3 }}>
        <Trans id="auth.signUp.subtitle" comment="Sign-up page subtitle">Get started with Job Seek</Trans>
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      <Box component="form" onSubmit={handleSubmit} noValidate>
        <TextField
          label={t({ id: "auth.field.name", comment: "Name input label", message: "Name" })}
          fullWidth
          required
          autoComplete="name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          sx={{ mb: 2 }}
        />
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
          autoComplete="new-password"
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
            ? t({ id: "auth.signUp.button.loading", comment: "Sign-up button while loading", message: "Creating account..." })
            : t({ id: "auth.signUp.button.submit", comment: "Sign-up submit button", message: "Sign up" })}
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
        <Trans id="auth.signUp.hasAccount" comment="Link to sign-in page from sign-up">
          Already have an account?{" "}
          <Link href={lp("/sign-in")} style={{ fontWeight: 600 }}>
            Sign in
          </Link>
        </Trans>
      </Typography>
    </>
  );
}
