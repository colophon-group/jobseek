const STYLES: Record<string, { cls: string; label: string }> = {
  funding:    { cls: "bg-warning-bg text-warning",   label: "Funding" },
  sec_filing: { cls: "bg-info-bg text-info",         label: "SEC Filing" },
  twitter:    { cls: "bg-info-bg text-info",         label: "Social" },
  headcount:  { cls: "bg-success-bg text-success",  label: "Headcount" },
  github:     { cls: "bg-info-bg text-info",         label: "GitHub" },
  job_gap:    { cls: "bg-error-bg text-error",       label: "Job Gap" },
};

export default function SignalTypeBadge({ type }: { type: string }) {
  const s = STYLES[type] ?? { cls: "bg-border-soft text-muted", label: type };
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-semibold whitespace-nowrap ${s.cls}`}>
      {s.label}
    </span>
  );
}
