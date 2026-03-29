"use client";

import { useState } from "react";

export default function CopyButton({ email }: { email: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    await navigator.clipboard.writeText(email);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <button
      onClick={copy}
      title="Copy email"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        background: copied ? "rgba(52,199,89,0.12)" : "var(--background)",
        border: "none",
        borderRadius: 8,
        padding: "0.4rem 0.8rem",
        color: copied ? "#1a8c3f" : "var(--text-muted)",
        cursor: "pointer",
        fontSize: 12.5,
        fontWeight: 500,
        letterSpacing: -0.1,
        transition: "all 0.15s",
        whiteSpace: "nowrap",
      }}
    >
      {copied ? "✓ Copied" : email}
    </button>
  );
}
