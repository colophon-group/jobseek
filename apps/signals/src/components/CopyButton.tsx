"use client";

export default function CopyButton({ email }: { email: string }) {
  async function copy() {
    await navigator.clipboard.writeText(email);
  }

  return (
    <button
      onClick={copy}
      title="Copy email"
      style={{
        background: "var(--surface-2)",
        border: "1px solid var(--border)",
        borderRadius: 6,
        padding: "0.3rem 0.6rem",
        color: "var(--text-muted)",
        cursor: "pointer",
        fontSize: 12,
        display: "flex",
        alignItems: "center",
        gap: 4,
      }}
    >
      {email} · Copy
    </button>
  );
}
