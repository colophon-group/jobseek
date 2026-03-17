"use client";

import { useState } from "react";

type RunState = "idle" | "running" | "SUCCEEDED" | "FAILED" | "error";

export default function RunDiscoveryButton() {
  const [state, setState] = useState<RunState>("idle");
  const [runId, setRunId] = useState<string | null>(null);

  async function run() {
    setState("running");
    try {
      const res = await fetch("/api/apify/run", { method: "POST" });
      if (!res.ok) throw new Error("Failed to trigger run");
      const data = await res.json();
      setRunId(data.runId);
      pollStatus(data.runId);
    } catch {
      setState("error");
    }
  }

  async function pollStatus(id: string) {
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`/api/apify/status/${id}`);
        if (!res.ok) return;
        const data = await res.json();
        if (data.status === "SUCCEEDED" || data.status === "FAILED") {
          setState(data.status);
          clearInterval(interval);
        }
      } catch {
        // keep polling
      }
    }, 3000);
  }

  const label =
    state === "idle" ? "Run Discovery"
    : state === "running" ? "Running…"
    : state === "SUCCEEDED" ? "Done ✓"
    : state === "FAILED" ? "Failed ✗"
    : "Error";

  const color =
    state === "SUCCEEDED" ? "#4ade80"
    : state === "FAILED" || state === "error" ? "#f87171"
    : "var(--text)";

  return (
    <button
      onClick={run}
      disabled={state === "running"}
      style={{
        background: "var(--accent)",
        border: "none",
        borderRadius: 6,
        padding: "0.4rem 1rem",
        color: color,
        cursor: state === "running" ? "not-allowed" : "pointer",
        fontSize: 13,
        opacity: state === "running" ? 0.7 : 1,
      }}
    >
      {label}
      {runId && state === "running" && (
        <span style={{ marginLeft: 6, fontSize: 11, opacity: 0.6 }}>{runId.slice(0, 8)}</span>
      )}
    </button>
  );
}
