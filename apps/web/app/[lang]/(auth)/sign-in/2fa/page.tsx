"use client";

import { useState, useRef } from "react";
import { useRouter } from "next/navigation";
import { Trans } from "@lingui/react/macro";
import { useLingui } from "@lingui/react/macro";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { authClient } from "@/lib/auth-client";
import { useLocalePath } from "@/lib/useLocalePath";

export default function TwoFactorPage() {
  const router = useRouter();
  const { t } = useLingui();
  const lp = useLocalePath();
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const dashboardUrl = lp("/dashboard");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    const { error } = await authClient.twoFactor.verifyTotp({
      code,
    });

    if (error) {
      setError(error.message ?? t({
        id: "auth.2fa.error.invalid",
        comment: "Invalid 2FA code error",
        message: "Invalid code",
      }));
      setLoading(false);
      setCode("");
      inputRef.current?.focus();
      return;
    }

    router.push(dashboardUrl);
  }

  return (
    <>
      <Typography variant="h5" fontWeight={700} textAlign="center" gutterBottom>
        <Trans id="auth.2fa.title" comment="2FA verification page heading">Two-factor authentication</Trans>
      </Typography>
      <Typography variant="body2" color="text.secondary" textAlign="center" sx={{ mb: 3 }}>
        <Trans id="auth.2fa.subtitle" comment="2FA verification page subtitle">Enter the 6-digit code from your authenticator app</Trans>
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      <Box component="form" onSubmit={handleSubmit} noValidate>
        <TextField
          inputRef={inputRef}
          label={t({ id: "auth.2fa.field.code", comment: "TOTP code input label", message: "Code" })}
          fullWidth
          required
          autoFocus
          autoComplete="one-time-code"
          inputProps={{ inputMode: "numeric", maxLength: 6, pattern: "[0-9]*" }}
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
          sx={{ mb: 3 }}
        />
        <Button
          type="submit"
          variant="contained"
          fullWidth
          size="large"
          disabled={loading || code.length !== 6}
        >
          {loading
            ? t({ id: "auth.2fa.button.loading", comment: "2FA verify button while loading", message: "Verifying..." })
            : t({ id: "auth.2fa.button.submit", comment: "2FA verify submit button", message: "Verify" })}
        </Button>
      </Box>
    </>
  );
}
