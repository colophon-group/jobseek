const STYLES: Record<string, { bg: string; text: string; label: string }> = {
  funding:    { bg: "rgba(255,159,10,0.12)", text: "#b45309", label: "Funding" },
  sec_filing: { bg: "rgba(0,113,227,0.1)",  text: "#0064cc", label: "SEC Filing" },
  twitter:    { bg: "rgba(94,92,230,0.1)",  text: "#4b48b5", label: "Social" },
  headcount:  { bg: "rgba(52,199,89,0.1)",  text: "#1a8c3f", label: "Headcount" },
  github:     { bg: "rgba(175,82,222,0.1)", text: "#8b3eae", label: "GitHub" },
  job_gap:    { bg: "rgba(255,59,48,0.1)",  text: "#cc2a22", label: "Job Gap" },
};

export default function SignalTypeBadge({ type }: { type: string }) {
  const s = STYLES[type] ?? { bg: "rgba(0,0,0,0.06)", text: "#6e6e73", label: type };
  return (
    <span
      style={{
        background: s.bg,
        color: s.text,
        fontSize: 11,
        fontWeight: 600,
        padding: "2px 8px",
        borderRadius: 6,
        whiteSpace: "nowrap",
        letterSpacing: 0.1,
      }}
    >
      {s.label}
    </span>
  );
}
