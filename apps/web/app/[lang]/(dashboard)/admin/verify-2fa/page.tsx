"use client";

import { useState, useRef } from "react";
import { useRouter, useParams } from "next/navigation";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import Container from "@mui/material/Container";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

export default function AdminVerify2FAPage() {
  const router = useRouter();
  const params = useParams();
  const lang = (params.lang as string) ?? "en";
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    const res = await fetch("/api/admin/verify-2fa", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });

    if (!res.ok) {
      const data = await res.json();
      setError(data.error ?? "Verification failed");
      setLoading(false);
      setCode("");
      inputRef.current?.focus();
      return;
    }

    router.push(`/${lang}/admin`);
    router.refresh();
  }

  return (
    <Container maxWidth="sm">
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "80vh",
          py: 4,
        }}
      >
        <Card sx={{ width: "100%", p: { xs: 3, sm: 4 } }}>
          <Typography variant="h5" fontWeight={700} textAlign="center" gutterBottom>
            Admin verification
          </Typography>
          <Typography variant="body2" color="text.secondary" textAlign="center" sx={{ mb: 3 }}>
            Enter your authenticator code to access admin
          </Typography>

          {error && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {error}
            </Alert>
          )}

          <Box component="form" onSubmit={handleSubmit} noValidate>
            <TextField
              inputRef={inputRef}
              label="6-digit code"
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
              {loading ? "Verifying..." : "Verify"}
            </Button>
          </Box>
        </Card>
      </Box>
    </Container>
  );
}
