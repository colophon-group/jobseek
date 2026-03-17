const COLORS: Record<string, { bg: string; text: string }> = {
  funding:     { bg: "#a16207aa", text: "#fde047" },
  sec_filing:  { bg: "#1d4ed8aa", text: "#93c5fd" },
  twitter:     { bg: "#0369a1aa", text: "#7dd3fc" },
  headcount:   { bg: "#065f46aa", text: "#6ee7b7" },
  github:      { bg: "#581c87aa", text: "#d8b4fe" },
  job_gap:     { bg: "#9f1239aa", text: "#fda4af" },
};

export default function SignalTypeBadge({ type }: { type: string }) {
  const c = COLORS[type] ?? { bg: "#374151aa", text: "#9ca3af" };
  return (
    <span
      style={{
        background: c.bg,
        color: c.text,
        fontSize: 11,
        padding: "2px 7px",
        borderRadius: 99,
        fontWeight: 500,
        whiteSpace: "nowrap",
      }}
    >
      {type.replace("_", " ")}
    </span>
  );
}
