"use client";

import { useState } from "react";
import { Copy, Check } from "lucide-react";

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
        background: copied ? "#dcfce7" : "var(--surface-2)",
        border: `1px solid ${copied ? "#bbf7d0" : "var(--border)"}`,
        borderRadius: 8,
        padding: "0.4rem 0.75rem",
        color: copied ? "#15803d" : "var(--text-muted)",
        cursor: "pointer",
        fontSize: 12.5,
        fontWeight: 500,
        transition: "all 0.15s",
        whiteSpace: "nowrap",
      }}
    >
      {copied ? <Check size={13} /> : <Copy size={13} />}
      {copied ? "Copied!" : email}
    </button>
  );
}
