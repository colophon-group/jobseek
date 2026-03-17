import { db } from "@/db";
import { outreachDraft, hiringSignal, company } from "@/db/schema";
import { eq } from "drizzle-orm";
import { notFound } from "next/navigation";
import Link from "next/link";
import DraftEditor from "@/components/DraftEditor";
import SignalTypeBadge from "@/components/SignalTypeBadge";
import ScoreBadge from "@/components/ScoreBadge";
import CopyEmailClientButton from "@/components/CopyButton";
import { ArrowLeft, ExternalLink, User } from "lucide-react";

export const dynamic = "force-dynamic";

const STATUS_STYLE: Record<string, { bg: string; text: string; border: string; label: string }> = {
  pending_review: { bg: "#eef2ff", text: "#4338ca", border: "#c7d2fe", label: "Pending review" },
  sent:           { bg: "#dcfce7", text: "#15803d", border: "#bbf7d0", label: "Sent" },
  archived:       { bg: "#f1f5f9", text: "#475569", border: "#e2e8f0", label: "Archived" },
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
    <div style={{ maxWidth: 740 }}>
      {/* Back */}
      <Link
        href="/outreach"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 5,
          color: "var(--text-muted)",
          textDecoration: "none",
          fontSize: 13,
          marginBottom: "1.25rem",
        }}
      >
        <ArrowLeft size={14} />
        Back to Outreach
      </Link>

      {/* Company + signal header */}
      <div
        style={{
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: 12,
          padding: "1.25rem 1.5rem",
          marginBottom: "1rem",
        }}
      >
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
              <span style={{ fontWeight: 700, fontSize: 18, color: "var(--text)" }}>
                {companyName ?? meta.company_name ?? "Unknown company"}
              </span>
              <SignalTypeBadge type={signal.signalType} />
              <ScoreBadge score={signal.score} />
              <span
                style={{
                  fontSize: 11,
                  padding: "2px 9px",
                  borderRadius: 99,
                  background: statusStyle.bg,
                  color: statusStyle.text,
                  border: `1px solid ${statusStyle.border}`,
                  fontWeight: 600,
                }}
              >
                {statusStyle.label}
              </span>
            </div>
            <p style={{ color: "var(--text-muted)", fontSize: 13, lineHeight: 1.55, margin: 0 }}>
              {signal.signalText}
            </p>
          </div>
        </div>

        {(meta.source_url || meta.careers_url) && (
          <div style={{ display: "flex", gap: 10, marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--border)" }}>
            {meta.source_url && (
              <a
                href={meta.source_url}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 5,
                  color: "var(--accent-text)",
                  textDecoration: "none",
                  fontSize: 12.5,
                  fontWeight: 500,
                  background: "var(--accent-light)",
                  padding: "4px 10px",
                  borderRadius: 7,
                }}
              >
                <ExternalLink size={12} />
                View news
              </a>
            )}
            {meta.careers_url && (
              <a
                href={meta.careers_url}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 5,
                  color: "#15803d",
                  textDecoration: "none",
                  fontSize: 12.5,
                  fontWeight: 500,
                  background: "#dcfce7",
                  padding: "4px 10px",
                  borderRadius: 7,
                }}
              >
                <ExternalLink size={12} />
                Careers page
              </a>
            )}
          </div>
        )}
      </div>

      {/* Contact card */}
      <div
        style={{
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: 12,
          padding: "1rem 1.25rem",
          marginBottom: "1rem",
          display: "flex",
          alignItems: "center",
          gap: "1rem",
        }}
      >
        <div
          style={{
            width: 38,
            height: 38,
            borderRadius: 10,
            background: "var(--surface-2)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <User size={18} color="var(--text-muted)" />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, color: "var(--text)", fontSize: 14 }}>
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

      {/* Editor */}
      <div
        style={{
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: 12,
          padding: "1.25rem 1.5rem",
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
