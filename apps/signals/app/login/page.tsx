"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Zap } from "lucide-react";

export default function LoginPage() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");

    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });

    if (res.ok) {
      router.push("/signals");
    } else {
      setError("Invalid password. Try again.");
      setLoading(false);
    }
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--background)",
        padding: "1.5rem",
      }}
    >
      <div style={{ width: "100%", maxWidth: 380 }}>
        {/* Logo */}
        <div style={{ textAlign: "center", marginBottom: "2.5rem" }}>
          <div
            style={{
              width: 48,
              height: 48,
              background: "var(--accent)",
              borderRadius: 14,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              margin: "0 auto 1rem",
              boxShadow: "0 4px 16px rgba(99,102,241,0.3)",
            }}
          >
            <Zap size={24} color="white" fill="white" />
          </div>
          <h1
            style={{
              fontSize: 24,
              fontWeight: 700,
              color: "var(--text)",
              letterSpacing: -0.5,
              margin: "0 0 6px",
            }}
          >
            Signals
          </h1>
          <p style={{ color: "var(--text-muted)", fontSize: 14, margin: 0 }}>
            AI-powered hiring intelligence
          </p>
        </div>

        {/* Card */}
        <div
          style={{
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: 14,
            padding: "2rem",
            boxShadow: "0 1px 4px rgba(0,0,0,0.06)",
          }}
        >
          <p style={{ color: "var(--text-muted)", fontSize: 13, marginBottom: "1.25rem", textAlign: "center" }}>
            Enter your password to continue
          </p>
          <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <input
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoFocus
              style={{
                background: "var(--surface-2)",
                border: "1.5px solid var(--border)",
                borderRadius: 8,
                padding: "0.65rem 0.875rem",
                color: "var(--text)",
                outline: "none",
                width: "100%",
                fontSize: 14,
              }}
            />
            {error && (
              <p
                style={{
                  color: "#dc2626",
                  fontSize: 13,
                  background: "#fee2e2",
                  border: "1px solid #fecaca",
                  borderRadius: 7,
                  padding: "0.5rem 0.75rem",
                  margin: 0,
                }}
              >
                {error}
              </p>
            )}
            <button
              type="submit"
              disabled={loading}
              style={{
                background: loading ? "#818cf8" : "var(--accent)",
                color: "#fff",
                border: "none",
                borderRadius: 8,
                padding: "0.65rem 1rem",
                cursor: loading ? "not-allowed" : "pointer",
                fontSize: 14,
                fontWeight: 600,
                transition: "background 0.15s",
              }}
            >
              {loading ? "Signing in…" : "Sign in"}
            </button>
          </form>
        </div>

        <p style={{ textAlign: "center", color: "var(--text-muted)", fontSize: 12, marginTop: "1.5rem" }}>
          Signals · Zurich Hack 2025
        </p>
      </div>
    </div>
  );
}
