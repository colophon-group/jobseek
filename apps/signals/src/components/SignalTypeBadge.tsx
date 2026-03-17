const COLORS: Record<string, { bg: string; text: string; border: string }> = {
  funding:    { bg: "#fef3c7", text: "#92400e", border: "#fde68a" },
  sec_filing: { bg: "#dbeafe", text: "#1e40af", border: "#bfdbfe" },
  twitter:    { bg: "#e0f2fe", text: "#075985", border: "#bae6fd" },
  headcount:  { bg: "#dcfce7", text: "#166534", border: "#bbf7d0" },
  github:     { bg: "#f3e8ff", text: "#6b21a8", border: "#e9d5ff" },
  job_gap:    { bg: "#fee2e2", text: "#991b1b", border: "#fecaca" },
};

const LABELS: Record<string, string> = {
  funding: "Funding",
  sec_filing: "SEC Filing",
  twitter: "Social",
  headcount: "Headcount",
  github: "GitHub",
  job_gap: "Job Gap",
};

export default function SignalTypeBadge({ type }: { type: string }) {
  const c = COLORS[type] ?? { bg: "#f1f5f9", text: "#475569", border: "#e2e8f0" };
  return (
    <span
      style={{
        background: c.bg,
        color: c.text,
        border: `1px solid ${c.border}`,
        fontSize: 11,
        padding: "2px 8px",
        borderRadius: 99,
        fontWeight: 600,
        whiteSpace: "nowrap",
        letterSpacing: 0.2,
      }}
    >
      {LABELS[type] ?? type.replace("_", " ")}
    </span>
  );
}
