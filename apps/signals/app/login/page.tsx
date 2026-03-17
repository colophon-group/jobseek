"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

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
      setError("Incorrect password.");
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
      <div style={{ width: "100%", maxWidth: 360, textAlign: "center" }}>
        {/* Logo dot */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            marginBottom: "2.5rem",
          }}
        >
          <div
            style={{
              width: 40,
              height: 40,
              background: "linear-gradient(145deg, #0071e3 0%, #5e5ce6 100%)",
              borderRadius: 13,
              marginBottom: "1.1rem",
              boxShadow: "0 4px 20px rgba(0,113,227,0.3)",
            }}
          />
          <p
            style={{
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: 1.4,
              textTransform: "uppercase",
              color: "var(--text-muted)",
              margin: "0 0 6px",
            }}
          >
            Intelligence
          </p>
          <h1
            style={{
              fontSize: 30,
              fontWeight: 700,
              color: "var(--text)",
              letterSpacing: -0.8,
              margin: 0,
            }}
          >
            Signals
          </h1>
        </div>

        {/* Card */}
        <div
          style={{
            background: "var(--surface)",
            borderRadius: "var(--radius)",
            boxShadow: "var(--card-shadow)",
            padding: "2rem",
          }}
        >
          <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <input
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoFocus
              style={{
                background: "var(--background)",
                border: "none",
                borderRadius: 10,
                padding: "0.75rem 1rem",
                color: "var(--text)",
                outline: "none",
                width: "100%",
                fontSize: 15,
                textAlign: "center",
                letterSpacing: 2,
              }}
            />
            {error && (
              <div
                style={{
                  fontSize: 13,
                  color: "var(--dot-red)",
                  padding: "0.5rem",
                  background: "rgba(255,59,48,0.08)",
                  borderRadius: 8,
                }}
              >
                {error}
              </div>
            )}
            <button
              type="submit"
              disabled={loading}
              style={{
                background: loading ? "#6aade8" : "var(--accent)",
                color: "#fff",
                border: "none",
                borderRadius: 10,
                padding: "0.75rem",
                cursor: loading ? "not-allowed" : "pointer",
                fontSize: 15,
                fontWeight: 600,
                letterSpacing: -0.2,
                transition: "background 0.15s",
              }}
            >
              {loading ? "Signing in…" : "Sign in"}
            </button>
          </form>
        </div>

        <p style={{ color: "var(--text-subtle)", fontSize: 12, marginTop: "1.5rem" }}>
          Signals · Zurich Hack 2025
        </p>
      </div>
    </div>
  );
}
