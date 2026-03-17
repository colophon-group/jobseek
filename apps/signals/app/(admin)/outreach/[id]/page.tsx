import { db } from "@/db";
import { outreachDraft, hiringSignal, company } from "@/db/schema";
import { eq } from "drizzle-orm";
import { notFound } from "next/navigation";
import Link from "next/link";
import DraftEditor from "@/components/DraftEditor";
import SignalTypeBadge from "@/components/SignalTypeBadge";
import ScoreBadge from "@/components/ScoreBadge";
import CopyEmailClientButton from "@/components/CopyButton";

export const dynamic = "force-dynamic";

const STATUS_STYLE: Record<string, { bg: string; text: string; label: string }> = {
  pending_review: { bg: "rgba(0,113,227,0.1)",  text: "#0064cc", label: "Pending review" },
  sent:           { bg: "rgba(52,199,89,0.12)",  text: "#1a8c3f", label: "Sent" },
  archived:       { bg: "rgba(0,0,0,0.06)",      text: "#6e6e73", label: "Archived" },
};

export default async function DraftPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  const rows = await db
    .select({
      draft: outreachDraft,
      signal: hiringSignal,
      companyName: company.name,
      companySlug: company.slug,
    })
    .from(outreachDraft)
    .innerJoin(hiringSignal, eq(outreachDraft.signalId, hiringSignal.id))
    .leftJoin(company, eq(hiringSignal.companyId, company.id))
    .where(eq(outreachDraft.id, id))
    .limit(1);

  if (!rows.length) notFound();

  const { draft, signal, companyName } = rows[0];
  const meta = (signal.metadata ?? {}) as Record<string, string>;
  const statusStyle = STATUS_STYLE[draft.status] ?? STATUS_STYLE.pending_review;

  return (
    <div style={{ maxWidth: 700 }}>
      {/* Back */}
      <Link
        href="/outreach"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 5,
          color: "var(--accent)",
          textDecoration: "none",
          fontSize: 13.5,
          fontWeight: 500,
          marginBottom: "1.5rem",
          letterSpacing: -0.1,
        }}
      >
        ← Outreach
      </Link>

      {/* Company + signal */}
      <div
        style={{
          background: "var(--surface)",
          borderRadius: "var(--radius)",
          boxShadow: "var(--card-shadow)",
          padding: "1.5rem",
          marginBottom: "1rem",
        }}
      >
        <div style={{ display: "flex", alignItems: "flex-start", gap: 10, flexWrap: "wrap", marginBottom: 10 }}>
          <h2
            style={{
              fontWeight: 700,
              fontSize: 20,
              color: "var(--text)",
              letterSpacing: -0.5,
              margin: 0,
            }}
          >
            {companyName ?? meta.company_name ?? "Unknown"}
          </h2>
          <SignalTypeBadge type={signal.signalType} />
          <ScoreBadge score={signal.score} />
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              padding: "2px 9px",
              borderRadius: 6,
              background: statusStyle.bg,
              color: statusStyle.text,
            }}
          >
            {statusStyle.label}
          </span>
        </div>

        <p style={{ color: "var(--text-muted)", fontSize: 13.5, lineHeight: 1.55, margin: 0 }}>
          {signal.signalText}
        </p>

        {(meta.source_url || meta.careers_url) && (
          <div
            style={{
              display: "flex",
              gap: 12,
              marginTop: 14,
              paddingTop: 14,
              borderTop: "1px solid rgba(0,0,0,0.06)",
            }}
          >
            {meta.source_url && (
              <a href={meta.source_url} target="_blank" rel="noopener noreferrer"
                style={{ color: "var(--accent)", fontSize: 13, fontWeight: 500, textDecoration: "none", letterSpacing: -0.1 }}>
                View news ↗
              </a>
            )}
            {meta.careers_url && (
              <a href={meta.careers_url} target="_blank" rel="noopener noreferrer"
                style={{ color: "var(--dot-green)", fontSize: 13, fontWeight: 500, textDecoration: "none", letterSpacing: -0.1 }}>
                Careers page ↗
              </a>
            )}
          </div>
        )}
      </div>

      {/* Contact */}
      <div
        style={{
          background: "var(--surface)",
          borderRadius: "var(--radius)",
          boxShadow: "var(--card-shadow)",
          padding: "1rem 1.5rem",
          marginBottom: "1rem",
          display: "flex",
          alignItems: "center",
          gap: "1rem",
        }}
      >
        <div
          style={{
            width: 36,
            height: 36,
            borderRadius: "50%",
            background: "var(--background)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 14,
            color: "var(--text-muted)",
            flexShrink: 0,
          }}
        >
          {(draft.contactName?.[0] ?? "?").toUpperCase()}
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, color: "var(--text)", fontSize: 14, letterSpacing: -0.2 }}>
            {draft.contactName}
          </div>
          {draft.contactTitle && (
            <div style={{ color: "var(--text-muted)", fontSize: 12.5, marginTop: 1 }}>
              {draft.contactTitle}
            </div>
          )}
        </div>
        {draft.contactEmail && <CopyEmailClientButton email={draft.contactEmail} />}
      </div>

      {/* Draft editor */}
      <div
        style={{
          background: "var(--surface)",
          borderRadius: "var(--radius)",
          boxShadow: "var(--card-shadow)",
          padding: "1.5rem",
        }}
      >
        <DraftEditor
          id={draft.id}
          subject={draft.subject}
          body={draft.body}
          status={draft.status as "pending_review" | "sent" | "archived"}
        />
      </div>
    </div>
  );
}
